from pathlib import Path
import json
import sys

from recursive_discovery import (
    Kernel, Ledger, Task,
    authority, compile_context,
    discover_math, install_math, tools_for,
    parse_arxiv, remember,
    replicate, summarize, contrast,
)


ARXIV_FIXTURE = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2601.00001v1</id>
    <updated>2026-01-02T00:00:00Z</updated>
    <published>2026-01-01T00:00:00Z</published>
    <title>  A Small   Research Paper </title>
    <summary> A compact abstract. </summary>
    <author><name>A. Researcher</name></author>
    <category term="cs.LG"/>
    <link href="https://arxiv.org/abs/2601.00001" rel="alternate"/>
    <link title="pdf" href="https://arxiv.org/pdf/2601.00001" rel="related"/>
  </entry>
</feed>"""


def test_arxiv_parser_and_sources_are_not_consequences(tmp_path: Path):
    rows = parse_arxiv(ARXIV_FIXTURE)
    assert rows[0]["title"] == "A Small Research Paper"
    assert rows[0]["authors"] == ["A. Researcher"]

    l = Ledger(tmp_path / "x.jsonl")
    k = Kernel(tmp_path / "key")
    s = l.put("study", {"q": "q"})
    _, sources = remember(l, "query", rows, refs={"study": [s.id]})
    assert sources[0].kind == "source"
    assert authority(sources[0], l, k) == "retrieved_source"


def test_math_portfolio_always_has_python_and_selects_by_formalism(tmp_path: Path):
    specs = discover_math()
    assert any(x["name"] == "python-check" for x in specs)
    l = Ledger(tmp_path / "x.jsonl")
    tools = install_math(l)
    c = l.put("claim", {"formalism": "python", "path": "proof.py"})
    selected = tools_for(c, tools)
    assert selected and selected[0].data["name"] == "python-check"


def test_statistics_are_richer_than_a_scalar():
    s = summarize([1, 2, 3, 4, 5])
    assert s["n"] == 5
    assert s["mean"] == 3
    assert s["sd"] > 0
    assert s["ci_low"] < s["mean"] < s["ci_high"]
    assert s["median"] == 3
    assert s["q25"] <= s["median"] <= s["q75"]

    c = contrast([2, 3, 4], [1, 1, 1])
    assert c["difference"] > 0
    assert "standardized_effect" in c


def test_lab_replicates_signed_runs_and_freezes_declared_inputs(tmp_path: Path):
    l = Ledger(tmp_path / "science.jsonl")
    k = Kernel(tmp_path / "key")

    script = tmp_path / "exp.py"
    script.write_text(
        "import json, os\n"
        "seed=int(os.environ['RSCIENCE_SEED'])\n"
        "print(json.dumps({'metric': seed/10, 'fixed': 1.0}))\n"
    )
    tool = l.put("tool", {
        "lane": "empirical",
        "verb": "run",
        "argv": [sys.executable, "{path}"],
        "inputs": ["{path}"],
    })
    exp = l.put("experiment", {"name": "replicate"})
    out = replicate(
        l, k, tool, exp,
        values={"path": str(script)},
        seeds=[1, 2, 3, 4],
        cwd=tmp_path,
        bundle_store=tmp_path / "store",
    )

    assert len(out["evidence"]) == 4
    assert all(k.verify(e) for e in out["evidence"])
    assert len(out["measurements"]) == 4
    assert out["summary"].data["metrics"]["metric"]["n"] == 4
    assert out["bundle"] is not None

    digest = next(iter(out["bundle"].data["inputs"].values()))["sha256"]
    assert (tmp_path / "store" / "blobs" / digest).exists()


def test_proposal_consequence_boundary_is_explicit(tmp_path: Path):
    l = Ledger(tmp_path / "x.jsonl")
    k = Kernel(tmp_path / "key")
    c = l.put("claim", {"statement": "I am true", "verdict": "pass"})
    fake = l.put("evidence", {"lane": "math", "verdict": "pass", "_sig": "fake"}, {"target": [c.id]})
    assert authority(c, l, k) == "proposal_or_state"
    assert authority(fake, l, k) == "untrusted"

    ok = tmp_path / "ok.py"
    ok.write_text("print('ok')")
    real = k.run(l, c, "math", [sys.executable, str(ok)], inputs=[ok])
    assert authority(real, l, k) == "consequence"


def test_context_compiler_keeps_local_truth_and_retrieval_under_budget(tmp_path: Path):
    l = Ledger(tmp_path / "x.jsonl")
    k = Kernel(tmp_path / "key")
    study = l.put("study", {"question": "q"})
    claim = l.put("claim", {"statement": "c"}, {"study": [study.id]})
    assumption = l.put("assumption", {"text": "A"})
    # Attach the assumption through a second claim-like local edge.
    linked = l.put("note", {"text": "local"}, {"target": [claim.id], "assumption": [assumption.id]})

    ok = tmp_path / "ok.py"
    ok.write_text("print('proof checked')")
    evidence = k.run(l, claim, "math", [sys.executable, str(ok)], inputs=[ok])

    _, sources = remember(l, "q", [{
        "provider": "arxiv", "id": "x", "title": "Relevant paper",
        "abstract": "A" * 5000, "authors": ["A"], "published": "2026",
        "updated": "2026", "categories": [], "url": "u", "pdf": "",
    }], refs={"study": [study.id]})

    # Large irrelevant artifact should never consume the packet.
    l.put("note", {"blob": "Z" * 100_000}, by="noise")

    packet = compile_context(
        l, k, Task("verify", claim.id, "check it"),
        max_chars=5000, radius=3,
    )
    ids = {x["id"] for x in packet["artifacts"]}
    assert claim.id in ids
    assert evidence.id in ids
    assert sources[0].id in ids
    assert packet["used_chars"] <= packet["budget_chars"]

    auth = {x["id"]: x["authority"] for x in packet["artifacts"]}
    assert auth[evidence.id] == "consequence"
    assert auth[sources[0].id] == "retrieved_source"


def test_empirical_summary_is_derived_not_promoted_to_consequence(tmp_path: Path):
    l = Ledger(tmp_path / "x.jsonl")
    k = Kernel(tmp_path / "key")
    exp = l.put("experiment", {"name": "x"})
    script = tmp_path / "exp.py"
    script.write_text("import json\nprint(json.dumps({'m': 1.0}))\n")
    tool = l.put("tool", {
        "lane": "empirical", "argv": [sys.executable, "{path}"], "inputs": ["{path}"]
    })
    out = replicate(
        l, k, tool, exp,
        values={"path": str(script)}, seeds=[0, 1],
        cwd=tmp_path,
    )
    assert authority(out["summary"], l, k) == "derived_from_consequence"
