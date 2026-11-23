"""User loading and session expiry helpers."""

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
