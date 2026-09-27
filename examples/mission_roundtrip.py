"""Run an offline mission, failed check, capsule export and reviewed response.

The kernel executes the check; the policy and external reply are scripted.
Use a new workspace and keep its kernel.key private.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile

from recursive_discovery.agenda import available_work
from recursive_discovery.capsules import (
    accept_response, changes_since, compile_capsule, export_capsule,
    load_capsule, receive_response,
)
from recursive_discovery.context import authority
from recursive_discovery.protocols import declare_check, interpret_checks
from recursive_discovery.core import Kernel, Task
from recursive_discovery.investigation import (
    Scope, create_mission, create_program, include, open_branch, request,
    set_attention, supersede,
)
from recursive_discovery.runtime import Runtime
from recursive_discovery.replay import attempt_history
from recursive_discovery.schema import define_schema, describe
from recursive_discovery.store import BlobStore, SQLiteLedger


def run(root: Path) -> dict:
    if root.exists() and any(root.iterdir()):
        raise FileExistsError(f"refusing to reuse a nonempty workspace: {root}")
    root.mkdir(parents=True, exist_ok=True)
    ledger = SQLiteLedger(root / "science.sqlite3")
    kernel, blobs = Kernel(root / "kernel.key"), BlobStore(root / "blobs")
    try:
        definition = ledger.put("note", {
            "text": "Synthetic example. A(r)=[[1,r],[r,1]]. Its determinant is 1-r^2. "
                    "The inverse formula requires r^2 != 1; the endpoints r=-1 and r=1 are singular."
        }, by="demo-author")
        descriptor = define_schema(ledger, "admissible parameter domain", fields={
            "condition": {"type": "string", "required": True},
            "explanation": {"type": "string"},
        }, references={"definition": {"kinds": ["note"], "min": 1, "context": True}},
            display=["condition", "explanation"])
        check_path = root / "inverse_check.py"
        check_path.write_text(
            "r = 1.0\n"
            "determinant = 1.0-r*r\n"
            "print({'r': r, 'determinant': determinant})\n"
            "assert determinant != 0, 'endpoint is singular'\n", encoding="utf-8")
        claim = ledger.put("claim", {
            "statement": "A(r) is invertible for every r in [-1,1].",
            "check_policy": "explicit",
        }, {"definition": [definition.id]}, by="demo-author")
        program = create_program(ledger, "Understand the domain of a reusable matrix diagnostic")
        mission = create_mission(ledger, "Repair the proposed domain without losing the failed case",
            program=program.id, targets=[claim.id], required=[definition.id],
            guidance="Retain the singular endpoint as negative evidence, not as a source to forget.",
            deliverables="Propose a restricted domain and cite the supplied definition; do not claim a proof was executed.")
        failed_branch = open_branch(ledger, mission.id, "Use the entire closed interval", targets=[claim.id])
        include(ledger, mission.id, [claim.id], branch=failed_branch.id)
        tool = ledger.put("tool", {"name": "endpoint-check", "lane": "math",
            "semantics": "executable_endpoint_test", "argv": [sys.executable, "{path}"],
            "inputs": ["{path}"]}, by="demo-author")
        experiment = declare_check(ledger, mission.id, claim.id,
            "Check the endpoint r=1 using the declared determinant formula",
            tool=tool.id, values={"path": str(check_path.resolve())}, branch=failed_branch.id)
        runtime = Runtime(ledger, kernel, root=root)
        def select_declared_experiment(encoded):
            # Scripted policy for this example.
            page = json.loads(encoded)
            item = next(x for x in page["items"] if x["target"] == experiment.id)
            return json.dumps({"key": item["key"], "reason": "Execute the declared endpoint test"})
        run = runtime.run(mission=mission.id, model=select_declared_experiment, max_steps=1)
        assert run["status"] == "step_limit"
        evidence = ledger.children(experiment.id, "evidence", "target")[0]
        assert kernel.verify(evidence) and evidence.data["verdict"] == "fail"
        history = attempt_history(ledger, kernel, targets=[claim.id])
        assert history["total"] == 1
        interpretation = interpret_checks(ledger, kernel, claim.id, [evidence.id],
            "The declared endpoint has determinant zero; this supplied test contradicts the closed-interval proposal.",
            by="demo-author")
        include(ledger, mission.id, [evidence.id, interpretation.id], branch=failed_branch.id)
        set_attention(ledger, failed_branch.id, mission.id, "dormant", expected=[],
            reason="The closed-interval claim fails at the supplied endpoint.", support=[evidence.id],
            revisit="Revisit when the domain or inverse notion changes.")

        # Include the failed run from the dormant branch.
        capsule = compile_capsule(ledger, kernel, blobs, mission.id, max_chars=30000,
            selections=[{"id": x.id} for x in (definition, claim, experiment, evidence, interpretation, descriptor)])
        exported = export_capsule(ledger, blobs, capsule.id, root / "export")
        manifest, _ = load_capsule(ledger, blobs, capsule.id)
        handles = {item["id"]: item["handle"] for item in manifest["items"]}
        start = definition.data["text"].index("The inverse formula")
        synthetic_reply = json.dumps({"capsule": capsule.data["delivery_id"], "contributions": [{
            "object_kind": "parameter_domain", "commitment": "Investigate the open interval instead.",
            "payload": {"condition": "-1 < r < 1", "explanation": "This proposal excludes the known singular endpoints."},
            "links": {"definition": [handles[definition.id]], "schema": [handles[descriptor.id]],
                      "qualifies": [handles[claim.id]]},
            "citations": [{"handle": handles[definition.id], "field": "text",
                           "start": start, "end": len(definition.data["text"])}],
        }]}, ensure_ascii=False, indent=2)
        (root / "synthetic-response.json").write_text(synthetic_reply, encoding="utf-8")
        response, outcome = receive_response(ledger, blobs, capsule.id, synthetic_reply,
                                              provider="synthetic-demo", model="no-model")
        assert outcome.data["status"] == "proposals"
        # Review detects new references to previously delivered artifacts.
        new_note = ledger.put("note", {"text": "Also investigate numerical conditioning near the endpoints."},
                              {"target": [claim.id]}, by="demo-author")
        successor = open_branch(ledger, mission.id, "Restricted domain and conditioning", targets=[claim.id])
        change = changes_since(ledger, blobs, capsule.id)
        assert new_note.id in change["touching_delivery"]
        accepted = accept_response(ledger, blobs, response.id, indices=[0],
            reviewed_revision=change["revision"], reviewed_changes=change["review_digest"],
            reason="Reviewed the endpoint failure and the new conditioning question; admit only as a proposal.",
            branch=successor.id)[0]
        preference = supersede(ledger, claim.id, accepted.id, mission.id,
            purpose="domain selection for the next diagnostic",
            reason="Prefer investigating the restricted domain; this does not establish a theorem.",
            support=[evidence.id])
        request(ledger, mission.id, "Check invertibility on the restricted domain and study conditioning",
                targets=[accepted.id], branch=successor.id)
        assert describe(ledger, accepted)[0]["conforms"]
        assert authority(accepted, ledger, kernel) == "proposal_or_state"
        assert ledger.get(evidence.id).refs["target"] == (experiment.id,)
        assert ledger.get(experiment.id).refs["checks"] == (claim.id,)
        return {
            "workspace": str(root), "synthetic_external_reply": True,
            "mission": mission.id, "capsule": capsule.id, "export": exported,
            "failed_check_verified": kernel.verify(evidence),
            "failure_verdict": evidence.data["verdict"],
            "attempt_history_items": history["total"],
            "interpretation_authority": authority(interpretation, ledger, kernel),
            "dormant_branch": failed_branch.id, "successor_branch": successor.id,
            "incoming_change_detected": True, "accepted_proposal": accepted.id,
            "accepted_authority": authority(accepted, ledger, kernel),
            "schema_conforms": describe(ledger, accepted)[0]["conforms"],
            "scoped_preference": preference.id,
            "available_work": [w.row() for w in available_work(ledger, kernel, Scope(ledger, mission.id))["eligible"]],
        }
    finally:
        ledger.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, help="New or empty workspace; otherwise a temporary directory is created.")
    args = parser.parse_args()
    root = args.workspace.resolve() if args.workspace else Path(tempfile.mkdtemp(prefix="rd-mission-demo-"))
    try:
        result = run(root)
    except (OSError, ValueError) as e:
        parser.exit(1, f"mission demo failed: {e}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
