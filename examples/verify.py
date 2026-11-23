"""Smoke test the shipped example patch the way CI does.

    python examples/verify.py

Runs the CLI the same way the GitHub workflow does, asserts the numbers the README
quotes, and prints a one-line summary. Exit code 0 means the published example is
still consistent with the published claims.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PATCH = ROOT / "examples" / "pr.patch"


def run(args: list[str]) -> subprocess.CompletedProcess[str]:
    """Run the CLI as a module, exactly as CI does."""
    return subprocess.run(
        [sys.executable, "-m", "diff_sentinel", *args],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def main() -> int:
    """Check the example patch and the README claims."""
    payload = json.loads(run(["review", "--patch", str(PATCH), "--format", "json"]).stdout)
    failures: list[str] = []

    def check(condition: bool, message: str) -> None:
        print(("ok   " if condition else "FAIL ") + message)
        if not condition:
            failures.append(message)

    check(payload["files"] == 7, "example patch still touches 7 files")
    check(payload["findings"], "example patch still produces findings")
    check(payload["score"] == 0, "example patch still scores 0/100")
    check(
        payload["severity_counts"]["critical"] == 4,
        "example patch still has 4 critical findings",
    )
    rules = {finding["rule"] for finding in payload["findings"]}
    for expected in ("secret", "migration-hazard", "typosquat", "debug-leftover"):
        check(expected in rules, "rule " + expected + " still fires")

    gate = run(["score", "--patch", str(PATCH), "--fail-on", "high"])
    check(gate.returncode == 1, "score --fail-on high still exits 1")
    check(gate.stdout.startswith("FAIL"), "score still prints a FAIL line")

    review = run(["review", "--patch", str(PATCH), "--format", "md"])
    check(review.stdout.startswith("## diff-sentinel review"), "markdown renderer still works")

    print()
    print("verified " + str(len(payload["findings"])) + " findings from " + PATCH.name)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
