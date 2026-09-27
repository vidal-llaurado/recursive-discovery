"""Optional telemetry recording with bounded storage and redacted content capture.

Trace IDs stay outside model requests and signed artifacts.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from pathlib import Path
from typing import Any, Callable
import inspect
import json
import math
import os
import re
import secrets
import sqlite3
import threading
import time
import traceback
import warnings

_CURRENT: ContextVar[Recorder | None] = ContextVar("rd_recorder", default=None)
_SPAN: ContextVar[dict | None] = ContextVar("rd_span", default=None)
SECRET = re.compile(r"^(?:_sig|authorization|proxy.authorization|cookie|set.cookie|password|passwd|secret|.*(?:api|private|signing)[_-]?key|access[_-]?token|refresh[_-]?token|token|env|credentials)$", re.I)
TOKEN = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+|\b(?:sk|ghp|github_pat)-[A-Za-z0-9_-]{12,}|\bghp_[A-Za-z0-9]{12,}")
CORRELATIONS = ("mission_id", "revision_id", "branch_id", "target_id", "decision_id", "session_id", "turn")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False)


def redact(value: Any, depth: int = 0) -> Any:
    """Best-effort key/token redaction, not a guarantee for secrets in arbitrary prose."""
    if depth > 30:
        return "[depth limit]"
    if isinstance(value, dict):
        return {str(k): "[redacted]" if SECRET.fullmatch(str(k)) else redact(v, depth + 1)
                for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [redact(v, depth + 1) for v in value]
    if isinstance(value, str):
        # Context is a JSON string inside a model request. Redact it recursively as well.
        if value.lstrip().startswith(("{", "[")):
            try:
                return _json(redact(json.loads(value), depth + 1))
            except (ValueError, TypeError, RecursionError):
                pass
        value = TOKEN.sub(lambda m: (m.group(1) or "") + "[redacted]", value)
        value = re.sub(r"(https?://)[^/\s:@]+:[^/\s@]+@", r"\1[redacted]@", value)
        return re.sub(r"(?i)([?&](?:token|key|api_key|secret|password)=)[^&\s\"']+", r"\1[redacted]", value)
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else "[non-finite]"
    return "[unsupported type]"


class Recorder:
    """Record telemetry in a separate SQLite database.

    Writes have a bounded busy wait and per-activation event/byte limits. Errors update
    health counters without replacing research results. The recorder is thread-safe
    within one process; research writers still require serialization.
    """
    def __init__(self, directory: str | Path, *, capture: str = "metadata",
                 max_events: int = 100_000, max_bytes: int = 128_000_000,
                 max_payload_bytes: int = 1_000_000):
        if capture not in {"metadata", "content"}:
            raise ValueError("capture must be metadata or content")
        if min(max_events, max_bytes, max_payload_bytes) < 1:
            raise ValueError("telemetry limits must be positive")
        self.directory = Path(directory).resolve()
        self.capture = capture
        self.max_events, self.max_bytes = max_events, max_bytes
        self.max_payload_bytes = max_payload_bytes
        self.lock = threading.RLock()
        self.db: sqlite3.Connection | None = None
        self.written = self.bytes_written = self.dropped = self.failures = 0
        self.closed = self.warned = False
        self.error_type: str | None = None
        # Dedicated identifiers, generated independently of scientific/model RNGs.
        self.recording_id = secrets.token_hex(16)
        try:
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.directory.chmod(0o700)
            path = self.directory / "events.sqlite3"
            fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
            os.close(fd)
            path.chmod(0o600)
            self.db = sqlite3.connect(path, timeout=0.1, check_same_thread=False)
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.executescript("""
              CREATE TABLE IF NOT EXISTS events(
                seq INTEGER PRIMARY KEY AUTOINCREMENT, recording_id TEXT NOT NULL,
                timestamp_ns INTEGER NOT NULL, trace_id TEXT NOT NULL, span_id TEXT NOT NULL,
                parent_span_id TEXT, phase TEXT NOT NULL, name TEXT NOT NULL,
                mission_id TEXT, decision_id TEXT, artifact_id TEXT,
                attributes TEXT NOT NULL, capture TEXT NOT NULL, payload TEXT);
              CREATE INDEX IF NOT EXISTS events_trace ON events(trace_id,seq);
              CREATE INDEX IF NOT EXISTS events_mission ON events(mission_id,seq);
              CREATE INDEX IF NOT EXISTS events_name ON events(name,seq);
              CREATE INDEX IF NOT EXISTS events_artifact ON events(artifact_id,seq);
              CREATE TABLE IF NOT EXISTS health(
                recording_id TEXT PRIMARY KEY, capture TEXT NOT NULL, written INTEGER NOT NULL,
                dropped INTEGER NOT NULL, failures INTEGER NOT NULL, bytes_written INTEGER NOT NULL,
                closed INTEGER NOT NULL, error_type TEXT);
              PRAGMA user_version=1;
            """)
            self.db.execute("INSERT INTO health VALUES(?,?,?,?,?,?,?,?)",
                (self.recording_id, capture, 0, 0, 0, 0, 0, None))
            self.db.commit()
        except Exception as error:
            self._failed(error)

    def _failed(self, error: Exception) -> None:
        self.failures += 1
        self.error_type = type(error).__name__
        if not self.warned:
            self.warned = True
            try:
                warnings.warn("RD telemetry unavailable or dropping events; research continues. Inspect telemetry health.", RuntimeWarning, stacklevel=2)
            except Exception:
                pass  # Even warnings-as-errors must not mask a research exception.

    def emit(self, name: str, attributes: dict | None = None, *, phase: str = "event",
             payload: Any = None, trace: dict | None = None) -> None:
        with self.lock:
            if self.closed or self.db is None:
                self.dropped += 1
                return
            if self.written >= self.max_events or self.bytes_written >= self.max_bytes:
                self.dropped += 1
                self._health()
                return
            try:
                trace = trace or _SPAN.get() or {"trace_id": secrets.token_hex(16), "span_id": secrets.token_hex(8)}
                attrs = {**trace.get("correlations", {}), **(attributes or {})}
                attrs = redact(attrs)
                body, capture = None, "metadata_only"
                if self.capture == "content" and payload is not None:
                    raw = payload() if callable(payload) else payload
                    clean = redact(raw)
                    body = _json(clean)
                    capture = "content_redacted"  # Never label a best-effort redacted copy as exact.
                    if len(body.encode()) > self.max_payload_bytes:
                        capture, body = "omitted_payload_limit", None
                encoded = _json(attrs)
                if len(encoded.encode()) > 256_000:
                    self.dropped += 1
                    self._health()
                    return
                cost = len(encoded.encode()) + (len(body.encode()) if body else 0) + 256
                if self.bytes_written + cost > self.max_bytes:
                    self.dropped += 1
                    self._health()
                    return
                with self.db:
                    self.db.execute("INSERT INTO events(recording_id,timestamp_ns,trace_id,span_id,parent_span_id,phase,name,mission_id,decision_id,artifact_id,attributes,capture,payload) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (self.recording_id, time.time_ns(), trace["trace_id"], trace["span_id"],
                         trace.get("parent_span_id"), phase, name,
                         attrs.get("mission_id"), attrs.get("decision_id"), attrs.get("artifact_id"), encoded, capture, body))
                self.written += 1
                self.bytes_written += cost
                self._health()
            except Exception as error:
                self.dropped += 1
                self._failed(error)

    def _health(self) -> None:
        if self.db is None:
            return
        try:
            with self.db:
                self.db.execute("INSERT OR REPLACE INTO health VALUES(?,?,?,?,?,?,?,?)",
                    (self.recording_id, self.capture, self.written, self.dropped,
                     self.failures, self.bytes_written, int(self.closed), self.error_type))
        except Exception as error:
            self._failed(error)

    def close(self) -> None:
        with self.lock:
            if self.closed:
                return
            self.closed = True
            if self.db is not None:
                try:
                    with self.db:
                        self.db.execute("INSERT OR REPLACE INTO health VALUES(?,?,?,?,?,?,?,?)",
                            (self.recording_id, self.capture, self.written, self.dropped,
                             self.failures, self.bytes_written, 1, self.error_type))
                    self.db.close()
                except Exception as error:
                    self._failed(error)


def enabled() -> bool:
    return _CURRENT.get() is not None


def emit(name: str, attributes: dict | None = None, *, payload: Any = None) -> None:
    recorder = _CURRENT.get()
    if recorder is not None:
        recorder.emit(name, attributes, payload=payload)


@contextmanager
def span(name: str, **attributes):
    recorder = _CURRENT.get()
    if recorder is None:
        yield None
        return
    parent = _SPAN.get()
    inherited = parent.get("correlations", {}) if parent else {}
    trace = {"trace_id": parent["trace_id"] if parent else secrets.token_hex(16),
             "span_id": secrets.token_hex(8), "parent_span_id": parent["span_id"] if parent else None,
             "correlations": {**inherited, **{k: v for k, v in attributes.items() if k in CORRELATIONS}}}
    token = _SPAN.set(trace)
    start = time.perf_counter_ns()
    recorder.emit(name, attributes, phase="start", trace=trace)
    status, exception = "OK", None
    try:
        yield trace
    except BaseException as error:
        status, exception = "ERROR", type(error).__name__
        recorder.emit("exception", {"error_type": exception},
                      payload=lambda: {"message": str(error), "traceback": traceback.format_exc()})
        raise
    finally:
        recorder.emit(name, {"status": status, "elapsed_ns": time.perf_counter_ns() - start,
                            **({"error_type": exception} if exception else {})}, phase="end", trace=trace)
        _SPAN.reset(token)


def traced(name: str):
    """Observe a boundary without changing its arguments, return value, or raised exception."""
    def decorate(fn):
        signature = inspect.signature(fn)
        @wraps(fn)
        def call(*args, **kwargs):
            if not enabled():
                return fn(*args, **kwargs)
            attrs = {"session_id": secrets.token_hex(8)} if name == "session" else {}
            try:
                bound = signature.bind_partial(*args, **kwargs).arguments
                scope, task = bound.get("scope"), bound.get("task")
                for key in ("mission", "branch"):
                    if isinstance(bound.get(key), str):
                        attrs[key + "_id"] = bound[key]
                if scope is not None:
                    attrs.update(mission_id=scope.root.id, revision_id=scope.revision.id)
                if task is not None:
                    attrs.update(target_id=task.target, task_verb=task.verb)
                target = bound.get("target")
                if getattr(target, "id", None):
                    attrs["target_id"] = target.id
            except Exception:
                pass
            with span(name, **attrs):
                result = fn(*args, **kwargs)
                if isinstance(result, dict) and isinstance(result.get("status"), str):
                    emit("operation.result", {"operation": name, "status": result["status"]})
                return result
        return call
    return decorate


@contextmanager
def observe(directory: str | Path, *, ledger=None, capture: str = "metadata", **limits):
    """Enable recording for this context.

    An optional ledger inventory captures the starting state. Keep the telemetry directory
    outside untrusted worker mounts.
    """
    recorder = Recorder(directory, capture=capture, **limits)
    current, previous_span = _CURRENT.set(recorder), _SPAN.set(None)
    try:
        with span("recording"):
            emit("recording.config", {"capture_mode": capture, "schema_version": 1})
            if ledger is not None:
                inventory(ledger)
            yield recorder
            emit("telemetry.health", {"written": recorder.written, "dropped": recorder.dropped,
                                      "failures": recorder.failures})
    finally:
        _SPAN.reset(previous_span)
        _CURRENT.reset(current)
        recorder.close()


def record_artifact(artifact, *, snapshot: bool = False) -> None:
    """Record selected ledger metadata in the telemetry store."""
    if not enabled():
        return
    try:
        d = artifact.data
        fields = {}
        if artifact.kind == "attention":
            fields["state"] = d.get("state")
        if artifact.kind == "evidence":
            fields.update({k: d[k] for k in ("lane", "returncode", "elapsed_s") if k in d})
        if artifact.kind == "evidence":
            fields.update(execution_verdict=d.get("verdict"), signature_present=isinstance(d.get("_sig"), str), verification="not_checked_by_observer")
        attrs = {"artifact_id": artifact.id, "artifact_kind": artifact.kind,
                 "created_at": artifact.t, "refs": {k: list(v) for k, v in artifact.refs.items()}, **fields}
        if artifact.kind == "mission":
            attrs["mission_id"] = artifact.id
        elif artifact.refs.get("mission"):
            attrs["mission_id"] = artifact.refs["mission"][0]
        if artifact.refs.get("scope"):
            attrs["scope_id"] = artifact.refs["scope"][0]
        emit("artifact.snapshot" if snapshot else "artifact.append", attrs,
             payload=lambda: {"id": artifact.id, "kind": artifact.kind, "data": d,
                              "refs": artifact.refs, "by": artifact.by, "t": artifact.t})
    except Exception as error:
        recorder = _CURRENT.get()
        if recorder:
            recorder._failed(error)


def inventory(ledger) -> None:
    if not enabled():
        return
    try:
        with span("inventory"):
            count = 0
            for artifact in ledger.all():
                record_artifact(artifact, snapshot=True)
                count += 1
            emit("inventory.complete", {"artifacts": count, "historical_replay": False})
    except Exception as error:
        _CURRENT.get()._failed(error)


def call_model(model, request: str, **attributes):
    """Observe the actual final request; do not infer token usage from character counts."""
    if not enabled():
        return model(request)
    with span("model.call", request_chars=len(request), **attributes):
        emit("model.request", {"request_chars": len(request)}, payload=lambda: {"request": request})
        response = model(request)
        emit("model.response", {"response_chars": len(response) if isinstance(response, str) else None},
             payload=lambda: {"response": response})
        return response


def usage(*, input_tokens: int | None = None, output_tokens: int | None = None,
          cost: float | None = None, currency: str | None = None) -> None:
    """Optional bridge hook for provider-reported usage. Missing counts remain unknown."""
    data = {}
    for key, value in (("input_tokens", input_tokens), ("output_tokens", output_tokens)):
        if value is not None:
            if type(value) is not int or value < 0:
                raise ValueError("token usage must be a nonnegative integer")
            data[key] = value
    if cost is not None:
        if not isinstance(cost, (int, float)) or isinstance(cost, bool) or not math.isfinite(cost) or cost < 0:
            raise ValueError("cost must be finite and nonnegative")
        if not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency):
            raise ValueError("cost needs a three-letter currency")
        data.update(cost=cost, currency=currency)
    if data:
        emit("model.usage", {**data, "usage_source": "provider_reported"})
