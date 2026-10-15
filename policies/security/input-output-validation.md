---
policy_id: SEC-INPUT-001
title: Input and Output Validation
doc_type: security-standard
domain: security
tags: [sec-input-001, security, review]
grounded_in:
  - https://github.com/OWASP/ASVS/blob/5cf9b032440be53ce345ab3c130fda46ba1ce7a2/5.0/en/0x10-V1-Encoding-and-Sanitization.md
  - https://github.com/OWASP/ASVS/blob/5cf9b032440be53ce345ab3c130fda46ba1ce7a2/5.0/en/0x11-V2-Validation-and-Business-Logic.md
  - https://github.com/modelcontextprotocol/modelcontextprotocol/blob/5f5440bb26a62e2cf3440b92da5a667efa03b267/docs/specification/2026-07-28/server/tools.mdx
---

# Input and Output Validation

## Intent

Treat requests and downstream results as data with explicit schemas and contextual limits before they reach interpreters or users.

## Applicability

Roles: application-server, mcp-client, mcp-server, mcp-proxy. Apply only to the behavior described below; a combined deployment can hold several roles.

## Required Rules

- **INPUT-1** Validate untrusted input types, shape, ranges, and business constraints at trusted boundaries, including tool arguments. Support: ASVS-INPUT:2.1.1; ASVS-INPUT:2.2.1; ASVS-INPUT:2.2.2; MCP-TOOLS:Security Considerations.
- **INPUT-2** Use parameterized database and process APIs; apply output encoding at the target interpreter boundary rather than relying on input validation alone. Support: ASVS-ENCODE:1.1.2; ASVS-ENCODE:1.2.1; ASVS-ENCODE:1.2.4; ASVS-ENCODE:1.2.5.
- **INPUT-3** MCP servers declaring an outputSchema must return conforming structured results and sanitize tool outputs. Support: MCP-TOOLS:Output Schema; MCP-TOOLS:Security Considerations.

## Recommended Guidance

- **INPUT-4** MCP clients should validate declared structured results before passing them to the model; fail closed on a malformed result before any consequential action. Support: MCP-TOOLS:Output Schema; MCP-TOOLS:Security Considerations; PolicyNIM decision.

## Review and Test Expectations

Test malformed tool arguments, unexpected result types, output injection, and invalid business combinations at the real boundary. Preserve safe rejection evidence without echoing payload secrets. These are review expectations for this PolicyNIM adaptation.

## Exceptions and Boundaries

An internal producer still needs validation when its output crosses a trust boundary. A schema-valid string can contain malicious instructions; MCP-CONTENT-001 covers that separate risk. Any deviation from a project requirement needs an owner, reason, compensating control, and review date (PolicyNIM decision); it can't waive an applicable upstream obligation.

## Examples

Applicable: A tools/call request with a string where the schema requires an integer is rejected before an application write.

Violation: A server concatenates a schema-valid tool argument into a shell command.

## Sources and Change Notice

This policy is PolicyNIM guidance adapted from the pinned sources below. Required Rules are requirements of this project baseline; recommended guidance retains that lower strength. A PolicyNIM decision isn't an upstream mandate. The referenced ASVS controls are selected controls, with their original levels retained upstream; this policy doesn't establish an ASVS verification level or certification.

- ASVS-ENCODE: [OWASP ASVS 5.0.0 ASVS-ENCODE](https://github.com/OWASP/ASVS/blob/5cf9b032440be53ce345ab3c130fda46ba1ce7a2/5.0/en/0x10-V1-Encoding-and-Sanitization.md); sections: 1.1.2, 1.2.1, 1.2.4, 1.2.5, 1.3.6.
- ASVS-INPUT: [OWASP ASVS 5.0.0 ASVS-INPUT](https://github.com/OWASP/ASVS/blob/5cf9b032440be53ce345ab3c130fda46ba1ce7a2/5.0/en/0x11-V2-Validation-and-Business-Logic.md); sections: 2.1.1, 2.2.1, 2.2.2.
- MCP-TOOLS: [MCP 2026-07-28 Tools](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/5f5440bb26a62e2cf3440b92da5a667efa03b267/docs/specification/2026-07-28/server/tools.mdx); sections: User Interaction Model, Tool, Output Schema, Security Considerations.

Attribution: Model Context Protocol contributors; OWASP ASVS contributors, version 5.0.0. Changes: selected guidance restated, role boundaries, project requirements, examples, and review scenarios added.

This adapted policy text is available under [CC BY-SA 4.0](../licenses/CC-BY-SA-4.0.txt). Upstream MCP licensing and retained notices appear in [MCP upstream notice](../licenses/MCP-UPSTREAM.txt); no per-contributor relicensing status is asserted. Repository software licensing is separate.
