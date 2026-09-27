# Telemetry design

Telemetry uses a separate, opt-in SQLite event store. Hooks record routing, context selection
and delivery, model calls, retrieval, execution and artifact metadata. The scientific ledger,
model requests and signed payloads contain no telemetry IDs.

`telemetry.py` accepts metadata from hooks without importing research classes. Context variables
carry trace parentage within one process. Selection and delivery are recorded separately so a
compiler candidate can be distinguished from material actually shown to the model.

Metadata capture is the default. Content capture requires an operator option and applies
best-effort redaction. Keep the capture outside agent-accessible mounts: it may contain information
beyond a mission's access restrictions. No automatic telemetry-to-research import is provided.

Recorder failures leave research results and exceptions intact, with health counters reporting
loss where possible. Synchronous writes add overhead. Missing captures and incomplete spans remain
gaps in the record. See [Telemetry](../OBSERVABILITY.md) for limits and retention.
