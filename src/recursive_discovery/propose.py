"""Search for and commit candidate sets, which later activation turns into live objects."""
from __future__ import annotations

from collections.abc import Callable
import json
from typing import Any, TYPE_CHECKING

from .context import compile_context, render_context
from .core import Artifact, Kernel, Ledger, Task
from .search import before, remember, remember_text


if TYPE_CHECKING:
    from .investigation import Scope


Model = Callable[[str], str]
Retriever = Callable[[str], list[dict[str, Any]]]
Reader = Callable[[dict[str, Any]], str]


def _json(text: str) -> Any:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        text = text.rsplit("```", 1)[0]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        starts = [i for i in (text.find("{"), text.find("[")) if i >= 0]
        if not starts:
            raise
        start = min(starts)
        for end in range(len(text), start, -1):
            try:
                return json.loads(text[start:end])
            except json.JSONDecodeError:
                pass
        raise


def _ask(model: Model, instruction: dict[str, Any]) -> Any:
    return _json(model(json.dumps(instruction, ensure_ascii=False, separators=(",", ":"))))


def candidate_set(
    ledger: Ledger,
    kernel: Kernel,
    task: Task,
    model: Model,
    *,
    actor: str = "model",
    retriever: Retriever | None = None,
    reader: Reader | None = None,
    source_before: str | None = None,
    max_queries: int = 3,
    max_reads: int = 2,
    max_candidates: int = 5,
    max_context_chars: int = 12_000,
) -> list[Artifact]:
    """Search if useful, then ask for a small diverse set of falsifiable commitments."""
    packet = compile_context(
        ledger, kernel, task,
        max_chars=max_context_chars,
        source_before=source_before,
    )

    if retriever is not None and max_queries > 0:
        scout = _ask(model, {
            "mode": "search_queries",
            "task": packet["task"],
            "context": render_context(packet),
            "instruction": (
                "Return JSON {queries:[...]}. Use at most the allowed number. "
                "Queries should retrieve literature that could change the candidate set."
            ),
            "max_queries": max_queries,
        })
        seen = set()
        for q in list(scout.get("queries", []))[:max_queries]:
            q = " ".join(str(q).split())
            if not q or q in seen:
                continue
            seen.add(q)
            rows = before(retriever(q), source_before)
            _, sources = remember(
                ledger, q, rows,
                refs={"target": [task.target]},
                provider=(rows[0].get("provider", "retriever") if rows else "retriever"),
            )
            if reader is not None:
                for source in sources[:max_reads]:
                    try:
                        text = reader(source.data)
                        if text:
                            remember_text(ledger, source, text)
                    except Exception:
                        pass
        packet = compile_context(
            ledger, kernel, task,
            max_chars=max_context_chars,
            source_before=source_before,
        )

    answer = _ask(model, {
        "mode": "candidate_set",
        "task": packet["task"],
        "context": render_context(packet),
        "instruction": (
            "Return JSON {candidates:[...]}. Produce mechanistically distinct, externally "
            "checkable commitments, not chain-of-thought. Each candidate must contain "
            "object_kind, title, commitment, predictions, falsifier, assumptions, source_ids, "
            "gain in [0,1], cost > 0, optional payload, and optional links mapping semantic "
            "reference roles to artifact ids already visible in context."
        ),
        "max_candidates": max_candidates,
    })

    out, signatures = [], set()
    available_sources = {a["id"] for a in packet["artifacts"] if a["kind"] == "source"}
    available_ids = {a["id"] for a in packet["artifacts"]}
    for raw in list(answer.get("candidates", []))[:max_candidates]:
        kind = str(raw.get("object_kind", "claim"))
        commitment = " ".join(str(raw.get("commitment", "")).split())
        predictions = raw.get("predictions", [])
        if not commitment:
            continue
        sig = json.dumps([kind, commitment, predictions], sort_keys=True, ensure_ascii=False)
        if sig in signatures:
            continue
        signatures.add(sig)
        gain = float(raw.get("gain", .5))
        cost = float(raw.get("cost", 1.0))
        if not 0 <= gain <= 1 or cost <= 0:
            continue
        source_ids = [x for x in raw.get("source_ids", []) if x in available_sources]
        data = {
            "object_kind": kind,
            "title": str(raw.get("title", commitment[:80])),
            "commitment": commitment,
            "predictions": predictions,
            "falsifier": raw.get("falsifier"),
            "assumptions": raw.get("assumptions", []),
            "gain": gain,
            "cost": cost,
            "payload": raw.get("payload", {}),
            "links": {
                str(role): [str(x) for x in ids if str(x) in available_ids]
                for role, ids in (raw.get("links", {}) or {}).items()
                if isinstance(ids, list)
            },
        }
        refs = {"target": [task.target]}
        if source_ids:
            refs["sources"] = source_ids
        out.append(ledger.put("candidate", data, refs, by=actor))
    return out


def activate(ledger: Ledger, candidate: Artifact, *, by: str = "controller",
             admission: Artifact | None = None) -> Artifact:
    """Turn a selected candidate into a live scientific object; unselected candidates stay inert."""
    if candidate.kind != "candidate":
        raise ValueError("candidate artifact required")
    if candidate.refs.get("response"):
        from .investigation import Scope, Conflict
        if (admission is None or admission.kind != "admission"
                or candidate.id not in admission.refs.get("candidates", ())
                or admission.refs.get("response") != candidate.refs.get("response")):
            raise ValueError("external contribution requires explicit admission")
        if ledger.get(admission.id) != admission:
            raise ValueError("admission must be a recorded ledger artifact")
        scope = Scope(ledger, admission.refs["mission"][0])
        if any(not scope.branch_active(b) for b in admission.refs.get("branch", ())):
            raise Conflict("admission branch is no longer active")
        if not scope.allowed(candidate.id):
            raise PermissionError("contribution is outside current mission information boundary")
        if admission.refs.get("revision") != (scope.revision.id,):
            raise Conflict("mission changed since admission")
    kind = candidate.data["object_kind"]
    data = dict(candidate.data.get("payload", {}))
    if kind == "claim":
        data.setdefault("statement", candidate.data["commitment"])
        data.setdefault("predictions", candidate.data.get("predictions", []))
        data.setdefault("falsifier", candidate.data.get("falsifier"))
    elif kind == "experiment":
        data.setdefault("protocol", candidate.data["commitment"])
    else:
        data.setdefault("proposal", candidate.data["commitment"])

    refs = {"candidate": [candidate.id]}
    for role in ("mission", "branch", "passages"):
        if candidate.refs.get(role):
            refs[role] = list(candidate.refs[role])
    if admission is not None:
        refs["admission"] = [admission.id]
        if admission.refs.get("branch"):
            refs["branch"] = list(admission.refs["branch"])
    if candidate.data.get("citations"):
        data["citations"] = candidate.data["citations"]
    targets = candidate.refs.get("target", ())
    if targets:
        refs["study" if ledger.get(targets[0]).kind == "study" else "target"] = list(targets)
    for role, ids in candidate.data.get("links", {}).items():
        if ids:
            refs[str(role)] = list(ids)
    if candidate.refs.get("sources"):
        refs["sources"] = list(candidate.refs["sources"])
    return ledger.put(kind, data, refs, by=by)


def store_candidates(
    ledger: Ledger,
    task: Task,
    raws: list[dict[str, Any]],
    *,
    visible_ids: set[str],
    source_ids: set[str],
    spans: dict[Any, list[tuple[int, int]]] | None = None,
    actor: str = "research-model",
    limit: int = 8,
    scope: Scope | None = None,
    branch: str | None = None,
) -> list[Artifact]:
    out, seen = [], set()
    for raw in raws[:limit]:
        from .capsules import RESERVED
        if not isinstance(raw, dict) or not isinstance(raw.get("payload", {}), dict):
            raise ValueError("candidate and payload must be objects")
        kind = str(raw.get("object_kind", "claim"))
        if kind in RESERVED:
            raise ValueError("candidates cannot mint control records or observations")
        commitment = " ".join(str(raw.get("commitment", "")).split())
        if not commitment:
            continue
        predictions = raw.get("predictions", [])
        sig = json.dumps([kind, commitment, predictions], sort_keys=True, ensure_ascii=False)
        if sig in seen:
            continue
        seen.add(sig)
        links = {}
        for role, ids in (raw.get("links", {}) or {}).items():
            if role in {"mission", "branch", "program", "scope", "response", "admission", "candidate"}:
                raise ValueError("candidate cannot assign control references")
            if isinstance(ids, list):
                keep = [str(x) for x in ids if str(x) in visible_ids]
                if keep:
                    links[str(role)] = keep
        refs = {"target": [task.target]}
        if scope:
            refs["mission"] = [scope.root.id]
        if branch:
            refs["branch"] = [branch]
        sources = [str(x) for x in raw.get("source_ids", []) if str(x) in source_ids]
        if sources:
            refs["sources"] = sources
        data = {
            "object_kind": kind,
            "title": str(raw.get("title", commitment[:100])),
            "commitment": commitment,
            "predictions": predictions,
            "falsifier": raw.get("falsifier"),
            "assumptions": raw.get("assumptions", []),
            "gain": raw.get("gain"),
            "cost": raw.get("cost"),
            "payload": raw.get("payload", {}),
            "links": links,
        }
        data["payload"] = {k: v for k, v in data["payload"].items() if k != "citations"}
        citations = raw.get("citations", [])
        if not isinstance(citations, list) or len(citations) > 32:
            raise ValueError("citations must be a list of at most 32 spans")
        for citation in citations:
            aid, start, end = citation["id"], citation["start"], citation["end"]
            field = citation.get("field", "text")
            if not isinstance(field, str):
                raise ValueError("citation field must name the delivered coordinate system")
            ranges = (spans or {}).get((aid, field), (spans or {}).get(aid, []) if field == "text" else [])
            if (aid not in visible_ids or type(start) is not int or type(end) is not int or start >= end
                    or not any(lo <= start < end <= hi for lo, hi in ranges)):
                raise ValueError("citation must be within a passage actually shown in this session")
        if citations:
            data["citations"] = [{**{k: c[k] for k in ("id", "start", "end")},
                                  **({"field": c["field"]} if c.get("field", "text") != "text" else {})}
                                 for c in citations]
            refs["passages"] = list(dict.fromkeys(c["id"] for c in citations))
        out.append(ledger.put("candidate", data, refs, by=actor))
    return out
