"""Frontier runner.

The runtime executes tasks that have explicit execution machinery. Everything else is handed to
a model session or a caller-supplied policy.
"""
from __future__ import annotations
from .telemetry import traced, emit

from pathlib import Path
import subprocess
from typing import Any
from collections.abc import Callable

from .core import Artifact, Kernel, Ledger, Task, frontier
from .math import install_math, verify_claim, tools_for, values_for
Model = Callable[[str], str]
from .session import choose_task, research_session
from .replay import record_decision, record_outcome, execution_attempt, attempt_history
from .store import BlobStore
from .investigation import Scope, set_attention, include, Conflict
from .agenda import available_work, choose_work
from .schema import require_inputs


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

    @traced("runtime.execute")
    def execute(self, task: Task, *, scope: Scope | None = None) -> list[Artifact]:
        target = self.ledger.get(task.target)
        if scope and not scope.allowed(target.id):
            raise PermissionError("target is outside the mission information boundary")
        if task.verb == "verify":
            selected = tools_for(target, self.math_tools)
            for tool in selected:
                if scope and not scope.tool_allowed(tool.id):
                    raise PermissionError("mathematical checker is not permitted by this mission")
                require_inputs(self.ledger, tool, values_for(target))
            return verify_claim(self.ledger, self.kernel, target, self.math_tools, cwd=self.root)
        if task.verb == "run":
            tool = self._tool(target)
            if tool is None:
                return []
            if scope and not scope.tool_allowed(tool.id):
                raise PermissionError("experiment tool is not permitted by this mission")
            values = dict(target.data.get("values", {}))
            if target.data.get("path"):
                values.setdefault("path", str(target.data["path"]))
            require_inputs(self.ledger, tool, values)
            seeds = target.data.get("seeds")
            if seeds:
                from .lab import replicate
                result = replicate(
                    self.ledger, self.kernel, tool, target,
                    values=values, seeds=seeds, cwd=self.root,
                    bundle_store=self.blobs,
                )
                return result["evidence"]
            return [self.kernel.use(self.ledger, tool, target, values, cwd=self.root, seed=0)]
        return []

    @traced("runtime")
    def run(
        self,
        *,
        model: Model | None = None,
        source_before: str | None = None,
        max_steps: int = 50,
        mission: str | None = None,
        scanners: tuple = (),
        context_chars: int = 18_000,
    ) -> dict[str, Any]:
        history = []
        unchanged = 0
        last = None
        for i in range(max_steps):
            scope = Scope(self.ledger, mission, source_before=source_before) if mission else None
            work = None
            if scope:
                agenda = available_work(self.ledger, self.kernel, scope, scanners=scanners)
                tasks = [w.task for w in agenda["eligible"]]
                if not tasks:
                    return {"status": "idle", "steps": i, "history": history,
                            "suppressed": agenda["suppressed"]}
                work = choose_work(self.ledger, self.kernel, scope, model, work=agenda["eligible"],
                                   max_chars=context_chars)
                if work is None:
                    return {"status": "deferred", "steps": i, "history": history}
                task = work.task
                refreshed = Scope(self.ledger, mission, source_before=source_before)
                if refreshed.revision.id != scope.revision.id:
                    raise Conflict("mission changed during work selection")
                # A model callback or another writer can change eligibility while routing.
                if work.key not in {w.key for w in available_work(self.ledger, self.kernel, refreshed, scanners=scanners)["eligible"]}:
                    return {"status": "scope_changed", "steps": i, "history": history}
                scope = refreshed
            else:
                tasks = frontier(self.ledger, self.kernel)
                if not tasks:
                    return {"status": "complete", "steps": i, "history": history}
                task = choose_task(self.ledger, self.kernel, model,
                                   source_before=source_before) if model is not None else tasks[0]
                if task is None:
                    return {"status": "complete", "steps": i, "history": history}
            key = (task.verb, task.target)
            if self.can_execute(task):
                target = self.ledger.get(task.target)
                if task.verb == "verify":
                    selected_tools = tools_for(target, self.math_tools)
                    values = values_for(target)
                else:
                    selected_tools = [self._tool(target)]
                    values = dict(target.data.get("values", {}))
                    if target.data.get("path"):
                        values.setdefault("path", str(target.data["path"]))
                try:
                    plans = [execution_attempt(self.ledger, target, tool, values, cwd=self.root,
                             seeds=target.data.get("seeds") or [0]) for tool in selected_tools if tool]
                except (ValueError, KeyError, TypeError) as e:
                    plans = []  # The execution boundary below records malformed jobs as blocked.
                attempt = plans[0] if len(plans) == 1 else None
                previous = attempt_history(self.ledger, self.kernel, signature=attempt["signature"],
                             accept=scope.allowed if scope else None)["items"] if attempt else []
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
                    payload={"verb": task.verb, "target": task.target, "attempt": attempt,
                             "plans": plans, "prior_attempts": [x["decision"] for x in previous]},
                    by="research-runtime",
                )
                try:
                    out = self.execute(task, scope=scope)
                except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError) as e:
                    record_outcome(self.ledger, execution_decision, status="blocked", summary={"error": str(e)})
                    return {"status": "blocked", "steps": i, "history": history, "error": str(e)}
                if scope and out:
                    include(self.ledger, scope.root.id, [a.id for a in out],
                            branch=work.branches[0] if len(work.branches) == 1 else None, by="research-runtime")
                record_outcome(
                    self.ledger, execution_decision,
                    status="executed" if out else "no_consequence",
                    produced=out,
                    summary={"n": len(out), "execution_verdicts": [x.data.get("verdict") for x in out],
                             "qualification": "Execution status, not a scientific conclusion."},
                )
                history.append({"verb": task.verb, "target": task.target, "mode": "consequence", "n": len(out)})
            elif model is not None:
                out = research_session(
                    self.ledger, self.kernel, task, model,
                    root=self.root, source_before=source_before, context_chars=context_chars,
                    scope=scope, branch=work.branches[0] if work and len(work.branches) == 1 else None,
                )
                if scope and work.request and out["status"] in {"activated", "proposals", "no_candidates", "handoff"}:
                    current = Scope(self.ledger, mission, source_before=source_before)
                    if current.revision.id != scope.revision.id:
                        raise Conflict("mission changed before recording request disposition")
                    set_attention(self.ledger, work.request, scope.root.id,
                        "deferred" if out["status"] == "handoff" else "done",
                        expected=scope.state(work.request)["heads"],
                        reason="awaiting external handoff" if out["status"] == "handoff" else "session concluded; this is not scientific adjudication",
                        support=[x.id for x in out.get("live", [])], by="research-runtime")
                if out["status"] == "handoff":
                    return {"status": "handoff", "steps": i + 1, "history": history,
                            "capsule": out["capsule"].id}
                if out["status"] == "scope_changed":
                    return {"status": "scope_changed", "steps": i + 1, "history": history}
                history.append({
                    "verb": task.verb, "target": task.target, "mode": "research_session",
                    "status": out["status"],
                    "live": [x.id for x in out.get("live", [])],
                    "turns": out.get("turns"),
                })
            else:
                return {"status": "blocked", "steps": i, "task": task, "history": history}

            next_tasks = ([w.task for w in available_work(self.ledger, self.kernel,
                          Scope(self.ledger, mission, source_before=source_before), scanners=scanners)["eligible"]]
                          if mission else frontier(self.ledger, self.kernel))
            next_key = (next_tasks[0].verb, next_tasks[0].target) if next_tasks else None
            if next_key == key == last:
                unchanged += 1
            else:
                unchanged = 0
            last = next_key
            if unchanged >= 2:
                return {"status": "blocked", "steps": i + 1, "task": next_tasks[0] if next_tasks else None, "history": history}
        return {"status": "step_limit", "steps": max_steps, "history": history}
