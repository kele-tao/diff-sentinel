"""diff-sentinel: a static pull-request reviewer.

Public API:
    review_patch(text, ...) -> Review
    review_diff(diff, ...)  -> Review
    parse_patch(text)       -> Diff
    Review, Finding, Severity, PullRequest
"""

from __future__ import annotations

from .catalog import RULE_CATALOG
from .diffparse import Diff, FileDiff, Hunk, parse_patch
from .model import Finding, ImpactEntry, PullRequest, Review, Severity
from .review import compute_impact, review_diff, review_patch
from .rules import ReviewOptions, run_rules
from .scoring import compute_score, verdict_for

__version__ = "1.0.0"
__all__ = [
    "Diff",
    "FileDiff",
    "Finding",
    "Hunk",
    "ImpactEntry",
    "PullRequest",
    "RULE_CATALOG",
    "Review",
    "ReviewOptions",
    "Severity",
    "__version__",
    "compute_impact",
    "compute_score",
    "parse_patch",
    "review_diff",
    "review_patch",
    "run_rules",
    "verdict_for",
]
