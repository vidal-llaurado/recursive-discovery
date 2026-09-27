"""Compile a task-local research packet under a context bound.

Retrieval uses the scientific graph first and lexical search second.
"""
from __future__ import annotations
from .telemetry import emit, traced, enabled

from collections import deque
from copy import deepcopy
import json
import re
from typing import Any

from .core import Artifact, Kernel, Ledger, Task
from .reading import available, excerpt, search_local
from .investigation import Scope
from .schema import context_dependencies, describe

DERIVED = {"measurement", "empirical_summary", "evaluation", "instrument_result"}
RETRIEVED = {"source", "source_text", "search"}


def _grounded(a: Artifact, ledger: Ledger, kernel: Kernel, seen: set[str] | None = None) -> bool:
    seen = set(seen or ())
    if a.id in seen:
        return False
    seen.add(a.id)
    if a.kind == "evidence":
        return kernel.verify(a)
    if a.kind == "measurement":
        xs = ledger.related(a.id, "evidence")
        return bool(xs) and all(_grounded(x, ledger, kernel, seen) for x in xs)
    if a.kind == "empirical_summary":
        xs = ledger.related(a.id, "measurements")
        return bool(xs) and all(_grounded(x, ledger, kernel, seen) for x in xs)
    if a.kind == "evaluation":
        xs = ledger.related(a.id, "evidence")
        return bool(xs) and all(_grounded(x, ledger, kernel, seen) for x in xs)
    if a.kind == "instrument_result":
        xs = ledger.related(a.id, "evidence")
        return bool(xs) and all(_grounded(x, ledger, kernel, seen) for x in xs)
    return False


def authority(a: Artifact, ledger: Ledger, kernel: Kernel) -> str:
    if a.kind == "evidence":
        return "consequence" if kernel.verify(a) else "untrusted"
    if a.kind in RETRIEVED:
        return "retrieved_source"
    if a.kind in DERIVED and _grounded(a, ledger, kernel):
        return "derived_from_consequence"
    return "proposal_or_state"


def _clip(x: Any, limit: int = 900) -> Any:
    if isinstance(x, str):
        return x if len(x) <= limit else x[:limit] + "…"
    if isinstance(x, list):
        return [_clip(v, limit) for v in x[:16]]
    if isinstance(x, tuple):
        return [_clip(v, limit) for v in x[:16]]
    if isinstance(x, dict):
        return {str(k): _clip(v, limit) for k, v in list(x.items())[:32]}
    return x


def _data(a: Artifact, query: str = "") -> dict[str, Any]:
    d = dict(a.data)
    if a.kind == "evidence":
        run = d.get("run", {})
        return {
            "lane": d.get("lane"), "semantics": d.get("semantics"), "verdict": d.get("verdict"),
            "stdout": _clip(d.get("stdout", ""), 700), "stderr": _clip(d.get("stderr", ""), 350),
            "run": {
                "fingerprint": run.get("fingerprint"), "runner": run.get("runner"),
                "executor": run.get("executor"), "inputs": run.get("inputs"), "seed": run.get("seed"),
            },
        }
    if a.kind == "source":
        return {
            "provider": d.get("provider"), "title": d.get("title"), "authors": d.get("authors"),
            "published": d.get("published"), "abstract": _clip(d.get("abstract", ""), 1300),
            "url": d.get("url"), "doi": d.get("doi"),
        }
    if a.kind == "source_text":
        return excerpt(a, query)
    return _clip(d)


def _contains(ledger: Ledger, aid: str) -> bool:
    if hasattr(ledger, "contains"):
        return bool(ledger.contains(aid))
    try:
        ledger.get(aid)
        return True
    except KeyError:
        return False


def _neighbors(ledger: Ledger, aid: str) -> set[str]:
    a = ledger.get(aid)
    ids = {x for xs in a.refs.values() for x in xs}
    ids.update(x.id for x in ledger.children(aid))
    return ids


def _terms(task: Task, target: Artifact) -> str:
    values = [str(target.data[k]) for k in ("question", "statement", "title", "protocol", "text") if target.data.get(k)]
    raw = " ".join([*(values or [str(v) for v in target.data.values()]), task.why])
    words = re.findall(r"[A-Za-z0-9_+.-]{3,}", raw.lower())
    stop = {"this", "that", "with", "from", "have", "been", "into", "does", "than", "when", "what", "which", "where", "scientific", "research"}
    out, seen = [], set()
    for w in words:
        if w in stop or w in seen:
            continue
        seen.add(w); out.append(w)
        if len(out) >= 12:
            break
    return " ".join(out)


def _approx_tokens(obj: Any) -> int:
    return max(1, (len(json.dumps(obj, ensure_ascii=False, separators=(",", ":"))) + 3) // 4)


class ContextOverflow(ValueError):
    """Required material cannot fit; the caller must narrow or split the delivery."""


def artifact_view(ledger: Ledger, kernel: Kernel, a: Artifact, *, full: bool = False,
                  span: dict | None = None, query: str = "") -> dict[str, Any]:
    data = deepcopy(a.data if full else _data(a, query))
    row = {"id": a.id, "kind": a.kind, "authority": authority(a, ledger, kernel),
           "by": a.by, "refs": {k: list(v) for k, v in a.refs.items()}, "data": data,
           "complete": data == a.data}
    if not full and span is None and a.kind == "source_text" and data.get("status") == "ok":
        row["span"] = {"field": "text", "start": data["start"], "end": data["end"],
                       "total_chars": data["total_chars"]}
    if span is not None:
        field, start, end = span.get("field", "text"), span.get("start"), span.get("end")
        text = a.data.get(field)
        if not isinstance(text, str) or type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text):
            raise ValueError("span must name an exact nonempty string-field range")
        row.update(data={field: text[start:end]}, complete=False,
                   span={"field": field, "start": start, "end": end, "total_chars": len(text)})
    if a.refs.get("schema"):
        row["conformance"] = describe(ledger, a)
        for report in row["conformance"]:
            # A renderer must not reveal text outside the selected span/preview.
            report["display"] = [{"field": x["field"], "value": row["data"][x["field"]]}
                                 for x in report.get("display", []) if x["field"] in row["data"]]
    return row


@traced("context.compile")
def compile_context(
    ledger: Ledger, kernel: Kernel, task: Task, *, max_chars: int | None = 12_000,
    max_tokens: int | None = None, radius: int = 3, source_before: str | None = None,
    lexical_results: int = 20, scope: Scope | None = None,
    full: bool = False, selections: list[dict] | None = None,
) -> dict[str, Any]:
    """Select context for local sessions and external capsules.

    Selections specify complete artifacts or exact string-field spans. Without selections,
    graph locality and lexical rank supply optional context. Full mode, mission requirements
    and selected spans also require their descriptor context dependencies in full. Raise
    ContextOverflow when required material cannot fit. Other local targets may use previews.
    Token counts are approximate.
    """
    if max_chars is None and max_tokens is None:
        raise ValueError("a context bound is required")
    if max_chars is not None and max_chars < 1000:
        raise ValueError("max_chars too small")
    if max_tokens is not None and max_tokens < 250:
        raise ValueError("max_tokens too small")
    if radius < 0:
        raise ValueError("radius must be nonnegative")
    if scope and source_before:
        scope = Scope(ledger, scope.root.id, source_before=min(source_before, scope.source_before or source_before))
    target = ledger.get(task.target)
    mandatory = {task.target}
    required_payloads = set(scope.required) if scope else set()
    if selections is not None:
        for spec in selections:
            if not isinstance(spec, dict) or not isinstance(spec.get("id"), str):
                raise ValueError("each selection must identify an artifact")
            ledger.get(spec["id"])
            mandatory.add(spec["id"])
            required_payloads.add(spec["id"])
    seeds = set(mandatory)
    if scope:
        mandatory |= scope.required | scope.roots
        seeds |= mandatory | scope.working
        cuts = [x for x in (source_before, scope.source_before) if x]
        source_before = min(cuts) if cuts else None
    distance = {x: 0 for x in seeds}
    q = deque(sorted(seeds))
    while q:
        aid = q.popleft()
        if distance[aid] >= radius:
            continue
        for nid in sorted(_neighbors(ledger, aid)):
            if _contains(ledger, nid) and ledger.get(nid).data.get("format") == "rd.mission-capsule.v1":
                continue
            if _contains(ledger, nid) and nid not in distance:
                distance[nid] = distance[aid] + 1
                q.append(nid)
    # A descriptor's requested dependencies travel with a required object, within the bound.
    pending = list(mandatory)
    while pending:
        aid = pending.pop()
        for sid in context_dependencies(ledger, ledger.get(aid)):
            if not _contains(ledger, sid):
                continue
            newly_required = sid not in mandatory
            newly_complete = aid in required_payloads and sid not in required_payloads
            mandatory.add(sid)
            if newly_complete:
                required_payloads.add(sid)
            if newly_required or newly_complete:
                pending.append(sid)
            distance.setdefault(sid, 1)
    query = _terms(task, target)
    lexical = []
    retrieval_error = None
    if query and lexical_results > 0:
        try:
            lexical = search_local(ledger, query, limit=lexical_results, cutoff=source_before,
                                   accept=scope.allowed if scope else None)
        except Exception as e:
            retrieval_error = str(e)[:300]
    ranks = {a.id: i for i, a in enumerate(lexical)}
    ids = set(distance) | set(ranks)
    kind_bonus = {
        "evidence": 48, "empirical_summary": 40, "evaluation": 40, "measurement": 24,
        "claim": 34, "experiment": 34, "assumption": 34, "discrepancy": 34,
        "resolution": 38, "invariant": 34, "world": 30, "grammar": 26,
        "source": 24, "source_text": 25, "decision": 34, "decision_outcome": 44,
        "outcome": 24, "tool": 8, "bundle": 8, "candidate": 20, "memo": 16,
        "selection": 18, "instrument_result": 34, "instrument_call": 12,
        "instrument_definition": 32, "schema": 30,
    }
    # Dormancy is not permission to hide relevant negative evidence.
    historical = {"evidence", "measurement", "empirical_summary", "evaluation", "instrument_result",
                  "source", "source_text", "discrepancy", "resolution", "supersession"}

    def allowed(a):
        return scope.allowed(a.id) if scope else available(ledger, a, source_before)

    audit = [] if enabled() else None
    candidates = []
    for aid in ids:
        a = ledger.get(aid)
        if a.data.get("format") == "rd.mission-capsule.v1":
            continue
        detail = {"id": aid, "kind": a.kind, "graph_distance": distance.get(aid),
                  "lexical_rank": ranks.get(aid), "required": aid in mandatory,
                  "required_payload": aid in required_payloads} if audit is not None else None
        if not allowed(a):
            if audit is not None:
                audit.append({"id": aid, "disposition": "information_boundary"})
            if aid in mandatory:
                raise PermissionError(f"required artifact is outside information boundary: {aid}")
            continue
        if scope and aid not in mandatory and not scope.attending(aid) and a.kind not in historical:
            if audit is not None:
                audit.append({**detail, "disposition": "dormant"})
            continue
        score = 200 if aid == task.target else 100 - 17 * distance.get(aid, radius + 1)
        score += kind_bonus.get(a.kind, 0)
        if aid in ranks:
            score += 24 / (1 + ranks[aid])
        if authority(a, ledger, kernel) == "consequence":
            score += 22
        if a.kind == "outcome" and not a.data.get("closed", True):
            score += 18
        if audit is not None:
            audit.append({**detail, "score": score, "authority": authority(a, ledger, kernel), "disposition": "candidate"})
        candidates.append((score, a.t, a.id))
    candidates.sort(reverse=True)
    specs = list(selections) if selections is not None else [{"id": aid} for _, _, aid in candidates]
    supplied = {x["id"] for x in specs}
    specs = [{"id": aid} for aid in sorted(mandatory - supplied)] + specs
    packet = {
        "task": {"verb": task.verb, "lane": task.lane, "why": task.why, "target": task.target},
        "retrieval": {"graph_radius": radius, "lexical_query": query, "lexical_hits": len(ranks),
                      "error": retrieval_error, "explicit_selection": selections is not None},
        "artifacts": [], "omitted": 0, "budget_chars": max_chars, "budget_tokens": max_tokens,
        "used_chars": 0, "approx_tokens": 0, "required": sorted(mandatory),
    }
    if scope:
        packet["mission"] = scope.context_note()

    def encoded():
        measure_packet(packet)
        return render_context(packet)

    def fits():
        raw = encoded()
        return ((max_chars is None or len(raw) <= max_chars)
                and (max_tokens is None or max(1, (len(raw) + 3) // 4) <= max_tokens))

    required_rows, optional_rows = [], []
    seen = set()
    for spec in specs:
        sig = json.dumps(spec, sort_keys=True)
        if sig in seen:
            continue
        seen.add(sig)
        a = ledger.get(spec["id"])
        if not allowed(a):
            raise PermissionError(f"selected artifact outside information boundary: {a.id}")
        row = artifact_view(ledger, kernel, a, full=full or a.id in required_payloads,
                            span=spec.get("span"), query=query)
        if scope and scope.replacement_notes(a.id):
            row["superseded_for"] = scope.replacement_notes(a.id)
        (required_rows if a.id in mandatory or selections is not None else optional_rows).append(row)
    packet["artifacts"] = list(required_rows)
    if not fits() and not full and not scope and selections is None:
        # A local target may be represented by a recoverable handle, never silently dropped.
        for row in required_rows:
            row["data"] = {"omitted": "payload exceeds local context; read this artifact by ID"}
            row["complete"] = False
            row.pop("span", None)
            row.pop("conformance", None)
    if not fits():
        raise ContextOverflow("required context exceeds delivery budget; select spans or split the mission")
    for row in optional_rows:
        packet["artifacts"].append(row)
        if not fits():
            packet["artifacts"].pop()
            packet["omitted"] += 1
    # Omission counters can push a previously tight packet over the bound.
    while not fits() and len(packet["artifacts"]) > len(required_rows):
        packet["artifacts"].pop(); packet["omitted"] += 1
    if not fits():
        raise ContextOverflow("required context and omission metadata exceed delivery budget")
    encoded()
    if audit is not None:
        selected = {r["id"] for r in packet["artifacts"]}
        for item in audit:
            if item["disposition"] == "candidate":
                item["disposition"] = "selected" if item["id"] in selected else "explicit_selection" if selections is not None else "budget_omitted"
        emit("context.selection", {"target_id": task.target, "candidate_count": len(audit),
            "candidates": audit[:500], "truncated": len(audit) > 500,
            "selected_ids": sorted(selected), "required_ids": packet["required"],
            "used_chars": packet["used_chars"], "budget_chars": max_chars,
            "omitted": packet["omitted"], "graph_radius": radius,
            "lexical_hits": len(ranks), "retrieval_error": retrieval_error is not None},
            payload=lambda: {"query": query, "packet": packet})
    return packet


def render_context(packet: dict[str, Any]) -> str:
    return json.dumps(packet, ensure_ascii=False, separators=(",", ":"))


def measure_packet(packet: dict[str, Any]) -> None:
    """Account for the final serialized packet, including its accounting fields."""
    while True:
        chars = len(render_context(packet))
        tokens = (chars + 3) // 4
        if (packet.get("used_chars"), packet.get("approx_tokens")) == (chars, tokens):
            return
        packet.update(used_chars=chars, approx_tokens=tokens)

def fit_request(prompt: dict[str, Any], packet: dict[str, Any], max_chars: int) -> str:
    """Fit the complete session JSON, including tools and observations.

    Keep the target and latest observation before optional history. Replace oversized results
    with readable handles. Mutate both input dictionaries so logging matches the sent request.
    """
    prompt["omitted"] = {"observations": 0, "instruments": 0}
    while True:
        measure_packet(packet)
        prompt["context"] = render_context(packet)
        encoded = render_context(prompt)
        if len(encoded) <= max_chars:
            return encoded
        observations = prompt["recent_observations"]
        if len(observations) > 1:
            observations.pop(0)
            prompt["omitted"]["observations"] += 1
        elif any(row["id"] not in set(packet.get("required", [packet["task"]["target"]]))
                 for row in packet["artifacts"]):
            required = set(packet.get("required", [packet["task"]["target"]]))
            index = max(i for i, row in enumerate(packet["artifacts"]) if row["id"] not in required)
            packet["artifacts"].pop(index)
            packet["omitted"] += 1
        elif prompt["instruments"]:
            prompt["instruments"].pop()
            prompt["omitted"]["instruments"] += 1
        elif observations and observations[0].get("status") != "deferred":
            observation = observations[0]
            ids = []
            for key in ("artifacts", "results", "sources"):
                ids.extend(row["id"] for row in observation.get(key, []) if "id" in row)
            for key in ("result_id", "query_id", "definition_id", "memo", "observation"):
                if observation.get(key):
                    ids.append(observation[key])
            if observation.get("passage"):
                ids.append(observation["passage"]["id"])
            observations[0] = {
                "action": observation["action"], "status": "deferred", "ids": ids[:30],
                "message": "Result exceeds the request budget. Read an id with a smaller length or search limit.",
            }
        else:
            raise ContextOverflow("request budget cannot fit required context, instructions and result handles")
