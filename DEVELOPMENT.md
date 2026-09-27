# Development

Install the package and test dependencies in a virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m pytest -q
```

Install `pypdf` to include the optional PDF extraction tests. CI runs the suite on
Python 3.11, 3.12 and 3.13. Tests use temporary workspaces, local subprocesses and
scripted model responses.

## Code and documentation

- [Design](docs/DESIGN.md) explains the system's main responsibilities.
- [Session implementation](docs/REFACTOR.md) maps the action loop to its modules and tests.
- [Implementation status](docs/IMPLEMENTATION_STATUS.md) lists supported features and limits.
- [Formalism](docs/FORMALISM.md) specifies state, routing and execution rules.
- [Mission design](docs/design/RQRE_TO_RD.md) records the mission, branch and capsule decisions.

Keep scientific state in the ledger, large source bytes in the blob store, and operational
telemetry in its separate database. Changes to action behavior should update the action
description, handler and relevant regression tests together.

## Workflows

Use [Workflows](docs/WORKFLOWS.md) for checks, attempt history and capsule round trips.
[Missions](docs/MISSIONS.md), [Reading](docs/READING.md) and
[Telemetry](docs/OBSERVABILITY.md) cover their Python and CLI interfaces.

`examples/mission_roundtrip.py` runs an offline example with a real kernel check and a
scripted external reply. Use a new workspace and keep its signing key private.
