# Source reading

Sessions can search, inspect outlines, read passages, follow continuations and cite earlier reads. Source bytes and text versions are retained in the ledger and blob store.

## Search, then choose what to read

Use `read` for source navigation. Local search returns addressable previews; `inspect` accepts original string-field spans. Oversized responses return readable handles.

```json
{"action":"literature_search","query":"stability under dependent observations","limit":6}
{"action":"read","id":"SOURCE_ID","view":"outline"}
{"action":"read","id":"SOURCE_TEXT_ID","query":"independence","length":2000}
{"action":"read","id":"SOURCE_TEXT_ID","start":12000,"length":2000}
```

Replace the IDs and offsets with actual returned values. Reading a bibliography `source`
acquires or reuses a `source_text` artifact. Subsequent reads should address that returned text
ID to stay on the same immutable extraction. `refresh: true` on a source explicitly fetches
another version; previous text artifacts and original response bytes remain available.

A passage returns `id`, `start`, `end`, `text`, `total_chars`, and `next`. Offsets are zero-based,
half-open Python character offsets in the extracted text, **not** UTF-8 byte offsets, PDF
coordinates, or offsets in the original HTML. Literal, case-insensitive `query` search begins
at `start`; `search_next` continues finding matches, while `next` continues sequential reading.
A miss is labelled `no_match`, not replaced by an unrelated document prefix.

Outlines contain paginated heading or page addresses. Markdown ATX headings outside fenced
code and HTML-extracted headings are recognized. PDFs expose page boundaries when the
optional `pypdf` package is installed. No outline is required for literal search or range reads.
For other artifacts, `read` addresses a stable JSON rendering of kind, data and references.
This also makes a deferred search artifact navigable through its source references.

## Original material and extraction fidelity

`acquire_source` retains fetched bytes in the existing `BlobStore` and records their SHA-256,
the reader version, extraction format, and scope on the text artifact. A source without a URL
may yield `abstract_only`; older `remember_text` artifacts are labelled `legacy_excerpt` in
views. Failed downloads and failed extraction are reported as failures, never quietly converted
into successful full-text reads. A failed extraction reports the retained raw digest.

HTML extraction retains block boundaries and headings. PDF extraction may lose equations, figures, tables and reading order, and does not perform OCR. Downloads are limited to 16 MB; parser resource limits and network isolation require separate deployment controls.

The legacy `read_source(...) -> str` API remains available, but does not provide the retained
bytes and fidelity metadata of `acquire_source`. It now raises extraction errors rather than
silently substituting an abstract.

## Retrieval and identity

`multi_search` combines provider rankings using reciprocal rank fusion: `score(document) = sum(1 / (60 + provider_rank))`. Each provider contributes once per document. Ties are deterministic. Deduplication uses normalized DOIs, then title, year and author metadata.

Pass a `diagnostics` list to capture provider errors, empty results and date-filtered responses. Sessions record these automatically. Literature search defaults to no automatic reading; use `read: N` to acquire results immediately.

New `remember` records separate source snapshots from discovery queries: the search artifact
references its `sources`. Repeating a query with unchanged source metadata does not create a
new source identity. Clients that followed the old source-to-search reference should use
`search.refs["sources"]` for new records. Old graph records remain readable without migration.

Local search preserves backend ranking. Lexical previews start near query terms; use passage search to inspect other matches.

## Context and citations

`context_chars` in `research_session` now bounds the complete serialized JSON request, including
instructions, action descriptions, instrument descriptions and recent observations. It is a
character limit, not an exact model-token limit. Provider wrappers may add their own overhead.
The context compiler's optional token estimate also remains an approximation.

The target and latest observation take precedence over optional history. Oversized observations
become explicit `deferred` handles that can be read with a smaller range. Their source data is
not truncated in storage. An impossible mandatory request raises an error instead of sending
an over-budget prompt. Decision records identify the views actually delivered, including
passage offsets; an available handle or a search preview is not recorded as a full-document read.

Finalized candidates may include `citations`, each with `id`, `field`, `start`, and `end`.
`field` defaults to `text`; generic JSON-rendered artifact passages explicitly use
`$artifact_json`, which must not be confused with original string-field coordinates. Each range must
be contained in passage text actually delivered in this session, including earlier turns whose
context has since been evicted. Validated spans and their references survive ordinary candidate
activation. This checks provenance and exposure, **not whether the passage supports the claim**.
The model still supplies the scientific interpretation.

`activate: []` preserves proposals without opening a branch. Invalid activation indices produce
an error observation rather than an implicit commitment. Omitting `activate` retains the existing
first-candidate default. Malformed model JSON and recoverable tool failures are reported back to
the session so it can revise its action.

## Date-restricted studies

`source_before` applies consistently to context, local search, inspection, reading, and declared
source-dependent instrument access. Publication dates that are missing, partial or invalid are
excluded under a cutoff. Explicit `version_date` must also qualify; arXiv revision dates populate
this field. Provider metadata-maintenance timestamps are not treated as publication revisions.
The filter follows declared references and rejects missing provenance under a cutoff.

This is a metadata/provenance filter, **not a point-in-time research guarantee**. Unrecorded
source revisions, undeclared dependencies, live external tools and a model's pretrained knowledge
can still carry later information. Date-sensitive empirical evaluation needs an independently
controlled data and execution boundary.

## Validation

```bash
pytest -q tests/test_reading.py tests/test_session_reading.py
pytest -q
```

The focused tests cover navigation, Unicode offsets, retained bytes, refresh, ranking, date restrictions, request budgets, error recovery, activation and citations after context eviction. PDF tests require the optional reader.

Missions and capsules share this reader and compiler. See [Workflows](WORKFLOWS.md) for CLI operations.

Mission `required` IDs, selected spans and their descriptor dependencies must fit in full. Otherwise the compiler raises `ContextOverflow`. Other local targets can use previews with reading handles.
