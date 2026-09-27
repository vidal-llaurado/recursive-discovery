"""Command-line entry point."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import secrets
import sys

from .context import compile_context
from .core import Task, frontier
from .investigation import Scope
from .protocols import declare_check, interpret_checks
from .math import install_math
from .instruments import Workbench, install_instruments
from .session import research_session
from .replay import trace as replay_trace
from .runtime import Runtime
from .search import multi_search, acquire_source, remember


def _project(path: str):
    from .project import open_project
    project = open_project(path)
    from .telemetry import inventory
    inventory(project.ledger)
    return project


def cmd_init(a):
    from .project import init_project
    p = init_project(a.project)
    install_math(p.ledger)
    install_instruments(p.ledger)
    print(p.root)
    p.close()


def cmd_status(a):
    p = _project(a.project)
    print(json.dumps({
        "project": str(p.root),
        "counts": p.ledger.counts(),
        "frontier": len(frontier(p.ledger, p.kernel)),
    }, indent=2, sort_keys=True))
    p.close()


def cmd_frontier(a):
    p = _project(a.project)
    for t in frontier(p.ledger, p.kernel):
        print(json.dumps({"verb": t.verb, "lane": t.lane, "target": t.target, "why": t.why}))
    p.close()


def cmd_inspect(a):
    p = _project(a.project)
    x = p.ledger.get(a.artifact)
    print(json.dumps({
        "id": x.id, "kind": x.kind, "data": x.data,
        "refs": {k: list(v) for k, v in x.refs.items()}, "by": x.by, "t": x.t,
    }, indent=2, ensure_ascii=False, sort_keys=True))
    p.close()


def cmd_search(a):
    from .investigation import include
    p = _project(a.project)
    try:
        scope = Scope(p.ledger, a.mission, source_before=a.before) if a.mission else None
        cutoff = scope.source_before if scope else a.before
        diagnostics = []
        rows = multi_search(a.query, max_results=a.limit,
                            providers=tuple(a.providers.split(",")), source_before=cutoff,
                            diagnostics=diagnostics)
        query, sources = remember(p.ledger, a.query, rows, provider="multi", diagnostics=diagnostics)
        if scope:
            sources = [source for source in sources if scope.allowed(source.id)]
            include(p.ledger, scope.root.id, [query.id, *[source.id for source in sources]])
        # Keep result rows on stdout; provider status remains machine-readable on stderr.
        print(json.dumps({"query_id": query.id, "providers": diagnostics}), file=sys.stderr)
        for i, source in enumerate(sources):
            row = {"id": source.id, "title": source.data.get("title"),
                   "published": source.data.get("published"), "url": source.data.get("url")}
            if i < a.read:
                try:
                    text = acquire_source(p.ledger, source, root=p.root, source_before=cutoff)
                    if scope and not scope.allowed(text.id):
                        raise PermissionError("source extract outside information boundary")
                    row.update(text_id=text.id, read_status="acquired", scope=text.data.get("scope"),
                               preview=text.data["text"][:a.read_chars])
                    if scope:
                        include(p.ledger, scope.root.id, [text.id])
                except (ValueError, OSError) as error:
                    row.update(read_status="failed", error=f"{type(error).__name__}: {error}")
            print(json.dumps(row, ensure_ascii=False))
    finally:
        p.close()


def cmd_context(a):
    p = _project(a.project)
    packet = compile_context(
        p.ledger, p.kernel, Task(a.verb, a.target, a.why, a.lane),
        max_chars=a.chars, max_tokens=a.tokens,
        scope=Scope(p.ledger, a.mission, source_before=a.before) if a.mission else None, source_before=a.before,
    )
    print(json.dumps(packet, indent=2, ensure_ascii=False))
    p.close()


def cmd_research_step(a):
    from .research import CommandModel
    p = _project(a.project)
    try:
        model = CommandModel(a.model_cmd)
        result = research_session(
            p.ledger, p.kernel, Task(a.verb, a.target, a.why, a.lane), model,
            root=p.root, source_before=a.before, max_active=a.candidates,
            context_chars=a.chars, branch=a.branch,
            scope=Scope(p.ledger, a.mission, source_before=a.before) if a.mission else None,
        )
        print(json.dumps({"status": result["status"],
                         "live": [x.id for x in result.get("live", [])],
                         "candidates": [x.id for x in result.get("candidates", [])],
                         "turns": result.get("turns")}, indent=2))
    finally:
        p.close()


def cmd_run(a):
    from .research import CommandModel
    p = _project(a.project)
    model = CommandModel(a.model_cmd) if a.model_cmd else None
    result = Runtime(p.ledger, p.kernel, root=p.root).run(
        model=model, source_before=a.before, max_steps=a.steps,
        mission=a.mission, context_chars=a.chars,
    )
    clean = dict(result)
    if clean.get("task") is not None:
        t = clean["task"]
        clean["task"] = {"verb": t.verb, "lane": t.lane, "target": t.target, "why": t.why}
    print(json.dumps(clean, indent=2))
    p.close()

def cmd_report(a):
    from .report import markdown_report
    p = _project(a.project)
    text = markdown_report(p.ledger, p.kernel, study_id=a.study)
    if a.out:
        Path(a.out).write_text(text)
        print(Path(a.out).resolve())
    else:
        print(text, end="")
    p.close()


def cmd_sealed_seal(a):
    from .sealed_service import SealedClient
    client = SealedClient(a.url, a.token)
    value = json.loads(Path(a.file).read_text())
    print(json.dumps(client.seal(value), indent=2, sort_keys=True))

def cmd_sealed_evaluate(a):
    from .sealed_service import SealedClient
    client = SealedClient(a.url, a.token)
    commitment = json.loads(Path(a.commitment).read_text())
    att = client.evaluate(a.handle, a.evaluator, commitment)
    if a.out:
        Path(a.out).write_text(json.dumps(att, indent=2, sort_keys=True))
        print(Path(a.out).resolve())
    else:
        print(json.dumps(att, indent=2, sort_keys=True))

def cmd_sealed_record(a):
    from .sealed_service import verify_remote
    p = _project(a.project)
    commitment = p.ledger.get(a.commitment)
    att = json.loads(Path(a.attestation).read_text())
    evidence, evaluation = verify_remote(
        p.ledger, p.kernel, commitment, att, service_key=a.service_key, cwd=p.root,
    )
    print(json.dumps({"evidence": evidence.id, "evaluation": evaluation.id if evaluation else None}, indent=2))
    p.close()

def cmd_sealed_serve(a):
    from .sealed_service import SealedEvaluator, serve
    registry = json.loads(Path(a.registry).read_text())
    token = a.token or secrets.token_urlsafe(32)
    evaluator = SealedEvaluator(a.root, key_path=a.key, registry=registry)
    print(f"sealed evaluator listening on {a.host}:{a.port}; token={token}", file=sys.stderr, flush=True)
    serve(evaluator, token=token, host=a.host, port=a.port)



def cmd_tools(a):
    p = _project(a.project)
    wb = Workbench(p.ledger, p.kernel, root=p.root)
    print(json.dumps(wb.describe(), indent=2, ensure_ascii=False))
    p.close()


def cmd_instrument(a):
    p = _project(a.project)
    wb = Workbench(p.ledger, p.kernel, root=p.root)
    spec = json.loads(Path(a.spec).read_text())
    target = p.ledger.get(a.target)
    evidence, result = wb.call(a.name, spec, target=target, by="human")
    print(json.dumps({
        "evidence": evidence.id,
        "result": result.id,
        "value": result.data.get("value"),
    }, indent=2, ensure_ascii=False))
    p.close()


def cmd_define_instrument(a):
    p = _project(a.project)
    wb = Workbench(p.ledger, p.kernel, root=p.root)
    spec = json.loads(Path(a.definition).read_text())
    target = p.ledger.get(a.target)
    definition = wb.define_expression(
        name=spec["name"],
        description=spec.get("description", ""),
        inputs=list(spec["inputs"]),
        expression=spec["expression"],
        tests=list(spec["tests"]),
        target=target,
        by="human",
    )
    print(json.dumps({"definition": definition.id, "name": definition.data["name"]}, indent=2))
    p.close()


def cmd_trace(a):
    p = _project(a.project)
    rows = replay_trace(p.ledger, target=a.target)
    out = []
    for row in rows:
        d = row["decision"]
        out.append({
            "decision": d.id,
            "action": d.data.get("action"),
            "state_digest": d.data.get("state_digest"),
            "policy": d.data.get("policy"),
            "target": list(d.refs.get("target", ())),
            "outcomes": [
                {"id": o.id, "status": o.data.get("status"), "summary": o.data.get("summary")}
                for o in row["outcomes"]
            ],
        })
    print(json.dumps(out, indent=2, ensure_ascii=False))
    p.close()

MISSION_OPERATIONS = (
    "program", "create", "revise", "branch", "include", "attention", "supersede", "request",
    "challenge", "check", "interpret", "schema", "agenda", "attempts", "capsule", "changes", "receive", "accept",
)


def cmd_mission(a):
    """Dispatch mission commands to their Python APIs."""
    from dataclasses import asdict, is_dataclass
    from . import investigation as inv
    from . import capsules
    from .schema import define_schema
    from .agenda import available_work
    from .replay import attempt_history
    raw = sys.stdin.read() if a.spec == "-" else Path(a.spec).read_text(encoding="utf-8")
    spec = json.loads(raw)
    if not isinstance(spec, dict):
        raise ValueError("mission spec must be a JSON object")
    p = _project(a.project)
    try:
        operations = {"program": inv.create_program, "create": inv.create_mission,
            "revise": inv.revise_mission, "branch": inv.open_branch, "include": inv.include,
            "attention": inv.set_attention, "supersede": inv.supersede, "request": inv.request,
            "challenge": inv.challenge, "check": declare_check, "schema": define_schema}
        if a.operation in operations:
            result = operations[a.operation](p.ledger, **spec)
        elif a.operation == "interpret":
            result = interpret_checks(p.ledger, p.kernel, **spec)
        elif a.operation == "agenda":
            scope = Scope(p.ledger, spec["mission"])
            offset, limit = spec.get("offset", 0), spec.get("limit", 20)
            if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
                raise ValueError("invalid agenda page")
            agenda = available_work(p.ledger, p.kernel, scope)
            items = agenda["eligible"]
            result = {"items": [x.row() for x in items[offset:offset+limit]], "total": len(items),
                      "next": offset+limit if offset+limit < len(items) else None,
                      "suppressed": agenda["suppressed"]}
        elif a.operation == "attempts":
            mission = spec.pop("mission", None)
            scope = Scope(p.ledger, mission) if mission else None
            result = attempt_history(p.ledger, p.kernel, accept=scope.allowed if scope else None, **spec)
        elif a.operation == "capsule":
            directory = spec.pop("directory", None)
            capsule = capsules.compile_capsule(p.ledger, p.kernel, p.blobs, **spec)
            result = capsules.export_capsule(p.ledger, p.blobs, capsule.id, directory) if directory else capsule
        elif a.operation == "changes":
            result = capsules.changes_since(p.ledger, p.blobs, **spec)
        elif a.operation == "receive":
            if "response_file" in spec:
                spec["text"] = Path(spec.pop("response_file")).read_text(encoding="utf-8")
            result = capsules.receive_response(p.ledger, p.blobs, **spec)
        elif a.operation == "accept":
            result = capsules.accept_response(p.ledger, p.blobs, **spec)
        else:
            raise ValueError("unknown mission operation")
        print(json.dumps(result, ensure_ascii=False, indent=2,
                         default=lambda x: asdict(x) if is_dataclass(x) else str(x)))
    finally:
        p.close()


def cmd_read(a):
    from .reading import available, outline, passage
    from .search import acquire_source
    p = _project(a.project)
    try:
        scope = Scope(p.ledger, a.mission, source_before=a.before) if a.mission else None
        cutoff = scope.source_before if scope else a.before
        artifact = p.ledger.get(a.artifact)
        permitted = lambda x: scope.allowed(x.id) if scope else available(p.ledger, x, cutoff)
        if not permitted(artifact):
            raise PermissionError("artifact outside information boundary")
        if artifact.kind == "source":
            artifact = acquire_source(p.ledger, artifact, root=p.root, source_before=cutoff, refresh=a.refresh)
        if not permitted(artifact):
            raise PermissionError("source extract outside information boundary")
        result = (outline(artifact, start=a.start) if a.outline else
                  passage(artifact, start=a.start, length=a.length, query=a.query))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        p.close()


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="recursive-discovery",
        description="Recursive mathematical and empirical scientific discovery.",
        epilog=(
            "Typical flow: init, then create a study, then research-step with a --model-cmd "
            "bridge to propose work, then run or resume to execute the frontier.\n"
            "See docs/USAGE.md for the model bridge contract and examples."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--telemetry-dir", help="opt-in external telemetry directory, outside worker mounts")
    p.add_argument("--telemetry-content", action="store_true", help="explicitly capture sensitive redacted request/response content")
    s = p.add_subparsers(dest="command", required=True, metavar="COMMAND")

    x=s.add_parser("mission", help="native mission, lifecycle and capsule operations from JSON specs")
    x.add_argument("project"); x.add_argument("operation", choices=MISSION_OPERATIONS)
    x.add_argument("--spec", required=True, help="JSON argument object; '-' reads stdin; see docs/WORKFLOWS.md")
    x.set_defaults(fn=cmd_mission)

    x=s.add_parser("read", help="read retained source outlines or exact passages")
    x.add_argument("project"); x.add_argument("artifact"); x.add_argument("--mission"); x.add_argument("--before")
    x.add_argument("--outline", action="store_true"); x.add_argument("--refresh", action="store_true")
    x.add_argument("--query"); x.add_argument("--start", type=int, default=0); x.add_argument("--length", type=int, default=4000)
    x.set_defaults(fn=cmd_read)

    x=s.add_parser("init", help="create a workspace (ledger, key, work and report dirs)")
    x.add_argument("project"); x.set_defaults(fn=cmd_init)

    x=s.add_parser("status", help="show artifact counts and workspace summary")
    x.add_argument("project"); x.set_defaults(fn=cmd_status)

    x=s.add_parser("frontier", help="list pending work derived from missing evidence")
    x.add_argument("project"); x.set_defaults(fn=cmd_frontier)

    x=s.add_parser("inspect", help="show one artifact by ID, including its payload and refs")
    x.add_argument("project"); x.add_argument("artifact"); x.set_defaults(fn=cmd_inspect)

    x=s.add_parser("tools", help="list installed mathematical and workbench tools")
    x.add_argument("project"); x.set_defaults(fn=cmd_tools)

    x=s.add_parser("instrument", help="call an instrument with a JSON spec file")
    x.add_argument("project"); x.add_argument("name"); x.add_argument("spec"); x.add_argument("--target", required=True, help="artifact this call is attached to"); x.set_defaults(fn=cmd_instrument)

    x=s.add_parser("define-instrument", help="register a persistent expression instrument")
    x.add_argument("project"); x.add_argument("definition"); x.add_argument("--target", required=True, help="artifact this definition is attached to"); x.set_defaults(fn=cmd_define_instrument)

    x=s.add_parser("search", help="query arXiv, OpenAlex, and Crossref, optionally reading sources")
    x.add_argument("project"); x.add_argument("query")
    x.add_argument("--providers", default="arxiv,openalex,crossref"); x.add_argument("--limit", type=int, default=12)
    x.add_argument("--before", help="publication-date cutoff, YYYY-MM-DD"); x.add_argument("--read", type=int, default=0, help="number of results to fetch full text for"); x.add_argument("--read-chars", type=int, default=4000, help="printed preview size; retained extract is not truncated"); x.add_argument("--mission")
    x.set_defaults(fn=cmd_search)

    x=s.add_parser("context", help="print the compiled context packet for a task")
    x.add_argument("project"); x.add_argument("target")
    x.add_argument("--verb", default="explore"); x.add_argument("--lane", default="meta"); x.add_argument("--why", default="advance the research frontier")
    x.add_argument("--chars", type=int, default=16000); x.add_argument("--tokens", type=int); x.add_argument("--mission"); x.add_argument("--before")
    x.set_defaults(fn=cmd_context)

    x=s.add_parser("trace", help="print recorded decisions and their outcomes")
    x.add_argument("project"); x.add_argument("--target"); x.set_defaults(fn=cmd_trace)

    x=s.add_parser("research-step", help="run one exploratory model session for a target artifact")
    x.add_argument("project"); x.add_argument("target"); x.add_argument("--model-cmd", required=True, help="command implementing the model bridge")
    x.add_argument("--verb", default="explore"); x.add_argument("--lane", default="meta"); x.add_argument("--why", default="advance the research frontier")
    x.add_argument("--before"); x.add_argument("--candidates", type=int, default=5)
    x.add_argument("--mission"); x.add_argument("--branch"); x.add_argument("--chars", type=int, default=18000); x.set_defaults(fn=cmd_research_step)

    x=s.add_parser("run", help="execute frontier work until complete, blocked, or the step limit")
    x.add_argument("project"); x.add_argument("--model-cmd", help="command implementing the model bridge"); x.add_argument("--before"); x.add_argument("--steps", type=int, default=50); x.add_argument("--mission"); x.add_argument("--chars", type=int, default=18000); x.set_defaults(fn=cmd_run)

    x=s.add_parser("resume", help="continue the frontier loop from existing ledger state")
    x.add_argument("project"); x.add_argument("--model-cmd", help="command implementing the model bridge"); x.add_argument("--before"); x.add_argument("--steps", type=int, default=50); x.add_argument("--mission"); x.add_argument("--chars", type=int, default=18000); x.set_defaults(fn=cmd_run)

    x=s.add_parser("report", help="write a Markdown report of the scientific record")
    x.add_argument("project"); x.add_argument("--study"); x.add_argument("--out"); x.set_defaults(fn=cmd_report)

    x=s.add_parser("sealed-serve", help="run the separate sealed-evaluation service")
    x.add_argument("--root", required=True); x.add_argument("--key", required=True); x.add_argument("--registry", required=True)
    x.add_argument("--token"); x.add_argument("--host", default="127.0.0.1"); x.add_argument("--port", type=int, default=8765); x.set_defaults(fn=cmd_sealed_serve)

    x=s.add_parser("sealed-seal", help="upload secret bytes to the sealed service and get a handle")
    x.add_argument("--url", required=True); x.add_argument("--token", required=True); x.add_argument("--file", required=True); x.set_defaults(fn=cmd_sealed_seal)

    x=s.add_parser("sealed-evaluate", help="reveal a sealed test once and return an attestation")
    x.add_argument("--url", required=True); x.add_argument("--token", required=True); x.add_argument("--handle", required=True); x.add_argument("--evaluator", required=True); x.add_argument("--commitment", required=True); x.add_argument("--out"); x.set_defaults(fn=cmd_sealed_evaluate)

    x=s.add_parser("sealed-record", help="verify a service attestation and record it in the ledger")
    x.add_argument("project"); x.add_argument("commitment"); x.add_argument("attestation"); x.add_argument("--service-key", required=True); x.set_defaults(fn=cmd_sealed_record)
    return p


def main():
    p = parser()
    a = p.parse_args()
    if a.telemetry_content and not a.telemetry_dir:
        p.error("--telemetry-content requires --telemetry-dir")
    if a.telemetry_dir:
        from .telemetry import observe
        with observe(a.telemetry_dir, capture="content" if a.telemetry_content else "metadata"):
            a.fn(a)
    else:
        a.fn(a)


if __name__ == "__main__":
    main()
