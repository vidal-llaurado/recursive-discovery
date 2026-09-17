"""Human-readable scientific report generated directly from ledger provenance."""
from __future__ import annotations

from collections import Counter
from typing import Iterable

from .context import authority
from .core import Artifact, Kernel, Ledger, frontier


def _short(x: object, n: int = 220) -> str:
    s = " ".join(str(x).split())
    return s if len(s) <= n else s[:n - 1] + "…"


def markdown_report(ledger: Ledger, kernel: Kernel, *, study_id: str | None = None) -> str:
    if study_id:
        study = ledger.get(study_id)
        if study.kind != "study":
            raise ValueError("study_id must reference a study")
        scope = {study.id}
        # Pull two-hop descendants and direct parents: enough for a legible research dossier.
        first = ledger.children(study.id)
        scope.update(a.id for a in first)
        for a in first:
            scope.update(x.id for x in ledger.children(a.id))
        arts = [ledger.get(x) for x in scope]
        title = study.data.get("question") or study.data.get("name") or study.id
    else:
        arts = ledger.all()
        title = "Recursive Discovery Project"

    kinds = Counter(a.kind for a in arts)
    auth = Counter(authority(a, ledger, kernel) for a in arts)
    lines = [f"# {title}", "", "## Scientific state", ""]
    lines += [f"- Artifacts: **{len(arts)}**", f"- Open frontier tasks: **{len(frontier(ledger, kernel))}**"]
    if auth:
        lines.append("- Authority: " + ", ".join(f"{k}={v}" for k, v in sorted(auth.items())))
    lines.append("- Artifact types: " + ", ".join(f"{k}={v}" for k, v in sorted(kinds.items())))

    def section(kind: str, heading: str, render):
        xs = [a for a in arts if a.kind == kind]
        if not xs:
            return
        lines.extend(["", f"## {heading}", ""])
        for a in xs:
            lines.append(render(a))

    section("claim", "Claims", lambda a: f"- `{a.id}` — {_short(a.data.get('statement') or a.data.get('commitment') or a.data)}")
    section("assumption", "Assumptions", lambda a: f"- `{a.id}` — {_short(a.data)}")
    section("evidence", "Trusted consequences", lambda a: f"- `{a.id}` — **{a.data.get('verdict')}** / {a.data.get('semantics')} / runner={a.data.get('run',{}).get('runner')} / target={','.join(a.refs.get('target',()))}")
    section("empirical_summary", "Empirical summaries", lambda a: f"- `{a.id}` — {_short(a.data.get('metrics', a.data), 500)}")
    section("evaluation", "Prospective evaluations", lambda a: f"- `{a.id}` — {_short(a.data, 500)}")
    section("discrepancy", "Discrepancies", lambda a: f"- `{a.id}` — class={a.data.get('class')} {_short(a.data.get('reason',''))}")
    section("invariant", "Promoted invariants", lambda a: f"- `{a.id}` — {_short(a.data)}")
    section("representation", "Representations", lambda a: f"- `{a.id}` — status={a.data.get('status')} {_short(a.data)}")
    section("mechanism", "Mechanisms", lambda a: f"- `{a.id}` — {_short(a.data.get('law', a.data))}")
    section("world", "Research worlds", lambda a: f"- `{a.id}` — {_short(a.data)}")
    section("decision", "Research decisions", lambda a: f"- `{a.id}` — **{a.data.get('action')}** / policy={a.data.get('policy')} / state={str(a.data.get('state_digest',''))[:12]}")
    section("decision_outcome", "Decision outcomes", lambda a: f"- `{a.id}` — **{a.data.get('status')}** / action={a.data.get('action')} / {_short(a.data.get('summary',''),300)}")
    section("source", "Literature sources", lambda a: f"- `{a.id}` — **{_short(a.data.get('title',''),120)}** ({a.data.get('published','')}) — {a.data.get('url','')}")

    tasks = frontier(ledger, kernel)
    if tasks:
        lines.extend(["", "## Open frontier", ""])
        for t in tasks:
            lines.append(f"- **{t.verb}** `{t.target}` [{t.lane}] — {t.why}")

    lines.extend(["", "## Reproduction", "", "All IDs above are content-addressed scientific artifacts. Trusted consequences can be rechecked against the project's kernel key and declared input hashes. Reproduction bundles and source snapshots, when present, are referenced by their artifact IDs."])
    return "\n".join(lines) + "\n"
