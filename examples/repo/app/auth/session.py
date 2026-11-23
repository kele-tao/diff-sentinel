"""Session refresh logic."""

from app.core import is_expired, load_user


def refresh(session, now):
    """Return a live session, or None when it has expired."""
    user = load_user(session["user_id"])
    if user is None:
        return None
    if is_expired(session, now):
        return None
    return session
