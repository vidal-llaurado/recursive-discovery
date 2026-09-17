# Usage

[README](../README.md) · [Formalism](FORMALISM.md) · [Security](../SECURITY.md)

## Install

The commands below assume a Linux/macOS-style shell and Python 3.11 or later. The worker module imports POSIX `resource`; native Windows support is not established.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

NumPy, SciPy, and SymPy are declared dependencies. Model-provider libraries belong in your model bridge. Lean, Z3, cvc5, and a container runtime are separate installations, discovered when available. PDF source extraction uses optional `pypdf`; install it separately when needed.

For development:

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
```

## Initialize a workspace and create a study

```bash
recursive-discovery init ./research-workspace
recursive-discovery status ./research-workspace
recursive-discovery tools ./research-workspace
```

A workspace contains a SQLite ledger, retained files, a kernel key, working files, and reports. It is application data, not part of the source repository. Do not publish keys or credentials.

Use the small Python API to create the initial study:

```python
from recursive_discovery import open_project

project = open_project("./research-workspace")
try:
    study = project.ledger.put(
        "study",
        {"question": "Your research question", "scope": "Your assumptions and constraints"},
        by="human",
    )
    print(study.id)
finally:
    project.close()
```

A study alone does not create a `verify` or `run` obligation. Start an exploratory session on its ID to propose the first executable work.

## Connect a model

`CommandModel` runs a command with a JSON request on stdin and reads its response from stdout. No provider is bundled and no credentials are stored.

```bash
recursive-discovery research-step ./research-workspace STUDY_ID \
  --model-cmd "python model_bridge.py"
```

`model_bridge.py` is a file you supply. It should pass the request to your selected model, return a single JSON object, write operational logs to stderr, and fail nonzero on errors. It is invoked for individual requests; persistent scientific state belongs in the ledger rather than an in-memory bridge session.

The two main request modes are:

| Mode | Request | Required response |
| :--- | :--- | :--- |
| `choose_frontier_task` | Available frontier tasks with keys and target data. | An exact `key` and a brief `reason`. |
| `research_session` | Task, compiled context, recent observations, instruments, and allowed action schemas. | One action object following the supplied schema. |

Session actions are `inspect`, `local_search`, `literature_search`, `instrument`, `define_instrument`, `memo`, and `finalize`. The `context` field is currently a serialized JSON packet inside the request rather than a separate message stream. Use the action schemas provided by the request as the contract.

A `finalize` response contains candidate commitments and indices to activate. Candidates can describe a claim or experiment, but executable work still needs a real file, configured tool, and meaningful protocol. Returning a path in JSON does not create the file.

The default session allows 12 exploratory turns and up to 3 activated candidates. Python callers can configure both through `research_session(...)`.

## Run and resume

```bash
recursive-discovery frontier ./research-workspace
recursive-discovery run ./research-workspace \
  --model-cmd "python model_bridge.py" --steps 50
recursive-discovery resume ./research-workspace \
  --model-cmd "python model_bridge.py" --steps 50
```

The runtime automatically dispatches supported claim checks and experiment runs, and returns other tasks to a model session. `resume` reads the same persistent graph and continues the frontier loop; it is not a distributed scheduler or a checkpoint of a model's private state.

Possible statuses include `complete`, `blocked`, and `step_limit`. `complete` means the implemented routing rules produced no pending tasks, which is narrower than a scientific conclusion being established.

## Inspect results

```bash
recursive-discovery inspect ./research-workspace ARTIFACT_ID
recursive-discovery context ./research-workspace ARTIFACT_ID --chars 16000
recursive-discovery trace ./research-workspace
recursive-discovery report ./research-workspace --out ./research-report.md
```

Reports are views of retained records. They do not become independent evidence.

## Search the literature

```bash
recursive-discovery search ./research-workspace "your research query" \
  --providers arxiv,openalex,crossref --limit 10 --read 2
```

`--before YYYY-MM-DD` requests a publication-date cutoff. This is a retrieval constraint, not protection against knowledge already contained in a model or information obtained through another access path. Results and extracted text retain source status.

## Instruments

List installed workbench descriptions:

```bash
recursive-discovery tools ./research-workspace
```

A built-in call reads its input specification from a JSON file:

```bash
recursive-discovery instrument ./research-workspace matrix matrix.json \
  --target STUDY_ID
```

For instance, `matrix.json` can contain `{"matrix": [[1, 0], [0, 2]]}`. The result is a diagnostic, not proof of a larger scientific claim.

A persistent expression definition has this shape:

```json
{
  "name": "vector-length",
  "description": "Euclidean norm of a numeric vector",
  "inputs": ["v"],
  "expression": "norm(v)",
  "tests": [{"inputs": {"v": [3, 4]}, "expected": 5}]
}
```

```bash
recursive-discovery define-instrument ./research-workspace definition.json \
  --target STUDY_ID
recursive-discovery instrument ./research-workspace vector-length call.json \
  --target STUDY_ID
```

`call.json` contains the declared inputs, such as `{"v": [3, 4]}`. Passing supplied cases enables the definition in the workbench; it does not certify its full domain or its scientific usefulness.

## Execution isolation and sealed data

A tool without an image uses the process path and is recorded as `process-not-hermetic`. A tool declaring `image` requires Docker or Podman; it does not silently fall back when that runtime is absent. Container commands must use paths meaningful inside the mounted work directory and an environment containing their dependencies.

**Do not assume that choosing a container protects every project file.** The adapter mounts its `cwd`. Keep the job workspace separate from signing keys, evaluator credentials, and confidential files; do not mount the full sensitive project directory for untrusted code. Review [Security](../SECURITY.md) before enabling model-generated execution.

The sealed evaluator uses separately registered evaluator commands and private data. Its CLI is exposed through `sealed-serve`, `sealed-seal`, `sealed-evaluate`, and `sealed-record`; inspect `--help` for each. Actual secrecy requires separate filesystem and credential access, not merely a separate artifact kind.

## Source map

```text
src/recursive_discovery/
    core.py                 artifacts, execution records, frontier, promotion
    store.py                SQLite graph and retained file storage
    session.py              model-owned investigation and action traces
    runtime.py              operational frontier dispatch
    context.py              task-local retrieval and authority labels
    instruments.py          built-in and learned workbench registration
    dynamic_instrument.py   constrained expression execution
    instrument_exec.py      numerical and symbolic diagnostics
    ops.py                  scientific transformation policies
    math.py                 installed checker adapters
    lab.py, stats.py        experiment replication and derived analysis
    replay.py               decision/outcome history and lookup
    sealed*.py              prospective evaluation interfaces
    cli.py                  command-line entry point
```

No optimizer-specific campaign or bundled stochastic benchmark is required to use the library.
