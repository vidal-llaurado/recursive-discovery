"""Declared checks and researcher interpretations of their execution results."""
from __future__ import annotations

from typing import Iterable
from .core import Artifact, Kernel, Ledger


def declare_check(ledger: Ledger, mission: str, target: str, protocol: str, *, tool: str,
                  values: dict | None = None, inputs: Iterable[str] = (),
                  seeds: Iterable[int] = (), branch: str | None = None,
                  by: str = "human") -> Artifact:
    """Declare an experiment that checks a contribution.

    Claims opt into this routing with data.check_policy="explicit". The declaration
    preserves existing claims and requires a separately registered tool for execution.
    """
    from .investigation import Scope, _ids, _put, _text

    scope = Scope(ledger, mission)
    capability = ledger.get(tool)
    if capability.kind != "tool" or not isinstance(capability.data.get("lane"), str):
        raise ValueError("check requires a registered tool with an execution lane")
    if not scope.allowed(target) or not scope.tool_allowed(tool):
        raise PermissionError("check target or tool outside mission policy")
    if branch and (branch not in scope.branches or not scope.branch_active(branch)):
        raise ValueError("check branch must be active in the current mission")
    values = {} if values is None else values
    if not isinstance(values, dict):
        raise TypeError("check values must be an object")
    inputs = _ids(ledger, inputs)
    if any(not scope.allowed(aid) for aid in inputs):
        raise PermissionError("check input outside mission policy")
    seeds = list(seeds)
    if any(type(seed) is not int for seed in seeds):
        raise TypeError("check seeds must be integers")
    return _put(ledger, "experiment", {"protocol": _text(protocol, "protocol"),
        "values": values, "seeds": seeds, "evidence_lane": capability.data["lane"]},
        {"checks": [target], "tool": [tool], "inputs": inputs,
         "mission": [scope.root.id], "branch": [branch] if branch else []}, by)


def interpret_checks(ledger: Ledger, kernel: Kernel, target: str, evidence: Iterable[str],
                     statement: str, *, by: str = "research-model") -> Artifact:
    """Record a researcher interpretation of authenticated check runs.

    Each cited run must address a declared check of this target. Later runs need a new
    interpretation that cites them.
    """
    from .investigation import _ids, _put, _text

    evidence = _ids(ledger, evidence)
    if not evidence:
        raise ValueError("at least one authenticated check result is required")
    experiments = []
    for eid in evidence:
        e = ledger.get(eid)
        if e.kind != "evidence" or not kernel.verify(e):
            raise ValueError("interpretation requires authenticated execution records")
        checked = [ledger.get(x) for x in e.refs.get("target", ())]
        matching = [x for x in checked if x.kind == "experiment" and target in x.refs.get("checks", ())]
        if not matching:
            raise ValueError("evidence does not address a declared check of this target")
        experiments.extend(x.id for x in matching)
    return _put(ledger, "resolution", {"check_interpretation": 1,
        "statement": _text(statement, "statement"),
        "qualification": "Researcher interpretation; execution provenance is not scientific adjudication."},
        {"target": [target], "evidence": evidence, "experiments": experiments}, by)
