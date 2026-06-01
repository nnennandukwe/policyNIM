"""Copyable coding-agent workflow prompts for first-run surfaces."""

from __future__ import annotations

from typing import TypedDict, cast


class AgentWorkflow(TypedDict):
    """Structured prompt shown by quickstart and support diagnostics."""

    title: str
    tool: str
    prompt: str


class AgentWorkflowCard(AgentWorkflow):
    """Hosted portal workflow prompt with explanatory card text."""

    description: str


_AGENT_WORKFLOW_CARDS: tuple[AgentWorkflowCard, ...] = (
    {
        "title": "Preflight before implementation",
        "tool": "policy_preflight",
        "description": (
            "Ask your coding agent to retrieve policy evidence and produce grounded "
            "implementation guidance before it edits code."
        ),
        "prompt": (
            "Before editing, call policy_preflight for: Implement a refresh-token cleanup "
            "background job. Use the cited constraints in your implementation plan. If the "
            "result is insufficient_context, stop and call policy_search with a narrower "
            "query before changing files."
        ),
    },
    {
        "title": "Retrieve policy evidence while debugging",
        "tool": "policy_search",
        "description": (
            "Use raw policy search when a review comment, failing gate, or unclear "
            "constraint needs the underlying source text."
        ),
        "prompt": (
            "Use policy_search for: release installer checksum verification. Summarize "
            "the relevant cited policy lines before proposing a fix."
        ),
    },
    {
        "title": "Verify MCP tool availability",
        "tool": "list_tools",
        "description": (
            "After adding the hosted server, ask your client to confirm it can see the "
            "PolicyNIM tools before you start a longer coding session."
        ),
        "prompt": (
            "List the PolicyNIM MCP tools and confirm policy_preflight and policy_search "
            "are available before starting implementation."
        ),
    },
)


def agent_workflows() -> list[AgentWorkflow]:
    """Return copyable first-use prompts without hosted portal descriptions."""
    return [
        {
            "title": workflow["title"],
            "tool": workflow["tool"],
            "prompt": workflow["prompt"],
        }
        for workflow in _AGENT_WORKFLOW_CARDS
    ]


def agent_workflow_cards() -> list[AgentWorkflowCard]:
    """Return copyable first-use prompts with hosted portal descriptions."""
    return [cast(AgentWorkflowCard, dict(workflow)) for workflow in _AGENT_WORKFLOW_CARDS]
