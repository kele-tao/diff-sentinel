"""Import graph and impact map tests."""

from __future__ import annotations

from pathlib import Path

from conftest import build_patch

from diff_sentinel.diffparse import parse_patch
from diff_sentinel.imports import (
    build_import_graph,
    join_relative,
    module_key,
    python_candidates,
    read_js_imports,
    read_python_imports,
)
from diff_sentinel.review import compute_impact


def write(root: Path, rel: str, text: str) -> None:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def test_module_key_strips_suffixes():
    assert module_key("src/app/auth/session.py") == "src/app/auth/session"
    assert module_key("src/app/__init__.py") == "src/app"
    assert module_key("web/lib/index.ts") == "web/lib"


def test_python_candidates_prefers_absolute_then_src():
    assert python_candidates("app.core", 0, "app/x") == ["app/core", "src/app/core"]


def test_python_relative_imports_resolve_upwards():
    assert python_candidates("core", 2, "app/auth/session") == ["app/core"]


def test_join_relative_handles_parent_segments():
    assert join_relative("src/web/page", "../lib/util") == "src/lib/util"
    assert join_relative("src/web/page", "./inline") == "src/web/inline"


def test_reads_python_imports(tmp_path):
    write(tmp_path, "a.py", "import os\nfrom b import c\nfrom . import d\n")
    imports = read_python_imports(str(tmp_path / "a.py"))
    assert ("os", 0) in imports
    assert ("b", 0) in imports
    assert ("", 1) in imports


def test_reads_python_imports_without_ast(tmp_path):
    write(tmp_path, "broken.py", "def (:\nimport os\n")
    imports = read_python_imports(str(tmp_path / "broken.py"))
    assert ("os", 0) in imports


def test_reads_js_imports(tmp_path):
    write(
        tmp_path,
        "a.js",
        "import x from './x';\n"
        "export { y } from '../y';\n"
        "const z = require('./z');\n"
        "import './side-effect';\n",
    )
    specs = read_js_imports(str(tmp_path / "a.js"))
    assert "./x" in specs
    assert "../y" in specs
    assert "./z" in specs
    assert "./side-effect" in specs


def test_builds_reverse_graph_for_python(tmp_path):
    write(tmp_path, "pkg/__init__.py", "")
    write(tmp_path, "pkg/core.py", "VALUE = 1\n")
    write(tmp_path, "pkg/service.py", "from pkg.core import VALUE\n")
    write(tmp_path, "pkg/api.py", "from pkg.service import run\n")
    graph = build_import_graph(tmp_path)
    dependents, depth = graph.transitive_dependents("pkg/core")
    assert dependents == ["pkg/service", "pkg/api"]
    assert depth == 2


def test_builds_reverse_graph_for_javascript(tmp_path):
    write(tmp_path, "web/lib/util.ts", "export const x = 1;\n")
    write(tmp_path, "web/lib/page.ts", "import { x } from './util';\n")
    write(tmp_path, "web/app.ts", "import { page } from './lib/page';\n")
    graph = build_import_graph(tmp_path)
    dependents, _ = graph.transitive_dependents("web/lib/util")
    assert dependents == ["web/lib/page", "web/app"]


def test_skip_directories_are_ignored(tmp_path):
    write(tmp_path, "node_modules/pkg/index.js", "export const x = 1;\n")
    write(tmp_path, "app.js", "import x from './node_modules/pkg';\n")
    graph = build_import_graph(tmp_path)
    assert "node_modules/pkg/index" not in graph.modules


def test_skip_prefix_option(tmp_path):
    write(tmp_path, "bench/fixture.py", "from core import x\n")
    write(tmp_path, "core.py", "x = 1\n")
    graph = build_import_graph(tmp_path, skip=("bench/",))
    assert graph.modules == {"core.py": "core"}


def test_graph_direct_and_transitive_dependents(tmp_path):
    write(tmp_path, "core.py", "x = 1\n")
    write(tmp_path, "a.py", "from core import x\n")
    write(tmp_path, "b.py", "from a import x\n")
    graph = build_import_graph(tmp_path)
    assert graph.direct_dependents("core") == {"a"}
    dependents, depth = graph.transitive_dependents("core")
    assert "b" in dependents
    assert depth == 2


def test_graph_stats_are_reported(tmp_path):
    write(tmp_path, "a.py", "from b import c\n")
    write(tmp_path, "b.py", "c = 1\n")
    graph = build_import_graph(tmp_path)
    assert graph.stats["files"] == 2
    assert graph.stats["edges"] == 1


def test_impact_map_lists_transitive_dependents(tmp_path):
    write(tmp_path, "pkg/__init__.py", "")
    write(tmp_path, "pkg/core.py", "VALUE = 1\n")
    write(tmp_path, "pkg/service.py", "from pkg.core import VALUE\n")
    write(tmp_path, "pkg/api.py", "from pkg.service import run\n")
    patch = (
        "diff --git a/pkg/core.py b/pkg/core.py\n"
        "--- a/pkg/core.py\n"
        "+++ b/pkg/core.py\n"
        "@@ -1 +1,2 @@\n"
        " VALUE = 1\n"
        "+OTHER = 2\n"
    )
    diff = parse_patch(patch)
    entries = compute_impact(diff, tmp_path)
    assert entries
    assert entries[0].path == "pkg/core.py"
    assert "pkg/api" in entries[0].dependents
    assert entries[0].max_depth == 2


def test_impact_map_skips_tests_and_docs(tmp_path):
    diff = parse_patch(build_patch({"tests/test_x.py": "x = 1\n", "docs/a.md": "hi\n"}))
    assert compute_impact(diff, tmp_path) == []
