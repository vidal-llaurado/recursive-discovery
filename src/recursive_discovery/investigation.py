"""Mission scope, branch membership and causal attention decisions.

Scope views leave ledger records intact. Callers must serialize writes.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any, Iterable
import hashlib
import json

from .core import Artifact, Ledger


class Conflict(ValueError):
    """The caller's basis is no longer the complete set of current heads."""


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def _text(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")
    return value.strip()


def _ids(ledger: Ledger, values: Iterable[str]) -> list[str]:
    if isinstance(values, str):
        raise TypeError("expected artifact IDs, not a string")
    result = list(dict.fromkeys(values))
    for aid in result:
        if not isinstance(aid, str):
            raise TypeError("artifact IDs must be strings")
        ledger.get(aid)
    return result


def _put(ledger: Ledger, kind: str, data: dict, refs: dict, by: str) -> Artifact:
    # Detach mutable caller payloads; both JSONL and SQLite use identical strict JSON values.
    data = json.loads(canonical(data))
    refs = {k: _ids(ledger, v) for k, v in refs.items() if v}
    return ledger.put(kind, data, refs, by=by)


def _heads(records: Iterable[Artifact]) -> list[Artifact]:
    records = list(records)
    parents = {p for r in records for p in r.refs.get("previous", ())}
    return sorted((r for r in records if r.id not in parents), key=lambda r: r.id)


def _expect(heads: Iterable[Artifact], expected: Iterable[str]) -> None:
    actual = {h.id for h in heads}
    if actual != set(expected):
        raise Conflict(f"expected heads differ; current heads: {sorted(actual)}")


def _policy(policy: dict | None) -> dict:
    p = json.loads(canonical(policy or {}))
    unknown = set(p) - {"source_before", "deny_ids", "tool_ids"}
    if unknown:
        raise ValueError(f"unknown enforced policy keys: {sorted(unknown)}; use guidance for prose")
    if p.get("source_before") is not None:
        date.fromisoformat(p["source_before"])
        if len(p["source_before"]) != 10:
            raise ValueError("source_before must be YYYY-MM-DD")
    for key in ("deny_ids", "tool_ids"):
        if key in p and (not isinstance(p[key], list) or any(not isinstance(x, str) for x in p[key])):
            raise TypeError(f"{key} must be an explicit list of artifact IDs")
    return p


def create_program(ledger: Ledger, objective: str, *, policy: dict | None = None,
                   guidance: str = "", by: str = "human") -> Artifact:
    return _put(ledger, "program", {"objective": _text(objective, "objective"),
                "guidance": guidance, "policy": _policy(policy)}, {}, by)


def create_mission(ledger: Ledger, objective: str, *, targets: Iterable[str] = (),
                   required: Iterable[str] = (), working: Iterable[str] = (),
                   program: str | None = None, guidance: str = "", deliverables: str = "",
                   policy: dict | None = None, by: str = "human") -> Artifact:
    if program and ledger.get(program).kind != "program":
        raise ValueError("program artifact required")
    mission = _put(ledger, "mission", {"objective": _text(objective, "objective"),
                "guidance": guidance, "deliverables": deliverables, "policy": _policy(policy)},
                {"targets": targets, "required": required, "working": working,
                 "program": [program] if program else []}, by)
    request(ledger, mission.id, objective, targets=mission.refs.get("targets", ()), by=by)
    return mission


def mission_root(ledger: Ledger, mission: str) -> Artifact:
    a = ledger.get(mission)
    if a.kind == "mission_revision":
        a = ledger.get(a.refs["mission"][0])
    if a.kind != "mission":
        raise ValueError("mission artifact required")
    return a


def mission_heads(ledger: Ledger, mission: str) -> list[Artifact]:
    root = mission_root(ledger, mission)
    return _heads([root, *ledger.children(root.id, "mission_revision", "mission")])


def mission_revision(ledger: Ledger, mission: str) -> Artifact:
    hs = mission_heads(ledger, mission)
    if len(hs) != 1:
        raise Conflict(f"mission has conflicting revisions: {[h.id for h in hs]}")
    return hs[0]


def revise_mission(ledger: Ledger, mission: str, *, expected: Iterable[str],
                   objective: str, targets: Iterable[str] = (), required: Iterable[str] = (),
                   working: Iterable[str] = (), guidance: str = "", deliverables: str = "",
                   policy: dict | None = None, by: str = "human") -> Artifact:
    """Write a complete revision; naming all heads explicitly also reconciles a fork."""
    root = mission_root(ledger, mission)
    hs = mission_heads(ledger, root.id)
    _expect(hs, expected)
    return _put(ledger, "mission_revision", {"objective": _text(objective, "objective"),
                "guidance": guidance, "deliverables": deliverables, "policy": _policy(policy)},
                {"mission": [root.id], "previous": [h.id for h in hs],
                 "targets": targets, "required": required, "working": working}, by)


def open_branch(ledger: Ledger, mission: str, question: str, *, targets: Iterable[str] = (),
                by: str = "research-model") -> Artifact:
    root = mission_root(ledger, mission)
    return _put(ledger, "branch", {"question": _text(question, "question")},
                {"mission": [root.id], "targets": targets}, by)


def include(ledger: Ledger, mission: str, artifacts: Iterable[str], *, branch: str | None = None,
            by: str = "human") -> Artifact:
    """Membership is explicit and may overlap. References to dependencies do not imply ownership."""
    root = mission_root(ledger, mission)
    if branch and ledger.get(branch).kind != "branch":
        raise ValueError("branch artifact required")
    return _put(ledger, "membership", {}, {"mission": [root.id], "members": artifacts,
                "branch": [branch] if branch else []}, by)


def attention(ledger: Ledger, subject: str, scope: str) -> dict[str, Any]:
    art = ledger.get(subject)
    default = "active" if art.kind == "branch" else "open"
    records = [a for a in ledger.children(subject, "attention", "subject")
               if scope in a.refs.get("scope", ())]
    hs = _heads(records)
    return {"state": hs[0].data["state"] if len(hs) == 1 else "conflict" if hs else default,
            "heads": [h.id for h in hs],
            "reasons": [h.data["reason"] for h in hs],
            "revisit": [h.data.get("revisit", "") for h in hs]}


def set_attention(ledger: Ledger, subject: str, scope: str, state: str, *,
                  expected: Iterable[str], reason: str, support: Iterable[str] = (),
                  revisit: str = "", by: str = "human") -> Artifact:
    a, s = ledger.get(subject), ledger.get(scope)
    states = {"active", "dormant", "closed"} if a.kind == "branch" else {"open", "deferred", "done"}
    if a.kind not in {"branch", "request"} or s.kind not in {"mission", "program"} or state not in states:
        raise ValueError("invalid attention subject, scope, or state")
    old = attention(ledger, subject, scope)
    if set(expected) != set(old["heads"]):
        raise Conflict(f"attention changed; current heads: {old['heads']}")
    return _put(ledger, "attention", {"state": state, "reason": _text(reason, "reason"),
                "revisit": revisit}, {"subject": [subject], "scope": [scope],
                "previous": old["heads"], "support": support}, by)


def supersede(ledger: Ledger, old: str, new: str, scope: str, *, purpose: str,
              reason: str, support: Iterable[str] = (), by: str = "human") -> Artifact:
    if old == new or ledger.get(scope).kind not in {"mission", "program"}:
        raise ValueError("supersession needs distinct artifacts and a mission/program scope")
    return _put(ledger, "supersession", {"purpose": _text(purpose, "purpose"),
                "reason": _text(reason, "reason")}, {"old": [old], "new": [new],
                "scope": [scope], "support": support}, by)


def request(ledger: Ledger, mission: str, question: str, *, targets: Iterable[str] = (),
            branch: str | None = None, origin: str = "directed", after: Iterable[str] = (),
            tools: Iterable[str] = (), guidance: str = "", by: str = "human") -> Artifact:
    root = mission_root(ledger, mission)
    if branch and ledger.get(branch).kind != "branch":
        raise ValueError("branch artifact required")
    after = _ids(ledger, after)
    for aid in after:
        if ledger.get(aid).kind != "request":
            raise ValueError("request prerequisites must be request artifacts")
    return _put(ledger, "request", {"research_request": 1, "question": _text(question, "question"),
                "origin": _text(origin, "origin"), "guidance": guidance},
                {"mission": [root.id], "targets": targets, "after": after, "tools": tools,
                 "branch": [branch] if branch else []}, by)


def challenge(ledger: Ledger, mission: str, target: str, obstruction: str, *,
              attempts: Iterable[str] = (), methods: str = "", branch: str | None = None,
              by: str = "human") -> Artifact:
    """Create a research request for a specific challenge."""
    return request(ledger, mission, _text(obstruction, "obstruction"),
                   targets=[target, *_ids(ledger, attempts)], branch=branch,
                   origin="challenge", guidance=methods, by=by)




class Scope:
    """A snapshot of mission membership, attention and access restrictions.

    Rebuild after writes. Access checks follow metadata and declared dependencies;
    filesystem and network isolation belong to the worker.
    """

    def __init__(self, ledger: Ledger, mission: str, *, source_before: str | None = None):
        self.ledger = ledger
        self.root = mission_root(ledger, mission)
        self.revision = mission_revision(ledger, self.root.id)
        self.program = self.root.refs.get("program", (None,))[0]
        self.scopes = [self.root.id] + ([self.program] if self.program else [])
        policies = [self.revision.data.get("policy", {})]
        if self.program:
            policies.append(ledger.get(self.program).data.get("policy", {}))
        if source_before is not None:
            _policy({"source_before": source_before})
        dates = [p["source_before"] for p in policies if p.get("source_before")]
        if source_before:
            dates.append(source_before)
        self.source_before = min(dates) if dates else None
        self.denied = {x for p in policies for x in p.get("deny_ids", ())}
        sets = [set(p["tool_ids"]) for p in policies if "tool_ids" in p]
        self.tool_ids = set.intersection(*sets) if sets else None
        self.roots = set(self.revision.refs.get("targets", ()))
        self.required = set(self.revision.refs.get("required", ()))
        self.working = set(self.revision.refs.get("working", ()))
        self.members = self.roots | self.required | self.working | {self.root.id, self.revision.id}
        self.branches: set[str] = set()
        self.owners: dict[str, set[str]] = defaultdict(set)
        self.events: dict[tuple[str, str], list[Artifact]] = defaultdict(list)
        self.supersessions: list[Artifact] = []
        for a in ledger.all():
            if a.kind == "supersession" and set(a.refs.get("scope", ())) & set(self.scopes):
                self.supersessions.append(a)
            if a.kind == "attention":
                for s in a.refs.get("scope", ()):
                    for subject in a.refs.get("subject", ()):
                        self.events[(subject, s)].append(a)
            # Existing RD study ownership is explicit; arbitrary graph reachability is not.
            if self.roots.intersection(a.refs.get("study", ())):
                self.members.add(a.id)
            if self.root.id in a.refs.get("mission", ()):
                self.members.add(a.id)
                self.members.update(a.refs.get("members", ()))
                self.branches.update(a.refs.get("branch", ()))
                if a.kind == "branch":
                    self.branches.add(a.id)
            for b in a.refs.get("branch", ()):
                if a.kind == "membership":
                    for member in a.refs.get("members", ()):
                        self.owners[member].add(b)
                else:
                    self.owners[a.id].add(b)
        for b in self.branches:
            self.members.add(b)
        for aid, owners in self.owners.items():
            if owners & self.branches:
                self.members.add(aid)
        self._allowed: dict[str, bool] = {}

    def state(self, subject: str, *, scope: str | None = None) -> dict:
        a = self.ledger.get(subject)
        default = "active" if a.kind == "branch" else "open"
        hs = _heads(self.events.get((subject, scope or self.root.id), []))
        return {"state": hs[0].data["state"] if len(hs) == 1 else "conflict" if hs else default,
                "heads": [h.id for h in hs], "reasons": [h.data["reason"] if self.allowed(h.id)
                    else "reason outside information boundary" for h in hs],
                "revisit": [h.data.get("revisit", "") if self.allowed(h.id) else "" for h in hs],
                "support": sorted({eid for h in hs if self.allowed(h.id) for eid in h.refs.get("support", ())})}

    def branch_active(self, branch: str) -> bool:
        return all(self.state(branch, scope=s)["state"] == "active" for s in self.scopes)

    def attending(self, aid: str) -> bool:
        if self.ledger.get(aid).kind == "branch":
            return self.branch_active(aid)
        owners = self.owners.get(aid, set()) & self.branches
        return not owners or any(self.branch_active(b) for b in owners)

    def allowed(self, aid: str) -> bool:
        if aid in self._allowed:
            return self._allowed[aid]
        from .reading import available
        ok = available(self.ledger, self.ledger.get(aid), self.source_before, denied=self.denied)
        self._allowed[aid] = ok
        return ok

    def tool_allowed(self, aid: str) -> bool:
        return self.allowed(aid) and (self.tool_ids is None or aid in self.tool_ids)

    def replacement_notes(self, aid: str) -> list[dict]:
        return [{"relation": x.id, "new": list(x.refs.get("new", ())),
                 "purpose": x.data["purpose"], "reason": x.data["reason"]}
                for x in self.supersessions if aid in x.refs.get("old", ()) and self.allowed(x.id)]

    def branch_page(self, *, offset: int = 0, limit: int = 12) -> dict:
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 30:
            raise ValueError("invalid branch page")
        ids = [b for b in sorted(self.branches) if self.allowed(b)]
        return {"total": len(ids), "offset": offset, "next": offset + limit if offset + limit < len(ids) else None,
                "items": [{"id": b, "question": self.ledger.get(b).data.get("question"),
                           "mission_state": self.state(b), "active": self.branch_active(b)}
                          for b in ids[offset:offset+limit]]}

    def context_note(self) -> dict:
        dormant = []
        for b in sorted(self.branches):
            if not self.branch_active(b) and self.allowed(b):
                dormant.append({"branch": b, "question": self.ledger.get(b).data.get("question"),
                                "decisions": [self.state(b, scope=s) for s in self.scopes]})
        return {"mission": self.root.id, "revision": self.revision.id,
                "objective": self.revision.data["objective"],
                "guidance": self.revision.data.get("guidance", ""),
                "dormant_branches": dormant[:8], "dormant_omitted": max(0, len(dormant) - 8),
                "source_before": self.source_before}


# Compatibility aliases for callers using the original import paths.
from .protocols import declare_check, interpret_checks
from .replay import attempt_signature
