"""Exercise the real session with scripted policies and deterministic local source fixtures."""
import json

import pytest

from recursive_discovery.context import measure_packet
from recursive_discovery.core import Kernel, Task
from recursive_discovery.search import remember_text
from recursive_discovery.store import SQLiteLedger
import recursive_discovery.session as session


@pytest.fixture
def world(tmp_path):
    with SQLiteLedger(tmp_path / "science.db") as ledger:
        kernel = Kernel(tmp_path / "key")
        target = ledger.put("study", {"question": "Investigate stability"})
        yield ledger, kernel, Task("inspect", target.id, "Find the conditions"), tmp_path


class Policy:
    model_id = "deterministic-regression-policy"

    def __init__(self, act, budget=18000):
        self.act, self.budget, self.requests = act, budget, []

    def __call__(self, encoded):
        assert len(encoded) <= self.budget
        packet = json.loads(encoded)
        self.requests.append(packet)
        answer = self.act(packet, len(self.requests) - 1)
        return json.dumps(answer) if isinstance(answer, dict) else answer


def run(world, policy, **kwargs):
    ledger, kernel, task, root = world
    return session.research_session(ledger, kernel, task, policy, root=root,
                                    context_chars=policy.budget, **kwargs)


def evict_documents(monkeypatch):
    """Force context eviction without mocking reading, storage, authority or activation."""
    compile_real = session.compile_context

    def compile_target_only(*args, **kwargs):
        packet = compile_real(*args, **kwargs)
        target = packet["task"]["target"]
        kept = [row for row in packet["artifacts"] if row["id"] == target]
        packet["omitted"] += len(packet["artifacts"]) - len(kept)
        packet["artifacts"] = kept
        measure_packet(packet)
        return packet

    monkeypatch.setattr(session, "compile_context", compile_target_only)


def test_read_passage_remains_citable_after_context_and_observation_eviction(world, monkeypatch):
    ledger, _, _, _ = world
    s = ledger.put("source", {"title": "Disconnected source", "published": "2020-01-01"})
    text = "background " * 2000 + "\nOnly under independence is the result justified."
    doc = remember_text(ledger, s, text)
    evict_documents(monkeypatch)
    remembered = {}

    def act(request, turn):
        assert doc.id not in {x["id"] for x in json.loads(request["context"])["artifacts"]}
        if turn == 0:
            return {"action": "read", "id": doc.id, "query": "Only under independence", "length": 300}
        if turn == 1:
            row = request["recent_observations"][-1]["passage"]
            assert "Only under independence" in row["text"]
            remembered.update(id=row["id"], start=row["start"], end=row["end"])
        if turn < 10:
            return {"action": "memo", "text": f"Work step {turn}: preserve the qualification."}
        assert all("passage" not in o for o in request["recent_observations"])
        return {"action": "finalize", "candidates": [{
            "object_kind": "claim", "commitment": "This result requires independence",
            "source_ids": [s.id], "links": {"support": [doc.id]}, "citations": [remembered],
        }], "activate": [0]}

    policy = Policy(act)
    result = run(world, policy, max_turns=12)
    assert result["status"] == "activated"
    claim = result["live"][0]
    assert claim.refs["sources"] == (s.id,)
    assert claim.refs["support"] == claim.refs["passages"] == (doc.id,)
    assert claim.data["citations"] == [remembered]
    # Current visibility is not falsified merely to authorize a historical citation.
    assert doc.id not in result["last_decision"].data["visible_artifacts"]
    assert any(any(e.get("id") == doc.id and "start" in e for e in d.data["payload"]["exposures"])
               for d in ledger.all("decision"))


def test_unread_span_is_rejected_then_a_valid_span_can_be_committed(world, monkeypatch):
    ledger, _, _, _ = world
    s = ledger.put("source", {"title": "Source", "published": "2020-01-01"})
    doc = remember_text(ledger, s, "A" * 10000)
    evict_documents(monkeypatch)

    def act(request, turn):
        if turn == 0:
            return {"action": "read", "id": doc.id, "start": 1000, "length": 100}
        if turn == 2:
            assert request["recent_observations"][-1]["status"] == "failed"
        return {"action": "finalize", "activate": [0], "candidates": [{
            "object_kind": "claim", "commitment": "A bounded statement",
            "citations": [{"id": doc.id, "start": 1000, "end": 9999 if turn == 1 else 1050}],
        }]}

    out = run(world, Policy(act), max_turns=3)
    assert out["status"] == "activated"
    assert len(ledger.all("claim")) == 1
    assert out["live"][0].data["citations"][0]["end"] == 1050


def test_explicit_empty_activation_does_not_open_a_branch(world):
    result = run(world, Policy(lambda request, turn: {
        "action": "finalize", "candidates": [{"object_kind": "claim", "commitment": "Uncommitted alternative"}],
        "activate": [],
    }))
    assert result["status"] == "proposals"
    assert len(result["candidates"]) == 1 and result["live"] == []
    assert not world[0].all("claim")


@pytest.mark.parametrize("action", ["read", "inspect", "local_search"])
def test_future_source_and_its_text_do_not_leak_through_reading_paths(world, action):
    ledger, _, _, _ = world
    s = ledger.put("source", {"title": "unavailable", "published": "2030-01-01", "abstract": "FORBIDDEN_FUTURE_PAYLOAD"})
    doc = remember_text(ledger, s, "FORBIDDEN_FUTURE_PAYLOAD")
    request = {"action": action, "id": doc.id, "ids": [s.id, doc.id], "query": "unavailable"}

    def act(packet, turn):
        assert "FORBIDDEN_FUTURE_PAYLOAD" not in json.dumps(packet)
        if turn == 0:
            return request
        if action == "read":
            assert packet["recent_observations"][-1]["status"] == "failed"
        return {"action": "finalize", "candidates": [], "activate": []}

    result = run(world, Policy(act), max_turns=2, source_before="2025-01-01")
    assert result["status"] == "no_candidates"


def test_future_derived_instrument_is_neither_listed_nor_callable(world):
    ledger, _, _, _ = world
    s = ledger.put("source", {"title": "Future", "published": "2030-01-01"})
    ledger.put("instrument_definition", {"name": "unavailable_tool", "status": "active",
               "inputs": ["x"], "expression": "x+1", "description": "FORBIDDEN_DESCRIPTION"}, {"source": [s.id]})

    def act(request, turn):
        assert "FORBIDDEN_DESCRIPTION" not in json.dumps(request)
        if turn == 0:
            return {"action": "instrument", "name": "unavailable_tool", "spec": {"x": 1}}
        assert "PermissionError" in request["recent_observations"][-1]["error"]
        return {"action": "finalize", "candidates": []}

    assert run(world, Policy(act), source_before="2025-01-01")["status"] == "no_candidates"


def test_oversized_inspection_is_recoverable_by_reading_a_handle(world):
    ledger, _, _, _ = world
    docs = [ledger.put("note", {"text": '"\\\n' * 10000, "index": i}) for i in range(10)]

    def act(request, turn):
        if turn == 0:
            return {"action": "inspect", "ids": [a.id for a in docs]}
        if turn == 1:
            row = request["recent_observations"][-1]
            assert row["status"] == "deferred"
            return {"action": "read", "id": row["ids"][0], "length": 500}
        assert request["recent_observations"][-1]["passage"]["status"] == "ok"
        return {"action": "finalize", "candidates": []}

    policy = Policy(act, budget=6500)
    assert run(world, policy)["status"] == "no_candidates"
    assert len(policy.requests) == 3
    assert len(ledger.get(docs[0].id).data["text"]) == 30000


@pytest.mark.parametrize("response", ['[', '[]', {"action": "inspect", "ids": None}, {"action": "read", "id": "missing"}])
def test_malformed_response_or_failed_tool_does_not_abort_research(world, response):
    def act(request, turn):
        if turn == 0:
            return response
        observation = request["recent_observations"][-1]
        assert observation.get("status") == "failed" or observation["action"] == "invalid"
        return {"action": "finalize", "candidates": []}

    out = run(world, Policy(act), max_turns=2)
    assert out["status"] == "no_candidates"
    assert len(world[0].all("decision")) == 2


def test_search_failure_is_visible_without_discarding_other_providers(world, monkeypatch):
    import recursive_discovery.search as search

    def fail(*args, **kwargs):
        raise TimeoutError("unreachable")

    monkeypatch.setattr(search, "arxiv", fail)
    monkeypatch.setattr(search, "openalex", lambda *a, **k: [{"title": "Useful", "published": "2020-01-01"}])
    monkeypatch.setattr(search, "crossref", lambda *a, **k: [])

    def act(request, turn):
        if turn == 0:
            return {"action": "literature_search", "query": "stability", "limit": 3}
        row = request["recent_observations"][-1]
        assert len(row["sources"]) == 1 and row["reads"] == []
        assert [x["status"] for x in row["providers"]] == ["error", "ok", "empty"]
        return {"action": "finalize", "candidates": []}

    assert run(world, Policy(act))["status"] == "no_candidates"
    assert world[0].all("search")[0].data["providers"][0]["status"] == "error"


def test_instrument_payload_stays_durable_without_flooding_request(world, monkeypatch):
    ledger, _, _, _ = world
    value = ["value" * 1000 for _ in range(100)]
    evidence = ledger.put("evidence", {"verdict": "fail", "stdout": "diagnostic"})
    result = ledger.put("instrument_result", {"value": value}, {"evidence": [evidence.id]})
    monkeypatch.setattr(session.Workbench, "call", lambda *args, **kwargs: (evidence, result))

    def act(request, turn):
        if turn == 0:
            return {"action": "instrument", "name": "matrix", "spec": {}}
        row = request["recent_observations"][-1]
        assert row["result_id"] == result.id
        assert row["execution_verdict"] == "fail"
        assert row["preview"]["next"] is not None
        return {"action": "finalize", "candidates": []}

    assert run(world, Policy(act, budget=6500))["status"] == "no_candidates"
    assert ledger.get(result.id).data["value"] == value


@pytest.mark.parametrize("indices", [[99], [True], "0"])
def test_invalid_activation_is_an_error_not_an_implicit_commit(world, indices):
    def act(request, turn):
        if turn == 0:
            return {"action": "finalize", "activate": indices, "candidates": [
                {"object_kind": "claim", "commitment": "Proposal only"}]}
        assert request["recent_observations"][-1]["status"] == "failed"
        return {"action": "finalize", "candidates": [], "activate": []}

    assert run(world, Policy(act), max_turns=2)["status"] == "no_candidates"
    assert not world[0].all("claim")
