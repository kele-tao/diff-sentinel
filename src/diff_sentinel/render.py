"""Output renderers: terminal review card, Markdown, JSON and the CI snippet.

All renderers are pure functions of a Review, so --format json and --format md
can never disagree about the findings. The terminal renderer uses ANSI colour
only when the stream is a TTY; pass color=True to force it.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from .model import Finding, Review, Severity

RESET = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"

COLORS: dict[Severity, str] = {
    Severity.CRITICAL: "\x1b[97;41m",
    Severity.HIGH: "\x1b[91m",
    Severity.MEDIUM: "\x1b[93m",
    Severity.LOW: "\x1b[96m",
    Severity.INFO: "\x1b[90m",
}

LABEL_WIDTH = 8
BT = "`"


class Palette:
    """Minimal ANSI helper that degrades to plain text."""

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def paint(self, text: str, code: str) -> str:
        """Wrap text in an ANSI code when colour is enabled."""
        if not self.enabled or not code:
            return text
        return code + text + RESET

    def bold(self, text: str) -> str:
        """Bold text when colour is enabled."""
        return self.paint(text, BOLD)

    def dim(self, text: str) -> str:
        """Dim text when colour is enabled."""
        return self.paint(text, DIM)


def terminal_width(default: int = 100) -> int:
    """Width for the terminal card, capped so the card stays readable."""
    try:
        columns = shutil.get_terminal_size((default, 24)).columns
    except OSError:
        return default
    return max(72, min(110, columns))


def render_terminal(
    review: Review,
    color: bool = False,
    width: int | None = None,
    limit: int | None = None,
) -> str:
    """Render the review card used by the CLI and the README screenshot.

    limit caps how many findings are printed (the rest are summarised); pass
    None to print every finding.
    """
    p = Palette(color)
    width = width or terminal_width()
    inner = width - 2
    out: list[str] = []

    def row(text: str) -> str:
        plain = visible(text)
        clipped = fit(plain, inner - 2)
        if len(clipped) != len(plain):
            text = clipped
        padding = " " * max(0, inner - 1 - len(clipped))
        return p.paint("|", DIM) + " " + text + padding + p.paint("|", DIM)

    target = review.target
    if len(target) > 56:
        target = "..." + target[-53:]
    header = "diff-sentinel " + review.mode + "  " + target
    out.append(p.paint("+" + "-" * inner + "+", DIM))
    out.append(row(p.bold(fit(header, inner - 2))))
    out.append(row("score " + p.bold(str(review.score)) + "/100  " + review.verdict))
    out.append(p.paint("+" + "-" * inner + "+", DIM))
    out.append("")

    counts = review.severity_counts()
    summary = "  ".join(
        severity_badge(p, Severity(value), counts[value])
        for value in ("critical", "high", "medium", "low", "info")
        if counts[value] or value in ("critical", "high")
    )
    out.append("  " + p.bold("findings") + "  " + summary)
    out.append(
        "  "
        + p.dim(
            str(review.files)
            + " files  +"
            + str(review.added)
            + " -"
            + str(review.removed)
            + "  "
            + str(len(review.findings))
            + " findings"
        )
    )
    out.append("")

    if not review.findings:
        out.append("  " + p.paint("No findings.", "\x1b[92m") + " Every rule passed.")
        out.append("")
    else:
        shown = review.findings if limit is None else review.findings[: max(1, limit)]
        hidden = len(review.findings) - len(shown)
        by_severity: dict[Severity, list[Finding]] = {}
        for finding in shown:
            by_severity.setdefault(finding.severity, []).append(finding)
        index = 0
        for severity in sorted(by_severity, key=lambda item: -item.rank):
            heading = severity.label
            out.append(
                "  " + p.bold(heading) + " " + p.dim("-" * max(4, width - 12 - len(heading)))
            )
            for finding in by_severity[severity]:
                index += 1
                out.extend(render_finding(p, finding, index, width))
            out.append("")
        if hidden:
            out.append(
                "  "
                + p.dim(
                    "... "
                    + str(hidden)
                    + " more finding(s) not shown; use --format md or --format json for the "
                    "full list"
                )
            )
            out.append("")

    if review.impact:
        out.append(
            "  "
            + p.bold("impact map")
            + " "
            + p.dim("(modules that transitively import the changed files)")
        )
        for entry in review.impact[:8]:
            out.append(
                "    "
                + p.paint(entry.path, "\x1b[96m")
                + p.dim(
                    " -> "
                    + str(len(entry.dependents))
                    + " dependent(s), depth "
                    + str(entry.max_depth)
                )
            )
            for dependent in entry.dependents[:4]:
                out.append("        " + p.dim(dependent))
            if len(entry.dependents) > 4:
                out.append("        " + p.dim("... " + str(len(entry.dependents) - 4) + " more"))
        out.append("")

    for note in review.notes:
        out.append("  " + p.dim("note: " + note))
    if review.notes:
        out.append("")

    out.append("  " + p.dim("-" * (width - 2)))
    out.append("  " + verdict_line(p, review))
    return "\n".join(out)


def verdict_line(p: Palette, review: Review) -> str:
    """The single-line verdict at the bottom of the card."""
    counts = review.severity_counts()
    if counts["critical"]:
        return p.paint(
            "VERDICT  do not merge: " + str(counts["critical"]) + " critical finding(s)",
            COLORS[Severity.CRITICAL],
        )
    if counts["high"]:
        return p.paint(
            "VERDICT  request changes: " + str(counts["high"]) + " high finding(s)",
            COLORS[Severity.HIGH],
        )
    if counts["medium"]:
        return p.paint(
            "VERDICT  review with nits: " + str(counts["medium"]) + " medium finding(s)",
            COLORS[Severity.MEDIUM],
        )
    return p.paint("VERDICT  ready to merge", "\x1b[92m")


def severity_badge(p: Palette, severity: Severity, count: int) -> str:
    """Coloured per-severity counter badge."""
    text = severity.label + " " + str(count)
    return p.paint(" " + text + " ", COLORS[severity] if count else DIM)


def render_finding(p: Palette, finding: Finding, index: int, width: int) -> list[str]:
    """Render one finding as three to five terminal lines."""
    tag = p.paint(" " + finding.severity.label.ljust(LABEL_WIDTH) + " ", COLORS[finding.severity])
    lines = ["  " + str(index).rjust(2) + ". " + tag + " " + p.bold(finding.title)]
    lines.append("      " + p.paint(finding.location, "\x1b[96m") + "  " + p.dim(finding.rule))
    if finding.evidence:
        evidence = finding.evidence.replace("\n", " ")[: max(20, width - 20)]
        lines.append("      " + p.paint("> ", DIM) + evidence)
    if finding.fix:
        wrapped = wrap_text(finding.fix, width - 17)
        for offset, chunk in enumerate(wrapped):
            prefix = "fix: " if offset == 0 else "     "
            lines.append("      " + p.dim(prefix + chunk))
    return lines


ANSI_STRIP_RE = re.compile(r"\x1b\[[0-9;]*m")


def visible(text: str) -> str:
    """Text with ANSI escape sequences removed (what the terminal shows)."""
    return ANSI_STRIP_RE.sub("", text)


def fit(text: str, width: int) -> str:
    """Clip text to width, adding an ellipsis when clipped."""
    if len(text) <= width:
        return text
    return text[: max(0, width - 3)] + "..."


def wrap_text(text: str, width: int) -> list[str]:
    """Greedy word wrap."""
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = word if not current else current + " " + word
        if len(candidate) > width and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines or [""]


def render_markdown(review: Review, include_impact: bool = True) -> str:
    """Markdown that can be pasted straight into a pull request comment."""
    counts = review.severity_counts()
    lines: list[str] = []
    lines.append("## diff-sentinel review")
    lines.append("")
    lines.append(
        "**Score: "
        + str(review.score)
        + "/100** - "
        + review.verdict
        + "  |  "
        + str(review.files)
        + " files  |  +"
        + str(review.added)
        + " -"
        + str(review.removed)
        + "  |  "
        + str(len(review.findings))
        + " findings"
    )
    lines.append("")
    lines.append("| critical | high | medium | low | info |")
    lines.append("| --- | --- | --- | --- | --- |")
    lines.append(
        "| "
        + " | ".join(str(counts[key]) for key in ("critical", "high", "medium", "low", "info"))
        + " |"
    )
    lines.append("")
    if not review.findings:
        lines.append("No findings. Every rule passed.")
        lines.append("")
        lines.append("<sub>Generated by diff-sentinel - static review, no LLM.</sub>")
        return "\n".join(lines) + "\n"
    lines.append("### Findings")
    lines.append("")
    lines.append("| # | severity | location | problem | evidence | fix |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for index, finding in enumerate(review.findings, 1):
        evidence = md_escape(finding.evidence[:80])
        lines.append(
            "| "
            + str(index)
            + " | "
            + finding.severity.value
            + " | `"
            + md_escape(finding.location)
            + "` | "
            + md_escape(finding.title)
            + " | "
            + ("`" + evidence + "`" if evidence else "")
            + " | "
            + md_escape(finding.fix)
            + " |"
        )
    if include_impact and review.impact:
        lines.append("")
        lines.append("### Impact map")
        lines.append("")
        lines.append("| changed module | dependents | depth | who depends on it |")
        lines.append("| --- | --- | --- | --- |")
        for entry in review.impact[:15]:
            names = ", ".join("`" + name + "`" for name in entry.dependents[:5])
            if len(entry.dependents) > 5:
                names += ", ... " + str(len(entry.dependents) - 5) + " more"
            lines.append(
                "| `"
                + md_escape(entry.path)
                + "` | "
                + str(len(entry.dependents))
                + " | "
                + str(entry.max_depth)
                + " | "
                + names
                + " |"
            )
    lines.append("")
    lines.append("<sub>Generated by diff-sentinel - static review, no LLM.</sub>")
    return "\n".join(lines) + "\n"


def md_escape(text: str) -> str:
    """Escape pipes and newlines so a finding cannot break the Markdown table."""
    return text.replace("|", "\\|").replace("\n", " ")


def render_json(review: Review) -> str:
    """Stable JSON with a trailing newline."""
    return json.dumps(review.to_dict(), indent=2, ensure_ascii=False) + "\n"


GITHUB_WORKFLOW = """name: diff-sentinel
on:
  pull_request:
    types: [opened, synchronize, reopened]
permissions:
  contents: read
  pull-requests: write
jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -e .
      - name: Review the diff
        run: |
          diff-sentinel review --base "origin/BASE_REF" --format md > review.md
          diff-sentinel score --base "origin/BASE_REF" --fail-on high
      - name: Comment on the pull request
        if: always()
        uses: marocchino/sticky-pull-request-comment@v2
        with:
          path: review.md
"""


def render_ci_snippet() -> str:
    """The GitHub Actions workflow printed by --emit-ci."""
    return GITHUB_WORKFLOW


def render_score_line(review: Review, threshold: Severity) -> str:
    """One-line output for the score subcommand."""
    blocking = review.blocking(threshold)
    status = "PASS" if not blocking else "FAIL"
    return (
        status
        + "  score="
        + str(review.score)
        + "/100  findings="
        + str(len(review.findings))
        + "  blocking="
        + str(len(blocking))
        + " (>="
        + threshold.value
        + ")  "
        + review.verdict
    )


def write_output(text: str, destination: str | None) -> None:
    """Write rendered output to a file, or print it to stdout."""
    if destination is None:
        print(text, end="" if text.endswith("\n") else "\n")
        return
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    print("wrote " + str(path) + " (" + str(path.stat().st_size) + " bytes)")
