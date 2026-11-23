"""The public rule catalogue.

Kept as data so "diff-sentinel rules" never drifts from the docs: the CLI reads
this table, and tests assert that every finding rule id in the code appears here.
"""

from __future__ import annotations

RULE_CATALOG: tuple[dict[str, str], ...] = (
    {
        "id": "risk-area",
        "severity": "critical/high/medium",
        "summary": "marks changes in payments, auth, migrations, SQL, infra and config",
    },
    {
        "id": "missing-tests",
        "severity": "high",
        "summary": "new branches in source files while no test file changed",
    },
    {
        "id": "missing-tests-untargeted",
        "severity": "medium",
        "summary": "tests changed but none reference the changed module or function",
    },
    {
        "id": "deleted-assertions",
        "severity": "critical/high",
        "summary": "assertions removed from a test file, or a whole test file deleted",
    },
    {
        "id": "migration-hazard",
        "severity": "critical/high/medium/low",
        "summary": "DROP COLUMN, DROP TABLE, TRUNCATE, NOT NULL without default, rename, lock",
    },
    {
        "id": "migration-no-transaction",
        "severity": "medium",
        "summary": "DDL added to a migration without BEGIN/COMMIT",
    },
    {
        "id": "new-dependency",
        "severity": "low/medium/high",
        "summary": "a dependency was added; reports the licence guess and whether it is known",
    },
    {
        "id": "unpinned-dependency",
        "severity": "medium",
        "summary": "a dependency was added without a version bound",
    },
    {
        "id": "typosquat",
        "severity": "high",
        "summary": "a new dependency name is one edit away from a popular package",
    },
    {
        "id": "secret",
        "severity": "critical",
        "summary": "AWS keys, private keys, provider tokens, credential URLs, hardcoded passwords",
    },
    {
        "id": "debug-leftover",
        "severity": "low/medium",
        "summary": "print, console.log, breakpoint, pdb or debugger left in the diff",
    },
    {
        "id": "commented-code",
        "severity": "low",
        "summary": "commented-out code added instead of deleted",
    },
    {
        "id": "large-addition",
        "severity": "medium",
        "summary": "a single file gains 80 or more contiguous lines",
    },
    {
        "id": "wide-diff",
        "severity": "medium",
        "summary": "the diff touches more than 25 files",
    },
    {
        "id": "large-diff",
        "severity": "low/high",
        "summary": "the diff changes more than 900 lines (2000 is the hard limit)",
    },
    {
        "id": "unrelated-change",
        "severity": "low/medium",
        "summary": "changes spread over four or more unrelated modules",
    },
    {
        "id": "weak-pr-title",
        "severity": "low",
        "summary": "PR title shorter than 15 characters, or a generic word like fix/update",
    },
    {
        "id": "empty-pr-body",
        "severity": "low",
        "summary": "PR body shorter than 40 characters",
    },
    {
        "id": "weak-commit-messages",
        "severity": "low",
        "summary": "most commits in the range have vague or very short subjects",
    },
)
