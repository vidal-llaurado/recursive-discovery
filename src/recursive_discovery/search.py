"""Literature and index retrieval (arXiv, OpenAlex, Crossref) and source reading."""
from __future__ import annotations
from .telemetry import emit, span, traced

from collections.abc import Callable, Iterable
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
import json
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any

from .core import Artifact, Ledger
from .reading import available, published_before
from .store import BlobStore

ATOM = "http://www.w3.org/2005/Atom"
UA = "recursive-discovery/1.0 research client"


def _get(url: str, *, timeout: float = 20.0, max_bytes: int = 16_000_000) -> bytes:
    if urllib.parse.urlsplit(url).scheme not in {"http", "https"}:
        raise ValueError("source URL must use HTTP or HTTPS")
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read(max_bytes + 1)
        if len(raw) > max_bytes:
            raise ValueError("source exceeds download byte limit")
        return raw


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
            "version_date": _text(entry, "a:updated"),
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


def _source_key(row: dict[str, Any]) -> str:
    doi = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "",
                 str(row.get("doi") or "").strip().lower())
    if doi:
        return "doi:" + doi
    title = _norm_title(str(row.get("title", "")))
    authors = row.get("authors") or []
    if isinstance(authors, str):
        authors = [authors]
    # Without a shared identifier, avoid merging same-titled work by different authors/years.
    return ("title:" + title + ":" + str(row.get("published") or "")[:4] + ":"
            + ";".join(_norm_title(str(a)) for a in authors)) if title else ""


def dedupe(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    out, seen = [], set()
    for row in rows:
        key = _source_key(row)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def before(rows: list[dict[str, Any]], cutoff: str | None) -> list[dict[str, Any]]:
    published_before({}, cutoff)
    return [r for r in rows if published_before(r, cutoff)]


@traced("search.online")
def multi_search(
    query: str,
    *,
    max_results: int = 12,
    providers: tuple[str, ...] = ("arxiv", "openalex", "crossref"),
    source_before: str | None = None,
    diagnostics: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Combine provider rankings with reciprocal rank fusion.

    Each provider contributes at most once per document: score = sum(1 / (60 + rank)).
    Diagnostics record failures, empty results and date filtering. Ties are deterministic.
    """
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must not be blank")
    if type(max_results) is not int or not 1 <= max_results <= 100:
        raise ValueError("max_results must be in [1, 100]")
    published_before({}, source_before)  # Validate the cutoff outside provider error handling.
    funcs = {"arxiv": arxiv, "openalex": openalex, "crossref": crossref}
    if any(name not in funcs for name in providers):
        raise ValueError("unknown search provider")
    scores, documents = {}, {}

    def quality(item):
        return (bool(item.get("pdf")), len(str(item.get("abstract") or "")),
                json.dumps(item, sort_keys=True, ensure_ascii=False))

    for name in dict.fromkeys(providers):
        try:
            with span("search.provider", provider=name):
                raw = funcs[name](query, max_results=max(3, max_results))
            rows = dedupe(before(raw, source_before))
            diagnostic = {"provider": name, "status": "ok" if rows else "filtered" if raw else "empty",
                          "received": len(raw), "eligible": len(rows)}
            for rank, row in enumerate(rows, 1):
                key = _source_key(row)
                scores[key] = scores.get(key, 0.0) + 1.0 / (60 + rank)
                # Stable representative selection, independent of provider call order.
                if key not in documents or quality(row) > quality(documents[key]):
                    documents[key] = row
        except Exception as error:
            diagnostic = {"provider": name, "status": "error",
                          "error": f"{type(error).__name__}: {error}"[:500]}
        emit("search.provider_result", {k: v for k, v in diagnostic.items() if k != "error"},
             payload=lambda: {"query": query, "diagnostic": diagnostic})
        if diagnostics is not None:
            diagnostics.append(diagnostic)
    return [documents[k] for k in sorted(scores, key=lambda k: (-scores[k], k))[:max_results]]


def remember(
    ledger: Ledger,
    query: str,
    rows: list[dict[str, Any]],
    *,
    refs: dict[str, list[str]] | None = None,
    provider: str = "multi",
    diagnostics: list[dict[str, Any]] | None = None,
) -> tuple[Artifact, list[Artifact]]:
    # Source snapshots do not change identity merely because another query found them.
    sources = [ledger.put("source", row, by=f"search:{row.get('provider', provider)}") for row in rows]
    links = dict(refs or {})
    links["sources"] = [s.id for s in sources]
    data = {"provider": provider, "query": query, "n": len(rows)}
    if diagnostics is not None:
        data["providers"] = diagnostics
    q = ledger.put("search", data, links, by=f"search:{provider}")
    return q, sources


class _HTMLText(HTMLParser):
    """Small text reader preserving headings and block boundaries; raw bytes are retained."""
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "nav", "footer"}:
            self.skip += 1
        if self.skip:
            return
        if re.fullmatch(r"h[1-6]", tag):
            self.parts.append("\n" + "#" * int(tag[1]) + " ")
        elif tag in {"p", "div", "section", "article", "li", "br", "tr", "pre"}:
            self.parts.append("\n")
        elif tag in {"td", "th"}:
            self.parts.append("\t")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "nav", "footer"} and self.skip:
            self.skip -= 1
        elif not self.skip and (re.fullmatch(r"h[1-6]", tag) or tag in {"p", "div", "li", "tr", "pre"}):
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.skip:
            self.parts.append(data)


def _extract(raw: bytes, url: str) -> dict[str, Any]:
    if raw[:4] == b"%PDF" or urllib.parse.urlsplit(url).path.lower().endswith(".pdf"):
        from pypdf import PdfReader
        pieces, pages, offset = [], [], 0
        for number, page in enumerate(PdfReader(BytesIO(raw)).pages, 1):
            text = page.extract_text() or ""
            pages.append({"page": number, "start": offset, "end": offset + len(text)})
            pieces.append(text)
            offset += len(text) + 1
        return {"text": "\n".join(pieces), "format": "pdf", "pages": pages,
                "warnings": ["PDF text extraction can lose layout, equations, tables and figures."]}
    text = raw.decode("utf-8-sig", errors="replace")
    warnings = ["Invalid UTF-8 was replaced; original bytes are retained."] if "\ufffd" in text else []
    if re.search(r"<(?:!doctype|html|head|body|p|div|h[1-6])(?:\s|>)", text, re.IGNORECASE):
        parser = _HTMLText()
        parser.feed(text)
        parser.close()
        text = re.sub(r"\n{3,}", "\n\n", "".join(parser.parts)).strip()
        return {"text": text, "format": "html", "warnings": warnings}
    return {"text": text, "format": "text", "warnings": warnings}


@traced("source.acquire")
def acquire_source(
    ledger: Ledger, source: Artifact, *, root: str | Path,
    source_before: str | None = None, refresh: bool = False,
    fetch: Callable[[str], bytes] | None = None,
) -> Artifact:
    """Retain downloaded bytes and a versioned text extract.

    Cached reads reuse an immutable artifact. Refresh may create a new version.
    Extraction errors include the retained raw digest.
    """
    if source.kind != "source":
        raise ValueError("source artifact required")
    if not available(ledger, source, source_before):
        raise PermissionError("source excluded by source_before")
    existing = [a for a in ledger.children(source.id, "source_text", "source")
                if available(ledger, a, source_before)]
    if existing and not refresh:
        cached = max(existing, key=lambda a: (a.t, a.id))
        emit("source.cache", {"source_id": source.id, "artifact_id": cached.id, "hit": True})
        return cached
    emit("source.cache", {"source_id": source.id, "hit": False, "refresh": refresh})
    url = source.data.get("pdf") or source.data.get("url")
    if not url:
        text = str(source.data.get("abstract") or "")
        if not text.strip():
            raise ValueError("source has neither a readable URL nor an abstract")
        data = {"text": text, "format": "text", "scope": "abstract_only",
                "warnings": ["No full-text URL was supplied; this is only the abstract."]}
    else:
        if urllib.parse.urlsplit(str(url)).scheme not in {"http", "https"}:
            raise ValueError("source URL must use HTTP or HTTPS")
        with span("source.download", source_id=source.id):
            raw = (fetch or _get)(str(url))
            emit("source.bytes", {"source_id": source.id, "bytes": len(raw)})
        if len(raw) > 16_000_000:
            raise ValueError("source exceeds download byte limit")
        digest = BlobStore(Path(root) / "blobs").put_bytes(raw)
        try:
            data = _extract(raw, str(url))
            if not data["text"].strip():
                raise ValueError("no extractable text")
        except Exception as error:
            raise ValueError(f"source extraction failed; retained raw_sha256={digest}: {error}") from error
        data.update(raw_sha256=digest, url=str(url), scope="extracted_text")
    data["reader"] = "source-reader-v1"
    return ledger.put("source_text", data, {"source": [source.id]}, by="search:reader")


def read_source(source: dict[str, Any], *, max_chars: int = 80_000, fetch: Callable[[str], bytes] | None = None) -> str:
    """Return source text as a string.

    Use acquire_source for retained bytes and extraction metadata. A missing URL returns
    the abstract; network and extraction errors propagate.
    """
    if max_chars < 1:
        raise ValueError("max_chars must be positive")
    url = source.get("pdf") or source.get("url")
    if not url:
        return str(source.get("abstract", ""))[:max_chars]
    return _extract((fetch or _get)(str(url)), str(url))["text"][:max_chars]


def remember_text(ledger: Ledger, source: Artifact, text: str) -> Artifact:
    """Persist a bounded source excerpt/full text as retrieval context, never evidence."""
    return ledger.put("source_text", {"text": text}, {"source": [source.id]}, by="search:reader")
