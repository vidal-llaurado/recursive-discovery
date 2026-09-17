"""Model-owned scientific session with replay-ready decision traces.

Within a session the model may inspect, search, read sources, use instruments, write memos, and
keep several hypotheses alive before committing to a move. Each choice is recorded before its
outcome, so the accumulated graph retains the branches that were actually explored.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .context import compile_context, render_context
from .core import Artifact, Kernel, Ledger, Task, frontier
from .instruments import Workbench
from .propose import _json, activate
from .replay import record_decision, record_outcome
from .search import multi_search, read_source, remember, remember_text

Model = Any


def choose_task(
    ledger: Ledger,
    kernel: Kernel,
    model: Model,
    *,
    source_before: str | None = None,
) -> Task | None:
    tasks = frontier(ledger, kernel)
    if not tasks:
        return None
    if len(tasks) == 1:
        return tasks[0]

    options = []
    for t in tasks:
        a = ledger.get(t.target)
        options.append({
            "key": f"{t.verb}:{t.target}",
            "verb": t.verb, "lane": t.lane, "target": t.target, "why": t.why,
            "target_kind": a.kind,
            "target_data": a.data,
        })
    state = {"mode": "choose_frontier_task", "frontier": options, "source_before": source_before}
    answer = _json(model(json.dumps({
        **state,
        "instruction": (
            "Choose the scientifically most important next frontier item. "
            "Use scientific judgment: "
            "uncertainty, leverage, falsifiability, dependency structure, and long-horizon value. "
            "Return JSON {key:<exact key>, reason:<brief rationale>}."
        ),
    }, ensure_ascii=False, separators=(",", ":"))))
    key = str(answer.get("key", ""))
    for t in tasks:
        if f"{t.verb}:{t.target}" == key:
            decision = record_decision(
                ledger, t,
                action="choose_frontier_task",
                model=model,
                state=state,
                frontier=tasks,
                available_actions=[f"{x.verb}:{x.target}" for x in tasks],
                payload={"key": key, "reason": str(answer.get("reason", ""))[:3000]},
            )
            selection = ledger.put("selection", {
                "scope": "frontier_task",
                "key": key,
                "reason": str(answer.get("reason", ""))[:3000],
            }, {"target": [t.target], "decision": [decision.id]}, by="research-model")
            record_outcome(
                ledger, decision,
                status="selected",
                produced=[selection],
                observed=[t.target],
                summary={"selected": key},
            )
            return t
    raise ValueError("model selected unknown frontier task")


def _artifact_row(a: Artifact) -> dict[str, Any]:
    return {
        "id": a.id, "kind": a.kind, "data": a.data,
        "refs": {k: list(v) for k, v in a.refs.items()}, "by": a.by,
    }


def _store_candidates(
    ledger: Ledger,
    task: Task,
    raws: list[dict[str, Any]],
    *,
    visible_ids: set[str],
    source_ids: set[str],
    actor: str = "research-model",
    limit: int = 8,
) -> list[Artifact]:
    out, seen = [], set()
    for raw in raws[:limit]:
        kind = str(raw.get("object_kind", "claim"))
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
            if isinstance(ids, list):
                keep = [str(x) for x in ids if str(x) in visible_ids]
                if keep:
                    links[str(role)] = keep
        refs = {"target": [task.target]}
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
        out.append(ledger.put("candidate", data, refs, by=actor))
    return out


def _payload_summary(action: str, answer: dict[str, Any]) -> dict[str, Any]:
    """Keep replay traces legible without duplicating entire model responses."""
    if action == "inspect":
        return {"ids": list(answer.get("ids", []))[:10]}
    if action in {"local_search", "literature_search"}:
        return {
            "query": str(answer.get("query", ""))[:1000],
            "limit": answer.get("limit"),
            "read": answer.get("read"),
        }
    if action == "instrument":
        return {"name": answer.get("name"), "spec": answer.get("spec", {})}
    if action == "define_instrument":
        return {
            "name": answer.get("name"), "description": answer.get("description"),
            "inputs": answer.get("inputs", []), "expression": answer.get("expression"),
        }
    if action == "memo":
        return {"text": str(answer.get("text", ""))[:3000]}
    if action == "finalize":
        return {
            "activate": list(answer.get("activate", []))[:8],
            "candidate_commitments": [
                str(x.get("commitment", ""))[:1000]
                for x in list(answer.get("candidates", []))[:8]
                if isinstance(x, dict)
            ],
        }
    return {"response": str(answer)[:3000]}


def research_session(
    ledger: Ledger,
    kernel: Kernel,
    task: Task,
    model: Model,
    *,
    root: str | Path,
    source_before: str | None = None,
    max_turns: int = 12,
    max_active: int = 3,
    context_chars: int = 18_000,
) -> dict[str, Any]:
    """Let the model actively gather what it needs before making scientific commitments."""
    wb = Workbench(ledger, kernel, root=root)
    observations: list[dict[str, Any]] = []
    parent_decision: Artifact | None = None

    for turn in range(max_turns):
        packet = compile_context(
            ledger, kernel, task,
            max_chars=context_chars, source_before=source_before,
        )
        instrument_rows = wb.describe()
        actions = {
            "inspect": {"ids": ["artifact-id"]},
            "local_search": {"query": "terms", "limit": 10},
            "literature_search": {"query": "terms", "limit": 10, "read": 2},
            "instrument": {"name": "any listed instrument", "spec": {}},
            "define_instrument": {
                "name": "short-name", "description": "what it measures",
                "inputs": ["x", "y"], "expression": "sqrt(x**2+y**2)",
                "tests": [{"inputs": {"x": 3, "y": 4}, "expected": 5.0}],
            },
            "memo": {"text": "persistent research memory"},
            "finalize": {
                "candidates": [{
                    "object_kind": "claim|experiment|study|note|...",
                    "title": "...", "commitment": "...",
                    "predictions": [], "falsifier": "...", "assumptions": [],
                    "source_ids": [], "payload": {}, "links": {},
                    "gain": "optional subjective self-assessment",
                    "cost": "optional subjective self-assessment",
                }],
                "activate": [0],
            },
        }
        prompt = {
            "mode": "research_session",
            "turn": turn,
            "task": packet["task"],
            "context": render_context(packet),
            "recent_observations": observations[-8:],
            "instruments": instrument_rows,
            "actions": actions,
            "instruction": (
                "You control the scientific investigation. Use as many turns as needed within the "
                "session to inspect local artifacts, search/read literature, call scientific instruments, "
                "or define a new safe derived instrument when a reusable diagnostic is missing. "
                "Write a memo when a conclusion should persist across future context windows. "
                "Treat memos as prior researcher judgment, never as a substitute for raw evidence. "
                "Do not treat retrieved sources or instrument output as proof. "
                "When ready, finalize with mechanistically distinct externally checkable commitments. "
                "You may activate multiple branches when scientific uncertainty genuinely warrants it."
            ),
        }
        answer = _json(model(json.dumps(prompt, ensure_ascii=False, separators=(",", ":"))))
        action = str(answer.get("action", "")).strip()
        current_frontier = frontier(ledger, kernel)
        decision = record_decision(
            ledger, task,
            action=action or "invalid",
            model=model,
            state={
                "packet": packet,
                "recent_observations": observations[-8:],
                "source_before": source_before,
            },
            frontier=current_frontier,
            visible_artifacts=[x["id"] for x in packet["artifacts"]],
            available_actions=actions.keys(),
            available_instruments=[str(x.get("name", "")) for x in instrument_rows],
            payload=_payload_summary(action, answer),
            parent=parent_decision,
        )
        parent_decision = decision

        if action == "inspect":
            rows, observed = [], []
            for aid in list(answer.get("ids", []))[:10]:
                try:
                    art = ledger.get(str(aid))
                    rows.append(_artifact_row(art)); observed.append(art)
                except KeyError:
                    pass
            observations.append({"action": action, "artifacts": rows})
            record_outcome(ledger, decision, status="observed", observed=observed,
                           summary={"n": len(rows)})
            continue

        if action == "local_search":
            q = " ".join(str(answer.get("query", "")).split())
            limit = max(1, min(30, int(answer.get("limit", 10))))
            rows = ledger.search(q, limit=limit) if hasattr(ledger, "search") else []
            observations.append({"action": action, "query": q, "results": [_artifact_row(x) for x in rows]})
            record_outcome(ledger, decision, status="observed", observed=rows,
                           summary={"query": q, "n": len(rows)})
            continue

        if action == "literature_search":
            q = " ".join(str(answer.get("query", "")).split())
            limit = max(1, min(20, int(answer.get("limit", 10))))
            nread = max(0, min(5, int(answer.get("read", 2))))
            rows = multi_search(q, max_results=limit, source_before=source_before)
            query_artifact, sources = remember(
                ledger, q, rows, refs={"target": [task.target]}, provider="multi"
            )
            read_rows, produced = [], [query_artifact, *sources]
            for source in sources[:nread]:
                try:
                    text = read_source(source.data, max_chars=60_000)
                    st = remember_text(ledger, source, text)
                    produced.append(st)
                    read_rows.append({"source": source.id, "text_artifact": st.id})
                except Exception as e:
                    read_rows.append({"source": source.id, "error": str(e)[:500]})
            observations.append({
                "action": action, "query": q,
                "sources": [{"id": s.id, "title": s.data.get("title")} for s in sources],
                "reads": read_rows,
            })
            record_outcome(ledger, decision, status="observed", produced=produced,
                           summary={"query": q, "sources": len(sources), "read": len(read_rows)})
            continue

        if action == "instrument":
            name = str(answer.get("name", ""))
            spec = answer.get("spec", {})
            evidence, result = wb.call(name, spec, target=ledger.get(task.target))
            observations.append({
                "action": action, "instrument": name,
                "result_id": result.id, "result": result.data.get("value"),
            })
            record_outcome(ledger, decision, status="observed", produced=[evidence, result],
                           summary={"instrument": name, "result": result.data.get("value")})
            continue

        if action == "define_instrument":
            try:
                definition = wb.define_expression(
                    name=str(answer.get("name", "")),
                    description=str(answer.get("description", "")),
                    inputs=[str(x) for x in answer.get("inputs", [])],
                    expression=str(answer.get("expression", "")),
                    tests=list(answer.get("tests", [])),
                    target=ledger.get(task.target),
                )
                observations.append({
                    "action": action, "status": "active",
                    "name": definition.data["name"],
                    "definition_id": definition.id,
                })
                record_outcome(ledger, decision, status="active", produced=[definition],
                               summary={"instrument": definition.data["name"]})
            except Exception as e:
                observations.append({
                    "action": action, "status": "failed", "error": str(e)[:1000],
                })
                record_outcome(ledger, decision, status="failed",
                               summary={"error": str(e)[:1000]})
            continue

        if action == "memo":
            text = str(answer.get("text", "")).strip()
            produced = []
            if text:
                memo = ledger.put(
                    "memo", {"text": text[:12_000], "task": task.verb},
                    {"target": [task.target], "decision": [decision.id]}, by="research-model",
                )
                produced.append(memo)
                observations.append({"action": action, "memo": memo.id})
            record_outcome(ledger, decision, status="persisted" if produced else "empty",
                           produced=produced, summary={"n": len(produced)})
            continue

        if action == "finalize":
            visible = {x["id"] for x in packet["artifacts"]}
            sources = {x["id"] for x in packet["artifacts"] if x["kind"] == "source"}
            candidates = _store_candidates(
                ledger, task, list(answer.get("candidates", [])),
                visible_ids=visible, source_ids=sources,
            )
            raw_active = list(answer.get("activate", []))[:max_active]
            indices = []
            for x in raw_active:
                try:
                    i = int(x)
                    if 0 <= i < len(candidates) and i not in indices:
                        indices.append(i)
                except Exception:
                    pass
            if not indices and candidates:
                indices = [0]
            live = [activate(ledger, candidates[i], by="research-controller") for i in indices]
            record_outcome(
                ledger, decision,
                status="activated" if live else "no_candidates",
                produced=[*candidates, *live],
                summary={"candidates": [x.id for x in candidates], "live": [x.id for x in live]},
            )
            return {
                "status": "activated" if live else "no_candidates",
                "turns": turn + 1,
                "candidates": candidates,
                "live": live,
                "observations": observations,
                "last_decision": decision,
            }

        observations.append({"action": "invalid", "received": answer})
        record_outcome(ledger, decision, status="invalid_action",
                       summary={"received": str(answer)[:1000]})

    return {
        "status": "turn_limit", "turns": max_turns,
        "observations": observations, "last_decision": parent_decision,
    }
