# PolicyNIM Docs

This repo now splits onboarding, contributor setup, workflows, and hosted
operations into separate pages so the root README can stay short.

## Start Here

- [../README.md](../README.md): concise project overview, current capabilities,
  quickstart, local diagnostics, and links outward
- [contributor-guide.md](contributor-guide.md): local setup, standalone init,
  env templates, important settings, model references, and quality gates
- [workflows.md](workflows.md): CLI, quickstart, diagnostics, route/preflight,
  MCP, runtime/evidence, eval, and troubleshooting handbook
- [hosted-beta-operations.md](hosted-beta-operations.md): hosted beta quickstart,
  recovery, container build flow, and Railway deployment notes
- [release.md](release.md): package, GitHub release, PyPI, and standalone
  installer release checklist

## Examples

- [../examples/codex/README.md](../examples/codex/README.md): hosted-first Codex setup
- [../examples/claude-code/README.md](../examples/claude-code/README.md): hosted-first Claude Code setup

## Architecture And Product Context

- [architecture.md](architecture.md): package boundaries, runtime flow, and
  interface rules
- [architecture-diagram.md](architecture-diagram.md): Mermaid diagrams for the
  current package layout and runtime flow
- [demo-script.md](demo-script.md): walk through the hero use case live
- [limitations.md](limitations.md): current product limits and non-goals
- [public-source-grounding.md](public-source-grounding.md): provenance notes for
  the shipped sample corpus

## Talks And Workflow Notes

- [ai-engineer-miami-context-plane.md](ai-engineer-miami-context-plane.md):
  centralized context-plane talk notes and project framing
- [extreme-programming-with-agents.md](extreme-programming-with-agents.md):
  XP, TDD, and agent workflow notes

## Testing And Coverage

- [../tests/README.md](../tests/README.md): current automated coverage and the
  opt-in live and Docker test knobs
