# Public-Source Grounding Notes

The PolicyNIM source corpus contains project guidance informed by public
standards and references. Maintained security adaptations carry pinned sources
and licenses; the engineering baseline carries reviewed source references and explicit project
decisions. SEC-PUBLIC-ENDPOINT-002 retains its legacy unknown provenance. No proprietary handbook is included.

Each policy below maps the shipped document to its public grounding and explains
how the repo uses that source material.

## Engineering Topics

The [engineering coverage matrix](engineering-policy-coverage.md) maps testing, APIs, data, reliability, observability, and supply-chain guidance to exact source sections and authored scenarios. Each topic has a local manifest; the root aggregate references the same source files.

The original architecture/backend IDs remain while their files move into topic directories. API compatibility uses SemVer 2.0.0 within its stated package scope and explicit PolicyNIM API decisions. Background-job and deadline guidance refers to the reviewed AWS retry article. Configuration and logging retain pinned OWASP references. Correlation uses the W3C Trace Context Recommendation and the stable OpenTelemetry Logs Data Model at an exact source commit.

All new engineering requirements label project choices. Source availability doesn't imply a reuse grant; no Google SRE or AWS prose is redistributed. Source attribution, reuse terms, and change notices stay in the manifests and each selected root's license notices.

## Security Policies

The maintained security expansion preserves `SEC-AUTH-001`, `SEC-SECRET-001`,
and `SEC-SESSION-001`, and adds six focused application/MCP policies. See the
[security coverage matrix](security-mcp-coverage.md) for exact editions, control
references, role boundaries, paired scenarios, source notices, and omissions.
Their adaptation metadata is declared in `policies/pack.toml`; it doesn't establish
which generation an existing index serves.

`SEC-PUBLIC-ENDPOINT-002` retains its existing safe readiness-response guidance
and unpinned legacy references to OWASP error/logging and Twelve-Factor config
material. This change doesn't invent a reviewed revision or license for those
legacy declarations. The moved architecture/backend policies now carry the
reviewed, pinned references described in the engineering section above.

## Template Note

`policies/TEMPLATE.md` is authoring guidance rather than part of the runtime corpus.
Its example `grounded_in` entry shows the expected shape for future policy
documents.

## Why This Matters

- It keeps the repo public-safe.
- It makes it clear that the corpus is adapted from public standards rather than
  copied from an internal handbook.
- It gives contributors a canonical place to extend provenance notes as new sample
  policies are added.
