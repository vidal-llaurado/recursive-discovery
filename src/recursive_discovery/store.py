"""Durable scientific storage: SQLite graph + content-addressed blobs.

This module changes persistence, not epistemics. Its API deliberately mirrors `core.Ledger`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile

from .core import Artifact, _hash, _json, _refs


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS artifacts(
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    data_json TEXT NOT NULL,
    by_actor TEXT NOT NULL,
    t REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS refs(
    artifact_id TEXT NOT NULL,
    role TEXT NOT NULL,
    position INTEGER NOT NULL,
    target_id TEXT NOT NULL,
    PRIMARY KEY(artifact_id, role, position),
    FOREIGN KEY(artifact_id) REFERENCES artifacts(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS refs_target ON refs(target_id, role);
CREATE INDEX IF NOT EXISTS artifacts_kind_t ON artifacts(kind, t);
"""


def _search_text(kind: str, data: dict[str, Any], by: str) -> str:
    return " ".join([kind, by, json.dumps(data, ensure_ascii=False, sort_keys=True)])


class SQLiteLedger:
    """Transactional artifact DAG with the same small API as `Ledger`.

    FTS is opportunistic: if SQLite was built without FTS5, lexical search falls back to LIKE.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=30.0)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.fts = True
        try:
            self.db.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS artifact_fts USING fts5(id UNINDEXED, text)"
            )
        except sqlite3.OperationalError:
            self.fts = False
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> "SQLiteLedger":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def contains(self, aid: str) -> bool:
        return self.db.execute("SELECT 1 FROM artifacts WHERE id=?", (aid,)).fetchone() is not None

    def put(
        self,
        kind: str,
        data: dict[str, Any],
        refs: dict[str, Iterable[str]] | None = None,
        by: str = "agent",
    ) -> Artifact:
        import time

        refs2 = _refs(refs)
        content = {"kind": kind, "data": data, "refs": refs2, "by": by}
        aid = _hash(content)
        existing = self.db.execute("SELECT 1 FROM artifacts WHERE id=?", (aid,)).fetchone()
        if existing:
            return self.get(aid)

        t = time.time()
        with self.db:
            self.db.execute(
                "INSERT INTO artifacts(id,kind,data_json,by_actor,t) VALUES(?,?,?,?,?)",
                (aid, kind, _json(data), by, t),
            )
            for role, targets in refs2.items():
                for i, target in enumerate(targets):
                    self.db.execute(
                        "INSERT INTO refs(artifact_id,role,position,target_id) VALUES(?,?,?,?)",
                        (aid, role, i, target),
                    )
            if self.fts:
                self.db.execute(
                    "INSERT INTO artifact_fts(id,text) VALUES(?,?)",
                    (aid, _search_text(kind, data, by)),
                )
        return Artifact(aid, kind, data, refs2, by, t)

    def _artifact(self, row: sqlite3.Row) -> Artifact:
        ref_rows = self.db.execute(
            "SELECT role,target_id FROM refs WHERE artifact_id=? ORDER BY role,position",
            (row["id"],),
        ).fetchall()
        refs: dict[str, list[str]] = {}
        for r in ref_rows:
            refs.setdefault(r["role"], []).append(r["target_id"])
        return Artifact(
            row["id"], row["kind"], json.loads(row["data_json"]),
            {k: tuple(v) for k, v in refs.items()}, row["by_actor"], row["t"],
        )

    def get(self, aid: str) -> Artifact:
        row = self.db.execute("SELECT * FROM artifacts WHERE id=?", (aid,)).fetchone()
        if row is None:
            raise KeyError(aid)
        return self._artifact(row)

    def all(self, kind: str | None = None) -> list[Artifact]:
        if kind is None:
            rows = self.db.execute("SELECT * FROM artifacts ORDER BY t,id").fetchall()
        else:
            rows = self.db.execute(
                "SELECT * FROM artifacts WHERE kind=? ORDER BY t,id", (kind,)
            ).fetchall()
        return [self._artifact(r) for r in rows]

    def children(self, aid: str, kind: str | None = None, role: str | None = None) -> list[Artifact]:
        where = ["r.target_id=?"]
        args: list[Any] = [aid]
        if role is not None:
            where.append("r.role=?")
            args.append(role)
        if kind is not None:
            where.append("a.kind=?")
            args.append(kind)
        rows = self.db.execute(
            "SELECT DISTINCT a.* FROM artifacts a JOIN refs r ON r.artifact_id=a.id "
            + "WHERE " + " AND ".join(where) + " ORDER BY a.t,a.id",
            args,
        ).fetchall()
        return [self._artifact(r) for r in rows]

    def related(self, aid: str, role: str) -> list[Artifact]:
        rows = self.db.execute(
            "SELECT a.* FROM refs r JOIN artifacts a ON a.id=r.target_id "
            "WHERE r.artifact_id=? AND r.role=? ORDER BY r.position",
            (aid, role),
        ).fetchall()
        return [self._artifact(r) for r in rows]

    def search(self, query: str, limit: int = 20) -> list[Artifact]:
        """Lexical artifact retrieval used by the context compiler."""
        q = " ".join(query.split())
        if not q:
            return []
        if self.fts:
            # FTS syntax is powerful but user/model text may contain punctuation. Quote tokens.
            terms = [x.strip('"\'()[]{}:,*') for x in q.split()]
            terms = [x for x in terms if x]
            fts_q = " OR ".join(f'"{x}"' for x in terms[:16])
            if fts_q:
                try:
                    rows = self.db.execute(
                        "SELECT a.* FROM artifact_fts f JOIN artifacts a ON a.id=f.id "
                        "WHERE artifact_fts MATCH ? ORDER BY bm25(artifact_fts) LIMIT ?",
                        (fts_q, int(limit)),
                    ).fetchall()
                    return [self._artifact(r) for r in rows]
                except sqlite3.OperationalError:
                    pass
        pattern = f"%{q[:200]}%"
        rows = self.db.execute(
            "SELECT * FROM artifacts WHERE data_json LIKE ? OR kind LIKE ? ORDER BY t DESC LIMIT ?",
            (pattern, pattern, int(limit)),
        ).fetchall()
        return [self._artifact(r) for r in rows]

    def counts(self) -> dict[str, int]:
        rows = self.db.execute(
            "SELECT kind,COUNT(*) AS n FROM artifacts GROUP BY kind ORDER BY kind"
        ).fetchall()
        return {r["kind"]: int(r["n"]) for r in rows}


class BlobStore:
    """Minimal immutable content-addressed store.

    Files are stored by SHA256. Directories become a manifest whose entries point to file blobs.
    """

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.blobs = self.root / "blobs"
        self.manifests = self.root / "manifests"
        self.blobs.mkdir(parents=True, exist_ok=True)
        self.manifests.mkdir(parents=True, exist_ok=True)

    def put_bytes(self, data: bytes) -> str:
        digest = hashlib.sha256(data).hexdigest()
        dst = self.blobs / digest
        if not dst.exists():
            tmp = dst.with_suffix(f".{os.getpid()}.tmp")
            tmp.write_bytes(data)
            os.replace(tmp, dst)
        return digest

    def put_file(self, path: str | Path) -> str:
        return self.put_bytes(Path(path).read_bytes())

    def put_path(self, path: str | Path) -> dict[str, Any]:
        path = Path(path)
        if path.is_file():
            return {"kind": "file", "sha256": self.put_file(path)}
        if not path.is_dir():
            raise FileNotFoundError(path)
        entries = []
        for p in sorted(x for x in path.rglob("*") if x.is_file()):
            entries.append({
                "path": p.relative_to(path).as_posix(),
                "sha256": self.put_file(p),
                "mode": p.stat().st_mode & 0o777,
            })
        raw = _json(entries).encode()
        digest = hashlib.sha256(raw).hexdigest()
        manifest = self.manifests / f"{digest}.json"
        if not manifest.exists():
            manifest.write_bytes(raw)
        return {"kind": "directory", "sha256": digest, "entries": len(entries)}

    def path(self, digest: str) -> Path:
        p = self.blobs / digest
        if not p.exists():
            raise KeyError(digest)
        return p

    def materialize(self, obj: dict[str, Any], destination: str | Path) -> Path:
        destination = Path(destination)
        if obj["kind"] == "file":
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.path(obj["sha256"]), destination)
            return destination
        manifest = json.loads((self.manifests / f"{obj['sha256']}.json").read_text())
        destination.mkdir(parents=True, exist_ok=True)
        for entry in manifest:
            out = destination / entry["path"]
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.path(entry["sha256"]), out)
            try:
                out.chmod(entry["mode"])
            except OSError:
                pass
        return destination

    def temp_materialize(self, obj: dict[str, Any]):
        td = tempfile.TemporaryDirectory()
        target = Path(td.name) / ("object" if obj["kind"] == "file" else "tree")
        self.materialize(obj, target)
        return td, target
