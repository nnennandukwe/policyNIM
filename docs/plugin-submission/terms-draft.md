# PolicyNIM service terms source draft

**Owner review required. This draft isn't approved, published or effective.**

This draft supplies product-specific terms for legal/owner review. The owner must
approve the operator identity, governing terms, effective date, privacy contact,
availability and applicable legal language before publishing an effective policy.
The repository's MIT license governs licensed software code; hosted-service terms
need their own approved scope.

## Proposed public text

PolicyNIM helps developers retrieve engineering policy sources, compile selected
sources, request grounded preflight guidance, inspect evidence and run bounded
evaluation jobs. You're responsible for reviewing source applicability and deciding
whether a suggested requirement applies to your work. Keep engineering review,
testing and approval appropriate to the system you're changing.

### Authorized use and submitted material

Use an account and policy corpus that you're authorized to access. Submit only
tasks, source material and evaluation inputs that you have permission to process
through PolicyNIM and its configured providers. Keep passwords, API keys, access
tokens and unnecessary personal or confidential information out of task text,
evaluation suites, idempotency keys and support reports.

Preserve the source attribution, revision information, licenses and change notices
returned with policies. Public-source availability alone doesn't grant unrestricted
reuse rights. Follow the terms attached to each underlying source.

### Guidance and evaluation results

Results depend on the configured policy corpus, active or retained generation,
provider availability and model behavior. Missing coverage can produce
`insufficient_context`. Check cited source text and the result's evidence before
using it to make an engineering decision. The service's retrieved guidance and
evaluation scores aren't legal advice, a certification or a compliance approval.

Submitting an evaluation authorizes bounded provider work within the supplied
limits and available allowance. Accepted jobs retain their inputs and can continue
after a client disconnects. Use the same idempotency key with identical inputs to
retry an uncertain submission. Changing inputs under an existing key returns a
conflict; a new key can start new work and consume additional allowance.

Cancellation applies to the identified job owned by your account. Active provider
work drains before a terminal state can be recorded, and already completed work
keeps its actual outcome. Reports remain available within the service's configured
retention window. Export needed reports before expiry and consult the published
privacy policy for retained summaries, accounting records and deletion requests.

### Access, availability and support

The service enforces authentication, scopes, ownership, quotas, deadlines and
provider budgets. Requests can be rejected or jobs can fail, expire or be
interrupted. Keep the returned state and recovery guidance when deciding whether
to retry. Access depends on valid credentials and the operator's enabled service
configuration.

Support follows the published support page: provide a minimal reproducible example
with safe diagnostics. Security reports use the private security channel. The
current project policy offers no response-time or uptime guarantee. Any paid
support or availability commitment requires a separately approved agreement.

## Owner decisions before approval

| Decision | Required owner review |
| --- | --- |
| Contracting operator and applicable law | Approve identity, effective date, jurisdiction and required notices. |
| Acceptable use and enforcement | Approve permitted use, restrictions, suspension/termination and appeals language. |
| Liability, warranties and disputes | Obtain appropriate legal review; no new legal assurance is supplied by this draft. |
| Commercial and geographic scope | Confirm whether access is paid, supported countries and any separate service agreement. |
| Privacy and policy changes | Approve the privacy policy, private contact route and notice/change process. |

Behavioral sources: [README](../../README.md),
[evaluation jobs](../evaluation-jobs.md),
[authorization](../hosted-authorization.md),
[limitations](../limitations.md), [support](../../SUPPORT.md),
[security](../../SECURITY.md), [source grounding](../public-source-grounding.md)
and [software license](../../LICENSE).
