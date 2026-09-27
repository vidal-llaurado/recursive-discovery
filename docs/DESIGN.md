# Design

[README](../README.md) · [Formalism](FORMALISM.md) · [Usage](USAGE.md)

## Responsibilities

**Investigation** chooses what to ask, read, derive, implement, and compare. It belongs to the model and its research policy.

**Execution** runs a declared tool and retains an authenticated account of what happened. It belongs to the kernel and configured workers.

**Interpretation** relates a result to assumptions, predictions, and alternative explanations. It belongs to scientific analysis; neither a process exit code nor an HMAC performs that work.

## The artifact graph is the shared workspace

Claims, sources, results, failed attempts, memos, instruments, and decisions should survive individual model invocations. They use one artifact/reference representation rather than separate memory services for every kind of object.

SQLite provides operational persistence and lexical retrieval. Content-addressed storage retains declared input files. Both sit behind the same small ledger interface.

Append-only application writes support lineage. A local database remains open to direct modification by a process with filesystem access, so protection of the ledger depends on the deployment boundary rather than on the storage format.

## Mission scope

Missions, branches, attention events and supersession records use the existing artifact model.
`Scope` derives a mission view; it never hides records from the ledger used by evidence checks.
Membership is explicit rather than inferred from arbitrary reachability. Mission-local dormancy
is the default, program suppression is explicit, and another active owner can retain shared work.
Access restrictions, attention state and evidence authority are separate decisions.

Causal heads expose stale updates and conflicting histories instead of using timestamp order.
Their checks require serialized writers; they do not provide distributed transactions.

## The model chooses scientific priority

The model-owned runtime asks which eligible work to pursue: directed requests, frontier obligations,
and optional opportunities share one agenda. Software enforces eligibility; the researcher chooses
priority from bounded, inspectable pages. Empty scoped work is `idle`, not scientific completeness.
Unscoped callers retain the original frontier path.

Context limits, execution limits, and session limits still exist as operational controls. Earlier gain/cost auction functions remain optional helpers for callers that want them.

## One action lifecycle

A small code registry binds each session action's prompt description to its handler, availability,
and membership rule. The loop compiles context, records actual exposure, receives an action, rechecks scope,
commits the decision, dispatches the advertised handler, and records its result. Handlers do not implement
separate observation or decision/outcome plumbing. The registry is not model-writable, and argument
descriptions are not a new schema language. Unexpected programming failures remain visible.

Scientific candidate validation stays in `propose.py`; protocol declaration/interpretation belongs
in `protocols.py`; attempt identity belongs in `replay.py`. Previous imports remain aliases.

## Context compilation

Graph locality and lexical retrieval select a starting packet. The researcher can inspect additional artifacts, search sources, and call instruments before committing to a branch.

Raw evidence and decision outcomes receive higher artifact-kind priority weights than memos. This is a default retrieval preference and a packet need not contain every relevant result, so a memo supplements its sources rather than replacing them.

The whole model request is bounded, including actions, tool descriptions and recent observations.
Explicit required material and selected spans cannot silently degrade into previews; an unfit
requirement raises a budget error. Other local targets may use marked previews with readable IDs.
The model-facing session remains serial. Branch records are not asynchronous investigators.

A capsule uses the same compiler to freeze the mission revision and delivered material. Its
manifest and body have content identities. Returned text is retained, structured contributions
become candidates, and explicit review against current changes precedes admission. An old capsule
is a valid historical delivery, not a live cache to overwrite.

## Reusable resources

Three distinct reuse mechanisms are retained:

- a world references a representation, invariant, or working mechanism;
- a synthesis grammar acquires a named composite;
- the workbench acquires a callable expression-defined diagnostic.

All three let earlier work become available to later work, though they remain related mechanisms rather than one unified registry. Recording a basis does not prove it correct, and a workbench interface check is narrower than the prospective utility check used by a scientific synthesis policy.

## Execution security

`Kernel` and `WorkerKernel` sign execution records. A signature is meaningful only while the signing key and relevant execution path are protected.

The default process worker is explicitly not hermetic. Container tools must declare an image and be configured with appropriate filesystem isolation. The container adapter mounts its working directory; do not place signing keys, evaluator secrets, or unrelated confidential files in that mount. The mathematical and symbolic tool paths also execute code and require an appropriate trust boundary.

See [Security](../SECURITY.md) for worker and credential isolation.

## History and replay

The replay layer adds decision/outcome records and lookup by state digest and action name, which is useful provenance even without a policy learner.

Dream-RSI motivated preserving this structure. Full policy replay would need stronger reconstruction of state, action identity, and branch semantics than a digest lookup provides, and the current implementation does not attempt it.

The [formalism](FORMALISM.md) defines these views and guards without expanding the world tuple.
[Session implementation](REFACTOR.md) maps the action loop to code and tests; the
[implementation status](IMPLEMENTATION_STATUS.md) retains the remaining limitations.

## Telemetry

Telemetry is an opt-in side channel. Instrumented boundaries copy IDs, timings, selection decisions
and optionally redacted content into a separate store. Trace parentage never changes artifact
identity, signed evidence or model prompts. Telemetry never reads the kernel key or adds an agent
action. Routing and context remain researchers' inputs. See [Observability](OBSERVABILITY.md).

Recorder faults leave research execution intact. Health counters report missing captures and
limits; unfinished spans remain incomplete. Request sizes can be measured without retaining the
request. Content capture is optional, and telemetry must stay outside untrusted worker mounts.
