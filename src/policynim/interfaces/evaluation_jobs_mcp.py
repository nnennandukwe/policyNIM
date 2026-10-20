"""Authenticated application jobs and owner-scoped report resources; no native Tasks adapter."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Annotated

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ResourceError, ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from policynim.interfaces.grounded_mcp import LoggedToolRunner
from policynim.job_types import (
    EvaluationJobError,
    EvaluationJobInfo,
    EvaluationJobLimits,
    EvaluationJobReportManifest,
    EvaluationJobReportPart,
)
from policynim.services.authorization import AuthenticatedPrincipal
from policynim.services.evaluation_jobs import EvaluationJobService, EvaluationJobWorker
from policynim.storage.evaluation_jobs import canonical_json
from policynim.types import EvalSuite

_REPORT_PART_SIZE = 128 * 1024


def _report_parts(service: EvaluationJobService, job_id: str, principal: AuthenticatedPrincipal):
    """Verify one retained owner report before describing its bounded transport fragments."""
    stored = service.result(job_id, principal=principal)
    serialized = canonical_json(stored.report.model_dump(mode="json"))
    parts = [
        serialized[index : index + _REPORT_PART_SIZE]
        for index in range(0, len(serialized), _REPORT_PART_SIZE)
    ]
    return stored, parts


def report_manifest(
    service: EvaluationJobService, job_id: str, principal: AuthenticatedPrincipal
) -> EvaluationJobReportManifest:
    """Return logical part references without exceeding the SDK's default SSE event limit."""
    stored, parts = _report_parts(service, job_id, principal)
    info = service.inspect(job_id, principal=principal)
    report = stored.report
    return EvaluationJobReportManifest(
        job_id=job_id,
        state=report.state,
        generation_id=report.snapshot.generation_id,
        suite_digest=report.snapshot.suite_digest,
        configuration_digest=report.snapshot.configuration_digest,
        snapshot_digest=report.snapshot_digest,
        report_sha256=stored.sha256,
        completed_case_ids=[case.case_id for case in report.completed_cases],
        unfinished_case_ids=report.unfinished_case_ids,
        usage=report.usage,
        expires_at=info.expires_at,
        part_uris=[
            f"policynim://evaluation-jobs/{job_id}/report/{index}" for index in range(len(parts))
        ],
    )


def report_part(
    service: EvaluationJobService, job_id: str, part: str, principal: AuthenticatedPrincipal
) -> EvaluationJobReportPart:
    """Read only a canonical bounded part index after fresh principal/owner checks."""
    if not part.isascii() or not part.isdigit() or len(part) > 6 or str(int(part)) != part:
        raise EvaluationJobError("invalid_report_part: Use a report URI returned by PolicyNIM.")
    stored, parts = _report_parts(service, job_id, principal)
    index = int(part)
    if index >= len(parts):
        raise EvaluationJobError("unknown_report_part: This report part doesn't exist.")
    content = parts[index]
    return EvaluationJobReportPart(
        job_id=job_id,
        part=index,
        part_count=len(parts),
        report_sha256=stored.sha256,
        part_sha256="sha256:" + hashlib.sha256(content.encode()).hexdigest(),
        content=content,
    )


def register_evaluation_jobs(
    server: MCPServer,
    *,
    get_principal: Callable[[str], AuthenticatedPrincipal | None],
    get_worker: Callable[[], EvaluationJobWorker],
    run_tool: LoggedToolRunner,
) -> None:
    """Expose authenticated application jobs without advertising MCP Tasks capability."""
    read_only = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False)
    changes_job = ToolAnnotations(
        read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=True
    )

    def principal(operation: str) -> AuthenticatedPrincipal:
        """Require a verified principal even if a direct dispatcher bypasses HTTP authentication."""
        value = get_principal(operation)
        if value is None:
            raise ToolError("invalid_token: Authenticate before accessing durable evaluation jobs.")
        return value

    @server.tool(name="eval_submit", annotations=changes_job)
    async def submit(
        suite: EvalSuite,
        idempotency_key: str,
        deadline_seconds: Annotated[float, Field(gt=0, le=3600)] = 3600,
        max_provider_calls: Annotated[int, Field(ge=1, le=512)] = 512,
        *,
        ctx: Context,
    ) -> EvaluationJobInfo:
        """Accept a bounded pinned evaluation and return an inspectable application job handle."""
        caller = principal("eval_submit")

        def operation() -> dict[str, object]:
            """Accept inputs without charging duplicate submissions for provider work."""
            worker = get_worker()
            worker.require_admission_ready()
            accepted = worker.service.submit(
                suite,
                idempotency_key=idempotency_key,
                principal=caller,
                limits=EvaluationJobLimits(
                    deadline_seconds=deadline_seconds, max_provider_calls=max_provider_calls
                ),
            )
            worker.start()
            return accepted.model_dump(mode="json")

        result = await run_tool("eval_submit", operation, ctx)
        return EvaluationJobInfo.model_validate(result)

    @server.tool(name="eval_inspect", annotations=read_only)
    async def inspect(job_id: str, *, ctx: Context) -> EvaluationJobInfo:
        """Inspect your accepted inputs and progress after disconnect or restart."""
        caller = principal("eval_inspect")
        result = await run_tool(
            "eval_inspect",
            lambda: get_worker().service.inspect(job_id, principal=caller).model_dump(mode="json"),
            ctx,
        )
        return EvaluationJobInfo.model_validate(result)

    @server.tool(name="eval_cancel", annotations=changes_job)
    async def cancel(job_id: str, *, ctx: Context) -> EvaluationJobInfo:
        """Request cancellation; active provider cleanup drains before terminal state."""
        caller = principal("eval_cancel")
        result = await run_tool(
            "eval_cancel",
            lambda: get_worker().service.cancel(job_id, principal=caller).model_dump(mode="json"),
            ctx,
        )
        return EvaluationJobInfo.model_validate(result)

    @server.tool(name="eval_result", annotations=read_only)
    async def result(job_id: str, *, ctx: Context) -> EvaluationJobReportManifest:
        """Retrieve your final report's input hashes, usage and bounded resource part links."""
        caller = principal("eval_result")
        payload = await run_tool(
            "eval_result",
            lambda: report_manifest(get_worker().service, job_id, caller).model_dump(mode="json"),
            ctx,
        )
        return EvaluationJobReportManifest.model_validate(payload)

    @server.resource("policynim://evaluation-jobs/{job_id}/report", mime_type="application/json")
    async def manifest_resource(job_id: str, ctx: Context) -> str:
        """Read only your retained report manifest through an authenticated logical ID."""
        try:
            caller = principal("eval_report_resource")
            payload = await run_tool(
                "eval_report_resource",
                lambda: report_manifest(get_worker().service, job_id, caller).model_dump(
                    mode="json"
                ),
                ctx,
            )
            return json.dumps(payload, ensure_ascii=True)
        except ToolError as exc:
            raise ResourceError(str(exc)) from exc
        except Exception:
            raise ResourceError(
                "internal_error: The report couldn't be read. Inspect job state before retrying."
            ) from None

    @server.resource(
        "policynim://evaluation-jobs/{job_id}/report/{part}", mime_type="application/json"
    )
    async def part_resource(job_id: str, part: str, ctx: Context) -> str:
        """Read one of your report's verified transport fragments during its retention period."""
        try:
            caller = principal("eval_report_resource")
            payload = await run_tool(
                "eval_report_resource",
                lambda: report_part(get_worker().service, job_id, part, caller).model_dump(
                    mode="json"
                ),
                ctx,
            )
            return json.dumps(payload, ensure_ascii=True)
        except ToolError as exc:
            raise ResourceError(str(exc)) from exc
        except Exception:
            raise ResourceError(
                "internal_error: The report couldn't be read. Inspect job state before retrying."
            ) from None
