"""Checker discovery for Lean, Z3, cvc5, SymPy, and executable Python checks.

Installed tools are discovered here; every run still goes through Kernel for signing.
"""
from __future__ import annotations

from pathlib import Path
import importlib.util
import shutil
import sys
from typing import Any

from .core import Artifact, Kernel, Ledger


def discover_math() -> list[dict[str, Any]]:
    """Return available consequence engines as ordinary tool descriptors."""
    here = Path(__file__).resolve().parent
    tools = [{
        "name": "python-check",
        "lane": "math",
        "verb": "verify",
        "semantics": "executable_mathematical_check",
        "accepts": ["python"],
        "argv": [sys.executable, "{path}"],
        "inputs": ["{path}"],
    }]

    if importlib.util.find_spec("sympy") is not None:
        checker = here / "sympy_check.py"
        tools.append({
            "name": "sympy",
            "lane": "math",
            "verb": "verify",
            "semantics": "symbolic_consequence",
            "accepts": ["sympy"],
            "argv": [sys.executable, str(checker), "{path}"],
            "inputs": [str(checker), "{path}"],
        })

    lean = shutil.which("lean")
    if lean:
        tools.append({
            "name": "lean",
            "lane": "math",
            "verb": "verify",
            "semantics": "formal_proof_check",
            "accepts": ["lean"],
            "argv": [lean, "{path}"],
            "inputs": ["{path}"],
        })

    solver = here / "solver.py"
    for name in ("z3", "cvc5"):
        if shutil.which(name):
            tools.append({
                "name": name,
                "lane": "math",
                "verb": "verify",
                "semantics": "smt_consequence",
                "accepts": ["smt2"],
                "argv": [sys.executable, str(solver), name, "{expected}", "{path}"],
                "inputs": [str(solver), "{path}"],
            })
    return tools


def install_math(ledger: Ledger) -> list[Artifact]:
    return [ledger.put("tool", spec, by="math-portfolio") for spec in discover_math()]


def tools_for(claim: Artifact, tools: list[Artifact]) -> list[Artifact]:
    formalism = claim.data.get("formalism")
    if not formalism:
        return []
    return [t for t in tools if formalism in t.data.get("accepts", [])]


def values_for(claim: Artifact) -> dict[str, str]:
    out = {"path": str(claim.data["path"])}
    if claim.data.get("formalism") == "smt2":
        out["expected"] = str(claim.data.get("expected", "unsat"))
    return out


def verify_claim(
    ledger: Ledger,
    kernel: Kernel,
    claim: Artifact,
    tools: list[Artifact] | None = None,
    *,
    cwd: str | Path = ".",
) -> list[Artifact]:
    """Run every compatible installed consequence engine; no engine gets special authority."""
    portfolio = tools or install_math(ledger)
    selected = tools_for(claim, portfolio)
    if not selected:
        return []
    return [kernel.use(ledger, tool, claim, values_for(claim), cwd=cwd, seed=0) for tool in selected]
