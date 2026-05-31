# PolicyNIM Agent Workflows

Use this page after the hosted MCP or local MCP setup succeeds. The goal is to
make PolicyNIM part of the agent loop before code changes happen, not a report
you read after the agent has already guessed.

## Start From Hosted MCP

When you have the hosted MCP URL, the shortest setup path is the browser token
flow plus one client command.

1. Open `https://<railway-domain>/mcp` in a browser. The hosted service routes
   browser visits to `/beta` so you can sign in with GitHub and create or rotate
   a token.
2. Export the token in the shell that launches your coding agent:

```bash
export POLICYNIM_TOKEN='<generated-beta-token>'
```

3. Add the hosted MCP server.

Codex:

```bash
codex mcp add policynim --url 'https://<railway-domain>/mcp' --bearer-token-env-var POLICYNIM_TOKEN
```

Claude Code:

```bash
claude mcp add --transport http policynim 'https://<railway-domain>/mcp' --header "Authorization: Bearer $POLICYNIM_TOKEN"
```

4. Ask the agent to list tools before the first real task:

```text
List the PolicyNIM MCP tools and confirm policy_preflight and policy_search are available before starting implementation.
```

If you installed the CLI, generate the same commands from the tested contract.
Run the command for the selected client:

Codex:

```bash
policynim quickstart --target hosted-mcp --client codex --hosted-url 'https://<railway-domain>/mcp' --format json
```

Claude Code:

```bash
policynim quickstart --target hosted-mcp --client claude-code --hosted-url 'https://<railway-domain>/mcp' --format json
```

The JSON includes `client_commands` for the selected client and
`agent_workflows` prompts you can paste into the agent chat.

## When To Call Each Tool

| Moment | Tool | Agent prompt |
| --- | --- | --- |
| Before implementation | `policy_preflight` | `Before editing, call policy_preflight for: Implement a refresh-token cleanup background job. Use the cited constraints in your implementation plan. If the result is insufficient_context, stop and call policy_search with a narrower query before changing files.` |
| Review or CI failure | `policy_search` | `Use policy_search for: release installer checksum verification. Summarize the relevant cited policy lines before proposing a fix.` |
| Release or automation change | `policy_preflight` | `Before editing, call policy_preflight for: Add a GitHub release smoke check for the installer. Use the cited constraints before changing workflow files.` |
| MCP setup check | tool list | `List the PolicyNIM MCP tools and confirm policy_preflight and policy_search are available before starting implementation.` |

Ask your agent to call `policy_preflight` before it edits code when the task
touches security, release automation, runtime behavior, CI gates, or any workflow
where policy evidence should shape the implementation plan.

Use `policy_search` when the agent needs raw citations for a question, review
comment, failing gate, or unclear policy boundary. If PolicyNIM returns
`insufficient_context`, stop and call `policy_search` with a narrower query
before asking the agent to continue.

## Preflight Before Implementation

Paste this at the start of an implementation run:

```text
Before editing, call policy_preflight for: Implement a refresh-token cleanup background job.
Use the cited constraints in your implementation plan. If the result is insufficient_context, stop and call policy_search with a narrower query before changing files.
```

CLI equivalent:

```bash
policynim preflight --task "Implement a refresh-token cleanup background job" --top-k 5
```

Good agent behavior after this call:

- cites the policy evidence it is using
- names review flags or tests required by the policy packet
- stops instead of inventing guidance when grounding is weak

## Debug Review Feedback

Use `policy_search` when you need the underlying evidence instead of a generated
implementation plan:

```text
Use policy_search for: release installer checksum verification.
Summarize the relevant cited policy lines before proposing a fix.
```

CLI equivalent:

```bash
policynim search --query "release installer checksum verification" --top-k 5
```

This is the right path for review comments, release gates, support triage, and
questions like "what policy says this CI check must stay offline?"

## Smoke MCP Before A Long Session

For local MCP, prove the generated config can list tools before a long agent run:

```bash
policynim mcp-config --target local-stdio --client codex --format json > codex-mcp-config.json
policynim mcp-smoke --mcp-config-file codex-mcp-config.json --format json
```

Then ask the agent:

```text
List the PolicyNIM MCP tools and confirm policy_preflight and policy_search are available before starting implementation.
```

For hosted MCP, the equivalent confidence check is the client tool-list prompt
after exporting `POLICYNIM_TOKEN` and adding the server.

## Diagnostics For Issues

When setup fails, do not paste raw config, local paths, or token-bearing command
output into a public issue. Use `policynim support-bundle --include-mcp-smoke`
to generate the redacted support bundle:

```bash
policynim support-bundle --include-mcp-smoke
```

Attach that JSON to the issue. It includes first-run targets, generated
quickstart commands, hosted client command metadata, agent workflow prompts, and
optional local MCP smoke output while redacting secrets and local path prefixes.

## Safety Rules

- Do not paste bearer tokens into prompts, issues, logs, or MCP config JSON.
- Hosted MCP does not require `policynim init` or `policynim ingest`.
- Local CLI and local MCP need `NVIDIA_API_KEY` plus `policynim ingest` before
  real `policy_preflight` or `policy_search` calls.
- Use `policy_preflight` for implementation planning; use `policy_search` for
  raw evidence and debugging.
- Treat `insufficient_context` as a stop signal. Narrow the query, add a domain,
  or add policy corpus coverage before continuing.
