---
policy_id: ARCH-JOB-001
title: Background Job Design Rules
doc_type: architecture-guidance
domain: architecture
tags:
  - jobs
  - retries
  - idempotency
grounded_in:
  - https://sre.google/workbook/index/
  - https://aws.amazon.com/builders-library/timeouts-retries-and-backoff-with-jitter/
---

# Background Job Design Rules

## Intent

Background jobs must be safe to retry, observable in production, and bounded enough
to fail without turning into an outage amplifier.

## Required Rules

- Jobs must be idempotent or have an explicit deduplication mechanism.
- Retries must be bounded and use backoff. Infinite or immediate retries are not
  allowed.
- Terminal failure states must be visible to operators. Silent poison-message loops
  are not acceptable.
- Job payloads must not contain raw secrets or loggable credential artifacts.
- Concurrency must be bounded. If a job fans out, the concurrency limit must be
  declared in code or configuration.
- Long-running jobs must emit progress or heartbeat signals that operators can follow.

## Review Expectations

- Verify retry policy, timeout behavior, and idempotency strategy together. These
  three concerns are one design decision, not three isolated toggles.
- Verify jobs emit structured success, retry, and failure events.
- Verify there is a documented operator action for dead-letter or permanently failed
  work.

## Public Grounding

- Google SRE workbook guidance informed the operator visibility and failure-mode
  expectations.
- AWS Builders' Library guidance informed the retry and backoff requirements.

