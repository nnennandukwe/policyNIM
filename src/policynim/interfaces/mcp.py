"""MCP surface for the Day 1 PolicyNIM scaffold."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from policynim.errors import NotImplementedYetError
from policynim.settings import get_settings
from policynim.types import MAX_TOP_K, MIN_TOP_K

NOT_IMPLEMENTED = (
    "PolicyNIM Day 1 only locks the public surface. Retrieval and answer generation "
    "arrive in later commits."
)
SUPPORTED_TRANSPORTS = ("stdio", "streamable-http")

mcp = FastMCP("PolicyNIM", json_response=True)


def _validate_top_k(top_k: int) -> None:
    """Validate top_k across MCP tools."""
    if not MIN_TOP_K <= top_k <= MAX_TOP_K:
        raise ValueError(f"top_k must be between {MIN_TOP_K} and {MAX_TOP_K}.")


@mcp.tool(name="policy_preflight")
def policy_preflight(
    task: str,
    domain: str | None = None,
    top_k: int = get_settings().default_top_k,
) -> dict[str, object]:
    """Return policy guidance for a coding task."""
    _validate_top_k(top_k)
    _ = (task, domain, top_k)
    raise NotImplementedYetError(NOT_IMPLEMENTED)


@mcp.tool(name="policy_search")
def policy_search(
    query: str,
    domain: str | None = None,
    top_k: int = get_settings().default_top_k,
) -> dict[str, object]:
    """Search the policy corpus."""
    _validate_top_k(top_k)
    _ = (query, domain, top_k)
    raise NotImplementedYetError(NOT_IMPLEMENTED)


def run_server(transport: str = "stdio") -> None:
    """Run the PolicyNIM MCP server."""
    if transport not in SUPPORTED_TRANSPORTS:
        allowed = ", ".join(SUPPORTED_TRANSPORTS)
        raise ValueError(f"Transport must be one of: {allowed}.")
    mcp.run(transport=transport)


if __name__ == "__main__":
    run_server()
