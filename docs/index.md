# PolicyNIM Docs

This repo now splits onboarding, contributor setup, workflows, and hosted
operations into separate pages so the root README can stay short.

## Start Here

- [../README.md](../README.md): concise project overview, current capabilities,
  quickstart, and links outward
- [../CHANGELOG.md](../CHANGELOG.md): versioned release history and pending
  release notes
- [contributor-guide.md](contributor-guide.md): local setup, standalone init,
  env templates, important settings, model references, and quality gates
- [workflows.md](workflows.md): CLI, route/preflight, MCP, runtime/evidence, eval, and
  troubleshooting handbook
- [agent-workflows.md](agent-workflows.md): copy-paste coding-agent prompts and
  integration recipes for hosted MCP, `policy_preflight`, `policy_search`, MCP
  setup smoke, and issue diagnostics
- [hosted-beta-operations.md](hosted-beta-operations.md): hosted beta quickstart,
  recovery, container build flow, and Railway deployment notes
- [release.md](release.md): package, GitHub release, PyPI, and standalone
  installer release checklist
- [oss-readiness-audit.md](oss-readiness-audit.md): developer journey audit,
  OSS-readiness priorities, current evidence map, and
  `scripts/oss_readiness_check.py` launch proof status
- [public-launch-runbook.md](public-launch-runbook.md): ordered maintainer
  workflow for collecting external public-launch proof
- [../CONTRIBUTING.md](../CONTRIBUTING.md): contribution workflow,
  verification expectations, and PR evidence checklist
- [../SECURITY.md](../SECURITY.md): private vulnerability reporting and secret
  redaction guidance
- [../SUPPORT.md](../SUPPORT.md): issue routing, support-bundle guidance, and
  response expectations
- [maintainer-triage.md](maintainer-triage.md): issue labels, repository topics,
  priority routing, and maintainer response rules
- [../.github/ISSUE_TEMPLATE/public_launch_evidence.yml](../.github/ISSUE_TEMPLATE/public_launch_evidence.yml):
  Public launch evidence issue form for strict readiness output, generated
  launch issues, attached proof records, and remaining external proof
- [../CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md): community standards and
  enforcement expectations

## First-Run Paths

- Hosted MCP: use [the README hosted path](../README.md#start-here-pick-a-path)
  when you want Codex or Claude Code to call PolicyNIM without cloning the repo
  or building a local index.
- Local CLI: use [the README local CLI path](../README.md#start-here-pick-a-path)
  when you want a terminal-first preflight over a local policy corpus.
- Source checkout: use [the README contributor path](../README.md#start-here-pick-a-path)
  when you want to change PolicyNIM itself or run the local test suite.
- High-value agent workflows:
  [README examples](../README.md#high-value-agent-workflows) and
  [agent workflow recipes](agent-workflows.md) show when to call
  `policy_preflight`, when to call `policy_search`, how to smoke MCP config,
  and what diagnostics to attach when setup fails. The
  [workflow handbook patterns](workflows.md#coding-agent-workflow-patterns)
  keep the same examples near the full CLI reference.

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
- [roadmap.md](roadmap.md): current roadmap, non-promises, and public adoption
  priorities
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
