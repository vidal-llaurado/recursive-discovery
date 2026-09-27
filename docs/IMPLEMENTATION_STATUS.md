# Implementation status

This page describes version 1.1.0. See [Workflows](WORKFLOWS.md) for usage and
[Formalism](FORMALISM.md) for the underlying rules.

## Supported features

| Feature | Implementation | Tests |
|---|---|---|
| Programs, mission revisions, branch membership and attention | `investigation.py` | `test_missions.py`, `test_mission_runtime.py` |
| Shared agenda for requests, frontier obligations and opportunities | `agenda.py`, `runtime.py` | `test_mission_runtime.py`, `test_native_integration.py` |
| Source retention, rank fusion, passage reading and date restrictions | `search.py`, `reading.py` | `test_reading.py`, `test_session_reading.py` |
| Bounded requests, required context and passage citations | `context.py`, `session.py` | `test_formal_contracts.py`, `test_session_reading.py` |
| Frozen capsules and reviewed admission of external proposals | `capsules.py`, `propose.py` | `test_capsule_flow.py`, `test_native_integration.py` |
| Versioned descriptors and tool input contracts | `schema.py` | `test_schema_guidance.py` |
| Declared checks, authenticated execution and researcher interpretation | `protocols.py`, `core.py` | `test_native_integration.py` |
| Attempt identity and execution history | `replay.py`, `runtime.py`, `instruments.py` | `test_native_integration.py` |
| Shared session action loop | `actions.py`, `session.py` | `test_action_lifecycle.py` |
| Mission, reading and capsule CLI operations | `cli.py` | `test_native_integration.py` |
| Optional telemetry recording, usage reporting and redaction | `telemetry.py` and instrumentation hooks | `test_observability.py` |

Missions and branches share the existing ledger. Dormancy is mission-local unless a program
records its own decision. Required context must fit in full or as selected spans; otherwise
compilation raises `ContextOverflow`. Capsule responses enter as proposals and require review
before activation.

## Current limits

- **Scale:** scope, frontier and attempt-history queries scan ledger state. Pagination bounds
  prompts, but does not reduce this database work.
- **Concurrency:** causal heads detect conflicting updates. Writers must still be serialized;
  multi-record workflows are not atomic, and JSONL readers need reopening after external writes.
- **Capsule freshness:** any non-capsule addition changes the snapshot, including unrelated
  additions. Review can therefore be required more often than strictly necessary.
- **External responses:** structured JSON is normalized. Free-form or malformed replies are
  retained for review, without automatic prose conversion. New sources remain unverified.
- **Schemas:** descriptors validate structure and supply context dependencies. Executable
  validators and automatic schema evolution are not supported.
- **Checks:** `claim.data.check_policy="explicit"` selects protocol-based routing. Other claims
  keep the mathematical-check path. Researchers interpret the recorded execution results.
- **Attempt identity:** signatures use declared inputs and file digests. They permit replication
  and do not provide an atomic input snapshot.
- **Date restrictions:** filters check declared metadata and dependencies. Historical data quality,
  model pretraining and undeclared filesystem/network inputs require separate controls.
- **Extraction:** HTML/Markdown headings and PDF page text are supported. OCR, equation recovery
  and layout reconstruction are not.
- **Telemetry:** recording is synchronous and bounded. Collector integration, cross-process
  tracing, automatic command-bridge usage reporting and retention/rotation are not implemented.

Optional solver/container integrations and live model quality require separate evaluation.
Deployment isolation is described in [Security](../SECURITY.md).
