"""Offline service doubles behind the real PolicyNIM MCP transports."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from unittest.mock import patch

from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from policynim.errors import MissingIndexError
from policynim.interfaces import mcp as mcp_module
from policynim.settings import Settings
from policynim.types import (
    Citation,
    HealthCheckResult,
    PolicyGuidance,
    PolicyMetadata,
    PreflightRequest,
    PreflightResult,
    ScoredChunk,
    SearchRequest,
    SearchResult,
)

UNEXPECTED_DETAIL = "private provider credential and /internal/customer/index.sqlite3"
CITATION = Citation(
    policy_id="JOB-001",
    title="Background Jobs",
    path="policies/backend/background-jobs.md",
    section="Cleanup",
    lines="20-24",
    chunk_id="JOB-001:cleanup",
)


class OfflineSettings(Settings):
    """Use only explicit test values and defaults, without developer or CI configuration."""

    model_config = SettingsConfigDict(env_file=None)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Exclude ambient environment, dotenv files, and per-user path discovery."""
        return (init_settings,)


class OfflineSearchService:
    def __init__(self, events: list[str]) -> None:
        """Record each per-call search service allocation."""
        self.events = events
        events.append("search.created")

    def search(self, request: SearchRequest) -> SearchResult:
        """Return cited fixture data or a controlled provider failure."""
        if request.query == "missing-index":
            raise MissingIndexError("Run `policynim ingest` first.")
        if request.query == "unexpected-failure":
            raise RuntimeError(UNEXPECTED_DETAIL)
        return SearchResult(
            query=request.query,
            domain=request.domain,
            top_k=request.top_k,
            hits=[
                ScoredChunk(
                    chunk_id=CITATION.chunk_id,
                    path=CITATION.path,
                    section=CITATION.section,
                    lines=CITATION.lines,
                    text="Cleanup jobs preserve active records and support safe retries.",
                    policy=PolicyMetadata(
                        policy_id=CITATION.policy_id,
                        title=CITATION.title,
                        doc_type="guidance",
                        domain="backend",
                    ),
                    score=0.98,
                )
            ],
        )

    def close(self) -> None:
        """Record cleanup after successful and failed search calls."""
        self.events.append("search.closed")


class OfflinePreflightService:
    def __init__(self, events: list[str]) -> None:
        """Record each per-call preflight service allocation."""
        self.events = events
        events.append("preflight.created")

    def preflight(self, request: PreflightRequest) -> PreflightResult:
        """Return grounded guidance or an explicit lack of supporting policy."""
        if request.task == "uncovered-task":
            return PreflightResult(
                task=request.task,
                domain=request.domain,
                summary="No supporting policy context was found.",
                insufficient_context=True,
            )
        return PreflightResult(
            task=request.task,
            domain=request.domain,
            summary="Preserve active records during cleanup.",
            applicable_policies=[
                PolicyGuidance(
                    policy_id=CITATION.policy_id,
                    title=CITATION.title,
                    rationale="The task changes cleanup behavior.",
                    citation_ids=[CITATION.chunk_id],
                )
            ],
            tests_required=["Prove active records survive cleanup."],
            citations=[CITATION],
        )

    def close(self) -> None:
        """Record cleanup after each preflight call."""
        self.events.append("preflight.closed")


class OfflineHealthService:
    def check(self) -> HealthCheckResult:
        """Report deterministic readiness without opening a real index."""
        return HealthCheckResult(status="ok", ready=True, table_name="offline", row_count=1)


@contextmanager
def offline_services(settings: Settings) -> Iterator[list[str]]:
    """Patch only domain factories; routing, MCP, auth, and serialization stay real."""
    events: list[str] = []
    with ExitStack() as stack:
        stack.enter_context(patch.object(mcp_module, "get_settings", return_value=settings))
        stack.enter_context(
            patch.object(
                mcp_module,
                "create_search_service",
                side_effect=lambda settings: OfflineSearchService(events),
            )
        )
        stack.enter_context(
            patch.object(
                mcp_module,
                "create_preflight_service",
                side_effect=lambda settings: OfflinePreflightService(events),
            )
        )
        stack.enter_context(
            patch.object(
                mcp_module, "create_runtime_health_service", return_value=OfflineHealthService()
            )
        )
        stack.enter_context(patch.object(mcp_module, "create_beta_auth_service", return_value=None))
        yield events


if __name__ == "__main__":
    settings = OfflineSettings(
        nvidia_api_key=None,
        default_top_k=3,
        mcp_require_auth=False,
        mcp_bearer_tokens=[],
        mcp_public_base_url=None,
        beta_signup_enabled=False,
    )
    with offline_services(settings):
        mcp_module.run_server("stdio")
