"""One-shot prospective evaluation with secret bytes kept out of the scientific ledger.

This local vault models the information boundary; it is not an OS security sandbox. A
production deployment should place the vault and evaluator in a separate service/worker.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import secrets
from typing import Any

from .core import Artifact, Kernel, Ledger


class Vault:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _meta(self, handle: str) -> Path:
        return self.root / f"{handle}.meta.json"

    def _data(self, handle: str) -> Path:
        return self.root / f"{handle}.sealed.json"

    def seal_json(self, value: Any, *, schema: dict[str, Any] | None = None) -> dict[str, Any]:
        raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        handle = secrets.token_hex(16)
        self._data(handle).write_bytes(raw)
        meta = {
            "handle": handle,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "schema": schema or {},
            "n": len(value) if hasattr(value, "__len__") else None,
            "retired": False,
        }
        self._meta(handle).write_text(json.dumps(meta, sort_keys=True))
        return {k: v for k, v in meta.items() if k != "retired"}

    def metadata(self, handle: str) -> dict[str, Any]:
        return json.loads(self._meta(handle).read_text())

    def path(self, handle: str) -> Path:
        meta = self.metadata(handle)
        if meta["retired"]:
            raise RuntimeError("sealed evaluation has been retired")
        return self._data(handle)

    def retire(self, handle: str) -> None:
        meta = self.metadata(handle)
        meta["retired"] = True
        self._meta(handle).write_text(json.dumps(meta, sort_keys=True))


def register_test(
    ledger: Ledger,
    metadata: dict[str, Any],
    *,
    refs: dict[str, list[str]] | None = None,
) -> Artifact:
    """The ledger receives only an opaque handle + digest/schema, never sealed bytes."""
    return ledger.put("sealed_test", metadata, refs, by="sealed-vault")


def commit(
    ledger: Ledger,
    proposal: Artifact,
    test: Artifact,
    *,
    metric: str,
    decision: str,
    predictions: Any = None,
    by: str = "controller",
) -> Artifact:
    """Commit proposal, predictions, metric and decision rule before revealing the test."""
    if test.kind != "sealed_test":
        raise ValueError("sealed_test artifact required")
    return ledger.put(
        "commitment",
        {"metric": metric, "decision": decision, "predictions": predictions},
        {"proposal": [proposal.id], "test": [test.id]},
        by=by,
    )


def evaluate(
    ledger: Ledger,
    kernel: Kernel,
    tool: Artifact,
    commitment: Artifact,
    vault: Vault,
    *,
    values: dict[str, Any] | None = None,
    cwd: str | Path = ".",
) -> tuple[Artifact, Artifact | None]:
    """Reveal a sealed set exactly once to a consequence tool, then retire it."""
    if commitment.kind != "commitment":
        raise ValueError("commitment artifact required")
    tests = ledger.related(commitment.id, "test")
    if len(tests) != 1:
        raise ValueError("commitment must reference exactly one sealed test")
    test = tests[0]
    handle = test.data["handle"]
    secret = vault.path(handle)
    vals = dict(values or {})
    vals["sealed"] = str(secret)

    try:
        evidence = kernel.use(ledger, tool, commitment, vals, cwd=cwd, seed=0)
    finally:
        # Once an evaluator has seen the bytes, the test is no longer prospective.
        vault.retire(handle)

    if evidence.data.get("verdict") != "pass":
        return evidence, None
    lines = [x for x in evidence.data.get("stdout", "").splitlines() if x.strip()]
    result = json.loads(lines[-1]) if lines else {}
    evaluation = ledger.put(
        "evaluation",
        {
            "metric": commitment.data["metric"],
            "decision": commitment.data["decision"],
            "result": result,
            "test_sha256": test.data["sha256"],
            "retired": True,
        },
        {"commitment": [commitment.id], "test": [test.id], "evidence": [evidence.id]},
        by="sealed-evaluator",
    )
    return evidence, evaluation
