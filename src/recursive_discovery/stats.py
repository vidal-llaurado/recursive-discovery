"""Statistical summaries: intervals, contrasts, and stopping rules."""
from __future__ import annotations

import math
import statistics
from statistics import NormalDist
from typing import Iterable


def _q(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    if not xs:
        raise ValueError("empty sample")
    if len(xs) == 1:
        return xs[0]
    h = (len(xs) - 1) * p
    lo, hi = math.floor(h), math.ceil(h)
    if lo == hi:
        return xs[lo]
    return xs[lo] + (h - lo) * (xs[hi] - xs[lo])


def summarize(values: Iterable[float], confidence: float = .95) -> dict[str, float]:
    xs = [float(x) for x in values]
    if not xs:
        raise ValueError("at least one observation required")
    if not 0 < confidence < 1:
        raise ValueError("confidence must be in (0,1)")

    n = len(xs)
    mean = statistics.fmean(xs)
    sd = statistics.stdev(xs) if n > 1 else 0.0
    sem = sd / math.sqrt(n) if n else 0.0
    z = NormalDist().inv_cdf((1 + confidence) / 2)
    median = statistics.median(xs)
    mad = statistics.median(abs(x - median) for x in xs)
    return {
        "n": float(n),
        "mean": mean,
        "sd": sd,
        "sem": sem,
        "ci_low": mean - z * sem,
        "ci_high": mean + z * sem,
        "median": median,
        "mad": mad,
        "q05": _q(xs, .05),
        "q25": _q(xs, .25),
        "q75": _q(xs, .75),
        "q95": _q(xs, .95),
        "min": min(xs),
        "max": max(xs),
    }


def contrast(a: Iterable[float], b: Iterable[float], confidence: float = .95) -> dict[str, float]:
    """Difference in means with an approximate normal interval and standardized effect."""
    x, y = [float(v) for v in a], [float(v) for v in b]
    if not x or not y:
        raise ValueError("both samples must be nonempty")
    sx, sy = summarize(x, confidence), summarize(y, confidence)
    diff = sx["mean"] - sy["mean"]
    se = math.sqrt(sx["sem"] ** 2 + sy["sem"] ** 2)
    z = NormalDist().inv_cdf((1 + confidence) / 2)
    pooled = math.sqrt(
        (((len(x) - 1) * sx["sd"] ** 2) + ((len(y) - 1) * sy["sd"] ** 2))
        / max(1, len(x) + len(y) - 2)
    )
    return {
        "difference": diff,
        "se": se,
        "ci_low": diff - z * se,
        "ci_high": diff + z * se,
        "standardized_effect": diff / pooled if pooled > 0 else 0.0,
        "n_a": float(len(x)),
        "n_b": float(len(y)),
    }


def bootstrap_interval(values, statistic=None, confidence: float = .95, *, resamples: int = 2000, seed: int = 0):
    """Nonparametric percentile interval for a chosen scalar statistic."""
    import random
    xs = [float(x) for x in values]
    if not xs:
        raise ValueError("at least one observation required")
    if not 0 < confidence < 1 or resamples < 100:
        raise ValueError("invalid confidence/resamples")
    statistic = statistic or statistics.fmean
    rng = random.Random(seed)
    n = len(xs)
    draws = [float(statistic([xs[rng.randrange(n)] for _ in range(n)])) for _ in range(resamples)]
    alpha = (1 - confidence) / 2
    return {
        "estimate": float(statistic(xs)),
        "ci_low": _q(draws, alpha),
        "ci_high": _q(draws, 1 - alpha),
        "confidence": confidence,
        "resamples": float(resamples),
        "method": "percentile_bootstrap",
    }


def paired_contrast(a, b, confidence: float = .95):
    """Paired differences preserve experimental blocking/seed structure."""
    x, y = [float(v) for v in a], [float(v) for v in b]
    if len(x) != len(y) or not x:
        raise ValueError("paired samples must be nonempty and aligned")
    d = [u - v for u, v in zip(x, y)]
    out = summarize(d, confidence)
    out["difference"] = out.pop("mean")
    out["method"] = "paired_difference"
    return out


def precision_stop(values, *, half_width: float, confidence: float = .95, min_n: int = 5) -> dict[str, float | bool]:
    """A transparent replication stopping rule based on interval precision, not significance."""
    xs = [float(x) for x in values]
    if len(xs) < min_n:
        return {"stop": False, "n": float(len(xs)), "half_width": float("inf"), "target": half_width}
    s = summarize(xs, confidence)
    hw = (s["ci_high"] - s["ci_low"]) / 2
    return {"stop": hw <= half_width, "n": float(len(xs)), "half_width": hw, "target": half_width}
