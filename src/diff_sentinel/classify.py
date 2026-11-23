"""Path classification: what area of the product a changed file belongs to.

Every rule needs the same three answers about a path: is it a test, what
language is it, and which risk area does it sit in. Keeping that logic in one
table means the risk report and the scoring can never disagree.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: (area, pattern) - first match wins, order matters.
AREA_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "payments",
        re.compile(
            r"(^|/)(pay|payment|payments|billing|invoice|invoices|checkout|stripe|pricing|"
            r"refund|refunds|subscription|subscriptions)(/|\.|$|_)",
            re.I,
        ),
    ),
    (
        "auth",
        re.compile(
            r"(^|/)(auth|authn|authz|login|logout|session|sessions|oauth|saml|sso|"
            r"permissions?|acl|password|passwords|tokens?|secrets?|crypto|security)"
            r"(/|\.|$|_)",
            re.I,
        ),
    ),
    (
        "migrations",
        re.compile(r"(^|/)(migrations?|alembic|migrate|schema|ddl|db/migrate)(/|\.|$)", re.I),
    ),
    ("data", re.compile(r"\.sql$", re.I)),
    (
        "infra",
        re.compile(
            r"(^|/)(dockerfile|docker-compose[^/]*\.ya?ml|terraform|k8s|kubernetes|helm|"
            r"charts?|deploy|deployment|ansible|\.github/workflows|ci)(/|\.|$)",
            re.I,
        ),
    ),
    (
        "config",
        re.compile(
            r"(^|/)([\w.-]*\.(env|ini|toml|cfg|conf|properties|ya?ml|json))$|"
            r"(^|/)(config|settings|conf)(/|\.|$|_)",
            re.I,
        ),
    ),
    (
        "dependencies",
        re.compile(
            r"(^|/)(requirements[^/]*\.txt|pyproject\.toml|setup\.py|setup\.cfg|package\.json|"
            r"package-lock\.json|yarn\.lock|pnpm-lock\.yaml|go\.mod|go\.sum|Cargo\.toml|"
            r"Cargo\.lock|Gemfile|Gemfile\.lock|pom\.xml|build\.gradle|composer\.json)$",
            re.I,
        ),
    ),
    ("docs", re.compile(r"(^|/)(docs?|documentation)(/)|\.(md|mdx|rst|adoc)$", re.I)),
    ("ci", re.compile(r"(^|/)\.github(/|$)", re.I)),
    (
        "tests",
        re.compile(
            r"(^|/)(tests?|specs?|__tests__|testdata|fixtures)(/)|"
            r"(^|/)test_[^/]*\.py$|_test\.(py|go)$|\.(test|spec)\.[cm]?[jt]sx?$",
            re.I,
        ),
    ),
)

#: Risk label per area, used by the risk-area rule.
AREA_RISK: dict[str, str] = {
    "payments": "critical",
    "auth": "critical",
    "migrations": "high",
    "data": "high",
    "infra": "high",
    "dependencies": "medium",
    "config": "medium",
    "ci": "low",
    "docs": "none",
    "tests": "none",
    "source": "medium",
}

LANGUAGE_BY_SUFFIX: tuple[tuple[str, str], ...] = (
    (".py", "python"),
    (".pyi", "python"),
    (".js", "javascript"),
    (".jsx", "javascript"),
    (".mjs", "javascript"),
    (".cjs", "javascript"),
    (".ts", "typescript"),
    (".tsx", "typescript"),
    (".go", "go"),
    (".rs", "rust"),
    (".java", "java"),
    (".kt", "kotlin"),
    (".rb", "ruby"),
    (".php", "php"),
    (".cs", "csharp"),
    (".c", "c"),
    (".h", "c"),
    (".cpp", "cpp"),
    (".hpp", "cpp"),
    (".sql", "sql"),
    (".sh", "shell"),
    (".ps1", "powershell"),
    (".yml", "yaml"),
    (".yaml", "yaml"),
    (".toml", "toml"),
    (".json", "json"),
    (".md", "markdown"),
    (".html", "html"),
    (".css", "css"),
    (".scss", "scss"),
    (".tf", "terraform"),
    (".proto", "protobuf"),
)

SOURCE_LANGUAGES = frozenset(
    {
        "python",
        "javascript",
        "typescript",
        "go",
        "rust",
        "java",
        "kotlin",
        "ruby",
        "php",
        "csharp",
        "c",
        "cpp",
    }
)

TEST_PATH_RE = re.compile(
    r"(^|/)(tests?|specs?|__tests__)(/)|(^|/)test_[^/]*\.py$|_test\.(py|go)$|"
    r"\.(test|spec)\.[cm]?[jt]sx?$|(^|/)conftest\.py$",
    re.I,
)

VENDORED_RE = re.compile(
    r"(^|/)(vendor|third_party|third-party|node_modules|generated|gen|dist|build)(/|\.|$)|"
    r"\.min\.(js|css)$",
    re.I,
)


@dataclass(frozen=True)
class PathInfo:
    """The classification of one changed path."""

    path: str
    area: str
    risk: str
    language: str
    is_test: bool
    is_doc: bool
    is_vendored: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "area": self.area,
            "risk": self.risk,
            "language": self.language,
            "is_test": self.is_test,
            "is_doc": self.is_doc,
        }


def classify(path: str) -> PathInfo:
    """Classify a repository-relative path."""
    normalised = path.replace("\\", "/")
    while normalised.startswith("./"):
        normalised = normalised[2:]
    normalised = normalised.lstrip("/")
    is_test = bool(TEST_PATH_RE.search(normalised))
    language = "other"
    lowered = normalised.lower()
    for suffix, name in LANGUAGE_BY_SUFFIX:
        if lowered.endswith(suffix):
            language = name
            break
    area = "source"
    for name, pattern in AREA_PATTERNS:
        if pattern.search(normalised):
            area = name
            break
    if is_test:
        area = "tests"
    risk = AREA_RISK.get(area, "medium")
    return PathInfo(
        path=normalised,
        area=area,
        risk=risk,
        language=language,
        is_test=is_test,
        is_doc=area == "docs",
        is_vendored=bool(VENDORED_RE.search(normalised)),
    )


def top_module(path: str, depth: int = 2) -> str:
    """Coarse module bucket used by unrelated-change detection.

    A file at the repository root belongs to "(root)"; everything else belongs to
    the directory that contains it, capped at depth path segments.
    """
    parts = [p for p in path.replace("\\", "/").split("/") if p]
    if len(parts) <= 1:
        return "(root)"
    return "/".join(parts[:-1][:depth]) or "(root)"


def is_source_like(info: PathInfo) -> bool:
    """True for files where behaviour lives: code, SQL, config and infra."""
    return info.language in SOURCE_LANGUAGES or info.area in {
        "migrations",
        "data",
        "infra",
        "config",
    }
