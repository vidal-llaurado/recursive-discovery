"""Tiny consequence adapter for SMT solvers whose exit code alone is not semantic."""
from __future__ import annotations

import shutil
import subprocess
import sys


def main() -> None:
    if len(sys.argv) != 4:
        raise SystemExit("usage: python -m recursive_discovery.solver <z3|cvc5> <expected> <file>")

    name, expected, path = sys.argv[1:]
    exe = shutil.which(name)
    if not exe:
        raise SystemExit(f"{name} not installed")

    argv = [exe, "-smt2", path] if name == "z3" else [exe, "--lang", "smt2", path]
    p = subprocess.run(argv, capture_output=True, text=True, check=False)
    sys.stdout.write(p.stdout)
    sys.stderr.write(p.stderr)

    tokens = [x.strip() for x in p.stdout.splitlines() if x.strip()]
    observed = next((x for x in reversed(tokens) if x in {"sat", "unsat", "unknown"}), None)
    raise SystemExit(0 if p.returncode == 0 and observed == expected else 1)


if __name__ == "__main__":
    main()
