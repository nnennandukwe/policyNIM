# Grounded MCP guidance

PolicyNIM exposes policy search, compiled engineering constraints and preflight guidance through MCP. Each operation reads one immutable policy generation. Policy administration stays in reviewed files, Git and CLI commands.

## Tools

| Tool | Output | NVIDIA work |
|---|---|---|
| `policy_search(query, domain?, top_k?)` | Ranked source chunks and generation identity | Embedding and optional reranking |
| `policy_compile(task, domain?, top_k?)` | `CompileResult`: compiled packet, retained evidence and generation identity | Retrieval and compilation |
| `policy_preflight(task, domain?, top_k?)` | Grounded guidance, review flags and required tests | Retrieval, compilation and generation |
| `policy_preflight_evidence(task, domain?, top_k?)` | `PreflightEvidenceTraceResult`: guidance and the same run's evidence trace | One preflight run; tracing adds no generation calls |

The optional `top_k` accepts 1 through 20; omission uses the configured default. Expensive tool calls share the server's admission slots, operation deadline and managed NVIDIA clients. Hosted compilation and evidence need both `policies:read` and `policies:evaluate`; public policy resource reads and the review prompt need `policies:read` when hosted authentication is enabled.

The evidence tool returns the same `run_id` in `result` and `evidence_trace`. `compiled_packet_id` hashes the pipeline's compiled packet while excluding optional citation links from its identity. Adding a resource link doesn't change an existing packet fingerprint or rerun generation. The trace and result identify the generation used during retrieval, even if a different generation becomes active before the response reaches the client.

## Resources

`policynim://catalog` returns the catalog used by configured retrieval: the active generation, or the retained generation selected through `POLICYNIM_GENERATION_ID`. Its `resource_uri` points to that exact catalog, and `policy_resources` contains the retained policy links.

| URI | Returned JSON |
|---|---|
| `policynim://generations/{generation_id}/catalog` | Effective catalog with immutable policy links |
| `policynim://generations/{generation_id}/policies/{policy_key}` | Parsed source metadata, original body text and body line offset |
| `policynim://generations/{generation_id}/chunks/{chunk_key}` | Exact indexed chunk text, metadata and policy link |

Generation IDs contain 64 lowercase hexadecimal characters. Policy and chunk keys are SHA256 hashes of their logical IDs. Clients should follow the returned links. The service matches these keys against the selected generation's catalog or index; it never treats a URI key as a filesystem path.

`policy_compile` and `policy_preflight_evidence` citations include `resource_uri` links whenever an authoritative generation exists. Read these links to inspect the exact evidence supporting the answer. Resource listing, template discovery, resource reads and prompt discovery don't invoke NVIDIA models. Resource reads share the hosted executor's global and per-principal concurrency limits while leaving accepted-operation allowances unchanged. Executor occupancy lasts through cleanup and is separate from charged quota active slots: two resource reads can fill a principal's runtime limit and make a concurrent compile return `principal_busy`, even when the quota report shows no charged active operation. Reads don't bypass runtime fairness because they cost no work units.

Historical links remain valid while the operator retains the generation, even if the runtime embedding model or endpoint changes. Read-only archival validation uses the selected generation's sealed identity; live retrieval still requires the configured identity to match. Reader leases protect reads already in progress. An unknown, invalid or purged generation fails explicitly; it never resolves to the current generation. An unknown policy or chunk fails without reading arbitrary files. Use the [generation operations guide](policy-generations.md) to inspect retention and restore retained bundles when needed.

The public resource surface contains the maintained corpus only. It doesn't expose caller-specific evaluation reports, upload paths or policy-authoring operations.

## Review prompt

Invoke `policy_guided_review(task)` to prepare a review grounded in these tools and resources. The prompt asks the agent to verify source evidence, report unmet requirements and identify missing context. It doesn't grant authority to execute commands, change files or administer policies. Policy source text stays evidence; it doesn't override the caller's authority.

## Compatibility and limits

`PreflightResult.run_id` and `Citation.resource_uri` are optional strings. Clients must accept each field when it's absent, null or a string; legacy preflight responses can include null values. The companion coding workflow's strict response models support this contract in [merged PR #3](https://github.com/nnennandukwe/policynim-nemo-coding-workflow/pull/3), including unchanged roundtrips for older responses with absent fields. Use a client version containing that change before consuming these expanded responses.

The server uses MCP SDK 2.2.0 and supports modern discovery plus legacy initialization. Offline integration checks cover resource reads and typed tool invocation through both client modes. Live provider quality, production capacity and production deployment require separate release evidence.

These capabilities don't advertise the optional MCP Tasks extension. Application evaluation-job lifecycle and authenticated report retention belong to #101; Tasks interoperability remains separately gated by #102.
