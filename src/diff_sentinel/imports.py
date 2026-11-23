"""Cheap static import graph for Python and JavaScript/TypeScript.

The impact map only has to answer one question: if this module changes, which
other modules in the repository can break? We parse imports, resolve them to
repository-relative modules, and keep a reverse adjacency list. Python is parsed
with ast (exact); JavaScript uses a regex scanner for the three import syntaxes
that matter: static import, export-from, and require. Resolution is best effort
and never raises on a file it cannot understand.
"""

from __future__ import annotations

import ast
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

SKIP_DIRS = {
    ".git",
    "node_modules",
    "vendor",
    "dist",
    "build",
    "__pycache__",
    ".venv",
    "venv",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "coverage",
    ".next",
    "target",
}

PY_SUFFIXES = (".py",)
JS_SUFFIXES = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")
SOURCE_SUFFIXES = PY_SUFFIXES + JS_SUFFIXES

JS_IMPORT_RE = re.compile(
    r"""(?:^|\n)\s*(?:import|export)\s[^;\n]*?from\s*['"]([^'"]+)['"]"""
    r"""|(?:^|\n)\s*import\s*['"]([^'"]+)['"]"""
    r"""|require\(\s*['"]([^'"]+)['"]\s*\)"""
)
JS_EXTENSIONS = ("", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
MAX_GRAPH_FILES = 20000
MAX_PARSE_BYTES = 2000000


@dataclass
class ImportGraph:
    """Reverse adjacency: module -> modules that import it."""

    modules: dict[str, str] = field(default_factory=dict)
    imports: dict[str, set[str]] = field(default_factory=dict)
    reverse: dict[str, set[str]] = field(default_factory=dict)
    skip: tuple[str, ...] = ()
    stats: dict[str, int] = field(default_factory=dict)

    def add_edge(self, importer: str, imported: str) -> None:
        """Record "importer imports imported"."""
        if importer == imported:
            return
        self.imports.setdefault(importer, set()).add(imported)
        self.reverse.setdefault(imported, set()).add(importer)

    def direct_dependents(self, module: str) -> set[str]:
        """Modules that import module directly."""
        return set(self.reverse.get(module, set()))

    def transitive_dependents(self, module: str, max_depth: int = 8) -> tuple[list[str], int]:
        """Breadth-first walk over reverse edges. Returns (ordered modules, max depth)."""
        seen: dict[str, int] = {}
        frontier = [module]
        depth = 0
        while frontier and depth < max_depth:
            depth += 1
            nxt: list[str] = []
            for node in frontier:
                for parent in self.reverse.get(node, ()):
                    if parent in seen or parent == module:
                        continue
                    seen[parent] = depth
                    nxt.append(parent)
            frontier = nxt
        ordered = sorted(seen.items(), key=lambda item: (item[1], item[0]))
        return [name for name, _ in ordered], (max(seen.values()) if seen else 0)

    def as_dict(self) -> dict[str, list[str]]:
        """Sorted adjacency, useful in JSON output and tests."""
        return {key: sorted(value) for key, value in sorted(self.imports.items())}


def module_key(rel_path: str) -> str:
    """Turn src/app/auth/session.py into src/app/auth/session."""
    rel_path = rel_path.replace("\\", "/")
    for suffix in SOURCE_SUFFIXES:
        if rel_path.endswith(suffix):
            rel_path = rel_path[: -len(suffix)]
            break
    if rel_path.endswith("/__init__"):
        rel_path = rel_path[: -len("/__init__")]
    if rel_path.endswith("/index"):
        rel_path = rel_path[: -len("/index")]
    return rel_path


def python_candidates(spec: str, level: int, importer: str) -> list[str]:
    """Repository-relative module keys a Python import may refer to."""
    parts = [p for p in spec.split(".") if p]
    roots: list[str] = []
    if level:
        importer_dir = importer.split("/")[:-1]
        back = level - 1
        if back > len(importer_dir):
            return []
        base = importer_dir[: len(importer_dir) - back] if back else importer_dir
        roots.append("/".join(base + parts))
    else:
        roots.append("/".join(parts))
        if parts:
            roots.append("src/" + "/".join(parts))
    return roots


def resolve(candidates: list[str], by_module: dict[str, str]) -> str | None:
    """Find the first candidate module key that exists in the repository.

    by_module maps a module key (a path without its extension) to the file that
    defines it, so a resolved value is always a real repository file path.
    """
    for root in candidates:
        root = root.strip("/")
        if not root:
            continue
        if root in by_module:
            return by_module[root]
        for suffix in ("", "/index"):
            key = root + suffix
            if key in by_module:
                return by_module[key]
    return None


def build_import_graph(root: str | Path, skip: tuple[str, ...] = ()) -> ImportGraph:
    """Scan a repository tree and build the import graph.

    skip holds repository-relative path prefixes to ignore.
    """
    root_path = Path(root)
    graph = ImportGraph(skip=tuple(skip))
    files: dict[str, str] = {}
    skipped_large = 0
    for dirpath, dirnames, filenames in os.walk(root_path):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        rel_dir = Path(dirpath).relative_to(root_path).as_posix()
        rel_dir = "" if rel_dir == "." else rel_dir + "/"
        for name in sorted(filenames):
            if not name.endswith(SOURCE_SUFFIXES):
                continue
            rel = (rel_dir + name).replace("\\", "/")
            if any(rel.startswith(prefix) for prefix in skip):
                continue
            abs_name = Path(dirpath) / name
            try:
                size = abs_name.stat().st_size
            except OSError:
                continue
            if size > MAX_PARSE_BYTES:
                skipped_large += 1
                continue
            files[rel] = str(abs_name)
            if len(files) >= MAX_GRAPH_FILES:
                break
        if len(files) >= MAX_GRAPH_FILES:
            break

    modules = {rel: module_key(rel) for rel in files}
    by_module: dict[str, str] = {}
    for rel in sorted(modules):
        by_module.setdefault(modules[rel], rel)

    for rel in sorted(files):
        key = modules[rel]
        graph.modules[rel] = key
        if rel.endswith(PY_SUFFIXES):
            for spec, level in read_python_imports(files[rel]):
                target = resolve(python_candidates(spec, level, key), by_module)
                if target:
                    graph.add_edge(key, modules[target])
        else:
            for spec in read_js_imports(files[rel]):
                if spec.startswith("."):
                    target = resolve([join_relative(key, spec)], by_module)
                    if target:
                        graph.add_edge(key, modules[target])
    graph.stats = {
        "files": len(files),
        "skipped_large": skipped_large,
        "edges": sum(len(v) for v in graph.imports.values()),
    }
    return graph


def join_relative(importer_key: str, spec: str) -> str:
    """Resolve a relative JS import against the importing module."""
    base = importer_key.split("/")[:-1]
    for piece in spec.split("/"):
        if piece in ("", "."):
            continue
        if piece == "..":
            if base:
                base.pop()
        else:
            base.append(piece)
    return "/".join(base)


def read_python_imports(abs_path: str) -> list[tuple[str, int]]:
    """(module, relative-level) pairs for every import in a Python file."""
    try:
        source = Path(abs_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    out: list[tuple[str, int]] = []
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        for line in source.splitlines():
            match = re.match(r"\s*from\s+([\w.]+)\s+import\s+", line)
            if match:
                out.append((match.group(1), 0))
            match = re.match(r"\s*import\s+([\w.]+)", line)
            if match:
                out.append((match.group(1), 0))
        return out
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.append((alias.name, 0))
        elif isinstance(node, ast.ImportFrom):
            if node.level or node.module:
                out.append((node.module or "", node.level))
    return out


def read_js_imports(abs_path: str) -> list[str]:
    """Import specifiers (the string literal, not the binding) of a JS/TS file."""
    try:
        source = Path(abs_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    out: list[str] = []
    for match in JS_IMPORT_RE.finditer(source):
        spec = match.group(1) or match.group(2) or match.group(3)
        if spec:
            out.append(spec)
    return out
