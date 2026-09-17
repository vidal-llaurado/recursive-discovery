# Changelog

All notable changes to this project are documented here. This project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## 1.0.0 — First public release

The first public release of Recursive Discovery, as described in the
[README](README.md) and [specification](docs/FORMALISM.md).

### Included

- SQLite artifact graph with content-addressed blob storage.
- Kernel-signed execution records with HMAC authentication and verification.
- Worker execution boundary, including container adapters.
- Model-owned research sessions with persistent branches and memos.
- Context compiler with authority labels and graph-proximity retrieval.
- Mathematical checker adapters (Python, SymPy, Lean, Z3, cvc5).
- Reproducible empirical laboratory with seeded runs and statistical summaries.
- Reusable instruments, including model-defined expression instruments.
- Decision/outcome history with lookup by state digest and action name.
- Sealed prospective evaluator with local vault and service adapter.
- Literature search and source reading (arXiv, OpenAlex, Crossref).
- Operational `recursive-discovery` CLI and Markdown reports.

### Scope

This release is the general research machinery. It does not bundle a model
provider, scientific demonstrations, or frozen benchmark results: model
credentials stay in your own bridge, and results depend on the checkers,
protocols, and data you supply.