"""Build and page eligible research work.

Frontier obligations, directed requests, challenges and opportunity producers share
one agenda. The model selects the next task.
"""
from __future__ import annotations
from .telemetry import emit, traced, call_model

from dataclasses import dataclass
from typing import Callable, Iterable
import json

from .core import Ledger, Kernel, Task, frontier
from .investigation import Scope, digest
from .replay import record_decision, record_outcome


@dataclass(frozen=True)
class Work:
    task: Task
    origin: str
    request: str | None = None
    branches: tuple[str, ...] = ()

    @property
    def key(self) -> str:
        return digest([self.task.key, self.origin, self.request, self.branches])[:20]

    def row(self) -> dict:
        return {"key": self.key, "origin": self.origin, "verb": self.task.verb,
                "target": self.task.target, "lane": self.task.lane,
                "why": self.task.why[:1500], "request": self.request,
                "branches": list(self.branches)}


@traced("agenda")
def available_work(ledger: Ledger, kernel: Kernel, scope: Scope, *,
                   scanners: Iterable[Callable[[Ledger, Scope], Iterable[Work]]] = ()) -> dict:
    proposed, suppressed = [], []
    for r in ledger.all("request"):
        if r.data.get("research_request") != 1 or r.id not in scope.members:
            continue
        proposed.append(Work(Task("investigate", r.id, r.data["question"]),
                             r.data["origin"], r.id, tuple(r.refs.get("branch", ()))))
    for t in frontier(ledger, kernel):
        parents = []
        if t.target not in scope.members:
            # Apply the originating claim's attention state to its shared assumption.
            if t.verb == "stress":
                parents = [a.id for a in ledger.children(t.target, "claim", "assumptions")
                           if a.id in scope.members and scope.allowed(a.id)]
            if not parents:
                continue
        origins = parents or [t.target]
        branches = set().union(*(scope.owners.get(aid, set()) & scope.branches for aid in origins))
        # A branchless origin keeps the obligation eligible even if another origin is paused.
        if any(not (scope.owners.get(aid, set()) & scope.branches) for aid in origins):
            branches = set()
        proposed.append(Work(t, "frontier", branches=tuple(sorted(branches))))
    for scan in scanners:
        proposed.extend(scan(ledger, scope))
    result, seen = [], set()
    for work in proposed:
        if not isinstance(work, Work):
            raise TypeError("opportunity producer must yield Work")
        if work.key in seen:
            continue
        seen.add(work.key)
        reason = None
        target = ledger.get(work.task.target)
        if not scope.allowed(target.id):
            reason = "information_boundary"
        elif work.branches and not any(scope.branch_active(b) for b in work.branches):
            reason = "branch_not_active"
        elif not scope.attending(target.id):
            reason = "branch_not_active"
        if work.request and not reason:
            if any(scope.state(work.request, scope=s)["state"] != "open" for s in scope.scopes):
                reason = "request_not_open"
            elif any(scope.state(r)["state"] != "done" for r in target.refs.get("after", ())):
                reason = "prerequisite_not_done"
            elif any(not scope.tool_allowed(t) for t in target.refs.get("tools", ())):
                reason = "required_tool_not_permitted"
        if reason:
            # A listing is also an information path: redact the question of denied work.
            suppressed.append({"key": work.key, "reason": reason,
                               **({} if reason == "information_boundary" else {"target": target.id})})
        else:
            result.append(work)
    result.sort(key=lambda w: (w.origin, w.key))
    emit("agenda.candidates", {"mission_id": scope.root.id, "revision_id": scope.revision.id,
        "generated_count": len(proposed), "eligible_count": len(result), "suppressed_count": len(suppressed),
        "eligible": [{k: v for k, v in w.row().items() if k != "why"} for w in result[:1500]],
        "suppressed": suppressed[:1500], "truncated": len(result) > 1500 or len(suppressed) > 1500},
        payload=lambda: {"eligible": [w.row() for w in result], "suppressed": suppressed})
    return {"eligible": result, "suppressed": suppressed}


def contradiction_opportunities(ledger: Ledger, scope: Scope) -> Iterable[Work]:
    """Propose work for recorded contradiction relationships."""
    for a in ledger.all():
        for role in ("contradicts", "falsifies"):
            for other in a.refs.get(role, ()):
                if a.id in scope.members or other in scope.members:
                    if scope.allowed(other):
                        yield Work(Task("investigate", a.id,
                            f"Examine the reported {role} link to {other}; the relation is a proposal, not an adjudication."),
                            "explore")


@traced("routing")
def choose_work(ledger: Ledger, kernel: Kernel, scope: Scope, model, *,
                work: list[Work] | None = None, max_chars: int = 14_000,
                max_pages: int = 12) -> Work | None:
    """Ask the research model to select work from paginated results."""
    options = available_work(ledger, kernel, scope)["eligible"] if work is None else work
    if not options:
        return None
    if max_chars < 2500 or max_pages < 1:
        raise ValueError("agenda bounds are too small")
    offset = 0
    for _ in range(max_pages):
        page = {"mode": "choose_research_work", "mission": scope.root.id,
                "revision": scope.revision.id, "objective": scope.revision.data["objective"],
                "total": len(options), "offset": offset, "items": [],
                "instruction": "Choose {key:exact key, reason:...}; request {offset:integer} to inspect another page; or {stop:true, reason:...}. Origin is not priority. Only displayed keys may be selected."}
        for w in options[offset:]:
            page["items"].append(w.row())
            if len(json.dumps(page, ensure_ascii=False, separators=(",", ":"))) > max_chars:
                page["items"].pop(); break
        if not page["items"]:
            raise ValueError("mission and first work item exceed agenda budget")
        encoded = json.dumps(page, ensure_ascii=False, separators=(",", ":"))
        emit("agenda.page", {"offset": offset, "total": len(options), "shown_keys": [x["key"] for x in page["items"]],
                             "request_chars": len(encoded), "budget_chars": max_chars}, payload=lambda: page)
        answer = json.loads(call_model(model, encoded)) if model is not None else {"key": page["items"][0]["key"], "reason": "first eligible work without a model"}
        if not isinstance(answer, dict):
            raise ValueError("agenda response must be an object")
        decision = record_decision(ledger, Task("route", scope.revision.id, "select research work"),
            action="route", model=model, state=page, payload=answer,
            visible_artifacts=[x["target"] for x in page["items"]],
            available_actions=["select", "page", "stop"])
        emit("agenda.choice", {"decision_id": decision.id,
             "key": answer.get("key") if answer.get("key") in {x["key"] for x in page["items"]} else None,
             "stop": answer.get("stop") is True, "offset": answer.get("offset") if type(answer.get("offset")) is int else None},
             payload=lambda: answer)
        if answer.get("stop") is True:
            record_outcome(ledger, decision, status="deferred", summary=answer.get("reason"))
            return None
        if "offset" in answer:
            next_offset = answer["offset"]
            if type(next_offset) is not int or not 0 <= next_offset < len(options):
                raise ValueError("invalid agenda page offset")
            offset = next_offset
            record_outcome(ledger, decision, status="page", summary={"offset": offset})
            continue
        shown = {x["key"] for x in page["items"]}
        for w in options:
            if w.key == answer.get("key") and w.key in shown:
                record_outcome(ledger, decision, status="selected", observed=[w.task.target], summary=w.row())
                return w
        raise ValueError("selected work was not on the displayed page")
    return None
