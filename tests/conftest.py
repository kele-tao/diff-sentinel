"""Shared pytest fixtures and patch builders."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

EXAMPLE_PATCH = ROOT / "examples" / "pr.patch"
EXAMPLE_REPO = ROOT / "examples" / "repo"
EXAMPLE_AFTER = ROOT / "examples" / "repo-after"


def build_patch(files: dict[str, str]) -> str:
    """Build a minimal unified diff that only adds whole new files."""
    chunks: list[str] = []
    for path, body in files.items():
        body = body if body.endswith("\n") else body + "\n"
        lines = body.split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        chunks.append("diff --git a/" + path + " b/" + path)
        chunks.append("new file mode 100644")
        chunks.append("--- /dev/null")
        chunks.append("+++ b/" + path)
        chunks.append("@@ -0,0 +1," + str(len(lines)) + " @@")
        chunks.extend("+" + line for line in lines)
    return "\n".join(chunks) + "\n"


def git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run git in repo and assert success."""
    proc = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc


@pytest.fixture()
def example_patch() -> str:
    """The committed example patch text."""
    return EXAMPLE_PATCH.read_text(encoding="utf-8")


@pytest.fixture()
def demo_repo(tmp_path: Path) -> Path:
    """A committed copy of examples/repo with the bad second commit on top."""
    work = tmp_path / "demo"
    shutil.copytree(EXAMPLE_REPO, work)
    git(work, "init", "-b", "main")
    git(work, "config", "user.email", "test@example.invalid")
    git(work, "config", "user.name", "test")
    git(work, "add", "-A")
    git(work, "commit", "-m", "chore: seed the demo service")
    git(work, "checkout", "-b", "feature/refresh")
    for source in sorted(EXAMPLE_AFTER.rglob("*")):
        if source.is_dir():
            continue
        target = work / source.relative_to(EXAMPLE_AFTER)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    git(work, "add", "-A")
    git(work, "commit", "-m", "fix: refresh tokens and park big orders")
    return work
