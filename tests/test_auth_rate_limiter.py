"""Regressions for expiry and admission in the portal's process-local limiter."""

from policynim.interfaces.mcp import _InMemoryRateLimiter


def test_expired_clients_are_reclaimed_during_repeated_churn() -> None:
    """A new client reclaims inactive clients without requiring their return."""
    limiter = _InMemoryRateLimiter(max_attempts=2, window_seconds=60)
    for window in range(4):
        for client in range(1000):
            assert limiter.allow(f"{window}-{client}", now=window * 60)
        assert len(limiter._attempts) == 1000


def test_recent_attempt_preserves_client_while_older_clients_expire() -> None:
    """Cleanup follows last admission time and preserves each live attempt."""
    limiter = _InMemoryRateLimiter(max_attempts=2, window_seconds=60)
    assert limiter.allow("returning", now=0)
    assert limiter.allow("abandoned", now=1)
    assert limiter.allow("returning", now=30)
    assert not limiter.allow("returning", now=59)
    assert limiter.allow("new", now=61)
    assert set(limiter._attempts) == {"returning", "new"}
    assert limiter.allow("returning", now=61)
    assert not limiter.allow("returning", now=89)
    assert limiter.allow("returning", now=90)
    assert not limiter.allow("returning", now=90)


def test_rejected_attempt_reclaims_other_expired_clients() -> None:
    """Cleanup still runs when the current caller has no admission left."""
    limiter = _InMemoryRateLimiter(max_attempts=1, window_seconds=60)
    assert limiter.allow("abandoned", now=0)
    assert limiter.allow("active", now=30)
    assert not limiter.allow("abandoned", now=59)
    assert not limiter.allow("active", now=60)
    assert set(limiter._attempts) == {"active"}
    assert not limiter.allow("active", now=89)
    assert limiter.allow("active", now=90)


def test_reset_clears_admission_and_expiry_state() -> None:
    """Reset restores admission without leaving stale cleanup state."""
    limiter = _InMemoryRateLimiter(max_attempts=1, window_seconds=60)
    assert limiter.allow("client", now=0)
    assert not limiter.allow("client", now=1)
    limiter.reset()
    assert not limiter._attempts
    assert limiter.allow("client", now=2)
    assert not limiter.allow("client", now=60)
    assert limiter.allow("other", now=62)
    assert set(limiter._attempts) == {"other"}
