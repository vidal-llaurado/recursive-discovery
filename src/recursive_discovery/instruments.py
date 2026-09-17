"""Workbench instruments, both built-in and model-defined.

Built-ins cover standard numerical and symbolic diagnostics. Models may also define expression
instruments during research; those definitions persist in the ledger and become available to
later Workbench instances.
"""
from __future__ import annotations

from pathlib import Path
import json
import re
import sys
from typing import Any

from .core import Artifact, Kernel, Ledger


SPECS = {
    "symbolic": {
        "description": "SymPy workbench: simplify/factor/expand/differentiate/integrate/solve/limit/series.",
        "schema": {"op": "simplify|factor|expand|diff|integrate|solve|limit|series",
                   "expr": "expression", "symbols": ["x", "y"], "var": "x"},
    },
    "falsify": {
        "description": "Bounded global numerical counterexample search for a symbolic relation.",
        "schema": {"lhs": "expression", "rhs": "expression", "relation": "<=|>=|==",
                   "variables": ["x"], "bounds": {"x": [-1, 1]}},
    },
    "matrix": {
        "description": "Spectrum, singular values, condition number, symmetry and non-normality.",
        "schema": {"matrix": [[1, 0], [0, 1]]},
    },
    "sensitivity": {
        "description": "Local gradients and dimensionless elasticities of a symbolic expression.",
        "schema": {"expr": "expression", "variables": ["x"], "point": {"x": 1.0}},
    },
    "hessian": {
        "description": "Symbolic gradient/Hessian plus curvature spectrum at an optional point.",
        "schema": {"expr": "x**2+y**2", "variables": ["x", "y"],
                   "point": {"x": 0.0, "y": 0.0}},
    },
    "dynamics": {
        "description": "Linear transient/stability analysis or nonlinear-map Jacobian/Lyapunov analysis.",
        "schema": {
            "mode": "linear|map",
            "A": [[0.9, 0.0], [0.0, 0.8]],
            "or_map": {"variables": ["x"], "map": ["0.9*x"], "initial": [1.0]},
        },
    },
    "scaling": {
        "description": "Fit linear or log-log scaling exponents from tabular empirical rows.",
        "schema": {"rows": [{"x": 1, "y": 2}], "target": "y",
                   "features": ["x"], "log": True},
    },
    "timeseries": {
        "description": "Trend, autocorrelation and dominant-frequency diagnostics for a trajectory.",
        "schema": {"values": [0.0, 1.0, 0.0, -1.0], "dt": 1.0, "max_lag": 20},
    },
}


def install_instruments(ledger: Ledger) -> list[Artifact]:
    runner = Path(__file__).with_name("instrument_exec.py").resolve()
    out = []
    for name, meta in SPECS.items():
        out.append(ledger.put("tool", {
            "name": name,
            "lane": "instrument",
            "verb": "analyze",
            "semantics": f"scientific_instrument:{name}",
            "description": meta["description"],
            "schema": meta["schema"],
            "argv": [sys.executable, str(runner), name, "{spec}"],
            "inputs": [str(runner), "{spec}"],
        }, by="instrument-portfolio"))
    return out


def _safe_name(name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_.-]+", "-", name.strip()).strip("-")
    if not name or len(name) > 80:
        raise ValueError("invalid instrument name")
    return name


class Workbench:
    def __init__(self, ledger: Ledger, kernel: Kernel, *, root: str | Path):
        self.ledger, self.kernel, self.root = ledger, kernel, Path(root)
        self.work = self.root / "work"
        self.work.mkdir(parents=True, exist_ok=True)
        self.tools = {t.data["name"]: t for t in install_instruments(ledger)}
        self.dynamic = {
            x.data["name"]: x for x in ledger.all("instrument_definition")
            if x.data.get("status") == "active"
        }

    def describe(self) -> list[dict[str, Any]]:
        builtins = [{
            "name": name, "kind": "builtin",
            "description": tool.data["description"], "schema": tool.data["schema"],
        } for name, tool in sorted(self.tools.items())]
        dynamic = [{
            "name": name, "kind": "learned",
            "description": d.data.get("description", ""),
            "schema": {"inputs": d.data["inputs"], "expression": d.data["expression"]},
            "definition_id": d.id,
        } for name, d in sorted(self.dynamic.items())]
        return builtins + dynamic

    def _dynamic_paths(self, definition: Artifact) -> tuple[Path, Path]:
        runner = Path(__file__).with_name("dynamic_instrument.py").resolve()
        path = self.work / f"definition-{definition.id}.json"
        path.write_text(json.dumps(definition.data, sort_keys=True))
        return runner, path

    def define_expression(
        self,
        *,
        name: str,
        description: str,
        inputs: list[str],
        expression: str,
        tests: list[dict[str, Any]],
        target: Artifact,
        by: str = "research-model",
    ) -> Artifact:
        """Validate a safe expression instrument and persist it for future researchers.

        Validation only checks executable semantics against declared interface tests. It does
        not make the scientific meaning of the diagnostic authoritative.
        """
        name = _safe_name(name)
        if name in SPECS:
            raise ValueError("cannot shadow built-in instrument")
        data = {
            "name": name, "description": description[:2000],
            "runtime": "expression", "inputs": list(inputs),
            "expression": expression, "tests": list(tests),
            "status": "proposal",
        }
        proposal = self.ledger.put(
            "instrument_definition", data,
            {"target": [target.id]}, by=by,
        )
        runner, def_path = self._dynamic_paths(proposal)
        spec_path = self.work / f"validate-{proposal.id}.json"
        spec_path.write_text("{}")
        tool = self.ledger.put("tool", {
            "name": f"validate:{name}",
            "lane": "instrument",
            "verb": "validate",
            "semantics": "instrument_interface_validation",
            "argv": [sys.executable, str(runner), "validate", str(def_path), str(spec_path)],
            "inputs": [str(runner), str(def_path), str(spec_path)],
        }, by="workbench")
        evidence = self.kernel.use(
            self.ledger, tool, proposal, {}, cwd=self.root, seed=0,
        )
        if evidence.data.get("verdict") != "pass":
            raise ValueError(f"instrument validation failed: {evidence.data.get('stderr','')[-1000:]}")
        active = self.ledger.put(
            "instrument_definition",
            {**data, "status": "active"},
            {"proposal": [proposal.id], "evidence": [evidence.id], "target": [target.id]},
            by="workbench",
        )
        self.dynamic[name] = active
        return active

    def call(
        self,
        name: str,
        spec: dict[str, Any],
        *,
        target: Artifact,
        by: str = "research-model",
    ) -> tuple[Artifact, Artifact]:
        if name in self.tools:
            tool = self.tools[name]
            call = self.ledger.put(
                "instrument_call", {"instrument": name, "spec": spec},
                {"target": [target.id]}, by=by,
            )
            path = self.work / f"instrument-{call.id}.json"
            path.write_text(json.dumps(spec, sort_keys=True))
            evidence = self.kernel.use(
                self.ledger, tool, call, {"spec": str(path)}, cwd=self.root, seed=0,
            )
        elif name in self.dynamic:
            definition = self.dynamic[name]
            call = self.ledger.put(
                "instrument_call",
                {"instrument": name, "spec": spec, "definition": definition.id},
                {"target": [target.id], "definition": [definition.id]}, by=by,
            )
            runner, def_path = self._dynamic_paths(definition)
            spec_path = self.work / f"instrument-{call.id}.json"
            spec_path.write_text(json.dumps(spec, sort_keys=True))
            tool = self.ledger.put("tool", {
                "name": name, "lane": "instrument", "verb": "analyze",
                "semantics": "learned_expression_instrument",
                "description": definition.data.get("description", ""),
                "argv": [sys.executable, str(runner), "call", str(def_path), str(spec_path)],
                "inputs": [str(runner), str(def_path), str(spec_path)],
            }, {"definition": [definition.id]}, by="workbench")
            evidence = self.kernel.use(
                self.ledger, tool, call, {}, cwd=self.root, seed=0,
            )
        else:
            raise KeyError(name)

        try:
            value = json.loads(evidence.data.get("stdout", "").splitlines()[-1])
        except Exception:
            value = {"raw": evidence.data.get("stdout", "")}
        result = self.ledger.put(
            "instrument_result",
            {"instrument": name, "value": value},
            {"call": [call.id], "evidence": [evidence.id], "target": [target.id]},
            by="workbench",
        )
        return evidence, result
