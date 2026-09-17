"""Separate one-shot sealed evaluation service.

The proposing model should not receive the service token or filesystem. The service owns secret
bytes, evaluates pre-registered commands, retires tests after first access, and signs an
attestation. A local Kernel can then verify that attestation as an external trust root.
"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen
from typing import Any
import hashlib
import hmac
import json
import os
import secrets
import subprocess
import tempfile
import time

from .core import Artifact, Kernel, Ledger


def _json(x: Any) -> bytes:
    return json.dumps(x, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


class SealedEvaluator:
    def __init__(self, root: str | Path, *, key_path: str | Path, registry: dict[str, dict[str, Any]]):
        self.root = Path(root)
        self.tests = self.root / "tests"
        self.tests.mkdir(parents=True, exist_ok=True)
        self.key_path = Path(key_path)
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.key_path.exists():
            self.key_path.write_bytes(os.urandom(32))
        self.key = self.key_path.read_bytes()
        self.registry = registry

    def _meta(self, h: str) -> Path: return self.tests / f"{h}.meta.json"
    def _data(self, h: str) -> Path: return self.tests / f"{h}.sealed.json"

    def seal(self, value: Any, schema: dict[str, Any] | None = None) -> dict[str, Any]:
        raw = _json(value)
        handle = secrets.token_hex(16)
        self._data(handle).write_bytes(raw)
        meta = {
            "handle": handle, "sha256": hashlib.sha256(raw).hexdigest(),
            "schema": schema or {}, "retired": False, "created_at": time.time(),
        }
        self._meta(handle).write_text(json.dumps(meta, sort_keys=True))
        return {k: v for k, v in meta.items() if k != "retired"}

    def evaluate(self, handle: str, evaluator: str, commitment: dict[str, Any]) -> dict[str, Any]:
        meta = json.loads(self._meta(handle).read_text())
        if meta["retired"]:
            raise RuntimeError("sealed test retired")
        if evaluator not in self.registry:
            raise KeyError(f"unknown evaluator: {evaluator}")
        spec = self.registry[evaluator]
        secret = self._data(handle)
        with tempfile.TemporaryDirectory() as td:
            commitment_path = Path(td) / "commitment.json"
            commitment_path.write_text(json.dumps(commitment, sort_keys=True))
            vals = {"sealed": str(secret), "commitment": str(commitment_path)}
            argv = [str(x).format_map(vals) for x in spec["argv"]]
            cwd = spec.get("cwd") or td
            try:
                p = subprocess.run(
                    argv, cwd=cwd, capture_output=True, text=True, check=False,
                    timeout=float(spec.get("timeout", 300)),
                    env={"PATH": os.environ.get("PATH", ""), "HOME": td, "LANG": "C.UTF-8"},
                )
                result = {
                    "handle": handle, "test_sha256": meta["sha256"], "evaluator": evaluator,
                    "commitment_sha256": hashlib.sha256(_json(commitment)).hexdigest(),
                    "returncode": p.returncode, "stdout": p.stdout[-50_000:], "stderr": p.stderr[-50_000:],
                    "retired": True, "evaluated_at": time.time(),
                }
            finally:
                meta["retired"] = True
                self._meta(handle).write_text(json.dumps(meta, sort_keys=True))
        result["sig"] = hmac.new(self.key, _json(result), hashlib.sha256).hexdigest()
        return result


class _Handler(BaseHTTPRequestHandler):
    evaluator: SealedEvaluator
    token: str

    def log_message(self, *_):
        return

    def _auth(self) -> bool:
        return hmac.compare_digest(self.headers.get("Authorization", ""), f"Bearer {self.token}")

    def _read(self) -> dict[str, Any]:
        return json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")

    def _send(self, code: int, payload: dict[str, Any]) -> None:
        raw = _json(payload)
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)

    def do_POST(self):
        if not self._auth():
            self._send(403, {"error": "forbidden"}); return
        try:
            doc = self._read()
            if self.path == "/seal":
                out = self.evaluator.seal(doc["value"], doc.get("schema"))
            elif self.path == "/evaluate":
                out = self.evaluator.evaluate(doc["handle"], doc["evaluator"], doc["commitment"])
            else:
                self._send(404, {"error": "not found"}); return
            self._send(200, out)
        except Exception as e:
            self._send(400, {"error": f"{type(e).__name__}: {e}"})


def serve(evaluator: SealedEvaluator, *, token: str, host: str = "127.0.0.1", port: int = 8765) -> None:
    cls = type("SealedHandler", (_Handler,), {"evaluator": evaluator, "token": token})
    ThreadingHTTPServer((host, port), cls).serve_forever()


class SealedClient:
    def __init__(self, base_url: str, token: str):
        self.base = base_url.rstrip("/"); self.token = token

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        req = Request(
            self.base + path, data=_json(payload), method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.token}"},
        )
        with urlopen(req, timeout=330) as r:
            out = json.loads(r.read())
        if "error" in out:
            raise RuntimeError(out["error"])
        return out

    def seal(self, value: Any, schema: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._post("/seal", {"value": value, "schema": schema or {}})

    def evaluate(self, handle: str, evaluator: str, commitment: dict[str, Any]) -> dict[str, Any]:
        return self._post("/evaluate", {"handle": handle, "evaluator": evaluator, "commitment": commitment})


def verify_remote(
    ledger: Ledger,
    kernel: Kernel,
    commitment: Artifact,
    attestation: dict[str, Any],
    *,
    service_key: str | Path,
    cwd: str | Path = ".",
) -> tuple[Artifact, Artifact | None]:
    """Turn a service-signed attestation into ordinary Kernel-signed evidence."""
    here = Path(__file__).with_name("attest_check.py").resolve()
    with tempfile.TemporaryDirectory() as td:
        att = Path(td) / "attestation.json"
        att.write_text(json.dumps(attestation, sort_keys=True))
        evidence = kernel.run(
            ledger, commitment, "empirical",
            [os.fspath(Path(os.sys.executable)), os.fspath(here), os.fspath(att), os.fspath(Path(service_key).resolve())],
            cwd=cwd, semantics="sealed_service_attestation",
            inputs=[here, att, Path(service_key).resolve()], seed=0,
        )
    if evidence.data.get("verdict") != "pass":
        return evidence, None
    evaluation = ledger.put("evaluation", {
        "metric": commitment.data.get("metric"), "decision": commitment.data.get("decision"),
        "result": json.loads(attestation.get("stdout") or "{}") if attestation.get("stdout", "").strip().startswith("{") else attestation.get("stdout"),
        "test_sha256": attestation.get("test_sha256"), "retired": True,
        "service_evaluator": attestation.get("evaluator"),
    }, {"commitment": [commitment.id], "evidence": [evidence.id]}, by="sealed-service")
    return evidence, evaluation
