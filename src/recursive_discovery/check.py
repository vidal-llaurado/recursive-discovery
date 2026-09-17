"""Standalone validation of synthesized instruments, mechanisms, and grammar extensions.

Intended to run as an independent checker process, separate from the session that proposed the
artifact.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path


def value(expr, row):
    op, args = expr["op"], expr["args"]

    def get(a):
        return value(a, row) if isinstance(a, dict) else row[a]

    xs = [get(a) for a in args]
    if op == "id":
        return xs[0]
    if op == "square":
        return xs[0] * xs[0]
    if op == "xor":
        return bool(xs[0]) ^ bool(xs[1])
    if op == "mul":
        return xs[0] * xs[1]
    if op == "add":
        return xs[0] + xs[1]
    if op == "sub":
        return xs[0] - xs[1]
    if op == "eq":
        return xs[0] == xs[1]
    raise ValueError(op)


def fields(expr):
    out = set()
    for a in expr["args"]:
        if isinstance(a, dict):
            out |= fields(a)
        else:
            out.add(a)
    return out


def materialize(rows, grammar):
    rows = [dict(r) for r in rows]
    for macro in (grammar or {}).get("macros", []):
        for row in rows:
            row[macro["name"]] = value(macro["expr"], row)
    return rows


def entropy(xs):
    counts = {}
    for x in xs:
        k = repr(x)
        counts[k] = counts.get(k, 0) + 1
    n = len(xs)
    return -sum((c/n) * math.log2(c/n) for c in counts.values()) if n else 0.0


def conditional_entropy(rows, outcome, coordinates):
    groups = {}
    for row in rows:
        k = tuple(repr(row[c]) for c in coordinates)
        groups.setdefault(k, []).append(row[outcome])
    n = len(rows)
    return sum((len(ys)/n) * entropy(ys) for ys in groups.values())


def rmse(model, rows, outcome):
    errors = []
    for row in rows:
        pred = float(model["scale"]) * float(value(model["feature"], row)) + float(model["bias"])
        errors.append((pred - float(row[outcome])) ** 2)
    return math.sqrt(sum(errors) / len(errors)) if errors else 0.0


def check_instrument(spec):
    rows = materialize(spec["cases"], spec.get("grammar"))
    outcome, coords = spec["outcome"], spec["coordinates"]
    expr = spec["expr"]
    allowed = set(spec["channels"]) | {m["name"] for m in spec.get("grammar", {}).get("macros", [])}
    used = fields(expr)
    if outcome in used or not used.issubset(allowed):
        raise AssertionError("instrument reads undeclared or outcome channel")
    base = conditional_entropy(rows, outcome, coords)
    name = "__instrument__"
    for row in rows:
        row[name] = value(expr, row)
    after = conditional_entropy(rows, outcome, coords + [name])
    gain = base - after
    assert gain >= float(spec.get("min_gain_bits", 1e-12))
    if spec.get("require_complete"):
        assert after <= float(spec.get("max_residual_bits", 1e-12))
    return {"baseline_bits": base, "residual_bits": after, "gain_bits": gain}


def check_mechanism(spec):
    rows = materialize(spec["cases"], spec.get("grammar"))
    model = spec["model"]
    allowed = set(spec["coordinates"]) | {m["name"] for m in spec.get("grammar", {}).get("macros", [])}
    if not fields(model["feature"]).issubset(allowed):
        raise AssertionError("mechanism reads undeclared coordinate")
    atol = float(spec.get("atol", 1e-9))
    errors = []
    for row in rows:
        pred = float(model["scale"]) * float(value(model["feature"], row)) + float(model["bias"])
        errors.append(abs(pred - float(row[spec["outcome"]])))
    worst = max(errors, default=0.0)
    assert worst <= atol
    return {"max_abs_error": worst, "n": len(rows)}


def check_grammar_extension(spec):
    rows = materialize(spec["cases"], spec.get("grammar"))
    ext = spec["extension"]
    macro = ext["macro"]
    expr = macro["expr"]
    allowed = set(ext["allowed_fields"])
    if not fields(expr).issubset(allowed):
        raise AssertionError("grammar extension reads undeclared field")

    if ext["purpose"] == "instrument":
        outcome, coords = ext["outcome"], ext["coordinates"]
        base = conditional_entropy(rows, outcome, coords)
        for row in rows:
            row[macro["name"]] = value(expr, row)
        after = conditional_entropy(rows, outcome, coords + [macro["name"]])
        gain = base - after
        assert gain >= float(spec.get("min_gain_bits", 1e-12))
        return {"baseline_bits": base, "residual_bits": after, "gain_bits": gain}

    if ext["purpose"] == "mechanism":
        candidate = {
            "feature": expr,
            "scale": ext["model"]["scale"],
            "bias": ext["model"]["bias"],
        }
        candidate_rmse = rmse(candidate, rows, ext["outcome"])
        baseline_model = spec.get("baseline_model")
        if baseline_model:
            baseline_rmse = rmse(baseline_model, rows, ext["outcome"])
            assert candidate_rmse + float(spec.get("min_improvement", 1e-12)) < baseline_rmse
        else:
            baseline_rmse = math.inf
            assert candidate_rmse <= float(spec.get("atol", 1e-9))
        return {"baseline_rmse": baseline_rmse, "candidate_rmse": candidate_rmse}

    raise ValueError(ext["purpose"])


def main():
    spec = json.loads(Path(sys.argv[1]).read_text())
    if spec["kind"] == "instrument":
        out = check_instrument(spec)
    elif spec["kind"] == "mechanism":
        out = check_mechanism(spec)
    elif spec["kind"] == "grammar_extension":
        out = check_grammar_extension(spec)
    else:
        raise ValueError(spec["kind"])
    print(json.dumps(out, sort_keys=True))


if __name__ == "__main__":
    main()
