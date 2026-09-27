# Research telemetry

[Development](../DEVELOPMENT.md) · [Formalism](FORMALISM.md#observability-is-an-external-projection)

Telemetry records spans and selected research metadata in a separate SQLite database. Capture is disabled by default and stays outside the scientific ledger and model context.

## Enable recording

Install the package, then enable telemetry for a command:

```bash
python -m pip install -e '.[dev]'
recursive-discovery --telemetry-dir ../rd-observations run ./workspace \
  --mission MISSION_ID --model-cmd 'python model_bridge.py'
```

Use an existing mission ID and model bridge. The global telemetry flags precede the command.
Telemetry is **off by default**. The default capture mode stores IDs, relationships, measured
sizes, statuses, and timings, not source text, prompts, model answers, tool output, or exception
messages. To retain best-effort redacted content, explicitly enable content capture:

```bash
recursive-discovery --telemetry-dir ../rd-observations --telemetry-content \
  run ./workspace --mission MISSION_ID --model-cmd 'python model_bridge.py'
```

Content capture retains a redacted copy. Request lengths and source coordinates describe the delivered request. Keep the capture outside agent-accessible worker mounts.

## Python API

```python
from recursive_discovery.telemetry import observe
from recursive_discovery.runtime import Runtime

# project is an already opened RD project. Keep the sibling capture outside worker mounts.
with observe('../rd-observations', ledger=project.ledger, capture='metadata') as recording:
    result = Runtime(project.ledger, project.kernel, root=project.root).run(
        mission=mission_id, model=model,
    )
print(recording.written, recording.dropped, recording.failures)
```

Passing a ledger records an initial inventory. The CLI does this when opening a project with recording enabled. Post-write hooks capture subsequent insertions; earlier calls and uninstrumented writes are absent.

An in-process model adapter can report actual provider usage without modifying its response:

```python
from recursive_discovery.telemetry import usage

# Use numbers returned by your provider. Do not estimate tokens or prices from characters.
usage(input_tokens=provider_input_tokens, output_tokens=provider_output_tokens)
# A known charge may additionally use cost=amount, currency='USD'.
```

Usage is unknown until a provider reports it. Command-line model bridges do not inherit the Python hook across processes. Character counts, latency and call counts are still recorded; token usage and cost are not estimated.

## Recorded information

Events include spans, artifact metadata, context selection and delivery, routing, execution outcomes and recorder health. Inspect the SQLite capture with local database tools.

Context audits include at most 500 candidates and report truncation. Separate events record selection and final delivery, since packing may omit selected material.

Exposure records identify presented spans. Evidence metadata uses `verification=not_checked_by_observer`; the recorder does not open the signing key. Compiler authority labels are captured observations.

## Storage, correlation, and history

`events.sqlite3` has an append-only event table and a small mutable recorder-health table. Event
sequence is the observation order in that database. `timestamp_ns` is wall time; duration is measured
with a monotonic clock. An artifact's own creation time is recorded separately. Never interpret an
inventory event's sequence as the artifact's original creation sequence.

Trace IDs use 32 hexadecimal characters; span IDs use 16. Context variables carry parentage within a process. Existing mission, revision, branch, target, session and decision IDs correlate events. Propagate context explicitly to new threads. Worker processes are observed through their outer spans and returned records.

Dropped events and uninstrumented operations leave gaps. Initial inventory records state at capture time.

## Failure behavior and retention

Recording failures preserve the research result or exception. They warn once and increment health counters, including when warnings are configured as errors. Writes are synchronous and wait up to 0.1 seconds for a busy database.

Defaults per recorder activation are 100,000 events, 128,000,000 accounted bytes, and 1,000,000 bytes
per content payload. `observe` accepts `max_events`, `max_bytes`, and `max_payload_bytes`. Oversized
content is omitted in full and marked `omitted_payload_limit`. Metadata above 256,000 encoded bytes
or an exhausted event/byte allowance drops that event. Approximate byte accounting is not a disk-size
quota: SQLite/WAL/index overhead is additional. Drops appear in health. A full disk can prevent health
from being persisted too; retain stderr and inspect the in-process counters.

Retention and rotation are manual. Use separate capture directories where needed and archive or delete a capture after closing the recorder. Deleting individual rows can break trace relationships. A process killed before cleanup leaves incomplete spans and an open health record.

## Content handling

The capture directory is owner-only and the database owner-readable/writable. Existing database
symlinks are refused where the OS supports `O_NOFOLLOW`. Redaction masks known credential keys,
HMAC tags, environment dictionaries, common token patterns and credentials in URLs; nested JSON
strings are handled as well. It cannot identify every secret in scientific prose, source code,
images, arbitrary relationship names or model output. Metadata IDs and relations can themselves be
sensitive. Content capture requires a trusted operator and an appropriate retention policy.

Tests exercise real JSONL and SQLite ledger writes, signed local subprocess execution, unchanged
model requests, redaction, limits, failure isolation, context correlation and routing.
