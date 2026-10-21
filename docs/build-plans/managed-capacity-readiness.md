# Managed-generation capacity readiness

The capacity harness currently writes a standalone SQLite fixture and replaces the MCP health and search service factories. Its HTTP probes can report ready without any managed generation. That omits the reader leases, source snapshot validation and readiness cache used by the hosted service, so those receipts can't satisfy #103's managed-runtime responsiveness criterion.

An owned public-boundary reproduction returned HTTP 200 from the patched route with no generation ID while the normal factory rejected the missing managed root. A separate feasibility run built 10,000 chunks from four Markdown policies through `GenerationStore.build`, then used normal health and search composition with provider-component doubles. The manifest and health reported 10,000 rows, retrieval returned five hits with the same generation ID, and the four domains each contained 2,500 chunks. Setup/check time was 5.655 seconds on that local environment; it's fixture feasibility evidence only.

## Scope

Generate at most four synthetic Markdown policies with one section per requested row. Preserve the existing uniform/skewed domain allocation and four-dimensional vectors. Publish through the existing generation build boundary inside the child before HTTP measurement. Keep source/settings identities explicit and every runtime path inside the owned fixture directory.

Remove both legacy service-factory replacements. Keep ordinary managed health, discovery and search composition; replace only provider-component construction with the existing deterministic embedding double and no reranker. Bind healthy probes and successful search responses to the published generation ID, and bind health row counts to the published/requested count. Retain expected publication metadata separately from observed health identities/counts.

Preserve all six scenarios, request distributions, delays, admission bounds, cancellation/drain semantics and paired active-before/after probe requirements from #162. Historical raw-fixture receipts stay historical. No public legacy-fixture selector exists, so no compatibility mode is needed. No production, live NVIDIA, model-quality or durable-job claim is added.

## Verification

Before changing the harness, retain a failing public-boundary test showing that its healthy fixture lacks a generation ID. Add managed positive controls with exact 10,000-row uniform and skewed distributions, plus smaller missing-pointer and corrupt-manifest rejection/recovery controls through actual HTTP health and search. Mismatched health IDs/counts or search IDs must fail evidence validation. Keep the existing six SQLite/probe failure controls and three active-probe positive controls.

Run the focused harness suite, static checks and the offline full suite with the existing owned standard environment. The separately prepared optional-extra environment stays untouched. Use local Qodo before the ready PR, then completed exact-head remote Qodo and CI. Root owns merging and the final 25-user profiles after the managed path is native.

The initial full run retained a timing-sensitive positive control: its one-user,
100 ms operation finished without a probe bracket, correctly leaving that scenario
incomplete. The test now gates only the synthetic provider's second query until
two real active observations bracket the probe responses. No production delay,
assertion or measurement gate is relaxed. A public HTTP-response corruption
regression also rejects malformed JSON-RPC/search envelopes and wrong generation
IDs after valid warmup and active probing, preserving the server's actual completed
operation outcomes.

The final parent adopts the published release integration so these checks use its
eight-tool application and ordinary hosted worker lifecycle. The small boundary
controls verify tool discovery and creation of the owned job store without
submitting evaluation work. Another actual HTTP-response corruption regression
rejects schema-valid empty or wrong-domain hits against the fixture's expected
count/domain, preserving accepted ledger outcomes while marking evidence incomplete.
An actual HTTP empty-discovery control also rejects a schema-valid response that
doesn't advertise the fixture's eight tools. Managed probe success requires that
set; generic in-process counter controls keep their existing response contract.

The exact-head review exposed two further evidence gaps. An actual missing-pointer
health response after an earlier valid active bracket now makes that measurement
incomplete; only availability failures after intentional shutdown are expected.
Actual warmup response corruption in a later scenario now retains both scenarios,
records the warmup failure class and returns a failing status. These checks keep
accepted application outcomes separate from whether their evidence passed. The
eight-tool fixture contract has a named expected set passed explicitly to discovery
validation, independently of retrieval generation identity.
