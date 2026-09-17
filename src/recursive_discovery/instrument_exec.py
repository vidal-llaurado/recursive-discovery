"""Deterministic numerical and symbolic diagnostics.

Outputs are derived observations. Running a diagnostic records execution provenance; it does not
establish that a scientific interpretation of the output is correct.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import sys


def _sympy(spec):
    import sympy as sp
    names = list(spec.get("symbols", []))
    symbols = {name: sp.Symbol(name, real=bool(spec.get("real", True))) for name in names}
    local = dict(symbols)
    expr = sp.sympify(spec["expr"], locals=local)
    op = spec.get("op", "simplify")
    if op == "simplify":
        out = sp.simplify(expr)
    elif op == "factor":
        out = sp.factor(expr)
    elif op == "expand":
        out = sp.expand(expr)
    elif op == "diff":
        out = sp.diff(expr, symbols[spec["var"]], int(spec.get("order", 1)))
    elif op == "integrate":
        v = symbols[spec["var"]]
        bounds = spec.get("bounds")
        out = sp.integrate(expr, (v, *bounds)) if bounds else sp.integrate(expr, v)
    elif op == "solve":
        v = symbols[spec["var"]]
        out = sp.solve(sp.Eq(expr, sp.sympify(spec.get("rhs", 0), locals=local)), v)
    elif op == "limit":
        out = sp.limit(expr, symbols[spec["var"]], sp.sympify(spec["to"], locals=local))
    elif op == "series":
        out = sp.series(expr, symbols[spec["var"]],
                        sp.sympify(spec.get("around", 0), locals=local),
                        int(spec.get("order", 6)))
    else:
        raise ValueError(f"unsupported symbolic operation: {op}")
    return {"op": op, "result": str(out), "srepr": sp.srepr(out)}


def _falsify(spec):
    import sympy as sp
    from scipy.optimize import differential_evolution

    variables = list(spec["variables"])
    syms = [sp.Symbol(x, real=True) for x in variables]
    local = {s.name: s for s in syms}
    lhs = sp.sympify(spec["lhs"], locals=local)
    rhs = sp.sympify(spec.get("rhs", "0"), locals=local)
    relation = spec.get("relation", "<=")
    bounds = [tuple(map(float, spec["bounds"][name])) for name in variables]
    f = sp.lambdify(syms, lhs - rhs, modules="numpy")

    def violation(x):
        try:
            d = float(f(*x))
            if not math.isfinite(d):
                return 1e12
        except Exception:
            return 1e12
        if relation in ("<=", "<"):
            return d
        if relation in (">=", ">"):
            return -d
        if relation == "==":
            return abs(d)
        raise ValueError(f"unsupported relation: {relation}")

    res = differential_evolution(
        lambda x: -violation(x),
        bounds=bounds,
        seed=int(spec.get("seed", 0)),
        polish=True,
        maxiter=int(spec.get("maxiter", 200)),
        popsize=int(spec.get("popsize", 12)),
        workers=1,
    )
    v = float(violation(res.x))
    tol = float(spec.get("tolerance", 1e-8))
    return {
        "relation": relation,
        "max_violation": v,
        "counterexample": bool(v > tol),
        "point": {name: float(x) for name, x in zip(variables, res.x)},
        "tolerance": tol,
        "evaluations": int(res.nfev),
    }


def _matrix(spec):
    import numpy as np
    A = np.asarray(spec["matrix"], dtype=float)
    if A.ndim != 2 or A.shape[0] != A.shape[1]:
        raise ValueError("matrix must be square")
    eig = np.linalg.eigvals(A)
    sv = np.linalg.svd(A, compute_uv=False)
    denom = max(float(np.linalg.norm(A, ord="fro")) ** 2, 1e-30)
    comm = A.T @ A - A @ A.T
    return {
        "shape": list(A.shape),
        "eigenvalues": [[float(z.real), float(z.imag)] for z in eig],
        "spectral_radius": float(np.max(np.abs(eig))),
        "singular_values": [float(x) for x in sv],
        "condition_2": float(np.linalg.cond(A)),
        "symmetry_error": float(np.linalg.norm(A - A.T, ord="fro") /
                                max(np.linalg.norm(A, ord="fro"), 1e-30)),
        "nonnormality": float(np.linalg.norm(comm, ord="fro") / denom),
        "trace": float(np.trace(A)),
        "determinant": float(np.linalg.det(A)),
    }


def _sensitivity(spec):
    import sympy as sp
    variables = list(spec["variables"])
    syms = [sp.Symbol(x, real=True) for x in variables]
    local = {s.name: s for s in syms}
    expr = sp.sympify(spec["expr"], locals=local)
    point = {str(k): float(v) for k, v in spec["point"].items()}
    f = sp.lambdify(syms, expr, modules="numpy")
    xs = [point[x] for x in variables]
    base = float(f(*xs))
    rel = float(spec.get("relative_step", 1e-5))
    absolute = float(spec.get("absolute_step", 1e-8))
    gradients, elasticities = {}, {}
    for i, name in enumerate(variables):
        h = max(abs(xs[i]) * rel, absolute)
        plus, minus = list(xs), list(xs)
        plus[i] += h
        minus[i] -= h
        g = (float(f(*plus)) - float(f(*minus))) / (2 * h)
        gradients[name] = g
        elasticities[name] = (g * xs[i] / base) if abs(base) > 1e-30 else None
    return {"value": base, "gradient": gradients, "elasticity": elasticities}


def _hessian(spec):
    import numpy as np
    import sympy as sp

    variables = list(spec["variables"])
    syms = [sp.Symbol(x, real=True) for x in variables]
    local = {s.name: s for s in syms}
    expr = sp.sympify(spec["expr"], locals=local)
    grad = sp.Matrix([sp.diff(expr, s) for s in syms])
    H = sp.hessian(expr, syms)
    out = {
        "gradient_symbolic": [str(x) for x in grad],
        "hessian_symbolic": [[str(H[i, j]) for j in range(H.cols)] for i in range(H.rows)],
    }
    if "point" in spec:
        point = {local[k]: float(v) for k, v in spec["point"].items()}
        g = np.asarray([float(x.subs(point)) for x in grad], dtype=float)
        h = np.asarray(H.subs(point), dtype=float)
        eig = np.linalg.eigvalsh((h + h.T) / 2)
        out.update({
            "gradient": g.tolist(),
            "hessian": h.tolist(),
            "eigenvalues": eig.tolist(),
            "lambda_min": float(eig.min()),
            "lambda_max": float(eig.max()),
            "condition_abs": float(abs(eig).max() / max(abs(eig).min(), 1e-30)),
            "trace": float(np.trace(h)),
        })
    return out


def _dynamics(spec):
    import numpy as np
    import sympy as sp

    mode = spec.get("mode", "linear")
    if mode == "linear":
        A = np.asarray(spec["A"], dtype=float)
        b = np.asarray(spec.get("b", np.zeros(A.shape[0])), dtype=float)
        if A.ndim != 2 or A.shape[0] != A.shape[1]:
            raise ValueError("A must be square")
        eig = np.linalg.eigvals(A)
        rho = float(np.max(np.abs(eig)))
        horizon = int(spec.get("horizon", 50))
        power = np.eye(A.shape[0])
        gains = []
        for k in range(horizon + 1):
            gains.append(float(np.linalg.svd(power, compute_uv=False)[0]))
            power = A @ power
        try:
            fixed = np.linalg.solve(np.eye(A.shape[0]) - A, b)
            fixed = fixed.tolist()
        except np.linalg.LinAlgError:
            fixed = None
        peak = int(np.argmax(gains))
        return {
            "mode": "linear",
            "spectral_radius": rho,
            "asymptotically_stable": bool(rho < 1.0),
            "eigenvalues": [[float(z.real), float(z.imag)] for z in eig],
            "fixed_point": fixed,
            "peak_transient_gain": gains[peak],
            "peak_transient_step": peak,
            "gain_at_horizon": gains[-1],
        }

    if mode != "map":
        raise ValueError("mode must be linear or map")

    variables = list(spec["variables"])
    syms = [sp.Symbol(x, real=True) for x in variables]
    local = {s.name: s for s in syms}
    maps = [sp.sympify(x, locals=local) for x in spec["map"]]
    F = sp.Matrix(maps)
    J = F.jacobian(syms)
    f = sp.lambdify(syms, F, modules="numpy")
    jf = sp.lambdify(syms, J, modules="numpy")
    x = np.asarray(spec["initial"], dtype=float)
    steps = int(spec.get("steps", 100))
    burn = int(spec.get("burn", 0))
    v = np.ones(len(x), dtype=float)
    v /= np.linalg.norm(v)
    log_growth = []
    trajectory = []
    for t in range(steps):
        jac = np.asarray(jf(*x), dtype=float)
        v = jac @ v
        n = float(np.linalg.norm(v))
        if n <= 1e-30 or not math.isfinite(n):
            log_growth.append(float("-inf"))
            v = np.ones(len(x), dtype=float) / math.sqrt(len(x))
        else:
            log_growth.append(math.log(n))
            v /= n
        x = np.asarray(f(*x), dtype=float).reshape(-1)
        if t >= burn:
            trajectory.append(x.copy())
    finite = [g for g in log_growth[burn:] if math.isfinite(g)]
    lle = float(sum(finite) / len(finite)) if finite else float("-inf")
    point = spec.get("point")
    local_out = None
    if point:
        xp = [float(point[name]) for name in variables]
        jp = np.asarray(jf(*xp), dtype=float)
        eig = np.linalg.eigvals(jp)
        local_out = {
            "jacobian": jp.tolist(),
            "eigenvalues": [[float(z.real), float(z.imag)] for z in eig],
            "spectral_radius": float(np.max(np.abs(eig))),
        }
    return {
        "mode": "map",
        "final_state": x.tolist(),
        "largest_finite_time_lyapunov": lle,
        "local": local_out,
        "trajectory_tail": [y.tolist() for y in trajectory[-min(20, len(trajectory)):]],
    }


def _scaling(spec):
    import numpy as np
    rows = list(spec["rows"])
    target = str(spec["target"])
    features = list(spec["features"])
    log = bool(spec.get("log", True))
    X, y = [], []
    for row in rows:
        vals = [float(row[f]) for f in features]
        yy = float(row[target])
        if log:
            if yy <= 0 or any(v <= 0 for v in vals):
                continue
            vals = [math.log(v) for v in vals]
            yy = math.log(yy)
        X.append([1.0, *vals])
        y.append(yy)
    if len(X) < len(features) + 1:
        raise ValueError("insufficient usable rows")
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float)
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    pred = X @ beta
    residual = y - pred
    ss_res = float(residual @ residual)
    ss_tot = float(((y - y.mean()) ** 2).sum())
    return {
        "mode": "log_power_law" if log else "linear",
        "intercept": float(beta[0]),
        "coefficients": {f: float(v) for f, v in zip(features, beta[1:])},
        "r2": 1.0 - ss_res / max(ss_tot, 1e-30),
        "rmse": float(math.sqrt(ss_res / len(y))),
        "n": int(len(y)),
    }


def _timeseries(spec):
    import numpy as np
    x = np.asarray(spec["values"], dtype=float).reshape(-1)
    if len(x) < 3:
        raise ValueError("at least three values required")
    x0 = x - x.mean()
    var = float(np.var(x))
    max_lag = min(int(spec.get("max_lag", 20)), len(x) - 1)
    acf = []
    denom = float(np.dot(x0, x0))
    for lag in range(max_lag + 1):
        acf.append(float(np.dot(x0[:len(x)-lag], x0[lag:]) / max(denom, 1e-30)))
    freq = np.fft.rfftfreq(len(x), d=float(spec.get("dt", 1.0)))
    power = np.abs(np.fft.rfft(x0)) ** 2
    if len(power) > 1:
        j = int(np.argmax(power[1:]) + 1)
        dominant = {"frequency": float(freq[j]), "power": float(power[j])}
    else:
        dominant = None
    t = np.arange(len(x), dtype=float)
    slope = float(np.polyfit(t, x, 1)[0])
    return {
        "n": int(len(x)),
        "mean": float(x.mean()),
        "variance": var,
        "trend_per_step": slope,
        "acf": acf,
        "dominant_frequency": dominant,
    }


def main():
    if len(sys.argv) != 3:
        raise SystemExit("usage: instrument_exec.py <kind> <spec.json>")
    kind, path = sys.argv[1:]
    spec = json.loads(Path(path).read_text())
    fn = {
        "symbolic": _sympy,
        "falsify": _falsify,
        "matrix": _matrix,
        "sensitivity": _sensitivity,
        "hessian": _hessian,
        "dynamics": _dynamics,
        "scaling": _scaling,
        "timeseries": _timeseries,
    }.get(kind)
    if fn is None:
        raise ValueError(kind)
    print(json.dumps(fn(spec), sort_keys=True))


if __name__ == "__main__":
    main()
