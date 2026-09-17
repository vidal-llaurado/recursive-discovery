"""Model-driven research loop.

The model chooses scientific moves; the consequence machinery records what happened.
"""
from __future__ import annotations

from collections.abc import Callable
import json
import shlex
import subprocess
from typing import Any

from .context import compile_context, render_context
from .core import Artifact, Kernel, Ledger, Task, frontier
from .propose import activate, candidate_set
from .search import multi_search, read_source

Model = Callable[[str], str]
Executor = Callable[[Task, Artifact, Ledger, Kernel], Any]


def _parse(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        a, b = text.find("{"), text.rfind("}")
        if a >= 0 and b > a:
            return json.loads(text[a:b + 1])
        raise


class CommandModel:
    """Connect any capable model through a tiny stdin/stdout contract.

    Command receives one JSON prompt on stdin and must emit JSON/text on stdout. This keeps the
    research software independent of a particular model vendor or SDK.
    """

    def __init__(self, command: str | list[str], *, timeout: float = 300.0):
        self.argv = shlex.split(command) if isinstance(command, str) else list(command)
        self.timeout = timeout

    def __call__(self, prompt: str) -> str:
        p = subprocess.run(
            self.argv, input=prompt, text=True, capture_output=True,
            timeout=self.timeout, check=False,
        )
        if p.returncode != 0:
            raise RuntimeError(p.stderr.strip() or f"model command exited {p.returncode}")
        return p.stdout


def choose_candidate(
    ledger: Ledger,
    kernel: Kernel,
    task: Task,
    candidates: list[Artifact],
    model: Model,
    *,
    source_before: str | None = None,
    max_context_chars: int = 14_000,
) -> Artifact:
    """Ask the capable model to choose a scientific move, rather than hard-code a utility score."""
    if not candidates:
        raise ValueError("no candidates")
    if len(candidates) == 1:
        return candidates[0]
    packet = compile_context(
        ledger, kernel, task, max_chars=max_context_chars, source_before=source_before,
    )
    options = [{
        "id": c.id,
        "object_kind": c.data.get("object_kind"),
        "title": c.data.get("title"),
        "commitment": c.data.get("commitment"),
        "predictions": c.data.get("predictions"),
        "falsifier": c.data.get("falsifier"),
        "self_assessed_gain": c.data.get("gain"),
        "self_assessed_cost": c.data.get("cost"),
    } for c in candidates]
    answer = _parse(model(json.dumps({
        "mode": "select_candidate",
        "task": packet["task"],
        "context": render_context(packet),
        "candidates": options,
        "instruction": (
            "Choose the scientifically strongest next move using your own research judgment. "
            "Treat gain/cost fields only as proposal self-assessments, not mechanical scores. "
            "Return JSON {id:<candidate id>, reason:<brief externally legible rationale>}."
        ),
    }, ensure_ascii=False, separators=(",", ":"))))
    cid = str(answer.get("id", ""))
    match = next((c for c in candidates if c.id == cid), None)
    if match is None:
        raise ValueError("model selected an unknown candidate")
    ledger.put("selection", {
        "reason": str(answer.get("reason", ""))[:2000],
        "candidate": cid,
    }, {"candidate": [cid], "target": [task.target]}, by="research-controller")
    return match


def research_step(
    ledger: Ledger,
    kernel: Kernel,
    task: Task,
    model: Model,
    *,
    execute: Executor | None = None,
    source_before: str | None = None,
    max_candidates: int = 5,
    search_results: int = 10,
) -> dict[str, Any]:
    """Search -> propose diverse commitments -> subjective model selection -> optional execution."""
    def retriever(q: str):
        return multi_search(q, max_results=search_results, source_before=source_before)

    candidates = candidate_set(
        ledger, kernel, task, model,
        actor="research-model", retriever=retriever, reader=read_source,
        source_before=source_before, max_candidates=max_candidates,
    )
    if not candidates:
        return {"status": "no_candidates", "task": task}
    selected = choose_candidate(
        ledger, kernel, task, candidates, model, source_before=source_before,
    )
    live = activate(ledger, selected, by="research-controller")
    if execute is not None:
        execute(task, live, ledger, kernel)
    return {
        "status": "executed" if execute else "activated",
        "task": task, "candidates": candidates, "selected": selected, "live": live,
    }


def drive_research(
    ledger: Ledger,
    kernel: Kernel,
    model: Model,
    execute: Executor,
    *,
    source_before: str | None = None,
    max_steps: int = 30,
) -> dict[str, Any]:
    """Drive the existing scientific frontier with a powerful searchable proposal model."""
    history = []
    for step in range(max_steps):
        tasks = frontier(ledger, kernel)
        if not tasks:
            return {"status": "complete", "steps": step, "history": history}
        # The capable model values scientific tasks; ordering here only preserves deterministic UI.
        task = tasks[0]
        result = research_step(
            ledger, kernel, task, model, execute=execute, source_before=source_before,
        )
        history.append({
            "verb": task.verb, "target": task.target,
            "status": result["status"],
            "selected": result.get("selected").id if result.get("selected") else None,
        })
        if result["status"] == "no_candidates":
            return {"status": "blocked", "steps": step + 1, "history": history}
    return {"status": "budget_exhausted", "steps": max_steps, "history": history}
