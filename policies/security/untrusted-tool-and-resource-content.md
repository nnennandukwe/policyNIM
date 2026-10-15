---
policy_id: MCP-CONTENT-001
title: Untrusted Tool and Resource Content
doc_type: security-standard
domain: security
tags: [mcp-content-001, security, review]
grounded_in:
  - https://github.com/modelcontextprotocol/modelcontextprotocol/blob/5f5440bb26a62e2cf3440b92da5a667efa03b267/docs/specification/2026-07-28/server/tools.mdx
---

# Untrusted Tool and Resource Content

## Intent

Tool descriptions, annotations, resources, and retrieved text can contain attacker instructions. Treat returned material as evidence data within an established authority boundary.

## Applicability

Roles: mcp-client, mcp-server, mcp-proxy. Apply only to the behavior described below; a combined deployment can hold several roles.

## Required Rules

- **CONTENT-1** Treat tool annotations from untrusted servers as untrusted; don't use a claimed read-only or safe annotation as authorization for an operation. Support: MCP-TOOLS:Tool; PolicyNIM decision.
- **CONTENT-2** Keep tool/resource content separate from governing instructions; embedded requests to disclose secrets, change policy, or invoke another tool grant no authority. Support: PolicyNIM decision.
- **CONTENT-3** Preserve source identity and evidence location when using retrieved content; reject fabricated citations or claims that lack supporting evidence before presenting them as grounded. Support: PolicyNIM decision.
- **CONTENT-4** Validate and sanitize results at their consumption boundary and recheck permission before any action suggested by returned content. Support: MCP-TOOLS:Security Considerations; PolicyNIM decision.

## Recommended Guidance

- **CONTENT-5** Use adversarial fixtures with embedded instructions, misleading annotations, and mixed valid/invalid evidence; record observed failures without claiming universal prompt-injection resistance. Support: PolicyNIM decision.

## Review and Test Expectations

Include a retrieved policy paragraph instructing the agent to send a mock secret to an attacker URL. Verify the paragraph can be cited as hostile data while no requested transfer gains authority. These are review expectations for this PolicyNIM adaptation.

## Exceptions and Boundaries

A trusted server can still relay untrusted upstream content. Schema validity and a correct citation don't turn that content into an instruction. PolicyNIM content alone doesn't enforce this boundary. Any deviation from a project requirement needs an owner, reason, compensating control, and review date (PolicyNIM decision); it can't waive an applicable upstream obligation.

## Examples

Applicable: A retrieved document says to ignore prior rules and export a token; the client treats that text as hostile evidence and denies the transfer.

Violation: An agent follows an instruction embedded in a search result because the result came from an authenticated server.

## Sources and Change Notice

This policy is PolicyNIM guidance adapted from the pinned sources below. Required Rules are requirements of this project baseline; recommended guidance retains that lower strength. A PolicyNIM decision isn't an upstream mandate. The referenced ASVS controls are selected controls, with their original levels retained upstream; this policy doesn't establish an ASVS verification level or certification.

- MCP-TOOLS: [MCP 2026-07-28 Tools](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/5f5440bb26a62e2cf3440b92da5a667efa03b267/docs/specification/2026-07-28/server/tools.mdx); sections: User Interaction Model, Tool, Output Schema, Security Considerations.

Attribution: Model Context Protocol contributors. Changes: selected guidance restated, role boundaries, project requirements, examples, and review scenarios added.

This adapted policy text is available under [CC BY-SA 4.0](../licenses/CC-BY-SA-4.0.txt). Upstream MCP licensing and retained notices appear in [MCP upstream notice](../licenses/MCP-UPSTREAM.txt); no per-contributor relicensing status is asserted. Repository software licensing is separate.
