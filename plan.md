# PolicyNIM v1 MVP Plan

## Summary
- Build PolicyNIM as a policy-aware preflight layer for AI coding agents: the agent asks what standards apply before generating code, and PolicyNIM returns grounded guidance with citations.
- Ship a clean Python 3.11 + `uv` repo with two public entrypoints: CLI and MCP. Keep the architecture small, explicit, and tutorial-friendly.
- Make NVIDIA the visible core of the system: hosted NIM APIs for embeddings, reranking, and answer synthesis; local OSS storage only for the vector index.

## Product Surface
- CLI commands:
  - `policynim ingest` to parse Markdown policies, chunk them, embed them, and build the local index.
  - `policynim preflight --task "..."` as the hero workflow.
  - `policynim search --query "..."` as the debug/discovery workflow.
  - `policynim mcp --transport stdio|streamable-http` to run the MCP server.
- MCP tools:
  - `policy_preflight(task, domain?, top_k?)` returning structured policy guidance for agents.
  - `policy_search(query, domain?, top_k?)` returning raw retrieved policy chunks and citations.
- Core response types:
  - `PreflightResult` with `summary`, `applicable_policies`, `implementation_guidance`, `review_flags`, `tests_required`, `citations`, `insufficient_context`.
  - `SearchResult` with ranked chunks, scores, and citation metadata.
  - `Citation` with `policy_id`, `title`, `path`, `section`, `lines`, `chunk_id`.

## Architecture
- Keep the repo framework-light. Do not center it on LangChain or LlamaIndex; use a thin internal pipeline with typed services.
- Use a heading-aware Markdown corpus with YAML frontmatter. Each policy doc should include stable metadata like `policy_id`, `title`, `doc_type`, `domain`, `tags`, and a `grounded_in` field referencing public standards.
- Use chunking by Markdown section with line-span preservation and stable chunk IDs so citations are reproducible and easy to inspect.
- Use NVIDIA-hosted models by default:
  - LLM: `nvidia/llama-3.3-nemotron-super-49b-v1.5`
  - Embeddings: `nvidia/llama-nemotron-embed-1b-v2`
  - Reranker: `nvidia/llama-nemotron-rerank-1b-v2`
- Use a thin `NVIDIAClient` wrapper:
  - chat + embeddings via NVIDIA’s OpenAI-compatible endpoints
  - reranking via NVIDIA’s retrieval endpoint
  - model IDs configurable from settings
- Use LanceDB as the local vector store because it is embedded and runs in-process “like SQLite,” which keeps v1 local-first and easy to understand while preserving a future path to filtering and hybrid search: [LanceDB Quickstart](https://docs.lancedb.com/quickstart).
- Retrieval flow:
  - embed query
  - dense search top 15-20
  - rerank top candidates
  - keep top 4-6 chunks with document diversity
  - return insufficient-context instead of bluffing when evidence is weak
- Answer flow:
  - pass only retrieved chunks to the LLM
  - require chunk-ID citations in the generated output
  - validate that every citation maps to a retrieved chunk before returning results
  - render concise implementation guidance for agents, not open-ended chat
- MCP implementation:
  - use stable MCP Python SDK v1.x with FastMCP
  - support both `stdio` and `streamable-http`
  - document Codex-oriented local usage and Claude Code usage with one concrete example each

## Sample Corpus, Demo, and Repo Layout
- Initial corpus should be backend + security focused so the repo tells one coherent story. Target 8-10 sample docs, not a broad handbook.
- Required sample docs:
  - backend logging standard
  - auth-sensitive code review standard
  - API versioning guidance
  - background job design rules
  - secrets handling and redaction rules
  - observability/test expectations for backend services
  - one or two curated review-lesson docs grounded in public incidents or OWASP/SRE-style guidance
- The hero demo should be: “Implement a refresh-token cleanup background job.” PolicyNIM should surface auth review rules, background-job rules, logging/redaction constraints, and test expectations with citations.
- Recommended repo layout:
  - `src/policynim/` for ingest, retrieval, generation, interfaces, and types
  - `policies/` for the sample corpus and a policy template
  - `evals/` for gold queries and eval runner
  - `examples/codex/` and `examples/claude-code/` for MCP setup examples
  - `docs/` for architecture, corpus format, and demo script
  - `tests/` for unit tests and opt-in live integration tests
  - `data/` for local index artifacts, gitignored
  - `README.md`, `.env.example`, `policynim.toml.example`, `LICENSE`

## One-Week Build Plan
- Day 1:
  - scaffold the repo, settings, typed models, and service boundaries
  - lock the CLI/MCP response schema
  - write the first 4-5 sample policies before building too much code
- Day 2:
  - finish the sample corpus and policy template
  - implement Markdown loading, frontmatter parsing, chunking, and citation spans
- Day 3:
  - integrate embeddings and LanceDB indexing
  - implement `ingest` and `search`
- Day 4:
  - integrate reranking and grounded synthesis
  - implement citation validation and insufficient-context behavior
- Day 5:
  - implement `preflight`
  - add FastMCP server with `policy_preflight` and `policy_search`
  - add Codex and Claude Code example configs
- Day 6:
  - add eval set
  - add unit tests around chunking, citation mapping, response validation, and retrieval flow
  - run a rerank on/off comparison
- Day 7:
  - write README, architecture doc, demo script, limitations, and public-source grounding notes
  - add CI for lint + tests if the core pipeline is stable

## Test Plan
- Functional tests:
  - `ingest` builds an index from the sample corpus
  - `search` returns relevant chunks with stable citation metadata
  - `preflight` returns structured grounded guidance with citations
  - MCP tools return the same data contract as the CLI service layer
- Retrieval/eval tests:
  - 10-15 gold queries across logging, auth, API versioning, background jobs, and one no-answer case
  - measure recall@5 for expected source docs
  - compare rerank off vs rerank on
- Grounding tests:
  - every citation in `PreflightResult` must map to a retrieved chunk
  - no-answer case must return insufficient context rather than fabricated guidance
- Repo-quality tests:
  - default CI runs without NVIDIA secrets using unit tests and fixtures
  - live NVIDIA integration tests remain opt-in behind `NVIDIA_API_KEY`

## Assumptions and Defaults
- Python 3.11 + `uv` is the baseline.
- V1 optimizes for tutorial clarity with clean seams, not enterprise abstraction layers.
- CLI + MCP both ship in v1; REST is out of scope.
- The corpus is synthetic team policy content grounded in public standards, not copied proprietary material.
- NVIDIA usage is explicit and central; the only non-NVIDIA core dependency in the retrieval path is the local vector store.
- If model availability changes, the model IDs remain configurable and the abstraction stays the same.
- If schedule slips, keep `policy_preflight` and `policy_search`, but cut Docker and any extra interface surface before cutting grounding or citations.

## References
- NVIDIA API Quickstart: [docs.api.nvidia.com/nim/docs/api-quickstart](https://docs.api.nvidia.com/nim/docs/api-quickstart)
- NVIDIA Retrieval APIs: [docs.api.nvidia.com/nim/reference/retrieval-apis](https://docs.api.nvidia.com/nim/reference/retrieval-apis)
- NVIDIA RAG Blueprint: [build.nvidia.com/nvidia/build-an-enterprise-rag-pipeline/blueprintcard](https://build.nvidia.com/nvidia/build-an-enterprise-rag-pipeline/blueprintcard)
- NVIDIA LLM model card: [build.nvidia.com/nvidia/llama-3_3-nemotron-super-49b-v1_5/modelcard](https://build.nvidia.com/nvidia/llama-3_3-nemotron-super-49b-v1_5/modelcard)
- NVIDIA embedding model card: [build.nvidia.com/nvidia/llama-nemotron-embed-1b-v2/modelcard](https://build.nvidia.com/nvidia/llama-nemotron-embed-1b-v2/modelcard)
- NVIDIA rerank model card: [build.nvidia.com/nvidia/llama-nemotron-rerank-1b-v2/modelcard](https://build.nvidia.com/nvidia/llama-nemotron-rerank-1b-v2/modelcard)
- MCP Python SDK v1.x: [github.com/modelcontextprotocol/python-sdk](https://github.com/modelcontextprotocol/python-sdk)
- LanceDB Quickstart: [docs.lancedb.com/quickstart](https://docs.lancedb.com/quickstart)

