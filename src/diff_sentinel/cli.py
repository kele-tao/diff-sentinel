"""Command line interface.

Exit codes (documented in the README and enforced by tests):

* 0 - review completed and no finding reached the --fail-on threshold
* 1 - the review ran and the gate failed (findings at or above the threshold)
* 2 - usage error: bad flag, unreadable patch, unknown git ref, not a repository
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .catalog import RULE_CATALOG
from .diffparse import DiffParseError, parse_patch
from .gitutil import (
    GitError,
    current_branch,
    diff_text,
    is_repo,
    pr_metadata,
    repo_root,
    resolve_range,
    run_git,
    staged_diff,
)
from .model import PullRequest, Review, Severity
from .render import (
    render_ci_snippet,
    render_json,
    render_markdown,
    render_score_line,
    render_terminal,
    write_output,
)
from .review import review_diff
from .rules import ReviewOptions
from .scoring import compute_score, gate_message

PROG = "diff-sentinel"
EPILOG = """examples:
  diff-sentinel review --base main
  diff-sentinel review --patch pr.patch --format md
  diff-sentinel score --base origin/main --fail-on high
  diff-sentinel review --patch pr.patch --emit-ci > .github/workflows/review.yml
"""


class UsageError(Exception):
    """A user mistake that should exit with status 2."""


def build_parser() -> argparse.ArgumentParser:
    """Build the argparse tree for the three subcommands."""
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Static pull-request reviewer: risk, tests, migrations, dependencies, "
        "secrets and impact. No LLM, no network.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=PROG + " " + __version__)
    sub = parser.add_subparsers(dest="command", metavar="command")

    review = sub.add_parser(
        "review",
        help="review a diff and print a prioritised findings list",
        description="Review the diff between a base ref and HEAD, or a unified patch file.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=EPILOG,
    )
    add_source_flags(review)
    add_review_flags(review)
    review.add_argument(
        "--format",
        choices=("terminal", "md", "json"),
        default="terminal",
        help="output format (default: terminal)",
    )
    review.add_argument(
        "--fail-on",
        metavar="SEVERITY",
        default=None,
        help="exit 1 when a finding at or above this severity exists "
        "(critical, high, medium, low, info)",
    )
    review.add_argument(
        "--emit-ci",
        action="store_true",
        help="print a ready-to-commit GitHub Actions workflow and exit",
    )

    score = sub.add_parser(
        "score",
        help="print the 0-100 review-readiness score and gate CI on it",
        description="Same analysis as review, but the output is one score line.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=EPILOG,
    )
    add_source_flags(score)
    add_review_flags(score)
    score.add_argument(
        "--fail-on",
        metavar="SEVERITY",
        default="high",
        help="exit 1 when a finding at or above this severity exists (default: high)",
    )
    score.add_argument(
        "--json", action="store_true", help="print the full review as JSON instead of one line"
    )

    rules = sub.add_parser("rules", help="list every rule, its severity and what it looks for")
    rules.add_argument("--json", action="store_true", help="machine-readable rule list")
    return parser


def add_source_flags(parser: argparse.ArgumentParser) -> None:
    """Flags that decide which diff is reviewed."""
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--base",
        metavar="REF",
        default="main",
        help="git ref to diff HEAD against (default: main; origin/main in CI)",
    )
    group.add_argument(
        "--patch",
        metavar="FILE",
        help="review a unified diff file instead of a git range; use - for stdin",
    )
    parser.add_argument(
        "--repo", metavar="DIR", default=".", help="repository directory (default: .)"
    )
    parser.add_argument(
        "--staged",
        action="store_true",
        help="review staged changes (git diff --cached) instead of a base branch",
    )


def add_review_flags(parser: argparse.ArgumentParser) -> None:
    """Flags that change the analysis or its rendering."""
    parser.add_argument(
        "--title", metavar="TEXT", default=None, help="PR title, used by the hygiene rules"
    )
    parser.add_argument(
        "--body", metavar="TEXT", default=None, help="PR description, used by the hygiene rules"
    )
    parser.add_argument(
        "--ignore",
        metavar="PREFIX",
        action="append",
        default=[],
        help="skip files whose path starts with PREFIX (repeatable)",
    )
    parser.add_argument(
        "--max-files",
        metavar="N",
        type=int,
        default=400,
        help="refuse to review diffs with more than N files (default: 400)",
    )
    parser.add_argument("--no-tests", action="store_true", help="disable the missing-test rules")
    parser.add_argument(
        "--color",
        choices=("auto", "always", "never"),
        default="auto",
        help="ANSI colour for --format terminal (default: auto)",
    )
    parser.add_argument(
        "--limit",
        metavar="N",
        type=int,
        default=None,
        help="print at most N findings in --format terminal (default: all)",
    )
    parser.add_argument(
        "-o",
        "--output",
        metavar="FILE",
        default=None,
        help="write the rendered output to FILE instead of stdout",
    )


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns the process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 2
    try:
        if args.command == "rules":
            return cmd_rules(args)
        if args.command == "review":
            return cmd_review(args)
        if args.command == "score":
            return cmd_score(args)
    except UsageError as exc:
        sys.stderr.write(PROG + ": error: " + str(exc) + "\n")
        return 2
    except (GitError, DiffParseError) as exc:
        sys.stderr.write(PROG + ": error: " + str(exc) + "\n")
        return 2
    except ValueError as exc:
        sys.stderr.write(PROG + ": error: " + str(exc) + "\n")
        return 2
    except KeyboardInterrupt:
        sys.stderr.write(PROG + ": interrupted\n")
        return 2
    parser.print_help()
    return 2


def parse_threshold(value: str | None, default: Severity | None) -> Severity | None:
    """Parse --fail-on, raising UsageError on an unknown severity name."""
    if value is None:
        return default
    try:
        return Severity.parse(value)
    except ValueError as exc:
        raise UsageError(str(exc)) from None


def color_enabled(mode: str) -> bool:
    """Resolve --color auto against the actual stdout."""
    if mode == "always":
        return True
    if mode == "never":
        return False
    return bool(getattr(sys.stdout, "isatty", lambda: False)())


def read_patch(path: str) -> str:
    """Read a patch file (or stdin) with friendly errors."""
    if path == "-":
        try:
            return sys.stdin.read()
        except OSError as exc:
            raise UsageError("cannot read stdin: " + str(exc)) from None
    file_path = Path(path)
    if not file_path.exists():
        raise UsageError(
            "patch file not found: "
            + path
            + " (pass an existing .patch/.diff file, or - for stdin)"
        )
    if file_path.is_dir():
        raise UsageError("expected a patch file but " + path + " is a directory")
    try:
        return file_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise UsageError("cannot read " + path + ": " + str(exc)) from None


def review_options(args: argparse.Namespace) -> ReviewOptions:
    """Translate CLI flags into ReviewOptions."""
    return ReviewOptions(
        max_files=args.max_files if args.max_files > 0 else 400,
        ignore=tuple(args.ignore),
        pr_title=args.title,
        pr_body=args.body,
        check_tests=not args.no_tests,
    )


def empty_review(target: str, mode: str, pr: PullRequest | None, note: str) -> Review:
    """A scored review for a diff with no changes."""
    review = Review(target=target, mode=mode, pr=pr, notes=[note])
    review.score, review.verdict = compute_score(review)
    return review


def finish(
    diff,
    *,
    repo: str | None,
    options: ReviewOptions,
    target: str,
    mode: str,
    pr: PullRequest | None,
) -> Review:
    """Enforce the file budget and hand the parsed diff to the engine."""
    if len(diff.files) > options.max_files:
        raise UsageError(
            "diff touches "
            + str(len(diff.files))
            + " files, above --max-files "
            + str(options.max_files)
            + "; review a narrower range or raise the limit deliberately"
        )
    return review_diff(
        diff, repo=repo, pr=pr, options=options, target=target, mode=mode, skip_impact=repo is None
    )


def load_review(args: argparse.Namespace) -> Review:
    """Build the Review for either a patch file or a git range."""
    options = review_options(args)
    if getattr(args, "patch", None):
        text = read_patch(args.patch)
        if not text.strip():
            raise UsageError("patch is empty: nothing to review (" + args.patch + ")")
        target = "<stdin>" if args.patch == "-" else Path(args.patch).name
        return finish(
            parse_patch(text), repo=None, options=options, target=target, mode="patch", pr=None
        )

    repo_dir = Path(args.repo)
    if not repo_dir.exists():
        raise UsageError("repository directory not found: " + str(repo_dir))
    if not is_repo(repo_dir):
        raise UsageError(
            str(repo_dir) + " is not a git repository; pass --repo DIR or --patch pr.patch"
        )
    root = repo_root(repo_dir)
    has_head = (
        run_git(root, ["rev-parse", "--verify", "--quiet", "HEAD"], check=False).returncode == 0
    )
    if not has_head:
        raise UsageError("repository has no commits yet; commit something before reviewing")

    if args.staged:
        text = staged_diff(root)
        target, mode, pr = "staged changes", "staged", None
    else:
        rev_range = resolve_range(root, args.base)
        text = diff_text(root, rev_range)
        target, mode = args.base + "...HEAD", "branch"
        pr = pr_metadata(root, args.base, current_branch(root))
        if args.title is not None:
            pr = PullRequest(
                title=args.title,
                body=args.body,
                commits=pr.commits,
                branch=pr.branch,
                base=pr.base,
                head=pr.head,
                author=pr.author,
            )
    if not text.strip():
        return empty_review(target, mode, pr, "no changes: the diff is empty")

    review = finish(
        parse_patch(text), repo=root, options=options, target=target, mode=mode, pr=pr
    )
    if pr is not None and options.pr_title is None and pr.commits:
        review.notes.append("PR title not supplied; pass --title to enable the PR-hygiene rules")
    return review


def cmd_review(args: argparse.Namespace) -> int:
    """The review subcommand."""
    if args.emit_ci:
        sys.stdout.write(render_ci_snippet())
        return 0
    threshold = parse_threshold(args.fail_on, None)
    review = load_review(args)
    if args.format == "json":
        text = render_json(review)
    elif args.format == "md":
        text = render_markdown(review)
    else:
        text = render_terminal(
            review, color=color_enabled(args.color), limit=args.limit
        )
    write_output(text, args.output)
    if threshold is not None:
        blocking = review.blocking(threshold)
        if blocking:
            sys.stderr.write(PROG + ": gate failed - " + gate_message(blocking, threshold) + "\n")
            return 1
    return 0


def cmd_score(args: argparse.Namespace) -> int:
    """The score subcommand."""
    threshold = parse_threshold(args.fail_on, Severity.HIGH)
    assert threshold is not None
    review = load_review(args)
    if args.json:
        write_output(render_json(review), args.output)
    else:
        write_output(render_score_line(review, threshold), args.output)
    return 1 if review.blocking(threshold) else 0


def cmd_rules(args: argparse.Namespace) -> int:
    """The rules subcommand."""
    if args.json:
        print(json.dumps(RULE_CATALOG, indent=2))
        return 0
    width = max(len(rule["id"]) for rule in RULE_CATALOG)
    for rule in RULE_CATALOG:
        print(rule["id"].ljust(width) + "  " + rule["severity"].ljust(8) + "  " + rule["summary"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
