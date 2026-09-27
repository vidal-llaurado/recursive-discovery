# Session implementation

## Action loop

Each session turn follows one path:

```text
compile and fit -> record exposure -> obtain action -> recheck scope
-> commit decision -> dispatch handler -> attach membership -> record outcome
```

`actions.Action` defines arguments, availability, a handler, membership rules and optional
attempt preparation. `actions.Result` returns an observation, artifact references, a summary
and an optional terminal result. The session handles the surrounding bookkeeping.

The action registry supplies both the model's action descriptions and the dispatch table.
Only advertised actions can run. Handlers validate their arguments; the model cannot register
new handlers.

## Modules

| Responsibility | Module |
|---|---|
| Turn order, request budget, exposure history and outcome recording | `session.py` |
| Action descriptions, availability, preparation and handlers | `actions.py` |
| Candidate validation, citations and activation | `propose.py` |
| Programs, missions, membership and attention | `investigation.py` |
| Declared checks and interpretation of their results | `protocols.py` |
| Decisions, outcomes, attempt identities and history | `replay.py` |
| Required context, retrieval and rendering | `context.py` |

## Required context

Mission `required` IDs and selected spans must be delivered in full. Descriptor context
dependencies inherit this requirement. If they exceed the budget, the compiler raises
`ContextOverflow`; the caller can narrow the selection, split the mission or increase the
budget. Other local targets may use previews with handles for further reading.

## Compatibility and trace format

The `research_session` signature and action names remain available.
`investigation.declare_check`, `investigation.interpret_checks` and
`investigation.attempt_signature` alias the implementations in `protocols.py` and `replay.py`.
`session._store_candidates` aliases `propose.store_candidates`.

New decisions store argument summaries in `payload.arguments`, delivered views in
`payload.exposures`, and instrument attempts in `payload.attempt` and `prior_attempts`.
Existing records keep their original shape. Inspection batches return per-item failures in
an `errors` list alongside successful views. Terminal results include the last decision.

Normally completed turns and recoverable errors record one outcome. Interruption, a programming
error or failed ledger write can leave an unmatched decision or partial handler writes.

## Tests

`test_action_lifecycle.py` covers dispatch, decision ordering, membership, scope rechecks,
error handling and compatibility imports. `test_formal_contracts.py` covers required context,
causal heads, capsule digests, schema contracts and evidence preservation. Mission and capsule
tests cover the complete workflows.
