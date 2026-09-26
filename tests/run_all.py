"""Run every BestNAI test suite and report a single summary.

Usage::

    python tests/run_all.py
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

#: (label, relative script path, extra env)
SUITES: tuple[tuple[str, str], ...] = (
    ("YesNAI contract alignment", "tests/smoke_yesnai_contract.py"),
    ("gateway HTTP surfaces", "tests/smoke_bestnai.py"),
    ("key pool", "tests/smoke_pool.py"),
    ("SDK end-to-end", "tests/e2e_sdk_gateway.py"),
)


def main() -> int:
    results: list[tuple[str, bool, float]] = []

    for label, script in SUITES:
        print(f"\n{'=' * 72}\n{label}  ({script})\n{'=' * 72}", flush=True)
        started = time.time()
        completed = subprocess.run(
            [sys.executable, str(REPO_ROOT / script)],
            cwd=str(REPO_ROOT),
            env={
                **__import__("os").environ,
                "PYTHONPATH": str(REPO_ROOT),
                "PYTHONIOENCODING": "utf-8",
            },
        )
        results.append((label, completed.returncode == 0, time.time() - started))

    print(f"\n{'=' * 72}\nSUMMARY\n{'=' * 72}")
    for label, ok, elapsed in results:
        print(f"{'PASS' if ok else 'FAIL'}  {label:<26} {elapsed:6.1f}s")

    failed = [label for label, ok, _ in results if not ok]
    if failed:
        print(f"\n{len(failed)} suite(s) failed: " + ", ".join(failed))
        return 1
    print("\nall suites passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
