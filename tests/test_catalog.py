"""Catalogue consistency: the CLI list and the code must never drift."""

from __future__ import annotations

import re
from pathlib import Path

from diff_sentinel.catalog import RULE_CATALOG

SOURCE = Path(__file__).resolve().parent.parent / "src" / "diff_sentinel"
RULE_RE = re.compile(r'rule="([a-z-]+)"')


def rule_ids_in_code() -> set[str]:
    """Every rule id a Finding is actually constructed with."""
    ids: set[str] = set()
    for path in SOURCE.glob("*.py"):
        ids.update(RULE_RE.findall(path.read_text(encoding="utf-8")))
    return ids


def test_every_code_rule_is_documented():
    documented = {entry["id"] for entry in RULE_CATALOG}
    missing = rule_ids_in_code() - documented
    assert not missing, "undocumented rules: " + ", ".join(sorted(missing))


def test_every_documented_rule_is_used():
    documented = {entry["id"] for entry in RULE_CATALOG}
    unused = documented - rule_ids_in_code()
    assert not unused, "catalogue entries with no implementation: " + ", ".join(sorted(unused))


def test_rule_ids_are_unique():
    ids = [entry["id"] for entry in RULE_CATALOG]
    assert len(ids) == len(set(ids))


def test_catalogue_is_non_trivial():
    assert len(RULE_CATALOG) == 19


def test_every_entry_has_severity_and_summary():
    for entry in RULE_CATALOG:
        assert entry["severity"], entry["id"]
        assert len(entry["summary"]) > 20, entry["id"]
