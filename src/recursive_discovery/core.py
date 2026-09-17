"""Artifacts, kernel-signed execution records, frontier routing, and promotion.

Artifact: immutable scientific state.
Kernel:   signed execution records with reproducibility provenance.
Frontier: missing evidence edges.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
import hashlib
import hmac
import json
import os
import platform
import subprocess
import sys
import time


def _json(x: Any) -> str:
    return json.dumps(x, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _refs(x: dict[str, Iterable[str]] | None) -> dict[str, tuple[str, ...]]:
    return {k: tuple(v) for k, v in sorted((x or {}).items())}


def _hash(x: Any, n: int = 20) -> str:
    return hashlib.sha256(_json(x).encode()).hexdigest()[:n]


def _digest(path: Path) -> str:
    if path.is_file():
        return hashlib.sha256(path.read_bytes()).hexdigest()
    if path.is_dir():
        rows = []
        for p in sorted(x for x in path.rglob("*") if x.is_file()):
            rows.append((p.relative_to(path).as_posix(), _digest(p)))
        return hashlib.sha256(_json(rows).encode()).hexdigest()
    raise FileNotFoundError(path)


@dataclass(frozen=True)
class Artifact:
    id: str
    kind: str
    data: dict[str, Any]
    refs: dict[str, tuple[str, ...]]
    by: str
    t: float


@dataclass(frozen=True)
class Task:
    verb: str
    target: str
    why: str
    lane: str = "meta"

    @property
    def key(self) -> str:
        return _hash((self.verb, self.target, self.lane), 12)


class Ledger:
    """Append-only JSONL artifact graph with content-derived IDs."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._a: dict[str, Artifact] = {}
        if self.path.exists():
            for line in self.path.read_text().splitlines():
                if line.strip():
                    d = json.loads(line)
                    a = Artifact(
                        d["id"], d["kind"], d["data"],
                        {k: tuple(v) for k, v in d["refs"].items()},
                        d["by"], d["t"],
                    )
                    self._a[a.id] = a

    def put(
        self,
        kind: str,
        data: dict[str, Any],
        refs: dict[str, Iterable[str]] | None = None,
        by: str = "agent",
    ) -> Artifact:
        refs2 = _refs(refs)
        content = {"kind": kind, "data": data, "refs": refs2, "by": by}
        aid = _hash(content)
        if aid in self._a:
            return self._a[aid]
        a = Artifact(aid, kind, data, refs2, by, time.time())
        with self.path.open("a", encoding="utf-8") as f:
            f.write(_json({"id": a.id, "kind": a.kind, "data": a.data,
                           "refs": a.refs, "by": a.by, "t": a.t}) + "\n")
        self._a[a.id] = a
        return a

    def get(self, aid: str) -> Artifact:
        return self._a[aid]

    def all(self, kind: str | None = None) -> list[Artifact]:
        return [a for a in self._a.values() if kind is None or a.kind == kind]

    def children(self, aid: str, kind: str | None = None, role: str | None = None) -> list[Artifact]:
        out = []
        for a in self._a.values():
            roles = [role] if role else a.refs.keys()
            if any(aid in a.refs.get(r, ()) for r in roles):
                if kind is None or a.kind == kind:
                    out.append(a)
        return out

    def related(self, aid: str, role: str) -> list[Artifact]:
        return [self._a[x] for x in self._a[aid].refs.get(role, ())]


class Kernel:
    """Executes a declared job and signs the resulting record.

    The base Kernel executes locally and is not a security sandbox. WorkerKernel delegates to a
    configured worker. Either way a run is identifiable: declared inputs are hashed, and the
    tool, command, runtime, platform, environment, and seed are recorded inside signed evidence.
    """

    def __init__(self, key_path: str | Path):
        self.key_path = Path(key_path)
        if not self.key_path.exists():
            self.key_path.parent.mkdir(parents=True, exist_ok=True)
            self.key_path.write_bytes(os.urandom(32))
            try:
                self.key_path.chmod(0o600)
            except OSError:
                pass
        self._key = self.key_path.read_bytes()

    def _sig(self, kind: str, data: dict[str, Any], refs: dict[str, tuple[str, ...]], by: str) -> str:
        doc = {"kind": kind, "data": data, "refs": refs, "by": by}
        return hmac.new(self._key, _json(doc).encode(), hashlib.sha256).hexdigest()

    def verify(self, a: Artifact) -> bool:
        sig = a.data.get("_sig")
        if not sig:
            return False
        data = dict(a.data)
        data.pop("_sig", None)
        return hmac.compare_digest(sig, self._sig(a.kind, data, a.refs, a.by))

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
        declared = {}
        for raw in inputs:
            p = Path(raw)
            p = p if p.is_absolute() else cwd / p
            declared[str(raw)] = _digest(p.resolve())

        env_patch = dict(env or {})
        if seed is not None:
            env_patch.setdefault("PYTHONHASHSEED", str(seed))
            env_patch.setdefault("RSCIENCE_SEED", str(seed))
        child_env = os.environ.copy()
        child_env.update(env_patch)

        run = {
            "runner": "local",
            "argv": argv,
            "inputs": declared,
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "env": env_patch,
            "seed": seed,
        }
        run["fingerprint"] = hashlib.sha256(_json(run).encode()).hexdigest()

        start = time.perf_counter()
        p = subprocess.run(
            argv, cwd=cwd, capture_output=True, text=True,
            timeout=timeout, check=False, env=child_env,
        )
        data = {
            "lane": lane,
            "semantics": semantics,
            "verdict": "pass" if p.returncode == 0 else "fail",
            "returncode": p.returncode,
            "stdout": p.stdout[-50_000:],
            "stderr": p.stderr[-50_000:],
            "elapsed_s": time.perf_counter() - start,
            "run": run,
        }
        r: dict[str, Iterable[str]] = {"target": [target.id]}
        if tool:
            r["tool"] = [tool.id]
        refs = _refs(r)
        data["_sig"] = self._sig("evidence", data, refs, "kernel")
        return ledger.put("evidence", data, refs, by="kernel")

    def use(
        self,
        ledger: Ledger,
        tool: Artifact,
        target: Artifact,
        values: dict[str, Any] | None = None,
        *,
        cwd: str | Path = ".",
        seed: int | None = None,
    ) -> Artifact:
        if tool.kind != "tool":
            raise ValueError("tool artifact required")
        values = values or {}
        argv = [str(x).format_map(values) for x in tool.data["argv"]]
        inputs = [str(x).format_map(values) for x in tool.data.get("inputs", [])]
        env = {k: str(v).format_map(values) for k, v in tool.data.get("env", {}).items()}
        return self.run(
            ledger, target, tool.data["lane"], argv,
            cwd=cwd,
            semantics=tool.data.get("semantics", tool.data.get("verb", "tool")),
            timeout=float(tool.data.get("timeout", 120.0)),
            tool=tool, inputs=inputs, env=env, seed=seed,
        )


def valid_evidence(ledger: Ledger, kernel: Kernel, target: Artifact, lane: str) -> list[Artifact]:
    return [
        a for a in ledger.children(target.id, "evidence", "target")
        if a.data.get("lane") == lane and kernel.verify(a)
    ]


def _passed(ledger: Ledger, kernel: Kernel, target: Artifact, lane: str) -> bool:
    return any(a.data.get("verdict") == "pass" for a in valid_evidence(ledger, kernel, target, lane))


def frontier(ledger: Ledger, kernel: Kernel) -> list[Task]:
    """Derive work from missing epistemic edges."""
    tasks: list[Task] = []

    for claim in ledger.all("claim"):
        ev = valid_evidence(ledger, kernel, claim, "math")
        if not ev:
            tasks.append(Task("verify", claim.id, "claim lacks adjudicated mathematical evidence", "math"))
        elif not any(x.data.get("verdict") == "pass" for x in ev):
            tasks.append(Task("revise", claim.id, "current mathematical checks fail", "math"))
        else:
            for a in ledger.related(claim.id, "assumptions"):
                if not ledger.children(a.id, "experiment", "challenges"):
                    tasks.append(Task("stress", a.id, "validated claim contains an untested assumption", "empirical"))

    for exp in ledger.all("experiment"):
        ev = valid_evidence(ledger, kernel, exp, "empirical")
        if not ev:
            tasks.append(Task("run", exp.id, "experiment lacks adjudicated result", "empirical"))
        elif not any(x.data.get("verdict") == "pass" for x in ev):
            tasks.append(Task("repair", exp.id, "empirical execution failed", "empirical"))
        elif exp.refs.get("discriminates") and not ledger.children(exp.id, "resolution", "experiment"):
            tasks.append(Task("resolve", exp.id, "discriminating experiment has not updated the live explanations"))

    for study in ledger.all("study"):
        claims = ledger.children(study.id, "claim", "study")
        exps = ledger.children(study.id, "experiment", "study")
        assumptions = [a for c in claims for a in ledger.related(c.id, "assumptions")]
        math_ok = any(_passed(ledger, kernel, c, "math") for c in claims)
        emp_ok = any(_passed(ledger, kernel, e, "empirical") for e in exps)
        stress_ok = all(
            any(_passed(ledger, kernel, e, "empirical")
                for e in ledger.children(a.id, "experiment", "challenges"))
            for a in assumptions
        )
        if math_ok and emp_ok and stress_ok and not ledger.children(study.id, "bridge", "study"):
            tasks.append(Task("reconcile", study.id, "validated theory and experiment have not been related"))

    for bridge in ledger.all("bridge"):
        if bridge.data.get("status") == "agreement":
            if not ledger.children(bridge.id, "invariant", "bridge"):
                tasks.append(Task("compress", bridge.id, "agreement has not been compressed into an invariant"))
        elif bridge.data.get("status") == "disagreement":
            if not ledger.children(bridge.id, "discrepancy", "bridge"):
                tasks.append(Task("diagnose", bridge.id, "disagreement has not been localized"))

    for d in ledger.all("discrepancy"):
        cls = d.data.get("class")
        if cls in {"in_scope", "mixed"}:
            explanations = ledger.children(d.id, "claim", "anomaly")
            if not explanations:
                tasks.append(Task("explain", d.id, "in-scope discrepancy has no explanatory conjecture"))
            elif len(explanations) >= 2 and all(_passed(ledger, kernel, h, "math") for h in explanations):
                if not ledger.children(d.id, "experiment", "discriminates"):
                    tasks.append(Task(
                        "discriminate", d.id,
                        "multiple admissible explanations survive; design an experiment that separates them",
                        "empirical",
                    ))
        elif cls == "out_of_scope":
            if not ledger.children(d.id, "regime", "discrepancy"):
                tasks.append(Task("scope", d.id, "failures lie outside the declared regime"))

    for r in ledger.all("resolution"):
        if r.data.get("surprise") and not ledger.children(r.id, "representation", "surprise"):
            tasks.append(Task(
                "expand", r.id,
                "no live explanation predicted the observation; test whether the representation is insufficient",
            ))

    for rep in ledger.all("representation"):
        status = rep.data.get("status")
        if status == "expanded" and not ledger.children(rep.id, "world", "basis"):
            tasks.append(Task(
                "promote", rep.id,
                "new coordinate resolves state aliasing and has not become part of the next world",
            ))
        elif status in {"blind", "mechanism"}:
            substrate_kind = "instrument" if status == "blind" else "mechanism"
            verb = "invent" if status == "blind" else "rethink"
            why = (
                "current coordinates alias outcomes; synthesize a measurement that refines the state"
                if status == "blind"
                else "current coordinates distinguish outcomes; synthesize a richer mechanism class"
            )
            substrates = ledger.children(rep.id, substrate_kind, "representation")
            requests = ledger.children(rep.id, "request", "representation")
            extensions = ledger.children(rep.id, "grammar_extension", "representation")
            # An extension blocks another identical synthesis attempt only while it is pending.
            pending = any(not ledger.children(e.id, "grammar", "extension") for e in extensions)
            if not substrates and not requests and not pending:
                tasks.append(Task(verb, rep.id, why))

    # Language growth is itself proposal -> consequence -> adoption.
    for extension in ledger.all("grammar_extension"):
        if extension.data.get("status") != "proposed":
            continue
        ev = valid_evidence(ledger, kernel, extension, "empirical")
        if not ev:
            tasks.append(Task(
                "validate", extension.id,
                "proposed grammar extension has not been prospectively checked",
                "empirical",
            ))
        elif any(x.data.get("verdict") == "pass" for x in ev):
            if not ledger.children(extension.id, "grammar", "extension"):
                tasks.append(Task(
                    "adopt", extension.id,
                    "validated composite has not been compressed into the active grammar",
                ))
        else:
            tasks.append(Task(
                "revise", extension.id,
                "proposed grammar extension failed prospective validation",
                "empirical",
            ))

    # New cognitive substrates are proposals until an external consequence validates them.
    for instrument in ledger.all("instrument"):
        if instrument.data.get("status") != "proposed":
            continue
        ev = valid_evidence(ledger, kernel, instrument, "empirical")
        if not ev:
            tasks.append(Task(
                "validate", instrument.id,
                "invented instrument has not been prospectively checked",
                "empirical",
            ))
        elif any(x.data.get("verdict") == "pass" for x in ev):
            if not ledger.children(instrument.id, "representation", "instrument"):
                tasks.append(Task(
                    "adopt", instrument.id,
                    "validated instrument has not been added to the representation",
                ))
        else:
            tasks.append(Task(
                "revise", instrument.id,
                "invented instrument failed prospective validation",
                "empirical",
            ))

    for mechanism in ledger.all("mechanism"):
        if mechanism.data.get("status") != "proposed":
            continue
        ev = valid_evidence(ledger, kernel, mechanism, "empirical")
        if not ev:
            tasks.append(Task(
                "validate", mechanism.id,
                "synthesized mechanism has not been prospectively checked",
                "empirical",
            ))
        elif any(x.data.get("verdict") == "pass" for x in ev):
            if not ledger.children(mechanism.id, "world", "basis"):
                tasks.append(Task(
                    "promote", mechanism.id,
                    "validated mechanism has not become part of the next research world",
                ))
        else:
            tasks.append(Task(
                "revise", mechanism.id,
                "synthesized mechanism failed prospective validation",
                "empirical",
            ))

    for inv in ledger.all("invariant"):
        if not ledger.children(inv.id, "world", "basis"):
            tasks.append(Task("promote", inv.id, "stable abstraction has not become a new research world"))

    return tasks


def compress(
    ledger: Ledger,
    bridge: Artifact,
    invariant: dict[str, Any],
    *,
    by: str = "synthesizer",
) -> Artifact:
    """Agreement -> stable abstraction."""
    if bridge.kind != "bridge" or bridge.data.get("status") != "agreement":
        raise ValueError("only an agreeing bridge can be compressed")
    return ledger.put("invariant", invariant, {"bridge": [bridge.id]}, by=by)


def promote(
    ledger: Ledger,
    basis: Artifact,
    next_world: dict[str, Any],
    *,
    world_refs: dict[str, Iterable[str]] | None = None,
    by: str = "synthesizer",
) -> Artifact:
    """A supported abstraction or representation -> new reasoning substrate."""
    if basis.kind not in {"invariant", "representation", "mechanism"}:
        raise ValueError("invariant, representation, or mechanism artifact required")
    refs: dict[str, Iterable[str]] = {"basis": [basis.id]}
    refs.update(world_refs or {})
    return ledger.put("world", next_world, refs, by=by)
