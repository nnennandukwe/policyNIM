"""Construct hosted route URLs from settings-validated service origins."""

from pydantic import AnyHttpUrl


def public_route_url(origin: AnyHttpUrl, route: str) -> str:
    """Preserve the validated origin and append an existing absolute route suffix."""
    return str(origin).rstrip("/") + route
