"""Unified diff parsing.

diff-sentinel reads the same unified format that git, GNU diff, and GitHub
produce. The parser is deliberately forgiving: it accepts mail-style patch
headers, quoted paths, multi-file patches, binary markers, rename and mode
changes and CRLF input. A malformed hunk raises DiffParseError carrying the
patch line number and the offending text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@ ?(.*)$")
BINARY_RE = re.compile(r"^Binary files (.+) and (.+) differ$")
DEVNULL = "/dev/null"

MAX_FILE_BYTES = 2_000_000
MAX_LINE_CHARS = 4000
MAX_FILES = 20_000


class DiffParseError(ValueError):
    """Raised when a patch cannot be parsed."""

    def __init__(self, message: str, line_no: int | None = None, line: str = "") -> None:
        self.line_no = line_no
        self.line = line
        suffix = "" if line_no is None else " (patch line " + str(line_no) + ")"
        snippet = "" if not line else "\n  " + line[:200]
        super().__init__(message + suffix + snippet)


@dataclass(frozen=True)
class Hunk:
    """One @@ block of a file diff."""

    old_start: int
    old_len: int
    new_start: int
    new_len: int
    section: str
    header: str
    raw: list[str]

    def iter_lines(self) -> list[tuple[str, str, int | None]]:
        """Return (kind, text, new_lineno) for every body line of the hunk."""
        out: list[tuple[str, str, int | None]] = []
        new_no = self.new_start
        for line in self.raw:
            if line == "":
                out.append(("context", "", new_no))
                new_no += 1
                continue
            marker, text = line[0], line[1:]
            if marker == "+":
                out.append(("add", text, new_no))
                new_no += 1
            elif marker == "-":
                out.append(("del", text, None))
            elif marker == "\\":
                continue
            else:
                out.append(("context", text, new_no))
                new_no += 1
        return out

    def added_text(self) -> str:
        """Joined text of every added line in this hunk."""
        return "\n".join(text for kind, text, _ in self.iter_lines() if kind == "add")

    def removed_text(self) -> str:
        """Joined text of every removed line in this hunk."""
        return "\n".join(text for kind, text, _ in self.iter_lines() if kind == "del")


@dataclass
class FileDiff:
    """The change to a single file."""

    old_path: str | None
    new_path: str | None
    hunks: list[Hunk] = field(default_factory=list)
    status: str = "modified"
    added: int = 0
    removed: int = 0
    binary: bool = False
    old_mode: str | None = None
    new_mode: str | None = None
    is_new: bool = False
    is_deleted: bool = False
    truncated: bool = False

    @property
    def path(self) -> str:
        """The most useful path: the new one, falling back to the old one."""
        return self.new_path or self.old_path or ""

    def added_lines(self) -> list[tuple[int, str]]:
        """(line-number, text) for every added line, numbered in the new file."""
        out: list[tuple[int, str]] = []
        for hunk in self.hunks:
            for kind, text, lineno in hunk.iter_lines():
                if kind == "add" and lineno is not None:
                    out.append((lineno, text))
        return out

    def removed_lines(self) -> list[tuple[int, str]]:
        """(line-number, text) for every removed line, numbered in the old file."""
        out: list[tuple[int, str]] = []
        for hunk in self.hunks:
            old_no = hunk.old_start
            for kind, text, _ in hunk.iter_lines():
                if kind == "del":
                    out.append((old_no, text))
                    old_no += 1
                elif kind == "context":
                    old_no += 1
        return out

    def added_text(self) -> str:
        """All added lines joined with newlines."""
        return "\n".join(text for _, text in self.added_lines())

    def removed_text(self) -> str:
        """All removed lines joined with newlines."""
        return "\n".join(text for _, text in self.removed_lines())

    def raw_lines(self) -> list[tuple[str, int | None, str]]:
        """Every add/remove body line as (kind, new_line, text)."""
        out: list[tuple[str, int | None, str]] = []
        for hunk in self.hunks:
            for kind, text, lineno in hunk.iter_lines():
                if kind == "add":
                    out.append(("add", lineno, text))
                elif kind == "del":
                    out.append(("del", None, text))
        return out


@dataclass
class Diff:
    """A parsed multi-file patch."""

    files: list[FileDiff] = field(default_factory=list)
    preamble: str = ""

    @property
    def added(self) -> int:
        return sum(f.added for f in self.files)

    @property
    def removed(self) -> int:
        return sum(f.removed for f in self.files)

    @property
    def paths(self) -> list[str]:
        return [f.path for f in self.files]

    @property
    def is_empty(self) -> bool:
        return not self.files

    def file(self, path: str) -> FileDiff | None:
        for item in self.files:
            if item.path == path:
                return item
        return None


def clean_path(value: str) -> str | None:
    """Normalise a path taken from a ---/+++/diff header."""
    value = value.strip()
    if not value or value == DEVNULL:
        return None
    if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
        value = _unquote(value)
    if "\t" in value:
        value = value.split("\t", 1)[0]
    value = re.sub(r"^[ab]/", "", value)
    if value.startswith("./"):
        value = value[2:]
    return value


def _clip(line: str) -> str:
    return line if len(line) <= MAX_LINE_CHARS else line[:MAX_LINE_CHARS]


def parse_patch(text: str) -> Diff:
    """Parse a unified diff into a Diff. Raises DiffParseError on bad input."""
    if text is None:
        raise DiffParseError("empty patch: nothing to review")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip():
        raise DiffParseError("empty patch: nothing to review")

    lines = text.split("\n")
    diff = Diff()
    current: FileDiff | None = None
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]
        lineno = i + 1

        if line.startswith("diff --git "):
            if current is not None:
                diff.files.append(current)
            old_p, new_p = _split_git_header(line[len("diff --git ") :])
            current = FileDiff(old_path=clean_path(old_p), new_path=clean_path(new_p))
            i += 1
            continue

        if line.startswith("--- ") and i + 1 < n and lines[i + 1].startswith("+++ "):
            old_p = clean_path(line[4:])
            new_p = clean_path(lines[i + 1][4:])
            if current is None:
                current = FileDiff(old_path=old_p, new_path=new_p)
            else:
                current.old_path = old_p
                current.new_path = new_p
            i += 2
            continue

        if line.startswith("@@ "):
            if current is None:
                raise DiffParseError(
                    "hunk found before any file header: the patch must start with a "
                    "diff --git line or a ---/+++ pair",
                    lineno,
                    line,
                )
            match = HUNK_RE.match(line)
            if not match:
                raise DiffParseError("malformed hunk header", lineno, line)
            old_start = int(match.group(1))
            old_len = int(match.group(2)) if match.group(2) is not None else 1
            new_start = int(match.group(3))
            new_len = int(match.group(4)) if match.group(4) is not None else 1
            body: list[str] = []
            i += 1
            old_seen = 0
            new_seen = 0
            while i < n and (old_seen < old_len or new_seen < new_len):
                body_line = lines[i]
                if body_line.startswith("diff --git ") or body_line.startswith("@@ "):
                    break
                body.append(_clip(body_line))
                if body_line.startswith("+"):
                    new_seen += 1
                elif body_line.startswith("-"):
                    old_seen += 1
                elif body_line.startswith("\\"):
                    pass
                else:
                    old_seen += 1
                    new_seen += 1
                i += 1
            current.hunks.append(
                Hunk(
                    old_start=old_start,
                    old_len=old_len,
                    new_start=new_start,
                    new_len=new_len,
                    section=match.group(5).strip(),
                    header=line,
                    raw=body,
                )
            )
            continue

        if current is not None:
            current = _apply_meta(current, line)
        i += 1

    if current is not None:
        diff.files.append(current)

    for item in diff.files:
        for hunk in item.hunks:
            for kind, _, _ in hunk.iter_lines():
                if kind == "add":
                    item.added += 1
                elif kind == "del":
                    item.removed += 1
        if item.added > MAX_FILE_BYTES or item.removed > MAX_FILE_BYTES:
            item.truncated = True

    if not diff.files:
        raise DiffParseError(
            "no file changes found in patch: expected diff --git or ---/+++ headers"
        )
    if len(diff.files) > MAX_FILES:
        raise DiffParseError(
            "patch has "
            + str(len(diff.files))
            + " files, refusing to analyse more than "
            + str(MAX_FILES)
            + "; split the review"
        )
    return diff


def _apply_meta(item: FileDiff, line: str) -> FileDiff:
    """Apply one metadata header line to the current file."""
    if line.startswith("new file mode "):
        item.is_new = True
        item.status = "added"
        item.new_mode = line.split()[-1]
    elif line.startswith("deleted file mode "):
        item.is_deleted = True
        item.status = "deleted"
        item.old_mode = line.split()[-1]
    elif line.startswith("old mode "):
        item.old_mode = line.split()[-1]
        item.status = "mode"
    elif line.startswith("new mode "):
        item.new_mode = line.split()[-1]
        item.status = "mode"
    elif line.startswith("rename from "):
        item.old_path = line[len("rename from ") :].strip()
        item.status = "renamed"
    elif line.startswith("rename to "):
        item.new_path = line[len("rename to ") :].strip()
        item.status = "renamed"
    elif line.startswith("copy from "):
        item.old_path = line[len("copy from ") :].strip()
        item.status = "copied"
    elif line.startswith("copy to "):
        item.new_path = line[len("copy to ") :].strip()
        item.status = "copied"
    elif BINARY_RE.match(line):
        item.binary = True
        item.status = "binary"
    return item


def _split_git_header(rest: str) -> tuple[str, str]:
    """Split 'a/x b/x'; paths may be quoted or contain spaces."""
    rest = rest.strip()
    if rest.startswith('"'):
        end = _closing_quote(rest)
        if end != -1:
            return _unquote(rest[: end + 1]), rest[end + 1 :].strip()
    marker = rest.find(" b/")
    if marker != -1:
        return rest[:marker], rest[marker + 1 :]
    parts = rest.split(" ")
    if len(parts) == 2:
        return parts[0], parts[1]
    return rest, rest


def _closing_quote(text: str) -> int:
    escaped = False
    for index in range(1, len(text)):
        ch = text[index]
        if escaped:
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == '"':
            return index
    return -1


def _unquote(value: str) -> str:
    inner = value[1:-1]
    try:
        return inner.encode("utf-8").decode("unicode_escape")
    except (UnicodeDecodeError, UnicodeEncodeError):
        return inner


def looks_like_patch(text: str) -> bool:
    """Cheap sniff used to produce a better error message."""
    if not text:
        return False
    head = text[:4096]
    return "diff --git " in head or head.lstrip().startswith("--- ") or "@@ -" in head
