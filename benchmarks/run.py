"""Benchmark: generate synthetic diffs, time the real code path, write results.

Run from the repository root:

    python benchmarks/run.py

It does three things:

1. builds a synthetic repository in benchmarks/out/repo (gitignored) with 600 Python modules
   and a realistic import graph (each module imports two earlier modules),
2. renders unified diffs of increasing size using the same format git emits,
3. times diff-sentinel end to end - parse + rules + impact map - and writes

    benchmarks/results.json
    benchmarks/RESULTS.md

The numbers printed here are the ones quoted in the README; nothing is typed by
hand.
"""

from __future__ import annotations

import json
import shutil
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from diff_sentinel.diffparse import parse_patch  # noqa: E402
from diff_sentinel.review import compute_impact, review_diff  # noqa: E402

SCRATCH = ROOT / "benchmarks" / "out"
REPO = SCRATCH / "repo"
RESULTS_JSON = ROOT / "benchmarks" / "results.json"
RESULTS_MD = ROOT / "benchmarks" / "RESULTS.md"
DIFF_SIZES = (50, 150, 500)
REPEATS = 5
REPO_MODULES = 600


def build_repo(files: int = REPO_MODULES) -> Path:
    """Create a synthetic Python repository with a dense import graph."""
    if REPO.exists():
        shutil.rmtree(REPO)
    for index in range(files):
        package = REPO / "pkg" / ("module_" + str(index // 25).zfill(2))
        package.mkdir(parents=True, exist_ok=True)
        init = package / "__init__.py"
        if not init.exists():
            init.write_text("", encoding="utf-8")
        imports = ""
        if index > 2:
            imports += (
                "from pkg.module_"
                + str((index - 1) // 25).zfill(2)
                + ".file_"
                + str(index - 1).zfill(4)
                + " import VALUE\n"
            )
        if index > 10:
            imports += (
                "from pkg.module_"
                + str((index - 7) // 25).zfill(2)
                + ".file_"
                + str(index - 7).zfill(4)
                + " import VALUE\n"
            )
        body = (
            '"""Synthetic module ' + str(index) + '."""\n\n'
            + imports
            + "\n\nVALUE = " + str(index) + "\n\n\n"
            + "def compute(value):\n"
            + "    if value is None:\n"
            + "        return 0\n"
            + "    return value + VALUE\n"
        )
        (package / ("file_" + str(index).zfill(4) + ".py")).write_text(body, encoding="utf-8")
    return REPO


def make_diff(paths: list[str]) -> str:
    """Render a unified diff that modifies every given path."""
    chunks: list[str] = []
    for path in paths:
        chunks.append("diff --git a/" + path + " b/" + path)
        chunks.append("--- a/" + path)
        chunks.append("+++ b/" + path)
        chunks.append("@@ -1,6 +1,12 @@")
        chunks.append(' """Synthetic module."""')
        chunks.append(" ")
        chunks.append("+import json")
        chunks.append("+")
        chunks.append("+")
        chunks.append(" def compute(value):")
        chunks.append("     if value is None:")
        chunks.append("         return 0")
        chunks.append("+    if value < 0:")
        chunks.append("+        print(json.dumps({'negative': value}))")
        chunks.append("+        return 0")
        chunks.append("     return value + VALUE")
    return "\n".join(chunks) + "\n"


def time_call(function, repeats: int = REPEATS) -> float:
    """Return the median seconds the callable took."""
    samples: list[float] = []
    for _ in range(repeats):
        started = time.perf_counter()
        function()
        samples.append(time.perf_counter() - started)
    return statistics.median(samples)


def main() -> int:
    """Run every benchmark and write the artefacts."""
    SCRATCH.mkdir(parents=True, exist_ok=True)
    repo = build_repo()
    module_paths = sorted(
        str(path.relative_to(REPO)).replace("\\", "/")
        for path in REPO.rglob("*.py")
        if path.name != "__init__.py"
    )
    rows: list[dict[str, object]] = []

    for size in DIFF_SIZES:
        paths = module_paths[:size]
        text = make_diff(paths)
        diff = parse_patch(text)
        parse_s = time_call(lambda: parse_patch(text))
        full_s = time_call(lambda: review_diff(diff, repo=repo, target="synthetic", mode="patch"))
        impact_s = time_call(lambda: compute_impact(diff, repo))
        review = review_diff(diff, repo=repo, target="synthetic", mode="patch")
        rows.append(
            {
                "files": len(diff.files),
                "changed_lines": diff.added + diff.removed,
                "diff_kb": round(len(text) / 1024, 1),
                "parse_ms": round(parse_s * 1000, 2),
                "review_ms": round(full_s * 1000, 2),
                "impact_ms": round(impact_s * 1000, 2),
                "findings": len(review.findings),
                "modules_scanned": len(module_paths),
                "max_dependents": max((len(e.dependents) for e in review.impact), default=0),
            }
        )
        print(
            f"{len(diff.files):>4} files  {diff.added + diff.removed:>5} lines  "
            f"parse {parse_s * 1000:6.2f} ms  review {full_s * 1000:7.2f} ms  "
            f"impact {impact_s * 1000:6.2f} ms"
        )

    payload = {
        "environment": {"python": sys.version.split()[0], "platform": sys.platform},
        "repo_files": len(module_paths),
        "repeats": REPEATS,
        "rows": rows,
    }
    RESULTS_JSON.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    RESULTS_MD.write_text(render_markdown(payload), encoding="utf-8")
    print("wrote benchmarks/results.json and benchmarks/RESULTS.md")
    return 0


def render_markdown(payload: dict) -> str:
    """Render the README table from the measured payload."""
    lines = [
        "# diff-sentinel benchmark",
        "",
        "Generated by `python benchmarks/run.py` on Python "
        + str(payload["environment"]["python"])
        + " ("
        + str(payload["environment"]["platform"])
        + ").",
        "Each row is the median of "
        + str(payload["repeats"])
        + " runs against a generated repository of "
        + str(payload["repo_files"])
        + " Python modules.",
        "",
        "| files | changed lines | diff size | parse | review (parse + rules + impact) |"
        " impact only | findings |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in payload["rows"]:
        lines.append(
            "| "
            + str(row["files"])
            + " | "
            + str(row["changed_lines"])
            + " | "
            + str(row["diff_kb"])
            + " KB | "
            + str(row["parse_ms"])
            + " ms | "
            + str(row["review_ms"])
            + " ms | "
            + str(row["impact_ms"])
            + " ms | "
            + str(row["findings"])
            + " |"
        )
    lines.append("")
    lines.append(
        "The table uses the median because it is the honest number on a shared laptop; "
        "raw samples per row are in benchmarks/results.json."
    )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
