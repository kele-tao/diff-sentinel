"""CLI behaviour: flags, exit codes and friendly errors."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import EXAMPLE_PATCH, git

from diff_sentinel.cli import main

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def test_help_exits_zero_for_subcommand(capsys):
    with pytest.raises(SystemExit) as info:
        main(["review", "--help"])
    assert info.value.code == 0
    assert "--fail-on" in capsys.readouterr().out


def test_no_subcommand_prints_help_and_exits_two(capsys):
    assert main([]) == 2
    assert "usage: diff-sentinel" in capsys.readouterr().out


def test_review_patch_json(capsys):
    code = main(["review", "--patch", str(EXAMPLE_PATCH), "--format", "json"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["files"] == 7
    assert payload["findings"]


def test_review_patch_markdown(capsys):
    code = main(["review", "--patch", str(EXAMPLE_PATCH), "--format", "md"])
    assert code == 0
    assert capsys.readouterr().out.startswith("## diff-sentinel review")


def test_review_patch_terminal_is_plain_with_color_never(capsys):
    code = main(["review", "--patch", str(EXAMPLE_PATCH), "--color", "never"])
    assert code == 0
    out = capsys.readouterr().out
    assert "\x1b[" not in out
    assert "VERDICT" in out


def test_fail_on_high_exits_one(capsys):
    code = main(["review", "--patch", str(EXAMPLE_PATCH), "--fail-on", "high"])
    assert code == 1
    assert "gate failed" in capsys.readouterr().err


def test_fail_on_critical_still_fails_for_this_patch(capsys):
    assert main(["review", "--patch", str(EXAMPLE_PATCH), "--fail-on", "critical"]) == 1
    capsys.readouterr()


def test_score_subcommand_exit_code(capsys):
    code = main(["score", "--patch", str(EXAMPLE_PATCH)])
    assert code == 1
    out = capsys.readouterr().out
    assert out.startswith("FAIL")


def test_score_json_flag(capsys):
    code = main(["score", "--patch", str(EXAMPLE_PATCH), "--json"])
    assert code == 1
    assert json.loads(capsys.readouterr().out)["score"] == 0


def test_output_file_is_written(tmp_path, capsys):
    target = tmp_path / "out" / "review.md"
    code = main(["review", "--patch", str(EXAMPLE_PATCH), "--format", "md", "-o", str(target)])
    assert code == 0
    assert target.exists()
    assert "diff-sentinel review" in target.read_text(encoding="utf-8")
    assert "wrote" in capsys.readouterr().out


def test_missing_patch_file_is_a_usage_error(capsys):
    assert main(["review", "--patch", "no-such-file.patch"]) == 2
    err = capsys.readouterr().err
    assert "patch file not found" in err
    assert "Traceback" not in err


def test_directory_as_patch_is_a_usage_error(tmp_path, capsys):
    assert main(["review", "--patch", str(tmp_path)]) == 2
    assert "is a directory" in capsys.readouterr().err


def test_empty_patch_is_a_usage_error(tmp_path, capsys):
    empty = tmp_path / "empty.patch"
    empty.write_text("   \n", encoding="utf-8")
    assert main(["review", "--patch", str(empty)]) == 2
    assert "patch is empty" in capsys.readouterr().err


def test_garbage_patch_is_a_usage_error(tmp_path, capsys):
    junk = tmp_path / "junk.patch"
    junk.write_text("hello there\n", encoding="utf-8")
    assert main(["review", "--patch", str(junk)]) == 2
    assert "no file changes" in capsys.readouterr().err


def test_unknown_severity_is_a_usage_error(capsys):
    assert main(["review", "--patch", str(EXAMPLE_PATCH), "--fail-on", "nope"]) == 2
    assert "unknown severity" in capsys.readouterr().err


def test_not_a_repository_is_a_usage_error(tmp_path, capsys):
    assert main(["review", "--repo", str(tmp_path)]) == 2
    assert "not a git repository" in capsys.readouterr().err


def test_missing_repository_directory_is_a_usage_error(tmp_path, capsys):
    assert main(["review", "--repo", str(tmp_path / "nope")]) == 2
    assert "repository directory not found" in capsys.readouterr().err


def test_base_branch_review_reports_impact(demo_repo, capsys):
    code = main(
        [
            "review",
            "--repo",
            str(demo_repo),
            "--base",
            "main",
            "--format",
            "json",
            "--color",
            "never",
        ]
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "branch"
    assert payload["files"] == 7
    assert payload["impact"]
    assert any(entry["dependent_count"] > 0 for entry in payload["impact"])


def test_branch_review_uses_commit_hygiene(demo_repo, capsys):
    code = main(
        ["review", "--repo", str(demo_repo), "--base", "main", "--format", "json",
         "--title", "fix", "--body", "x"]
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    rule_ids = {finding["rule"] for finding in payload["findings"]}
    assert "weak-pr-title" in rule_ids


def test_unknown_base_ref_is_a_usage_error(demo_repo, capsys):
    code = main(["review", "--repo", str(demo_repo), "--base", "does-not-exist"])
    assert code == 2
    assert "unknown base ref" in capsys.readouterr().err


def test_staged_review(demo_repo, capsys):
    target = demo_repo / "app" / "staged_only.py"
    target.write_text("print('staged')\n", encoding="utf-8")
    git(demo_repo, "add", "app/staged_only.py")
    code = main(
        ["review", "--repo", str(demo_repo), "--staged", "--format", "json"]
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "staged"
    assert payload["files"] == 1


def test_max_files_guard(capsys):
    code = main(["review", "--patch", str(EXAMPLE_PATCH), "--max-files", "2"])
    assert code == 2
    assert "above --max-files" in capsys.readouterr().err


def test_ignore_prefix(capsys):
    code = main(
        ["review", "--patch", str(EXAMPLE_PATCH), "--ignore", "migrations/", "--format", "json"]
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    paths = [finding["path"] for finding in payload["findings"] if finding["path"]]
    assert paths
    assert all(not path.startswith("migrations/") for path in paths)


def test_rules_subcommand_lists_every_rule(capsys):
    assert main(["rules"]) == 0
    out = capsys.readouterr().out
    assert "typosquat" in out
    assert "missing-tests" in out


def test_rules_json(capsys):
    assert main(["rules", "--json"]) == 0
    rules = json.loads(capsys.readouterr().out)
    assert len(rules) >= 15
    assert all({"id", "severity", "summary"} <= set(rule) for rule in rules)


def test_emit_ci_prints_workflow(capsys):
    assert main(["review", "--patch", str(EXAMPLE_PATCH), "--emit-ci"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("name: diff-sentinel")
    assert "pull_request" in out


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as info:
        main(["--version"])
    assert info.value.code == 0
    assert "diff-sentinel 1.0.0" in capsys.readouterr().out


def test_stdin_patch(monkeypatch, capsys):
    patch = EXAMPLES.joinpath("pr.patch").read_text(encoding="utf-8")

    class FakeStdin:
        def read(self):
            return patch

    monkeypatch.setattr("sys.stdin", FakeStdin())
    assert main(["review", "--patch", "-", "--format", "json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["target"] == "<stdin>"
