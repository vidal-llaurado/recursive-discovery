"""Optional, versioned descriptors for fields, references and context dependencies.

Stored objects receive conformance reports. Operations that require a descriptor
reject nonconforming input.
"""
from __future__ import annotations

import math
from typing import Any, Iterable

from .core import Artifact, Ledger
from .investigation import _put, _text

TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool,
         "array": list, "object": dict, "null": type(None)}


def define_schema(ledger: Ledger, name: str, *, fields: dict | None = None,
                  references: dict | None = None, display: Iterable[str] = (),
                  supersedes: str | None = None, by: str = "human") -> Artifact:
    """Create an immutable descriptor; names are descriptive, IDs select exact versions."""
    fields, references, display = fields or {}, references or {}, list(display)
    for field, rule in fields.items():
        _text(field, "field")
        if not isinstance(rule, dict) or set(rule) - {"type", "required", "description"}:
            raise ValueError(f"invalid field rule for {field}")
        if rule.get("type") not in TYPES or type(rule.get("required", False)) is not bool:
            raise ValueError(f"invalid field type/required flag for {field}")
    for role, rule in references.items():
        _text(role, "reference role")
        if not isinstance(rule, dict) or set(rule) - {"kinds", "min", "context", "description"}:
            raise ValueError(f"invalid reference rule for {role}")
        if type(rule.get("min", 0)) is not int or rule.get("min", 0) < 0:
            raise ValueError("reference min must be a nonnegative integer")
        if type(rule.get("context", False)) is not bool:
            raise ValueError("context must be boolean")
        if not isinstance(rule.get("kinds", []), list) or any(not isinstance(x, str) for x in rule.get("kinds", [])):
            raise ValueError("reference kinds must be a list of strings")
    if any(x not in fields for x in display):
        raise ValueError("display fields must be declared")
    if supersedes and ledger.get(supersedes).kind != "schema":
        raise ValueError("prior schema artifact required")
    return _put(ledger, "schema", {"name": _text(name, "name"), "format": 1,
                "fields": fields, "references": references, "display": display},
                {"previous": [supersedes] if supersedes else []}, by)


def check(ledger: Ledger, schema_id: str, data: dict, refs: dict | None = None) -> dict:
    schema = ledger.get(schema_id)
    if schema.kind != "schema" or schema.data.get("format") != 1:
        raise ValueError("supported schema descriptor required")
    errors = []
    for field, rule in schema.data["fields"].items():
        if field not in data:
            if rule.get("required"):
                errors.append(f"missing field: {field}")
            continue
        value, typename = data[field], rule["type"]
        valid = isinstance(value, TYPES[typename])
        if typename in {"number", "integer"}:
            valid = type(value) in (int, float) and type(value) is not bool
            if typename == "integer":
                valid = type(value) is int
            if type(value) is float:
                valid = valid and math.isfinite(value)
        if not valid:
            errors.append(f"{field}: expected {typename}")
    for role, rule in schema.data["references"].items():
        ids = (refs or {}).get(role, ())
        if len(set(ids)) < rule.get("min", 0):
            errors.append(f"{role}: requires at least {rule['min']} reference(s)")
        for aid in ids:
            try:
                target = ledger.get(aid)
            except KeyError:
                errors.append(f"{role}: missing artifact {aid}")
                continue
            if rule.get("kinds") and target.kind not in rule["kinds"]:
                errors.append(f"{role}: unexpected kind {target.kind}")
    return {"schema": schema.id, "conforms": not errors, "errors": errors}


def describe(ledger: Ledger, artifact: Artifact) -> list[dict]:
    """Inspection remains possible even when a schema reference cannot be resolved."""
    out = []
    for sid in artifact.refs.get("schema", ()):
        try:
            report = check(ledger, sid, artifact.data, artifact.refs)
            s = ledger.get(sid)
            report["name"] = s.data["name"]
            report["display"] = [{"field": f, "value": artifact.data.get(f)}
                                 for f in s.data.get("display", ())]
            out.append(report)
        except (KeyError, ValueError, TypeError):
            out.append({"schema": sid, "conforms": None, "errors": ["unknown/unsupported descriptor"]})
    return out


def context_dependencies(ledger: Ledger, artifact: Artifact) -> set[str]:
    """Descriptors guide selection; callers still enforce access and context bounds."""
    ids = set()
    for sid in artifact.refs.get("schema", ()):
        try:
            schema = ledger.get(sid)
        except KeyError:
            continue
        if schema.kind != "schema" or schema.data.get("format") != 1:
            continue
        ids.add(sid)
        for role, rule in schema.data.get("references", {}).items():
            if rule.get("context"):
                ids.update(artifact.refs.get(role, ()))
    return ids


def require_inputs(ledger: Ledger, tool: Artifact, values: dict) -> None:
    """Validate call values against a tool's required input descriptor."""
    for sid in tool.refs.get("input_schema", ()):
        report = check(ledger, sid, values)
        if not report["conforms"]:
            raise ValueError(f"tool input schema {sid}: {'; '.join(report['errors'])}")
