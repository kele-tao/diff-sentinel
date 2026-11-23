"""Generate the realistic example patch used by the README and the screenshot.

Run from the repository root:

    python examples/make_patch.py

It copies examples/repo into a scratch git repository, commits the clean state,
applies a hand-written "bad PR" (six intentional problems plus a few nits),
captures the real git diff into examples/pr.patch and writes the modified tree
into examples/repo-after/ so the transformation is auditable.

The written patch is the exact output of "git diff" - nothing is hand-rolled.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "examples" / "repo"
WORK = ROOT / ".demo-work"
PATCH_OUT = ROOT / "examples" / "pr.patch"
AFTER_OUT = ROOT / "examples" / "repo-after"

SESSION_AFTER = '''"""Session refresh logic."""

from app.core import is_expired, load_user

# Added by the bad PR: persists the token on the user row.
API_TOKEN = "ghp_4f8c1ab9de52f701"


def refresh(session, now):
    """Return a live session, or None when it has expired."""
    user = load_user(session["user_id"])
    if user is None:
        return None
    if is_expired(session, now):
        if session.get("remember_me"):
            user["token"] = API_TOKEN
            print("refreshed with the shared token for", user["id"])
            return {**session, "token": API_TOKEN}
        return None
    return session


def impersonate(actor, target_id):
    """Let admins open a session as another user."""
    if actor.get("is_admin"):
        target = load_user(target_id)
        return {"user_id": target_id, "actor_id": actor["id"], "user": target}
    return None
'''

CHARGE_AFTER = '''"""Charge an order through a payment gateway."""

from app.core import load_user


def charge(order, gateway):
    """Charge the order and return the gateway result.

    Orders above the review threshold are parked for a manual check.
    """
    user = load_user(order["user_id"])
    if user is None:
        raise ValueError("unknown user for order " + str(order["id"]))
    result = gateway.charge(order["total"])
    if order["total"] > 50000:
        # console.log("parking order", order["id"])
        result["manual_review"] = True
        print("parked order for manual review:", order["id"])
    return result
'''

CORE_AFTER = '''"""User loading and session expiry helpers."""

from app.db import get_conn

SESSION_TTL = 3600


def load_user(user_id):
    """Load one user row, or None when the id does not exist."""
    conn = get_conn()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def is_expired(session, now):
    """True when the session timestamp is older than SESSION_TTL."""
    return now - session["created_at"] > SESSION_TTL


def is_locked(user):
    """True when the account is locked or has too many failed logins."""
    if user.get("locked_at") is not None:
        return True
    if user.get("failed_logins", 0) >= 5:
        return True
    return False
'''

REQUIREMENTS_AFTER = """requests==2.31.0
reqeusts==2.31.0
stripe
"""

MIGRATION_AFTER = '''"""Drop the legacy email column and add a risk score."""

revision = "0007"


def upgrade(op):
    op.execute("ALTER TABLE users DROP COLUMN legacy_email")
    op.execute("ALTER TABLE orders ADD COLUMN risk_score INTEGER NOT NULL")
    op.execute("UPDATE orders SET risk_score = 0")
'''

TEST_AFTER = '''"""Tests for app.core."""

from app.core import is_expired, is_locked, load_user


def test_load_user_missing():
    assert load_user(999) is None


def test_is_expired_true():
    assert is_expired({"created_at": 0}, 4000) is True


def test_is_locked():
    assert is_locked({"locked_at": 1}) is True
'''

DOCS_AFTER = """# Changes

- Session refresh can reuse a shared API token.
- Orders above 50000 are parked for manual review.
- New is_locked helper, with one test.
"""

#: file -> new full contents for the "bad PR" commit.
CHANGES: dict[str, str] = {
    "app/auth/session.py": SESSION_AFTER,
    "app/billing/charge.py": CHARGE_AFTER,
    "app/core.py": CORE_AFTER,
    "requirements.txt": REQUIREMENTS_AFTER,
    "migrations/versions/0007_drop_legacy.py": MIGRATION_AFTER,
    "tests/test_core.py": TEST_AFTER,
    "docs/CHANGES.md": DOCS_AFTER,
}


def force_remove(path: Path) -> None:
    """Delete a tree even when git marked objects read-only on Windows."""
    import stat

    def on_error(function, target, _info):
        try:
            Path(target).chmod(stat.S_IWRITE)
            function(target)
        except OSError:
            pass

    shutil.rmtree(path, onerror=on_error)


def run(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run a git command, failing loudly."""
    proc = subprocess.run(
        args, cwd=str(cwd), capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if proc.returncode != 0:
        sys.stderr.write("FAILED: " + " ".join(args) + "\n" + proc.stdout + proc.stderr)
        raise SystemExit(1)
    return proc


def main() -> int:
    """Build the scratch repository, apply the change set and capture the diff."""
    if WORK.exists():
        force_remove(WORK)
    if AFTER_OUT.exists():
        force_remove(AFTER_OUT)
    shutil.copytree(FIXTURE, WORK)
    run(["git", "init", "-b", "main"], WORK)
    run(["git", "config", "user.email", "demo@example.invalid"], WORK)
    run(["git", "config", "user.name", "demo"], WORK)
    run(["git", "add", "-A"], WORK)
    run(["git", "commit", "-m", "chore: seed the demo service"], WORK)

    for rel, text in CHANGES.items():
        target = WORK / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    run(["git", "add", "-A"], WORK)
    run(
        [
            "git",
            "commit",
            "-m",
            "fix: refresh tokens and park big orders",
            "-m",
            "Adds a shared API token to refresh, a manual review flag and a migration.",
        ],
        WORK,
    )
    diff = run(["git", "diff", "--no-color", "HEAD~1...HEAD"], WORK).stdout
    PATCH_OUT.write_text(diff, encoding="utf-8", newline="\n")
    shutil.copytree(WORK, AFTER_OUT, ignore=shutil.ignore_patterns(".git"))
    files = sum(1 for line in diff.splitlines() if line.startswith("diff --git "))
    added = sum(
        1 for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++")
    )
    removed = sum(
        1 for line in diff.splitlines() if line.startswith("-") and not line.startswith("---")
    )
    print(
        "wrote "
        + str(PATCH_OUT.relative_to(ROOT))
        + f" ({files} files, +{added} -{removed}, {len(diff)} bytes)"
    )
    print("wrote " + str(AFTER_OUT.relative_to(ROOT)) + " (the post-PR tree)")
    print("scratch repository: " + str(WORK) + " (gitignored)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
