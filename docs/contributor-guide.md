# PolicyNIM Contributor Guide

Use this guide when you are running PolicyNIM from a local checkout instead of
the hosted beta.

## Local Setup

Install the runtime, test, and dev dependencies:

```bash
uv sync --group test --group dev
```

If you want the CLI to prompt for the required values and write the local config
file for you, run:

```bash
uv run policynim init
```

In a source checkout, `init` writes the checkout `.env` file, and the normal
settings loader reads that file on subsequent `uv run policynim ...` commands.
In an installed standalone runtime, `init` writes the user config path printed in
the command output.

Otherwise, copy the development example environment file:

```bash
cp .env.development.example .env
```

If you copied the template manually, set `NVIDIA_API_KEY` in `.env` or your
shell. For the official key-creation flow, use NVIDIA's
[API Catalog Quickstart Guide](https://docs.api.nvidia.com/nim/docs/api-quickstart)
and [Build catalog](https://build.nvidia.com/).

If you want to index a custom policy directory instead of the bundled sample
corpus, add `POLICYNIM_CORPUS_DIR=/abs/path/to/policies` manually to `.env`.

Build the local index once before using search, preflight, or the local MCP
server:

```bash
uv run policynim ingest
```

## Standalone Install

If you are using an installed copy instead of a source checkout, initialize the
user-owned config once:

```bash
policynim init
```

That command prompts for `NVIDIA_API_KEY` and an optional custom corpus
directory, then writes the standalone `config.env` file under your platform
config directory with user-owned defaults for `POLICYNIM_INDEX_DB_PATH`,
`POLICYNIM_RUNTIME_RULES_ARTIFACT_PATH`, `POLICYNIM_RUNTIME_EVIDENCE_DB_PATH`,
and `POLICYNIM_EVAL_WORKSPACE_DIR`.

After that, run `policynim ingest` as usual. Source checkouts can keep using the
checkout `.env` flow above and `uv run` for in-project commands. Run
`policynim doctor` before or after ingest when you need a safe local setup
diagnostic that does not call NVIDIA-hosted APIs.

## Direct CLI Install

Use the PyPI package path when you already have Python 3.11 or 3.12 and want
`pipx` or `uv tool` to manage an isolated CLI environment:

Public PyPI install status: treat PyPI package availability as install-channel
discovery until `pypi_install_smoke` passes for the version you are installing.
The contributor docs describe the current source-tree first-run contract. If
`policynim quickstart` is unavailable after a public PyPI install, that package
version does not pass the public launch gate. Use a source checkout or a GitHub
release built from the current CLI, or wait for the next PyPI release before
using the no-clone first-run path.

```bash
pipx install --python 3.11 policynim
uv tool install --python 3.11 policynim
policynim --help
policynim quickstart
policynim doctor
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

Use `--python 3.12` instead when Python 3.12 is your managed runtime. If your
machine does not expose `3.11` or `3.12` by name, pass the full path to that
Python executable.

PyPI trusted-publishing evidence is still tracked separately in the public
launch runbook. Package availability proves the install channel exists; it does
not by itself prove that the Release workflow's `publish-pypi` job was verified.

Both installers download the latest GitHub release, verify `SHA256SUMS`, install
the versioned bundle, and print PATH guidance. If GitHub CLI is available and
you want provenance verification during install, set
`POLICYNIM_VERIFY_ATTESTATION=1`; the installers then run
`gh attestation verify` before extracting the bundle. They do not prompt for or
collect `NVIDIA_API_KEY`; run `policynim quickstart` first when you need to
choose between hosted MCP, local CLI, and local MCP. The hosted MCP path does
not require local setup. Run `policynim init` after installation to create the
local config, then `policynim ingest` to build the local policy index only when
you choose a local CLI or local MCP workflow.

## Environment Templates

The repo ships three related templates:

- `.env.development.example` is the preferred local-development template
- `.env.production.example` is the hosted Railway and Docker reference
- `.env.example` remains a backward-compatible alias for the development defaults

Leave `POLICYNIM_CORPUS_DIR` unset to use the bundled sample corpus.

## Important Runtime Settings

Core retrieval and grounding:

- `NVIDIA_API_KEY`
- `POLICYNIM_CORPUS_DIR`
- `POLICYNIM_INDEX_DB_PATH`
- `POLICYNIM_DEFAULT_TOP_K`
- `POLICYNIM_NVIDIA_CHAT_MODEL`
- `POLICYNIM_NVIDIA_BASE_URL`
- `POLICYNIM_NVIDIA_TIMEOUT_SECONDS`
- `POLICYNIM_NVIDIA_MAX_RETRIES`

Hosted MCP and beta portal:

- `POLICYNIM_ENV`
- `POLICYNIM_MCP_HOST`
- `POLICYNIM_MCP_PORT`
- `POLICYNIM_MCP_REQUIRE_AUTH`
- `POLICYNIM_MCP_BEARER_TOKENS`
- `POLICYNIM_MCP_PUBLIC_BASE_URL`
- `POLICYNIM_BETA_SIGNUP_ENABLED`
- `POLICYNIM_BETA_AUTH_DB_PATH`
- `POLICYNIM_BETA_SESSION_SECRET`
- `POLICYNIM_BETA_GITHUB_CLIENT_ID`
- `POLICYNIM_BETA_GITHUB_CLIENT_SECRET`
- `POLICYNIM_BETA_DAILY_REQUEST_QUOTA`

Runtime rules, evidence, and eval UI:

- `POLICYNIM_RUNTIME_RULES_ARTIFACT_PATH`
  default: `data/runtime/runtime_rules.json`
- `POLICYNIM_RUNTIME_EVIDENCE_DB_PATH`
  default: `data/runtime/runtime_evidence.sqlite3`
- `POLICYNIM_RUNTIME_SHELL_TIMEOUT_SECONDS`
  default: `300`
- `POLICYNIM_EVAL_UI_PORT`
- `POLICYNIM_EVAL_WORKSPACE_DIR`

Policy-backed regeneration reuses the same NVIDIA chat settings and eval
workspace setting. It does not add a regeneration-specific environment variable
or artifact directory.

## Optional NVIDIA Eval Packages

The default development install does not include NeMo Evaluator or NeMo Agent
Toolkit evaluation packages. Install them only when you need the optional
`nemo_evaluator` or `nat` backend paths:

```bash
uv sync --extra nvidia-eval --group test --group dev
```

The `nvidia-eval` extra pins `nemo-evaluator==0.2.5`,
`nvidia-simple-evals==26.3`, and `nvidia-nat-eval==1.6.0`. The adapters import
those packages lazily, so offline CI and the default local workflow do not need
them.

NeMo Evaluator Launcher is available as a second project extra:

```bash
uv sync --extra nvidia-eval --extra nvidia-eval-launcher --group test --group dev
```

The `nvidia-eval-launcher` extra pins `nemo-evaluator-launcher==0.2.4` and
`nvidia-nat[eval]==1.6.0`. The project keeps `httpx==0.27.2` because that is the
compatible launcher stack version; default CI does not sync this extra.

## Optional NVIDIA Guardrails Package

The default development install does not include NeMo Guardrails. PolicyNIM has
an internal output-rail wrapper for generated preflight drafts, but it does not add a
CLI flag, MCP tool, eval backend, or default factory switch. Install the package
only when directly constructing the internal Guardrails-backed generator:

```bash
uv sync --extra nvidia-guardrails --group test --group dev
```

The `nvidia-guardrails` extra pins `nemoguardrails[nvidia]==0.21.0`. The adapter
imports Guardrails lazily, so offline CI and the default local workflow do not
need the package.

## Default Model References

The default example configs use:

- embeddings:
  [`nvidia/llama-nemotron-embed-1b-v2`](https://docs.api.nvidia.com/nim/reference/nvidia-llama-nemotron-embed-1b-v2)
- reranking:
  [`nvidia/llama-nemotron-rerank-1b-v2`](https://docs.api.nvidia.com/nim/reference/nvidia-llama-nemotron-rerank-1b-v2-infer)
- grounded generation:
  [NVIDIA LLM API reference](https://docs.api.nvidia.com/nim/reference/llm-apis)

## Contributor Workflow

Install pre-commit once:

```bash
uv run --group dev pre-commit install
```

Run the standard quality gates:

```bash
uv run ruff check .
uv run pyright
uv run pytest -q -m "not live and not docker_live"
uv lock --check
uv build --out-dir dist
uv run policynim doctor --format json
uv run policynim support-bundle
```

For live or hosted-only checks, use the coverage notes in
[../tests/README.md](../tests/README.md).

## Client Setup Examples

Use the hosted-first examples unless you specifically need a local MCP server:

- [../examples/codex/README.md](../examples/codex/README.md)
- [../examples/claude-code/README.md](../examples/claude-code/README.md)

Use [workflows.md](workflows.md) for the command handbook after local setup is
done.
