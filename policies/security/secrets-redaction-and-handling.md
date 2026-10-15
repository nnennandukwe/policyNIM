---
policy_id: SEC-SECRET-001
title: Secrets Redaction and Handling
doc_type: security-standard
domain: security
tags: [sec-secret-001, security, review]
grounded_in:
  - https://github.com/OWASP/ASVS/blob/5cf9b032440be53ce345ab3c130fda46ba1ce7a2/5.0/en/0x23-V14-Data-Protection.md
  - https://github.com/OWASP/ASVS/blob/5cf9b032440be53ce345ab3c130fda46ba1ce7a2/5.0/en/0x25-V16-Security-Logging-and-Error-Handling.md
  - https://github.com/OWASP/CheatSheetSeries/blob/72865b1e3b5754246a101778dc00b37f9c233373/cheatsheets/Logging_Cheat_Sheet.md
  - https://github.com/OWASP/CheatSheetSeries/blob/72865b1e3b5754246a101778dc00b37f9c233373/cheatsheets/Secrets_Management_Cheat_Sheet.md
---

# Secrets Redaction and Handling

## Intent

Keep credentials within the smallest runtime boundary and keep safe diagnostics useful without exposing sensitive data.

## Applicability

Roles: application-server, mcp-client, mcp-server, mcp-proxy, deployment-operator. Apply only to the behavior described below; a combined deployment can hold several roles.

## Required Rules

- **SECRET-1** Deliver secrets through an access-controlled secret source; don't commit credentials or place them in URLs, screenshots, or shared fixtures. Support: CS-SECRET:2 General Secrets Management; ASVS-DATA:14.2.1; PolicyNIM decision.
- **SECRET-2** Remove or mask credentials before logs, traces, analytics, exception text, or public responses; retain safe event fields instead of raw request or token bodies. Support: ASVS-LOG:16.2.5; ASVS-LOG:16.5.1; CS-LOG:Data to exclude.
- **SECRET-3** Document rotation and revocation for each external credential and validate required secrets through the central settings surface at startup. Support: CS-SECRET:2 General Secrets Management; PolicyNIM decision.
- **SECRET-4** Use obvious mock values in examples and regression fixtures; verify redaction with synthetic canaries through success and failure paths. Support: PolicyNIM decision.

## Recommended Guidance

- **SECRET-5** Minimize sensitive token claims and tool-result fields to the application's documented protection requirements. Support: ASVS-DATA:14.1.1; ASVS-DATA:14.1.2; ASVS-DATA:14.2.6.

## Review and Test Expectations

Inspect request, error, tracing, and evaluation artifacts after rejected credentials and downstream failures; confirm synthetic canaries never appear. Check rotation rollback without printing values. These are review expectations for this PolicyNIM adaptation.

## Exceptions and Boundaries

A public configuration key name can appear in diagnostics. Its secret value can't. A secret manager doesn't remove the need for startup validation or output redaction. Any deviation from a project requirement needs an owner, reason, compensating control, and review date (PolicyNIM decision); it can't waive an applicable upstream obligation.

## Examples

Applicable: A failed token exchange records the error class and request ID while stripping its synthetic bearer credential.

Violation: A debug exception copies the Authorization header into a trace attachment.

## Sources and Change Notice

This policy is PolicyNIM guidance adapted from the pinned sources below. Required Rules are requirements of this project baseline; recommended guidance retains that lower strength. A PolicyNIM decision isn't an upstream mandate. The referenced ASVS controls are selected controls, with their original levels retained upstream; this policy doesn't establish an ASVS verification level or certification.

- ASVS-DATA: [OWASP ASVS 5.0.0 ASVS-DATA](https://github.com/OWASP/ASVS/blob/5cf9b032440be53ce345ab3c130fda46ba1ce7a2/5.0/en/0x23-V14-Data-Protection.md); sections: 14.1.1, 14.1.2, 14.2.1, 14.2.3, 14.2.6.
- ASVS-LOG: [OWASP ASVS 5.0.0 ASVS-LOG](https://github.com/OWASP/ASVS/blob/5cf9b032440be53ce345ab3c130fda46ba1ce7a2/5.0/en/0x25-V16-Security-Logging-and-Error-Handling.md); sections: 16.2.5, 16.3.1, 16.3.2, 16.4.1, 16.5.1, 16.5.3.
- CS-LOG: [OWASP Logging Cheat Sheet](https://github.com/OWASP/CheatSheetSeries/blob/72865b1e3b5754246a101778dc00b37f9c233373/cheatsheets/Logging_Cheat_Sheet.md); sections: Data to exclude.
- CS-SECRET: [OWASP Secrets Management Cheat Sheet](https://github.com/OWASP/CheatSheetSeries/blob/72865b1e3b5754246a101778dc00b37f9c233373/cheatsheets/Secrets_Management_Cheat_Sheet.md); sections: 2 General Secrets Management.

Attribution: OWASP ASVS contributors, version 5.0.0; OWASP Cheat Sheet Series contributors. Changes: selected guidance restated, role boundaries, project requirements, examples, and review scenarios added.

This adapted policy text is available under [CC BY-SA 4.0](../licenses/CC-BY-SA-4.0.txt). Upstream MCP licensing and retained notices appear in [MCP upstream notice](../licenses/MCP-UPSTREAM.txt); no per-contributor relicensing status is asserted. Repository software licensing is separate.
