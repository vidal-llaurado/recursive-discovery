"""Small semantic consequence adapter for SymPy claim specifications."""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> None:
    try:
        import sympy as sp
    except Exception as e:
        raise SystemExit(f"SymPy unavailable: {e}")

    spec = json.loads(Path(sys.argv[1]).read_text())
    names = list(spec.get("symbols", []))
    symbols = {name: sp.Symbol(name) for name in names}
    kind = spec.get("kind", "identity")

    if kind == "identity":
        lhs = sp.sympify(spec["lhs"], locals=symbols)
        rhs = sp.sympify(spec["rhs"], locals=symbols)
        ok = sp.simplify(lhs - rhs) == 0
        print(json.dumps({"kind": kind, "proved": bool(ok)}))
        raise SystemExit(0 if ok else 1)

    if kind == "zero":
        expr = sp.sympify(spec["expr"], locals=symbols)
        ok = sp.simplify(expr) == 0
        print(json.dumps({"kind": kind, "proved": bool(ok)}))
        raise SystemExit(0 if ok else 1)

    if kind == "solve":
        symbol = symbols[spec["symbol"]]
        expr = sp.sympify(spec["expr"], locals=symbols)
        actual = sorted(map(str, sp.solve(expr, symbol)))
        expected = sorted(map(str, spec.get("expected", [])))
        ok = actual == expected
        print(json.dumps({"kind": kind, "actual": actual, "expected": expected, "proved": ok}))
        raise SystemExit(0 if ok else 1)

    raise SystemExit(f"unsupported SymPy claim kind: {kind}")


if __name__ == "__main__":
    main()
