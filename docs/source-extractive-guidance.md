# Source-extractive policy guidance

PolicyNIM's compiler and preflight tools select policy sources and quote their full normalized bodies. They don't generate new policy requirements, implementation recipes or summaries of those requirements. This boundary addresses observed live output that invented batch sizes, a Go cancellation API and a changed token lifecycle rule despite valid citations.

Commands and public JSON field shapes stay the same. The meaning of their authoritative strings changes:

- A compiled packet's `required_steps` contains whole selected source bodies, with canonical policy IDs and every citation for each body. Other constraint categories stay empty.
- Preflight's `plan_steps` contains those full bodies once. These are source references to review, including their conditions, exceptions and examples; placement in this legacy field doesn't make every sentence mandatory.
- `applicable_policies` retains canonical policy IDs, titles and owner-bound references. Its rationale and the top-level summary use fixed service text.
- `implementation_guidance`, `review_flags` and `tests_required` stay empty. Policy review and test requirements remain visible inside each quoted whole body.

A caller that needs a source's metadata can use its catalog/resource references. Selection input includes the pinned provenance's applicability and requirement level. Task text describes the user's intent; it doesn't become policy authority. A synthesized advisory field would need a separately reviewed, versioned response contract.

The NVIDIA adapters request compact source IDs instead of generated policy wording. Each ID belongs to that request's source catalog. Selection arrays are sets, and output follows stable catalog order. The adapter materializes complete bodies locally, preserving whitespace and custom headings. A second generation stage retains the compiled source set. NVIDIA's extractive mode stops after a failed conformance judgment with the existing `no_material_trigger` reason; it doesn't dispatch another generator or judge call for quotations that can't change. The Guardrails wrapper preserves this mode. Custom generators keep their existing retry behavior unless they explicitly declare `source_extractive = True`. Public attempt records and stop-reason values stay compatible; an identical quotation isn't a repaired policy.

Services independently validate every returned statement and metadata field, including responses from custom providers. Invented prose, altered triggers, incomplete references, changed titles and unsupported summaries fail closed. Services don't repair an invented claim into authoritative guidance. A provider can't regain source ownership by copying a chunk list after losing its typed source binding.

Default composition derives complete bodies only from the router's held, validated generation snapshot. It validates the complete canonical chunk inventory and captures immutable chunk fingerprints. It doesn't reread mutable source paths or join retrieved chunks into an alleged complete policy. Missing complete source ownership stops before chat. Explicit constructor-supplied source catalogs exist for authored synthetic evaluator fixtures; they're trusted composition inputs, never an environment fallback or provider-produced authority.

Selected context remains limited to 80 chunks and 256 KiB of serialized source data. Whole source units also have a 256 KiB budget. Each compiled packet and preflight result has a separate 1 MiB ceiling, including its discovery evidence and citation metadata. Trace and regeneration envelopes can be larger because they repeat these bounded values; the existing attempt limit remains unchanged. A limit refuses the whole output; PolicyNIM doesn't silently trim conditions or exceptions. Narrow `top_k` or the policy domain, or publish smaller reviewed policy documents when the source budget is exceeded.

The evaluator independently matches exact body quotations against its own held generation and validates their owner/title/full citation coordinates. It doesn't treat words inside those proved quotations as newly asserted policy IDs. Unmatched prose, near quotations, changed source revisions and all structured policy/citation IDs retain the ordinary forbidden-policy checks. The gold expectations don't change.

Quote fidelity establishes agreement with the supplied source body. It doesn't establish that a policy applies to the task, that its source is trustworthy, that every category choice is correct or that malicious source text is safe to follow. Source authorship, relevance, semantic correctness and independent conformance remain separate review questions. Earlier live results with unsupported details remain retained failure evidence; retrieval recall isn't a semantic-quality score.

Public `CompiledPolicyPacket`, `PreflightResult` and `EvalCaseResult` schemas and nested serialization shapes remain unchanged. Historical case/report hashes aren't rewritten. Consumers should review the shift from synthesized instructions to source quotations before adopting this release.
