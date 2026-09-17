"""Verify a sealed-evaluator attestation against its service HMAC key."""
from __future__ import annotations
import hashlib, hmac, json, sys
from pathlib import Path


def canonical(x):
    return json.dumps(x, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def main():
    doc = json.loads(Path(sys.argv[1]).read_text())
    key = Path(sys.argv[2]).read_bytes()
    sig = doc.pop("sig", "")
    expected = hmac.new(key, canonical(doc), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        raise SystemExit("invalid sealed evaluator attestation")
    if not doc.get("retired"):
        raise SystemExit("test was not retired")
    print(json.dumps(doc, sort_keys=True))


if __name__ == "__main__":
    main()
