"""Optional NeMo Guardrails output-rail adapter for generated preflight drafts."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from importlib import import_module
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as installed_version
from importlib.resources import files
from typing import Any, Protocol

from pydantic import ValidationError

from policynim.contracts import Generator
from policynim.errors import ConfigurationError, ProviderError
from policynim.providers.nvidia import NVIDIAGenerator
from policynim.settings import Settings
from policynim.types import (
    CompiledPolicyPacket,
    GeneratedPolicyGuidance,
    GeneratedPreflightDraft,
    PreflightRequest,
    RegenerationContext,
    ScoredChunk,
)

_NEMO_GUARDRAILS_DISTRIBUTION = "nemoguardrails"
_NVIDIA_GUARDRAILS_EXTRA = "nvidia-guardrails"
_NVIDIA_GUARDRAILS_INSTALL_HINT = "uv sync --extra nvidia-guardrails"
_DEFAULT_GUARDRAILS_MODEL = "nvidia/llama-3.3-nemotron-super-49b-v1.5"
_MODEL_PLACEHOLDER = "__POLICYNIM_NVIDIA_CHAT_MODEL__"
_ASSET_ROOT = ("templates", "nvidia_guardrails", "preflight_output")
_CONFIG_ASSET = "config.yml"
_COLANG_ASSET = "rails.co"


class _OutputRailsClient(Protocol):
    def check(
        self,
        messages: list[dict[str, str]],
        rail_types: Sequence[Any] | None = None,
    ) -> Any:
        """Run Guardrails checks for one message list."""
        ...


class NeMoGuardrailsPreflightGenerator(Generator):
    """Wrap an existing generator with NeMo Guardrails output-rail validation."""

    def __init__(
        self,
        *,
        base_generator: Generator,
        rails: _OutputRailsClient | None = None,
        rail_type_output: Any | None = None,
        owns_rails: bool = False,
        model: str | None = None,
    ) -> None:
        if rails is None:
            rails, rail_type_output = _create_default_output_rails(
                model=model or _DEFAULT_GUARDRAILS_MODEL
            )
            owns_rails = True

        self._base_generator = base_generator
        self._rails = rails
        self._rail_type_output = rail_type_output
        self._owns_rails = owns_rails

    @classmethod
    def from_settings(cls, settings: Settings) -> NeMoGuardrailsPreflightGenerator:
        """Construct an internal Guardrails-backed generator from settings."""
        rails: _OutputRailsClient | None = None
        base_generator: Generator | None = None
        try:
            rails, rail_type_output = _create_default_output_rails(model=settings.nvidia_chat_model)
            base_generator = NVIDIAGenerator.from_settings(settings)
            return cls(
                base_generator=base_generator,
                rails=rails,
                rail_type_output=rail_type_output,
                owns_rails=True,
            )
        except Exception:
            _close_component(base_generator)
            _close_component(rails)
            raise

    def generate_preflight(
        self,
        request: PreflightRequest,
        context: Sequence[ScoredChunk],
        *,
        compiled_packet: CompiledPolicyPacket | None = None,
        regeneration_context: RegenerationContext | None = None,
    ) -> GeneratedPreflightDraft:
        """Generate a preflight draft and fail closed if output rails reject it."""
        generated = self._base_generator.generate_preflight(
            request,
            context,
            compiled_packet=compiled_packet,
            regeneration_context=regeneration_context,
        )
        draft = _coerce_generated_draft(generated)
        guardrailed_content = self._check_output_rails(draft)
        guardrailed_draft = _parse_guardrailed_draft(guardrailed_content)
        _validate_guardrailed_draft(
            guardrailed_draft,
            context,
            regeneration_context=regeneration_context,
        )
        return guardrailed_draft

    def close(self) -> None:
        """Release owned generator and Guardrails resources."""
        _close_component(self._base_generator)
        if self._owns_rails:
            _close_component(self._rails)

    def _check_output_rails(self, draft: GeneratedPreflightDraft) -> str:
        content = json.dumps(draft.model_dump(mode="json"), sort_keys=True)
        rail_types = [self._rail_type_output] if self._rail_type_output is not None else None
        try:
            result = self._rails.check(
                [{"role": "assistant", "content": content}],
                rail_types=rail_types,
            )
        except Exception as exc:
            raise ProviderError(
                "NeMo Guardrails output rail execution failed.",
                failure_class="guardrails_execution",
            ) from exc

        status_label = _rail_status_label(result)
        if status_label.endswith("blocked"):
            raise ProviderError(
                "NeMo Guardrails output rails blocked the generated preflight draft.",
                failure_class="guardrails_blocked",
            )
        if not status_label.endswith(("passed", "modified")):
            raise ProviderError(
                "NeMo Guardrails output rails returned an unrecognized status.",
                failure_class="invalid_response",
            )

        checked_content = _rail_result_content(result)
        if not isinstance(checked_content, str) or not checked_content.strip():
            raise ProviderError(
                "NeMo Guardrails output rails returned an empty preflight draft.",
                failure_class="invalid_response",
            )
        return checked_content


def _create_default_output_rails(
    *,
    model: str,
) -> tuple[_OutputRailsClient, Any]:
    _require_optional_guardrails_distribution()
    try:
        guardrails_module = import_module("nemoguardrails")
        options_module = import_module("nemoguardrails.rails.llm.options")
    except ModuleNotFoundError as exc:
        raise ConfigurationError(
            "NeMo Guardrails output rails require the optional "
            f"`{_NVIDIA_GUARDRAILS_EXTRA}` extra. Install it with "
            f"`{_NVIDIA_GUARDRAILS_INSTALL_HINT}`.",
            failure_class="missing_optional_dependency",
        ) from exc

    try:
        LLMRails = getattr(guardrails_module, "LLMRails")
        RailsConfig = getattr(guardrails_module, "RailsConfig")
        RailType = getattr(options_module, "RailType")
    except AttributeError as exc:
        raise ConfigurationError(
            "Installed NeMo Guardrails package does not expose the expected Python API.",
            failure_class="guardrails_configuration",
        ) from exc

    colang_content, yaml_content = _load_guardrails_assets(model=model)
    try:
        config = RailsConfig.from_content(
            colang_content=colang_content,
            yaml_content=yaml_content,
        )
        return LLMRails(config), RailType.OUTPUT
    except ModuleNotFoundError as exc:
        raise ConfigurationError(
            "NeMo Guardrails NVIDIA output rails require the optional "
            f"`{_NVIDIA_GUARDRAILS_EXTRA}` extra. Install it with "
            f"`{_NVIDIA_GUARDRAILS_INSTALL_HINT}`.",
            failure_class="missing_optional_dependency",
        ) from exc
    except Exception as exc:
        raise ConfigurationError(
            "Unable to initialize packaged NeMo Guardrails output rails.",
            failure_class="guardrails_configuration",
        ) from exc


def _require_optional_guardrails_distribution() -> None:
    try:
        installed_version(_NEMO_GUARDRAILS_DISTRIBUTION)
    except PackageNotFoundError as exc:
        raise ConfigurationError(
            "Guardrails-backed preflight generation requires optional package "
            f"`{_NEMO_GUARDRAILS_DISTRIBUTION}`. Install it with "
            f"`{_NVIDIA_GUARDRAILS_INSTALL_HINT}`.",
            failure_class="missing_optional_dependency",
        ) from exc


def _load_guardrails_assets(*, model: str) -> tuple[str, str]:
    try:
        asset_dir = files("policynim").joinpath(*_ASSET_ROOT)
        colang_content = asset_dir.joinpath(_COLANG_ASSET).read_text(encoding="utf-8")
        yaml_content = asset_dir.joinpath(_CONFIG_ASSET).read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError) as exc:
        raise ConfigurationError(
            "Packaged NeMo Guardrails output-rail assets are missing.",
            failure_class="missing_guardrails_assets",
        ) from exc

    return colang_content, yaml_content.replace(_MODEL_PLACEHOLDER, model)


def _coerce_generated_draft(generated: Any) -> GeneratedPreflightDraft:
    try:
        if isinstance(generated, GeneratedPreflightDraft):
            return generated
        if isinstance(generated, Mapping):
            return GeneratedPreflightDraft.model_validate(generated)

        payload = {
            "summary": getattr(generated, "summary"),
            "applicable_policies": [
                _coerce_generated_policy_guidance(item)
                for item in getattr(generated, "applicable_policies", [])
            ],
            "plan_steps": list(getattr(generated, "plan_steps", [])),
            "implementation_guidance": list(getattr(generated, "implementation_guidance", [])),
            "review_flags": list(getattr(generated, "review_flags", [])),
            "tests_required": list(getattr(generated, "tests_required", [])),
            "citation_ids": list(getattr(generated, "citation_ids", [])),
            "insufficient_context": bool(getattr(generated, "insufficient_context", False)),
        }
        return GeneratedPreflightDraft.model_validate(payload)
    except (AttributeError, TypeError, ValidationError) as exc:
        raise ProviderError(
            "Base generator returned a malformed preflight draft.",
            failure_class="invalid_response",
        ) from exc


def _coerce_generated_policy_guidance(item: Any) -> GeneratedPolicyGuidance:
    if isinstance(item, GeneratedPolicyGuidance):
        return item
    if isinstance(item, Mapping):
        return GeneratedPolicyGuidance.model_validate(item)
    return GeneratedPolicyGuidance.model_validate(
        {
            "policy_id": getattr(item, "policy_id"),
            "title": getattr(item, "title"),
            "rationale": getattr(item, "rationale"),
            "citation_ids": list(getattr(item, "citation_ids", [])),
        }
    )


def _parse_guardrailed_draft(content: str) -> GeneratedPreflightDraft:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ProviderError(
            "NeMo Guardrails output rails returned invalid JSON.",
            failure_class="invalid_response",
        ) from exc

    if not isinstance(payload, dict):
        raise ProviderError(
            "NeMo Guardrails output rails returned malformed JSON.",
            failure_class="invalid_response",
        )

    try:
        return GeneratedPreflightDraft.model_validate(payload)
    except ValidationError as exc:
        raise ProviderError(
            "NeMo Guardrails output rails returned a malformed preflight draft.",
            failure_class="invalid_response",
        ) from exc


def _validate_guardrailed_draft(
    draft: GeneratedPreflightDraft,
    context: Sequence[ScoredChunk],
    *,
    regeneration_context: RegenerationContext | None,
) -> None:
    allowed_chunk_ids = {chunk.chunk_id for chunk in context}
    unsupported_citation_ids = [
        citation_id
        for citation_id in _draft_citation_ids(draft)
        if citation_id not in allowed_chunk_ids
    ]
    if unsupported_citation_ids:
        raise ProviderError(
            "NeMo Guardrails output rails returned unsupported citation ids: "
            f"{', '.join(_ordered_unique(unsupported_citation_ids))}.",
            failure_class="invalid_response",
        )

    if regeneration_context is None:
        return

    unsupported_trigger_chunk_ids = [
        chunk_id
        for trigger in regeneration_context.triggers
        for chunk_id in trigger.chunk_ids
        if chunk_id not in allowed_chunk_ids
    ]
    if unsupported_trigger_chunk_ids:
        raise ProviderError(
            "Regeneration context referenced chunk ids outside the retained context: "
            f"{', '.join(_ordered_unique(unsupported_trigger_chunk_ids))}.",
            failure_class="invalid_response",
        )


def _draft_citation_ids(draft: GeneratedPreflightDraft) -> list[str]:
    return _ordered_unique(
        [
            *draft.citation_ids,
            *[
                citation_id
                for policy in draft.applicable_policies
                for citation_id in policy.citation_ids
            ],
        ]
    )


def _rail_status_label(result: Any) -> str:
    if isinstance(result, Mapping):
        status = result.get("status")
    else:
        status = getattr(result, "status", None)
    if status is None:
        return ""
    label = getattr(status, "value", None) or getattr(status, "name", None) or str(status)
    return str(label).strip().lower()


def _rail_result_content(result: Any) -> Any:
    if isinstance(result, Mapping):
        return result.get("content")
    return getattr(result, "content", None)


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


__all__ = ["NeMoGuardrailsPreflightGenerator"]
