"""Policy-backed preflight regeneration loop."""

from __future__ import annotations

from collections.abc import Sequence
from types import TracebackType
from typing import Protocol

from policynim.contracts import Generator
from policynim.services.compiler import create_policy_compiler_service
from policynim.services.conformance import PolicyConformanceService
from policynim.services.evidence_trace import (
    PolicyEvidenceTraceService,
    compiled_policy_packet_id,
    create_policy_evidence_trace_service,
)
from policynim.services.preflight import (
    _apply_compiled_packet_to_draft,
    _coerce_generated_draft,
    _insufficient_context_result,
    _trace_step,
    _validate_and_materialize_result,
)
from policynim.settings import Settings, get_settings
from policynim.types import (
    CompiledPolicyConstraint,
    CompiledPolicyPacket,
    CompileRequest,
    CompileResult,
    EvalBackend,
    PolicyConformanceRequest,
    PolicyConformanceResult,
    PolicyConformanceTraceStep,
    PolicyEvidenceTrace,
    PreflightRegenerationRequest,
    PreflightRegenerationResult,
    PreflightRequest,
    PreflightResult,
    PreflightTraceResult,
    RegenerationAttempt,
    RegenerationBackend,
    RegenerationContext,
    RegenerationStopReason,
    RegenerationTrigger,
    RegenerationTriggerKind,
    ScoredChunk,
)


class _PolicyCompilerService(Protocol):
    def compile(self, request: CompileRequest) -> CompileResult:
        """Compile one policy packet request."""
        ...

    def close(self) -> None:
        """Release compiler resources."""
        ...


class _PolicyConformanceService(Protocol):
    def evaluate(
        self,
        request: PolicyConformanceRequest,
        *,
        backend: EvalBackend,
    ) -> PolicyConformanceResult:
        """Evaluate one preflight result against compiled policy."""
        ...

    def close(self) -> None:
        """Release conformance resources."""
        ...


class PolicyRegenerationService:
    """Run a bounded policy-backed preflight regeneration loop."""

    def __init__(
        self,
        *,
        compiler_service: _PolicyCompilerService,
        generator: Generator,
        conformance_service: _PolicyConformanceService,
        evidence_trace_service: PolicyEvidenceTraceService | None = None,
    ) -> None:
        self._compiler_service = compiler_service
        self._generator = generator
        self._conformance_service = conformance_service
        self._evidence_trace_service = (
            evidence_trace_service or create_policy_evidence_trace_service()
        )

    def __enter__(self) -> PolicyRegenerationService:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        """Release owned provider resources held by this service."""
        _close_component(self._compiler_service)
        _close_component(self._generator)
        _close_component(self._conformance_service)

    def regenerate(
        self,
        request: PreflightRegenerationRequest,
    ) -> PreflightRegenerationResult:
        """Run preflight generation, conformance scoring, and bounded retries."""
        preflight_request = PreflightRequest(
            task=request.task,
            domain=request.domain,
            top_k=request.top_k,
        )
        compile_result = self._compiler_service.compile(
            CompileRequest(task=request.task, domain=request.domain, top_k=request.top_k)
        )
        compiled_packet = compile_result.packet
        retained_context = compile_result.retained_context
        packet_id = compiled_policy_packet_id(compiled_packet)

        if compiled_packet.insufficient_context or not retained_context:
            result = _insufficient_context_result(preflight_request)
            trace_steps = _initial_trace_steps(compiled_packet)
            trace_result = _trace_result(
                result=result,
                compiled_packet=compiled_packet,
                retained_context=retained_context,
                trace_steps=trace_steps,
            )
            evidence_trace = self._evidence_trace_service.build(trace_result)
            attempt = _attempt(
                attempt_index=0,
                compiled_packet_id=packet_id,
                triggers=[],
                result=result,
                conformance_result=None,
                evidence_trace=evidence_trace,
            )
            return _result(
                request=request,
                passed=False,
                stop_reason="insufficient_context",
                compiled_packet_id=packet_id,
                attempts=[attempt],
            )

        attempts: list[RegenerationAttempt] = []
        next_triggers: list[RegenerationTrigger] = []
        previous_result: PreflightResult | None = None
        for attempt_index in range(request.max_regenerations + 1):
            attempt_triggers = list(next_triggers)
            regeneration_context = (
                RegenerationContext(
                    attempt_index=attempt_index,
                    max_regenerations=request.max_regenerations,
                    compiled_packet_id=packet_id,
                    previous_result=previous_result,
                    triggers=attempt_triggers,
                )
                if previous_result is not None
                else None
            )
            trace_steps = _initial_trace_steps(compiled_packet)
            result = self._generate_attempt_result(
                preflight_request,
                retained_context,
                compiled_packet=compiled_packet,
                regeneration_context=regeneration_context,
                trace_steps=trace_steps,
            )
            trace_result = _trace_result(
                result=result,
                compiled_packet=compiled_packet,
                retained_context=retained_context,
                trace_steps=trace_steps,
            )
            if result.insufficient_context:
                evidence_trace = self._evidence_trace_service.build(trace_result)
                attempts.append(
                    _attempt(
                        attempt_index=attempt_index,
                        compiled_packet_id=packet_id,
                        triggers=attempt_triggers,
                        result=result,
                        conformance_result=None,
                        evidence_trace=evidence_trace,
                    )
                )
                return _result(
                    request=request,
                    passed=False,
                    stop_reason="insufficient_context",
                    compiled_packet_id=packet_id,
                    attempts=attempts,
                )

            conformance_result = self._conformance_service.evaluate(
                PolicyConformanceRequest(
                    task=request.task,
                    result=result,
                    compiled_packet=compiled_packet,
                    trace_steps=trace_steps,
                ),
                backend=request.backend,
            )
            evidence_trace = self._evidence_trace_service.build(
                trace_result,
                conformance_result=conformance_result,
            )
            attempts.append(
                _attempt(
                    attempt_index=attempt_index,
                    compiled_packet_id=packet_id,
                    triggers=attempt_triggers,
                    result=result,
                    conformance_result=conformance_result,
                    evidence_trace=evidence_trace,
                )
            )
            if conformance_result.passed:
                return _result(
                    request=request,
                    passed=True,
                    stop_reason="passed",
                    compiled_packet_id=packet_id,
                    attempts=attempts,
                )
            if attempt_index >= request.max_regenerations:
                return _result(
                    request=request,
                    passed=False,
                    stop_reason="max_regenerations",
                    compiled_packet_id=packet_id,
                    attempts=attempts,
                )

            next_triggers = regeneration_triggers_from_conformance(
                conformance_result,
                compiled_packet,
            )
            if not next_triggers:
                return _result(
                    request=request,
                    passed=False,
                    stop_reason="no_material_trigger",
                    compiled_packet_id=packet_id,
                    attempts=attempts,
                )
            previous_result = result

        return _result(
            request=request,
            passed=False,
            stop_reason="max_regenerations",
            compiled_packet_id=packet_id,
            attempts=attempts,
        )

    def _generate_attempt_result(
        self,
        request: PreflightRequest,
        retained_context: Sequence[ScoredChunk],
        *,
        compiled_packet: CompiledPolicyPacket,
        regeneration_context: RegenerationContext | None,
        trace_steps: list[PolicyConformanceTraceStep],
    ) -> PreflightResult:
        generated = self._generator.generate_preflight(
            request,
            retained_context,
            compiled_packet=compiled_packet,
            regeneration_context=regeneration_context,
        )
        draft = _coerce_generated_draft(generated)
        draft = _apply_compiled_packet_to_draft(draft, compiled_packet)
        step_kind = (
            "policy_regeneration" if regeneration_context is not None else "grounded_generation"
        )
        trace_steps.append(
            _trace_step(
                step_id=("regenerate" if regeneration_context is not None else "generate"),
                kind=step_kind,
                summary=draft.summary or "Generated preflight draft.",
                citation_ids=draft.citation_ids,
            )
        )
        validated = _validate_and_materialize_result(request, retained_context, draft)
        if validated is None:
            return _insufficient_context_result(request)
        return validated


def create_policy_regeneration_service(
    settings: Settings | None = None,
    *,
    backend: RegenerationBackend = "nemo",
) -> PolicyRegenerationService:
    """Build the default policy regeneration service from application settings."""
    active_settings = settings or get_settings()
    compiler_service: _PolicyCompilerService | None = None
    generator: Generator | None = None
    conformance_service: _PolicyConformanceService | None = None
    try:
        compiler_service = create_policy_compiler_service(active_settings)
        generator = _create_default_generator(active_settings)
        conformance_service = _create_conformance_service(active_settings, backend=backend)
        return PolicyRegenerationService(
            compiler_service=compiler_service,
            generator=generator,
            conformance_service=conformance_service,
        )
    except Exception:
        _close_component(conformance_service)
        _close_component(generator)
        _close_component(compiler_service)
        raise


def regeneration_triggers_from_conformance(
    conformance_result: PolicyConformanceResult,
    compiled_packet: CompiledPolicyPacket,
) -> list[RegenerationTrigger]:
    """Convert failed conformance metrics into typed regeneration triggers."""
    triggers: list[RegenerationTrigger] = []
    for metric in conformance_result.metrics:
        if metric.passed:
            continue
        triggers.extend(_triggers_for_metric(metric.name, metric.failure_reasons, compiled_packet))

    if not conformance_result.passed:
        judged_metrics: tuple[tuple[str, RegenerationTriggerKind], ...] = (
            ("final_adherence", "final_adherence"),
            ("trajectory_adherence", "trajectory_adherence"),
        )
        for metric_name, trigger_kind in judged_metrics:
            failed_metric = next(
                (
                    metric
                    for metric in conformance_result.metrics
                    if metric.name == metric_name and not metric.passed
                ),
                None,
            )
            if failed_metric is None:
                continue
            if not (conformance_result.constraint_ids or conformance_result.chunk_ids):
                continue
            triggers.append(
                RegenerationTrigger(
                    kind=trigger_kind,
                    metric_name=metric_name,
                    failure_reasons=list(failed_metric.failure_reasons),
                    constraint_ids=list(conformance_result.constraint_ids),
                    chunk_ids=list(conformance_result.chunk_ids),
                )
            )

    return _dedupe_triggers(triggers)


def _create_default_generator(settings: Settings) -> Generator:
    from policynim.providers import NVIDIAGenerator

    return NVIDIAGenerator.from_settings(settings)


def _create_conformance_service(
    settings: Settings,
    *,
    backend: RegenerationBackend,
) -> PolicyConformanceService:
    from policynim.providers import (
        NeMoAgentToolkitPolicyConformanceEvaluator,
        NeMoEvaluatorPolicyConformanceEvaluator,
        NVIDIAPolicyConformanceEvaluator,
    )

    if backend == "nemo":
        evaluator = NVIDIAPolicyConformanceEvaluator.from_settings(settings)
    elif backend == "nemo_evaluator":
        evaluator = NeMoEvaluatorPolicyConformanceEvaluator.from_settings(settings)
    else:
        evaluator = NeMoAgentToolkitPolicyConformanceEvaluator.from_settings(settings)
    return PolicyConformanceService(evaluator=evaluator)


def _initial_trace_steps(
    compiled_packet: CompiledPolicyPacket,
) -> list[PolicyConformanceTraceStep]:
    return [
        _trace_step(
            step_id="compile",
            kind="policy_compilation",
            summary=(
                "Compiled policy packet had insufficient context."
                if compiled_packet.insufficient_context
                else "Compiled policy packet for generation."
            ),
            citation_ids=[citation.chunk_id for citation in compiled_packet.citations],
        )
    ]


def _trace_result(
    *,
    result: PreflightResult,
    compiled_packet: CompiledPolicyPacket,
    retained_context: Sequence[ScoredChunk],
    trace_steps: list[PolicyConformanceTraceStep],
) -> PreflightTraceResult:
    return PreflightTraceResult(
        result=result,
        compiled_packet=compiled_packet,
        retained_context=list(retained_context),
        trace_steps=list(trace_steps),
    )


def _attempt(
    *,
    attempt_index: int,
    compiled_packet_id: str,
    triggers: Sequence[RegenerationTrigger],
    result: PreflightResult,
    conformance_result: PolicyConformanceResult | None,
    evidence_trace: PolicyEvidenceTrace,
) -> RegenerationAttempt:
    return RegenerationAttempt(
        attempt_index=attempt_index,
        compiled_packet_id=compiled_packet_id,
        triggers=list(triggers),
        result=result,
        conformance_result=conformance_result,
        evidence_trace=evidence_trace,
    )


def _result(
    *,
    request: PreflightRegenerationRequest,
    passed: bool,
    stop_reason: RegenerationStopReason,
    compiled_packet_id: str,
    attempts: Sequence[RegenerationAttempt],
) -> PreflightRegenerationResult:
    if not attempts:
        raise ValueError("regeneration result requires at least one attempt.")
    final_attempt = attempts[-1]
    return PreflightRegenerationResult(
        request=request,
        passed=passed,
        stop_reason=stop_reason,
        compiled_packet_id=compiled_packet_id,
        final_result=final_attempt.result,
        final_conformance_result=final_attempt.conformance_result,
        evidence_trace=final_attempt.evidence_trace,
        attempts=list(attempts),
    )


def _triggers_for_metric(
    metric_name: str,
    failure_reasons: Sequence[str],
    compiled_packet: CompiledPolicyPacket,
) -> list[RegenerationTrigger]:
    if metric_name == "plan_completeness":
        return [
            _constraint_trigger(
                kind="required_steps",
                metric_name=metric_name,
                failure_reasons=failure_reasons,
                category_name="required_steps",
                constraints=compiled_packet.required_steps,
            )
        ]
    if metric_name == "guidance_coverage":
        return [
            trigger
            for trigger in (
                _constraint_trigger(
                    kind="architectural_expectations",
                    metric_name=metric_name,
                    failure_reasons=failure_reasons,
                    category_name="architectural_expectations",
                    constraints=compiled_packet.architectural_expectations,
                ),
                _constraint_trigger(
                    kind="style_constraints",
                    metric_name=metric_name,
                    failure_reasons=failure_reasons,
                    category_name="style_constraints",
                    constraints=compiled_packet.style_constraints,
                ),
            )
            if trigger.constraint_ids or trigger.chunk_ids
        ]
    if metric_name == "test_coverage":
        return [
            _constraint_trigger(
                kind="test_expectations",
                metric_name=metric_name,
                failure_reasons=failure_reasons,
                category_name="test_expectations",
                constraints=compiled_packet.test_expectations,
            )
        ]
    if metric_name == "forbidden_pattern_handling":
        return [
            _constraint_trigger(
                kind="forbidden_patterns",
                metric_name=metric_name,
                failure_reasons=failure_reasons,
                category_name="forbidden_patterns",
                constraints=compiled_packet.forbidden_patterns,
            )
        ]
    if metric_name == "citation_support":
        return [
            RegenerationTrigger(
                kind="citation_support",
                metric_name=metric_name,
                failure_reasons=list(failure_reasons),
                constraint_ids=_all_constraint_ids(compiled_packet),
                chunk_ids=_all_constraint_chunk_ids(compiled_packet),
            )
        ]
    if metric_name == "compiled_context":
        return [
            RegenerationTrigger(
                kind="insufficient_context",
                metric_name=metric_name,
                failure_reasons=list(failure_reasons),
                constraint_ids=[],
                chunk_ids=[],
            )
        ]
    return []


def _constraint_trigger(
    *,
    kind: RegenerationTriggerKind,
    metric_name: str,
    failure_reasons: Sequence[str],
    category_name: str,
    constraints: Sequence[CompiledPolicyConstraint],
) -> RegenerationTrigger:
    return RegenerationTrigger(
        kind=kind,
        metric_name=metric_name,
        failure_reasons=list(failure_reasons),
        constraint_ids=[
            f"{category_name}:{index}" for index, _constraint in enumerate(constraints)
        ],
        chunk_ids=_constraint_chunk_ids(constraints),
    )


def _all_constraint_ids(compiled_packet: CompiledPolicyPacket) -> list[str]:
    return [
        constraint_id
        for category_name, constraints in _constraint_categories(compiled_packet)
        for constraint_id in [
            f"{category_name}:{index}" for index, _constraint in enumerate(constraints)
        ]
    ]


def _all_constraint_chunk_ids(compiled_packet: CompiledPolicyPacket) -> list[str]:
    return _ordered_unique(
        [
            citation_id
            for _category_name, constraints in _constraint_categories(compiled_packet)
            for constraint in constraints
            for citation_id in constraint.citation_ids
        ]
    )


def _constraint_categories(
    compiled_packet: CompiledPolicyPacket,
) -> tuple[tuple[str, Sequence[CompiledPolicyConstraint]], ...]:
    return (
        ("required_steps", compiled_packet.required_steps),
        ("forbidden_patterns", compiled_packet.forbidden_patterns),
        ("architectural_expectations", compiled_packet.architectural_expectations),
        ("test_expectations", compiled_packet.test_expectations),
        ("style_constraints", compiled_packet.style_constraints),
    )


def _constraint_chunk_ids(
    constraints: Sequence[CompiledPolicyConstraint],
) -> list[str]:
    return _ordered_unique(
        [citation_id for constraint in constraints for citation_id in constraint.citation_ids]
    )


def _dedupe_triggers(triggers: Sequence[RegenerationTrigger]) -> list[RegenerationTrigger]:
    deduped: list[RegenerationTrigger] = []
    seen: set[tuple[str, str, tuple[str, ...], tuple[str, ...]]] = set()
    for trigger in triggers:
        if not (trigger.failure_reasons or trigger.constraint_ids or trigger.chunk_ids):
            continue
        key = (
            trigger.kind,
            trigger.metric_name,
            tuple(trigger.constraint_ids),
            tuple(trigger.chunk_ids),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(trigger)
    return deduped


def _ordered_unique(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def _close_component(component: object | None) -> None:
    close = getattr(component, "close", None)
    if callable(close):
        close()


__all__ = [
    "PolicyRegenerationService",
    "create_policy_regeneration_service",
    "regeneration_triggers_from_conformance",
]
