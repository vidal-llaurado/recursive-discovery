"""Frontier loop: select a task, execute it, record the consequence."""
from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from .core import Kernel, Ledger, Task, frontier
from .ops import allocate, bid, settle


Offer = dict[str, Any]
Propose = Callable[[Task, Ledger], Iterable[Offer]]
Execute = Callable[[Task, Any, Ledger, Kernel], Any]


def drive(
    ledger: Ledger,
    kernel: Kernel,
    propose: Propose,
    execute: Execute,
    *,
    budget: float = 1.0,
    max_cycles: int = 32,
    patience: int = 3,
) -> dict[str, Any]:
    """Run until the frontier closes, stalls, or reaches a safety bound.

    `propose` and `execute` are ordinary callables. There is intentionally no Agent class.
    """
    history = []
    dry = 0

    for cycle in range(max_cycles):
        before = frontier(ledger, kernel)
        if not before:
            return {"status": "complete", "cycles": cycle, "history": history}

        for task in before:
            for o in propose(task, ledger):
                bid(
                    ledger, task,
                    actor=o["actor"], gain=o["gain"], cost=o["cost"],
                    plan=o.get("plan", ""), attempt=cycle,
                )

        winners = allocate(ledger, before, budget)
        if not winners:
            return {"status": "stalled:no-bids", "cycles": cycle, "history": history}

        outcomes = []
        for task, winner in winners:
            now = frontier(ledger, kernel)
            if task.key not in {t.key for t in now}:
                continue
            execute(task, winner, ledger, kernel)
            after = frontier(ledger, kernel)
            outcomes.append(settle(ledger, task, winner, now, after, attempt=cycle))

        closed = sum(bool(o.data["closed"]) for o in outcomes)
        history.append({
            "cycle": cycle,
            "frontier": len(before),
            "winners": [(t.verb, b.by) for t, b in winners],
            "closed": closed,
        })
        dry = 0 if closed else dry + 1
        if dry >= patience:
            return {"status": "stalled:no-progress", "cycles": cycle + 1, "history": history}

    return {"status": "bounded", "cycles": max_cycles, "history": history}
