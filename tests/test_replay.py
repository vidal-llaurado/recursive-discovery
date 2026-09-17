from pathlib import Path
import sys

from recursive_discovery import Ledger, Kernel, Task
from recursive_discovery.context import compile_context
from recursive_discovery.replay import record_decision, record_outcome, replay_lookup, trace


class Model:
    model_id = "test-model"


def test_replay_records_committed_state_action_outcome(tmp_path: Path):
    l = Ledger(tmp_path / "science.jsonl")
    k = Kernel(tmp_path / "key")
    target = l.put("study", {"question": "q"})
    task = Task("explain", target.id, "need explanation")
    claim = l.put("claim", {"statement": "h"}, {"study": [target.id]})

    d = record_decision(
        l, task,
        action="inspect",
        model=Model(),
        state={"visible": [target.id]},
        frontier=[task],
        visible_artifacts=[target.id],
        available_actions=["inspect", "finalize"],
        payload={"ids": [claim.id]},
    )
    o = record_outcome(l, d, status="observed", observed=[claim], summary={"n": 1})

    rows = trace(l, target=target.id)
    assert rows[0]["decision"].id == d.id
    assert rows[0]["outcomes"][0].id == o.id
    found = replay_lookup(l, state_digest=d.data["state_digest"], action="inspect")
    assert [x.id for x in found] == [o.id]


def test_context_prefers_raw_consequence_history_over_memo(tmp_path: Path):
    l = Ledger(tmp_path / "science.jsonl")
    k = Kernel(tmp_path / "key")
    study = l.put("study", {"question": "q"})
    claim = l.put("claim", {"statement": "c"}, {"study": [study.id]})
    memo = l.put("memo", {"text": "prior researcher likes hypothesis X"}, {"target": [claim.id]})

    ok = tmp_path / "ok.py"
    ok.write_text("print('checked')")
    evidence = k.run(l, claim, "math", [sys.executable, str(ok)], inputs=[ok])

    task = Task("verify", claim.id, "verify claim")
    d = record_decision(
        l, task, action="execute_frontier_task", model=Model(),
        state={"claim": claim.id}, frontier=[task],
        visible_artifacts=[claim.id], available_actions=["execute_frontier_task"],
    )
    outcome = record_outcome(l, d, status="executed", produced=[evidence])

    packet = compile_context(l, k, task, max_chars=7000, radius=3)
    ids = [x["id"] for x in packet["artifacts"]]
    assert evidence.id in ids and memo.id in ids and outcome.id in ids
    assert ids.index(evidence.id) < ids.index(memo.id)
    assert ids.index(outcome.id) < ids.index(memo.id)
