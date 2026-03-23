# PolicyNIM Day 4 Execution Plan

## Summary
- Day 4 should add the second retrieval layer: reranking plus grounded synthesis, while keeping the public `preflight` surfaces deferred to Day 5.
- The deliverable is an internal preflight pipeline that can take a task, retrieve dense candidates, rerank them with NVIDIA, generate grounded guidance with NVIDIA chat completion, validate every citation against retrieved chunks, and return `insufficient_context=true` instead of bluffing when grounding fails.
- `policynim search` should be upgraded now to use reranking so the debug workflow reflects the real retrieval stack. `policynim preflight` and MCP tools remain stubs on Day 4.
- This implementation must begin with a worker-agent swarm. It is not a single-agent rollout. The coordinator must spawn parallel worker agents immediately after initial repo grounding and use them for the bounded Day 4 workstreams below.
- If this plan is accepted later, persist it as `.plan/day-4-execution-plan.md`.

## Skills Used
- `audit-boundaries`
  - Keep reranking and generation behind provider/service seams instead of leaking prompt logic into CLI code.
- `audit-abstractions`
  - Avoid adding generic RAG frameworks, agent managers, or prompt-orchestration layers.
- `failure-path-testing`
  - Lock invalid citations, malformed model output, weak-evidence fallback, and provider failures now.
- `dx-audit`
  - Keep `search` behavior understandable from the terminal even though the preflight workflow stays internal on Day 4.

## Scope
- Sync local repo with remote `main`.
- Create `feat/day4-rerank-and-grounded-synthesis`.
- Add a real NVIDIA reranker for the hosted retrieval endpoint.
- Add a real NVIDIA generator for grounded preflight synthesis through chat completions.
- Add `PreflightService` as an internal orchestration layer; do not wire CLI or MCP `preflight` yet.
- Upgrade `SearchService` so `policynim search` returns reranked results, not dense-only results.
- Implement citation validation and deterministic insufficient-context fallback behavior.
- Add offline tests for reranking, grounded synthesis, and citation validation plus opt-in live NVIDIA coverage.
- Update README and architecture notes for the Day 4 retrieval flow.
- Do not turn on CLI `preflight`, `policy_preflight`, or `policy_search` MCP wiring yet.
- Do not add eval harnesses or example clients yet.

## Mandatory Swarm Delegation
- The coordinator must spawn at least 4 parallel `worker` agents before substantive implementation starts.
- The coordinator keeps branch setup, contract decisions, final integration, verification, and PR preparation.
- Worker ownership must be disjoint so patches can be integrated without overlap.
- The coordinator must not bypass the swarm by doing all four workstreams locally.

### Required swarm shape
1. Worker 1: NVIDIA reranker
   - Own `src/policynim/providers/nvidia.py` reranking additions and any provider-specific live-test updates.
   - Implement `NVIDIAReranker`, retrieval-endpoint wiring, retry/auth/error mapping, and score validation.
2. Worker 2: NVIDIA grounded generator
   - Own `src/policynim/providers/nvidia.py` generation additions and any generator-specific live-test updates.
   - Implement `NVIDIAGenerator`, prompt contract, JSON parsing, and deterministic generation settings.
   - This worker must accommodate Worker 1’s changes rather than reverting them.
3. Worker 3: service orchestration
   - Own `src/policynim/services/preflight.py`, `src/policynim/services/search.py`, and `src/policynim/services/__init__.py`.
   - Implement reranked search flow, internal preflight pipeline, citation mapping, and insufficient-context fallback.
4. Worker 4: tests and docs
   - Own `tests/test_search_service.py`, `tests/test_preflight_service.py`, `tests/test_nvidia_live.py`, `README.md`, and `docs/architecture.md`.
   - Add offline coverage, live smoke hooks, and Day 4 docs updates.
- The coordinator may add one extra `explorer` agent for NVIDIA API-reference verification if needed, but the 4-worker swarm above is mandatory, not optional.
- After workers return, the coordinator reviews each patch, resolves any interface mismatches, runs verification, and lands the integrated result.

## Dependencies And Settings
- Keep using `openai` for NVIDIA-hosted chat completions at [NVIDIA LLM APIs](https://docs.api.nvidia.com/nim/reference/llm-apis).
- Add a direct HTTP client dependency for the reranking endpoint, preferably `httpx`, so the retrieval API call is explicit and testable.
- Reuse existing settings already present in the repo:
  - `NVIDIA_API_KEY`
  - `POLICYNIM_NVIDIA_CHAT_MODEL`
  - `POLICYNIM_NVIDIA_RERANK_MODEL`
  - `POLICYNIM_NVIDIA_BASE_URL`
  - `POLICYNIM_NVIDIA_RETRIEVAL_BASE_URL`
  - `POLICYNIM_NVIDIA_TIMEOUT_SECONDS`
  - `POLICYNIM_NVIDIA_MAX_RETRIES`
- Do not add new Day 4 environment knobs unless implementation proves one is necessary.
- Use the current NVIDIA hosted reranking endpoint shape from the official reference for [`llama-nemotron-rerank-1b-v2`](https://docs.api.nvidia.com/nim/reference/nvidia-llama-nemotron-rerank-1b-v2-infer).

## Canonical Contracts
- Public CLI changes:
  - `policynim search` keeps the same flags and JSON shape, but results are reranked before return.
  - `policynim preflight` remains intentionally unimplemented on Day 4.
- Public MCP changes:
  - none; both `policy_preflight` and `policy_search` remain deferred to Day 5.
- Public type changes:
  - keep `SearchResult`, `PreflightRequest`, `PreflightResult`, and `Citation` unchanged.
  - `SearchResult.hits[*].score` now represents rerank score when reranking is enabled.
- Internal additions:
  - `NVIDIAReranker`
  - `NVIDIAGenerator`
  - `PreflightService`
  - one internal validated generation payload model, e.g. `GeneratedPreflightDraft`, that uses chunk IDs instead of full citation objects
- `Reranker` and `Generator` contracts remain the shared seams for Day 4’s concrete NVIDIA-backed implementations.

## Component Design
- `NVIDIAReranker`
  - Accept `query`, `candidates`, and `top_k`.
  - Send query plus candidate passage text to NVIDIA’s retrieval endpoint.
  - Map returned scores back onto the original `ScoredChunk` metadata.
  - Return top `k` chunks in descending rerank score order.
  - Missing key raises `ConfigurationError`; 401/403 do not retry; 429/5xx/timeouts retry up to configured max; malformed responses raise `ProviderError`.
- `NVIDIAGenerator`
  - Use NVIDIA chat completions with deterministic settings and temperature `0`.
  - Prompt for strict JSON-only output.
  - Include only retained reranked chunks, with `chunk_id`, policy metadata, path, section, lines, and text.
  - Require citations only by `chunk_id`; local code maps them to `Citation`.
  - Malformed or non-JSON output raises `ProviderError`.
- `SearchService`
  - Keep index validation and query embedding.
  - Run dense search for `max(request.top_k, 15)` candidates.
  - Rerank those candidates and return the reranked top `request.top_k`.
  - Keep domain filtering in the dense-search step.
  - `insufficient_context=true` only when the index exists but no reranked hits survive.
- `PreflightService`
  - Embed task text, dense-search `max(request.top_k, 15)`, rerank, then retain up to `request.top_k` chunks with diversity capped at 2 chunks per `policy_id`.
  - Generate a grounded draft from retained context.
  - Validate every cited `chunk_id` against retained chunks and map them to public `Citation` values.
  - If no dense hits, no reranked hits, no surviving citations, or invalid cited chunk IDs, return deterministic insufficient context instead of partial guidance.

## Grounding And Validation Rules
- Every citation in the final `PreflightResult` must come from a retained reranked chunk.
- The generator may only cite by `chunk_id`; `PreflightService` owns conversion to full `Citation` values.
- `PolicyGuidance.citation_ids` must be a subset of final retained chunk IDs.
- Guidance sections with no surviving citation IDs must not be returned as grounded guidance.
- The insufficient-context fallback must be deterministic:
  - summary explains that PolicyNIM could not find enough grounded policy evidence
  - all guidance arrays empty
  - citations empty
  - `insufficient_context=true`

## Commit Plan
1. `build(day4): add rerank client dependency and day-4 docs scaffolding`
2. `feat(nvidia): add reranker and grounded generator adapters`
3. `feat(search): rerank search results before returning JSON`
4. `feat(preflight): add internal grounded preflight service and citation validation`
5. `test(day4): cover reranking, grounding, and insufficient-context behavior`
6. `docs: document day-4 rerank and synthesis pipeline`

## Branch And PR Flow
1. `git fetch origin` and confirm local `main` matches `origin/main`.
2. Create `feat/day4-rerank-and-grounded-synthesis` from `main`.
3. Immediately spawn the required 4-worker swarm and assign the ownership above before any broad local implementation.
4. Integrate worker patches, then run offline tests before any live NVIDIA verification.
5. If `NVIDIA_API_KEY` is present, run one opt-in rerank smoke and one opt-in grounded-generation smoke outside default CI.
6. Open a real PR only after reranked `search` behavior and internal preflight tests are passing.

## Test Plan
- `search` returns reranked results in a different order when the fake reranker changes relevance ordering.
- `search` still honors `domain` filtering before reranking.
- `search` returns `insufficient_context=true` when the dense index exists but reranked hits are empty.
- `PreflightService` returns grounded `PreflightResult` with mapped `Citation` objects from retained chunk IDs.
- `PreflightService` enforces document diversity at two chunks max per policy.
- Generator output that references unknown chunk IDs downgrades to insufficient context.
- Generator output with no surviving citations downgrades to insufficient context.
- Missing or empty index still raises the current actionable index error at the service boundary.
- Missing or invalid `NVIDIA_API_KEY` fails clearly for reranking and generation.
- Offline tests use fake embedder, fake reranker, and fake generator.
- Live Day 4 coverage remains opt-in behind `NVIDIA_API_KEY`.

## Definition Of Done
- Fresh Day 4 branch exists off updated `main`.
- The implementation started with the required 4-worker swarm, and those worker responsibilities were actually used for the bounded Day 4 workstreams.
- `policynim search` uses dense retrieval plus NVIDIA reranking while keeping the same public JSON schema.
- Internal `PreflightService` exists and can return grounded `PreflightResult` values with validated citations.
- Citation validation is enforced and weak or ungrounded outputs degrade to `insufficient_context=true`.
- NVIDIA reranking and generation logic stay isolated behind provider/service seams.
- README and architecture notes reflect the Day 4 rerank-plus-grounding pipeline.
- CLI `preflight` and both MCP tools remain intentionally deferred to Day 5.
