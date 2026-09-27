# Missions, branches and handoffs

Missions organize research using the existing ledger, context compiler, session and runtime.

This guide covers the Python API. See [Workflows](WORKFLOWS.md) for CLI equivalents.

## Storage and meaning

A mission stores its objective, targets, required and working context, guidance, deliverables and access restrictions. Creation also opens a directed request, so work can begin with an empty frontier.

Branches group artifacts by explicit membership. Attention events mark work active, dormant or closed within a mission or program. Supersession records a preferred replacement for a stated purpose, preserving earlier dependencies.

A capsule freezes a mission revision and selected payloads or spans. Later changes require a new capsule. External replies enter as proposals and need local review before admission.

Mission records use either the JSONL or SQLite ledger; capsule and response bytes use `BlobStore`. `Scope` derives a view from those records. It does not restrict direct ledger or filesystem access.

## Start a mission

Open an existing project:

```python
from recursive_discovery import open_project
from recursive_discovery.investigation import (
    create_program, create_mission, open_branch, include, request, Scope,
)

project = open_project("./research-workspace")
ledger = project.ledger

program = create_program(
    ledger,
    "Understand the conditions under which the diagnostic remains reliable",
    guidance="Prefer interpretable failures to unsupported generalization.",
)
mission = create_mission(
    ledger,
    "Investigate whether the construction survives the boundary case",
    program=program.id,
    targets=[claim_id],          # Existing artifact IDs.
    required=[definition_id],    # Required in full or as a selected span.
    working=[earlier_note_id],   # Optional starting context.
    guidance="Retain earlier counterexamples. Investigate alternative representations.",
    deliverables="Propose a checkable revision and identify the assumptions it needs.",
)
branch = open_branch(ledger, mission.id, "Examine the current representation")
include(ledger, mission.id, [claim_id, experiment_id], branch=branch.id)

# Add an exploratory request.
request(ledger, mission.id, "Does a different representation remove the obstruction?",
        targets=[claim_id], branch=branch.id, origin="explore")
```

`include()` assigns membership only to the supplied IDs. Dependencies retain their own membership, and branches can belong to multiple missions.

Close the project in application cleanup using `project.close()`.

## Enforced restrictions versus guidance

Programs and missions accept exactly three enforced policy keys:

```python
policy = {
    "source_before": "2024-12-31",
    "deny_ids": [restricted_artifact_id],
    "tool_ids": [approved_tool_id],
}
```

Omitting `tool_ids` adds no restriction; `[]` prohibits executable tools in the scoped session/runtime. Program and mission denials are combined, tool allowlists are intersected, and the earliest date wins. Per-call cutoffs may narrow access further. Unknown policy keys are rejected; preferences and stopping advice belong in `guidance`.

Access checks follow declared dependencies, including candidate semantic links. Under a cutoff, sources need valid full publication and version dates. Missing, malformed or future dates are rejected. These checks rely on supplied metadata; they cannot inspect undeclared inputs or remove model pretraining knowledge.

Configure worker isolation, credentials and historical datasets separately. `tool_ids` limits execution tools, not network providers. Direct ledger/kernel calls bypass `Scope`.

## Attention, conflict, and supersession

```python
from recursive_discovery.investigation import attention, set_attention, supersede

state = attention(ledger, branch.id, mission.id)
paused = set_attention(
    ledger, branch.id, mission.id, "dormant",
    expected=state["heads"],
    reason="This approach repeatedly fails at the same boundary condition.",
    support=[failed_evidence_id],
    revisit="Revisit when the representation or boundary condition changes.",
)
```

Dormancy defaults to **mission scope**. Program-wide suppression requires an explicit event with `program.id` as its scope. A mission-level activation cannot override a dormant or closed program-level decision.

Branches use `active`, `dormant` and `closed`; requests use `open`, `deferred` and `done`. These states control scheduling. Reopening requires a decision against the current heads.

Events name causal predecessors. Stale `expected` heads raise `Conflict`; concurrent heads suppress scheduling until all are reconciled. Serialize writers: these checks are not distributed transactions. Reopen JSONL readers after another instance writes.

Dormancy defers work while preserving its frontier obligations and evidence. Required or selected historical material remains in context; optional retrieval still depends on the budget.

A shared assumption can inherit a stress obligation from an in-scope claim without being owned by that branch. A reusable tool from a dormant investigation remains callable when otherwise permitted.

```python
supersede(
    ledger, old_representation_id, new_representation_id, mission.id,
    purpose="small-noise approximation",
    reason="Prefer the new representation in this regime, not universally.",
    support=[comparison_id],
)
```

Supersession annotates context with the purpose and replacement while preserving references, evidence and branch state. Overlapping preferences remain visible. `Scope.branch_page(offset=0, limit=12)` pages branch attention. Context includes up to eight dormant summaries and reports omissions.

## Revisions

A mission ID identifies the root; `mission_revision()` returns its current head. Revisions replace the editable description while retaining earlier records.

```python
from recursive_discovery.investigation import mission_revision, revise_mission

current = mission_revision(ledger, mission.id)
revision = revise_mission(
    ledger, mission.id, expected=[current.id],
    objective="A narrower question after the first result",
    targets=[revised_target_id], required=[definition_id], working=[],
    guidance="Retain the failed original as provenance.",
    deliverables="A prospective test design.", policy=current.data["policy"],
)
```

Revising an objective leaves existing requests open. Review their attention state separately. Scoped sessions stop when the revision changes, and routing rechecks revision and eligibility before dispatch.

## Agenda

```python
from recursive_discovery.agenda import available_work, contradiction_opportunities
from recursive_discovery.runtime import Runtime

agenda = available_work(ledger, project.kernel, Scope(ledger, mission.id))
# agenda["eligible"]: Work objects; agenda["suppressed"]: explicit reasons.

result = Runtime(ledger, project.kernel, root=project.root).run(
    mission=mission.id,
    model=model_bridge,  # Same JSON-in/JSON-out callable used by RD.
    max_steps=25,
    context_chars=18000,
    scanners=(contradiction_opportunities,),  # Optional scanner.
)
```

The agenda combines requests, challenges, frontier obligations and caller-supplied opportunities. Work origin is separate from `Task.lane`. The model can page through results, select a displayed key or defer. Extend the agenda with `Work` producers.

The optional contradiction scanner proposes investigation of recorded contradiction relationships. Enable it explicitly; unresolved relationships may otherwise be offered repeatedly.

Scoped runtime statuses include `idle` (no eligible work in this view), `deferred`, `handoff`, `scope_changed`, `blocked`, and `step_limit`. A completed local request may leave frontier obligations or inactive work elsewhere. Unscoped runs retain the existing frontier-driven behavior.

Scoped actions manage branches, requests, challenges, descriptors and capsules. Model-driven branch changes are mission-local; program-wide changes require controller calls. Handoff defers the request, and admitting a reply does not reactivate it.

Session and agenda requests have character budgets. Oversized observations use readable handles; `inspect` accepts string-field spans. [Reading](READING.md) uses the same access restrictions and request budget.

## Export an immutable capsule

```python
from recursive_discovery.capsules import compile_capsule, export_capsule

capsule = compile_capsule(
    ledger, project.kernel, project.blobs, mission.id,
    max_chars=100000,
    selections=[
        {"id": definition_id},
        {"id": failed_evidence_id},
        {"id": paper_text_id,
         "span": {"field": "text", "start": 12000, "end": 14800}},
    ],
)
paths = export_capsule(ledger, project.blobs, capsule.id, "./handoffs")
```

Without `selections`, the compiler chooses graph-local and lexical context. Selected material, mission targets, required references and descriptor dependencies are included. Each explicit selection is mandatory and may contain multiple spans. Offsets address the original artifact string in zero-based, half-open Python characters.

The Markdown handoff contains selected material, handles, provenance labels, mission details and a JSON reply guide. The JSON file is its manifest. Re-export refuses to overwrite a file whose contents differ.

If required material exceeds the budget, `ContextOverflow` requires narrower spans, a smaller mission, split handoffs or a larger budget. Selected spans are labelled as partial content.

Freshness depends on the ledger snapshot, compiler fingerprint and current evidence verification. Any non-capsule addition changes the next compilation, including new references to old artifacts. The local snapshot retains historical IDs; the portable manifest contains its digest. `changes_since()` reports additions, missing records, incoming references and mission revision for local review.

Cache hits verify retained hashes and current evidence labels. Capsules use material already retained in the project; they do not fetch missing sources.

## Return and admit contributions

The receiving model returns JSON following the guide in the actual capsule. A minimal example:

```json
{
  "capsule": "DELIVERY_ID_FROM_THE_MARKDOWN",
  "sources": [{"handle": "N1", "title": "A new source", "url": "https://example.org/paper"}],
  "contributions": [{
    "object_kind": "parameter_domain",
    "commitment": "Investigate the restricted domain instead.",
    "payload": {"condition": "-1 < r < 1"},
    "links": {"qualifies": ["H2"]},
    "source_ids": ["N1"],
    "citations": [{"handle": "H1", "field": "text", "start": 12000, "end": 12120}]
  }]
}
```

Use handles and ranges from the capsule. New sources remain unverified and are not fetched automatically. Contributions can use new scientific kinds, but cannot create control records, tool registrations or authenticated/derived observations.

```python
from pathlib import Path
from recursive_discovery.capsules import receive_response, changes_since, accept_response

response, normalization = receive_response(
    ledger, project.blobs, capsule.id,
    Path("reply.json").read_text(encoding="utf-8"),
    provider="external-chat", model="record-the-model-you-used",
)
assert normalization.data["status"] == "proposals"
review = changes_since(ledger, project.blobs, capsule.id)
# Inspect the retained proposals and relevant changes before calling accept_response.
live = accept_response(
    ledger, project.blobs, response.id,
    indices=[0], reviewed_revision=review["revision"],
    reviewed_changes=review["review_digest"], branch=successor_branch_id,
    reason="Reviewed the original delivery, new evidence, and the proposed dependencies.",
)
```

Raw responses are always retained. Valid JSON is normalized into proposals; malformed or free-form replies receive `invalid_response`. Identical text and provenance return the same response/outcome. Corrected JSON creates a new response.

Citations must fall within supplied string ranges. This verifies exposure, not support for the claim. Dependencies in semantic links and `source_ids` both undergo cutoff checks.

Admission requires the current mission revision, change-report digest and an active destination branch. Changed state requires another review. A proposal can be activated once; use `include()` to share the resulting artifact. Admission preserves links to the capsule, candidate and citations.

Validation precedes proposal writes. Multi-record writes are not atomic, but deterministic IDs permit retries after interruptions. Serialize normalization and admission calls.

## Optional schemas and challenge requests

```python
from recursive_discovery.schema import define_schema, check
from recursive_discovery.investigation import challenge, attempt_signature

descriptor = define_schema(ledger, "parameter domain", fields={
    "condition": {"type": "string", "required": True},
    "explanation": {"type": "string"},
}, references={
    "definition": {"kinds": ["note"], "min": 1, "context": True},
}, display=["condition", "explanation"])
```

Artifacts select descriptors with `refs={"schema": [descriptor.id], ...}`. Descriptors define primitive field types, required fields, reference kinds/cardinality, display order and context dependencies. IDs fix the version. Unknown descriptors and unregistered kinds use generic views.

Stored objects receive advisory conformance reports. Tools can require strict call-value checks with `refs["input_schema"]`, enforced by the scoped workbench/runtime before execution. Descriptors contain no executable validators or renderers.

```python
challenge(ledger, mission.id, claim_id,
          "The estimator is unstable near the boundary; distinguish numerical from conceptual failure.",
          attempts=[failed_evidence_id],
          methods="Consider sensitivity analysis, a different parameterization, or an external derivation.")
```

A challenge records an obstruction, prior attempts and suggested methods. It can lead to local analysis, execution or a capsule. `attempt_signature()` identifies declared targets, inputs and protocols while permitting replication.

## Limits and compatibility

Ordinary claims retain mathematical-check routing. Claims with `check_policy="explicit"` use
[declared protocols](WORKFLOWS.md#declared-checks). Schema selection does not change routing.

Execution is serial. Scope, frontier and change-review queries scan ledger state; pagination
limits prompt size rather than database work. See [Implementation status](IMPLEMENTATION_STATUS.md).

The offline `examples/mission_roundtrip.py` example runs a kernel check and imports a scripted
reply. Use a new workspace and keep its signing key private.

The action registry lives in `actions.py`, checks in `protocols.py`, and shared decision/outcome
handling in `session.py`. Legacy `investigation` imports remain aliases. See
[Session implementation](REFACTOR.md) for details.
