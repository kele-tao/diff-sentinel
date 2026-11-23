"""The review rules.

Each rule is a pure function: it receives a ReviewContext and returns findings.
Rules never mutate the context, never touch the network, and never read files
outside the repository root, which keeps them safe to run on untrusted patches.

Severity policy (published in the README):

* critical - merging would ship a credential, drop data, or remove a safety net
* high     - a reviewer must ask for a change before merge
* medium   - worth fixing in this PR, but not blocking
* low      - nit or hygiene signal
* info     - context only, never blocks
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .classify import PathInfo, classify, is_source_like, top_module
from .diffparse import Diff, FileDiff
from .model import Finding, PullRequest, Review, Severity

LARGE_ADDITION = 80
BIG_FILE_COUNT = 25
BIG_DIFF_LINES = 900
HUGE_DIFF_LINES = 2000

DECISION_RE = re.compile(
    r"\b(if|elif|else|for|while|switch|case|catch|except|finally|assert)\b"
    r"|\?\s*[^:]+:"
    r"|&&|\|\|"
)

FUNC_DEF_PY = re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)\s*\(")
FUNC_DEF_JS = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)"
    r"|^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\("
    r"|^\s*(?:export\s+)?(?:default\s+)?class\s+([A-Za-z_$][\w$]*)"
)

ASSERTION_RE = re.compile(
    r"(?:^|[\s.(])(assert\b|assertEqual|assertTrue|assertFalse|assertRaises|assertIn|"
    r"expect\s*\(|to(?:Be|Equal|HaveBeenCalled|Throw|Match)|XCTAssert|require\.|"
    r"t\.(?:Error|Fatal)|should\.|pytest\.raises|assertEquals)"
)

SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), "AWS access key id"),
    (
        "private-key",
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----"),
        "private key block",
    ),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}\b"), "GitHub token"),
    ("gitlab-token", re.compile(r"\bglpat-[A-Za-z0-9_-]{16,}\b"), "GitLab token"),
    ("slack-token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"), "Slack token"),
    ("stripe-key", re.compile(r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{10,}\b"), "Stripe key"),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"), "Google API key"),
    (
        "jwt",
        re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
        "JSON Web Token",
    ),
    (
        "db-url",
        re.compile(
            r"\b(?:postgres|postgresql|mysql|mongodb(?:\+srv)?|redis)://"
            r"[^\s:@/]+:[^\s:@/]+@",
            re.I,
        ),
        "database URL with credentials",
    ),
    (
        "hardcoded-credential",
        re.compile(
            r"""(?ix)
            \b(?:password|passwd|pwd|secret|api[_-]?key|apikey|access[_-]?token|
                 auth[_-]?token|client[_-]?secret|private[_-]?key|credential)\b
            \s*[:=]\s*
            ["'][^"']{6,}["']"""
        ),
        "hardcoded credential",
    ),
)

PLACEHOLDER_RE = re.compile(
    r"(?i)(changeme|change_me|placeholder|example|dummy|sample|your[_-]?|xxxx|\*\*\*|"
    r"\$\{|\{\{|%s|<[a-z_]+>|fake|redacted|nosuch|notreal)"
)

DEBUG_PATTERNS: tuple[tuple[re.Pattern[str], Severity, str], ...] = (
    (
        re.compile(r"^\s*(?:print|console\.log|console\.debug|fmt\.Print(?:f|ln)?)\s*\("),
        Severity.LOW,
        "debug print",
    ),
    (
        re.compile(
            r"^\s*(?:import pdb|pdb\.set_trace|breakpoint\s*\(\s*\)|debugger\s*;?\s*$|"
            r"binding\.pry|byebug)"
        ),
        Severity.MEDIUM,
        "interactive debugger call",
    ),
)

COMMENTED_CODE_RE = re.compile(
    r"^\s*(?:#|//)\s*(?:if |for |while |def |fn |class |return |import |from |const |let |"
    r"var |function |public |private |SELECT |INSERT |UPDATE |DELETE )"
)

MIGRATION_TX_RE = re.compile(
    r"(?i)(?:\bBEGIN\s*;|\bCOMMIT\s*;|START TRANSACTION|atomic\s*=\s*True)"
)

MIGRATION_HAZARDS: tuple[tuple[re.Pattern[str], Severity, str, str], ...] = (
    (
        re.compile(r"\bDROP\s+COLUMN\b", re.I),
        Severity.CRITICAL,
        "DROP COLUMN destroys data",
        "Ship a two step migration: stop writing the column, deploy that, then drop it in a "
        "later release after a verified backup.",
    ),
    (
        re.compile(r"\bDROP\s+TABLE\b", re.I),
        Severity.CRITICAL,
        "DROP TABLE destroys a table",
        "Rename the table to a tombstone, deploy readers off it, then delete it in a later "
        "release.",
    ),
    (
        re.compile(r"\bTRUNCATE\b", re.I),
        Severity.HIGH,
        "TRUNCATE wipes rows",
        "Use a scoped DELETE with a WHERE clause, or move the rows to an archive table first.",
    ),
    (
        re.compile(r"(?i)\bADD\s+COLUMN\s+[\w\"]+\s+[^;]*NOT\s+NULL"),
        Severity.HIGH,
        "NOT NULL column added without a default",
        "Add the column as nullable, backfill it in batches, then set NOT NULL in a second "
        "migration. On MySQL the single step also rewrites the table under a lock.",
    ),
    (
        re.compile(r"(?i)\bALTER\s+COLUMN\s+[\w\"]+\s+(?:TYPE|SET DATA TYPE)"),
        Severity.MEDIUM,
        "column type change",
        "Check the implicit cast: widening is safe, narrowing truncates. Backfill and verify "
        "row counts before and after.",
    ),
    (
        re.compile(r"(?i)\bRENAME\s+(?:COLUMN|TO)\b"),
        Severity.MEDIUM,
        "rename breaks readers",
        "Deploy code that reads both names, then rename, then delete the old path.",
    ),
    (
        re.compile(r"(?i)\bCREATE\s+(?:UNIQUE\s+)?INDEX\b(?!\s+CONCURRENTLY)"),
        Severity.LOW,
        "index built without CONCURRENTLY",
        "On PostgreSQL use CREATE INDEX CONCURRENTLY outside a transaction to avoid blocking "
        "writes for the duration of the build.",
    ),
    (
        re.compile(r"(?i)\bUPDATE\s+\w+\s+SET\b(?!.*\bWHERE\b)"),
        Severity.HIGH,
        "UPDATE without WHERE",
        "Add a WHERE clause, or run it inside a transaction and verify the affected row count "
        "before committing.",
    ),
)

GENERIC_SUBJECTS = re.compile(
    r"^(?:wip|fix|fixes|fixed|update|updates|changes?|stuff|temp|debug|more|misc|cleanup|"
    r"final|address comments)\b\s*$",
    re.I,
)

MANIFEST_NAMES = {
    "requirements.txt",
    "requirements-dev.txt",
    "requirements-prod.txt",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "package.json",
    "go.mod",
    "cargo.toml",
    "gemfile",
    "pom.xml",
    "composer.json",
}

#: Popular packages with their licence. Used for the licence guess and for the
#: typosquat distance check. An unknown package is reported as "unknown", never
#: guessed.
KNOWN_PACKAGES: dict[str, str] = {
    "requests": "Apache-2.0",
    "urllib3": "MIT",
    "numpy": "BSD-3-Clause",
    "pandas": "BSD-3-Clause",
    "flask": "BSD-3-Clause",
    "django": "BSD-3-Clause",
    "fastapi": "MIT",
    "pydantic": "MIT",
    "sqlalchemy": "MIT",
    "click": "BSD-3-Clause",
    "pytest": "MIT",
    "boto3": "Apache-2.0",
    "botocore": "Apache-2.0",
    "cryptography": "Apache-2.0 OR BSD-3-Clause",
    "pyyaml": "MIT",
    "python-dateutil": "Apache-2.0 OR BSD-3-Clause",
    "setuptools": "MIT",
    "httpx": "BSD-3-Clause",
    "aiohttp": "Apache-2.0",
    "celery": "BSD-3-Clause",
    "redis": "MIT",
    "psycopg2": "LGPL-3.0",
    "psycopg2-binary": "LGPL-3.0",
    "jinja2": "BSD-3-Clause",
    "markupsafe": "BSD-3-Clause",
    "werkzeug": "BSD-3-Clause",
    "jsonschema": "MIT",
    "pillow": "MIT-CMU",
    "lxml": "BSD-3-Clause",
    "orjson": "Apache-2.0 OR MIT",
    "uvicorn": "BSD-3-Clause",
    "gunicorn": "MIT",
    "alembic": "MIT",
    "lodash": "MIT",
    "axios": "MIT",
    "react": "MIT",
    "vue": "MIT",
    "express": "MIT",
    "typescript": "Apache-2.0",
    "webpack": "MIT",
    "eslint": "MIT",
    "prettier": "MIT",
    "moment": "MIT",
    "chalk": "MIT",
    "commander": "MIT",
    "stripe": "MIT",
    "zod": "MIT",
    "next": "MIT",
    "mongoose": "MIT",
    "sequelize": "MIT",
    "jest": "MIT",
    "vitest": "MIT",
}


@dataclass
class ReviewOptions:
    """Knobs that change what the rules look at."""

    max_files: int = 400
    ignore: tuple[str, ...] = ()
    pr_title: str | None = None
    pr_body: str | None = None
    check_tests: bool = True


@dataclass
class ReviewContext:
    """Everything the rules need, computed once per review."""

    diff: Diff
    options: ReviewOptions
    repo: str | None = None
    pr: PullRequest | None = None
    infos: dict[str, PathInfo] = field(default_factory=dict)
    test_text: str = ""
    test_paths: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def investigate(self) -> None:
        """Precompute path classification and the concatenated test text."""
        for item in self.diff.files:
            self.infos[item.path] = classify(item.path)
        self.test_paths = [p for p, info in self.infos.items() if info.is_test]
        chunks: list[str] = []
        for item in self.diff.files:
            if self.infos[item.path].is_test:
                chunks.append(item.added_text())
                chunks.append(item.removed_text())
        self.test_text = "\n".join(chunks)

    @property
    def changed_source(self) -> list[FileDiff]:
        """Non-test files where behaviour lives."""
        return [
            item
            for item in self.diff.files
            if not self.infos[item.path].is_test and is_source_like(self.infos[item.path])
        ]


def run_rules(ctx: ReviewContext, review: Review) -> None:
    """Execute every rule and attach the findings to review."""
    for rule in ALL_RULES:
        review.add(rule(ctx))
    review.findings.sort(key=lambda f: f.sort_key())
    review.notes.extend(ctx.notes)


# --------------------------------------------------------------------------- #
# 1. change-risk classification
# --------------------------------------------------------------------------- #

RISK_LABELS: dict[str, tuple[str, Severity, str]] = {
    "payments": (
        "payments",
        Severity.CRITICAL,
        "Money path changed; a defect here charges or refunds the wrong amount.",
    ),
    "auth": (
        "authentication",
        Severity.CRITICAL,
        "Auth surface changed; a defect here is an account-takeover class bug.",
    ),
    "migrations": (
        "database schema",
        Severity.HIGH,
        "Schema migration changed; review the rollback path and the lock it takes.",
    ),
    "data": (
        "SQL",
        Severity.HIGH,
        "Raw SQL changed; check indexes, locking and affected row counts.",
    ),
    "infra": (
        "infrastructure",
        Severity.HIGH,
        "Deployment or CI changed; a defect lands straight in production.",
    ),
    "config": (
        "configuration",
        Severity.MEDIUM,
        "Configuration changed; verify defaults and that secrets stay out of it.",
    ),
    "dependencies": (
        "dependency manifest",
        Severity.MEDIUM,
        "Dependency manifest changed; see the dependency findings below.",
    ),
}


def first_added(item: FileDiff) -> str:
    """First added line of a file diff, clipped, for evidence display."""
    lines = item.added_lines()
    if lines:
        return lines[0][1].strip()[:120]
    return "(file removed)"


def rule_risk(ctx: ReviewContext) -> list[Finding]:
    """Flag changes in areas where a mistake is expensive, once per area."""
    found: list[Finding] = []
    grouped: dict[str, list[FileDiff]] = {}
    for item in ctx.diff.files:
        info = ctx.infos[item.path]
        if info.is_test:
            continue
        if info.area in RISK_LABELS:
            grouped.setdefault(info.area, []).append(item)
    for area, items in grouped.items():
        name, severity, why = RISK_LABELS[area]
        first = items[0]
        added = first.added_lines()
        if area in ("migrations", "data") and added:
            ddl = [
                (lineno, text)
                for lineno, text in added
                if re.search(r"(?i)\b(?:CREATE|ALTER|DROP|TRUNCATE|UPDATE|INSERT)\b", text)
            ]
            if ddl:
                added = ddl
        title = name + " code changed"
        if len(items) > 1:
            title = name + " code changed in " + str(len(items)) + " files"
        evidence = added[0][1].strip()[:120] if added else first_added(first)
        found.append(
            Finding(
                rule="risk-area",
                severity=severity,
                title=title,
                path=first.path,
                line=added[0][0] if added else None,
                evidence=evidence,
                fix="Get a domain owner to review this file before merge.",
                detail=why,
            )
        )
    return found


# --------------------------------------------------------------------------- #
# 2. missing tests
# --------------------------------------------------------------------------- #


def added_defs(item: FileDiff) -> list[str]:
    """Names of functions and classes added by this file diff."""
    names: list[str] = []
    for _, text in item.added_lines():
        for pattern in (FUNC_DEF_PY, FUNC_DEF_JS):
            match = pattern.match(text)
            if match:
                groups = [group for group in match.groups() if group]
                if groups:
                    names.append(groups[0])
    return names


def branch_points(item: FileDiff) -> int:
    """Number of added lines that introduce a decision point."""
    return sum(1 for _, text in item.added_lines() if DECISION_RE.search(text))


def rule_missing_tests(ctx: ReviewContext) -> list[Finding]:
    """New branches without a corresponding test change."""
    if not ctx.options.check_tests:
        return []
    found: list[Finding] = []
    test_text = ctx.test_text
    for item in ctx.changed_source:
        if not item.added:
            continue
        branches = branch_points(item)
        if branches == 0:
            continue
        stem = Path(item.path).stem
        if stem in ("__init__", "index", "mod"):
            stem = Path(item.path).parent.name
        defs = added_defs(item)
        stem_mentioned = bool(stem) and stem in test_text
        defs_mentioned = [
            name for name in defs if re.search(r"\b" + re.escape(name) + r"\b", test_text)
        ]
        if not ctx.test_paths:
            found.append(
                Finding(
                    rule="missing-tests",
                    severity=Severity.HIGH,
                    title="new branches, zero test changes",
                    path=item.path,
                    line=item.added_lines()[0][0],
                    evidence=str(branches) + " new decision point(s)",
                    fix="Add one test per new branch: the true path and the false path.",
                    detail="This file adds "
                    + str(branches)
                    + " branches but the diff touches no test file.",
                )
            )
            continue
        if not stem_mentioned and not defs_mentioned:
            target = defs[0] if defs else stem
            found.append(
                Finding(
                    rule="missing-tests-untargeted",
                    severity=Severity.MEDIUM,
                    title="test changes do not mention the changed code",
                    path=item.path,
                    line=item.added_lines()[0][0],
                    evidence=target,
                    fix="Point the new test at "
                    + target
                    + ": import the module under test and assert the new branch.",
                    detail="The diff changes tests, but none of them reference "
                    + repr(target)
                    + ".",
                )
            )
    return found


def rule_deleted_assertions(ctx: ReviewContext) -> list[Finding]:
    """Assertions removed from tests are how regressions get shipped."""
    found: list[Finding] = []
    for item in ctx.diff.files:
        info = ctx.infos[item.path]
        if item.is_deleted and info.is_test:
            found.append(
                Finding(
                    rule="deleted-assertions",
                    severity=Severity.CRITICAL,
                    title="test file deleted",
                    path=item.path,
                    evidence=str(item.removed) + " lines removed",
                    fix="Restore the file, or explain in the PR body why the coverage is no "
                    "longer needed.",
                    detail="Deleting a test file removes the regression net for everything it "
                    "covered.",
                )
            )
            continue
        if not info.is_test:
            continue
        removed = [text for _, text in item.removed_lines() if ASSERTION_RE.search(text)]
        added = [text for _, text in item.added_lines() if ASSERTION_RE.search(text)]
        if removed and len(removed) > len(added):
            found.append(
                Finding(
                    rule="deleted-assertions",
                    severity=Severity.HIGH,
                    title="assertions removed from tests",
                    path=item.path,
                    line=item.removed_lines()[0][0],
                    evidence=removed[0].strip()[:120],
                    fix="Keep the assertion unless the behaviour it encodes was deliberately "
                    "dropped; if so, say so in the PR body.",
                    detail=str(len(removed))
                    + " assertion(s) removed, "
                    + str(len(added))
                    + " added.",
                )
            )
    return found


# --------------------------------------------------------------------------- #
# 3. migration hazards
# --------------------------------------------------------------------------- #


def rule_migration_hazards(ctx: ReviewContext) -> list[Finding]:
    """Destructive or locking SQL in migration and SQL files."""
    found: list[Finding] = []
    for item in ctx.diff.files:
        info = ctx.infos[item.path]
        if info.area not in ("migrations", "data") and not item.path.lower().endswith(".sql"):
            continue
        if item.is_deleted:
            continue
        added = item.added_lines()
        if not added:
            continue
        seen: set[str] = set()
        has_ddl = False
        for lineno, text in added:
            if re.search(r"(?i)\b(?:CREATE|ALTER|DROP|TRUNCATE)\b", text):
                has_ddl = True
            for pattern, severity, title, hint in MIGRATION_HAZARDS:
                if not pattern.search(text) or title in seen:
                    continue
                seen.add(title)
                found.append(
                    Finding(
                        rule="migration-hazard",
                        severity=severity,
                        title=title,
                        path=item.path,
                        line=lineno,
                        evidence=text.strip()[:140],
                        fix=hint,
                        detail="Detected in a migration/SQL file.",
                    )
                )
        if has_ddl and not any(MIGRATION_TX_RE.search(text) for _, text in added):
            ddl_line = next(
                (
                    (lineno, text)
                    for lineno, text in added
                    if re.search(r"(?i)\b(?:CREATE|ALTER|DROP|TRUNCATE)\b", text)
                ),
                added[0],
            )
            found.append(
                Finding(
                    rule="migration-no-transaction",
                    severity=Severity.MEDIUM,
                    title="DDL without an explicit transaction",
                    path=item.path,
                    line=ddl_line[0],
                    evidence=ddl_line[1].strip()[:120],
                    fix="Wrap the DDL in BEGIN; ... COMMIT; (or the framework's atomic block) so "
                    "a failure halfway cannot leave a half-migrated schema.",
                    detail="The migration adds DDL but never opens a transaction.",
                )
            )
    return found


# --------------------------------------------------------------------------- #
# 4. dependency additions
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Dependency:
    """One parsed dependency line."""

    name: str
    version: str
    raw: str


REQ_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*([<>=!~].*)?$")
QUOTED_DEP_RE = re.compile(r"""^\s*["']([A-Za-z0-9][A-Za-z0-9._-]*)\s*([<>=!~][^"']*)?["']""")
JSON_DEP_RE = re.compile(r'^\s*"((?:@[\w.-]+/)?[\w.-]+)"\s*:\s*"([\^~><=]*\s*\d[^"]*)"')


def is_manifest(path: str) -> bool:
    """True for dependency manifest files we know how to read."""
    name = Path(path).name.lower()
    return name in MANIFEST_NAMES or name.startswith("requirements")


def parse_dependency_line(path: str, text: str) -> Dependency | None:
    """Extract a dependency from one added line of a manifest."""
    name = Path(path).name.lower()
    stripped = text.strip()
    if not stripped or stripped.startswith("#") or stripped.startswith("//"):
        return None
    if name.endswith(".json"):
        match = JSON_DEP_RE.match(stripped)
        if match:
            return Dependency(match.group(1), match.group(2).strip(), stripped)
        return None
    if name in ("pyproject.toml", "setup.py", "setup.cfg", "cargo.toml"):
        match = QUOTED_DEP_RE.match(stripped)
        if match:
            return Dependency(match.group(1), (match.group(2) or "").strip(), stripped)
        return None
    if name == "go.mod":
        match = re.match(r"^\s*([\w./-]+)\s+(v[\w.+-]+)", stripped)
        if match:
            return Dependency(match.group(1), match.group(2), stripped)
        return None
    if "requirements" in name:
        if stripped.startswith("-") or "://" in stripped:
            return None
        match = REQ_RE.match(stripped)
        if match:
            return Dependency(match.group(1), (match.group(2) or "").strip(), stripped)
    return None


def edit_distance(a: str, b: str, cap: int = 3) -> int:
    """Damerau-Levenshtein distance, capped for speed."""
    if a == b:
        return 0
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev2: list[int] = []
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if i > 1 and j > 1 and ca == b[j - 2] and a[i - 2] == cb:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        prev2, prev = prev, cur
    return min(prev[-1], cap + 1)


def typosquat_candidates(name: str) -> list[str]:
    """Popular packages the given name is one edit away from."""
    lowered = name.lower().replace("_", "-")
    out: list[str] = []
    for known in KNOWN_PACKAGES:
        if known == lowered:
            continue
        if lowered.replace("-", "") == known.replace("-", ""):
            out.append(known)
        elif len(known) >= 6 and edit_distance(lowered, known, cap=1) == 1:
            out.append(known)
    return sorted(out)


def rule_dependencies(ctx: ReviewContext) -> list[Finding]:
    """New dependencies: licence guess, pinning and typosquat distance."""
    found: list[Finding] = []
    for item in ctx.diff.files:
        if not is_manifest(item.path) or item.is_deleted:
            continue
        removed = {line.strip() for _, line in item.removed_lines()}
        for lineno, text in item.added_lines():
            if text.strip() in removed:
                continue
            dep = parse_dependency_line(item.path, text)
            if dep is None:
                continue
            base = dep.name.lower()
            licence = KNOWN_PACKAGES.get(base, "unknown")
            squats = typosquat_candidates(base)
            if squats:
                found.append(
                    Finding(
                        rule="typosquat",
                        severity=Severity.HIGH,
                        title="dependency looks like a typo of " + squats[0],
                        path=item.path,
                        line=lineno,
                        evidence=text.strip()[:140],
                        fix="Confirm the exact package name on the registry before merging; "
                        "typosquats ship install scripts that exfiltrate credentials.",
                        detail="Edit distance 1 from "
                        + ", ".join(squats[:3])
                        + "; the real package is licensed "
                        + KNOWN_PACKAGES.get(squats[0], "unknown")
                        + ".",
                    )
                )
            pinned = bool(re.search(r"(==|~=|===|>=|<=|@\d|\^\s*\d|~\s*\d)", dep.version or ""))
            if not pinned:
                found.append(
                    Finding(
                        rule="unpinned-dependency",
                        severity=Severity.MEDIUM,
                        title="dependency added without a version bound",
                        path=item.path,
                        line=lineno,
                        evidence=text.strip()[:140],
                        fix="Pin it (==x.y.z) or bound it (>=x.y,<x+1) so CI resolves the same "
                        "version you tested.",
                        detail="Version spec was empty for " + dep.name + ".",
                    )
                )
            if squats:
                continue
            if licence == "unknown":
                severity = Severity.MEDIUM
            elif "GPL" in licence.upper() and "LGPL" not in licence.upper():
                severity = Severity.HIGH
            else:
                severity = Severity.LOW
            found.append(
                Finding(
                    rule="new-dependency",
                    severity=severity,
                    title="new dependency: " + dep.name,
                    path=item.path,
                    line=lineno,
                    evidence=text.strip()[:140],
                    fix="Confirm the licence is compatible and the package is actively "
                    "maintained before adding it to the tree.",
                    detail="Licence guess: "
                    + licence
                    + ". Known package: "
                    + ("yes" if base in KNOWN_PACKAGES else "no"),
                )
            )
    return found


# --------------------------------------------------------------------------- #
# 5. secrets
# --------------------------------------------------------------------------- #


def mask(value: str) -> str:
    """Show enough of a secret to locate it, never enough to use it."""
    if len(value) <= 8:
        return value[:2] + "***"
    return value[:6] + "..." + value[-2:]


def rule_secrets(ctx: ReviewContext) -> list[Finding]:
    """Credentials committed in the added lines of the diff."""
    found: list[Finding] = []
    for item in ctx.diff.files:
        if item.is_deleted:
            continue
        for lineno, text in item.added_lines():
            stripped = text.strip()
            if not stripped:
                continue
            for name, pattern, label in SECRET_PATTERNS:
                match = pattern.search(text)
                if not match:
                    continue
                snippet = match.group(0)
                if PLACEHOLDER_RE.search(snippet):
                    continue
                found.append(
                    Finding(
                        rule="secret",
                        severity=Severity.CRITICAL,
                        title=label + " committed",
                        path=item.path,
                        line=lineno,
                        evidence=mask(snippet),
                        fix="Revoke and rotate the credential now, then load the replacement from "
                        "an environment variable or secret manager. Rewriting history is not "
                        "enough once it is pushed.",
                        detail="Matched detector " + name + ".",
                    )
                )
                break
    return found


# --------------------------------------------------------------------------- #
# 6. debug leftovers
# --------------------------------------------------------------------------- #


def rule_debug_leftovers(ctx: ReviewContext) -> list[Finding]:
    """Debug statements and commented-out code added to non-test files."""
    found: list[Finding] = []
    for item in ctx.diff.files:
        info = ctx.infos[item.path]
        if item.is_deleted or info.is_test or info.is_doc:
            continue
        for lineno, text in item.added_lines():
            matched = False
            for pattern, severity, label in DEBUG_PATTERNS:
                if pattern.search(text):
                    found.append(
                        Finding(
                            rule="debug-leftover",
                            severity=severity,
                            title=label + " left in the diff",
                            path=item.path,
                            line=lineno,
                            evidence=text.strip()[:140],
                            fix="Remove it, or replace it with a structured log call behind the "
                            "project's logging configuration.",
                            detail="Debug statements shipped to production leak data and slow "
                            "hot paths.",
                        )
                    )
                    matched = True
                    break
            if not matched and COMMENTED_CODE_RE.match(text):
                found.append(
                    Finding(
                        rule="commented-code",
                        severity=Severity.LOW,
                        title="commented-out code added",
                        path=item.path,
                        line=lineno,
                        evidence=text.strip()[:140],
                        fix="Delete it: git keeps history, and dead code rots silently.",
                        detail="Comments that contain code are a review smell.",
                    )
                )
    return found


# --------------------------------------------------------------------------- #
# 7. large additions and diff size
# --------------------------------------------------------------------------- #


def longest_added_run(item: FileDiff) -> tuple[int, int | None]:
    """Longest contiguous run of added lines, and where it starts."""
    best = 0
    best_line: int | None = None
    for hunk in item.hunks:
        run = 0
        start: int | None = None
        for kind, _text, lineno in hunk.iter_lines():
            if kind == "add":
                if run == 0:
                    start = lineno
                run += 1
                if run > best:
                    best = run
                    best_line = start
            else:
                run = 0
    return best, best_line


def rule_large_change(ctx: ReviewContext) -> list[Finding]:
    """Big single-file additions and oversized diffs."""
    found: list[Finding] = []
    for item in ctx.changed_source:
        run, line = longest_added_run(item)
        if run >= LARGE_ADDITION:
            found.append(
                Finding(
                    rule="large-addition",
                    severity=Severity.MEDIUM,
                    title="large block added in one file",
                    path=item.path,
                    line=line,
                    evidence=str(run) + " contiguous added lines",
                    fix="Split the work: nobody reviews a 100 line function by reading it once. "
                    "Extract helpers and name the seams.",
                    detail="Longest contiguous added run is " + str(run) + " lines.",
                )
            )
    total_lines = ctx.diff.added + ctx.diff.removed
    if len(ctx.diff.files) > BIG_FILE_COUNT:
        found.append(
            Finding(
                rule="wide-diff",
                severity=Severity.MEDIUM,
                title="diff touches " + str(len(ctx.diff.files)) + " files",
                path=None,
                evidence=str(len(ctx.diff.files)) + " files",
                fix="Split the PR by concern. Reviewers approve small diffs and rubber-stamp "
                "big ones.",
                detail="File count is above the " + str(BIG_FILE_COUNT) + " file budget.",
            )
        )
    if total_lines > HUGE_DIFF_LINES:
        found.append(
            Finding(
                rule="large-diff",
                severity=Severity.HIGH,
                title="diff is " + str(total_lines) + " changed lines",
                path=None,
                evidence="+" + str(ctx.diff.added) + " / -" + str(ctx.diff.removed),
                fix="Land the mechanical part first (rename, move, format), then the behaviour "
                "change in a second PR.",
                detail="Above the " + str(HUGE_DIFF_LINES) + " line hard limit.",
            )
        )
    elif total_lines > BIG_DIFF_LINES:
        found.append(
            Finding(
                rule="large-diff",
                severity=Severity.LOW,
                title="diff is " + str(total_lines) + " changed lines",
                path=None,
                evidence="+" + str(ctx.diff.added) + " / -" + str(ctx.diff.removed),
                fix="Consider splitting at "
                + str(BIG_DIFF_LINES)
                + " lines: review quality drops sharply past that.",
                detail="Softer budget than the hard limit.",
            )
        )
    return found


# --------------------------------------------------------------------------- #
# 8. unrelated changes
# --------------------------------------------------------------------------- #


def rule_unrelated_change(ctx: ReviewContext) -> list[Finding]:
    """Changes that span unrelated modules and are not explained by the PR text."""
    modules: dict[str, list[str]] = {}
    for item in ctx.diff.files:
        info = ctx.infos[item.path]
        if info.is_test or info.is_doc:
            continue
        modules.setdefault(top_module(item.path), []).append(item.path)
    if len(modules) < 4:
        return []
    pr_text = ((ctx.options.pr_title or "") + " " + (ctx.options.pr_body or "")).lower()
    unexplained: list[str] = []
    for module in modules:
        stem = module.split("/")[-1].lower()
        if pr_text.strip() and stem and stem in pr_text:
            continue
        unexplained.append(module)
    severity = Severity.MEDIUM if len(unexplained) >= 3 else Severity.LOW
    return [
        Finding(
            rule="unrelated-change",
            severity=severity,
            title="changes span " + str(len(modules)) + " unrelated modules",
            path=None,
            evidence=", ".join(sorted(unexplained)[:6]) or "(all explained by the PR text)",
            fix="Split the diff by module, or list every touched area in the PR description so "
            "the reviewer knows the blast radius.",
            detail="Modules touched: " + ", ".join(sorted(modules)) + ".",
        )
    ]


# --------------------------------------------------------------------------- #
# 9. PR hygiene
# --------------------------------------------------------------------------- #


def rule_hygiene(ctx: ReviewContext) -> list[Finding]:
    """Title, body and commit message quality."""
    found: list[Finding] = []
    options = ctx.options
    title = (options.pr_title or "").strip()
    body = (options.pr_body or "").strip()
    if options.pr_title is not None:
        if len(title) < 15:
            found.append(
                Finding(
                    rule="weak-pr-title",
                    severity=Severity.LOW,
                    title="PR title is too short to be useful",
                    evidence=title or "(empty)",
                    fix="State the behaviour change: 'Reject expired sessions on refresh' beats "
                    "'auth fix'.",
                    detail="Title has " + str(len(title)) + " characters; 15 is the floor.",
                )
            )
        if GENERIC_SUBJECTS.match(title):
            found.append(
                Finding(
                    rule="weak-pr-title",
                    severity=Severity.LOW,
                    title="PR title is a generic word",
                    evidence=title,
                    fix="Name the subsystem and the outcome in the title.",
                    detail="Titles like 'fix' or 'update' cost the reviewer a round trip.",
                )
            )
    if options.pr_body is not None and len(body) < 40:
        found.append(
            Finding(
                rule="empty-pr-body",
                severity=Severity.LOW,
                title="PR description says nothing",
                evidence=body[:60] or "(empty)",
                fix="Three lines are enough: what changed, why now, how you tested it.",
                detail="Body has " + str(len(body)) + " characters.",
            )
        )
    commits = ctx.pr.commits if ctx.pr else ()
    poor = [
        commit.subject.strip()
        for commit in commits
        if len(commit.subject.strip()) < 12 or GENERIC_SUBJECTS.match(commit.subject.strip())
    ]
    if commits and poor and (len(poor) >= 2 or len(poor) == len(commits)):
        found.append(
            Finding(
                rule="weak-commit-messages",
                severity=Severity.LOW,
                title=str(len(poor)) + " of " + str(len(commits)) + " commits have weak subjects",
                evidence="; ".join(poor[:3]),
                fix="Write imperative subjects of at least 12 characters that describe the "
                "change.",
                detail="Commit history is part of the review artefact.",
            )
        )
    if not commits and ctx.pr is not None:
        ctx.notes.append("no commit messages available: reviewed a patch, not a branch")
    return found


ALL_RULES = (
    rule_risk,
    rule_missing_tests,
    rule_deleted_assertions,
    rule_migration_hazards,
    rule_dependencies,
    rule_secrets,
    rule_debug_leftovers,
    rule_large_change,
    rule_unrelated_change,
    rule_hygiene,
)
