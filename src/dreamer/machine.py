"""Agent B: a model that answers shell commands as if it were a Linux machine."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, HookMatcher, ResultMessage

from dreamer.transcript import Transcript, compact_hook, start_session

OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "stdout": {"type": "string"},
        "stderr": {"type": "string"},
        "exit_code": {"type": "integer"},
    },
    "required": ["stdout", "stderr", "exit_code"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class CommandOutput:
    stdout: str
    stderr: str
    exit_code: int


class MachineError(Exception):
    def __init__(self, subtype: str, errors: list[str]) -> None:
        self.subtype = subtype
        self.errors = errors
        super().__init__(
            f"machine turn ended with {subtype}: {'; '.join(errors) or 'no output'}"
        )


def output_from_result(
    subtype: str, structured_output: Any, errors: list[str] | None
) -> CommandOutput:
    if subtype != "success" or not isinstance(structured_output, dict):
        raise MachineError(subtype, list(errors or []))
    return CommandOutput(
        stdout=str(structured_output["stdout"]),
        stderr=str(structured_output["stderr"]),
        exit_code=int(structured_output["exit_code"]),
    )


def _prompt() -> str:
    return files("dreamer").joinpath("prompts/machine.md").read_text()


class Machine:
    def __init__(
        self,
        transcript: Transcript,
        name: str,
        model: str | None,
        scratch_dir: Path,
        client: ClaudeSDKClient | None = None,
    ) -> None:
        self._transcript = transcript
        self._session = start_session(transcript, name, "machine", model)
        scratch_dir.mkdir(parents=True, exist_ok=True)
        self._client = client or ClaudeSDKClient(
            ClaudeAgentOptions(
                tools=[],
                setting_sources=[],
                permission_mode="bypassPermissions",
                strict_mcp_config=True,
                cwd=scratch_dir,
                model=model,
                system_prompt=_prompt(),
                output_format={"type": "json_schema", "schema": OUTPUT_SCHEMA},
                hooks={"PreCompact": [HookMatcher(hooks=[compact_hook(transcript)])]},
                session_id=self._session.new_id,
                resume=self._session.resume_id,
            )
        )
        self._lock = asyncio.Lock()
        self._stale_turn = False

    async def __aenter__(self) -> Machine:
        await self._client.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._client.disconnect()

    async def run(self, command: str) -> CommandOutput:
        async with self._lock:
            try:
                return await self._run(command)
            except asyncio.CancelledError:
                self._stale_turn = True
                raise
            except Exception as e:
                self._transcript.write("error", message=repr(e))
                raise

    async def _drain(self) -> ResultMessage | None:
        result: ResultMessage | None = None
        async for message in self._client.receive_response():
            if isinstance(message, ResultMessage):
                result = message
        return result

    async def _run(self, command: str) -> CommandOutput:
        if self._stale_turn:
            # Agent A's interrupt cancels this coroutine mid-turn while B's CLI keeps
            # running; without draining, the next query returns the previous command's
            # result and every later output is shifted by one.
            await self._client.interrupt()
            await self._drain()
            self._stale_turn = False
        self._transcript.write("user", text=command)
        await self._client.query(command)
        result = await self._drain()
        if result is None:
            raise MachineError("no_result", [])
        self._transcript.write(
            "result",
            subtype=result.subtype,
            num_turns=result.num_turns,
            duration_ms=result.duration_ms,
            usage=result.usage,
            cost_usd=result.total_cost_usd,
        )
        out = output_from_result(result.subtype, result.structured_output, result.errors)
        self._transcript.write(
            "output", stdout=out.stdout, stderr=out.stderr, exit_code=out.exit_code
        )
        return out
