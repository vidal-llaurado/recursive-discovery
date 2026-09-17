"""Project layout for the finished local research machine."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import json

from .store import BlobStore, SQLiteLedger
from .worker import WorkerKernel
from .instruments import install_instruments


@dataclass
class Project:
    root: Path
    ledger: SQLiteLedger
    blobs: BlobStore
    kernel: WorkerKernel
    config: dict

    def close(self) -> None:
        self.ledger.close()


def init_project(path: str | Path) -> Project:
    root = Path(path).resolve()
    root.mkdir(parents=True, exist_ok=True)
    for name in ("blobs", "work", "reports", "sealed"):
        (root / name).mkdir(exist_ok=True)
    config_path = root / "project.json"
    if not config_path.exists():
        config_path.write_text(json.dumps({
            "version": 1,
            "context_chars": 16000,
        }, indent=2, sort_keys=True))
    return open_project(root)


def open_project(path: str | Path) -> Project:
    root = Path(path).resolve()
    if not root.exists():
        raise FileNotFoundError(root)
    config_path = root / "project.json"
    config = json.loads(config_path.read_text()) if config_path.exists() else {}
    return Project(
        root=root,
        ledger=SQLiteLedger(root / "science.sqlite3"),
        blobs=BlobStore(root / "blobs"),
        kernel=WorkerKernel(root / "kernel.key"),
        config=config,
    )
