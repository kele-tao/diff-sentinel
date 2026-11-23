"""Tests for app.core."""

from app.core import is_expired, load_user


def test_load_user_missing():
    assert load_user(999) is None


def test_is_expired_true():
    assert is_expired({"created_at": 0}, 4000) is True
