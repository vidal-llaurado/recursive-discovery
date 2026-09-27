"""Session action descriptions and handlers.

Handlers return effects; the session records observations, membership and outcomes.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .core import Artifact, Kernel, Ledger, Task
from .context import artifact_view, authority
from .instruments import Workbench
from .investigation import Scope, request, challenge, open_branch, set_attention
from .protocols import declare_check, interpret_checks
from .propose import activate, store_candidates
from .reading import available, excerpt, outline, passage, search_local
from .replay import attempt_signature, attempt_history
from .schema import define_schema, require_inputs
from .search import multi_search, acquire_source, remember


@dataclass
class ActionContext:
    ledger: Ledger
    kernel: Kernel
    task: Task
    root: Path
    workbench: Workbench
    source_before: str | None
    scope: Scope | None
    branch: str | None
    context_chars: int
    max_active: int
    seen_ids: set[str] = field(default_factory=set)
    seen_sources: set[str] = field(default_factory=set)
    seen_spans: dict[tuple[str, str], list[tuple[int, int]]] = field(default_factory=dict)
    decision: Artifact | None = None

    def permitted(self, artifact: Artifact) -> bool:
        return (self.scope.allowed(artifact.id) if self.scope else
                available(self.ledger, artifact, self.source_before))

    def get(self, aid: str) -> Artifact:
        artifact = self.ledger.get(aid)
        if not self.permitted(artifact):
            raise PermissionError("artifact outside information boundary")
        return artifact

    def references(self, ids: list[str]) -> list[str]:
        if not isinstance(ids, list) or any(not isinstance(x, str) or x not in self.seen_ids for x in ids):
            raise ValueError("references must identify material exposed in this session")
        for aid in ids:
            self.get(aid)
        return ids

    def preview(self, artifact: Artifact, query: str = "") -> dict:
        return {"id": artifact.id, "kind": artifact.kind,
                "authority": authority(artifact, self.ledger, self.kernel),
                "title": str(artifact.data.get("title", ""))[:160],
                "preview": excerpt(artifact, query, length=500)}


@dataclass
class Result:
    status: str = "observed"
    observation: dict | None = None
    produced: tuple[Artifact, ...] = ()
    observed: tuple[Artifact | str, ...] = ()
    summary: Any = None
    terminal: dict | None = None


def artifacts(ctx: ActionContext, *items: Artifact, status: str = "proposed") -> Result:
    return Result(status, {"artifacts": [artifact_view(ctx.ledger, ctx.kernel, a) for a in items]}, items)


def inspect(ctx: ActionContext, args: dict[str, Any]) -> Result:
    rows, observed, errors = [], [], []
    for aid in list(args.get("ids", []))[:10]:
        try:
            art = ctx.get(str(aid))
            rows.append(artifact_view(ctx.ledger, ctx.kernel, art, full=True, span=args.get("span")))
            observed.append(art)
        except (KeyError, ValueError, PermissionError) as error:
            errors.append({"id": str(aid), "error": str(error)[:500]})
    return Result(observation={"artifacts": rows, "errors": errors}, observed=tuple(observed),
                  summary={"n": len(rows), "errors": errors})


def read(ctx: ActionContext, args: dict[str, Any]) -> Result:
    art = ctx.get(str(args.get("id", "")))
    if art.kind == "source":
        art = acquire_source(ctx.ledger, art, root=ctx.root, source_before=ctx.source_before,
                             refresh=bool(args.get("refresh", False)))
        ctx.get(art.id)
    mode = args.get("view", "passage")
    if mode == "outline":
        value = outline(art, start=args.get("start", 0))
    elif mode == "passage":
        value = passage(art, start=args.get("start", 0),
                        length=args.get("length", min(4000, ctx.context_chars // 3)), query=args.get("query"))
    else:
        raise ValueError("read view must be outline or passage")
    return Result(value.get("status", "observed"), {"passage": value}, observed=(art,),
                  summary={k: v for k, v in value.items() if k != "text"})


def local_search(ctx: ActionContext, args: dict[str, Any]) -> Result:
    query = " ".join(str(args.get("query", "")).split())
    limit = max(1, min(30, int(args.get("limit", 10))))
    offset = int(args.get("offset", 0))
    if not 0 <= offset <= 10_000:
        raise ValueError("local search offset must be in [0, 10000]")
    found = search_local(ctx.ledger, query, limit=offset + limit + 1, cutoff=ctx.source_before,
                         accept=ctx.scope.allowed if ctx.scope else None)
    rows = found[offset:offset + limit]
    return Result(observation={"query": query, "offset": offset,
                  "next": offset + limit if len(found) > offset + limit else None,
                  "results": [ctx.preview(a, query) for a in rows]}, observed=tuple(rows),
                  summary={"query": query, "n": len(rows), "offset": offset})


def literature_search(ctx: ActionContext, args: dict[str, Any]) -> Result:
    query = " ".join(str(args.get("query", "")).split())
    limit = max(1, min(20, int(args.get("limit", 10))))
    nread = max(0, min(5, int(args.get("read", 0))))
    diagnostics = []
    rows = multi_search(query, max_results=limit, source_before=ctx.source_before, diagnostics=diagnostics)
    event, sources = remember(ctx.ledger, query, rows, refs={"target": [ctx.task.target]},
                              provider="multi", diagnostics=diagnostics)
    reads, produced = [], [event, *sources]
    sources = [a for a in sources if ctx.permitted(a)]
    for source in sources[:nread]:
        try:
            text = acquire_source(ctx.ledger, source, root=ctx.root, source_before=ctx.source_before)
            produced.append(text)
            reads.append({"source": source.id, "text_artifact": text.id})
        except (ValueError, OSError) as error:
            reads.append({"source": source.id, "error": str(error)[:500]})
    return Result(observation={"query": query, "query_id": event.id,
                  "sources": [ctx.preview(a, query) for a in sources], "reads": reads, "providers": diagnostics},
                  produced=tuple(produced), summary={"query": query, "sources": len(sources),
                  "read": len(reads), "providers": diagnostics})


def instrument_trace(ctx: ActionContext, args: dict[str, Any]) -> dict[str, Any]:
    """Describe an attempt before dispatch."""
    name = str(args.get("name", ""))
    tool = ctx.workbench.tools.get(name) or ctx.workbench.dynamic.get(name)
    if tool is None:
        raise KeyError(name)
    ctx.get(tool.id)
    target = ctx.get(ctx.task.target)
    targets = list(target.refs.get("targets", ())) or [ctx.task.target]
    protocol = {"action": "instrument", "tool": tool.id, "spec": args.get("spec", {})}
    signature = attempt_signature(ctx.ledger, targets=targets, protocol=protocol)
    prior = attempt_history(ctx.ledger, ctx.kernel, signature=signature,
                            accept=lambda aid: ctx.permitted(ctx.ledger.get(aid)))["items"]
    return {"attempt": {"signature": signature, "targets": targets, "protocol": protocol},
            "prior_attempts": [x["decision"] for x in prior]}


def instrument(ctx: ActionContext, args: dict[str, Any]) -> Result:
    name, spec = str(args.get("name", "")), args.get("spec", {})
    tool = ctx.workbench.tools.get(name) or ctx.workbench.dynamic.get(name)
    if tool is None:
        raise KeyError(name)
    ctx.get(tool.id)
    if ctx.scope and not ctx.scope.tool_allowed(tool.id):
        raise PermissionError("tool not permitted")
    if name in ctx.workbench.tools:
        require_inputs(ctx.ledger, tool, spec)
    evidence, result = ctx.workbench.call(name, spec, target=ctx.get(ctx.task.target))
    return Result(observation={"instrument": name, "result_id": result.id,
                  "execution_verdict": evidence.data.get("verdict"), "preview": excerpt(result, ""),
                  "prior_attempts": ctx.decision.data["payload"].get("prior_attempts", [])},
                  produced=(evidence, result), summary={"instrument": name, "result_id": result.id})


def define_instrument(ctx: ActionContext, args: dict[str, Any]) -> Result:
    if ctx.scope and ctx.scope.tool_ids is not None:
        raise PermissionError("defining an executable instrument requires separate approval under a tool allowlist")
    definition = ctx.workbench.define_expression(
        name=str(args.get("name", "")), description=str(args.get("description", "")),
        inputs=[str(x) for x in args.get("inputs", [])], expression=str(args.get("expression", "")),
        tests=list(args.get("tests", [])), target=ctx.get(ctx.task.target))
    return Result("active", {"status": "active", "name": definition.data["name"],
                  "definition_id": definition.id}, (definition,), summary={"instrument": definition.data["name"]})


def attempts(ctx: ActionContext, args: dict[str, Any]) -> Result:
    targets = ctx.references(args.get("targets", [ctx.task.target]))
    page = attempt_history(ctx.ledger, ctx.kernel, targets=targets,
        accept=lambda aid: ctx.permitted(ctx.ledger.get(aid)), offset=args.get("offset", 0), limit=args.get("limit", 8))
    return Result(observation=page, observed=tuple(x["decision"] for x in page["items"]),
                  summary={"total": page["total"], "offset": page["offset"]})


def interpret(ctx: ActionContext, args: dict[str, Any]) -> Result:
    target = args.get("target", ctx.task.target)
    evidence = args.get("evidence", [])
    if not isinstance(evidence, list):
        raise ValueError("evidence must be a list")
    ctx.references([target, *evidence])
    return artifacts(ctx, interpret_checks(ctx.ledger, ctx.kernel, target, evidence, args["statement"]),
                     status="interpreted")


def memo(ctx: ActionContext, args: dict[str, Any]) -> Result:
    text = str(args.get("text", "")).strip()
    if not text:
        return Result("empty", summary={"n": 0})
    note = ctx.ledger.put("memo", {"text": text[:12_000], "task": ctx.task.verb},
                          {"target": [ctx.task.target], "decision": [ctx.decision.id]}, by="research-model")
    return Result("persisted", {"memo": note.id}, (note,), summary={"n": 1})


def finalize(ctx: ActionContext, args: dict[str, Any]) -> Result:
    visible = {aid for aid in ctx.seen_ids if ctx.permitted(ctx.ledger.get(aid))}
    candidates = store_candidates(ctx.ledger, ctx.task, list(args.get("candidates", [])),
        visible_ids=visible, source_ids=ctx.seen_sources & visible, spans=ctx.seen_spans,
        scope=ctx.scope, branch=ctx.branch)
    indices = args.get("activate", [0] if candidates else [])
    if not isinstance(indices, list) or any(type(i) is not int or not 0 <= i < len(candidates) for i in indices):
        raise ValueError("activate must contain valid integer candidate indices")
    if len(set(indices)) > ctx.max_active:
        raise ValueError("activation exceeds session branch bound")
    live = [activate(ctx.ledger, candidates[i], by="research-controller") for i in dict.fromkeys(indices)]
    status = "activated" if live else "proposals" if candidates else "no_candidates"
    return Result(status, produced=tuple([*candidates, *live]),
                  summary={"candidates": [a.id for a in candidates], "live": [a.id for a in live]},
                  terminal={"candidates": candidates, "live": live})


def new_request(ctx: ActionContext, args: dict[str, Any]) -> Result:
    targets = ctx.references(args.get("targets", []))
    return artifacts(ctx, request(ctx.ledger, ctx.scope.root.id, args["question"], targets=targets,
        branch=ctx.branch, origin="explore", guidance=args.get("guidance", ""), by="research-model"))


def new_branch(ctx: ActionContext, args: dict[str, Any]) -> Result:
    targets = ctx.references(args.get("targets", []))
    branch = open_branch(ctx.ledger, ctx.scope.root.id, args["question"], targets=targets)
    followup = request(ctx.ledger, ctx.scope.root.id, args["question"], targets=targets,
                       branch=branch.id, origin="explore", by="research-model")
    return artifacts(ctx, branch, followup)


def new_challenge(ctx: ActionContext, args: dict[str, Any]) -> Result:
    ctx.references([args["target"], *args.get("attempts", [])])
    return artifacts(ctx, challenge(ctx.ledger, ctx.scope.root.id, args["target"], args["obstruction"],
        attempts=args.get("attempts", []), methods=args.get("methods", ""), by="research-model"))


def check(ctx: ActionContext, args: dict[str, Any]) -> Result:
    ctx.references([args["target"], args["tool"], *args.get("inputs", [])])
    return artifacts(ctx, declare_check(ctx.ledger, ctx.scope.root.id, args["target"], args["protocol"],
        tool=args["tool"], values=args.get("values", {}), inputs=args.get("inputs", []),
        seeds=args.get("seeds", []), branch=ctx.branch, by="research-model"))


def new_schema(ctx: ActionContext, args: dict[str, Any]) -> Result:
    return artifacts(ctx, define_schema(ctx.ledger, args["name"], fields=args.get("fields"),
        references=args.get("references"), display=args.get("display", []), by="research-model"))


def branches(ctx: ActionContext, args: dict[str, Any]) -> Result:
    return Result("proposed", ctx.scope.branch_page(offset=args.get("offset", 0), limit=args.get("limit", 12)))


def branch_state(ctx: ActionContext, args: dict[str, Any]) -> Result:
    branch = args.get("branch")
    if branch not in ctx.scope.branches or not ctx.scope.allowed(branch):
        raise ValueError("branch must belong to this mission and be permitted")
    return artifacts(ctx, set_attention(ctx.ledger, branch, ctx.scope.root.id, args["state"],
        expected=args.get("expected", []), reason=args["reason"], revisit=args.get("revisit", ""), by="research-model"))


def pause_branch(ctx: ActionContext, args: dict[str, Any]) -> Result:
    return branch_state(ctx, {**args, "branch": ctx.branch, "state": "dormant"})


def capsule(ctx: ActionContext, args: dict[str, Any]) -> Result:
    from .capsules import compile_capsule
    from .store import BlobStore
    cap = compile_capsule(ctx.ledger, ctx.kernel, BlobStore(ctx.root / "blobs"), ctx.scope.root.id,
        selections=args.get("selections"), max_chars=int(args.get("max_chars", 120000)), source_before=ctx.source_before)
    return Result("handoff", produced=(cap,), terminal={"capsule": cap})


@dataclass(frozen=True)
class Action:
    arguments: dict[str, Any]
    handler: Callable[[ActionContext, dict], Result]
    mission: bool = False
    branch: bool = False
    inherit: bool = True
    prepare: Callable[[ActionContext, dict], dict] | None = None


ACTIONS = {
    "inspect": Action({"ids": ["artifact-id"], "span": "optional {field,start,end} on a string field"}, inspect),
    "read": Action({"id": "artifact-id", "view": "passage", "start": 0,
                    "length": 4000, "query": None, "refresh": False}, read),
    "local_search": Action({"query": "terms", "limit": 10, "offset": 0}, local_search),
    "literature_search": Action({"query": "terms", "limit": 10, "read": 0}, literature_search),
    "instrument": Action({"name": "any listed instrument", "spec": {}}, instrument, prepare=instrument_trace),
    "define_instrument": Action({"name": "short-name", "description": "what it measures",
        "inputs": ["x", "y"], "expression": "sqrt(x**2+y**2)",
        "tests": [{"inputs": {"x": 3, "y": 4}, "expected": 5.0}]}, define_instrument),
    "attempts": Action({"targets": [], "offset": 0, "limit": 8}, attempts),
    "interpret": Action({"target": "visible-id", "evidence": ["visible-evidence-id"],
        "statement": "interpretation under the declared protocol"}, interpret),
    "memo": Action({"text": "persistent research memory"}, memo),
    "finalize": Action({"candidates": [{"object_kind": "claim|experiment|study|note|...",
        "title": "...", "commitment": "...", "predictions": [], "falsifier": "...", "assumptions": [],
        "source_ids": [], "citations": [], "payload": {}, "links": {},
        "gain": "optional subjective self-assessment", "cost": "optional subjective self-assessment"}],
        "activate": [0]}, finalize),
    "branches": Action({"offset": 0, "limit": 12}, branches, mission=True, inherit=False),
    "branch_state": Action({"branch": "branch-id from branch listing", "state": "active|dormant|closed",
        "expected": ["current-head-ids"], "reason": "why change attention", "revisit": "optional"},
        branch_state, mission=True, inherit=False),
    "request": Action({"question": "new investigation", "targets": ["visible-id"], "guidance": "optional"},
        new_request, mission=True, inherit=False),
    "open_branch": Action({"question": "alternative investigation", "targets": ["visible-id"]},
        new_branch, mission=True, inherit=False),
    "check": Action({"target": "visible-id", "tool": "permitted-tool-id",
        "protocol": "declared question and procedure", "values": {}, "inputs": []}, check, mission=True, inherit=False),
    "challenge": Action({"target": "visible-id", "obstruction": "specific uncertainty", "attempts": [],
        "methods": "optional"}, new_challenge, mission=True, inherit=False),
    "define_schema": Action({"name": "descriptor", "fields": {}, "references": {}, "display": []},
        new_schema, mission=True, inherit=False),
    "capsule": Action({"selections": "optional [{id,span:{field,start,end}}]", "max_chars": 120000},
        capsule, mission=True),
    "pause_branch": Action({"reason": "why pause", "revisit": "what would justify reopening", "expected": []},
        pause_branch, mission=True, branch=True, inherit=False),
}


def offered(ctx: ActionContext) -> dict[str, Action]:
    return {name: spec for name, spec in ACTIONS.items()
            if (not spec.mission or ctx.scope is not None) and (not spec.branch or ctx.branch is not None)}


def describe(ctx: ActionContext, offered_actions: dict[str, Action]) -> dict[str, dict]:
    descriptions = {name: deepcopy(spec.arguments) for name, spec in offered_actions.items()}
    descriptions["attempts"]["targets"] = [ctx.task.target]
    if "pause_branch" in descriptions:
        descriptions["pause_branch"]["expected"] = ctx.scope.state(ctx.branch)["heads"]
    return descriptions
