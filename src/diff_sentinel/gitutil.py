"""Git plumbing: turn a working tree plus a base ref into a patch and metadata.

Everything shells out to the installed git binary with explicit arguments and a
scrubbed environment, so the tool works on Windows, macOS and Linux without a
git binding dependency. Failures are translated into GitError with an actionable
message instead of leaking a CalledProcessError traceback.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .model import Commit, PullRequest

GIT_TIMEOUT = 120
MAX_COMMITS = 50


class GitError(RuntimeError):
    """Raised when git is missing, the ref is unknown, or the repository is broken."""


@dataclass(frozen=True)
class RepoInfo:
    """Facts about the repository under review."""

    root: str
    branch: str | None
    head: str | None
    base: str | None = None


def run_git(
    cwd: str | Path, args: list[str], check: bool = True
) -> subprocess.CompletedProcess[str]:
    """Run git in cwd with a scrubbed environment."""
    git = shutil.which("git")
    if git is None:
        raise GitError(
            "git executable not found on PATH; install git or pass --patch to review a "
            "patch file without a repository"
        )
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_PAGER"] = "cat"
    env["LC_ALL"] = "C"
    try:
        proc = subprocess.run(
            [git, *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=GIT_TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:
        raise GitError(
            "git " + " ".join(args) + " timed out after " + str(GIT_TIMEOUT) + "s"
        ) from exc
    except OSError as exc:
        raise GitError("could not start git: " + str(exc)) from exc
    if check and proc.returncode != 0:
        raise GitError(friendly_git_error(args, proc.stderr))
    return proc


def friendly_git_error(args: list[str], stderr: str) -> str:
    """Translate a git failure into one actionable sentence."""
    joined = " ".join(args)
    lines = stderr.strip().splitlines()
    tail = lines[-1] if lines else "no output"
    if "not a git repository" in stderr:
        return "not a git repository: run diff-sentinel from inside a repo or pass --repo DIR"
    if (
        "unknown revision" in stderr
        or "bad revision" in stderr
        or "ambiguous argument" in stderr
    ):
        return (
            "unknown git ref in 'git "
            + joined
            + "': check the base branch name (for example --base origin/main)"
        )
    if "does not have any commits" in stderr:
        return "repository has no commits yet; make a first commit before reviewing"
    return "git " + joined + " failed: " + tail


def is_repo(path: str | Path) -> bool:
    """True when path is inside a git working tree."""
    try:
        proc = run_git(path, ["rev-parse", "--is-inside-work-tree"], check=False)
    except GitError:
        return False
    return proc.returncode == 0 and proc.stdout.strip() == "true"


def repo_root(path: str | Path) -> str:
    """Absolute path of the working tree root."""
    out = run_git(path, ["rev-parse", "--show-toplevel"]).stdout.strip()
    if not out:
        raise GitError("could not determine the repository root")
    return out


def current_branch(path: str | Path) -> str | None:
    """Branch name, or None for a detached HEAD."""
    proc = run_git(path, ["rev-parse", "--abbrev-ref", "HEAD"], check=False)
    if proc.returncode != 0:
        return None
    name = proc.stdout.strip()
    return None if name in ("", "HEAD") else name


def head_sha(path: str | Path) -> str | None:
    """Current commit sha, or None when the repository has no commits."""
    proc = run_git(path, ["rev-parse", "HEAD"], check=False)
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def merge_base(repo: str | Path, base: str) -> str:
    """Commit where base and HEAD diverged."""
    proc = run_git(repo, ["merge-base", base, "HEAD"], check=False)
    if proc.returncode != 0:
        raise GitError(
            "cannot find a merge base between "
            + base
            + " and HEAD; fetch the base branch (git fetch origin "
            + base.split("/")[-1]
            + ") or review against a local branch"
        )
    return proc.stdout.strip()


def resolve_range(repo: str | Path, base: str) -> str:
    """Validate base and return the three-dot diff range against HEAD."""
    verify = run_git(repo, ["rev-parse", "--verify", "--quiet", base + "^{commit}"], check=False)
    if verify.returncode != 0:
        raise GitError(
            "unknown base ref "
            + repr(base)
            + ": it is not a commit in this repository. Try --base origin/main, "
            "--base HEAD~1, or pass --patch pr.patch"
        )
    return merge_base(repo, base) + "...HEAD"


def diff_text(repo: str | Path, rev_range: str, context: int = 3) -> str:
    """Raw unified diff for the given range, with rename detection."""
    return run_git(
        repo,
        ["diff", "--no-color", "--no-ext-diff", "--find-renames", "-U" + str(context), rev_range],
    ).stdout


def staged_diff(repo: str | Path, context: int = 3) -> str:
    """Raw unified diff of the index against HEAD."""
    return run_git(
        repo,
        ["diff", "--cached", "--no-color", "--no-ext-diff", "--find-renames", "-U" + str(context)],
    ).stdout


def diff_files(repo: str | Path, rev_range: str) -> list[str]:
    """Paths touched by the range, respecting rename detection."""
    proc = run_git(repo, ["diff", "--name-only", "--find-renames", "--no-color", rev_range])
    return [line for line in proc.stdout.splitlines() if line.strip()]


def read_commits(repo: str | Path, rev_range: str, limit: int = MAX_COMMITS) -> tuple[Commit, ...]:
    """Commit subjects and bodies for the range, newest first."""
    base = rev_range.split("...")[0]
    proc = run_git(
        repo,
        ["log", "--no-merges", "--format=%h%x1f%s%x1f%b%x1e", base + "..HEAD", "-n", str(limit)],
        check=False,
    )
    if proc.returncode != 0:
        return ()
    commits: list[Commit] = []
    for chunk in proc.stdout.split("\x1e"):
        chunk = chunk.strip("\n")
        if not chunk:
            continue
        parts = chunk.split("\x1f")
        if len(parts) >= 2:
            body = parts[2].strip() if len(parts) > 2 else ""
            commits.append(Commit(sha=parts[0].strip(), subject=parts[1].strip(), body=body))
    return tuple(commits)


def pr_metadata(repo: str | Path, base: str, head_branch: str | None) -> PullRequest:
    """Best-effort PR metadata: branch names, author and commits."""
    proc = run_git(repo, ["log", "-1", "--format=%an", "HEAD"], check=False)
    author = proc.stdout.strip() if proc.returncode == 0 else None
    return PullRequest(
        title=None,
        body=None,
        commits=read_commits(repo, base + "...HEAD"),
        branch=head_branch,
        base=base,
        head="HEAD",
        author=author,
    )
