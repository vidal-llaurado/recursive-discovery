# Design

[README](../README.md) · [Formalism](FORMALISM.md) · [Usage](USAGE.md)

## Separate three responsibilities

**Investigation** chooses what to ask, read, derive, implement, and compare. It belongs to the model and its research policy.

**Execution** runs a declared tool and retains an authenticated account of what happened. It belongs to the kernel and configured workers.

**Interpretation** relates a result to assumptions, predictions, and alternative explanations. It belongs to scientific analysis; neither a process exit code nor an HMAC performs that work.

Keeping these separate is more important than adding more agent roles. A stronger model can produce better proposals without acquiring the ability to make those proposals true by assertion.

## The artifact graph is the shared workspace

Claims, sources, results, failed attempts, memos, instruments, and decisions should survive individual model invocations. They use one artifact/reference representation rather than separate memory services for every kind of object.

SQLite provides operational persistence and lexical retrieval. Content-addressed storage retains declared input files. Both sit behind the same small ledger interface.

Append-only application writes support lineage. A local database remains open to direct modification by a process with filesystem access, so protection of the ledger depends on the deployment boundary rather than on the storage format.

## The model chooses scientific priority

The model-owned runtime asks the model which frontier task is worth pursuing, so gain and burden are whatever the model judges them to be.

Context limits, execution limits, and session limits still exist as operational controls. Earlier gain/cost auction functions remain optional helpers for callers that want them.

## Context compilation

Graph locality and lexical retrieval select a starting packet. The researcher can inspect additional artifacts, search sources, and call instruments before committing to a branch.

Raw evidence and decision outcomes receive higher artifact-kind priority weights than memos. This is a default retrieval preference and a packet need not contain every relevant result, so a memo supplements its sources rather than replacing them.

The model-facing session is presently serial and bounded. Multiple branches persist in the graph as records rather than as independent asynchronous investigators.

## Reusable resources

Three distinct reuse mechanisms are retained:

- a world references a representation, invariant, or working mechanism;
- a synthesis grammar acquires a named composite;
- the workbench acquires a callable expression-defined diagnostic.

All three let earlier work become available to later work, though they remain related mechanisms rather than one unified registry. Recording a basis does not prove it correct, and a workbench interface check is narrower than the prospective utility check used by a scientific synthesis policy.

## Execution security

`Kernel` and `WorkerKernel` sign execution records. A signature is meaningful only while the signing key and relevant execution path are protected.

The default process worker is explicitly not hermetic. Container tools must declare an image and be configured with appropriate filesystem isolation. The container adapter mounts its working directory; do not place signing keys, evaluator secrets, or unrelated confidential files in that mount. The mathematical and symbolic tool paths also execute code and require an appropriate trust boundary.

See [Security](../SECURITY.md). This documentation describes a deployment requirement, not a security certification.

## History and replay

The replay layer adds decision/outcome records and lookup by state digest and action name, which is useful provenance even without a policy learner.

Dream-RSI motivated preserving this structure. Full policy replay would need stronger reconstruction of state, action identity, and branch semantics than a digest lookup provides, and the current implementation does not attempt it.

The design target is a compact system with legible boundaries: what was proposed, what actually executed, what was observed, what remains an interpretation, and what later research is allowed to reuse.
