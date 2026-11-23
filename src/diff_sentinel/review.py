"""Analysis pipeline: diff -> context -> rules -> impact map -> score.

The public entry points are review_diff() for an already parsed patch and
review_patch() for raw text. Both are pure with respect to the filesystem apart
from the optional repository scan used to build the impact map.
"""

from __future__ import annotations

from pathlib import Path

from .classify import classify
from .diffparse import Diff, parse_patch
from .imports import ImportGraph, build_import_graph, module_key
from .model import ImpactEntry, PullRequest, Review, Severity
from .rules import ReviewContext, ReviewOptions, run_rules
from .scoring import compute_score

MAX_IMPACT_DEPENDENTS = 40


def review_patch(
    text: str,
    *,
    repo: str | Path | None = None,
    pr: PullRequest | None = None,
    options: ReviewOptions | None = None,
    target: str = "patch",
    mode: str = "patch",
    skip_impact: bool = False,
) -> Review:
    """Review a unified diff given as text."""
    return review_diff(
        parse_patch(text),
        repo=repo,
        pr=pr,
        options=options,
        target=target,
        mode=mode,
        skip_impact=skip_impact,
    )


def review_diff(
    diff: Diff,
    *,
    repo: str | Path | None = None,
    pr: PullRequest | None = None,
    options: ReviewOptions | None = None,
    target: str = "patch",
    mode: str = "patch",
    skip_impact: bool = False,
) -> Review:
    """Review a parsed Diff and return a scored Review."""
    opts = options or ReviewOptions()
    files = apply_ignore(diff, opts.ignore)
    truncated = len(diff.files) - len(files.files)
    review = Review(
        target=target,
        mode=mode,
        files=len(files.files),
        added=files.added,
        removed=files.removed,
        pr=pr,
    )
    if truncated:
        review.notes.append(
            str(truncated) + " file(s) excluded by --ignore; counts reflect the rest"
        )
    ctx = ReviewContext(diff=files, options=opts, repo=None if repo is None else str(repo), pr=pr)
    ctx.investigate()
    run_rules(ctx, review)
    if repo is not None and not skip_impact:
        review.impact = compute_impact(files, Path(repo), opts.ignore)
    review.stats.update(
        {
            "files": len(files.files),
            "added": files.added,
            "removed": files.removed,
            "test_files": len(ctx.test_paths),
            "source_files": len(ctx.changed_source),
            "ignored_files": truncated,
            "rule_count": 19,
        }
    )
    if files.is_empty:
        review.notes.append("the diff contains no file changes")
    review.score, review.verdict = compute_score(review)
    return review


def apply_ignore(diff: Diff, prefixes: tuple[str, ...]) -> Diff:
    """Drop files whose path starts with one of the ignore prefixes."""
    if not prefixes:
        return diff
    kept = [item for item in diff.files if not any(item.path.startswith(p) for p in prefixes)]
    return Diff(files=kept, preamble=diff.preamble)


def compute_impact(
    diff: Diff,
    repo: Path,
    ignore: tuple[str, ...] = (),
    max_entries: int = MAX_IMPACT_DEPENDENTS,
) -> list[ImpactEntry]:
    """Reverse import graph over the repository, restricted to changed modules."""
    changed: list[tuple[str, str]] = []
    for item in diff.files:
        info = classify(item.path)
        if info.is_test or info.is_doc or item.is_deleted:
            continue
        changed.append((item.path, module_key(item.path)))
    if not changed:
        return []
    graph: ImportGraph = build_import_graph(repo, skip=tuple(ignore))
    entries: list[ImpactEntry] = []
    for path, key in changed:
        dependents, depth = graph.transitive_dependents(key)
        if not dependents:
            continue
        entries.append(
            ImpactEntry(path=path, dependents=tuple(dependents[:max_entries]), max_depth=depth)
        )
    entries.sort(key=lambda entry: (-len(entry.dependents), entry.path))
    return entries


def is_blocking(review: Review, threshold: Severity) -> bool:
    """True when any finding is at or above threshold."""
    return bool(review.blocking(threshold))
