from .core import (
    Artifact, Task, Ledger, Kernel,
    valid_evidence, frontier, compress, promote,
)
from .ops import (
    stress, conjecture, conjectures, diagnose, discriminate, resolve, expand_representation,
    base_grammar, adopt_grammar, extend_grammar, invent_instrument, synthesize_mechanism,
    bid, settle, calibration, allocate,
)
from .loop import drive

__all__ = [
    "Artifact", "Task", "Ledger", "Kernel",
    "valid_evidence", "frontier", "compress", "promote",
    "stress", "conjecture", "conjectures", "diagnose", "discriminate", "resolve", "expand_representation",
    "base_grammar", "adopt_grammar", "extend_grammar", "invent_instrument", "synthesize_mechanism",
    "bid", "settle", "calibration", "allocate",
    "drive",
]

from .math import discover_math, install_math, tools_for, values_for
from .search import arxiv, parse_arxiv, remember
from .stats import summarize, contrast, bootstrap_interval, paired_contrast, precision_stop
from .lab import snapshot, run_json, replicate, replicate_until
from .context import authority, compile_context, render_context

__all__ += [
    "discover_math", "install_math", "tools_for", "values_for",
    "arxiv", "parse_arxiv", "remember",
    "summarize", "contrast", "bootstrap_interval", "paired_contrast", "precision_stop",
    "snapshot", "run_json", "replicate", "replicate_until",
    "authority", "compile_context", "render_context",
]

from .propose import candidate_set, activate
from .sealed import Vault, register_test, commit, evaluate
from .search import before

__all__ += [
    "candidate_set", "activate",
    "Vault", "register_test", "commit", "evaluate",
    "before",
]
from .store import SQLiteLedger, BlobStore
from .worker import RunResult, ProcessWorker, ContainerWorker, AutoWorker, WorkerKernel
from .research import CommandModel, choose_candidate, research_step, drive_research
from .report import markdown_report
from .project import Project, init_project, open_project
from .sealed_service import SealedEvaluator, SealedClient, verify_remote
from .search import crossref, openalex, multi_search, read_source, remember_text, dedupe
from .math import verify_claim

__all__ += [
    "SQLiteLedger", "BlobStore",
    "RunResult", "ProcessWorker", "ContainerWorker", "AutoWorker", "WorkerKernel",
    "CommandModel", "choose_candidate", "research_step", "drive_research",
    "markdown_report", "Project", "init_project", "open_project",
    "SealedEvaluator", "SealedClient", "verify_remote",
    "crossref", "openalex", "multi_search", "read_source", "remember_text", "dedupe",
    "verify_claim",
]

from .runtime import Runtime
__all__ += ["Runtime"]

from .instruments import Workbench, install_instruments
from .session import choose_task, research_session

__all__ += ["Workbench", "install_instruments", "choose_task", "research_session"]

from .replay import digest as replay_digest, record_decision, record_outcome, trace as replay_trace, replay_lookup

__all__ += ["replay_digest", "record_decision", "record_outcome", "replay_trace", "replay_lookup"]
