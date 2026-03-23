"""MCP surface for the Day 1 PolicyNIM scaffold."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

DEFAULT_TOP_K = 5
NOT_IMPLEMENTED = (
    "PolicyNIM Day 1 only locks the public surface. Retrieval and answer generation "
    "arrive in later commits."
)

mcp = FastMCP("PolicyNIM", json_response=True)


@mcp.tool(name="policy_preflight")
def policy_preflight(
    task: str,
    domain: str | None = None,
    top_k: int = DEFAULT_TOP_K,
) -> dict[str, object]:
    """Return policy guidance for a coding task."""
    _ = (task, domain, top_k)
    raise NotImplementedError(NOT_IMPLEMENTED)


@mcp.tool(name="policy_search")
def policy_search(
    query: str,
    domain: str | None = None,
    top_k: int = DEFAULT_TOP_K,
) -> dict[str, object]:
    """Search the policy corpus."""
    _ = (query, domain, top_k)
    raise NotImplementedError(NOT_IMPLEMENTED)


def run_server(transport: str = "stdio") -> None:
    """Run the PolicyNIM MCP server."""
    mcp.run(transport=transport)


if __name__ == "__main__":
    run_server()

