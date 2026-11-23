"""Scoring, rendering and JSON determinism tests."""

from __future__ import annotations

import json

from conftest import build_patch

from diff_sentinel.model import PENALTIES, PullRequest, Severity
from diff_sentinel.render import (
    render_ci_snippet,
    render_json,
    render_markdown,
    render_score_line,
    render_terminal,
)
from diff_sentinel.review import review_patch
from diff_sentinel.scoring import compute_score, gate_message, verdict_for


def clean_review():
    return review_patch(build_patch({"docs/notes.md": "Just prose, no code at all.\n"}))


def test_score_is_capped_at_zero():
    review = review_patch(
        build_patch(
            {
                "src/app/auth/x.py": "TOKEN = 'ghp_0123456789abcdef'\nprint('x')\n",
                "migrations/0009_x.py": "op.execute('ALTER TABLE t DROP COLUMN a')\n",
            }
        )
    )
    assert review.score < 10
    assert review.verdict.startswith("Do not merge")


def test_clean_diff_scores_high():
    review = review_patch(
        build_patch({"docs/guide.md": "A page of documentation with no code in it.\n"})
    )
    assert review.score >= 90


def test_penalty_table_is_linear():
    review = review_patch(build_patch({"src/tool.py": "def f():\n    print('x')\n"}))
    expected = 100
    for finding in review.findings:
        expected -= PENALTIES[finding.severity]
    expected += 0
    assert 0 <= review.score <= 100
    assert review.score == max(0, min(100, expected + review.stats["test_files"] * 4))


def test_verdict_thresholds():
    assert verdict_for(100).startswith("Ready to merge")
    assert verdict_for(80).startswith("Ready with nits")
    assert verdict_for(60).startswith("Needs changes")
    assert verdict_for(40).startswith("Risky")
    assert verdict_for(0).startswith("Do not merge")


def test_blocking_filters_by_severity():
    review = review_patch(build_patch({"src/tool.py": "def f():\n    print('x')\n"}))
    assert review.blocking(Severity.MEDIUM) == []
    assert review.blocking(Severity.LOW)


def test_gate_message_mentions_the_worst_finding():
    patch = build_patch({"src/app/auth/x.py": "TOKEN = 'ghp_0123456789abcdef'\n"})
    review = review_patch(patch)
    message = gate_message(review.blocking(Severity.HIGH), Severity.HIGH)
    assert "worst:" in message
    assert "at " in message


def test_terminal_render_contains_score_and_verdict():
    review = review_patch(build_patch({"src/tool.py": "def f():\n    print('x')\n"}))
    text = render_terminal(review, color=False, width=90)
    assert "diff-sentinel" in text
    assert "/100" in text
    assert "VERDICT" in text
    assert "fix:" in text


def test_terminal_colour_adds_ansi_only_when_enabled():
    review = review_patch(build_patch({"src/tool.py": "print('x')\n"}))
    plain = render_terminal(review, color=False)
    coloured = render_terminal(review, color=True)
    assert "\x1b[" not in plain
    assert "\x1b[" in coloured


def test_markdown_render_has_tables_and_impact_section():
    review = review_patch(build_patch({"src/tool.py": "print('x')\n"}))
    text = render_markdown(review)
    assert text.startswith("## diff-sentinel review")
    assert "| # | severity |" in text
    assert "no LLM" in text


def test_markdown_escapes_pipes_in_evidence():
    review = review_patch(build_patch({"src/tool.py": "print('a|b')\n"}))
    text = render_markdown(review)
    assert "a\\|b" in text


def test_json_is_deterministic_and_complete():
    review = review_patch(build_patch({"src/tool.py": "print('x')\n"}))
    first = render_json(review)
    second = render_json(review)
    assert first == second
    payload = json.loads(first)
    assert set(payload) == {
        "target",
        "mode",
        "files",
        "added",
        "removed",
        "score",
        "verdict",
        "severity_counts",
        "findings",
        "impact",
        "stats",
        "notes",
        "pr",
    }
    assert payload["findings"][0]["location"].startswith("src/tool.py:")


def test_json_round_trips_unicode():
    review = review_patch(build_patch({"docs/x.md": "héllo 世界 🚀\n"}))
    payload = json.loads(render_json(review))
    assert payload["target"] == "patch"


def test_score_line_is_one_line():
    review = review_patch(build_patch({"src/tool.py": "print('x')\n"}))
    line = render_score_line(review, Severity.HIGH)
    assert "\n" not in line
    assert line.startswith("PASS") or line.startswith("FAIL")


def test_ci_snippet_is_a_workflow():
    snippet = render_ci_snippet()
    assert snippet.startswith("name: diff-sentinel")
    assert "pull_request" in snippet
    assert "diff-sentinel score" in snippet


def test_impact_map_is_empty_for_a_patch_review():
    review = review_patch(build_patch({"src/tool.py": "print('x')\n"}))
    assert review.impact == []


def test_pr_metadata_appears_in_json():
    pr = PullRequest(
        title="Reject expired sessions on refresh",
        body="Stops refresh from returning a stale session, adds coverage for the new branch.",
        commits=(),
        branch="feature/refresh",
        base="main",
        head="HEAD",
        author="dev",
    )
    review = review_patch(build_patch({"src/x.py": "x = 1\n"}), pr=pr)
    payload = json.loads(render_json(review))
    assert payload["pr"]["branch"] == "feature/refresh"


def test_score_function_is_pure():
    review = clean_review()
    before = review.score
    score, verdict = compute_score(review)
    assert score == before
    assert verdict == review.verdict
