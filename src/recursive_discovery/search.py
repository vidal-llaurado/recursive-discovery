"""Literature and index retrieval (arXiv, OpenAlex, Crossref) and source reading."""
from __future__ import annotations

from collections.abc import Callable, Iterable
from html.parser import HTMLParser
from io import BytesIO
import json
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any

from .core import Artifact, Ledger

ATOM = "http://www.w3.org/2005/Atom"
UA = "recursive-discovery/1.0 research client"


def _get(url: str, *, timeout: float = 20.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _text(node: ET.Element, path: str) -> str:
    x = node.find(path, {"a": ATOM})
    return " ".join((x.text or "").split()) if x is not None else ""


def parse_arxiv(xml: bytes | str) -> list[dict[str, Any]]:
    if isinstance(xml, str):
        xml = xml.encode()
    root = ET.fromstring(xml)
    ns = {"a": ATOM}
    rows = []
    for entry in root.findall("a:entry", ns):
        links = {
            x.attrib.get("title") or x.attrib.get("rel", ""): x.attrib.get("href", "")
            for x in entry.findall("a:link", ns)
        }
        rows.append({
            "provider": "arxiv",
            "id": _text(entry, "a:id"),
            "doi": "",
            "title": _text(entry, "a:title"),
            "abstract": _text(entry, "a:summary"),
            "authors": [_text(a, "a:name") for a in entry.findall("a:author", ns)],
            "published": _text(entry, "a:published"),
            "updated": _text(entry, "a:updated"),
            "categories": [x.attrib.get("term", "") for x in entry.findall("a:category", ns)],
            "url": links.get("alternate") or _text(entry, "a:id"),
            "pdf": links.get("pdf", ""),
        })
    return rows


def arxiv(query: str, *, max_results: int = 8, fetch: Callable[[str], bytes] | None = None) -> list[dict[str, Any]]:
    params = urllib.parse.urlencode({
        "search_query": query, "start": 0, "max_results": max_results,
        "sortBy": "relevance", "sortOrder": "descending",
    })
    raw = (fetch or _get)(f"https://export.arxiv.org/api/query?{params}")
    return parse_arxiv(raw)


def crossref(query: str, *, max_results: int = 8, fetch: Callable[[str], bytes] | None = None) -> list[dict[str, Any]]:
    params = urllib.parse.urlencode({"query.bibliographic": query, "rows": max_results, "select": "DOI,title,author,abstract,published,URL,type,subject"})
    data = json.loads((fetch or _get)(f"https://api.crossref.org/works?{params}"))
    rows = []
    for item in data.get("message", {}).get("items", []):
        title = " ".join(item.get("title", [])[:1])
        authors = [" ".join(filter(None, [a.get("given"), a.get("family")])) for a in item.get("author", [])]
        parts = item.get("published", {}).get("date-parts", [[]])[0]
        published = "-".join(str(x).zfill(2) for x in parts) if parts else ""
        abstract = re.sub(r"<[^>]+>", " ", item.get("abstract", ""))
        rows.append({
            "provider": "crossref", "id": item.get("DOI", ""), "doi": item.get("DOI", ""),
            "title": " ".join(title.split()), "abstract": " ".join(abstract.split()),
            "authors": authors, "published": published, "updated": "",
            "categories": item.get("subject", []), "url": item.get("URL", ""), "pdf": "",
        })
    return rows


def _invert_abstract(index: dict[str, list[int]] | None) -> str:
    if not index:
        return ""
    n = max((max(v) for v in index.values() if v), default=-1) + 1
    words = [""] * n
    for word, positions in index.items():
        for p in positions:
            if 0 <= p < n:
                words[p] = word
    return " ".join(words)


def openalex(query: str, *, max_results: int = 8, fetch: Callable[[str], bytes] | None = None) -> list[dict[str, Any]]:
    params = urllib.parse.urlencode({"search": query, "per-page": max_results})
    data = json.loads((fetch or _get)(f"https://api.openalex.org/works?{params}"))
    rows = []
    for item in data.get("results", []):
        loc = item.get("primary_location") or {}
        source = loc.get("source") or {}
        rows.append({
            "provider": "openalex", "id": item.get("id", ""), "doi": (item.get("doi") or "").replace("https://doi.org/", ""),
            "title": item.get("title", ""), "abstract": _invert_abstract(item.get("abstract_inverted_index")),
            "authors": [x.get("author", {}).get("display_name", "") for x in item.get("authorships", [])],
            "published": item.get("publication_date", ""), "updated": item.get("updated_date", ""),
            "categories": [x.get("display_name", "") for x in item.get("topics", [])[:8]],
            "url": loc.get("landing_page_url") or item.get("doi") or item.get("id", ""),
            "pdf": loc.get("pdf_url") or "", "source": source.get("display_name", ""),
        })
    return rows


def _norm_title(x: str) -> str:
    return re.sub(r"\W+", " ", x.lower()).strip()


def dedupe(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    out, seen = [], set()
    for row in rows:
        key = (row.get("doi") or "").lower().strip()
        if not key:
            key = _norm_title(str(row.get("title", "")))
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def before(rows: list[dict[str, Any]], cutoff: str | None) -> list[dict[str, Any]]:
    if not cutoff:
        return list(rows)
    c = cutoff[:10]
    return [r for r in rows if not str(r.get("published", ""))[:10] or str(r.get("published", ""))[:10] <= c]


def multi_search(
    query: str,
    *,
    max_results: int = 12,
    providers: tuple[str, ...] = ("arxiv", "openalex", "crossref"),
    source_before: str | None = None,
) -> list[dict[str, Any]]:
    """Search independent public research indexes; provider failures do not erase other results."""
    funcs = {"arxiv": arxiv, "openalex": openalex, "crossref": crossref}
    rows: list[dict[str, Any]] = []
    each = max(3, max_results)
    for name in providers:
        try:
            rows.extend(funcs[name](query, max_results=each))
        except Exception:
            continue
    return before(dedupe(rows), source_before)[:max_results]


def remember(
    ledger: Ledger,
    query: str,
    rows: list[dict[str, Any]],
    *,
    refs: dict[str, list[str]] | None = None,
    provider: str = "multi",
) -> tuple[Artifact, list[Artifact]]:
    q = ledger.put("search", {"provider": provider, "query": query, "n": len(rows)}, refs, by=f"search:{provider}")
    common = dict(refs or {})
    common["search"] = [q.id]
    sources = [ledger.put("source", row, common, by=f"search:{row.get('provider', provider)}") for row in rows]
    return q, sources


class _HTMLText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self.skip = 0
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "nav", "footer"}:
            self.skip += 1
    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "nav", "footer"} and self.skip:
            self.skip -= 1
    def handle_data(self, data: str) -> None:
        if not self.skip:
            self.parts.append(data)


def read_source(source: dict[str, Any], *, max_chars: int = 80_000, fetch: Callable[[str], bytes] | None = None) -> str:
    """Best-effort paper/page reading. PDF extraction uses pypdf when installed."""
    url = source.get("pdf") or source.get("url")
    if not url:
        return str(source.get("abstract", ""))[:max_chars]
    raw = (fetch or _get)(str(url))
    if raw[:4] == b"%PDF" or str(url).lower().endswith(".pdf"):
        try:
            from pypdf import PdfReader
            text = "\n".join((p.extract_text() or "") for p in PdfReader(BytesIO(raw)).pages)
            return text[:max_chars]
        except Exception:
            return str(source.get("abstract", ""))[:max_chars]
    parser = _HTMLText()
    try:
        parser.feed(raw.decode("utf-8", errors="ignore"))
        return " ".join(" ".join(parser.parts).split())[:max_chars]
    except Exception:
        return raw.decode("utf-8", errors="ignore")[:max_chars]


def remember_text(ledger: Ledger, source: Artifact, text: str) -> Artifact:
    """Persist a bounded source excerpt/full text as retrieval context, never evidence."""
    return ledger.put("source_text", {"text": text}, {"source": [source.id]}, by="search:reader")
