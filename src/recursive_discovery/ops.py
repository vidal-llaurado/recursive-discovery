"""Replaceable scientific heuristics above the trusted core."""
from __future__ import annotations

from typing import Any, Callable, Iterable
import hashlib
import json
import math

from .core import Artifact, Ledger, Task


def stress(assumption: Artifact, rel: float = 0.1) -> dict[str, Any]:
    """Scalar predicate -> boundary-crossing intervention."""
    if assumption.kind != "assumption":
        raise ValueError("assumption artifact required")
    var, op, value = (assumption.data[k] for k in ("var", "op", "value"))
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError("numeric predicate required")
    d = max(abs(float(value)) * rel, rel)
    sides = {
        "<":  [value - d, value, value + d],
        "<=": [value - d, value, value + d],
        ">":  [value + d, value, value - d],
        ">=": [value + d, value, value - d],
        "==": [value, value + d, value - d],
        "!=": [value + d, value, value - d],
    }
    if op not in sides:
        raise ValueError(f"unsupported operator: {op}")
    return {
        "var": var,
        "values": list(dict.fromkeys(sides[op])),
        "boundary": value,
        "assumption": f"{var} {op} {value}",
    }


def conjectures(
    cases: list[dict[str, Any]],
    *,
    label: str = "anomaly",
    features: Iterable[str] | None = None,
    limit: int = 3,
) -> list[dict[str, Any]]:
    """Return the best legible one-feature rule for each feature.

    Multiple equally good rules are useful: observationally equivalent explanations
    are exactly what active experiment design should try to separate.
    """
    if not cases or label not in cases[0] or limit <= 0:
        return []
    ys = [bool(r[label]) for r in cases]
    if all(ys) or not any(ys):
        return []

    fs = list(features or [k for k in cases[0] if k != label])
    rules = []

    for order, f in enumerate(fs):
        if any(f not in r for r in cases):
            continue
        xs = [r[f] for r in cases]
        best = None

        def keep(score: float, op: str, value: Any):
            nonlocal best
            candidate = (score, op, value)
            if best is None or candidate[0] > best[0]:
                best = candidate

        if all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in xs):
            vals = sorted(set(map(float, xs)))
            for a, b in zip(vals, vals[1:]):
                t = (a + b) / 2
                for op in ("<=", ">"):
                    ps = [(float(x) <= t) if op == "<=" else (float(x) > t) for x in xs]
                    keep(sum(p == y for p, y in zip(ps, ys)) / len(ys), op, t)
        else:
            for v in set(xs):
                ps = [x == v for x in xs]
                keep(sum(p == y for p, y in zip(ps, ys)) / len(ys), "==", v)

        if best is not None:
            score, op, value = best
            rules.append({
                "if": {"var": f, "op": op, "value": value},
                "then": f"{label}=true",
                "accuracy": score,
                "n": len(cases),
                "_order": order,
            })

    rules.sort(key=lambda r: (-r["accuracy"], r["_order"]))
    for r in rules:
        r.pop("_order", None)
    return rules[:limit]


def conjecture(
    cases: list[dict[str, Any]],
    *,
    label: str = "anomaly",
    features: Iterable[str] | None = None,
) -> dict[str, Any] | None:
    """Return the single best one-feature rule, or None."""
    rows = conjectures(cases, label=label, features=features, limit=1)
    return rows[0] if rows else None



def diagnose(
    cases: list[dict[str, Any]],
    *,
    prediction: str = "predicted",
    observation: str = "observed",
    assumptions: Iterable[str] | None = None,
    features: Iterable[str] | None = None,
    atol: float = 0.0,
    rtol: float = 0.0,
) -> dict[str, Any]:
    """Localize disagreement before trying to explain it."""
    if not cases:
        raise ValueError("at least one case required")

    def same(a: Any, b: Any) -> bool:
        numeric = (
            isinstance(a, (int, float)) and not isinstance(a, bool)
            and isinstance(b, (int, float)) and not isinstance(b, bool)
        )
        return math.isclose(float(a), float(b), abs_tol=atol, rel_tol=rtol) if numeric else a == b

    names = list(assumptions or sorted({
        k for row in cases for k in row.get("assumptions", {})
    }))
    annotated = []
    for i, row in enumerate(cases):
        if prediction not in row or observation not in row:
            raise ValueError(f"case {i} lacks prediction or observation")
        state = row.get("assumptions", {})
        scope = all(bool(state.get(a, False)) for a in names) if names else True
        mismatch = not same(row[prediction], row[observation])
        annotated.append((i, row, scope, mismatch))

    mismatches = [i for i, _, _, m in annotated if m]
    inside = [i for i, _, s, m in annotated if s and m]
    outside = [i for i, _, s, m in annotated if not s and m]
    if not mismatches:
        cls = "agreement"
    elif inside and outside:
        cls = "mixed"
    elif inside:
        cls = "in_scope"
    else:
        cls = "out_of_scope"

    effects = []
    for name in names:
        violated = [(row, m) for _, row, _, m in annotated
                    if not bool(row.get("assumptions", {}).get(name, False))]
        satisfied = [(row, m) for _, row, _, m in annotated
                     if bool(row.get("assumptions", {}).get(name, False))]
        rv = sum(m for _, m in violated) / len(violated) if violated else 0.0
        rs = sum(m for _, m in satisfied) / len(satisfied) if satisfied else 0.0
        effects.append({
            "assumption": name,
            "violated_n": len(violated),
            "satisfied_n": len(satisfied),
            "mismatch_if_violated": rv,
            "mismatch_if_satisfied": rs,
            "lift": rv - rs,
        })
    effects.sort(key=lambda x: abs(x["lift"]), reverse=True)

    reserved = {prediction, observation, "assumptions"}
    feature_names = list(features or [k for k in cases[0] if k not in reserved])
    residual_rows = []
    for _, row, scope, mismatch in annotated:
        if scope:
            r = {k: row[k] for k in feature_names if k in row}
            r["anomaly"] = mismatch
            residual_rows.append(r)
    rules = conjectures(residual_rows, features=feature_names) if residual_rows else []

    return {
        "class": cls,
        "n": len(cases),
        "mismatches": len(mismatches),
        "mismatch_indices": mismatches,
        "in_scope_mismatches": inside,
        "out_of_scope_mismatches": outside,
        "scope": names,
        "assumption_effects": effects,
        "residual_rule": rules[0] if rules else None,
        "residual_rules": rules,
    }


def _rule_predict(hypothesis: Artifact, point: dict[str, Any]) -> bool:
    """Evaluate the tiny Boolean rule language emitted by `conjectures`."""
    rule = hypothesis.data.get("rule")
    if not rule:
        raise ValueError(f"hypothesis {hypothesis.id} has no rule")
    q = rule["if"]
    x, op, v = point[q["var"]], q["op"], q["value"]
    test = {
        "<":  lambda: x < v,
        "<=": lambda: x <= v,
        ">":  lambda: x > v,
        ">=": lambda: x >= v,
        "==": lambda: x == v,
        "!=": lambda: x != v,
    }
    if op not in test:
        raise ValueError(f"unsupported rule operator: {op}")
    return bool(test[op]())


def discriminate(
    hypotheses: Iterable[Artifact],
    candidates: Iterable[dict[str, Any]],
    *,
    prior: dict[str, float] | None = None,
    predictor: Callable[[Artifact, dict[str, Any]], Any] | None = None,
) -> dict[str, Any]:
    """Choose the cheapest intervention with the most hypothesis-separating outcome.

    With deterministic hypotheses, expected information gain is simply the entropy
    of their predicted outcomes under the current prior.
    """
    hs = list(hypotheses)
    xs = list(candidates)
    if len(hs) < 2:
        raise ValueError("at least two hypotheses required")
    if not xs:
        raise ValueError("at least one candidate intervention required")

    raw = {h.id: float((prior or {}).get(h.id, 1.0)) for h in hs}
    if any(v < 0 or not math.isfinite(v) for v in raw.values()) or sum(raw.values()) <= 0:
        raise ValueError("prior weights must be finite, nonnegative, and not all zero")
    z = sum(raw.values())
    weights = {k: v / z for k, v in raw.items()}
    predict = predictor or _rule_predict

    best = None
    for candidate in xs:
        cost = float(candidate.get("_cost", 1.0))
        if not math.isfinite(cost) or cost <= 0:
            raise ValueError("candidate _cost must be finite and positive")
        point = {k: v for k, v in candidate.items() if not k.startswith("_")}
        preds = {h.id: predict(h, point) for h in hs}

        mass: dict[str, float] = {}
        labels: dict[str, Any] = {}
        members: dict[str, list[str]] = {}
        for h in hs:
            key = repr(preds[h.id])
            mass[key] = mass.get(key, 0.0) + weights[h.id]
            labels[key] = preds[h.id]
            members.setdefault(key, []).append(h.id)

        ig = -sum(p * math.log2(p) for p in mass.values() if p > 0)
        score = ig / cost
        row = {
            "intervention": point,
            "cost": cost,
            "predictions": preds,
            "information_gain_bits": ig,
            "score": score,
            "partitions": [
                {"outcome": labels[k], "probability": mass[k], "hypotheses": members[k]}
                for k in sorted(mass)
            ],
        }
        key = (score, ig, -cost)
        if best is None or key > best[0]:
            best = (key, row)

    return best[1]


def resolve(
    design: dict[str, Any],
    observed: Any,
    *,
    atol: float = 0.0,
    rtol: float = 0.0,
) -> dict[str, Any]:
    """Update deterministic explanations after the discriminating observation."""
    def same(a: Any, b: Any) -> bool:
        numeric = (
            isinstance(a, (int, float)) and not isinstance(a, bool)
            and isinstance(b, (int, float)) and not isinstance(b, bool)
        )
        return math.isclose(float(a), float(b), abs_tol=atol, rel_tol=rtol) if numeric else a == b

    predictions = design["predictions"]
    survivors = [hid for hid, pred in predictions.items() if same(pred, observed)]
    rejected = [hid for hid in predictions if hid not in survivors]
    return {
        "observed": observed,
        "intervention": design["intervention"],
        "survivors": survivors,
        "rejected": rejected,
        "resolved": len(survivors) == 1,
        "surprise": len(survivors) == 0,
    }



def _entropy(values: Iterable[Any]) -> float:
    values = list(values)
    if not values:
        return 0.0
    counts: dict[str, int] = {}
    for v in values:
        k = repr(v)
        counts[k] = counts.get(k, 0) + 1
    n = len(values)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def _conditional_entropy(
    cases: list[dict[str, Any]],
    outcome: str,
    coordinates: Iterable[str],
) -> float:
    """H(outcome | coordinates) using the declared representation exactly."""
    keys = list(coordinates)
    groups: dict[tuple[str, ...], list[Any]] = {}
    for i, row in enumerate(cases):
        if outcome not in row:
            raise ValueError(f"case {i} lacks outcome {outcome!r}")
        if any(k not in row for k in keys):
            missing = [k for k in keys if k not in row]
            raise ValueError(f"case {i} lacks coordinates {missing}")
        state = tuple(repr(row[k]) for k in keys)
        groups.setdefault(state, []).append(row[outcome])

    n = len(cases)
    return sum((len(ys) / n) * _entropy(ys) for ys in groups.values())


def expand_representation(
    cases: list[dict[str, Any]],
    *,
    outcome: str,
    coordinates: Iterable[str],
    candidates: Iterable[str],
    costs: dict[str, float] | None = None,
    min_gain: float = 1e-12,
) -> dict[str, Any]:
    """Decide whether surprise means missing coordinate or missing mechanism.

    A representation is provably aliased on the supplied cases when the same declared
    coordinate state maps to multiple observed outcomes, i.e. H(Y | C) > 0.

    Candidate observables are ranked by how much conditional entropy they remove per
    unit measurement cost.
    """
    if not cases:
        raise ValueError("at least one case required")

    coordinates = list(coordinates)
    candidates = [c for c in candidates if c not in coordinates]
    base = _conditional_entropy(cases, outcome, coordinates)

    # If the current coordinates already distinguish observed outcomes, adding a coordinate
    # is not justified by these data. The explanation family is the thing that failed.
    if base <= min_gain:
        return {
            "status": "mechanism",
            "coordinates": coordinates,
            "baseline_uncertainty_bits": base,
            "reason": "current coordinates already separate the observed outcomes",
        }

    ranked = []
    for feature in candidates:
        if any(feature not in row for row in cases):
            continue
        cost = float((costs or {}).get(feature, 1.0))
        if not math.isfinite(cost) or cost <= 0:
            raise ValueError(f"invalid measurement cost for {feature!r}")
        after = _conditional_entropy(cases, outcome, coordinates + [feature])
        gain = max(0.0, base - after)
        ranked.append({
            "coordinate": feature,
            "uncertainty_bits": after,
            "gain_bits": gain,
            "cost": cost,
            "score": gain / cost,
        })

    ranked.sort(key=lambda r: (r["score"], r["gain_bits"], -r["cost"]), reverse=True)

    if not ranked or ranked[0]["gain_bits"] <= min_gain:
        return {
            "status": "blind",
            "coordinates": coordinates,
            "baseline_uncertainty_bits": base,
            "ranked": ranked,
            "reason": "declared coordinates alias outcomes and no measured candidate resolves the aliasing",
        }

    best = ranked[0]
    return {
        "status": "expanded",
        "coordinates": coordinates,
        "add_coordinate": best["coordinate"],
        "next_coordinates": coordinates + [best["coordinate"]],
        "baseline_uncertainty_bits": base,
        "residual_uncertainty_bits": best["uncertainty_bits"],
        "gain_bits": best["gain_bits"],
        "cost": best["cost"],
        "score": best["score"],
        "ranked": ranked,
        "reason": "new coordinate reduces outcome aliasing in the current representation",
    }



BASE_PRIMITIVES = ("id", "square", "xor", "mul", "add", "sub", "eq")


def base_grammar() -> dict[str, Any]:
    """The seed language. Useful concepts are appended as validated macros."""
    return {"primitives": list(BASE_PRIMITIVES), "macros": [], "generation": 0}


def adopt_grammar(grammar: dict[str, Any] | None, extension: dict[str, Any]) -> dict[str, Any]:
    """Validated composite -> atomic concept in the next grammar generation."""
    g = json.loads(json.dumps(grammar or base_grammar()))
    macro = json.loads(json.dumps(extension["macro"]))
    names = {m["name"] for m in g.get("macros", [])}
    if macro["name"] not in names:
        g.setdefault("macros", []).append(macro)
    g["generation"] = int(g.get("generation", 0)) + 1
    return g


def _macro_names(grammar: dict[str, Any] | None) -> list[str]:
    return [m["name"] for m in (grammar or {}).get("macros", [])]


def _materialize(cases: Iterable[dict[str, Any]], grammar: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Evaluate macros in append order; later concepts may use earlier concepts."""
    rows = [dict(r) for r in cases]
    for macro in (grammar or {}).get("macros", []):
        for row in rows:
            row[macro["name"]] = _expr_value(macro["expr"], row)
    return rows


def _exprs(fields: Iterable[str], grammar: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """One reasoning step in the current language."""
    fs = list(dict.fromkeys(fields))
    ops = set((grammar or base_grammar()).get("primitives", BASE_PRIMITIVES))
    out = []
    if "id" in ops:
        out += [{"op": "id", "args": [f]} for f in fs]
    if "square" in ops:
        out += [{"op": "square", "args": [f]} for f in fs]
    binary = [op for op in ("xor", "mul", "add", "sub", "eq") if op in ops]
    for i, a in enumerate(fs):
        for b in fs[i + 1:]:
            for op in binary:
                out.append({"op": op, "args": [a, b]})
    return out


def _expr_value(expr: dict[str, Any], row: dict[str, Any]) -> Any:
    op, args = expr["op"], expr["args"]

    def value(a: Any) -> Any:
        return _expr_value(a, row) if isinstance(a, dict) else row[a]

    xs = [value(a) for a in args]
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
    raise ValueError(f"unsupported expression op: {op}")


def _expr_fields(expr: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for a in expr["args"]:
        if isinstance(a, dict):
            out |= _expr_fields(a)
        else:
            out.add(a)
    return out


def _expr_depth(expr: dict[str, Any]) -> int:
    if expr["op"] == "id" and all(not isinstance(a, dict) for a in expr["args"]):
        return 0
    child = [_expr_depth(a) for a in expr["args"] if isinstance(a, dict)]
    return 1 + (max(child) if child else 0)


def _expr_complexity(expr: dict[str, Any]) -> int:
    return 1 + sum(_expr_complexity(a) for a in expr["args"] if isinstance(a, dict))


def _expr_cost(
    expr: dict[str, Any],
    costs: dict[str, float] | None = None,
    description_cost: float = 0.05,
) -> float:
    costs = costs or {}
    measurement = sum(float(costs.get(f, 1.0)) for f in _expr_fields(expr))
    total = measurement + description_cost * _expr_complexity(expr)
    if not math.isfinite(total) or total <= 0:
        raise ValueError("expression cost must be finite and positive")
    return total


def _expr_text(expr: dict[str, Any]) -> str:
    op, args = expr["op"], expr["args"]

    def text(a: Any) -> str:
        return _expr_text(a) if isinstance(a, dict) else str(a)

    if op == "id":
        return text(args[0])
    if op == "square":
        return f"({text(args[0])})^2"
    symbol = {"xor": "xor", "mul": "*", "add": "+", "sub": "-", "eq": "=="}[op]
    return f"({text(args[0])} {symbol} {text(args[1])})"


def _signature(expr: dict[str, Any], rows: list[dict[str, Any]]) -> tuple[tuple[str, str], ...] | None:
    try:
        values = [_expr_value(expr, r) for r in rows]
    except (KeyError, TypeError, ValueError, ZeroDivisionError, OverflowError):
        return None
    return tuple((type(v).__name__, repr(v)) for v in values)


def _deep_exprs(
    fields: Iterable[str],
    rows: list[dict[str, Any]],
    grammar: dict[str, Any] | None,
    *,
    max_depth: int = 2,
    max_programs: int = 512,
) -> list[dict[str, Any]]:
    """Search deeper syntax, quotienting programs by observed behavior.

    Semantic deduplication is the scaling trick: keep one smallest program for each
    output vector rather than every syntactic tree.
    """
    g = grammar or base_grammar()
    ops = set(g.get("primitives", BASE_PRIMITIVES))
    atoms = [{"op": "id", "args": [f]} for f in dict.fromkeys(fields)]
    pool = list(atoms)
    by_sig: dict[tuple[tuple[str, str], ...], dict[str, Any]] = {}
    for e in atoms:
        sig = _signature(e, rows)
        if sig is not None:
            by_sig[sig] = e

    commutative = {"xor", "mul", "add", "eq"}
    binary = [op for op in ("xor", "mul", "add", "sub", "eq") if op in ops]
    deep: list[dict[str, Any]] = []

    for depth in range(1, max_depth + 1):
        candidates: list[dict[str, Any]] = []
        if "square" in ops:
            for a in pool:
                if _expr_depth(a) == depth - 1:
                    candidates.append({"op": "square", "args": [a]})
        for op in binary:
            for i, a in enumerate(pool):
                for j, b in enumerate(pool):
                    if op in commutative and j < i:
                        continue
                    if max(_expr_depth(a), _expr_depth(b)) != depth - 1:
                        continue
                    candidates.append({"op": op, "args": [a, b]})

        next_rows: list[dict[str, Any]] = []
        local: dict[tuple[tuple[str, str], ...], dict[str, Any]] = {}
        for e in candidates:
            sig = _signature(e, rows)
            if sig is None:
                continue
            old = by_sig.get(sig) or local.get(sig)
            if old is None or (_expr_complexity(e), _expr_text(e)) < (_expr_complexity(old), _expr_text(old)):
                local[sig] = e

        chosen = sorted(local.values(), key=lambda e: (_expr_complexity(e), _expr_text(e)))[:max_programs]
        for e in chosen:
            sig = _signature(e, rows)
            if sig is not None:
                by_sig[sig] = e
        pool += chosen
        deep += chosen
        if len(pool) > max_programs:
            # Preserve atoms; cap the rest by description length.
            tail = sorted(pool[len(atoms):], key=lambda e: (_expr_complexity(e), _expr_text(e)))
            pool = atoms + tail[: max(0, max_programs - len(atoms))]

    return [e for e in deep if _expr_depth(e) >= 2]


def _macro_name(expr: dict[str, Any]) -> str:
    raw = json.dumps(expr, sort_keys=True, separators=(",", ":"))
    return "g_" + hashlib.sha256(raw.encode()).hexdigest()[:10]


def invent_instrument(
    cases: list[dict[str, Any]],
    *,
    outcome: str,
    coordinates: Iterable[str],
    channels: Iterable[str],
    costs: dict[str, float] | None = None,
    grammar: dict[str, Any] | None = None,
    description_cost: float = 0.05,
    min_gain: float = 1e-12,
    require_complete: bool = False,
) -> dict[str, Any] | None:
    """Current grammar -> cheapest useful derived measurement."""
    if not cases:
        raise ValueError("at least one case required")
    g = grammar or base_grammar()
    rows = _materialize(cases, g)
    coordinates = list(coordinates)
    channels = [c for c in channels if c not in coordinates and c != outcome]
    macros = _macro_names(g)
    fields = list(dict.fromkeys(channels + macros))
    base = _conditional_entropy(rows, outcome, coordinates)
    if base <= min_gain:
        return None

    field_costs = dict(costs or {})
    for m in g.get("macros", []):
        field_costs.setdefault(m["name"], float(m.get("cost", description_cost)))

    ranked = []
    for order, expr in enumerate(_exprs(fields, g)):
        try:
            augmented = []
            for row in rows:
                r = dict(row)
                r["__instrument__"] = _expr_value(expr, row)
                augmented.append(r)
            after = _conditional_entropy(augmented, outcome, coordinates + ["__instrument__"])
        except (KeyError, TypeError, ValueError, ZeroDivisionError, OverflowError):
            continue
        gain = max(0.0, base - after)
        cost = _expr_cost(expr, field_costs, description_cost)
        ranked.append({
            "expr": expr,
            "name": _expr_text(expr),
            "baseline_uncertainty_bits": base,
            "residual_uncertainty_bits": after,
            "gain_bits": gain,
            "cost": cost,
            "score": gain / cost,
            "_order": order,
        })

    ranked.sort(key=lambda r: (-r["score"], -r["gain_bits"], r["_order"]))
    eligible = [r for r in ranked if r["gain_bits"] > min_gain]
    if require_complete:
        eligible = [r for r in eligible if r["residual_uncertainty_bits"] <= min_gain]
    if not eligible:
        return None
    best = dict(eligible[0])
    best.pop("_order", None)
    best.update({
        "status": "proposed",
        "kind": "derived_measurement",
        "coordinates": coordinates,
        "channels": channels,
        "grammar_generation": int(g.get("generation", 0)),
    })
    return best


def _affine_fit(xs: list[float], ys: list[float]) -> tuple[float, float, float]:
    """Least-squares y = a*x + b; return a, b, RMSE."""
    if len(xs) != len(ys) or not xs:
        raise ValueError("nonempty aligned samples required")
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    var = sum((x - mx) ** 2 for x in xs)
    if var <= 1e-18:
        a = 0.0
    else:
        a = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / var
    b = my - a * mx
    mse = sum((a * x + b - y) ** 2 for x, y in zip(xs, ys)) / len(xs)
    return a, b, math.sqrt(mse)


def _best_mechanism(
    cases: list[dict[str, Any]],
    *,
    outcome: str,
    coordinates: list[str],
    grammar: dict[str, Any],
    expressions: Iterable[dict[str, Any]],
) -> dict[str, Any] | None:
    rows = _materialize(cases, grammar)
    ys = [float(r[outcome]) for r in rows]
    ranked = []
    for order, expr in enumerate(expressions):
        try:
            xs = [float(_expr_value(expr, r)) for r in rows]
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if any(not math.isfinite(x) for x in xs):
            continue
        a, b, rmse = _affine_fit(xs, ys)
        ranked.append({
            "feature": expr,
            "feature_text": _expr_text(expr),
            "scale": a,
            "bias": b,
            "rmse": rmse,
            "complexity": _expr_complexity(expr),
            "_order": order,
        })
    ranked.sort(key=lambda r: (r["rmse"], r["complexity"], r["_order"]))
    if not ranked:
        return None
    best = dict(ranked[0])
    best.pop("_order", None)
    return best


def synthesize_mechanism(
    cases: list[dict[str, Any]],
    *,
    outcome: str,
    coordinates: Iterable[str],
    grammar: dict[str, Any] | None = None,
    atol: float = 1e-9,
) -> dict[str, Any] | None:
    """Search one step in the current grammar for a predictive mechanism."""
    if not cases:
        raise ValueError("at least one case required")
    if any(outcome not in r for r in cases):
        raise ValueError(f"missing outcome {outcome!r}")
    g = grammar or base_grammar()
    coordinates = list(coordinates)
    fields = coordinates + _macro_names(g)
    best = _best_mechanism(
        cases, outcome=outcome, coordinates=coordinates, grammar=g,
        expressions=_exprs(fields, g),
    )
    if best is None:
        return None
    best.update({
        "status": "proposed" if best["rmse"] <= atol else "unresolved",
        "outcome": outcome,
        "coordinates": coordinates,
        "grammar_generation": int(g.get("generation", 0)),
        "law": f"{outcome} = {best['scale']:.12g}*{best['feature_text']} + {best['bias']:.12g}",
    })
    return best


def extend_grammar(
    cases: list[dict[str, Any]],
    *,
    purpose: str,
    outcome: str,
    grammar: dict[str, Any] | None = None,
    coordinates: Iterable[str] = (),
    channels: Iterable[str] = (),
    costs: dict[str, float] | None = None,
    max_depth: int = 2,
    description_cost: float = 0.05,
    atol: float = 1e-9,
    min_improvement: float = 1e-12,
) -> dict[str, Any] | None:
    """Search one level beyond the current language and propose a reusable macro.

    The extension need not finish the scientific task. It must improve the task on the
    observed cases; held-out validation is required before the macro can enter the grammar.
    """
    if purpose not in {"instrument", "mechanism"}:
        raise ValueError("purpose must be 'instrument' or 'mechanism'")
    g = grammar or base_grammar()
    rows = _materialize(cases, g)
    coordinates = list(coordinates)
    macros = _macro_names(g)

    if purpose == "instrument":
        channels = [c for c in channels if c not in coordinates and c != outcome]
        fields = list(dict.fromkeys(channels + macros))
        base = _conditional_entropy(rows, outcome, coordinates)
        if base <= min_improvement:
            return None
        field_costs = dict(costs or {})
        for m in g.get("macros", []):
            field_costs.setdefault(m["name"], float(m.get("cost", description_cost)))
        candidates = _deep_exprs(fields, rows, g, max_depth=max_depth)
        ranked = []
        for expr in candidates:
            augmented = []
            try:
                for row in rows:
                    r = dict(row)
                    r["__candidate__"] = _expr_value(expr, row)
                    augmented.append(r)
                after = _conditional_entropy(augmented, outcome, coordinates + ["__candidate__"])
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
            gain = max(0.0, base - after)
            if gain <= min_improvement:
                continue
            cost = _expr_cost(expr, field_costs, description_cost)
            ranked.append((after, -gain / cost, _expr_complexity(expr), _expr_text(expr), expr, gain, cost))
        if not ranked:
            return None
        # Prefer fully resolving concepts, then information/cost, then simplicity.
        ranked.sort()
        after, negscore, _, _, expr, gain, cost = ranked[0]
        training = {
            "baseline_uncertainty_bits": base,
            "residual_uncertainty_bits": after,
            "gain_bits": gain,
            "score": -negscore,
        }
        model = None
        allowed = channels + macros

    else:
        fields = list(dict.fromkeys(coordinates + macros))
        shallow = _best_mechanism(
            cases, outcome=outcome, coordinates=coordinates, grammar=g,
            expressions=_exprs(fields, g),
        )
        baseline_rmse = shallow["rmse"] if shallow else math.inf
        candidates = _deep_exprs(fields, rows, g, max_depth=max_depth)
        deep = _best_mechanism(
            cases, outcome=outcome, coordinates=coordinates, grammar=g,
            expressions=candidates,
        )
        if deep is None or not math.isfinite(deep["rmse"]):
            return None
        if not (deep["rmse"] + min_improvement < baseline_rmse):
            return None
        expr = deep["feature"]
        cost = description_cost * _expr_complexity(expr)
        training = {
            "baseline_rmse": baseline_rmse,
            "candidate_rmse": deep["rmse"],
            "improvement": baseline_rmse - deep["rmse"],
        }
        model = {"scale": deep["scale"], "bias": deep["bias"], "rmse": deep["rmse"]}
        allowed = coordinates + macros

    name = _macro_name(expr)
    macro = {
        "name": name,
        "expr": expr,
        "text": _expr_text(expr),
        "cost": cost,
        "depth": _expr_depth(expr),
        "fields": sorted(_expr_fields(expr)),
    }
    return {
        "status": "proposed",
        "purpose": purpose,
        "macro": macro,
        "training": training,
        "model": model,
        "outcome": outcome,
        "coordinates": coordinates,
        "channels": list(channels) if purpose == "instrument" else [],
        "allowed_fields": allowed,
        "parent_generation": int(g.get("generation", 0)),
        "parent_grammar": g,
        "atol": atol,
    }



def bid(
    ledger: Ledger,
    task: Task,
    *,
    actor: str,
    gain: float,
    cost: float,
    plan: str = "",
    attempt: int | None = None,
) -> Artifact:
    """Predict normalized edge-closing gain and cost for one frontier task."""
    if not (math.isfinite(gain) and 0 <= gain <= 1):
        raise ValueError("gain must be in [0,1]")
    if not math.isfinite(cost) or cost <= 0:
        raise ValueError("cost must be finite and positive")
    return ledger.put(
        "bid",
        {"verb": task.verb, "lane": task.lane, "gain": float(gain),
         "cost": float(cost), "plan": plan, "attempt": attempt},
        {"target": [task.target]},
        by=actor,
    )


def settle(
    ledger: Ledger,
    task: Task,
    winning_bid: Artifact,
    before: Iterable[Task],
    after: Iterable[Task],
    *,
    attempt: int | None = None,
) -> Artifact:
    """Turn a bid into calibration data using actual epistemic edge closure."""
    b, a = {t.key for t in before}, {t.key for t in after}
    closed = task.key not in a
    opened = sorted(a - b)
    return ledger.put(
        "outcome",
        {
            "verb": task.verb,
            "lane": task.lane,
            "predicted_gain": winning_bid.data["gain"],
            "realized_gain": 1.0 if closed else 0.0,
            "closed": closed,
            "opened": opened,
            "cost": winning_bid.data["cost"],
            "attempt": attempt,
        },
        {"bid": [winning_bid.id], "target": [task.target]},
        by="controller",
    )


def calibration(ledger: Ledger, actor: str) -> dict[str, float]:
    """Beta-calibrated edge-closing reliability for an actor."""
    rows = []
    for out in ledger.all("outcome"):
        bids = ledger.related(out.id, "bid")
        if bids and bids[0].by == actor:
            rows.append((bids[0], out))
    n = len(rows)
    successes = sum(1 for _, o in rows if o.data["closed"])
    trust = (successes + 1) / (n + 2)  # Beta(1,1) posterior mean
    brier = (
        sum((float(b.data["gain"]) - float(o.data["realized_gain"])) ** 2
            for b, o in rows) / n
        if n else 0.25
    )
    return {
        "attempts": float(n),
        "successes": float(successes),
        "trust": trust,
        "multiplier": 2 * trust,  # neutral prior -> 1.0
        "brier": brier,
    }


def allocate(
    ledger: Ledger,
    tasks: Iterable[Task],
    budget: float = math.inf,
) -> list[tuple[Task, Artifact]]:
    """One unsettled bid per task, ranked by calibrated gain/cost."""
    tasks = list(tasks)
    actors = {b.by for b in ledger.all("bid")}
    trust = {a: calibration(ledger, a)["multiplier"] for a in actors}
    offers = []

    for task in tasks:
        for b in ledger.children(task.target, "bid", "target"):
            if ledger.children(b.id, "outcome", "bid"):
                continue
            if (b.data.get("verb"), b.data.get("lane")) != (task.verb, task.lane):
                continue
            g, c = float(b.data["gain"]), float(b.data["cost"])
            score = g * trust.get(b.by, 1.0) / c
            offers.append((score, g, task, b))

    offers.sort(key=lambda x: (x[0], x[1]), reverse=True)
    chosen, seen, spent = [], set(), 0.0
    for _, _, task, b in offers:
        c = float(b.data["cost"])
        if task.key not in seen and spent + c <= budget:
            chosen.append((task, b))
            seen.add(task.key)
            spent += c
    return chosen
