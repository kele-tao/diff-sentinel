# Contributing

Thanks for looking at diff-sentinel. It is a small, stdlib-only tool and we would
like to keep it that way.

## Setup

    git clone https://github.com/kele-tao/diff-sentinel
    cd diff-sentinel
    python -m venv .venv
    . .venv/bin/activate        # Windows: .venv\Scripts\activate
    pip install -e ".[dev]"

## Before opening a pull request

    python -m pytest -q         # all tests, offline
    ruff check .                # style, line length 100
    python benchmarks/run.py    # only if you touched a hot path

Every rule needs three things:

1. a unit test that feeds it a patch and asserts the finding,
2. an entry in `diff_sentinel/catalog.py` (the CLI prints it and the README
   lists it), and
3. a one-line `fix` string that a reviewer could follow without reading the
   source.

## Ground rules

- No new runtime dependencies. If a dependency is unavoidable, the pull request
  must say why the standard library cannot do it.
- No network access from tests, no sleeps, no flaky timing assertions.
- Output must stay deterministic: identical input, byte-identical output.
- Keep a diff reviewable: this project is reviewed with itself.

## Reporting a bad finding

Open an issue with the patch that triggered it (redact secrets), the command you
ran, and the finding you expected. False positives are bugs in this project.
