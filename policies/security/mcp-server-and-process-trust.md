---
policy_id: MCP-TRUST-001
title: MCP Server and Process Trust
doc_type: security-standard
domain: security
tags: [mcp-trust-001, security, review]
grounded_in:
  - https://github.com/OWASP/ASVS/blob/5cf9b032440be53ce345ab3c130fda46ba1ce7a2/5.0/en/0x24-V15-Secure-Coding-and-Architecture.md
  - https://github.com/modelcontextprotocol/modelcontextprotocol/blob/5f5440bb26a62e2cf3440b92da5a667efa03b267/docs/docs/2026-07-28/tutorials/security/security_best_practices.mdx
---

# MCP Server and Process Trust

## Intent

A local MCP server is executable code running with the launch context's privileges. Transport selection, hosted authorization, and operating-system isolation protect different boundaries.

## Applicability

Roles: mcp-client, mcp-proxy, deployment-operator. Apply only to the behavior described below; a combined deployment can hold several roles.

## Required Rules

- **TRUST-1** Before one-click local server configuration executes code, show the full command and arguments, identify the execution risk, obtain explicit approval, and allow cancellation. Support: MCP-SECURITY:Local MCP Server Compromise.
- **TRUST-2** Record server/dependency origin and reviewed versions, including transitive dependencies; apply a documented vulnerability remediation policy. Support: ASVS-DEPENDENCY:15.1.1; ASVS-DEPENDENCY:15.1.2; ASVS-DEPENDENCY:15.2.1; ASVS-DEPENDENCY:15.2.4.
- **TRUST-3** Document the actual filesystem, network, environment, and process privileges of each deployment; don't label stdio or OAuth as an execution sandbox. Support: PolicyNIM decision.
- **TRUST-4** A proxy that can spawn stdio processes must authorize process creation and constrain allowed commands independently from a remote server's tool descriptions. Support: MCP-SECURITY:stdio Transport Security in Proxy Scenarios; PolicyNIM decision.

## Recommended Guidance

- **TRUST-5** Run local servers with restricted resources and an appropriate maintained sandbox; avoid unnecessary inherited credentials and verify denied filesystem/network access through the chosen isolation mechanism. Support: MCP-SECURITY:Local MCP Server Compromise; PolicyNIM decision.

## Review and Test Expectations

Inspect executable origin and exact arguments; test cancel-before-launch, a changed executable, unauthorized proxy spawning, and actual denied file/network operations when claiming isolation. These are review expectations for this PolicyNIM adaptation.

## Exceptions and Boundaries

Direct stdio doesn't require the hosted HTTP OAuth exchange. A container or subprocess alone doesn't prove isolation; record its configured privileges and observed denial behavior. Any deviation from a project requirement needs an owner, reason, compensating control, and review date (PolicyNIM decision); it can't waive an applicable upstream obligation.

## Examples

Applicable: The host shows the pinned command, accepts cancellation without spawning, and tests the configured sandbox's filesystem restrictions.

Violation: A remote tool description makes a proxy spawn a new local executable and the operator claims stdio prevents file access.

## Sources and Change Notice

This policy is PolicyNIM guidance adapted from the pinned sources below. Required Rules are requirements of this project baseline; recommended guidance retains that lower strength. A PolicyNIM decision isn't an upstream mandate. The referenced ASVS controls are selected controls, with their original levels retained upstream; this policy doesn't establish an ASVS verification level or certification.

- ASVS-DEPENDENCY: [OWASP ASVS 5.0.0 ASVS-DEPENDENCY](https://github.com/OWASP/ASVS/blob/5cf9b032440be53ce345ab3c130fda46ba1ce7a2/5.0/en/0x24-V15-Secure-Coding-and-Architecture.md); sections: 15.1.1, 15.1.2, 15.2.1, 15.2.4.
- MCP-SECURITY: [MCP 2026-07-28 Security Best Practices](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/5f5440bb26a62e2cf3440b92da5a667efa03b267/docs/docs/2026-07-28/tutorials/security/security_best_practices.mdx); sections: Confused Deputy Problem, Token Passthrough, Server-Side Request Forgery (SSRF), State Handle Hijacking, Local MCP Server Compromise, OAuth Authorization URL Validation, stdio Transport Security in Proxy Scenarios, Scope Minimization.

Attribution: Model Context Protocol contributors; OWASP ASVS contributors, version 5.0.0. Changes: selected guidance restated, role boundaries, project requirements, examples, and review scenarios added.

This adapted policy text is available under [CC BY-SA 4.0](../licenses/CC-BY-SA-4.0.txt). Upstream MCP licensing and retained notices appear in [MCP upstream notice](../licenses/MCP-UPSTREAM.txt); no per-contributor relicensing status is asserted. Repository software licensing is separate.
