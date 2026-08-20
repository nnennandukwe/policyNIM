"""Tests for lifecycle helper utilities."""

from __future__ import annotations

from policynim.lifecycle import close_if_supported


class _Closable:
    """Track whether the helper invoked the close hook."""

    def __init__(self) -> None:
        """Start in the open state."""
        self.closed = False

    def close(self) -> None:
        """Record that the resource was closed."""
        self.closed = True


class _NotClosable:
    """Object without a close hook."""


def test_close_if_supported_closes_owned_components() -> None:
    """Call the close hook when the component exposes one."""
    component = _Closable()

    close_if_supported(component)

    assert component.closed is True


def test_close_if_supported_ignores_missing_close_hooks() -> None:
    """Treat components without close hooks as no-ops."""
    close_if_supported(_NotClosable())
