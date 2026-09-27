"""Read retained artifacts by version and character range.

Offsets are zero-based, half-open Python character offsets in the extracted text.
The artifact ID identifies the version.
"""
from __future__ import annotations
from .telemetry import traced

from datetime import date
import json
import re
from typing import Any, Callable, Iterable

from .core import Artifact, Ledger


def published_before(data: dict[str, Any], cutoff: str | None) -> bool:
    """Check publication and version dates against a cutoff.

    Missing, partial or invalid dates fail closed. Index maintenance timestamps are
    not publication revision dates.
    """
    if cutoff is None:
        return True
    bound = date.fromisoformat(cutoff)
    for value in [data.get("published"), *([data["version_date"]] if data.get("version_date") else [])]:
        try:
            if date.fromisoformat(str(value)[:10]) > bound:
                return False
        except ValueError:
            return False
    return True


def available(ledger: Ledger, artifact: Artifact, cutoff: str | None,
              *, denied: Iterable[str] = ()) -> bool:
    """Apply restrictions to metadata and declared dependencies.

    Candidate semantic links count as dependencies; administrative membership does not.
    Filesystem, network and undeclared inputs require separate controls.
    """
    denied = set(denied)
    if cutoff is None and not denied:
        return True
    if cutoff is not None:
        date.fromisoformat(cutoff)
    pending, seen = [artifact], set()
    while pending:
        a = pending.pop()
        if a.id in seen:
            continue
        seen.add(a.id)
        if a.id in denied:
            return False
        if a.kind == "source" and not published_before(a.data, cutoff):
            return False
        if cutoff and a.kind == "source_text" and not a.refs.get("source"):
            return False
        dependencies = [x for role, ids in a.refs.items()
                        if role not in {"mission", "branch", "program", "scope", "previous", "members"}
                        for x in ids]
        if a.kind == "candidate":
            for ids in a.data.get("links", {}).values():
                if not isinstance(ids, (list, tuple)) or any(not isinstance(x, str) for x in ids):
                    return False
                dependencies.extend(ids)
        for aid in dependencies:
            if aid not in seen:
                try:
                    pending.append(ledger.get(aid))
                except KeyError:
                    return False
    return True


def artifact_text(artifact: Artifact) -> str:
    if artifact.kind == "source_text":
        return str(artifact.data.get("text", ""))
    return json.dumps({"kind": artifact.kind, "data": artifact.data, "refs": artifact.refs},
                      sort_keys=True, ensure_ascii=False, indent=2)


@traced("reading.passage")
def passage(
    artifact: Artifact, *, start: int = 0, length: int = 4000, query: str | None = None,
) -> dict[str, Any]:
    """Read a range or find a literal, case-insensitive match at or after start.

    Use next for sequential reading and search_next for further matches. A miss returns
    no_match without substituting other text.
    """
    text = artifact_text(artifact)
    if type(start) is not int or not 0 <= start <= len(text):
        raise ValueError("start must be an integer between zero and total_chars")
    if type(length) is not int or not 1 <= length <= 12_000:
        raise ValueError("length must be an integer in [1, 12000]")
    row: dict[str, Any] = {
        "id": artifact.id, "kind": artifact.kind, "view": "passage",
        "field": "text" if artifact.kind == "source_text" else "$artifact_json",
        "source_ids": list(artifact.refs.get("source", ())), "total_chars": len(text),
    }
    if artifact.kind == "source_text":
        row["scope"] = artifact.data.get("scope", "legacy_excerpt")
        row["raw_sha256"] = artifact.data.get("raw_sha256")
        row["warnings"] = artifact.data.get("warnings", [])
    if query is not None:
        if not query.strip():
            raise ValueError("query must not be empty")
        match = re.compile(re.escape(query), re.IGNORECASE).search(text, start)
        if match is None:
            return {**row, "status": "no_match", "query": query}
        row.update(match_start=match.start(), search_next=match.end())
        start = max(0, match.start() - min(length // 4, 240))
    end = min(len(text), start + length)
    row.update(status="ok", start=start, end=end, text=text[start:end],
               next=end if end < len(text) else None)
    pages = artifact.data.get("pages", []) if artifact.kind == "source_text" else []
    if pages:
        row["pages"] = [p["page"] for p in pages if p["start"] < end and p["end"] > start]
    return row


@traced("reading.outline")
def outline(artifact: Artifact, *, start: int = 0, limit: int = 30) -> dict[str, Any]:
    """Return paginated page boundaries or headings.

    ATX headings outside fenced code are recognized. Range reads work without an outline.
    """
    if type(start) is not int or start < 0 or type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("invalid outline start or limit")
    text = artifact_text(artifact)
    entries = []
    if artifact.kind == "source_text" and artifact.data.get("pages"):
        entries = [{"title": f"Page {p['page']}", "level": 1, "start": p["start"]}
                   for p in artifact.data["pages"]]
    else:
        offset, fence = 0, None
        for line in text.splitlines(keepends=True):
            marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
            if marker:
                token = marker.group(1)
                if fence is None:
                    fence = token
                elif token[0] == fence[0] and len(token) >= len(fence):
                    fence = None
            elif fence is None:
                heading = re.match(r"^ {0,3}(#{1,6})\s+(.+?)\s*#*\s*$", line)
                if heading:
                    entries.append({"title": heading.group(2)[:240],
                                    "level": len(heading.group(1)), "start": offset})
            offset += len(line)
    selected = entries[start:start + limit]
    return {"id": artifact.id, "kind": artifact.kind, "view": "outline",
            "source_ids": list(artifact.refs.get("source", ())),
            "entries": selected, "total": len(entries),
            "next": start + len(selected) if start + len(selected) < len(entries) else None}


def excerpt(artifact: Artifact, query: str, *, length: int = 1600) -> dict[str, Any]:
    """Select a lexical preview near query terms.

    Candidate windows include the first occurrence of each distinct term.
    """
    text = artifact_text(artifact)
    terms = list(dict.fromkeys(re.findall(r"\w{3,}", query.lower())))[:16]
    patterns = [re.compile(r"\b" + re.escape(t) + r"\b", re.IGNORECASE) for t in terms]
    starts = {0}
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            starts.add(max(0, match.start() - length // 4))
    start = max(sorted(starts), key=lambda s: sum(bool(p.search(text[s:s + length])) for p in patterns))
    return passage(artifact, start=start, length=length)


@traced("search.local")
def search_local(
    ledger: Ledger, query: str, *, limit: int = 20, cutoff: str | None = None,
    accept: Callable[[str], bool] | None = None,
) -> list[Artifact]:
    """Preserve backend ranking while filtering; excluded hits do not occupy result slots."""
    if type(limit) is not int or limit < 1:
        raise ValueError("limit must be a positive integer")
    if not hasattr(ledger, "search"):
        terms = set(re.findall(r"\w+", query.lower()))
        scored = []
        for a in ledger.all():
            text = artifact_text(a).lower()
            scored.append((sum(t in text for t in terms), a))
        rows = [a for score, a in sorted(scored, key=lambda x: (-x[0], x[1].id)) if score]
        return [a for a in rows if available(ledger, a, cutoff) and (accept is None or accept(a.id))][:limit]
    count = limit
    while True:
        rows = ledger.search(query, limit=count)
        eligible = [a for a in rows if available(ledger, a, cutoff) and (accept is None or accept(a.id))]
        if len(eligible) >= limit or len(rows) < count:
            return eligible[:limit]
        count *= 2
