"""Compile a task-local research packet under a context bound.

Retrieval uses the scientific graph first and lexical search second.
"""
from __future__ import annotations

from collections import deque
import json
import re
from typing import Any

from .core import Artifact, Kernel, Ledger, Task

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


def _data(a: Artifact) -> dict[str, Any]:
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
        return {"text": _clip(d.get("text", ""), 2500)}
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
    raw = " ".join([task.verb, task.why, target.kind, json.dumps(target.data, ensure_ascii=False)])
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
    return max(1, len(json.dumps(obj, ensure_ascii=False, separators=(",", ":"))) // 4)


def compile_context(
    ledger: Ledger,
    kernel: Kernel,
    task: Task,
    *,
    max_chars: int | None = 12_000,
    max_tokens: int | None = None,
    radius: int = 3,
    source_before: str | None = None,
    lexical_results: int = 20,
) -> dict[str, Any]:
    """Compile a task-local scientific dependency slice under a hard context bound.

    Graph-local objects are mandatory/high priority. SQLite-backed ledgers additionally supply
    FTS hits. `max_tokens` is only a context-capacity approximation; it is never used to rank
    scientific work or value model effort.
    """
    if max_chars is None and max_tokens is None:
        raise ValueError("a context bound is required")
    if max_chars is not None and max_chars < 1000:
        raise ValueError("max_chars too small")
    if max_tokens is not None and max_tokens < 250:
        raise ValueError("max_tokens too small")

    target = ledger.get(task.target)
    distance = {task.target: 0}
    q = deque([task.target])
    while q:
        aid = q.popleft()
        if distance[aid] >= radius:
            continue
        for nid in _neighbors(ledger, aid):
            if _contains(ledger, nid) and nid not in distance:
                distance[nid] = distance[aid] + 1
                q.append(nid)

    lexical_ids: set[str] = set()
    query = _terms(task, target)
    if query and hasattr(ledger, "search"):
        try:
            lexical_ids = {a.id for a in ledger.search(query, limit=lexical_results)}
        except Exception:
            lexical_ids = set()

    ids = set(distance) | lexical_ids
    # Raw scientific history outranks compressed/model-authored memory.  This ordering is
    # intentionally replay-friendly: consequences, live/failed branches and realized decisions
    # remain visible before memos that summarize them.
    kind_bonus = {
        "evidence": 48, "empirical_summary": 40, "evaluation": 40, "measurement": 24,
        "claim": 34, "experiment": 34, "assumption": 34,
        "discrepancy": 34, "resolution": 38, "invariant": 34,
        "world": 30, "grammar": 26, "source": 24, "source_text": 25,
        "decision": 34, "decision_outcome": 44,
        "outcome": 24, "tool": 8, "bundle": 8, "candidate": 20,
        "memo": 16, "selection": 18, "instrument_result": 34, "instrument_call": 12,
        "instrument_definition": 32,
    }

    candidates = []
    for aid in ids:
        a = ledger.get(aid)
        if source_before and a.kind == "source":
            published = str(a.data.get("published", ""))[:10]
            if published and published > source_before[:10]:
                continue
        dist = distance.get(aid, radius + 1)
        score = 200 if aid == task.target else 100 - 17 * dist
        if aid in lexical_ids:
            score += 20
        score += kind_bonus.get(a.kind, 0)
        if authority(a, ledger, kernel) == "consequence":
            score += 22
        if a.kind == "outcome" and not a.data.get("closed", True):
            score += 18
        candidates.append((score, a.t, a))
    candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)

    packet = {
        "task": {"verb": task.verb, "lane": task.lane, "why": task.why, "target": task.target},
        "retrieval": {"graph_radius": radius, "lexical_query": query, "lexical_hits": len(lexical_ids)},
        "artifacts": [], "omitted": 0,
    }

    def fits(row: dict[str, Any]) -> bool:
        trial = dict(packet)
        trial["artifacts"] = packet["artifacts"] + [row]
        if max_chars is not None and len(json.dumps(trial, ensure_ascii=False, separators=(",", ":"))) > max_chars:
            return False
        if max_tokens is not None and _approx_tokens(trial) > max_tokens:
            return False
        return True

    for _, _, a in candidates:
        row = {
            "id": a.id, "kind": a.kind, "authority": authority(a, ledger, kernel), "by": a.by,
            "refs": {k: list(v) for k, v in a.refs.items()}, "data": _data(a),
        }
        if fits(row):
            packet["artifacts"].append(row)
        else:
            packet["omitted"] += 1

    encoded = json.dumps(packet, ensure_ascii=False, separators=(",", ":"))
    packet["used_chars"] = len(encoded)
    packet["approx_tokens"] = _approx_tokens(packet)
    packet["budget_chars"] = max_chars
    packet["budget_tokens"] = max_tokens
    return packet


def render_context(packet: dict[str, Any]) -> str:
    return json.dumps(packet, ensure_ascii=False, separators=(",", ":"))
