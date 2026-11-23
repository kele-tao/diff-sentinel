"""Session refresh logic."""

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
