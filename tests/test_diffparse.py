"""Unified diff parser tests."""

from __future__ import annotations

import pytest
from conftest import build_patch

from diff_sentinel.diffparse import DiffParseError, clean_path, looks_like_patch, parse_patch


def test_parses_the_committed_example(example_patch):
    diff = parse_patch(example_patch)
    assert len(diff.files) == 7
    assert diff.added > 0
    assert "app/auth/session.py" in diff.paths


def test_hunk_line_numbers_are_absolute(example_patch):
    diff = parse_patch(example_patch)
    session = diff.file("app/auth/session.py")
    assert session is not None
    lines = session.added_lines()
    assert lines[0][0] == 5
    assert "API_TOKEN" in [text for _, text in lines if "API_TOKEN" in text][0]


def test_added_and_removed_counts(example_patch):
    diff = parse_patch(example_patch)
    for item in diff.files:
        assert item.added == len(item.added_lines())
        assert item.removed == len(item.removed_lines())


def test_new_file_is_marked_added():
    patch = build_patch({"src/new.py": "print('hi')\n"})
    diff = parse_patch(patch)
    assert diff.files[0].is_new is True
    assert diff.files[0].status == "added"
    assert diff.files[0].old_path is None


def test_empty_patch_is_rejected():
    with pytest.raises(DiffParseError, match="empty patch"):
        parse_patch("   \n\n")


def test_garbage_patch_is_rejected():
    with pytest.raises(DiffParseError, match="no file changes"):
        parse_patch("this is not a patch at all\n")


def test_hunk_before_header_is_rejected():
    with pytest.raises(DiffParseError, match="hunk found before any file header"):
        parse_patch("@@ -1 +1 @@\n-a\n+b\n")


def test_malformed_hunk_header_reports_the_line():
    patch = "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ broken @@\n-a\n+b\n"
    with pytest.raises(DiffParseError) as info:
        parse_patch(patch)
    assert info.value.line_no == 4


def test_crlf_input_is_normalised():
    patch = build_patch({"a.py": "x = 1\n"}).replace("\n", "\r\n")
    diff = parse_patch(patch)
    assert diff.files[0].added_lines()[0][1] == "x = 1"


def test_unicode_content_survives():
    patch = build_patch({"docs/readme.md": "héllo → 世界 🚀\n"})
    diff = parse_patch(patch)
    assert "世界" in diff.files[0].added_lines()[0][1]
    assert "🚀" in diff.files[0].added_text()


def test_clean_path_strips_prefixes():
    assert clean_path("b/src/app.py") == "src/app.py"
    assert clean_path("/dev/null") is None
    assert clean_path("./x/y.py") == "x/y.py"


def test_binary_marker_is_parsed():
    patch = (
        "diff --git a/logo.png b/logo.png\n"
        "Binary files a/logo.png and b/logo.png differ\n"
    )
    diff = parse_patch(patch)
    assert diff.files[0].binary is True


def test_rename_metadata_sets_status():
    patch = (
        "diff --git a/old.py b/new.py\n"
        "similarity index 100%\n"
        "rename from old.py\n"
        "rename to new.py\n"
    )
    diff = parse_patch(patch)
    assert diff.files[0].status == "renamed"
    assert diff.files[0].path == "new.py"


def test_deleted_file_is_marked():
    patch = (
        "diff --git a/gone.py b/gone.py\n"
        "deleted file mode 100644\n"
        "--- a/gone.py\n"
        "+++ /dev/null\n"
        "@@ -1,2 +0,0 @@\n"
        "-one\n"
        "-two\n"
    )
    diff = parse_patch(patch)
    assert diff.files[0].is_deleted is True
    assert diff.files[0].removed_lines()[0][0] == 1


def test_quoted_paths_are_unquoted():
    patch = (
        'diff --git "a/sp ace.py" "b/sp ace.py"\n'
        "--- \"a/sp ace.py\"\n"
        "+++ \"b/sp ace.py\"\n"
        "@@ -1 +1 @@\n"
        "-a\n"
        "+b\n"
    )
    diff = parse_patch(patch)
    assert diff.files[0].path == "sp ace.py"


def test_no_newline_marker_is_ignored():
    patch = (
        "diff --git a/x.py b/x.py\n"
        "--- a/x.py\n"
        "+++ b/x.py\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
        "\\ No newline at end of file\n"
    )
    diff = parse_patch(patch)
    assert diff.files[0].added_text() == "new"


def test_looks_like_patch_sniffing():
    assert looks_like_patch("diff --git a/x b/x\n") is True
    assert looks_like_patch("--- a/x\n") is True
    assert looks_like_patch("hello") is False
    assert looks_like_patch("") is False


def test_many_files_can_be_parsed():
    files = {"mod/file_" + str(index) + ".py": "x = " + str(index) + "\n" for index in range(60)}
    diff = parse_patch(build_patch(files))
    assert len(diff.files) == 60
    assert diff.added == 60
