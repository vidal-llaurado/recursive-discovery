"""Execution workers.

`ProcessWorker` is constrained and reproducible but not hermetic.
`ContainerWorker` is the preferred boundary when Docker/Podman and an image are available.
`WorkerKernel` preserves the existing Kernel contract while delegating execution.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
import hashlib
import os
import platform
import resource
import shutil
import subprocess
import sys
import time

from .core import Artifact, Kernel, Ledger, _digest, _json, _refs


@dataclass(frozen=True)
class RunResult:
    returncode: int
    stdout: str
    stderr: str
    elapsed_s: float
    isolation: str
    executor: str


class ProcessWorker:
    """Portable constrained subprocess. Explicitly not a filesystem/network sandbox."""

    name = "process"

    def __init__(self, *, memory_mb: int | None = None, cpu_seconds: int | None = None):
        self.memory_mb = memory_mb
        self.cpu_seconds = cpu_seconds

    def _limits(self):
        memory_mb, cpu_seconds = self.memory_mb, self.cpu_seconds

        def apply() -> None:
            try:
                if memory_mb:
                    n = int(memory_mb) * 1024 * 1024
                    resource.setrlimit(resource.RLIMIT_AS, (n, n))
                if cpu_seconds:
                    n = int(cpu_seconds)
                    resource.setrlimit(resource.RLIMIT_CPU, (n, n + 1))
                resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
            except (ValueError, OSError):
                pass
        return apply

    def run(
        self,
        argv: list[str],
        *,
        cwd: str | Path,
        env: dict[str, str],
        timeout: float,
        tool: Artifact | None = None,
    ) -> RunResult:
        safe = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(Path(cwd).resolve()),
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
        }
        if os.name == "nt" and "SYSTEMROOT" in os.environ:
            safe["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
        safe.update(env)
        start = time.perf_counter()
        p = subprocess.run(
            argv,
            cwd=Path(cwd).resolve(),
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
            env=safe,
            start_new_session=True,
            preexec_fn=self._limits() if os.name == "posix" else None,
        )
        return RunResult(
            p.returncode, p.stdout, p.stderr, time.perf_counter() - start,
            "process-not-hermetic", sys.executable,
        )


class ContainerWorker:
    """Rootless-style Docker/Podman runner with network disabled and capabilities dropped.

    Tools using this worker should declare `image` and container-ready argv. The project cwd is
    mounted at `/work`; relative paths therefore remain stable and reproducible.
    """

    def __init__(self, engine: str | None = None, *, memory: str = "4g", cpus: str = "2"):
        self.engine = engine or shutil.which("podman") or shutil.which("docker")
        if not self.engine:
            raise RuntimeError("Docker/Podman not available")
        self.memory = memory
        self.cpus = cpus

    def run(
        self,
        argv: list[str],
        *,
        cwd: str | Path,
        env: dict[str, str],
        timeout: float,
        tool: Artifact | None = None,
    ) -> RunResult:
        if tool is None or not tool.data.get("image"):
            raise ValueError("container execution requires tool.data['image']")
        image = str(tool.data["image"])
        cmd = [
            self.engine, "run", "--rm",
            "--network", "none",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--pids-limit", "256",
            "--memory", str(tool.data.get("memory", self.memory)),
            "--cpus", str(tool.data.get("cpus", self.cpus)),
            "-v", f"{Path(cwd).resolve()}:/work:rw",
            "-w", "/work",
        ]
        for key, value in sorted(env.items()):
            cmd += ["-e", f"{key}={value}"]
        cmd += [image, *argv]
        start = time.perf_counter()
        p = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=timeout)
        return RunResult(
            p.returncode, p.stdout, p.stderr, time.perf_counter() - start,
            "container", f"{Path(self.engine).name}:{image}",
        )


class AutoWorker:
    """Use a container when the tool declares an image; otherwise use a constrained process."""

    def __init__(self):
        self.process = ProcessWorker()
        engine = shutil.which("podman") or shutil.which("docker")
        self.container = ContainerWorker(engine) if engine else None

    def run(self, argv: list[str], *, cwd: str | Path, env: dict[str, str], timeout: float,
            tool: Artifact | None = None) -> RunResult:
        if tool is not None and tool.data.get("image"):
            if not self.container:
                raise RuntimeError("tool requires hermetic container execution but Docker/Podman is unavailable")
            return self.container.run(argv, cwd=cwd, env=env, timeout=timeout, tool=tool)
        return self.process.run(argv, cwd=cwd, env=env, timeout=timeout, tool=tool)


class WorkerKernel(Kernel):
    """Kernel with the same trust/signature semantics and a pluggable execution boundary."""

    def __init__(self, key_path: str | Path, worker: Any | None = None):
        super().__init__(key_path)
        self.worker = worker or AutoWorker()

    def run(
        self,
        ledger: Ledger,
        target: Artifact,
        lane: str,
        argv: list[str],
        *,
        cwd: str | Path = ".",
        semantics: str = "check",
        timeout: float = 120.0,
        tool: Artifact | None = None,
        inputs: Iterable[str | Path] = (),
        env: dict[str, str] | None = None,
        seed: int | None = None,
    ) -> Artifact:
        cwd = Path(cwd).resolve()
        declared: dict[str, str] = {}
        for raw in inputs:
            p = Path(raw)
            p = p if p.is_absolute() else cwd / p
            declared[str(raw)] = _digest(p.resolve())

        env_patch = dict(env or {})
        if seed is not None:
            env_patch.setdefault("PYTHONHASHSEED", str(seed))
            env_patch.setdefault("RSCIENCE_SEED", str(seed))

        result = self.worker.run(
            argv, cwd=cwd, env=env_patch, timeout=timeout, tool=tool,
        )
        run = {
            "runner": result.isolation,
            "executor": result.executor,
            "argv": argv,
            "inputs": declared,
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "env": env_patch,
            "seed": seed,
        }
        run["fingerprint"] = hashlib.sha256(_json(run).encode()).hexdigest()
        data = {
            "lane": lane,
            "semantics": semantics,
            "verdict": "pass" if result.returncode == 0 else "fail",
            "returncode": result.returncode,
            "stdout": result.stdout[-50_000:],
            "stderr": result.stderr[-50_000:],
            "elapsed_s": result.elapsed_s,
            "run": run,
        }
        r: dict[str, Iterable[str]] = {"target": [target.id]}
        if tool:
            r["tool"] = [tool.id]
        refs = _refs(r)
        data["_sig"] = self._sig("evidence", data, refs, "kernel")
        return ledger.put("evidence", data, refs, by="kernel")
