---
policy_id: SEC-EGRESS-001
title: SSRF and Egress Boundaries
doc_type: security-standard
domain: security
tags: [sec-egress-001, security, review]
grounded_in:
  - https://github.com/OWASP/ASVS/blob/5cf9b032440be53ce345ab3c130fda46ba1ce7a2/5.0/en/0x10-V1-Encoding-and-Sanitization.md
  - https://github.com/OWASP/CheatSheetSeries/blob/72865b1e3b5754246a101778dc00b37f9c233373/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.md
  - https://github.com/modelcontextprotocol/modelcontextprotocol/blob/5f5440bb26a62e2cf3440b92da5a667efa03b267/docs/docs/2026-07-28/tutorials/security/security_best_practices.mdx
---

# SSRF and Egress Boundaries

## Intent

Server-side request forgery (SSRF) makes a service fetch attacker-chosen destinations using its own network access. Cover tool URLs, OAuth discovery, metadata, and redirects.

## Applicability

Roles: application-server, mcp-client, mcp-proxy, deployment-operator. Apply only to the behavior described below; a combined deployment can hold several roles.

## Required Rules

- **EGRESS-1** Validate outbound schemes, hosts, ports, and resolved addresses against the integration's documented destination policy before fetching untrusted URLs. Support: ASVS-ENCODE:1.3.6; MCP-SECURITY:Server-Side Request Forgery (SSRF).
- **EGRESS-2** Apply destination checks to every redirect and the address actually used to connect; block private, loopback, link-local, and cloud-metadata targets unless explicitly authorized for that integration. Support: MCP-SECURITY:Server-Side Request Forgery (SSRF); PolicyNIM decision.
- **EGRESS-3** Require HTTPS for production OAuth URLs and reject executable schemes; keep loopback development exceptions explicit and isolated from production. Support: MCP-SECURITY:OAuth Authorization URL Validation; MCP-SECURITY:Server-Side Request Forgery (SSRF).

## Recommended Guidance

- **EGRESS-4** Use maintained URL/IP parsers and network egress enforcement alongside application checks; account for IPv6, alternative address forms, and DNS rebinding. Support: MCP-SECURITY:Server-Side Request Forgery (SSRF); CS-SSRF:Case 2 - Application can send requests to ANY external IP address or domain name.

## Review and Test Expectations

Test public-to-private redirects, metadata addresses, IPv4-mapped IPv6, DNS answer changes between validation and connect, and malicious authorization URLs without making real external requests. These are review expectations for this PolicyNIM adaptation.

## Exceptions and Boundaries

An approved private API needs a narrow destination rule and equivalent redirect/DNS protections; a development loopback exception doesn't authorize arbitrary internal network access. Any deviation from a project requirement needs an owner, reason, compensating control, and review date (PolicyNIM decision); it can't waive an applicable upstream obligation.

## Examples

Applicable: Discovery metadata redirects to a link-local metadata endpoint; the client rejects the redirect before connecting.

Violation: A public hostname passes validation but reconnects to a private address after DNS rebinding.

## Sources and Change Notice

This policy is PolicyNIM guidance adapted from the pinned sources below. Required Rules are requirements of this project baseline; recommended guidance retains that lower strength. A PolicyNIM decision isn't an upstream mandate. The referenced ASVS controls are selected controls, with their original levels retained upstream; this policy doesn't establish an ASVS verification level or certification.

- ASVS-ENCODE: [OWASP ASVS 5.0.0 ASVS-ENCODE](https://github.com/OWASP/ASVS/blob/5cf9b032440be53ce345ab3c130fda46ba1ce7a2/5.0/en/0x10-V1-Encoding-and-Sanitization.md); sections: 1.1.2, 1.2.1, 1.2.4, 1.2.5, 1.3.6.
- CS-SSRF: [OWASP Server Side Request Forgery Prevention Cheat Sheet](https://github.com/OWASP/CheatSheetSeries/blob/72865b1e3b5754246a101778dc00b37f9c233373/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.md); sections: Case 1 - Application can send request only to identified and trusted applications, Case 2 - Application can send requests to ANY external IP address or domain name.
- MCP-SECURITY: [MCP 2026-07-28 Security Best Practices](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/5f5440bb26a62e2cf3440b92da5a667efa03b267/docs/docs/2026-07-28/tutorials/security/security_best_practices.mdx); sections: Confused Deputy Problem, Token Passthrough, Server-Side Request Forgery (SSRF), State Handle Hijacking, Local MCP Server Compromise, OAuth Authorization URL Validation, stdio Transport Security in Proxy Scenarios, Scope Minimization.

Attribution: Model Context Protocol contributors; OWASP ASVS contributors, version 5.0.0; OWASP Cheat Sheet Series contributors. Changes: selected guidance restated, role boundaries, project requirements, examples, and review scenarios added.

This adapted policy text is available under [CC BY-SA 4.0](../licenses/CC-BY-SA-4.0.txt). Upstream MCP licensing and retained notices appear in [MCP upstream notice](../licenses/MCP-UPSTREAM.txt); no per-contributor relicensing status is asserted. Repository software licensing is separate.
