Before an AI coding agent changes a system, it needs the engineering rules that
apply to that work. PolicyNIM retrieves those policy sources and returns guidance
with citations so I can inspect what the recommendation depends on.

I'm using a dedicated sample account and a public policy corpus. I'll show the
package version, the service revision and the policy generation used for this run.
That gives us a way to connect each result to the system that produced it.

First, I'm asking which policies apply to adding a public API endpoint. The search
result should identify relevant source chunks and their cited lines. I'll open a
returned source and compare the citation with the text we can actually read.

Next, I'll compile the policies for the same task and request a preflight. The
compiled packet keeps the selected source material together. The preflight uses
that context to produce guidance. I'll check which requirements the sources support
and whether the result reports missing coverage.

Now I'll request a preflight with its evidence trace. This should connect the
guidance to the selected policies and chunks from that same run. I'll compare the
returned references with the source text so we can see what supports the output.

The next workflow creates a persistent evaluation job. I've prepared a sample suite
with one case and pinned it to this policy catalog. I'm setting a deadline of
120 seconds and a limit of eight provider calls. I'll submit it with a new
idempotency key and keep the returned job ID.

I'll inspect the accepted job, disconnect and reconnect, and inspect that same ID
again. When the job reaches a terminal state, I'll show its actual state and the
report response. If a report's available, I'll open the returned report resources
and inspect the recorded inputs, hashes and provider usage. If it failed or was
interrupted, I'll show the failure and recovery guidance we're given.

I'll also retry the identical submission with its original key. We should receive
the original job rather than starting another evaluation.

For cancellation, I'll start another bounded sample job and request cancellation
for the ID that was just returned. I'll approve the client's confirmation if it's
shown, then inspect the actual state. Active provider work needs to drain before
the job can reach a terminal state. If the job finished first, I'll say that and
use the approved review procedure to capture a cancellation while work's still
queued or running.

Now I'll check three requests where the service needs to limit its behavior. A
birthday greeting has no policy-workflow intent, so it should use no PolicyNIM
tools. An attempt to read the other sample account's job should be denied without
revealing its report. A preflight in the intentionally absent policy domain should
report insufficient context and avoid inventing requirements or citations.

These checks connect the user's request, the tools that ran and the outcome we can
inspect. That's the evidence I want before accepting guidance or starting another
job. I'll record the actual results for all eight cases with the tested service
revision and package checksum.
