"""Reproducible empirical execution built on the Kernel.

Execution records and measured outputs are stored as separate artifacts.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
from typing import Any, Iterable

from .core import Artifact, Kernel, Ledger
from .stats import summarize


def _digest(path: Path) -> str:
    if path.is_file():
        return hashlib.sha256(path.read_bytes()).hexdigest()
    rows = []
    for p in sorted(x for x in path.rglob("*") if x.is_file()):
        rows.append((p.relative_to(path).as_posix(), _digest(p)))
    return hashlib.sha256(json.dumps(rows, separators=(",", ":")).encode()).hexdigest()


def snapshot(
    ledger: Ledger,
    tool: Artifact,
    values: dict[str, Any],
    *,
    cwd: str | Path,
    store: Any,
    refs: dict[str, list[str]] | None = None,
) -> Artifact:
    """Content-address and retain every input the tool declared."""
    cwd = Path(cwd).resolve()
    cas = store if hasattr(store, "put_path") else None
    store_path = None if cas else Path(store).resolve()
    if store_path:
        blobs = store_path / "blobs"
        blobs.mkdir(parents=True, exist_ok=True)
    manifest = {}

    for template in tool.data.get("inputs", []):
        raw = str(template).format_map(values)
        path = Path(raw)
        path = path if path.is_absolute() else cwd / path
        path = path.resolve()
        if cas:
            obj = cas.put_path(path)
            manifest[raw] = obj
        else:
            digest = _digest(path)
            dest = blobs / digest
            if not dest.exists():
                if path.is_dir():
                    shutil.copytree(path, dest)
                else:
                    shutil.copy2(path, dest)
            manifest[raw] = {
                "sha256": digest,
                "kind": "directory" if path.is_dir() else "file",
            }

    bundle = ledger.put(
        "bundle",
        {"inputs": manifest, "tool": tool.id},
        {"tool": [tool.id], **(refs or {})},
        by="lab",
    )
    if store_path:
        manifests = store_path / "manifests"
        manifests.mkdir(exist_ok=True)
        (manifests / f"{bundle.id}.json").write_text(
            json.dumps(bundle.data, sort_keys=True, indent=2)
        )
    return bundle


def _json_result(stdout: str) -> Any:
    lines = [x for x in stdout.splitlines() if x.strip()]
    if not lines:
        raise ValueError("experiment produced no JSON result")
    return json.loads(lines[-1])


def run_json(
    ledger: Ledger,
    kernel: Kernel,
    tool: Artifact,
    experiment: Artifact,
    *,
    values: dict[str, Any],
    cwd: str | Path,
    seed: int,
    bundle: Artifact | None = None,
) -> tuple[Artifact, Artifact | None]:
    """Run one replicate. Signed evidence records execution; measurement records output."""
    evidence = kernel.use(ledger, tool, experiment, values, cwd=cwd, seed=seed)
    if evidence.data.get("verdict") != "pass":
        return evidence, None

    value = _json_result(evidence.data["stdout"])
    refs = {"experiment": [experiment.id], "evidence": [evidence.id]}
    if bundle:
        refs["bundle"] = [bundle.id]
    measurement = ledger.put(
        "measurement",
        {"seed": seed, "value": value},
        refs,
        by="lab",
    )
    return evidence, measurement


def replicate(
    ledger: Ledger,
    kernel: Kernel,
    tool: Artifact,
    experiment: Artifact,
    *,
    values: dict[str, Any],
    seeds: Iterable[int],
    cwd: str | Path,
    bundle_store: str | Path | None = None,
) -> dict[str, Any]:
    """Execute independent seeds and summarize numeric measurements."""
    seeds = list(seeds)
    if not seeds:
        raise ValueError("at least one seed required")

    bundle = None
    if bundle_store is not None:
        bundle = snapshot(
            ledger, tool, values, cwd=cwd, store=bundle_store,
            refs={"experiment": [experiment.id]},
        )

    evidence, measurements = [], []
    for seed in seeds:
        e, m = run_json(
            ledger, kernel, tool, experiment,
            values=values, cwd=cwd, seed=seed, bundle=bundle,
        )
        evidence.append(e)
        if m is not None:
            measurements.append(m)

    rows = [m.data["value"] for m in measurements if isinstance(m.data["value"], dict)]
    numeric = {}
    if rows:
        fields = set.intersection(*[
            {k for k, v in r.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
            for r in rows
        ])
        for field in sorted(fields):
            numeric[field] = summarize(r[field] for r in rows)

    summary = ledger.put(
        "empirical_summary",
        {
            "requested_replicates": len(seeds),
            "successful_replicates": len(measurements),
            "metrics": numeric,
        },
        {
            "experiment": [experiment.id],
            "measurements": [m.id for m in measurements],
            **({"bundle": [bundle.id]} if bundle else {}),
        },
        by="lab",
    )
    return {
        "evidence": evidence,
        "measurements": measurements,
        "summary": summary,
        "bundle": bundle,
    }


def replicate_until(
    ledger: Ledger,
    kernel: Kernel,
    tool: Artifact,
    experiment: Artifact,
    *,
    values: dict[str, Any],
    seeds: Iterable[int],
    cwd: str | Path,
    metric: str,
    half_width: float,
    min_n: int = 5,
    max_n: int = 50,
    bundle_store: Any | None = None,
) -> dict[str, Any]:
    """Replicate until a predeclared precision target is met or `max_n` is reached."""
    from .stats import precision_stop
    seeds = list(seeds)[:max_n]
    if len(seeds) < min_n:
        raise ValueError("not enough seeds for min_n")
    bundle = snapshot(
        ledger, tool, values, cwd=cwd, store=bundle_store,
        refs={"experiment": [experiment.id]},
    ) if bundle_store is not None else None
    evidence, measurements, observed = [], [], []
    stop = None
    for seed in seeds:
        e, m = run_json(
            ledger, kernel, tool, experiment,
            values=values, cwd=cwd, seed=seed, bundle=bundle,
        )
        evidence.append(e)
        if m is not None:
            measurements.append(m)
            value = m.data.get("value", {})
            if isinstance(value, dict) and isinstance(value.get(metric), (int, float)):
                observed.append(float(value[metric]))
        stop = precision_stop(observed, half_width=half_width, min_n=min_n)
        if stop["stop"]:
            break
    summary = ledger.put("empirical_summary", {
        "requested_max_replicates": max_n,
        "successful_replicates": len(measurements),
        "metric": metric,
        "precision_rule": stop,
        "metrics": {metric: summarize(observed)} if observed else {},
    }, {
        "experiment": [experiment.id],
        "measurements": [m.id for m in measurements],
        **({"bundle": [bundle.id]} if bundle else {}),
    }, by="lab")
    return {"evidence": evidence, "measurements": measurements, "summary": summary, "bundle": bundle}
