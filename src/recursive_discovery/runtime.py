"""Frontier runner.

The runtime executes tasks that have explicit execution machinery. Everything else is handed to
a model session or a caller-supplied policy.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .core import Artifact, Kernel, Ledger, Task, frontier
from .lab import replicate
from .math import install_math, verify_claim
from .research import Model
from .session import choose_task, research_session
from .replay import record_decision, record_outcome
from .store import BlobStore


class Runtime:
    def __init__(self, ledger: Ledger, kernel: Kernel, *, root: str | Path = "."):
        self.ledger, self.kernel, self.root = ledger, kernel, Path(root)
        self.blobs = BlobStore(self.root / "blobs")
        self.math_tools = install_math(ledger)

    def can_execute(self, task: Task) -> bool:
        target = self.ledger.get(task.target)
        if task.verb == "verify" and target.kind == "claim":
            return bool(target.data.get("formalism") and target.data.get("path"))
        if task.verb == "run" and target.kind == "experiment":
            return bool(self._tool(target))
        return False

    def _tool(self, experiment: Artifact) -> Artifact | None:
        refs = self.ledger.related(experiment.id, "tool")
        if refs:
            return refs[0]
        tid = experiment.data.get("tool_id")
        if tid:
            try:
                return self.ledger.get(str(tid))
            except KeyError:
                return None
        # Compact default for explicit Python experiment paths.
        path = experiment.data.get("path")
        if path:
            return self.ledger.put("tool", {
                "name": "python-experiment", "lane": "empirical", "verb": "run",
                "semantics": "experiment", "argv": [__import__('sys').executable, "{path}"],
                "inputs": ["{path}"],
            }, by="runtime")
        return None

    def execute(self, task: Task) -> list[Artifact]:
        target = self.ledger.get(task.target)
        if task.verb == "verify":
            return verify_claim(self.ledger, self.kernel, target, self.math_tools, cwd=self.root)
        if task.verb == "run":
            tool = self._tool(target)
            if tool is None:
                return []
            values = dict(target.data.get("values", {}))
            if target.data.get("path"):
                values.setdefault("path", str(target.data["path"]))
            seeds = target.data.get("seeds")
            if seeds:
                result = replicate(
                    self.ledger, self.kernel, tool, target,
                    values=values, seeds=seeds, cwd=self.root,
                    bundle_store=self.blobs,
                )
                return result["evidence"]
            return [self.kernel.use(self.ledger, tool, target, values, cwd=self.root, seed=0)]
        return []

    def run(
        self,
        *,
        model: Model | None = None,
        source_before: str | None = None,
        max_steps: int = 50,
    ) -> dict[str, Any]:
        history = []
        unchanged = 0
        last = None
        for i in range(max_steps):
            tasks = frontier(self.ledger, self.kernel)
            if not tasks:
                return {"status": "complete", "steps": i, "history": history}
            task = choose_task(
                self.ledger, self.kernel, model, source_before=source_before,
            ) if model is not None else tasks[0]
            if task is None:
                return {"status": "complete", "steps": i, "history": history}
            key = (task.verb, task.target)
            if self.can_execute(task):
                execution_decision = record_decision(
                    self.ledger, task,
                    action="execute_frontier_task",
                    model=self,
                    state={
                        "frontier": [
                            {"verb": x.verb, "lane": x.lane, "target": x.target, "why": x.why}
                            for x in tasks
                        ],
                        "selected": {"verb": task.verb, "lane": task.lane, "target": task.target},
                    },
                    frontier=tasks,
                    available_actions=["execute_frontier_task"],
                    payload={"verb": task.verb, "target": task.target},
                    by="research-runtime",
                )
                out = self.execute(task)
                record_outcome(
                    self.ledger, execution_decision,
                    status="executed" if out else "no_consequence",
                    produced=out,
                    summary={"n": len(out)},
                )
                history.append({"verb": task.verb, "target": task.target, "mode": "consequence", "n": len(out)})
            elif model is not None:
                out = research_session(
                    self.ledger, self.kernel, task, model,
                    root=self.root, source_before=source_before,
                )
                history.append({
                    "verb": task.verb, "target": task.target, "mode": "research_session",
                    "status": out["status"],
                    "live": [x.id for x in out.get("live", [])],
                    "turns": out.get("turns"),
                })
            else:
                return {"status": "blocked", "steps": i, "task": task, "history": history}

            next_tasks = frontier(self.ledger, self.kernel)
            next_key = (next_tasks[0].verb, next_tasks[0].target) if next_tasks else None
            if next_key == key == last:
                unchanged += 1
            else:
                unchanged = 0
            last = next_key
            if unchanged >= 2:
                return {"status": "blocked", "steps": i + 1, "task": next_tasks[0] if next_tasks else None, "history": history}
        return {"status": "step_limit", "steps": max_steps, "history": history}
