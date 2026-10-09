from dreamer.agent import MAX_OUTPUT_CHARS, format_result, make_bash_tool
from dreamer.machine import CommandOutput, MachineError


def test_format_success_stdout_only() -> None:
    assert format_result(CommandOutput("hello\n", "", 0)) == ("hello\n", False)


def test_format_stderr_with_zero_exit_is_not_error() -> None:
    text, is_error = format_result(CommandOutput("", "warn\n", 0))
    assert text == "warn\n" and is_error is False


def test_format_nonzero_exit_appends_code_and_flags_error() -> None:
    text, is_error = format_result(CommandOutput("out\n", "bad\n", 2))
    assert text == "out\nbad\n\nExit code 2"
    assert is_error is True


def test_format_truncates_long_output() -> None:
    text, _ = format_result(CommandOutput("x" * (MAX_OUTPUT_CHARS + 10), "", 0))
    assert text.endswith("... [truncated]")
    assert len(text) == MAX_OUTPUT_CHARS + len("... [truncated]")


async def test_bash_tool_returns_machine_output() -> None:
    async def fake(command: str) -> CommandOutput:
        return CommandOutput(f"ran {command}\n", "", 0)

    tool = make_bash_tool(fake)
    result = await tool.handler({"command": "ls"})
    assert result["content"] == [{"type": "text", "text": "ran ls\n"}]
    assert result.get("is_error", False) is False


async def test_bash_tool_nonzero_exit_sets_is_error() -> None:
    async def fake(command: str) -> CommandOutput:
        return CommandOutput("", "nope\n", 1)

    result = await make_bash_tool(fake).handler({"command": "false"})
    assert result["is_error"] is True


async def test_bash_tool_machine_error_becomes_shell_error() -> None:
    async def fake(command: str) -> CommandOutput:
        raise MachineError("error_max_structured_output_retries", [])

    result = await make_bash_tool(fake).handler({"command": "ls"})
    assert result["is_error"] is True
    assert result["content"][0]["text"].startswith("bash: command execution failed")


async def test_bash_tool_unexpected_exception_also_contained() -> None:
    async def fake(command: str) -> CommandOutput:
        raise RuntimeError("boom")

    result = await make_bash_tool(fake).handler({"command": "ls"})
    assert result["is_error"] is True


def test_bash_tool_schema_and_name() -> None:
    tool = make_bash_tool(lambda c: None)  # type: ignore[arg-type,return-value]
    assert tool.name == "Bash"
    assert tool.input_schema["required"] == ["command"]
    assert set(tool.input_schema["properties"]) == {"command", "description", "timeout"}
