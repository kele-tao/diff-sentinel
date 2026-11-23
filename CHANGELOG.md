# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-02-14

### Added

- Unified diff parser (`--patch`, `--staged`, stdin) that accepts git, GNU diff
  and GitHub output, including renames, binary markers and CRLF patches.
- Git-backed review (`--base REF`) with merge-base resolution, commit metadata
  and PR-hygiene checks.
- 19 rules across nine analysers: change risk, missing tests, deleted
  assertions, migration hazards, dependency additions, secrets, debug
  leftovers, large/unrelated changes and PR hygiene.
- Transitive change-impact map built from a stdlib Python `ast` import graph
  plus a regex scanner for JavaScript and TypeScript.
- 0-100 review-readiness score with `--fail-on SEVERITY` exit-code gate.
- Output formats: terminal review card, Markdown for PR comments, JSON, and a
  ready-to-commit GitHub Actions workflow (`--emit-ci`).
- `rules` subcommand that lists the catalogue from a single source of truth.
- Tests offline and deterministic; benchmarks that write their own tables.

### Known limitations

- The import graph is best effort: dynamic imports, `importlib`, path aliases
  (tsconfig/webpack) and monorepo workspaces are not resolved.
- Licence detection uses a bundled table of ~50 popular packages; unknown
  packages are reported as unknown rather than guessed.
- Only Python and JavaScript/TypeScript participate in the impact map; other
  languages are still reviewed by the rules, just without dependents.

[1.0.0]: https://github.com/kele-tao/diff-sentinel/releases/tag/v1.0.0
