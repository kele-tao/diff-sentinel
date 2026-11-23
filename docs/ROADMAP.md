# Roadmap

What shipped in v1.0 and what is deliberately deferred. Anything listed as cut
is cut honestly: it does not exist in the codebase.

## Shipped in 1.0

- Unified diff parsing from `git diff`, `--staged` and `--patch` files.
- 19 rules across change risk, missing tests, deleted assertions, migration
  hazards, dependency additions, secrets, debug leftovers, large diffs,
  unrelated changes and PR hygiene.
- Transitive impact map for Python and JavaScript/TypeScript.
- 0-100 score, verdict, `--fail-on` gate, Markdown/JSON/terminal output, CI
  snippet generator, `rules` catalogue command.
- 107 tests, offline, deterministic; benchmark harness that writes its own table.

## Deferred (not shipped, not faked)

1. **GitHub API comment posting.** `--emit-ci` prints a workflow that uses a
   third-party comment action. Posting directly would need a token, a network
   call and pagination; that conflicts with "offline, no network, no secrets".
2. **Cross-language import resolution.** Go, Rust and Java dependents are not
   mapped. The graph covers Python (`ast`) and JS/TS (regex). Adding more
   languages means writing a real resolver per language; a half parser would
   produce confident wrong answers.
3. **Monorepo path aliases.** `tsconfig` paths, webpack aliases and pnpm
   workspaces are not resolved, so relative imports only. Alias support needs a
   config-aware resolver and a fixture per ecosystem.
4. **Historical secret scanning.** Only added lines are scanned. Scanning the
   full history would require walking every blob; `gitleaks` and
   `trufflehog` already do that better.
5. **Per-project rule configuration.** There is no `.diff-sentinel.toml` yet.
   Severity overrides and rule disabling are the first thing to add once real
   users report false positives (tracked as the top issue in the repo).
6. **SARIF output.** GitHub code scanning wants SARIF; the JSON model already
   carries everything needed (rule id, severity, location, message), so this is
   a renderer, not an analyser change.

## Next

- `.diff-sentinel.toml` with rule severity overrides and path ignores.
- SARIF renderer so findings appear inline in the GitHub files-changed tab.
- A Go import resolver, then Rust (`mod` graph), behind the same interface.
- Golden-file tests for the terminal renderer (a real terminal snapshot, not
  string matching).
