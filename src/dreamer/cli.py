"""`dreamer`: chat with Agent A while Agent B dreams the machine."""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys
import tempfile
from pathlib import Path

from claude_agent_sdk import AssistantMessage, ClaudeSDKError, TextBlock, ToolUseBlock

from dreamer.agent import Agent
from dreamer.machine import Machine
from dreamer.transcript import Transcript, generate_name, transcript_path, validate_name

RUNS_DIR = Path("runs")


def _parse(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="dreamer", description="A coding agent on a dreamed machine.")
    p.add_argument("task", nargs="?", help="first prompt to send before the chat loop")
    p.add_argument("--agent", help="agent name; existing names resume", default=None)
    p.add_argument("--machine", help="machine name; existing names resume", default=None)
    p.add_argument("--model", help="model for the agent", default=None)
    p.add_argument("--machine-model", help="model for the machine", default=None)
    return p.parse_args(argv)


async def _turn(agent: Agent, prompt: str) -> None:
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGINT, lambda: asyncio.ensure_future(agent.interrupt()))
    try:
        async for message in agent.send(prompt):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        print(block.text, flush=True)
                    elif isinstance(block, ToolUseBlock):
                        print(f"$ {block.input.get('command', '')}", flush=True)
    finally:
        loop.remove_signal_handler(signal.SIGINT)


async def _run(args: argparse.Namespace) -> None:
    agent_name = validate_name(args.agent) if args.agent else generate_name()
    machine_name = validate_name(args.machine) if args.machine else generate_name()
    print(f"agent: {agent_name}   machine: {machine_name}")
    print(
        f"transcripts: {transcript_path(RUNS_DIR, agent_name, 'agent')}  "
        f"{transcript_path(RUNS_DIR, machine_name, 'machine')}"
    )

    machine = Machine(
        Transcript(transcript_path(RUNS_DIR, machine_name, "machine")),
        machine_name,
        args.machine_model,
        Path(tempfile.mkdtemp(prefix="m-")),
    )
    agent = Agent(
        Transcript(transcript_path(RUNS_DIR, agent_name, "agent")),
        agent_name,
        args.model,
        machine.run,
        Path(tempfile.mkdtemp(prefix="work-", dir="/tmp")),
    )
    async with machine, agent:
        pending: str | None = args.task
        while True:
            if pending is None:
                try:
                    pending = input("\n> ")
                except (EOFError, KeyboardInterrupt):
                    print()
                    return
            if pending.strip() in {"", "/quit"}:
                if pending.strip() == "/quit":
                    return
                pending = None
                continue
            try:
                await _turn(agent, pending)
            except ClaudeSDKError as e:
                agent.transcript.write("error", message=str(e))
                print(f"error: {e}", file=sys.stderr)
            pending = None


def main() -> None:
    args = _parse(sys.argv[1:])
    try:
        if args.agent:
            validate_name(args.agent)
        if args.machine:
            validate_name(args.machine)
    except ValueError as e:
        sys.exit(str(e))
    asyncio.run(_run(args))
