"""Connection helper shared by every module."""

import sqlite3

_CONN = None


def get_conn():
    """Return a process-wide sqlite connection."""
    global _CONN
    if _CONN is None:
        _CONN = sqlite3.connect(":memory:")
    return _CONN
