# Research workflows

This guide covers check protocols, attempt history, CLI commands and capsule round trips. Import the Python APIs from the modules shown in the examples.

## Declared checks

Set `check_policy="explicit"` to route a claim through a declared protocol and researcher interpretation.

```python
from recursive_discovery.investigation import create_mission
from recursive_discovery.protocols import declare_check, interpret_checks
from recursive_discovery.core import frontier

claim = ledger.put("claim", {
    "statement": "The proposed diagnostic distinguishes the stated cases.",
    "check_policy": "explicit",
})
mission = create_mission(ledger, "Investigate the diagnostic", targets=[claim.id])
# The claim now produces a design_check task.
experiment = declare_check(
    ledger, mission.id, claim.id,
    "Evaluate the diagnostic under the declared data and procedure",
    tool=registered_tool_id,
    values={"path": str(protocol_path)},
    inputs=[dataset_artifact_id],
)
```

`registered_tool_id`, `protocol_path` and `dataset_artifact_id` must already exist. The tool is
registered through RD's ordinary capability mechanism. `declare_check` cannot install a command
or bypass a mission allowlist. Artifact inputs identify provenance; the tool's declared file
inputs still determine which bytes the execution kernel hashes.

The returned object is an ordinary `experiment`, linked by `checks` to the claim. RD runs it
through the existing execution path. An authenticated execution, whether its process passes or
fails, creates an interpretation obligation for an explicit-policy claim. A failed process also
retains its repair obligation. New executions require new interpretations; existing references
are not rewritten.

```python
interpretation = interpret_checks(
    ledger, kernel, claim.id, [execution_evidence_id],
    "Under this procedure the observation does not support the proposed diagnostic.",
)
```

`interpret_checks` validates evidence provenance and target association. Its resolution is a researcher-authored `proposal_or_state`; a successful process can still produce a negative scientific result.

Claims without `check_policy="explicit"` retain the original mathematical-first routing.
Declaring an experiment does not mutate an old claim or silently opt it into a new policy.
Arbitrary artifact kinds can also be checked and interpreted, but the automatic design/interpret
frontier extension applies to explicit-policy claims only. No new legacy study/bridge semantics
are inferred for those arbitrary kinds.

## Attempt history

```python
from recursive_discovery.replay import attempt_history

page = attempt_history(ledger, kernel, targets=[claim.id], offset=0, limit=8)
```

Attempt history joins decisions, outcomes and signed execution records, distinguishing blocked jobs, failed processes and recorded outputs. The runtime records inputs, values, protocol and seeds; instrument calls record argument-based identities. Each tool plan retains its own evidence references.

Matching signatures permit replication. File changes alter file-based signatures; changing an author does not. Pre-execution digests identify the planned attempt, while kernel records describe the actual run.

Use `attempts` for paginated history and `interpret` to record conclusions. Mission sessions also provide `check`, `challenge`, `request`, `open_branch`, `branch_state`, `pause_branch`, `define_schema` and `capsule`.

## Command-line operations

The `mission` command dispatches JSON specs to the corresponding Python functions. Specs are data, not executable code.

```bash
recursive-discovery mission ./workspace create --spec mission.json
recursive-discovery mission ./workspace agenda --spec agenda.json
recursive-discovery run ./workspace --mission MISSION_ID --model-cmd "python model_bridge.py"
recursive-discovery read ./workspace SOURCE_ID --mission MISSION_ID --outline
recursive-discovery read ./workspace SOURCE_TEXT_ID --query "boundary condition" --length 1800
```

Replace returned IDs and paths in these examples. A minimal `mission.json` is:

```json
{"objective":"Investigate the boundary condition","guidance":"Retain failed attempts as history."}
```

An `agenda.json` is:

```json
{"mission":"MISSION_ID","offset":0,"limit":20}
```

Other supported operations are `program`, `revise`, `branch`, `include`, `attention`,
`supersede`, `request`, `challenge`, `check`, `interpret`, `schema`, `attempts`, `capsule`,
`changes`, `receive` and `accept`. `--spec -` reads the object from stdin. The detailed mission
API examples in [MISSIONS.md](MISSIONS.md) show the arguments for lifecycle and schema actions.
Program-wide decisions require the program ID explicitly; mission-local is the normal scope.

`run`, `resume` and `research-step` accept `--mission`; `--chars` bounds the session request.
`research-step` also accepts `--branch`. `context`, `read` and `search` accept mission scope
and a further `--before` restriction. Unscoped administrative `inspect` and direct kernel calls
remain trusted operations, not sandboxed agent endpoints.

CLI search retains original bytes and returns acquired text IDs. Provider diagnostics go to stderr; result rows are JSON lines on stdout. `--read-chars` limits the printed preview (default 4000), while extraction remains complete. Rows report reading failures.

## External capsule round trip

Prepare a capsule spec; `selections` is optional. Required mission context is still enforced.

```json
{"mission":"MISSION_ID","max_chars":100000,"directory":"./handoffs","selections":[{"id":"SOURCE_TEXT_ID","span":{"field":"text","start":12000,"end":14800}}]}
```

```bash
recursive-discovery mission ./workspace capsule --spec capsule.json
```

Send the exported Markdown to the external researcher. It includes a delivery ID, actual
selected material, short handles, authority labels and a response guide. The JSON manifest
retains the exact provenance. External replies must identify that delivery ID; do not substitute
the ledger bundle ID in the response body.

To retain and normalize the raw reply:

```json
{"capsule":"CAPSULE_BUNDLE_ID","response_file":"./external-response.json","provider":"external-chat"}
```

```bash
recursive-discovery mission ./workspace receive --spec receive.json
recursive-discovery mission ./workspace changes --spec changes.json
```

`changes.json` is `{"capsule":"CAPSULE_BUNDLE_ID"}`. Inspect that report and the returned
proposals. An admission spec explicitly records the review:

```json
{"response":"RESPONSE_ARTIFACT_ID","indices":[0],"reviewed_revision":"CURRENT_REVISION_ID","reviewed_changes":"REVIEW_DIGEST","reason":"Reviewed the supplied evidence and the changes since delivery."}
```

```bash
recursive-discovery mission ./workspace accept --spec admission.json
```

Admission fails if the mission revision or change report has changed; refresh the report and review again. `indices: []` admits nothing. Accepted contributions retain proposal authority, and admission does not reactivate branches.

## Citation coordinate systems

Source reading returns `field="text"` and zero-based half-open Python character offsets in the
immutable extracted text. Explicit `inspect` spans identify the original named string field.
Reading a general artifact returns `field="$artifact_json"`, identifying the stable JSON view
of its kind/data/refs. These offsets are **not interchangeable**.

Local candidates cite the field returned by the reader:

```json
{"id":"ARTIFACT_ID","field":"text","start":12000,"end":12120}
```

The `field` is optional only for the default `text` coordinate system. Validated fields and ranges
survive activation. Capsule citations use the provided H-handle and the original field specified
in that delivery; they are checked against the actual delivered range. Citation verification
establishes exposure and provenance, not scientific entailment.

## Offline worked example

```bash
python examples/mission_roundtrip.py --workspace ./new-demo-workspace
```

Use an empty/new path. The example executes a real endpoint test through the runtime and kernel,
records its attempt, interprets the failed test, pauses its branch, freezes evidence in a capsule,
detects incoming information and admits a constructed external proposal to a successor branch.
No network or language model is used. The sample policy and external reply are synthetic.
Do not distribute the generated workspace's signing key.

## Record telemetry

```bash
recursive-discovery --telemetry-dir ../observations run ./workspace \
  --mission MISSION_ID --model-cmd 'python model_bridge.py'
```

The global `--telemetry-content` flag enables best-effort redacted content capture and is off by
default. Telemetry stays separate from the research ledger. The [telemetry guide](OBSERVABILITY.md)
covers recorded metadata, explicit provider usage, limits and retention. Inspect the SQLite capture
using trusted local tools.
