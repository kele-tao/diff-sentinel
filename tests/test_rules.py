"""Rule tests: one behaviour per documented rule."""

from __future__ import annotations

from conftest import build_patch

from diff_sentinel.model import PullRequest, Severity
from diff_sentinel.review import review_patch
from diff_sentinel.rules import ReviewOptions, edit_distance, typosquat_candidates


def rules(review):
    return {finding.rule for finding in review.findings}


def test_risk_area_flags_auth_and_payments():
    patch = build_patch({"src/app/auth/session.py": "x = 1\n"})
    review = review_patch(patch)
    risk = [f for f in review.findings if f.rule == "risk-area"]
    assert risk
    assert risk[0].severity is Severity.CRITICAL
    assert "authentication" in risk[0].title


def test_risk_area_flags_migrations_as_high():
    patch = build_patch({"migrations/0002_add.py": "op.execute('ALTER TABLE t ADD c INT')\n"})
    review = review_patch(patch)
    risk = [f for f in review.findings if f.rule == "risk-area"]
    assert risk[0].severity is Severity.HIGH


def test_risk_area_is_reported_once_per_area():
    patch = build_patch(
        {
            "src/auth/a.py": "x = 1\n",
            "src/auth/b.py": "y = 2\n",
        }
    )
    review = review_patch(patch)
    risk = [f for f in review.findings if f.rule == "risk-area"]
    assert len(risk) == 1
    assert "2 files" in risk[0].title


def test_missing_tests_when_no_test_changed():
    body = "def run(x):\n    if x:\n        return 1\n    return 0\n"
    patch = build_patch({"src/service.py": body})
    review = review_patch(patch)
    assert "missing-tests" in rules(review)
    finding = next(f for f in review.findings if f.rule == "missing-tests")
    assert finding.severity is Severity.HIGH


def test_missing_tests_is_satisfied_by_a_matching_test():
    patch = build_patch(
        {
            "src/service.py": "def run(x):\n    if x:\n        return 1\n    return 0\n",
            "tests/test_service.py": "from src.service import run\n\n\ndef test_run():\n"
            "    assert run(True) == 1\n",
        }
    )
    review = review_patch(patch)
    assert "missing-tests" not in rules(review)


def test_untargeted_test_change_is_medium():
    patch = build_patch(
        {
            "src/payments/charge.py": "def charge(o):\n    if o:\n        return 1\n"
            "    return 0\n",
            "tests/test_other.py": "def test_something_else():\n    assert 1 == 1\n",
        }
    )
    review = review_patch(patch)
    assert "missing-tests-untargeted" in rules(review)


def test_deleted_assertions_are_high():
    patch = (
        "diff --git a/tests/test_x.py b/tests/test_x.py\n"
        "--- a/tests/test_x.py\n"
        "+++ b/tests/test_x.py\n"
        "@@ -1,4 +1,4 @@\n"
        " def test_x():\n"
        "-    assert add(1, 2) == 3\n"
        "-    assert add(0, 0) == 0\n"
        "+    assert add(1, 2) is not None\n"
    )
    review = review_patch(patch)
    assert "deleted-assertions" in rules(review)


def test_deleted_test_file_is_critical():
    patch = (
        "diff --git a/tests/test_gone.py b/tests/test_gone.py\n"
        "deleted file mode 100644\n"
        "--- a/tests/test_gone.py\n"
        "+++ /dev/null\n"
        "@@ -1,2 +0,0 @@\n"
        "-def test_a():\n"
        "-    assert True\n"
    )
    review = review_patch(patch)
    finding = next(f for f in review.findings if f.rule == "deleted-assertions")
    assert finding.severity is Severity.CRITICAL


def test_migration_hazards_are_detected():
    patch = build_patch(
        {
            "migrations/0003_drop.py": (
                "op.execute('ALTER TABLE users DROP COLUMN email')\n"
                "op.execute('ALTER TABLE orders ADD COLUMN x INT NOT NULL')\n"
            )
        }
    )
    review = review_patch(patch)
    titles = [f.title for f in review.findings if f.rule == "migration-hazard"]
    assert any("DROP COLUMN" in title for title in titles)
    assert any("NOT NULL" in title for title in titles)


def test_migration_without_transaction_is_flagged():
    patch = build_patch({"migrations/0004_t.py": "op.execute('CREATE TABLE t (id INT)')\n"})
    review = review_patch(patch)
    assert "migration-no-transaction" in rules(review)


def test_migration_with_transaction_is_not_flagged():
    patch = build_patch(
        {
            "migrations/0005_t.py": (
                "op.execute('BEGIN;')\n"
                "op.execute('CREATE TABLE t (id INT)')\n"
                "op.execute('COMMIT;')\n"
            )
        }
    )
    review = review_patch(patch)
    assert "migration-no-transaction" not in rules(review)


def test_update_without_where_is_high():
    patch = build_patch({"db/backfill.sql": "UPDATE orders SET risk = 0;\n"})
    review = review_patch(patch)
    hazards = [f for f in review.findings if f.rule == "migration-hazard"]
    assert any(f.severity is Severity.HIGH and "WHERE" in f.title for f in hazards)


def test_secret_in_diff_is_critical_and_masked():
    token = "ghp_0123456789abcdef"
    patch = build_patch({"src/config.py": "TOKEN = '" + token + "'\n"})
    review = review_patch(patch)
    finding = next(f for f in review.findings if f.rule == "secret")
    assert finding.severity is Severity.CRITICAL
    assert finding.evidence.endswith("ef")
    assert token not in finding.evidence


def test_secret_placeholder_is_ignored():
    patch = build_patch({"src/config.py": "password = 'changeme-placeholder'\n"})
    review = review_patch(patch)
    assert "secret" not in rules(review)


def test_database_url_with_credentials_is_flagged():
    patch = build_patch({"settings.py": "DB = 'postgres://admin:hunter2@db:5432/app'\n"})
    review = review_patch(patch)
    assert any(f.rule == "secret" and "database" in f.title for f in review.findings)


def test_debug_print_is_low():
    patch = build_patch({"src/tool.py": "def f():\n    print('debug')\n"})
    review = review_patch(patch)
    finding = next(f for f in review.findings if f.rule == "debug-leftover")
    assert finding.severity is Severity.LOW


def test_breakpoint_is_medium():
    patch = build_patch({"src/tool.py": "def f():\n    breakpoint()\n    return 1\n"})
    review = review_patch(patch)
    finding = next(f for f in review.findings if f.rule == "debug-leftover")
    assert finding.severity is Severity.MEDIUM


def test_commented_out_code_is_low():
    patch = build_patch({"src/tool.py": "# if old_flag:\n#     return 1\nx = 2\n"})
    review = review_patch(patch)
    assert "commented-code" in rules(review)


def test_large_addition_is_flagged():
    body = "def big():\n" + "".join("    x" + str(i) + " = " + str(i) + "\n" for i in range(100))
    review = review_patch(build_patch({"src/big.py": body}))
    finding = next(f for f in review.findings if f.rule == "large-addition")
    assert finding.severity is Severity.MEDIUM
    assert "101 contiguous added lines" in finding.evidence


def test_unrelated_change_needs_four_modules():
    three = build_patch(
        {"a/x.py": "x = 1\n", "b/y.py": "y = 1\n", "c/z.py": "z = 1\n"}
    )
    assert "unrelated-change" not in rules(review_patch(three))
    four = build_patch(
        {"a/x.py": "x = 1\n", "b/y.py": "y = 1\n", "c/z.py": "z = 1\n", "d/w.py": "w = 1\n"}
    )
    assert "unrelated-change" in rules(review_patch(four))


def test_weak_pr_title_is_flagged():
    options = ReviewOptions(pr_title="fix")
    review = review_patch(build_patch({"src/x.py": "x = 1\n"}), options=options)
    assert "weak-pr-title" in rules(review)


def test_good_pr_title_is_not_flagged():
    review = review_patch(
        build_patch({"src/x.py": "x = 1\n"}),
        options=ReviewOptions(
            pr_title="Reject expired sessions on refresh",
            pr_body="Stops refresh from returning a stale session and adds a test for the new "
            "branch, so expired users are logged out.",
        ),
    )
    assert "weak-pr-title" not in rules(review)
    assert "empty-pr-body" not in rules(review)


def test_weak_commit_messages_are_flagged():
    pr = PullRequest(
        title="Reject expired sessions on refresh",
        body="Stops refresh from returning a stale session and adds coverage for the branch.",
        commits=(),
    )
    review = review_patch(build_patch({"src/x.py": "x = 1\n"}), pr=pr)
    assert "weak-commit-messages" not in rules(review)


def test_dependencies_report_licence_and_pin():
    patch = build_patch({"requirements.txt": "requests==2.31.0\nflask\n"})
    review = review_patch(patch)
    deps = [f for f in review.findings if f.rule == "new-dependency"]
    assert {dep.title.split(": ")[1] for dep in deps} == {"requests", "flask"}
    assert any("Licence guess: Apache-2.0" in dep.detail for dep in deps)
    assert "unpinned-dependency" in rules(review)


def test_typosquat_detects_one_edit_typo():
    assert typosquat_candidates("reqeusts") == ["requests"]
    assert typosquat_candidates("requests") == []
    assert edit_distance("reqeusts", "requests") == 1
    assert edit_distance("numpy", "numpy") == 0
    assert edit_distance("psycopg2", "psycopg2-binary") > 1


def test_typosquat_finding_suppresses_new_dependency_duplicate():
    patch = build_patch({"requirements.txt": "reqeusts==2.31.0\n"})
    review = review_patch(patch)
    assert "typosquat" in rules(review)
    assert "new-dependency" not in rules(review)


def test_can_disable_test_rules():
    patch = build_patch({"src/service.py": "def run(x):\n    if x:\n        return 1\n"})
    review = review_patch(patch, options=ReviewOptions(check_tests=False))
    assert "missing-tests" not in rules(review)


def test_every_finding_carries_a_fix():
    patch = build_patch({"src/app/auth/x.py": "TOKEN = 'ghp_0123456789abcdef'\n"})
    review = review_patch(patch)
    assert review.findings
    assert all(finding.fix for finding in review.findings)


def test_findings_are_sorted_by_severity():
    body = "print('a')\nTOKEN = 'ghp_0123456789abcdef'\n"
    review = review_patch(build_patch({"src/x.py": body}))
    ranks = [finding.severity.rank for finding in review.findings]
    assert ranks == sorted(ranks, reverse=True)
