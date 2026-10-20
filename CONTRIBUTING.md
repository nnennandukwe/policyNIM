# Contributing to PolicyNIM

PolicyNIM retrieves policy sources and produces grounded guidance for coding agents.
Start with [the contributor guide](docs/contributor-guide.md) and the accepted
[roadmap](https://github.com/nnennandukwe/policyNIM/issues/88). Keep each PR tied to
one issue and describe the behavior a caller will observe.

## Set up and verify

Use Python 3.11 or 3.12 and the checked-in lockfile:

```bash
uv sync --locked --group test --group dev
uv run --no-sync ruff check .
uv run --no-sync pyright
uv run --no-sync pytest -q -m "not live and not docker_live"
uv lock --check
```

Default CI is offline. Live NVIDIA, hosted MCP and Docker checks are opt-in;
[tests/README.md](tests/README.md) names their gates. Don't run live checks simply
because credentials happen to be present.

For changed commands, exercise help, successful behavior, rejection and recovery.
For MCP changes, verify the relevant transport and client behavior described in
[the workflow guide](docs/workflows.md). Packaging or release changes also need the
checks in [docs/release.md](docs/release.md). A source test result doesn't prove a
wheel, standalone artifact, external client or production deployment works.

## Submit a PR

Include the issue, user impact, exact revision tested, commands/results and any
skipped checks. Explain interruption, cleanup or migration behavior when affected.
Use the PR template and keep provider calls or external writes explicit.

Never include tokens, private policy text, task content or unredacted diagnostic
bundles. Follow [SUPPORT.md](SUPPORT.md) for safe diagnostics and
[SECURITY.md](SECURITY.md) for private vulnerability reports.

Dependency and GitHub Actions updates need the same review as other code. Keep
versions/lockfiles aligned, preserve immutable action pins and verify offline gates;
Dependabot doesn't replace maintainer review. [CODEOWNERS](.github/CODEOWNERS)
identifies the current owner. The configuration alone doesn't prove branch
protection or required-review settings are enabled.
