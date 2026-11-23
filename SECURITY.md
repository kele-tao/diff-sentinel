# Security Policy

## Supported versions

| Version | Supported |
| --- | --- |
| 1.x | yes |

## Reporting a vulnerability

Please do not open a public issue for a security problem. Use GitHub's private
vulnerability reporting (Security tab, then Report a vulnerability) or email the
maintainer listed in `pyproject.toml`. We will acknowledge within 3 working
days and aim to ship a fix within 14 days for anything rated high or critical.

## How diff-sentinel handles secrets

diff-sentinel is designed to be safe to run in CI against untrusted patches:

- It never executes code from the patch, never imports the reviewed repository
  and never resolves dependencies.
- It never makes a network call.
- It reads files from the repository only for the impact map, using the stdlib
  `ast` module in parse-only mode.
- A detected credential is masked in every output format: the report shows at
  most the first 6 and last 2 characters so a reviewer can locate it, never the
  full value.
- Findings are written to stdout or a file you choose; nothing is uploaded.

## Scanning scope

The secret detectors cover AWS access keys, GitHub/GitLab/Slack tokens, Stripe
keys, Google API keys, JWTs, private key blocks, URLs with embedded database
credentials and hardcoded password-style assignments. They are pattern based
and deliberately conservative: placeholder values such as `changeme`,
`example` or ${VAR} are ignored to keep false positives low. Absence
of a finding is not a guarantee that a diff contains no secret.
