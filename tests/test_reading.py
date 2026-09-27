"""Deterministic regressions for retrieval/reading mechanics, not a model benchmark."""
import hashlib
import json

import pytest

from recursive_discovery.core import Kernel, Ledger, Task
from recursive_discovery.context import authority, compile_context, fit_request, render_context
from recursive_discovery.reading import (
    artifact_text, available, excerpt, outline, passage, published_before, search_local,
)
from recursive_discovery.search import acquire_source, before, dedupe, multi_search, remember, remember_text
from recursive_discovery.store import BlobStore, SQLiteLedger
import recursive_discovery.search as search


@pytest.fixture(params=["jsonl", "sqlite"])
def workspace(tmp_path, request):
    ledger = Ledger(tmp_path / "ledger.jsonl") if request.param == "jsonl" else SQLiteLedger(tmp_path / "ledger.db")
    yield ledger, Kernel(tmp_path / "key")
    if hasattr(ledger, "close"):
        ledger.close()


def source(ledger, *, published="2020-01-01", **data):
    return ledger.put("source", {"title": "An unrelated document", "published": published, **data})


@pytest.mark.parametrize("size", [17, 4000, 12000])
def test_ranges_round_trip_long_lines_unicode_and_quotes(workspace, size):
    ledger, _ = workspace
    text = 'αß漢字 "quote"\\' * 1800 + "\nlast line"
    doc = remember_text(ledger, source(ledger), text)
    pieces, start = [], 0
    while start is not None:
        row = passage(doc, start=start, length=size)
        assert row["text"] == text[row["start"]:row["end"]]
        assert row["id"] == doc.id
        assert row["end"] > row["start"]
        pieces.append(row["text"])
        start = row["next"]
    assert "".join(pieces) == text


def test_literal_find_continuation_and_no_match(workspace):
    ledger, _ = workspace
    text = "padding " * 2000 + "Risk[0] only under independence. " + "padding " * 2000 + "risk[0] fails otherwise."
    doc = remember_text(ledger, source(ledger), text)
    first = passage(doc, query="risk[0]", length=120)
    second = passage(doc, query="risk[0]", start=first["search_next"], length=120)
    assert first["match_start"] == text.index("Risk[0]")
    assert second["match_start"] == text.index("risk[0]")
    assert "independence" in first["text"]
    assert "fails otherwise" in second["text"]
    assert passage(doc, query="not in this document")["status"] == "no_match"
    assert "text" not in passage(doc, query="not in this document")


@pytest.mark.parametrize("kwargs", [{"start": -1}, {"start": True}, {"length": 0}, {"length": 12001}, {"query": ""}])
def test_bad_ranges_are_explicit(workspace, kwargs):
    ledger, _ = workspace
    doc = remember_text(ledger, source(ledger), "text")
    with pytest.raises(ValueError):
        passage(doc, **kwargs)


def test_outline_addresses_are_real_and_code_fences_are_not_headings(workspace):
    ledger, _ = workspace
    text = "# Main\nIntroduction\n```python\n# not a section\n```\n## Conditions\nOnly if stable.\n## Counterexample\nUnstable."
    doc = remember_text(ledger, source(ledger), text)
    first = outline(doc, limit=2)
    second = outline(doc, start=first["next"], limit=2)
    assert [x["title"] for x in first["entries"]] == ["Main", "Conditions"]
    assert [x["title"] for x in second["entries"]] == ["Counterexample"]
    assert second["next"] is None
    for entry in first["entries"] + second["entries"]:
        assert entry["title"] in passage(doc, start=entry["start"], length=80)["text"]


def test_original_bytes_cached_reads_and_explicit_refresh(workspace, tmp_path):
    ledger, kernel = workspace
    s = source(ledger, url="https://example.test/document")
    versions = [b"<html><h1>Claim</h1><p>First version.</p></html>",
                b"<html><h1>Claim</h1><p>Revised conditions.</p></html>"]
    calls = []

    def fetch(url):
        calls.append(url)
        return versions[len(calls) - 1]

    old = acquire_source(ledger, s, root=tmp_path, fetch=fetch)
    cached = acquire_source(ledger, s, root=tmp_path, fetch=fetch)
    new = acquire_source(ledger, s, root=tmp_path, fetch=fetch, refresh=True)
    assert old.id == cached.id != new.id
    assert len(calls) == 2
    assert "First version" in ledger.get(old.id).data["text"]
    assert "Revised conditions" in new.data["text"]
    for doc, raw in zip([old, new], versions):
        digest = hashlib.sha256(raw).hexdigest()
        assert doc.data["raw_sha256"] == digest
        assert BlobStore(tmp_path / "blobs").path(digest).read_bytes() == raw
        assert authority(doc, ledger, kernel) == "retrieved_source"


def test_html_preserves_navigation_and_does_not_emit_scripts(workspace, tmp_path):
    ledger, _ = workspace
    raw = b"<html><h1>Thesis</h1><p>Supported <em>conditionally</em>.</p><script>HIDDEN_SCRIPT</script><h2>Limitations</h2><p>Fails in this regime.</p></html>"
    doc = acquire_source(ledger, source(ledger, url="https://example.test"), root=tmp_path, fetch=lambda _: raw)
    assert "HIDDEN_SCRIPT" not in doc.data["text"]
    assert [x["title"] for x in outline(doc)["entries"]] == ["Thesis", "Limitations"]
    assert "Fails in this regime" in passage(doc, query="Limitations")["text"]


def test_abstract_only_is_labelled_and_failed_pdf_is_not_an_abstract(workspace, tmp_path):
    ledger, _ = workspace
    doc = acquire_source(ledger, source(ledger, abstract="Only an abstract"), root=tmp_path)
    assert passage(doc)["scope"] == "abstract_only"
    assert doc.data["warnings"]
    s = source(ledger, url="https://example.test/broken.pdf", abstract="Must not substitute this")
    with pytest.raises(ValueError, match="retained raw_sha256"):
        acquire_source(ledger, s, root=tmp_path, fetch=lambda _: b"%PDF invalid")
    assert not ledger.children(s.id, "source_text", "source")


def test_network_failure_is_not_a_successful_read(workspace, tmp_path):
    ledger, _ = workspace
    s = source(ledger, url="https://example.test", abstract="Not full text")

    def fail(_):
        raise TimeoutError("offline")

    with pytest.raises(TimeoutError):
        acquire_source(ledger, s, root=tmp_path, fetch=fail)
    assert not ledger.children(s.id, "source_text", "source")


def test_search_fusion_does_not_let_first_provider_fill_every_slot(monkeypatch):
    for name, prefix in [("arxiv", "a"), ("openalex", "b"), ("crossref", "c")]:
        rows = [{"title": f"{prefix}{i}", "published": "2020-01-01"} for i in range(5)]
        monkeypatch.setattr(search, name, lambda *args, rows=rows, **kwargs: rows)
    rows = multi_search("query", max_results=3)
    assert {r["title"] for r in rows} == {"a0", "b0", "c0"}
    assert rows == multi_search("query", max_results=3, providers=("crossref", "arxiv", "openalex"))


def test_rank_fusion_normalizes_dois_and_counts_each_provider_once(monkeypatch):
    common = {"title": "Shared", "doi": "10.123/xyz"}
    monkeypatch.setattr(search, "arxiv", lambda *a, **k: [common, common, {"title": "A"}])
    monkeypatch.setattr(search, "openalex", lambda *a, **k: [{"title": "Shared", "doi": "https://doi.org/10.123/XYZ"}, {"title": "B"}])
    rows = multi_search("q", providers=("arxiv", "openalex", "arxiv"))
    assert len(rows) == 3 and rows[0]["title"] == "Shared"
    assert len(dedupe([common, {**common, "doi": "DOI:10.123/XYZ"}])) == 1


def test_search_reports_failure_empty_and_filtered(monkeypatch):
    def fail(*args, **kwargs):
        raise TimeoutError("provider offline")

    monkeypatch.setattr(search, "arxiv", fail)
    monkeypatch.setattr(search, "openalex", lambda *a, **k: [])
    monkeypatch.setattr(search, "crossref", lambda *a, **k: [{"title": "Future", "published": "2030-01-01"}])
    diagnostics = []
    assert multi_search("q", source_before="2020-01-01", diagnostics=diagnostics) == []
    assert [r["status"] for r in diagnostics] == ["error", "empty", "filtered"]
    assert "TimeoutError" in diagnostics[0]["error"]


def test_source_identity_is_independent_of_discovery_query(workspace):
    ledger, _ = workspace
    rows = [{"title": "Same source", "provider": "fixture", "published": "2020-01-01"}]
    first, a = remember(ledger, "first question", rows)
    second, b = remember(ledger, "second question", rows)
    assert a[0].id == b[0].id and first.id != second.id
    assert first.refs["sources"] == second.refs["sources"] == (a[0].id,)
    assert a[0].id in artifact_text(first)  # Deferred search handles remain navigable.


@pytest.mark.parametrize("published", [None, "", "2020", "invalid", "2030-01-01"])
def test_cutoff_rejects_unknown_partial_and_future_dates(published):
    assert not published_before({"published": published}, "2025-01-01")
    assert published_before({"published": published}, None)
    assert before([{"published": published}], "2025-01-01") == []


def test_explicit_revision_date_also_obeys_cutoff():
    assert not published_before({"published": "2010-01-01", "version_date": "2030-01-01"}, "2025-01-01")
    assert published_before({"published": "2010-01-01", "updated": "2030-01-01"}, "2025-01-01")
    with pytest.raises(ValueError):
        published_before({}, "bad cutoff")


def test_cutoff_follows_document_and_derived_dependencies(workspace):
    ledger, kernel = workspace
    study = ledger.put("study", {"question": "What are the conditions?"})
    s = source(ledger, published="2030-01-01", abstract="FORBIDDEN_PAYLOAD")
    doc = remember_text(ledger, s, "FORBIDDEN_PAYLOAD")
    memo = ledger.put("memo", {"text": "FORBIDDEN_PAYLOAD"}, {"basis": [doc.id], "target": [study.id]})
    for a in (s, doc, memo):
        assert not available(ledger, a, "2025-01-01")
    packet = compile_context(ledger, kernel, Task("inspect", study.id, "conditions"), source_before="2025-01-01")
    assert "FORBIDDEN_PAYLOAD" not in render_context(packet)
    assert not search_local(ledger, "FORBIDDEN_PAYLOAD", cutoff="2025-01-01")


def test_cutoff_missing_provenance_fails_closed(workspace):
    ledger, _ = workspace
    orphan = ledger.put("source_text", {"text": "unattributed"})
    dangling = ledger.put("memo", {"text": "derived"}, {"source": ["missing"]})
    assert not available(ledger, orphan, "2025-01-01")
    assert not available(ledger, dangling, "2025-01-01")


def test_filtering_does_not_consume_eligible_search_slots(tmp_path):
    with SQLiteLedger(tmp_path / "l.db") as ledger:
        old = source(ledger, title="needle")
        for i in range(8):
            source(ledger, title=f"needle {i}", published="2030-01-01")
        rows = search_local(ledger, "needle", limit=1, cutoff="2025-01-01")
        assert [a.id for a in rows] == [old.id]


def test_lexical_preview_reaches_material_beyond_the_prefix(workspace):
    ledger, kernel = workspace
    study = ledger.put("study", {"question": "Which stability precondition is missing?"})
    text = "Introductory boilerplate. " * 3000 + "\n## Stability precondition\nA spectral radius below one is required."
    doc = remember_text(ledger, source(ledger), text)
    row = excerpt(doc, "stability precondition")
    assert row["start"] > 2500 and "spectral radius" in row["text"]
    packet = compile_context(ledger, kernel, Task("inspect", study.id, "stability precondition"), radius=0)
    matching = next(a for a in packet["artifacts"] if a["id"] == doc.id)
    assert "spectral radius" in matching["data"]["text"]


def test_context_keeps_backend_rank_not_just_hit_membership(tmp_path):
    with SQLiteLedger(tmp_path / "l.db") as ledger:
        kernel = Kernel(tmp_path / "key")
        target = ledger.put("study", {"question": "zephyr"})
        a = ledger.put("note", {"text": "zephyr " * 20})
        b = ledger.put("note", {"text": "zephyr and many unrelated words " * 4})
        ranked = [x.id for x in ledger.search("zephyr") if x.id in {a.id, b.id}]
        packet = compile_context(ledger, kernel, Task("inspect", target.id, "zephyr"), radius=0)
        actual = [x["id"] for x in packet["artifacts"] if x["id"] in {a.id, b.id}]
        assert actual == ranked


@pytest.mark.parametrize("budget", [1000, 1100, 1800, 5000, 9000])
def test_final_packet_including_metadata_obeys_budget(workspace, budget):
    ledger, kernel = workspace
    target = ledger.put("study", {"question": "q", "large": "x" * 100_000})
    for i in range(20):
        ledger.put("note", {"text": "q " * 500, "i": i}, {"target": [target.id]})
    packet = compile_context(ledger, kernel, Task("inspect", target.id, "q"), max_chars=budget)
    assert packet["used_chars"] == len(render_context(packet)) <= budget
    assert packet["approx_tokens"] == (packet["used_chars"] + 3) // 4
    assert target.id in [x["id"] for x in packet["artifacts"]]


def test_full_request_counts_tools_observations_and_json_escaping(workspace):
    ledger, kernel = workspace
    target = ledger.put("study", {"question": "q"})
    doc = remember_text(ledger, source(ledger), '"\\\n' * 4000)
    packet = compile_context(ledger, kernel, Task("read", target.id, "q"))
    prompt = {"actions": {"read": {"id": "id"}}, "instruction": "inspect",
              "instruments": [{"name": "huge", "description": "x" * 20_000}],
              "recent_observations": [{"action": "read", "passage": passage(doc, length=12000)}]}
    encoded = fit_request(prompt, packet, 1800)
    assert len(encoded) <= 1800
    assert prompt["recent_observations"][0]["status"] == "deferred"
    assert doc.id in prompt["recent_observations"][0]["ids"]
    assert prompt["omitted"]["instruments"] == 1
    assert ledger.get(doc.id).data["text"] == '"\\\n' * 4000


def test_representative_selection_is_independent_of_provider_order(monkeypatch):
    first = {"title": "Same", "doi": "10.1/same", "provider": "arxiv", "abstract": "Short"}
    second = {"title": "Same", "doi": "10.1/same", "provider": "openalex", "abstract": "Longer abstract"}
    monkeypatch.setattr(search, "arxiv", lambda *a, **k: [first])
    monkeypatch.setattr(search, "openalex", lambda *a, **k: [second])
    a = multi_search("query", providers=("arxiv", "openalex"))
    b = multi_search("query", providers=("openalex", "arxiv"))
    assert a == b == [second]


def test_same_title_different_authors_are_not_silently_merged():
    rows = [{"title": "Stability", "authors": [name], "published": "2020-01-01"}
            for name in ("A. Author", "B. Author")]
    assert len(dedupe(rows)) == 2


def test_pdf_page_addresses_and_retained_bytes_when_backend_is_installed(workspace, tmp_path):
    pypdf = pytest.importorskip("pypdf")
    from io import BytesIO
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject

    writer = pypdf.PdfWriter()
    for text in ("First page: claim.", "Second page: restrictive condition."):
        page = writer.add_blank_page(width=300, height=200)
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                                 NameObject("/Subtype"): NameObject("/Type1"),
                                 NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 20 100 Td ({text}) Tj ET".encode())
        page[NameObject("/Contents")] = writer._add_object(stream)
    buf = BytesIO()
    writer.write(buf)
    raw = buf.getvalue()
    ledger, _ = workspace
    doc = acquire_source(ledger, source(ledger, url="https://example.test/test.pdf"), root=tmp_path, fetch=lambda _: raw)
    pages = outline(doc)["entries"]
    assert [p["title"] for p in pages] == ["Page 1", "Page 2"]
    row = passage(doc, start=pages[1]["start"], length=100)
    assert row["pages"] == [2] and "restrictive condition" in row["text"]
    assert BlobStore(tmp_path / "blobs").path(doc.data["raw_sha256"]).read_bytes() == raw
