# PolicyNIM

<p align="center">
  <picture>
    <source
      media="(prefers-color-scheme: dark)"
      srcset="src/policynim/assets/beta/policynim_darkmode.jpg"
    >
    <img
      src="src/policynim/assets/beta/policynim_lightmode.png"
      alt="PolicyNIM logo"
      width="460"
    >
  </picture>
</p>

[![CI](https://github.com/nnennandukwe/policyNIM/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/nnennandukwe/policyNIM/actions/workflows/ci.yml?query=branch%3Amain)
[![PyPI](https://img.shields.io/pypi/v/policynim?label=PyPI)](https://pypi.org/project/policynim/)
[![Python versions](https://img.shields.io/pypi/pyversions/policynim)](https://pypi.org/project/policynim/)
[![GitHub Release](https://img.shields.io/github/v/release/nnennandukwe/policyNIM?sort=semver)](https://github.com/nnennandukwe/policyNIM/releases)
[![License](https://img.shields.io/github/license/nnennandukwe/policyNIM)](LICENSE)
[![Built with NVIDIA NIM](https://img.shields.io/badge/Built%20with-NVIDIA%20NIM-76B900?logo=nvidia&logoColor=white)](https://docs.nvidia.com/nim/)

PolicyNIM is a policy-aware engineering preflight layer for AI coding agents.

It helps an agent retrieve grounded policy evidence, generate implementation
guidance with citations attached, and fail closed when the available grounding
is too weak to trust.

PolicyNIM currently ships with two main user-facing surfaces:

- a JSON-first CLI for local developer workflows
- an MCP server for integrations such as Codex and Claude Code

## Start Here: Pick A Path

Most developers should start with one of these paths. Hosted MCP is the fastest
way to try PolicyNIM with a coding agent. Local CLI is the fastest way to run a
policy preflight from your own terminal. Source checkout is for contributors.

### Hosted MCP In A Few Clicks

<p align="center">
  <img
    src="docs/assets/readme/policynim-beta-dark-landing-preview.png"
    alt="PolicyNIM hosted beta landing page in dark mode"
    width="1100"
  >
</p>

Use this when you want Codex or Claude Code to call PolicyNIM without cloning
the repo or building a local index.

If you were given only the hosted MCP URL, open it in a browser first. In
particular, open the hosted `/mcp` URL in a browser; the service routes you to
`/beta` so you can create or rotate a token before adding the MCP server to a
coding agent.

1. Open `https://<railway-domain>/beta`.
2. Sign in with GitHub.
3. Generate or rotate your hosted API key.
4. Export the token, then choose the command for your client:

```bash
export POLICYNIM_TOKEN='<generated-beta-token>'
```

Codex:

```bash
codex mcp add policynim --url 'https://<railway-domain>/mcp' --bearer-token-env-var POLICYNIM_TOKEN
```

Claude Code:

```bash
claude mcp add --transport http policynim 'https://<railway-domain>/mcp' --header "Authorization: Bearer $POLICYNIM_TOKEN"
```

If you installed the CLI, generate the same hosted setup command from the
tested config contract instead of copying it by hand. Run the command for the
selected client.

Codex:

```bash
policynim quickstart --target hosted-mcp --client codex --hosted-url 'https://<railway-domain>/mcp' --format json
```

Claude Code:

```bash
policynim quickstart --target hosted-mcp --client claude-code --hosted-url 'https://<railway-domain>/mcp' --format json
```

The JSON includes `client_commands` with the exact MCP client command for the
selected client to paste after exporting `POLICYNIM_TOKEN`.

Codex:

```bash
policynim mcp-config --target hosted-http --client codex --hosted-url 'https://<railway-domain>/mcp' --bearer-token-env-var POLICYNIM_TOKEN
```

Claude Code:

```bash
policynim mcp-config --target hosted-http --client claude-code --hosted-url 'https://<railway-domain>/mcp' --bearer-token-env-var POLICYNIM_TOKEN
```

Add `--format json` when you want reviewable setup evidence. If the generated
JSON includes `"hosted_url_placeholder": true`, the command is still using an
example URL. Replace the hosted URL placeholder with the deployed `/mcp` URL
before adding the server to a client. `quickstart` also emits `hosted_url` and
`beta_portal_url`; with a real `/mcp` URL, the token portal URL is derived from
the same hosted origin. Hosted `mcp-config --format json` includes the same
`beta_portal_url` so setup reports can show both the portal and MCP endpoint.

The hosted MCP path does not require local setup. Run `policynim init` and
`policynim ingest` only when you choose a local CLI or local MCP workflow.
MCP clients still receive protocol/auth responses from `/mcp`.

Then ask your coding agent to call the MCP tools directly:

- `List the PolicyNIM MCP tools and confirm policy_preflight and policy_search are available before starting implementation.`
- `Before editing, call policy_preflight for: Implement a refresh-token cleanup background job. Use the cited constraints in your implementation plan. If the result is insufficient_context, stop and call policy_search with a narrower query before changing files.`
- `Use policy_search for: release installer checksum verification. Summarize the relevant cited policy lines before proposing a fix.`

Use [docs/hosted-beta-operations.md](docs/hosted-beta-operations.md) for:

- hosted beta recovery topics
- container build and local hosted-image checks
- Railway deploy setup and smoke-test notes

### Local CLI In A Few Commands

Use this when you want to run PolicyNIM against a local policy corpus from your
terminal.

Use the PyPI package path when you already have Python 3.11 or 3.12 and want
`pipx` or `uv` to manage an isolated CLI environment:

Public PyPI install status: treat PyPI package availability as install-channel
discovery until `pypi_install_smoke` passes for the version you are installing.
The README documents the current source-tree first-run contract. If
`policynim quickstart` is unavailable after a public PyPI install, that package
version does not pass the public launch gate. Use a source checkout or a GitHub
release built from the current CLI, or wait for the next PyPI release before
using the no-clone first-run path.

```bash
pipx install --python 3.11 policynim
```

Or:

```bash
uv tool install --python 3.11 policynim
```

Use the GitHub release installers when you want a standalone `policynim` binary
without managing Python dependencies yourself:

Published standalone release targets are macOS Apple Silicon (`darwin-arm64`),
macOS Intel (`darwin-amd64`), Linux x86_64 (`linux-amd64`), and Windows x86_64
(`windows-amd64`). `install.sh` auto-detects the supported macOS or Linux
target and downloads the matching tarball; `install.ps1` installs the Windows
bundle.

GitHub release installer status: treat release asset availability as
install-channel discovery until `github_release_install_smoke` passes for the
version you are installing. If `policynim quickstart` is unavailable after a
GitHub release installer run, that release does not pass the public launch gate.
Use a source checkout or wait for the next GitHub release before using the
no-clone first-run path.

```bash
curl -fsSL https://github.com/nnennandukwe/policyNIM/releases/latest/download/install.sh | sh
```

```powershell
irm https://github.com/nnennandukwe/policyNIM/releases/latest/download/install.ps1 | iex
```

Then run the no-network checks:

```bash
policynim --help
policynim quickstart
policynim doctor
```

For a real local preflight, add your NVIDIA API key, initialize config, build
the index, and run the task:

```bash
export NVIDIA_API_KEY='<your-nvidia-api-key>'
policynim init
policynim ingest
policynim preflight --task "Implement a refresh-token cleanup background job" --top-k 5
```

Use `policynim doctor` whenever setup, ingest, or MCP launch behavior is
unclear; it prints safe local diagnostics without calling NVIDIA-hosted APIs.

Both GitHub installer paths verify release checksums before installing. If you
also want GitHub artifact attestation verification during install, install
GitHub CLI and set `POLICYNIM_VERIFY_ATTESTATION=1` before running the
installer; the scripts run `gh attestation verify` against the downloaded
release asset. PyPI trusted-publishing evidence remains a separate
public-launch proof point; do not treat package availability alone as proof
that the release workflow's `publish-pypi` job was verified. Public launch
evidence also includes a clean PyPI install smoke and a clean GitHub release
installer smoke for the published version.

After install, run `policynim quickstart` to choose the hosted MCP, local CLI,
or local MCP path. Use `policynim --help` whenever you need to confirm the
entrypoint is available.

### Source Checkout For Contributors

Use this when you want to change PolicyNIM itself or run the local test suite.

```bash
uv sync --group test --group dev
uv run policynim quickstart --target local-cli --format json
uv run policynim doctor
uv run pytest -q -m "not live and not docker_live"
```

Add `NVIDIA_API_KEY` and run `uv run policynim ingest` only when you want to
exercise live retrieval, preflight, or local MCP behavior from the checkout.
The full contributor path is documented below.

## High-Value Agent Workflows

Use PolicyNIM where an agent is about to make or explain an implementation
choice and needs project policy evidence attached to that choice.

For the fuller coding-agent playbook, see
[docs/agent-workflows.md](docs/agent-workflows.md).

### Preflight before implementation

Ask your coding agent to call `policy_preflight` before it edits code:

- `Before editing, call policy_preflight for: Implement a refresh-token cleanup background job. Use the cited constraints in your implementation plan. If the result is insufficient_context, stop and call policy_search with a narrower query before changing files.`
- `Before editing, call policy_preflight for: Add a GitHub release smoke check for the installer. Use the cited constraints before changing workflow files.`

The CLI equivalent is:

```bash
policynim preflight --task "Implement a refresh-token cleanup background job" --top-k 5
```

### Retrieve policy evidence while debugging

Ask your coding agent to call `policy_search` when review feedback or a failing
gate needs the underlying policy text:

- `Use policy_search for: release installer checksum verification. Summarize the relevant cited policy lines before proposing a fix.`
- `Use policy_search for: runtime evidence retention rules.`

The CLI equivalent is:

```bash
policynim search --query "release installer checksum verification" --top-k 5
```

### Verify MCP wiring before a real session

Generate client config, then smoke the generated file before asking Codex or
Claude Code to use the server:

```bash
policynim mcp-config --target local-stdio --client codex --format json > codex-mcp-config.json
policynim mcp-smoke --mcp-config-file codex-mcp-config.json --format json
```

### Attach issue-ready diagnostics

When setup fails, attach the redacted support bundle instead of raw local paths
or secrets:

```bash
policynim support-bundle --include-mcp-smoke
```

## What Works Today

- Deterministic Markdown ingest with heading-aware chunking and source line spans.
- Ingest-time compilation of `runtime_rules` frontmatter into the persisted runtime rules artifact.
- NVIDIA-hosted embeddings and reranking for retrieval.
- SQLite-backed sqlite-vec storage for the retrievable policy index.
- Task-aware policy routing with citation-preserving selected-policy packets.
- Policy compilation into citation-backed planning and generation constraints.
- Grounded preflight synthesis with compiled plan steps, citation validation, and
  fail-closed fallback.
- Opt-in preflight evidence traces that link chunks, selected policies, compiled
  constraints, generated guidance, and conformance checks.
- Opt-in policy-backed regeneration for preflight and eval preflight cases,
  reusing the same compiled packet and typed conformance failures as retry
  triggers.
- Eval backend selection with optional policy-conformance scoring for compiled
  plans and preflight outputs, with compact traces embedded in eval result
  artifacts and local Phoenix reporting for non-headless runs.
- Runtime-rule decisions plus SQLite-backed evidence for allowed, confirmed,
  blocked, and failed runtime actions.
- Interactive `init` setup plus JSON-first CLI commands for `ingest`,
  `dump-index`, `search`, `route`, `compile`, `preflight`, `eval`, `mcp`,
  `runtime`, and `evidence`.
- MCP tools for `policy_preflight` and `policy_search`.
- Hosted HTTP `streamable-http` with `/healthz`, a self-serve `/beta` portal,
  and bearer auth on `/mcp`.

## Local Contributor Setup

Use this path only if you want to run PolicyNIM from a local checkout.

```bash
uv sync --group test --group dev
export NVIDIA_API_KEY='<your-nvidia-api-key>'
uv run policynim ingest
uv run pytest -q -m "not live and not docker_live"
```

If you want the CLI to prompt for the required values and write the local config
file for you, run:

```bash
uv run policynim init
```

In a source checkout, `init` writes the checkout `.env` file that PolicyNIM
loads by default. Installed copies should keep using the direct `policynim init`
entrypoint described below.

If you prefer to manage `.env` manually, copy the template first:

```bash
cp .env.development.example .env
```

After the index is built, the fastest local sanity checks are:

```bash
uv run policynim doctor
uv run policynim support-bundle
uv run policynim mcp-smoke
uv run policynim mcp-config --client codex
uv run policynim search --query "refresh token cleanup background job" --top-k 5
uv run policynim route --task "Implement a refresh-token cleanup background job" --top-k 5
uv run policynim compile --task "Implement a refresh-token cleanup background job" --top-k 5
uv run policynim preflight --task "Implement a refresh-token cleanup background job" --top-k 5
uv run policynim preflight --task "Implement a refresh-token cleanup background job" --top-k 5 --trace
uv run policynim preflight --task "Implement a refresh-token cleanup background job" --top-k 5 --regenerate --backend nemo
```

Use [docs/contributor-guide.md](docs/contributor-guide.md) for environment
templates, runtime settings, optional NVIDIA eval and Guardrails extras, and
contributor quality gates. The launcher path is installable in-project with
`uv sync --extra nvidia-eval --extra nvidia-eval-launcher --group test --group dev`;
the internal Guardrails output-rail wrapper uses `uv sync --extra nvidia-guardrails`.

If you are using an installed copy instead of a source checkout, run
`policynim init` once first so PolicyNIM can write the standalone config file
and data-path defaults before `policynim ingest`. Then use
`policynim mcp-config --client codex` or
`policynim mcp-config --client claude-code` to generate no-clone local stdio
MCP config from the installed entrypoint. Use `uv run` only when running
commands from the source checkout's uv-managed project environment.

Use [docs/workflows.md](docs/workflows.md) for the CLI, MCP, runtime, eval, and
troubleshooting handbook.

## Docs Map

Start here when you want the longer version of a specific path:

- [docs/index.md](docs/index.md): documentation hub by audience and task
- [docs/contributor-guide.md](docs/contributor-guide.md): local setup, env vars,
  model references, and quality gates
- [docs/workflows.md](docs/workflows.md): CLI surfaces,
  ingest/search/route/compile/preflight, eval, MCP, runtime/evidence, and
  troubleshooting
- [docs/agent-workflows.md](docs/agent-workflows.md): copy-paste coding-agent
  prompts and integration recipes for `policy_preflight`, `policy_search`, MCP
  setup smoke, and diagnostics
- [docs/hosted-beta-operations.md](docs/hosted-beta-operations.md): hosted beta
  quickstart, recovery, container build flow, and Railway deploy notes
- [docs/release.md](docs/release.md): CLI packaging, GitHub release, PyPI
  publish, and smoke-test checklist
- [docs/oss-readiness-audit.md](docs/oss-readiness-audit.md): current developer
  journey, OSS-readiness priorities, evidence map, and launch proof status from
  `scripts/oss_readiness_check.py`
- [docs/public-launch-runbook.md](docs/public-launch-runbook.md): ordered
  external proof workflow for moving from local readiness to public launch
- [CHANGELOG.md](CHANGELOG.md): versioned release history and pending release
  notes
- [docs/architecture.md](docs/architecture.md): package boundaries, runtime flow,
  and interface rules
- [docs/architecture-diagram.md](docs/architecture-diagram.md): Mermaid diagram
  of the current package layout and runtime flow
- [docs/demo-script.md](docs/demo-script.md): step-by-step demo for the hero use case
- [docs/limitations.md](docs/limitations.md): current product limits and non-goals
- [docs/public-source-grounding.md](docs/public-source-grounding.md): provenance
  notes for the shipped sample corpus
- [tests/README.md](tests/README.md): current automated coverage
- [CONTRIBUTING.md](CONTRIBUTING.md): contribution workflow, verification
  expectations, and PR evidence checklist
- [SECURITY.md](SECURITY.md): private vulnerability reporting and secret
  redaction guidance
- [SUPPORT.md](SUPPORT.md): issue routing, support-bundle guidance, and response
  expectations
- [docs/maintainer-triage.md](docs/maintainer-triage.md): issue label and repo
  topic taxonomy, priority routing, and response rules
- [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md): community standards and enforcement
  expectations
- [docs/roadmap.md](docs/roadmap.md): current roadmap, non-promises, and public
  adoption priorities
- [examples/codex/README.md](examples/codex/README.md): Codex MCP setup example
- [examples/claude-code/README.md](examples/claude-code/README.md): Claude Code
  MCP setup example

## Talks And Workflow Notes

- [docs/ai-engineer-miami-context-plane.md](docs/ai-engineer-miami-context-plane.md): centralized context-plane talk notes and project framing
- [docs/extreme-programming-with-agents.md](docs/extreme-programming-with-agents.md): XP, TDD, and agent workflow notes

## Limits And Scope

Current limitations are intentional:

- the system is local-first and aimed at a single developer workflow
- CI is offline-only and does not run live NVIDIA end-to-end checks by default
- the sample corpus is narrow and synthetic, not a broad enterprise handbook
- grounded answers may fail closed even when raw retrieval finds useful chunks

See [docs/limitations.md](docs/limitations.md) for the full list and future
expansion areas.
