"""Decision/outcome traces for long-horizon research.

The module records what the researcher could see, what it chose, and what artifacts followed.
It does not optimize an exploration policy, and lookup is not a counterfactual simulator.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable

from .core import Artifact, Ledger, Task


def _stable(x: Any) -> str:
    return json.dumps(x, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def digest(x: Any) -> str:
    return hashlib.sha256(_stable(x).encode()).hexdigest()


def model_identity(model: Any) -> str:
    """Return a non-secret, stable-enough identity label for a proposal policy."""
    explicit = getattr(model, "model_id", None) or getattr(model, "name", None)
    if explicit:
        return str(explicit)
    cls = type(model)
    return f"{cls.__module__}.{cls.__qualname__}"


def frontier_snapshot(tasks: Iterable[Task]) -> list[dict[str, str]]:
    return [
        {"verb": t.verb, "lane": t.lane, "target": t.target, "why": t.why}
        for t in tasks
    ]


def record_decision(
    ledger: Ledger,
    task: Task,
    *,
    action: str,
    model: Any,
    state: Any,
    frontier: Iterable[Task] = (),
    visible_artifacts: Iterable[str] = (),
    available_actions: Iterable[str] = (),
    available_instruments: Iterable[str] = (),
    payload: dict[str, Any] | None = None,
    parent: Artifact | None = None,
    by: str = "research-model",
) -> Artifact:
    """Commit one research decision before its consequence is observed.

    `state` is hashed so the trace can identify the exact decision state without duplicating the
    full context packet.  Artifact IDs visible to the model are retained explicitly because they
    are the replay boundary: later replay is exact only inside the realized tree.
    """
    refs: dict[str, list[str]] = {"target": [task.target]}
    if parent is not None:
        refs["parent_decision"] = [parent.id]
    data = {
        "task": {"verb": task.verb, "lane": task.lane, "target": task.target, "why": task.why},
        "state_digest": digest(state),
        "frontier": frontier_snapshot(frontier),
        "visible_artifacts": list(dict.fromkeys(str(x) for x in visible_artifacts)),
        "available_actions": list(dict.fromkeys(str(x) for x in available_actions)),
        "available_instruments": list(dict.fromkeys(str(x) for x in available_instruments)),
        "action": action,
        "payload": payload or {},
        "policy": model_identity(model),
    }
    return ledger.put("decision", data, refs, by=by)


def record_outcome(
    ledger: Ledger,
    decision: Artifact,
    *,
    status: str,
    produced: Iterable[Artifact | str] = (),
    observed: Iterable[Artifact | str] = (),
    summary: Any = None,
    by: str = "research-runtime",
) -> Artifact:
    """Attach the realized consequence of a previously committed decision."""
    produced_ids = [x.id if isinstance(x, Artifact) else str(x) for x in produced]
    observed_ids = [x.id if isinstance(x, Artifact) else str(x) for x in observed]
    refs: dict[str, list[str]] = {"decision": [decision.id]}
    if produced_ids:
        refs["produced"] = list(dict.fromkeys(produced_ids))
    if observed_ids:
        refs["observed"] = list(dict.fromkeys(observed_ids))
    data = {
        "status": status,
        "action": decision.data.get("action"),
        "state_digest": decision.data.get("state_digest"),
        "summary": summary,
    }
    return ledger.put("decision_outcome", data, refs, by=by)


def trace(ledger: Ledger, *, target: str | None = None) -> list[dict[str, Any]]:
    """Return committed decisions with their realized outcomes in time order."""
    decisions = ledger.all("decision")
    if target is not None:
        decisions = [d for d in decisions if target in d.refs.get("target", ())]
    rows = []
    for d in decisions:
        outcomes = ledger.children(d.id, "decision_outcome", "decision")
        rows.append({
            "decision": d,
            "outcomes": outcomes,
        })
    return rows


def replay_lookup(
    ledger: Ledger,
    *,
    state_digest: str,
    action: str,
) -> list[Artifact]:
    """Return realized outcomes for an action already executed from the same recorded state.

    This is deliberately a lookup, not a counterfactual world model.  No answer is fabricated for
    branches the historical discovery tree never visited.
    """
    out: list[Artifact] = []
    for d in ledger.all("decision"):
        if d.data.get("state_digest") == state_digest and d.data.get("action") == action:
            out.extend(ledger.children(d.id, "decision_outcome", "decision"))
    return out
