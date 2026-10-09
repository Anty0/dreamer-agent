import pytest

from dreamer.machine import OUTPUT_SCHEMA, CommandOutput, MachineError, output_from_result


def test_schema_requires_all_fields() -> None:
    assert set(OUTPUT_SCHEMA["required"]) == {"stdout", "stderr", "exit_code"}
    assert OUTPUT_SCHEMA["additionalProperties"] is False


def test_output_from_success() -> None:
    out = output_from_result("success", {"stdout": "a", "stderr": "", "exit_code": 0}, None)
    assert out == CommandOutput("a", "", 0)


def test_output_from_success_without_structured_output_raises() -> None:
    with pytest.raises(MachineError) as e:
        output_from_result("success", None, None)
    assert e.value.subtype == "success"


def test_output_from_retry_error_raises_with_errors() -> None:
    with pytest.raises(MachineError) as e:
        output_from_result("error_max_structured_output_retries", None, ["bad json"])
    assert e.value.errors == ["bad json"]
    assert "error_max_structured_output_retries" in str(e.value)
