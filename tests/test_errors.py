from __future__ import annotations

from time import perf_counter

import pytest
from pydantic import BaseModel, ValidationError

from policynim.errors import PolicyNIMError, elapsed_ms, find_failure_class, format_validation_error


class _Payload(BaseModel):
    task: str


def test_format_validation_error_uses_first_location() -> None:
    with pytest.raises(ValidationError) as excinfo:
        _Payload.model_validate({})

    message = format_validation_error("Preflight request", excinfo.value)

    assert message.startswith("Preflight request is invalid at task:")


def test_find_failure_class_walks_exception_causes() -> None:
    try:
        raise PolicyNIMError("upstream timeout", failure_class="timeout")
    except PolicyNIMError as exc:
        try:
            raise RuntimeError("wrapper") from exc
        except RuntimeError as wrapper:
            assert find_failure_class(wrapper) == "timeout"


def test_elapsed_ms_returns_non_negative_float() -> None:
    value = elapsed_ms(perf_counter())

    assert isinstance(value, float)
    assert value >= 0.0
