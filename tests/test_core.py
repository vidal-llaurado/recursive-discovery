from pathlib import Path
import sys

from recursive_discovery import (
    Ledger, Kernel, Task, allocate, bid, calibration, compress, diagnose, discriminate, drive,
    frontier, promote, resolve, settle, stress, conjecture, conjectures, expand_representation,
    base_grammar, adopt_grammar, extend_grammar, invent_instrument, synthesize_mechanism,
)


def test_content_addressing(tmp_path: Path):
    l = Ledger(tmp_path / "x.jsonl")
    a = l.put("claim", {"x": 1})
    b = l.put("claim", {"x": 1})
    assert a.id == b.id
    assert len(l.all()) == 1


def test_fake_evidence_does_not_close_frontier(tmp_path: Path):
    l = Ledger(tmp_path / "x.jsonl")
    k = Kernel(tmp_path / "key")
    s = l.put("study", {"q": "q"})
    c = l.put("claim", {"statement": "x"}, {"study": [s.id]})
    l.put("evidence", {"lane": "math", "verdict": "pass", "_sig": "fake"}, {"target": [c.id]})
    assert any(t.verb == "verify" and t.target == c.id for t in frontier(l, k))


def test_execution_is_signed_and_hashes_declared_inputs(tmp_path: Path):
    l = Ledger(tmp_path / "x.jsonl")
    k = Kernel(tmp_path / "key")
    c = l.put("claim", {"statement": "x"})
    tool = l.put("tool", {
        "lane": "math", "semantics": "check",
        "argv": [sys.executable, "{script}"], "inputs": ["{script}"],
    })
    p = tmp_path / "ok.py"
    p.write_text("print('ok')")
    e1 = k.use(l, tool, c, {"script": p.name}, cwd=tmp_path, seed=7)
    fp1 = e1.data["run"]["fingerprint"]
    assert k.verify(e1)
    assert e1.data["run"]["inputs"][p.name]
    assert e1.data["run"]["seed"] == 7
    p.write_text("print('changed')")
    e2 = k.use(l, tool, c, {"script": p.name}, cwd=tmp_path, seed=7)
    assert e2.data["run"]["fingerprint"] != fp1


def test_assumption_becomes_stress_obligation_then_closes(tmp_path: Path):
    l = Ledger(tmp_path / "x.jsonl")
    k = Kernel(tmp_path / "key")
    s = l.put("study", {"q": "q"})
    a = l.put("assumption", {"var": "lambda", "op": ">", "value": 0})
    c = l.put("claim", {"statement": "x"}, {"study": [s.id], "assumptions": [a.id]})
    x = l.put("experiment", {"protocol": "baseline"}, {"study": [s.id]})
    ok = tmp_path / "ok.py"
    ok.write_text("print('ok')")
    k.run(l, c, "math", [sys.executable, str(ok)], inputs=[ok])
    k.run(l, x, "empirical", [sys.executable, str(ok)], inputs=[ok])
    assert any(t.verb == "stress" and t.target == a.id for t in frontier(l, k))
    sx = l.put("experiment", {"protocol": stress(a)}, {"study": [s.id], "challenges": [a.id]})
    k.run(l, sx, "empirical", [sys.executable, str(ok)], inputs=[ok])
    assert any(t.verb == "reconcile" and t.target == s.id for t in frontier(l, k))


def test_compress_and_promote_are_separate_epistemic_moves(tmp_path: Path):
    l = Ledger(tmp_path / "x.jsonl")
    k = Kernel(tmp_path / "key")
    b = l.put("bridge", {"status": "agreement"})
    assert any(t.verb == "compress" for t in frontier(l, k))
    inv = compress(l, b, {"law": "z"})
    assert any(t.verb == "promote" and t.target == inv.id for t in frontier(l, k))
    promote(l, inv, {"coordinates": ["z"]})
    assert not any(t.target == inv.id for t in frontier(l, k))


def test_stress_crosses_numeric_boundary(tmp_path: Path):
    l = Ledger(tmp_path / "x.jsonl")
    a = l.put("assumption", {"var": "x", "op": ">", "value": 0})
    i = stress(a)
    assert i["values"][0] > 0 and 0 in i["values"] and i["values"][-1] < 0


def test_anomaly_to_one_split_conjecture():
    cases = [
        {"noise": .1, "depth": 20, "anomaly": False},
        {"noise": .2, "depth": 2,  "anomaly": False},
        {"noise": .4, "depth": 9,  "anomaly": False},
        {"noise": .7, "depth": 3,  "anomaly": True},
        {"noise": .9, "depth": 30, "anomaly": True},
    ]
    r = conjecture(cases, features=["noise", "depth"])
    assert r and r["if"]["var"] == "noise" and r["accuracy"] == 1.0


def test_diagnose_separates_scope_failure_from_theory_failure():
    out = diagnose([
        {"x": 1, "predicted": True, "observed": True, "assumptions": {"A": True}},
        {"x": -1, "predicted": True, "observed": False, "assumptions": {"A": False}},
    ], assumptions=["A"], features=["x"])
    assert out["class"] == "out_of_scope"
    assert out["in_scope_mismatches"] == []
    assert out["assumption_effects"][0]["lift"] == 1.0

    inside = diagnose([
        {"noise": .1, "predicted": True, "observed": True, "assumptions": {"A": True}},
        {"noise": .2, "predicted": True, "observed": True, "assumptions": {"A": True}},
        {"noise": .8, "predicted": True, "observed": False, "assumptions": {"A": True}},
        {"noise": .9, "predicted": True, "observed": False, "assumptions": {"A": True}},
    ], assumptions=["A"], features=["noise"])
    assert inside["class"] == "in_scope"
    assert inside["residual_rule"]["if"]["var"] == "noise"
    assert inside["residual_rule"]["accuracy"] == 1.0


def test_frontier_routes_disagreement_through_diagnosis(tmp_path: Path):
    l = Ledger(tmp_path / "x.jsonl")
    k = Kernel(tmp_path / "key")
    b = l.put("bridge", {"status": "disagreement"})
    assert any(t.verb == "diagnose" and t.target == b.id for t in frontier(l, k))
    d = l.put("discrepancy", {"class": "in_scope"}, {"bridge": [b.id]})
    fs = frontier(l, k)
    assert any(t.verb == "explain" and t.target == d.id for t in fs)
    l.put("claim", {"statement": "h"}, {"anomaly": [d.id]})
    assert not any(t.verb == "explain" and t.target == d.id for t in frontier(l, k))


def test_out_of_scope_discrepancy_routes_to_regime_not_conjecture(tmp_path: Path):
    l = Ledger(tmp_path / "x.jsonl")
    k = Kernel(tmp_path / "key")
    b = l.put("bridge", {"status": "disagreement"})
    d = l.put("discrepancy", {"class": "out_of_scope"}, {"bridge": [b.id]})
    fs = frontier(l, k)
    assert any(t.verb == "scope" and t.target == d.id for t in fs)
    assert not any(t.verb == "explain" and t.target == d.id for t in fs)
    l.put("regime", {"scope": ["A"]}, {"discrepancy": [d.id]})
    assert not any(t.target == d.id for t in frontier(l, k))


def test_failed_bid_calibrates_actor_and_changes_allocation(tmp_path: Path):
    l = Ledger(tmp_path / "x.jsonl")
    task = Task("reconcile", "study", "why")
    fast = bid(l, task, actor="fast", gain=.9, cost=.5, attempt=0)
    bid(l, task, actor="careful", gain=.95, cost=.6, attempt=0)
    assert allocate(l, [task], budget=1)[0][1].by == "fast"
    settle(l, task, fast, [task], [task], attempt=0)
    assert calibration(l, "fast")["multiplier"] < 1
    assert allocate(l, [task], budget=1)[0][1].by == "careful"


def test_drive_advances_diagnose_then_explain(tmp_path: Path):
    l = Ledger(tmp_path / "x.jsonl")
    k = Kernel(tmp_path / "key")
    b = l.put("bridge", {"status": "disagreement"})

    def propose(task, _):
        yield {"actor": "worker", "gain": .9, "cost": .5}

    def execute(task, winner, ledger, _kernel):
        if task.verb == "diagnose":
            ledger.put("discrepancy", {"class": "in_scope"}, {"bridge": [b.id]})
        elif task.verb == "explain":
            d = ledger.get(task.target)
            ledger.put("claim", {"statement": "h"}, {"anomaly": [d.id]})
        elif task.verb == "verify":
            return

    result = drive(l, k, propose, execute, budget=1, max_cycles=3, patience=2)
    assert any(t.verb == "verify" for t in frontier(l, k))
    assert result["history"][0]["winners"][0][0] == "diagnose"
    assert result["history"][1]["winners"][0][0] == "explain"


def test_conjectures_preserve_observationally_equivalent_explanations():
    cases = [
        {"noise": .1, "depth": 4,  "anomaly": False},
        {"noise": .2, "depth": 6,  "anomaly": False},
        {"noise": .4, "depth": 8,  "anomaly": False},
        {"noise": .7, "depth": 12, "anomaly": True},
        {"noise": .8, "depth": 16, "anomaly": True},
        {"noise": 1.0, "depth": 20, "anomaly": True},
    ]
    rs = conjectures(cases, features=["noise", "depth"], limit=2)
    assert len(rs) == 2
    assert {r["if"]["var"] for r in rs} == {"noise", "depth"}
    assert all(r["accuracy"] == 1.0 for r in rs)


def test_discriminate_maximizes_information_per_cost(tmp_path: Path):
    l = Ledger(tmp_path / "x.jsonl")
    h1 = l.put("claim", {"rule": {
        "if": {"var": "noise", "op": ">", "value": .55},
        "then": "anomaly=true",
    }})
    h2 = l.put("claim", {"rule": {
        "if": {"var": "depth", "op": ">", "value": 10},
        "then": "anomaly=true",
    }})
    d = discriminate([h1, h2], [
        {"noise": .2, "depth": 4, "_cost": .1},   # agree false: zero bits
        {"noise": .8, "depth": 4, "_cost": .5},   # disagree: one bit
        {"noise": .2, "depth": 16, "_cost": 1.0}, # disagree: one bit, more costly
    ])
    assert d["intervention"] == {"noise": .8, "depth": 4}
    assert d["information_gain_bits"] == 1.0
    assert d["score"] == 2.0


def test_resolution_eliminates_prediction_inconsistent_hypothesis(tmp_path: Path):
    l = Ledger(tmp_path / "x.jsonl")
    h1 = l.put("claim", {"rule": {"if": {"var": "x", "op": ">", "value": 0}, "then": "anomaly=true"}})
    h2 = l.put("claim", {"rule": {"if": {"var": "x", "op": "<=", "value": 0}, "then": "anomaly=true"}})
    d = discriminate([h1, h2], [{"x": 1, "_cost": 1}])
    r = resolve(d, True)
    assert r["survivors"] == [h1.id]
    assert r["rejected"] == [h2.id]
    assert r["resolved"] is True
    assert r["surprise"] is False


def test_frontier_routes_two_verified_explanations_to_discrimination(tmp_path: Path):
    l = Ledger(tmp_path / "x.jsonl")
    k = Kernel(tmp_path / "key")
    d = l.put("discrepancy", {"class": "in_scope"})
    h1 = l.put("claim", {"statement": "h1"}, {"anomaly": [d.id]})
    h2 = l.put("claim", {"statement": "h2"}, {"anomaly": [d.id]})
    ok = tmp_path / "ok.py"
    ok.write_text("print('ok')")
    k.run(l, h1, "math", [sys.executable, str(ok)], inputs=[ok])
    k.run(l, h2, "math", [sys.executable, str(ok)], inputs=[ok])

    fs = frontier(l, k)
    assert any(t.verb == "discriminate" and t.target == d.id for t in fs)

    exp = l.put("experiment", {"design": {}}, {
        "discriminates": [d.id], "hypotheses": [h1.id, h2.id]
    })
    fs = frontier(l, k)
    assert not any(t.verb == "discriminate" and t.target == d.id for t in fs)
    assert any(t.verb == "run" and t.target == exp.id for t in fs)

    k.run(l, exp, "empirical", [sys.executable, str(ok)], inputs=[ok])
    fs = frontier(l, k)
    assert any(t.verb == "resolve" and t.target == exp.id for t in fs)

    l.put("resolution", {"survivors": [h1.id], "rejected": [h2.id]}, {"experiment": [exp.id]})
    assert not any(t.verb == "resolve" and t.target == exp.id for t in frontier(l, k))


def test_representation_expansion_finds_coordinate_that_resolves_aliasing():
    cases = [
        {"x": 1, "mode": "a", "batch": "u", "y": 1},
        {"x": 1, "mode": "b", "batch": "u", "y": 3},
        {"x": 2, "mode": "a", "batch": "v", "y": 2},
        {"x": 2, "mode": "b", "batch": "v", "y": 6},
    ]
    r = expand_representation(
        cases, outcome="y", coordinates=["x"],
        candidates=["mode", "batch"],
        costs={"mode": .25, "batch": 1.0},
    )
    assert r["status"] == "expanded"
    assert r["add_coordinate"] == "mode"
    assert r["gain_bits"] > 0
    assert r["residual_uncertainty_bits"] == 0.0


def test_representation_expansion_does_not_add_variable_when_coordinates_suffice():
    cases = [
        {"x": 1, "mode": "a", "y": 1},
        {"x": 2, "mode": "b", "y": 2},
        {"x": 3, "mode": "a", "y": 3},
    ]
    r = expand_representation(
        cases, outcome="y", coordinates=["x"], candidates=["mode"]
    )
    assert r["status"] == "mechanism"
    assert r["baseline_uncertainty_bits"] == 0.0


def test_representation_expansion_requests_new_observable_when_blind():
    cases = [
        {"x": 1, "batch": "same", "y": 1},
        {"x": 1, "batch": "same", "y": 3},
    ]
    r = expand_representation(
        cases, outcome="y", coordinates=["x"], candidates=["batch"]
    )
    assert r["status"] == "blind"
    assert r["baseline_uncertainty_bits"] > 0


def test_surprise_routes_to_representation_expansion(tmp_path: Path):
    l = Ledger(tmp_path / "x.jsonl")
    k = Kernel(tmp_path / "key")
    r = l.put("resolution", {"surprise": True})
    fs = frontier(l, k)
    assert any(t.verb == "expand" and t.target == r.id for t in fs)

    rep = l.put("representation", {
        "status": "expanded",
        "coordinates": ["x"],
        "next_coordinates": ["x", "mode"],
        "gain_bits": 1.0,
    }, {"surprise": [r.id]})
    fs = frontier(l, k)
    assert not any(t.verb == "expand" and t.target == r.id for t in fs)
    assert any(t.verb == "promote" and t.target == rep.id for t in fs)

    w = promote(l, rep, {"coordinates": ["x", "mode"]})
    assert w.kind == "world"
    assert not any(t.verb == "promote" and t.target == rep.id for t in frontier(l, k))


def test_representation_failure_routes_differently_for_aliasing_and_mechanism(tmp_path: Path):
    l = Ledger(tmp_path / "x.jsonl")
    k = Kernel(tmp_path / "key")
    blind = l.put("representation", {"status": "blind"})
    mechanism = l.put("representation", {"status": "mechanism"})
    fs = frontier(l, k)
    assert any(t.verb == "invent" and t.target == blind.id for t in fs)
    assert any(t.verb == "rethink" and t.target == mechanism.id for t in fs)


def test_instrument_invention_can_discover_interaction_missed_by_raw_channels():
    cases = [
        {"x": 1, "a": 0, "b": 0, "y": False},
        {"x": 1, "a": 0, "b": 1, "y": True},
        {"x": 1, "a": 1, "b": 0, "y": True},
        {"x": 1, "a": 1, "b": 1, "y": False},
    ]
    # Neither a nor b individually resolves the aliasing.
    rep = expand_representation(
        cases, outcome="y", coordinates=["x"], candidates=["a", "b"]
    )
    assert rep["status"] == "blind"

    instrument = invent_instrument(
        cases, outcome="y", coordinates=["x"], channels=["a", "b"],
        costs={"a": .1, "b": .1},
    )
    assert instrument is not None
    assert instrument["status"] == "proposed"
    assert instrument["expr"]["op"] in {"xor", "eq"}  # both refine XOR state perfectly
    assert instrument["residual_uncertainty_bits"] == 0.0
    assert instrument["gain_bits"] == 1.0


def test_mechanism_synthesis_expands_from_linear_coordinate_to_square_law():
    cases = [
        {"x": -2, "y": 4},
        {"x": -1, "y": 1},
        {"x": 0, "y": 0},
        {"x": 1, "y": 1},
        {"x": 2, "y": 4},
    ]
    m = synthesize_mechanism(cases, outcome="y", coordinates=["x"])
    assert m is not None
    assert m["status"] == "proposed"
    assert m["feature"]["op"] == "square"
    assert abs(m["scale"] - 1.0) < 1e-12
    assert abs(m["bias"]) < 1e-12
    assert m["rmse"] < 1e-12


def test_frontier_validates_then_adopts_instrument(tmp_path: Path):
    l = Ledger(tmp_path / "x.jsonl")
    k = Kernel(tmp_path / "key")
    blind = l.put("representation", {"status": "blind", "coordinates": ["x"]})
    assert any(t.verb == "invent" and t.target == blind.id for t in frontier(l, k))

    inst = l.put("instrument", {
        "status": "proposed", "name": "xor(a,b)", "spec": "unused",
    }, {"representation": [blind.id]})
    fs = frontier(l, k)
    assert not any(t.verb == "invent" and t.target == blind.id for t in fs)
    assert any(t.verb == "validate" and t.target == inst.id for t in fs)

    ok = tmp_path / "ok.py"
    ok.write_text("print('ok')")
    k.run(l, inst, "empirical", [sys.executable, str(ok)], inputs=[ok])
    fs = frontier(l, k)
    assert any(t.verb == "adopt" and t.target == inst.id for t in fs)

    l.put("representation", {
        "status": "expanded", "coordinates": ["x"],
        "next_coordinates": ["x", "xor(a,b)"],
    }, {"instrument": [inst.id]})
    assert not any(t.verb == "adopt" and t.target == inst.id for t in frontier(l, k))


def test_frontier_validates_then_promotes_mechanism(tmp_path: Path):
    l = Ledger(tmp_path / "x.jsonl")
    k = Kernel(tmp_path / "key")
    rep = l.put("representation", {"status": "mechanism", "coordinates": ["x"]})
    assert any(t.verb == "rethink" and t.target == rep.id for t in frontier(l, k))

    m = l.put("mechanism", {
        "status": "proposed", "law": "y=x^2", "spec": "unused",
    }, {"representation": [rep.id]})
    fs = frontier(l, k)
    assert not any(t.verb == "rethink" and t.target == rep.id for t in fs)
    assert any(t.verb == "validate" and t.target == m.id for t in fs)

    ok = tmp_path / "ok.py"
    ok.write_text("print('ok')")
    k.run(l, m, "empirical", [sys.executable, str(ok)], inputs=[ok])
    fs = frontier(l, k)
    assert any(t.verb == "promote" and t.target == m.id for t in fs)

    promote(l, m, {"mechanism": "y=x^2"})
    assert not any(t.verb == "promote" and t.target == m.id for t in frontier(l, k))


def test_independent_checker_validates_instrument_and_mechanism(tmp_path: Path):
    import recursive_discovery.check as check

    instrument_spec = {
        "kind": "instrument",
        "expr": {"op": "xor", "args": ["a", "b"]},
        "channels": ["a", "b"],
        "outcome": "y",
        "coordinates": ["x"],
        "cases": [
            {"x": 2, "a": 0, "b": 0, "y": False},
            {"x": 2, "a": 0, "b": 1, "y": True},
            {"x": 2, "a": 1, "b": 0, "y": True},
            {"x": 2, "a": 1, "b": 1, "y": False},
        ],
        "min_gain_bits": .5,
    }
    out = check.check_instrument(instrument_spec)
    assert out["gain_bits"] == 1.0

    mechanism_spec = {
        "kind": "mechanism",
        "model": {
            "feature": {"op": "square", "args": ["x"]},
            "scale": 1.0, "bias": 0.0,
        },
        "coordinates": ["x"], "outcome": "y",
        "cases": [{"x": 3, "y": 9}, {"x": 4, "y": 16}],
        "atol": 1e-12,
    }
    out = check.check_mechanism(mechanism_spec)
    assert out["max_abs_error"] == 0.0



def test_recursive_grammar_invents_three_way_parity_macro():
    cases = []
    for a in (0, 1):
        for b in (0, 1):
            for c in (0, 1):
                cases.append({"x": 1, "a": a, "b": b, "c": c, "y": bool(a ^ b ^ c)})

    g0 = base_grammar()
    g0["primitives"] = ["id", "xor"]
    assert invent_instrument(
        cases, outcome="y", coordinates=["x"], channels=["a", "b", "c"],
        grammar=g0, require_complete=True,
    ) is None

    ext = extend_grammar(
        cases, purpose="instrument", outcome="y", coordinates=["x"],
        channels=["a", "b", "c"], grammar=g0, max_depth=2,
    )
    assert ext is not None
    assert ext["macro"]["depth"] == 2
    assert "xor" in ext["macro"]["text"]

    g1 = adopt_grammar(g0, ext)
    assert g1["generation"] == 1
    idea = invent_instrument(
        cases, outcome="y", coordinates=["x"], channels=["a", "b", "c"],
        grammar=g1, require_complete=True,
    )
    assert idea is not None
    assert idea["residual_uncertainty_bits"] == 0.0
    assert idea["expr"]["args"][0] == ext["macro"]["name"]


def test_recursive_grammar_compounds_concepts_until_x16_is_shallow():
    cases = [{"x": x, "y": x ** 16} for x in (-2, -1, 0, 1, 2)]
    g = base_grammar()

    m0 = synthesize_mechanism(cases, outcome="y", coordinates=["x"], grammar=g)
    assert m0["status"] == "unresolved"
    e1 = extend_grammar(
        cases, purpose="mechanism", outcome="y", coordinates=["x"], grammar=g, max_depth=2,
    )
    assert e1 is not None
    assert e1["training"]["candidate_rmse"] < e1["training"]["baseline_rmse"]
    g = adopt_grammar(g, e1)

    m1 = synthesize_mechanism(cases, outcome="y", coordinates=["x"], grammar=g)
    assert m1["status"] == "unresolved"
    e2 = extend_grammar(
        cases, purpose="mechanism", outcome="y", coordinates=["x"], grammar=g, max_depth=2,
    )
    assert e2 is not None
    assert e2["training"]["candidate_rmse"] == 0.0
    g = adopt_grammar(g, e2)

    m2 = synthesize_mechanism(cases, outcome="y", coordinates=["x"], grammar=g)
    assert g["generation"] == 2
    assert m2["status"] == "proposed"
    assert m2["rmse"] == 0.0
    assert m2["feature"]["op"] == "id"
    assert m2["feature"]["args"][0] == e2["macro"]["name"]


def test_frontier_validates_and_adopts_grammar_extension(tmp_path: Path):
    l = Ledger(tmp_path / "x.jsonl")
    k = Kernel(tmp_path / "key")
    rep = l.put("representation", {"status": "mechanism"})
    g = l.put("grammar", base_grammar(), {"representation": [rep.id]})
    ext = l.put("grammar_extension", {
        "status": "proposed", "macro": {"name": "g1", "expr": {"op": "square", "args": ["x"]}},
    }, {"representation": [rep.id], "grammar": [g.id]})

    fs = frontier(l, k)
    assert any(t.verb == "validate" and t.target == ext.id for t in fs)
    assert not any(t.verb == "rethink" and t.target == rep.id for t in fs)

    ok = tmp_path / "ok.py"
    ok.write_text("print('ok')")
    k.run(l, ext, "empirical", [sys.executable, str(ok)], inputs=[ok])
    fs = frontier(l, k)
    assert any(t.verb == "adopt" and t.target == ext.id for t in fs)

    g1 = l.put("grammar", {"generation": 1, "macros": [], "primitives": []}, {
        "extension": [ext.id], "parent": [g.id], "representation": [rep.id],
    })
    fs = frontier(l, k)
    assert not any(t.verb == "adopt" and t.target == ext.id for t in fs)
    assert any(t.verb == "rethink" and t.target == rep.id for t in fs)


def test_independent_checker_validates_recursive_grammar_extension():
    import recursive_discovery.check as check

    g = base_grammar()
    g["primitives"] = ["id", "xor"]
    train = []
    heldout = []
    for a in (0, 1):
        for b in (0, 1):
            for c in (0, 1):
                train.append({"x": 1, "a": a, "b": b, "c": c, "y": bool(a ^ b ^ c)})
                heldout.append({"x": 2, "a": a, "b": b, "c": c, "y": bool(a ^ b ^ c)})
    ext = extend_grammar(
        train, purpose="instrument", outcome="y", coordinates=["x"],
        channels=["a", "b", "c"], grammar=g, max_depth=2,
    )
    out = check.check_grammar_extension({
        "kind": "grammar_extension", "grammar": g,
        "extension": ext, "cases": heldout, "min_gain_bits": .5,
    })
    assert out["gain_bits"] == 1.0
    assert out["residual_bits"] == 0.0
