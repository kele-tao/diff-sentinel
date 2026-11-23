"""Review-readiness scoring.

The score answers one question: can a reviewer say yes to this diff right now?
It starts at 100, subtracts a fixed penalty per finding by severity, adds a small
bonus when the diff arrives with tests, and clamps to 0..100. The mapping is
published in the README so nobody is surprised by the verdict.
"""

from __future__ import annotations

from .model import PENALTIES, Finding, Review, Severity

VERDICTS: tuple[tuple[int, str], ...] = (
    (90, "Ready to merge"),
    (75, "Ready with nits"),
    (55, "Needs changes"),
    (30, "Risky"),
    (0, "Do not merge"),
)

VERDICT_DETAIL: dict[str, str] = {
    "Ready to merge": "nothing that should block a reviewer.",
    "Ready with nits": "worth a skim, no blockers.",
    "Needs changes": "the reviewer should ask for at least one fix.",
    "Risky": "several blockers; expect a long review thread.",
    "Do not merge": "critical problems present.",
}

TEST_BONUS = 4
MAX_BONUS = 8


def verdict_for(score: int) -> str:
    """Map a 0..100 score to a one-line verdict."""
    for floor, text in VERDICTS:
        if score >= floor:
            return text + " - " + VERDICT_DETAIL[text]
    return "Do not merge - " + VERDICT_DETAIL["Do not merge"]


def compute_score(review: Review) -> tuple[int, str]:
    """Return (score, verdict) for the review's current findings."""
    raw = 100
    for finding in review.findings:
        raw -= PENALTIES[finding.severity]
    bonus = min(MAX_BONUS, TEST_BONUS * int(review.stats.get("test_files", 0)))
    if review.findings:
        raw += bonus
    score = max(0, min(100, raw))
    return score, verdict_for(score)


def gate_message(findings: list[Finding], threshold: Severity) -> str:
    """One-line explanation for a failed --fail-on gate."""
    if not findings:
        return "no findings at or above " + threshold.value
    first = findings[0]
    return (
        str(len(findings))
        + " finding(s) at or above "
        + threshold.value
        + "; worst: "
        + first.rule
        + " ("
        + first.severity.value
        + ") at "
        + first.location
    )
