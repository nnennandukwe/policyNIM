# Contributing to PolicyNIM

PolicyNIM is a verification tool for AI-coding-agent workflows. Contributions
should keep the public CLI, MCP tools, docs, and release gates easy to verify.

## Before Opening a PR

1. Read [docs/contributor-guide.md](docs/contributor-guide.md) for local setup.
2. Run `uv run policynim doctor --format json` and fix any setup issues it
   reports.
3. Run the offline quality gates:

```bash
uv run ruff check .
uv run pyright
uv run pytest -q -m "not live and not docker_live"
uv lock --check
uv build --out-dir dist
uv run policynim support-bundle
```

4. If your change affects MCP setup, also check the relevant hosted-first client
   example under [examples/](examples).
5. If your change affects packaging, installers, or release automation, run
   `uv run python scripts/release_check.py` and attach the ship/hold output.

## What To Include

- Explain the user-facing workflow you changed.
- Choose one primary lane from
  [docs/oss-readiness-audit.md#high-value-pr-sequence](docs/oss-readiness-audit.md#high-value-pr-sequence):
  first-run and hosted MCP onboarding, local CLI and MCP verification,
  installability and release trust, SQLite migration and storage contract, or
  maintainer trust and public launch proof.
- Keep the PR scoped to one user-facing thesis, one primary evidence surface,
  and a bounded rollback story.
- Include exact commands and test output, not just "tests pass."
- Call out whether live NVIDIA, hosted beta, Docker, or Railway checks were not
  run.
- For CLI or MCP changes, include the relevant `--help`, `policynim doctor`, or
  `policynim support-bundle` output.
- No API keys, bearer tokens, generated hosted beta tokens, or private policy
  corpus content should appear in issues, pull requests, screenshots, or logs.

## Live And Hosted Checks

Default CI is offline. Live NVIDIA, hosted MCP, and Docker checks are opt-in so
pull requests stay reproducible for outside contributors. See
[tests/README.md](tests/README.md) for the exact environment variables and
commands.

## Dependency And Ownership Updates

PolicyNIM keeps review ownership in [.github/CODEOWNERS](.github/CODEOWNERS)
and bounded weekly dependency updates in
[.github/dependabot.yml](.github/dependabot.yml). Dependabot PRs are not
auto-merge candidates. Treat updates to `uv.lock`, GitHub Actions, installers,
release scripts, and MCP entrypoints as code changes that need deterministic
offline gates and code-owner review before merge.

## Review Bar

Maintainer trust matters more than broad feature claims. Prefer small,
evidence-backed changes with clear recovery behavior, deterministic tests, and
docs that match the actual command output.
