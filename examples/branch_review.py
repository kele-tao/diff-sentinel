"""Run a real branch review against the throwaway demo repository.

Usage (from the repository root):

    python examples/branch_review.py

It builds (or reuses) .demo-work: a copy of examples/repo with a first commit and
the "bad PR" from examples/repo-after as a second commit, then reviews the second
commit against the first, so the impact map has real imports to walk. This is the
exact command behind docs/impact-map.png.

Pass --plain to drop the ANSI colour (useful when piping to a file).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from diff_sentinel.cli import main  # noqa: E402

DEMO = ROOT / ".demo-work"
FIXTURE = ROOT / "examples" / "repo"
AFTER = ROOT / "examples" / "repo-after"
TITLE = "Refresh tokens and park big orders"


def git(*args: str) -> subprocess.CompletedProcess[str]:
    """Run git inside the demo repository, failing loudly."""
    proc = subprocess.run(
        ["git", *args],
        cwd=str(DEMO),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        sys.stderr.write("git " + " ".join(args) + " failed:\n" + proc.stdout + proc.stderr)
        raise SystemExit(1)
    return proc


def build_demo() -> None:
    """Create the two-commit demo repository when it is missing."""
    if (DEMO / ".git").exists():
        return
    if DEMO.exists():
        shutil.rmtree(DEMO)
    shutil.copytree(FIXTURE, DEMO)
    git("init", "-b", "main")
    git("config", "user.email", "demo@example.invalid")
    git("config", "user.name", "demo")
    git("add", "-A")
    git("commit", "-m", "chore: seed the demo service")
    git("checkout", "-b", "feature/refresh")
    for source in sorted(AFTER.rglob("*")):
        if source.is_dir():
            continue
        target = DEMO / source.relative_to(AFTER)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    git("add", "-A")
    git("commit", "-m", "fix: refresh tokens and park big orders")


def main_cli(argv: list[str]) -> int:
    """Review the demo branch with the real CLI."""
    build_demo()
    color = "never" if "--plain" in argv else "always"
    return main(
        [
            "review",
            "--repo",
            str(DEMO),
            "--base",
            "main",
            "--title",
            TITLE,
            "--color",
            color,
            "--limit",
            "3",
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main_cli(sys.argv[1:]))
