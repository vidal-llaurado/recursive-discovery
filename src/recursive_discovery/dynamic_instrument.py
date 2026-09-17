"""Expression-defined instruments.

Definitions use a small arithmetic/numerical language rather than arbitrary Python, so models
can define instruments during research without obtaining host-code execution.
"""
from __future__ import annotations

import ast
import io
import json
import tokenize
import math
from pathlib import Path
import sys
from typing import Any

import numpy as np


FUNCS = {
    "sqrt": np.sqrt, "exp": np.exp, "log": np.log, "log1p": np.log1p,
    "sin": np.sin, "cos": np.cos, "tan": np.tan, "tanh": np.tanh,
    "abs": np.abs, "mean": np.mean, "std": np.std, "var": np.var,
    "sum": np.sum, "min": np.min, "max": np.max, "norm": np.linalg.norm,
    "dot": np.dot, "clip": np.clip,
}
CONSTANTS = {"pi": math.pi, "e": math.e}


def _value(x: Any) -> Any:
    if isinstance(x, list):
        return np.asarray(x, dtype=float)
    return x


def _eval(node: ast.AST, env: dict[str, Any]) -> Any:
    if isinstance(node, ast.Expression):
        return _eval(node.body, env)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float, bool)):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in env:
            return env[node.id]
        if node.id in CONSTANTS:
            return CONSTANTS[node.id]
        raise ValueError(f"unknown name: {node.id}")
    if isinstance(node, ast.List):
        return np.asarray([_eval(x, env) for x in node.elts], dtype=float)
    if isinstance(node, ast.Tuple):
        return tuple(_eval(x, env) for x in node.elts)
    if isinstance(node, ast.UnaryOp):
        x = _eval(node.operand, env)
        if isinstance(node.op, ast.USub): return -x
        if isinstance(node.op, ast.UAdd): return +x
        if isinstance(node.op, ast.Not): return not bool(x)
        raise ValueError("unsupported unary operator")
    if isinstance(node, ast.BinOp):
        a, b = _eval(node.left, env), _eval(node.right, env)
        if isinstance(node.op, ast.Add): return a + b
        if isinstance(node.op, ast.Sub): return a - b
        if isinstance(node.op, ast.Mult): return a * b
        if isinstance(node.op, ast.Div): return a / b
        if isinstance(node.op, ast.Pow): return a ** b
        if isinstance(node.op, ast.Mod): return a % b
        raise ValueError("unsupported binary operator")
    if isinstance(node, ast.Compare) and len(node.ops) == 1:
        a, b = _eval(node.left, env), _eval(node.comparators[0], env)
        op = node.ops[0]
        if isinstance(op, ast.Lt): return a < b
        if isinstance(op, ast.LtE): return a <= b
        if isinstance(op, ast.Gt): return a > b
        if isinstance(op, ast.GtE): return a >= b
        if isinstance(op, ast.Eq): return a == b
        if isinstance(op, ast.NotEq): return a != b
        raise ValueError("unsupported comparison")
    if isinstance(node, ast.IfExp):
        return _eval(node.body if bool(_eval(node.test, env)) else node.orelse, env)
    if isinstance(node, ast.Subscript):
        base = _eval(node.value, env)
        idx = _eval(node.slice, env) if not isinstance(node.slice, ast.Slice) else slice(
            _eval(node.slice.lower, env) if node.slice.lower else None,
            _eval(node.slice.upper, env) if node.slice.upper else None,
            _eval(node.slice.step, env) if node.slice.step else None,
        )
        return base[idx]
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        name = node.func.id
        if name not in FUNCS:
            raise ValueError(f"function not allowed: {name}")
        args = [_eval(x, env) for x in node.args]
        kwargs = {kw.arg: _eval(kw.value, env) for kw in node.keywords}
        return FUNCS[name](*args, **kwargs)
    raise ValueError(f"unsupported syntax: {type(node).__name__}")


def evaluate(expression: str, inputs: dict[str, Any], allowed_inputs: list[str]) -> Any:
    extra = set(inputs) - set(allowed_inputs)
    missing = set(allowed_inputs) - set(inputs)
    if extra:
        raise ValueError(f"undeclared inputs: {sorted(extra)}")
    if missing:
        raise ValueError(f"missing inputs: {sorted(missing)}")

    # Scientific variable names often collide with Python keywords (notably lambda).
    # Rewrite declared names token-wise into inert aliases before parsing.
    aliases = {name: f"__v{i}" for i, name in enumerate(allowed_inputs)}
    toks = []
    for tok in tokenize.generate_tokens(io.StringIO(expression).readline):
        if tok.type == tokenize.NAME and tok.string in aliases:
            tok = tokenize.TokenInfo(tok.type, aliases[tok.string], tok.start, tok.end, tok.line)
        toks.append(tok)
    rewritten = tokenize.untokenize(toks)
    tree = ast.parse(rewritten, mode="eval")
    env = {aliases[k]: _value(v) for k, v in inputs.items()}
    return _eval(tree, env)


def jsonable(x: Any) -> Any:
    if isinstance(x, np.ndarray):
        return x.tolist()
    if isinstance(x, np.generic):
        return x.item()
    if isinstance(x, tuple):
        return [jsonable(v) for v in x]
    if isinstance(x, list):
        return [jsonable(v) for v in x]
    if isinstance(x, dict):
        return {str(k): jsonable(v) for k, v in x.items()}
    return x


def close(a: Any, b: Any, atol: float, rtol: float) -> bool:
    aa, bb = np.asarray(a), np.asarray(b)
    if aa.shape != bb.shape:
        return False
    if aa.dtype.kind in "biufc" and bb.dtype.kind in "biufc":
        return bool(np.allclose(aa, bb, atol=atol, rtol=rtol))
    return bool(np.all(aa == bb))


def main():
    if len(sys.argv) != 4:
        raise SystemExit("dynamic_instrument.py <validate|call> <definition.json> <spec.json>")
    mode, def_path, spec_path = sys.argv[1:]
    definition = json.loads(Path(def_path).read_text())
    spec = json.loads(Path(spec_path).read_text())
    expression = str(definition["expression"])
    inputs = list(definition["inputs"])

    if mode == "call":
        print(json.dumps({"value": jsonable(evaluate(expression, spec, inputs))}, sort_keys=True))
        return

    if mode != "validate":
        raise ValueError(mode)
    tests = list(definition.get("tests", []))
    if not tests:
        raise ValueError("at least one interface test required")
    rows = []
    for test in tests:
        value = jsonable(evaluate(expression, test["inputs"], inputs))
        atol = float(test.get("atol", 1e-9))
        rtol = float(test.get("rtol", 1e-9))
        ok = close(value, test["expected"], atol, rtol)
        rows.append({"ok": ok, "value": value, "expected": test["expected"]})
        if not ok:
            raise AssertionError(rows[-1])
    print(json.dumps({"validated": True, "tests": rows}, sort_keys=True))


if __name__ == "__main__":
    main()
