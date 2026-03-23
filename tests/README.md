# Test Plan

Current automated coverage includes:

- Markdown policy parsing, metadata normalization, and deterministic chunking
- Day 3 ingest orchestration with local LanceDB rebuild behavior
- Day 3 search orchestration with domain filters and missing-index handling
- CLI output for `ingest` and JSON-first `search`
- Opt-in live NVIDIA embedding smoke coverage behind `NVIDIA_API_KEY`
