"""Lifecycle helpers for optional owned components."""

from __future__ import annotations


def close_if_supported(component: object | None) -> None:
    """Close one optional component when it exposes a callable close hook."""
    close = getattr(component, "close", None)
    if callable(close):
        close()
