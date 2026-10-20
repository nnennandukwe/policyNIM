# Chat availability recovery (#121)

PolicyNIM defaults to `nvidia/nemotron-3.5-lightning-30b-a3b` for policy
compilation, grounded guidance, and policy-conformance evaluation. Its shared
chat adapter sends temperature `1`, top-p `0.95`, and
`chat_template_kwargs.enable_thinking=false`. NVIDIA's
[request example](https://build.nvidia.com/nvidia/nemotron-3.5-lightning-30b-a3b/build)
documents the sampling settings and thinking option. PolicyNIM disables thinking
so these paths can parse and validate the final JSON content.

The previous default, `nvidia/nemotron-3-super-120b-a12b`, remains a supported
explicit override with the same request options. Other model overrides retain
temperature `0`, top-p `1`, and no thinking option. There's no automatic fallback.
Set `POLICYNIM_NVIDIA_CHAT_MODEL` explicitly when an installation needs a
different model; existing hosted variables override code defaults.

## Diagnose HTTP 404

A model listed in `/models` might still be unavailable to the selected account.
HTTP 404 stops after one attempt even when retries are enabled. The public error
names the relevant model and endpoint settings and suggests checking account
access with a minimal request. It doesn't expose the provider response body or
credential. A missing model, wrong endpoint, or account access can produce the
same status; the error doesn't claim that the model was retired.

For a chat failure, check `POLICYNIM_NVIDIA_CHAT_MODEL` and
`POLICYNIM_NVIDIA_BASE_URL` against NVIDIA's current model contract, then verify
that the selected credential can call it. Preserve explicit model choices until
the replacement passes the actual compilation and preflight path. Changing only
the chat model doesn't change the stored embedding identity or require a new
generation. Changing the embedding model requires a separately built compatible
generation; see [generation recovery](policy-generations.md).

## Verified behavior and limits

On October 20, 2026, a minimal direct request and the actual CLI preflight both
returned HTTP 404 for the previous model with the selected hosted credential.
Retrieval succeeded before compilation failed. The Lightning candidate, with
thinking disabled, completed actual CLI compilation and preflight using the
same credential and endpoint: the response passed JSON validation, retained the
selected generation ID, included tracing and logging citations, and reported
`insufficient_context=false`.

That probe establishes one supported request path. It doesn't establish task
quality across the policy library, optional NeMo Guardrails behavior, or a
production deployment. Broader grounded evaluations and hosted acceptance checks
remain release gates. The earlier embedding-endpoint repair remains documented
separately in [the #107 recovery record](hosted-provider-recovery.md).
