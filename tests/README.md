# Test Plan

Current automated coverage includes:

- Markdown policy parsing, metadata normalization, and deterministic chunking
- Day 3 ingest orchestration with local LanceDB rebuild behavior
- Day 3 search orchestration with domain filters and missing-index handling
- Day 4 grounded preflight orchestration, citation validation, and fallback behavior
- CLI output for `ingest`, JSON-first `search`, and JSON-first `preflight`
- MCP tool parity for `policy_preflight` and `policy_search`
- MCP startup wiring for `stdio` and `streamable-http`
- Opt-in live NVIDIA embedding smoke coverage behind `NVIDIA_API_KEY`
