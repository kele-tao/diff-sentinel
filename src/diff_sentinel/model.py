"""Data model shared by every diff-sentinel component.

A finding is one concrete problem the reviewer wants a human to look at.
Findings carry the rule that produced them, a severity, a location rendered as
path:line, the evidence copied out of the diff, and a suggested fix. Nothing in
this module performs analysis; it only defines the shapes every other module
shares.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Iterable


class Severity(enum.Enum):
    """Ordered severity levels used for scoring and gating."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"

    @property
    def rank(self) -> int:
        """Higher rank means more severe (CRITICAL is 4, INFO is 0)."""
        return _RANK[self]

    @property
    def label(self) -> str:
        """Upper-case label used by the terminal renderer."""
        return self.value.upper()

    @classmethod
    def parse(cls, value: str) -> "Severity":
        """Parse a user supplied severity name, raising a friendly error."""
        try:
            return cls(str(value).strip().lower())
        except (TypeError, ValueError):
            allowed = ", ".join(item.value for item in cls)
            raise ValueError(
                "unknown severity " + repr(value) + "; choose one of: " + allowed
            ) from None


_RANK: dict[Severity, int] = {
    Severity.CRITICAL: 4,
    Severity.HIGH: 3,
    Severity.MEDIUM: 2,
    Severity.LOW: 1,
    Severity.INFO: 0,
}

#: Points removed from the 100 point review score per finding.
PENALTIES: dict[Severity, int] = {
    Severity.CRITICAL: 26,
    Severity.HIGH: 11,
    Severity.MEDIUM: 5,
    Severity.LOW: 2,
    Severity.INFO: 0,
}


@dataclass(frozen=True)
class Finding:
    """One problem found in a diff."""

    rule: str
    severity: Severity
    title: str
    fix: str
    path: str | None = None
    line: int | None = None
    evidence: str = ""
    detail: str = ""

    @property
    def location(self) -> str:
        """Human readable location, path:line when both are known."""
        if self.path is None:
            return "(diff)"
        if self.line is None:
            return self.path
        return self.path + ":" + str(self.line)

    def sort_key(self) -> tuple[int, str, str, int]:
        """Stable ordering: severity first, then rule, path and line."""
        return (-self.severity.rank, self.rule, self.path or "", self.line or 0)

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready representation."""
        return {
            "rule": self.rule,
            "severity": self.severity.value,
            "title": self.title,
            "path": self.path,
            "line": self.line,
            "location": self.location,
            "evidence": self.evidence,
            "fix": self.fix,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class Commit:
    """A commit message pulled from git log."""

    sha: str
    subject: str
    body: str = ""


@dataclass(frozen=True)
class PullRequest:
    """Metadata about the change under review."""

    title: str | None = None
    body: str | None = None
    commits: tuple[Commit, ...] = ()
    branch: str | None = None
    base: str | None = None
    head: str | None = None
    author: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "body": self.body,
            "branch": self.branch,
            "base": self.base,
            "head": self.head,
            "author": self.author,
            "commits": [
                {"sha": c.sha, "subject": c.subject, "body": c.body} for c in self.commits
            ],
        }


@dataclass(frozen=True)
class ImpactEntry:
    """Transitive dependents of one changed module."""

    path: str
    dependents: tuple[str, ...]
    max_depth: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "dependents": list(self.dependents),
            "dependent_count": len(self.dependents),
            "max_depth": self.max_depth,
        }


@dataclass
class Review:
    """The complete result of reviewing one diff."""

    target: str
    mode: str
    files: int = 0
    added: int = 0
    removed: int = 0
    findings: list[Finding] = field(default_factory=list)
    score: int = 100
    verdict: str = ""
    impact: list[ImpactEntry] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    pr: PullRequest | None = None

    def add(self, findings: Iterable[Finding]) -> None:
        """Extend the finding list."""
        self.findings.extend(findings)

    def severity_counts(self) -> dict[str, int]:
        """Count findings per severity, including zero entries."""
        counts = {item.value: 0 for item in Severity}
        for finding in self.findings:
            counts[finding.severity.value] += 1
        return counts

    def blocking(self, threshold: Severity) -> list[Finding]:
        """Findings at or above threshold."""
        return [f for f in self.findings if f.severity.rank >= threshold.rank]

    def to_dict(self) -> dict[str, Any]:
        """Full JSON representation, stable across runs."""
        return {
            "target": self.target,
            "mode": self.mode,
            "files": self.files,
            "added": self.added,
            "removed": self.removed,
            "score": self.score,
            "verdict": self.verdict,
            "severity_counts": self.severity_counts(),
            "findings": [f.to_dict() for f in self.findings],
            "impact": [entry.to_dict() for entry in self.impact],
            "stats": self.stats,
            "notes": self.notes,
            "pr": None if self.pr is None else self.pr.to_dict(),
        }
