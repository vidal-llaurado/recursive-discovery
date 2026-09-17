"""Command-line entry point."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import secrets
import sys

from .context import compile_context
from .core import Task, frontier
from .math import install_math
from .instruments import Workbench, install_instruments
from .project import init_project, open_project
from .report import markdown_report
from .research import CommandModel
from .session import research_session
from .replay import trace as replay_trace
from .runtime import Runtime
from .search import multi_search, read_source, remember, remember_text
from .sealed_service import SealedEvaluator, SealedClient, serve, verify_remote


def _project(path: str):
    return open_project(path)


def cmd_init(a):
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
    p = _project(a.project)
    rows = multi_search(
        a.query, max_results=a.limit,
        providers=tuple(a.providers.split(",")), source_before=a.before,
    )
    _, sources = remember(p.ledger, a.query, rows, provider="multi")
    if a.read:
        for s in sources[:a.read]:
            try:
                text = read_source(s.data, max_chars=a.read_chars)
                remember_text(p.ledger, s, text)
            except Exception:
                pass
    for s in sources:
        print(json.dumps({"id": s.id, "title": s.data.get("title"), "published": s.data.get("published"), "url": s.data.get("url")}))
    p.close()


def cmd_context(a):
    p = _project(a.project)
    packet = compile_context(
        p.ledger, p.kernel, Task(a.verb, a.target, a.why, a.lane),
        max_chars=a.chars, max_tokens=a.tokens,
    )
    print(json.dumps(packet, indent=2, ensure_ascii=False))
    p.close()


def cmd_research_step(a):
    p = _project(a.project)
    model = CommandModel(a.model_cmd)
    result = research_session(
        p.ledger, p.kernel, Task(a.verb, a.target, a.why, a.lane), model,
        root=p.root, source_before=a.before, max_active=a.candidates,
    )
    print(json.dumps({
        "status": result["status"],
        "live": [x.id for x in result.get("live", [])],
        "candidates": [x.id for x in result.get("candidates", [])],
        "turns": result.get("turns"),
    }, indent=2))
    p.close()


def cmd_run(a):
    p = _project(a.project)
    model = CommandModel(a.model_cmd) if a.model_cmd else None
    result = Runtime(p.ledger, p.kernel, root=p.root).run(
        model=model, source_before=a.before, max_steps=a.steps,
    )
    clean = dict(result)
    if clean.get("task") is not None:
        t = clean["task"]
        clean["task"] = {"verb": t.verb, "lane": t.lane, "target": t.target, "why": t.why}
    print(json.dumps(clean, indent=2))
    p.close()

def cmd_report(a):
    p = _project(a.project)
    text = markdown_report(p.ledger, p.kernel, study_id=a.study)
    if a.out:
        Path(a.out).write_text(text)
        print(Path(a.out).resolve())
    else:
        print(text, end="")
    p.close()


def cmd_sealed_seal(a):
    client = SealedClient(a.url, a.token)
    value = json.loads(Path(a.file).read_text())
    print(json.dumps(client.seal(value), indent=2, sort_keys=True))

def cmd_sealed_evaluate(a):
    client = SealedClient(a.url, a.token)
    commitment = json.loads(Path(a.commitment).read_text())
    att = client.evaluate(a.handle, a.evaluator, commitment)
    if a.out:
        Path(a.out).write_text(json.dumps(att, indent=2, sort_keys=True))
        print(Path(a.out).resolve())
    else:
        print(json.dumps(att, indent=2, sort_keys=True))

def cmd_sealed_record(a):
    p = _project(a.project)
    commitment = p.ledger.get(a.commitment)
    att = json.loads(Path(a.attestation).read_text())
    evidence, evaluation = verify_remote(
        p.ledger, p.kernel, commitment, att, service_key=a.service_key, cwd=p.root,
    )
    print(json.dumps({"evidence": evidence.id, "evaluation": evaluation.id if evaluation else None}, indent=2))
    p.close()

def cmd_sealed_serve(a):
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
    s = p.add_subparsers(dest="command", required=True, metavar="COMMAND")

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
    x.add_argument("--before", help="publication-date cutoff, YYYY-MM-DD"); x.add_argument("--read", type=int, default=0, help="number of results to fetch full text for"); x.add_argument("--read-chars", type=int, default=40000)
    x.set_defaults(fn=cmd_search)

    x=s.add_parser("context", help="print the compiled context packet for a task")
    x.add_argument("project"); x.add_argument("target")
    x.add_argument("--verb", default="explore"); x.add_argument("--lane", default="meta"); x.add_argument("--why", default="advance the research frontier")
    x.add_argument("--chars", type=int, default=16000); x.add_argument("--tokens", type=int)
    x.set_defaults(fn=cmd_context)

    x=s.add_parser("trace", help="print recorded decisions and their outcomes")
    x.add_argument("project"); x.add_argument("--target"); x.set_defaults(fn=cmd_trace)

    x=s.add_parser("research-step", help="run one exploratory model session for a target artifact")
    x.add_argument("project"); x.add_argument("target"); x.add_argument("--model-cmd", required=True, help="command implementing the model bridge")
    x.add_argument("--verb", default="explore"); x.add_argument("--lane", default="meta"); x.add_argument("--why", default="advance the research frontier")
    x.add_argument("--before"); x.add_argument("--candidates", type=int, default=5); x.set_defaults(fn=cmd_research_step)

    x=s.add_parser("run", help="execute frontier work until complete, blocked, or the step limit")
    x.add_argument("project"); x.add_argument("--model-cmd", help="command implementing the model bridge"); x.add_argument("--before"); x.add_argument("--steps", type=int, default=50); x.set_defaults(fn=cmd_run)

    x=s.add_parser("resume", help="continue the frontier loop from existing ledger state")
    x.add_argument("project"); x.add_argument("--model-cmd", help="command implementing the model bridge"); x.add_argument("--before"); x.add_argument("--steps", type=int, default=50); x.set_defaults(fn=cmd_run)

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
    a = parser().parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
