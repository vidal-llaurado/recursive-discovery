"""Record research decisions, their visible context and resulting artifacts."""
from __future__ import annotations
from .telemetry import emit

import hashlib
import json
from typing import Any, Iterable

from .core import Artifact, Ledger, Task


def _stable(x: Any) -> str:
    return json.dumps(x, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def digest(x: Any) -> str:
    return hashlib.sha256(_stable(x).encode()).hexdigest()


def model_identity(model: Any) -> str:
    """Return a policy identity label without credentials."""
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
    """Record a decision before execution.

    Store the state digest and visible artifact IDs to identify the decision's context.
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
    record = ledger.put("decision", data, refs, by=by)
    emit("decision.recorded", {"decision_id": record.id, "target_id": task.target,
                               "action": action, "visible_ids": data["visible_artifacts"]})
    return record


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
    record = ledger.put("decision_outcome", data, refs, by=by)
    emit("decision.outcome", {"decision_id": decision.id, "artifact_id": record.id,
                             "status": status, "produced_ids": produced_ids, "observed_ids": observed_ids})
    return record


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
    """Look up recorded outcomes for an action and state digest."""
    out: list[Artifact] = []
    for d in ledger.all("decision"):
        if d.data.get("state_digest") == state_digest and d.data.get("action") == action:
            out.extend(ledger.children(d.id, "decision_outcome", "decision"))
    return out


def execution_attempt(ledger: Ledger, target: Artifact, tool: Artifact, values: dict,
                      *, cwd=".", seeds=()) -> dict[str, Any]:
    """Describe an execution attempt, including current input file digests.

    Repeated attempts are permitted. This is not an atomic filesystem snapshot; signed
    execution records retain the inputs actually used.
    """
    from pathlib import Path
    from .core import _digest
    root = Path(cwd).resolve()
    inputs, errors = {}, {}
    for raw in tool.data.get("inputs", []):
        path = str(raw).format_map(values)
        try:
            p = Path(path)
            inputs[path] = _digest(p if p.is_absolute() else root / p)
        except OSError as e:
            # Missing input is a runtime blockage, not an observed falsification.
            errors[path] = type(e).__name__
    targets = list(target.refs.get("checks", ())) or [target.id]
    protocol = {"tool": tool.id, "values": values,
                "argv": [str(x).format_map(values) for x in tool.data["argv"]],
                "input_digests": inputs, "input_errors": errors, "seeds": list(seeds),
                "declared_protocol": target.data.get("protocol"), "cwd": str(root)}
    return {"signature": attempt_signature(ledger, targets=targets,
            inputs=target.refs.get("inputs", ()), protocol=protocol),
            "targets": targets, "protocol": protocol}


def attempt_history(ledger: Ledger, kernel, *, targets: Iterable[str] = (),
                    signature: str | None = None, accept=None,
                    offset: int = 0, limit: int = 8) -> dict[str, Any]:
    """Summarize recorded decisions and outcomes.

    Execution status and researcher interpretation remain separate fields.
    """
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 30:
        raise ValueError("invalid attempt-history page")
    targets = set(targets)
    rows = []
    for d in ledger.all("decision"):
        payload = d.data.get("payload", {})
        plans = ([payload["attempt"]] if isinstance(payload.get("attempt"), dict)
                 else payload.get("plans", []))
        for attempt in plans:
            if not isinstance(attempt, dict) or not attempt.get("signature"):
                continue
            if signature and attempt.get("signature") != signature:
                continue
            if targets and not targets.intersection(attempt.get("targets", ())):
                continue
            if accept and (not accept(d.id) or any(not accept(x) for x in attempt.get("targets", ()))):
                continue
            outcomes = [x for x in ledger.children(d.id, "decision_outcome", "decision")
                        if accept is None or accept(x.id)]
            evidence = {}
            for outcome in outcomes:
                for aid in outcome.refs.get("produced", ()):
                    a = ledger.get(aid)
                    tool = attempt.get("protocol", {}).get("tool")
                    if (a.kind == "evidence" and (accept is None or accept(a.id))
                            and (len(plans) == 1 or tool in a.refs.get("tool", ()))):
                        evidence[a.id] = {"id": a.id, "authenticated": kernel.verify(a),
                            "execution_verdict": a.data.get("verdict"),
                            "returncode": a.data.get("returncode"), "semantics": a.data.get("semantics")}
            rows.append({"decision": d.id, "signature": attempt["signature"],
                         "targets": attempt.get("targets", []),
                         "outcomes": [{"id": x.id, "status": x.data.get("status"),
                                       "summary": x.data.get("summary")} for x in outcomes],
                         "execution_records": list(evidence.values()),
                         "authority": "execution_history_not_scientific_verdict"})
    rows.reverse()
    return {"items": rows[offset:offset+limit], "total": len(rows), "offset": offset,
            "next": offset+limit if offset+limit < len(rows) else None}


def attempt_signature(ledger: Ledger, *, targets: Iterable[str], inputs: Iterable[str] = (),
                      protocol: dict | None = None) -> str:
    """Identify an attempt from its declared target, inputs and protocol."""
    from .investigation import _ids, digest

    return digest({"targets": sorted(_ids(ledger, targets)), "inputs": sorted(_ids(ledger, inputs)),
                   "protocol": protocol or {}})
