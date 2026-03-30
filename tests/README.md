# Test Plan

Current automated coverage includes:

- Markdown policy parsing, metadata normalization, and deterministic chunking
- Additional Day 6 chunking edge cases around blank sections and repeated nested headings
- Day 3 ingest orchestration with local LanceDB rebuild behavior
- Day 3 search orchestration with domain filters and missing-index handling
- Day 6 eval orchestration, rerank on/off comparison, and isolated live-eval index handling
- Day 4 grounded preflight orchestration, citation validation, and fallback behavior
- Day 6 citation-deduplication and policy-vs-draft citation validation edge cases
- NVIDIA response-validation coverage for malformed grounded-generation and reranking payloads
- CLI output for `ingest`, JSON-first `search`, and JSON-first `preflight`
- CLI output for `eval`
- MCP tool parity for `policy_preflight` and `policy_search`
- MCP startup wiring for `stdio` and `streamable-http`
- Hosted HTTP `/healthz` readiness checks and the optional bearer-auth wrapper
- Opt-in live NVIDIA embedding smoke coverage behind `NVIDIA_API_KEY`
