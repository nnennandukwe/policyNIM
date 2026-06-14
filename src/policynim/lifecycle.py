"""Shared helpers for closing optionally owned resources."""

from __future__ import annotations


def close_owned_resource(resource: object | None) -> None:
    """Close an optional resource when it exposes a callable close hook."""
    close = getattr(resource, "close", None)
    if callable(close):
        close()
