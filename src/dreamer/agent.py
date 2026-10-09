"""Agent A: a coding agent whose only tool is a Bash that never runs."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from importlib.resources import files
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookMatcher,
    Message,
    ResultMessage,
    SdkMcpTool,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    create_sdk_mcp_server,
    tool,
)

from dreamer.machine import CommandOutput, MachineError
from dreamer.transcript import Transcript, compact_hook, start_session

RunCommand = Callable[[str], Awaitable[CommandOutput]]

MAX_OUTPUT_CHARS = 30_000
TOOL_NAME = "mcp__os__Bash"

BASH_DESCRIPTION = """Executes a given bash command in a persistent shell session with optional \
timeout, ensuring proper handling and security measures.

Before executing the command, please follow these steps:
1. Directory verification: if the command will create new directories or files, first use ls to \
verify the parent directory exists and is the correct location.
2. Command execution: always quote file paths that contain spaces with double quotes.

Usage notes:
- The command argument is required.
- You can specify an optional timeout in milliseconds. If not specified, commands will time out \
after 120000ms (2 minutes).
- It is very helpful if you write a clear, concise description of what this command does in 5-10 \
words.
- If the output exceeds 30000 characters, output will be truncated before being returned to you.
- Avoid using search commands like find and grep where a more specific tool would do; here there \
is no other tool, so use them freely.
- When issuing multiple commands, use ; or && to chain them. Do not use newlines to separate \
commands.
- Try to maintain your current working directory by using absolute paths and avoiding cd."""

BASH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "command": {"type": "string", "description": "The command to execute"},
        "description": {
            "type": "string",
            "description": "Clear, concise description of what this command does in 5-10 words.",
        },
        "timeout": {"type": "integer", "description": "Optional timeout in milliseconds"},
    },
    "required": ["command"],
}


def format_result(out: CommandOutput) -> tuple[str, bool]:
    text = out.stdout
    if out.stderr:
        text += out.stderr
    if out.exit_code != 0:
        text += f"\nExit code {out.exit_code}"
    if len(text) > MAX_OUTPUT_CHARS:
        text = text[:MAX_OUTPUT_CHARS] + "... [truncated]"
    return text, out.exit_code != 0


def make_bash_tool(run_command: RunCommand) -> SdkMcpTool[Any]:
    @tool("Bash", BASH_DESCRIPTION, BASH_SCHEMA)
    async def bash(args: dict[str, Any]) -> dict[str, Any]:
        try:
            text, is_error = format_result(await run_command(str(args["command"])))
        except MachineError as e:
            text, is_error = f"bash: command execution failed ({e.subtype})", True
        except Exception as e:  # noqa: BLE001
            text, is_error = f"bash: command execution failed ({type(e).__name__})", True
        return {"content": [{"type": "text", "text": text}], "is_error": is_error}

    return bash


def _prompt() -> str:
    return files("dreamer").joinpath("prompts/agent.md").read_text()


class Agent:
    def __init__(
        self, transcript: Transcript, name: str, model: str | None, run_command: RunCommand
    ) -> None:
        self.transcript = transcript
        self._session = start_session(transcript, name, "agent", model)
        server = create_sdk_mcp_server("os", tools=[make_bash_tool(run_command)])
        self._client = ClaudeSDKClient(
            ClaudeAgentOptions(
                tools=[],
                mcp_servers={"os": server},
                allowed_tools=[TOOL_NAME],
                permission_mode="bypassPermissions",
                setting_sources=[],
                model=model,
                system_prompt=_prompt(),
                hooks={"PreCompact": [HookMatcher(hooks=[compact_hook(transcript)])]},
                session_id=self._session.new_id,
                resume=self._session.resume_id,
            )
        )

    async def __aenter__(self) -> Agent:
        await self._client.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._client.disconnect()

    async def interrupt(self) -> None:
        await self._client.interrupt()

    async def send(self, prompt: str) -> AsyncIterator[Message]:
        self.transcript.write("user", text=prompt)
        await self._client.query(prompt)
        async for message in self._client.receive_response():
            self._record(message)
            yield message

    def _record(self, message: Message) -> None:
        t = self.transcript
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    t.write("text", text=block.text)
                elif isinstance(block, ThinkingBlock) and block.thinking:
                    t.write("thinking", text=block.thinking)
                elif isinstance(block, ToolUseBlock):
                    t.write("tool_use", tool_use_id=block.id, name=block.name, input=block.input)
        elif isinstance(message, UserMessage) and isinstance(message.content, list):
            for block in message.content:
                if isinstance(block, ToolResultBlock):
                    t.write(
                        "tool_result",
                        tool_use_id=block.tool_use_id,
                        content=block.content,
                        is_error=bool(block.is_error),
                    )
        elif isinstance(message, ResultMessage):
            t.write(
                "result",
                subtype=message.subtype,
                num_turns=message.num_turns,
                duration_ms=message.duration_ms,
                usage=message.usage,
                cost_usd=message.total_cost_usd,
            )
