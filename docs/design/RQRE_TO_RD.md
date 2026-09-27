# Mission, branch and capsule design

The integration adapts RQRE's investigation workflows to Recursive Discovery's ledger,
context compiler and execution kernel. The main objects are a persistent mission, its
branches, and immutable capsules used for external research.

## Missions and capsules

A mission stores the objective, targets, required context, guidance, expected contributions
and access restrictions. Revisions retain earlier mission descriptions. Users can create
missions directly, including when the frontier has no pending work.

A capsule freezes one revision and its selected material. It contains payloads or exact source
spans, with handles that identify the original artifacts and coordinates. Local sessions and
capsules share context selection; local sessions can also use previews and continuation handles.
Required material that exceeds the budget raises an error.

Capsules remain valid records of what was sent. New work requires a fresh snapshot when the
ledger changes, including when a new artifact references an old selected artifact. The current
implementation uses conservative snapshot invalidation.

External responses retain their raw text and capsule identity. Structured contributions become
candidates. Admission requires review against the current mission revision and change report.
Existing dependencies are preserved, and newly introduced sources remain unverified.

## Branch membership and attention

Membership is assigned explicitly and may overlap. Following every reachable reference would
incorrectly assign shared datasets, definitions and instruments to a single branch.

Branches can be active, dormant or closed within a mission. Dormancy is mission-local by default;
program-wide suppression requires a program decision. Deferring a branch preserves its records,
failed attempts and relevant evidence. Its reusable tools can remain available to other work.

Attention events name their causal predecessors. Stale updates are rejected, and concurrent heads
require reconciliation. This detects conflicts; callers must still serialize writes.

Supersession records a preferred replacement for a stated purpose and scope. It preserves old
references and does not automatically close the earlier branch. Context can include compact
summaries of dormant work, with detailed records available on inspection.

## Routing and challenges

Directed requests, frontier obligations and exploratory opportunities share one agenda. Software
checks eligibility; the model chooses a task from bounded pages. Work origin is distinct from
`Task.lane`, which describes the execution channel.

Narrative preferences belong in guidance. Enforced policy consists of date restrictions, denied
artifact IDs and tool allowlists. These checks use declared metadata and dependencies; worker
isolation supplies filesystem and network controls.

Challenges are research requests describing an obstruction, prior attempts and suggested methods.
They can lead to local analysis, execution or an external capsule. Attempt signatures bring prior
runs into context while allowing deliberate replication.

## Schemas and evidence

Optional descriptors define fields, references, display order and context dependencies. Immutable
IDs select descriptor versions. Unknown kinds remain usable through generic views. Tools can require
strict input conformance at execution; research objects can retain advisory conformance reports.

Descriptors do not authenticate scientific results. Claims can select explicit check protocols;
the kernel authenticates execution records, and researchers interpret their consequences.

## Integration points

| Component | Responsibility |
|---|---|
| Ledger and blob store | Retain missions, attention events, capsules, responses and descriptors. |
| `Scope` | Derive membership, attention and access restrictions from ledger records. |
| Context compiler | Select and render material within the request budget. |
| Agenda and runtime | Offer eligible work and dispatch the selected task. |
| Candidate preparation | Validate local and external proposals before activation. |
| Decision history | Record choices, attempts, handoffs and outcomes. |
| Execution kernel | Run declared jobs and authenticate their records. |

See [Missions](../MISSIONS.md) for the API and [Implementation status](../IMPLEMENTATION_STATUS.md)
for current limits.
