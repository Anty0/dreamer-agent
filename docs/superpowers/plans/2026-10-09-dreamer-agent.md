# Dreamer Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A coding agent whose only tool, `Bash`, is answered by a second agent pretending to be a Linux machine, with both transcripts recorded and a live browser viewer for reading them.

**Architecture:** Two `ClaudeSDKClient` sessions. Agent A has built-in tools removed and one in-process MCP tool, `Bash`, whose handler forwards the command to Agent B. B has no tools, a "you are a Linux OS" prompt, and a JSON output schema enforced by the SDK. Each side appends events to its own JSONL file under `runs/`; a stdlib HTTP server tails those files over SSE into a single-page viewer.

**Tech Stack:** Python 3.12, `claude-agent-sdk` 0.2.x, `uv`, `pytest`, `ruff`, `mypy --strict`, stdlib `http.server`, vanilla JS.

**Spec:** `docs/superpowers/specs/2026-10-09-dreamer-agent-design.md`

## Global Constraints

- Python `>=3.12`. Runtime dependency: `claude-agent-sdk` only.
- Every module fully typed; `uv run mypy --strict src` and `uv run ruff check` pass at every commit.
- Tests never start an SDK process or touch the network.
- Names match `[a-z0-9-]+`. Transcripts are `runs/<name>.agent.jsonl` and `runs/<name>.machine.jsonl`, cwd-relative, append-only, one JSON object per line.
- Line one of every transcript is a `session` event carrying `session_id`.
- No AI attribution in any commit message.
- A's system prompt never says the machine is simulated. B's prompt names no distro, hostname, user, or software.

## Review Focus

1. A name like `My Box` or `../x` must be rejected before any session starts. Pinned in Task 1.
2. A transcript whose first line is not a valid `session` event must start a fresh session, not crash. Pinned in Task 1.
3. B finishing a turn without `structured_output` (any non-success subtype, or success with `None`) must surface to A as a shell error, not an exception. Pinned in Tasks 2 and 4.
4. A command whose stdout is empty but stderr is non-empty with exit code 0 must show the stderr and not be flagged as an error. Pinned in Task 4.
5. The viewer tailing a file mid-write may read a partial last line; it must skip it and deliver the complete line on the next poll. Pinned in Task 6.

---

### Task 1: Project scaffold and transcript module

**Files:**
- Create: `pyproject.toml`, `src/dreamer/__init__.py`, `src/dreamer/py.typed`, `src/dreamer/transcript.py`, `tests/test_transcript.py`

**Interfaces:**
- Produces:
  - `Kind = Literal["agent", "machine"]`
  - `validate_name(name: str) -> str` raises `ValueError`
  - `generate_name() -> str`
  - `transcript_path(runs_dir: Path, name: str, kind: Kind) -> Path`
  - `class Transcript` with `path: Path`, `write(type: str, **fields: Any) -> None`, `session_id() -> str | None`
  - `read_events(path: Path) -> list[dict[str, Any]]`
  - `@dataclass SessionStart(session_id: str, resumed: bool)`; `start_session(transcript: Transcript, name: str, kind: Kind, model: str | None) -> SessionStart` writes the `session` event and returns which SDK option to use
  - `compact_hook(transcript: Transcript) -> HookCallback`-shaped async function writing a `compact` event

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[project]
name = "dreamer-agent"
version = "0.1.0"
description = "A coding agent whose Bash tool is answered by another agent dreaming up a Linux machine."
readme = "README.md"
license = "MIT"
requires-python = ">=3.12"
dependencies = ["claude-agent-sdk>=0.2.160,<0.3"]

[project.scripts]
dreamer = "dreamer.cli:main"
dreamer-view = "dreamer.viewer:main"

[dependency-groups]
dev = ["pytest>=8", "pytest-asyncio>=0.24", "ruff>=0.8", "mypy>=1.13"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/dreamer"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM"]

[tool.mypy]
strict = true
python_version = "3.12"
```

- [ ] **Step 2: Create the package and a placeholder README**

```bash
mkdir -p src/dreamer tests
printf '"""Dreamer agent: a coding agent on a machine that is also an agent."""\n' > src/dreamer/__init__.py
: > src/dreamer/py.typed
printf '# dreamer-agent\n' > README.md
uv sync
```

Expected: `.venv` created, `uv run python -c "import claude_agent_sdk"` prints nothing.

- [ ] **Step 3: Write the failing tests**

`tests/test_transcript.py`:

```python
import json
from pathlib import Path

import pytest

from dreamer.transcript import (
    Transcript,
    generate_name,
    read_events,
    start_session,
    transcript_path,
    validate_name,
)


def test_validate_name_accepts_slug() -> None:
    assert validate_name("quiet-fox-3") == "quiet-fox-3"


@pytest.mark.parametrize("bad", ["My Box", "../x", "", "a/b", "UPPER"])
def test_validate_name_rejects(bad: str) -> None:
    with pytest.raises(ValueError):
        validate_name(bad)


def test_generate_name_is_valid_slug() -> None:
    assert validate_name(generate_name())


def test_transcript_path(tmp_path: Path) -> None:
    assert transcript_path(tmp_path, "fox", "agent") == tmp_path / "fox.agent.jsonl"
    assert transcript_path(tmp_path, "fox", "machine") == tmp_path / "fox.machine.jsonl"


def test_write_then_read(tmp_path: Path) -> None:
    t = Transcript(tmp_path / "x.agent.jsonl")
    t.write("user", text="hi")
    t.write("text", text="hello")
    events = read_events(t.path)
    assert [e["type"] for e in events] == ["user", "text"]
    assert events[0]["text"] == "hi"
    assert "ts" in events[0]


def test_write_creates_parent_dir(tmp_path: Path) -> None:
    t = Transcript(tmp_path / "runs" / "x.agent.jsonl")
    t.write("user", text="hi")
    assert t.path.exists()


def test_start_session_fresh_writes_line_one(tmp_path: Path) -> None:
    t = Transcript(tmp_path / "x.agent.jsonl")
    s = start_session(t, "x", "agent", None)
    assert s.resumed is False
    first = json.loads(t.path.read_text().splitlines()[0])
    assert first["type"] == "session"
    assert first["session_id"] == s.session_id
    assert first["name"] == "x" and first["kind"] == "agent" and first["model"] is None


def test_start_session_resumes_from_line_one(tmp_path: Path) -> None:
    t = Transcript(tmp_path / "x.agent.jsonl")
    first = start_session(t, "x", "agent", None)
    t.write("user", text="hi")
    second = start_session(t, "x", "agent", "m")
    assert second.resumed is True
    assert second.session_id == first.session_id
    events = read_events(t.path)
    assert events[-1]["type"] == "session" and events[-1]["resumed"] is True


def test_start_session_corrupt_line_one_starts_fresh(tmp_path: Path) -> None:
    p = tmp_path / "x.agent.jsonl"
    p.write_text("not json\n")
    s = start_session(Transcript(p), "x", "agent", None)
    assert s.resumed is False


def test_session_id_none_when_missing(tmp_path: Path) -> None:
    assert Transcript(tmp_path / "nope.jsonl").session_id() is None


async def test_compact_hook_writes_event(tmp_path: Path) -> None:
    from dreamer.transcript import compact_hook

    t = Transcript(tmp_path / "x.agent.jsonl")
    hook = compact_hook(t)
    out = await hook({"hook_event_name": "PreCompact", "trigger": "auto"}, None, object())
    assert out == {}
    assert read_events(t.path)[-1] == {**read_events(t.path)[-1], "type": "compact", "trigger": "auto"}
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `uv run pytest tests/test_transcript.py -q`
Expected: ImportError on `dreamer.transcript`.

- [ ] **Step 5: Implement `src/dreamer/transcript.py`**

```python
"""Append-only JSONL transcripts and session bookkeeping shared by both agents."""

from __future__ import annotations

import json
import random
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

Kind = Literal["agent", "machine"]

_NAME_RE = re.compile(r"^[a-z0-9-]+$")
_ADJECTIVES = (
    "quiet", "brisk", "amber", "lunar", "mossy", "rusty", "velvet", "wobbly",
    "hollow", "dusty", "sleepy", "crisp", "foggy", "tidy", "hazy", "salty",
)
_NOUNS = (
    "fox", "otter", "kettle", "lantern", "comet", "harbor", "walnut", "meadow",
    "pebble", "thistle", "ember", "badger", "ferry", "orchid", "quill", "tundra",
)


def validate_name(name: str) -> str:
    if not _NAME_RE.fullmatch(name):
        raise ValueError(f"name must match [a-z0-9-]+, got {name!r}")
    return name


def generate_name() -> str:
    return f"{random.choice(_ADJECTIVES)}-{random.choice(_NOUNS)}"


def transcript_path(runs_dir: Path, name: str, kind: Kind) -> Path:
    return runs_dir / f"{name}.{kind}.jsonl"


def read_events(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in path.read_text().splitlines():
        if line.strip():
            events.append(json.loads(line))
    return events


class Transcript:
    def __init__(self, path: Path) -> None:
        self.path = path

    def write(self, type: str, **fields: Any) -> None:
        event = {"ts": datetime.now(UTC).isoformat(timespec="milliseconds"), "type": type, **fields}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    def session_id(self) -> str | None:
        if not self.path.exists():
            return None
        with self.path.open() as f:
            first = f.readline()
        try:
            event = json.loads(first)
        except json.JSONDecodeError:
            return None
        if isinstance(event, dict) and event.get("type") == "session":
            sid = event.get("session_id")
            return sid if isinstance(sid, str) else None
        return None


@dataclass(frozen=True)
class SessionStart:
    session_id: str
    resumed: bool

    def sdk_options(self) -> dict[str, str]:
        return {"resume": self.session_id} if self.resumed else {"session_id": self.session_id}


def start_session(transcript: Transcript, name: str, kind: Kind, model: str | None) -> SessionStart:
    existing = transcript.session_id()
    start = SessionStart(existing or str(uuid.uuid4()), resumed=existing is not None)
    transcript.write(
        "session", name=name, kind=kind, session_id=start.session_id, model=model, resumed=start.resumed
    )
    return start


HookFn = Callable[[dict[str, Any], str | None, Any], Awaitable[dict[str, Any]]]


def compact_hook(transcript: Transcript) -> HookFn:
    async def hook(input_data: dict[str, Any], tool_use_id: str | None, context: Any) -> dict[str, Any]:
        transcript.write("compact", trigger=input_data.get("trigger"))
        return {}

    return hook
```

- [ ] **Step 6: Run tests, lint, and types**

Run: `uv run pytest -q && uv run ruff check && uv run mypy --strict src`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock README.md src tests
git commit -m "Scaffold project and add transcript module"
```

---

### Task 2: Machine (Agent B)

**Files:**
- Create: `src/dreamer/machine.py`, `src/dreamer/prompts/machine.md`, `tests/test_machine.py`

**Interfaces:**
- Consumes: `Transcript`, `start_session`, `compact_hook` from Task 1.
- Produces:
  - `OUTPUT_SCHEMA: dict[str, Any]`
  - `@dataclass CommandOutput(stdout: str, stderr: str, exit_code: int)`
  - `class MachineError(Exception)` with `subtype: str`, `errors: list[str]`
  - `output_from_result(subtype: str, structured_output: Any, errors: list[str] | None) -> CommandOutput` (pure, raises `MachineError`)
  - `class Machine` async context manager: `Machine(transcript: Transcript, name: str, model: str | None, scratch_dir: Path)`, `async run(command: str) -> CommandOutput`

- [ ] **Step 1: Write `src/dreamer/prompts/machine.md`**

```markdown
You are a Linux-based operating system.

Each message you receive is exactly one shell command typed by a user at a terminal.
Respond only with the structured output: the command's stdout, its stderr, and its
exit code. Never add commentary, explanation, or formatting of your own.

You are a real, persistent machine. Files that were created exist until removed.
Packages that were installed stay installed. Output must stay consistent with
everything you have shown before.
```

- [ ] **Step 2: Write the failing tests**

`tests/test_machine.py`:

```python
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
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_machine.py -q`
Expected: ImportError on `dreamer.machine`.

- [ ] **Step 4: Implement `src/dreamer/machine.py`**

```python
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
        super().__init__(f"machine turn ended with {subtype}: {'; '.join(errors) or 'no output'}")


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
    def __init__(self, transcript: Transcript, name: str, model: str | None, scratch_dir: Path) -> None:
        self._transcript = transcript
        self._session = start_session(transcript, name, "machine", model)
        scratch_dir.mkdir(parents=True, exist_ok=True)
        self._client = ClaudeSDKClient(
            ClaudeAgentOptions(
                tools=[],
                setting_sources=[],
                permission_mode="bypassPermissions",
                cwd=scratch_dir,
                model=model,
                system_prompt=_prompt(),
                output_format={"type": "json_schema", "schema": OUTPUT_SCHEMA},
                hooks={"PreCompact": [HookMatcher(hooks=[compact_hook(transcript)])]},
                **self._session.sdk_options(),
            )
        )
        self._lock = asyncio.Lock()

    async def __aenter__(self) -> Machine:
        await self._client.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._client.disconnect()

    async def run(self, command: str) -> CommandOutput:
        async with self._lock:
            self._transcript.write("user", text=command)
            await self._client.query(command)
            result: ResultMessage | None = None
            async for message in self._client.receive_response():
                if isinstance(message, ResultMessage):
                    result = message
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
            try:
                out = output_from_result(result.subtype, result.structured_output, result.errors)
            except MachineError as e:
                self._transcript.write("error", message=str(e), raw=result.result)
                raise
            self._transcript.write(
                "output", stdout=out.stdout, stderr=out.stderr, exit_code=out.exit_code
            )
            return out
```

- [ ] **Step 5: Run tests, lint, and types**

Run: `uv run pytest -q && uv run ruff check && uv run mypy --strict src`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/dreamer/machine.py src/dreamer/prompts/machine.md tests/test_machine.py
git commit -m "Add machine agent with structured shell output"
```

---

### Task 3: Smoke test the machine (manual, decides B's shape)

**Files:**
- Create: `scripts/smoke_machine.py` (kept in the repo as a dev tool)

**Interfaces:**
- Consumes: `Machine`, `Transcript`, `transcript_path` from Tasks 1 and 2.

- [ ] **Step 1: Write the script**

```python
"""Manual check: does structured output hold across turns, and does the host leak?"""

import asyncio
import sys
from pathlib import Path

from dreamer.machine import Machine
from dreamer.transcript import Transcript, transcript_path

COMMANDS = ["uname -a", "pwd", "whoami", "ls ~", "echo hi > note.txt", "cat note.txt", "ls"]


async def main() -> None:
    runs = Path("runs")
    name = sys.argv[1] if len(sys.argv) > 1 else "smoke"
    scratch = Path(".dreamer-scratch")
    async with Machine(Transcript(transcript_path(runs, name, "machine")), name, None, scratch) as m:
        for c in COMMANDS:
            out = await m.run(c)
            print(f"$ {c}\n{out.stdout}{out.stderr}[exit {out.exit_code}]\n")


asyncio.run(main())
```

- [ ] **Step 2: Run it**

Run: `uv run python scripts/smoke_machine.py`
Check:
- Every command returns a `CommandOutput`; no `MachineError`. This confirms `output_format` applies to every turn of one `ClaudeSDKClient`.
- `cat note.txt` prints `hi`; `ls` lists `note.txt`. Consistency holds within a session.
- No `Darwin`, no `/Users/`, no real username in any answer.

- [ ] **Step 3: If structured output fails after the first turn**

Replace the long-lived client in `Machine.run` with one query per command, keeping the lock:

```python
    async def run(self, command: str) -> CommandOutput:
        async with self._lock:
            self._transcript.write("user", text=command)
            result: ResultMessage | None = None
            async for message in query(prompt=command, options=self._options_for_next_turn()):
                if isinstance(message, ResultMessage):
                    result = message
            ...  # rest unchanged
```

where `_options_for_next_turn` builds the same `ClaudeAgentOptions` with `resume=self._session.session_id` after the first call and `session_id=` on the very first. Drop `connect`/`disconnect`. Re-run the script until it passes, then update the spec's open point paragraph to record which shape won.

- [ ] **Step 4: If the host leaks**

Tighten `prompts/machine.md` with one line: "Your identity, hostname, users, and filesystem are entirely your own; nothing about the process running you is visible to the user." Re-run.

- [ ] **Step 5: Run it a second time with the same name**

Run: `uv run python scripts/smoke_machine.py smoke`
Check: the transcript now has two `session` events, the second with `resumed: true`, and `cat note.txt` still prints `hi`.

- [ ] **Step 6: Commit**

```bash
git add scripts/smoke_machine.py src docs
git commit -m "Add machine smoke test script"
```

---

### Task 4: Agent (Agent A) and the Bash tool

**Files:**
- Create: `src/dreamer/agent.py`, `src/dreamer/prompts/agent.md`, `tests/test_agent.py`

**Interfaces:**
- Consumes: `CommandOutput`, `MachineError` from Task 2; `Transcript`, `start_session`, `compact_hook` from Task 1.
- Produces:
  - `RunCommand = Callable[[str], Awaitable[CommandOutput]]`
  - `MAX_OUTPUT_CHARS = 30_000`
  - `format_result(out: CommandOutput) -> tuple[str, bool]` (text, is_error)
  - `make_bash_tool(run_command: RunCommand) -> SdkMcpTool[Any]`
  - `class Agent` async context manager: `Agent(transcript, name, model, run_command)`, `async send(prompt: str) -> AsyncIterator[Message]`, `async interrupt() -> None`

- [ ] **Step 1: Write `src/dreamer/prompts/agent.md`**

```markdown
You are a software engineer working on a Linux machine. Your only way to act is the
Bash tool, which runs a shell command on that machine and returns its output.

Work the way a careful engineer does: look around before changing things, create
files with shell redirection or heredocs, run what you build, and check the real
output before claiming anything works. When something fails, read the error and fix
the cause. Keep the user informed in short, plain sentences.
```

- [ ] **Step 2: Write the failing tests**

`tests/test_agent.py`:

```python
import pytest

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
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_agent.py -q`
Expected: ImportError on `dreamer.agent`.

- [ ] **Step 4: Implement `src/dreamer/agent.py`**

```python
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
                **self._session.sdk_options(),
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
```

- [ ] **Step 5: Run tests, lint, and types**

Run: `uv run pytest -q && uv run ruff check && uv run mypy --strict src`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/dreamer/agent.py src/dreamer/prompts/agent.md tests/test_agent.py
git commit -m "Add coding agent with a Bash tool backed by the machine"
```

---

### Task 5: CLI chat loop

**Files:**
- Create: `src/dreamer/cli.py`

**Interfaces:**
- Consumes: `Agent`, `Machine`, `Transcript`, `transcript_path`, `validate_name`, `generate_name`.
- Produces: `main() -> None` console entry point `dreamer`.

- [ ] **Step 1: Implement `src/dreamer/cli.py`**

```python
"""`dreamer`: chat with Agent A while Agent B dreams the machine."""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys
from pathlib import Path

from claude_agent_sdk import AssistantMessage, ClaudeSDKError, TextBlock, ToolUseBlock

from dreamer.agent import Agent
from dreamer.machine import Machine
from dreamer.transcript import Transcript, generate_name, transcript_path, validate_name

RUNS_DIR = Path("runs")
SCRATCH_DIR = Path(".dreamer-scratch")


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
    print(f"transcripts: {transcript_path(RUNS_DIR, agent_name, 'agent')}  "
          f"{transcript_path(RUNS_DIR, machine_name, 'machine')}")

    machine = Machine(
        Transcript(transcript_path(RUNS_DIR, machine_name, "machine")),
        machine_name, args.machine_model, SCRATCH_DIR,
    )
    agent = Agent(
        Transcript(transcript_path(RUNS_DIR, agent_name, "agent")),
        agent_name, args.model, machine.run,
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
```

- [ ] **Step 2: Lint and types**

Run: `uv run ruff check && uv run mypy --strict src`
Expected: pass. If mypy complains that `Agent`/`Machine` are not async context managers in `async with machine, agent`, the `__aenter__` return annotations from Tasks 2 and 4 are wrong; fix them there.

- [ ] **Step 3: Manual run**

Run: `uv run dreamer --agent demo --machine demo "Create hello.py that prints hello world, run it, and show me the output."`
Check:
- The terminal shows A's prose and `$ ...` lines only.
- `runs/demo.agent.jsonl` contains `session`, `user`, `tool_use`, `tool_result`, `text`, `result` events. If `tool_result` events are missing, the SDK does not echo tool results through `receive_response`; in that case move the `tool_result` write into the Bash handler in Task 4 (pass the transcript into `make_bash_tool`, write `content` and `is_error` there, with `tool_use_id=None`) and update `test_bash_tool_returns_machine_output` to check the written event.
- `runs/demo.machine.jsonl` contains only `session`, `user`, `result`, `output` events.
- Ctrl-C mid-turn returns to the `>` prompt; Ctrl-D exits cleanly.
- Running the same command again resumes: both transcripts gain a second `session` with `resumed: true`, and A remembers `hello.py`.

- [ ] **Step 4: Commit**

```bash
git add src/dreamer/cli.py
git commit -m "Add dreamer chat loop"
```

---

### Task 6: Viewer server

**Files:**
- Create: `src/dreamer/viewer.py`, `tests/test_viewer.py`

**Interfaces:**
- Consumes: nothing from other tasks except the transcript file naming.
- Produces: `make_server(runs_dir: Path) -> ThreadingHTTPServer` bound to a free localhost port; `main() -> None` console entry point `dreamer-view`. Routes: `GET /` (page), `GET /runs` (JSON list), `GET /events?file=<name>&from=<line>` (SSE).

- [ ] **Step 1: Write the failing tests**

`tests/test_viewer.py`:

```python
import http.client
import json
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from dreamer.viewer import make_server


@pytest.fixture
def server(tmp_path: Path) -> Iterator[tuple[str, int, Path]]:
    runs = tmp_path / "runs"
    runs.mkdir()
    srv = make_server(runs)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv.server_address[0], srv.server_address[1], runs
    srv.shutdown()


def _get(host: str, port: int, path: str) -> tuple[int, bytes]:
    c = http.client.HTTPConnection(host, port, timeout=5)
    c.request("GET", path)
    r = c.getresponse()
    return r.status, r.read()


def test_index_serves_html(server: tuple[str, int, Path]) -> None:
    host, port, _ = server
    status, body = _get(host, port, "/")
    assert status == 200 and b"<html" in body.lower()


def test_runs_lists_transcripts(server: tuple[str, int, Path]) -> None:
    host, port, runs = server
    (runs / "fox.agent.jsonl").write_text('{"type":"session"}\n')
    (runs / "fox.machine.jsonl").write_text("")
    status, body = _get(host, port, "/runs")
    items = json.loads(body)
    assert status == 200
    assert {i["file"] for i in items} == {"fox.agent.jsonl", "fox.machine.jsonl"}
    assert {i["kind"] for i in items} == {"agent", "machine"}
    assert all(i["name"] == "fox" for i in items)


def test_events_unknown_file_is_404(server: tuple[str, int, Path]) -> None:
    host, port, _ = server
    status, _ = _get(host, port, "/events?file=nope.agent.jsonl")
    assert status == 404


def test_events_rejects_path_escape(server: tuple[str, int, Path]) -> None:
    host, port, _ = server
    status, _ = _get(host, port, "/events?file=../pyproject.toml")
    assert status == 404


def test_events_replays_then_tails(server: tuple[str, int, Path]) -> None:
    host, port, runs = server
    f = runs / "fox.agent.jsonl"
    f.write_text('{"type":"session"}\n{"type":"user","text":"hi"}\n')
    c = http.client.HTTPConnection(host, port, timeout=5)
    c.request("GET", "/events?file=fox.agent.jsonl&from=1")
    r = c.getresponse()
    assert r.status == 200
    assert r.getheader("Content-Type", "").startswith("text/event-stream")

    def read_event() -> dict[str, object]:
        lines: list[bytes] = []
        while True:
            line = r.fp.readline()
            if line in (b"\n", b""):
                break
            lines.append(line)
        data = b"".join(line[len(b"data: "):] for line in lines if line.startswith(b"data: "))
        return json.loads(data)  # type: ignore[no-any-return]

    first = read_event()
    assert first == {"line": 1, "event": {"type": "user", "text": "hi"}}

    with f.open("a") as fh:
        fh.write('{"type":"text","te')  # partial line, must be skipped for now
        fh.flush()
        fh.write('xt":"ok"}\n')
    second = read_event()
    assert second == {"line": 2, "event": {"type": "text", "text": "ok"}}
    c.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_viewer.py -q`
Expected: ImportError on `dreamer.viewer`.

- [ ] **Step 3: Implement `src/dreamer/viewer.py`**

```python
"""`dreamer-view`: a local page that tails a transcript as it grows."""

from __future__ import annotations

import json
import re
import sys
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from urllib.parse import parse_qs, urlparse

_FILE_RE = re.compile(r"^([a-z0-9-]+)\.(agent|machine)\.jsonl$")
_POLL_SECONDS = 0.3


def _list_runs(runs_dir: Path) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for p in sorted(runs_dir.glob("*.jsonl")):
        m = _FILE_RE.match(p.name)
        if not m:
            continue
        st = p.stat()
        items.append({"file": p.name, "name": m[1], "kind": m[2], "size": st.st_size, "mtime": st.st_mtime})
    return items


def _complete_lines(path: Path, offset: int) -> tuple[list[str], int]:
    """Lines fully terminated by newline after byte `offset`, and the new offset."""
    with path.open("rb") as f:
        f.seek(offset)
        chunk = f.read()
    end = chunk.rfind(b"\n")
    if end < 0:
        return [], offset
    complete = chunk[: end + 1]
    return complete.decode("utf-8", errors="replace").splitlines(), offset + len(complete)


class _Handler(BaseHTTPRequestHandler):
    runs_dir: Path

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_GET(self) -> None:
        url = urlparse(self.path)
        if url.path == "/":
            self._send(200, "text/html; charset=utf-8", files("dreamer").joinpath("viewer.html").read_bytes())
        elif url.path == "/runs":
            self._send(200, "application/json", json.dumps(_list_runs(self.runs_dir)).encode())
        elif url.path == "/events":
            self._events(parse_qs(url.query))
        else:
            self._send(404, "text/plain", b"not found")

    def _send(self, status: int, ctype: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _events(self, q: dict[str, list[str]]) -> None:
        name = q.get("file", [""])[0]
        path = self.runs_dir / name
        if not _FILE_RE.match(name) or not path.is_file():
            self._send(404, "text/plain", b"unknown transcript")
            return
        start = int(q.get("from", ["0"])[0])
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        line_no = 0
        offset = 0
        try:
            while True:
                lines, offset = _complete_lines(path, offset)
                for line in lines:
                    if line_no >= start and line.strip():
                        try:
                            event = json.loads(line)
                        except json.JSONDecodeError:
                            event = {"type": "error", "message": "unparseable line", "raw": line}
                        payload = json.dumps({"line": line_no, "event": event}, ensure_ascii=False)
                        self.wfile.write(f"data: {payload}\n\n".encode())
                    line_no += 1
                self.wfile.flush()
                time.sleep(_POLL_SECONDS)
        except (BrokenPipeError, ConnectionResetError):
            return


def make_server(runs_dir: Path) -> ThreadingHTTPServer:
    handler = type("Handler", (_Handler,), {"runs_dir": runs_dir})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    return server


def main() -> None:
    runs_dir = Path("runs")
    url_path = "/"
    if len(sys.argv) > 1:
        target = Path(sys.argv[1])
        runs_dir = target.parent if target.parent != Path() else runs_dir
        url_path = f"/?file={target.name}"
    server = make_server(runs_dir)
    host, port = server.server_address[0], server.server_address[1]
    url = f"http://{host}:{port}{url_path}"
    print(f"dreamer-view: {url}  (runs in {runs_dir})")
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
```

Note on `_complete_lines`: the test writes a partial line and expects it to be skipped; `rfind(b"\n")` guarantees only newline-terminated lines are emitted and the offset never advances past a partial line.

- [ ] **Step 4: Create a minimal `src/dreamer/viewer.html` so `/` serves**

```html
<!doctype html><html><head><meta charset="utf-8"><title>dreamer</title></head><body>viewer</body></html>
```

- [ ] **Step 5: Run tests, lint, and types**

Run: `uv run pytest -q && uv run ruff check && uv run mypy --strict src`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/dreamer/viewer.py src/dreamer/viewer.html tests/test_viewer.py
git commit -m "Add transcript viewer server with SSE tail"
```

---

### Task 7: Viewer page

**Files:**
- Modify: `src/dreamer/viewer.html` (replace the placeholder)

**Interfaces:**
- Consumes: `/runs` JSON (`file`, `name`, `kind`, `size`, `mtime`) and `/events` SSE payloads `{"line": n, "event": {...}}` from Task 6; event types from the spec's transcript table.

- [ ] **Step 1: Write `src/dreamer/viewer.html`**

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>dreamer</title>
<style>
  :root {
    --bg: #f6f7f9; --fg: #1c1e21; --muted: #6b7280; --card: #ffffff; --line: #e5e7eb;
    --accent: #2563eb; --err: #dc2626; --err-bg: #fef2f2; --ok: #16a34a; --mark: #fde68a;
    --mono: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  }
  @media (prefers-color-scheme: dark) {
    :root { --bg: #0f1115; --fg: #e5e7eb; --muted: #9ca3af; --card: #171a21; --line: #2a2f3a;
            --accent: #60a5fa; --err: #f87171; --err-bg: #2a1515; --ok: #4ade80; --mark: #92400e; }
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--fg); font: 14px/1.45 system-ui, sans-serif; }
  header { position: sticky; top: 0; background: var(--bg); border-bottom: 1px solid var(--line);
           padding: 10px 16px; display: flex; flex-wrap: wrap; gap: 10px; align-items: center; z-index: 2; }
  header h1 { font-size: 15px; margin: 0 8px 0 0; }
  header input[type=search] { flex: 1 1 220px; padding: 6px 10px; border: 1px solid var(--line);
                              border-radius: 6px; background: var(--card); color: var(--fg); }
  .chips { display: flex; gap: 6px; flex-wrap: wrap; }
  .chip { border: 1px solid var(--line); border-radius: 999px; padding: 2px 10px; cursor: pointer;
          background: var(--card); color: var(--muted); user-select: none; }
  .chip.on { color: var(--fg); border-color: var(--accent); }
  .stats { display: flex; gap: 14px; color: var(--muted); font-variant-numeric: tabular-nums; }
  main { max-width: 1000px; margin: 0 auto; padding: 16px; }
  .card { background: var(--card); border: 1px solid var(--line); border-radius: 8px;
          margin: 0 0 8px; padding: 8px 12px; cursor: pointer; }
  .card.err { background: var(--err-bg); border-color: var(--err); }
  .card .head { display: flex; gap: 10px; align-items: baseline; }
  .card .ts { color: var(--muted); font-family: var(--mono); font-size: 12px; white-space: nowrap; }
  .badge { font-size: 11px; text-transform: uppercase; letter-spacing: .04em; padding: 1px 6px;
           border-radius: 4px; border: 1px solid var(--line); color: var(--muted); white-space: nowrap; }
  .badge.user { color: var(--accent); border-color: var(--accent); }
  .badge.tool_use, .badge.output { color: var(--fg); }
  .exit { font-family: var(--mono); font-size: 11px; padding: 1px 6px; border-radius: 4px; color: #fff;
          background: var(--ok); }
  .exit.bad { background: var(--err); }
  .preview { flex: 1; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
  .preview.mono, .full pre { font-family: var(--mono); }
  .full { display: none; margin-top: 8px; border-top: 1px solid var(--line); padding-top: 8px; }
  .card.open .full { display: block; }
  .full pre { margin: 0; white-space: pre-wrap; word-break: break-word; max-height: 60vh; overflow: auto; }
  .full h4 { margin: 8px 0 4px; font-size: 12px; color: var(--muted); }
  .divider { text-align: center; color: var(--err); border-top: 2px dashed var(--err); margin: 16px 0; padding-top: 4px; }
  mark { background: var(--mark); color: inherit; }
  #jump { position: fixed; right: 16px; bottom: 16px; display: none; padding: 8px 14px; border-radius: 999px;
          border: 0; background: var(--accent); color: #fff; cursor: pointer; }
  #jump.show { display: block; }
  #runs a { display: block; padding: 10px 12px; border: 1px solid var(--line); border-radius: 8px;
            margin-bottom: 8px; color: var(--fg); text-decoration: none; background: var(--card); }
  #runs .meta { color: var(--muted); font-size: 12px; }
  .hidden { display: none !important; }
</style>
</head>
<body>
<header>
  <h1 id="title">dreamer</h1>
  <input id="search" type="search" placeholder="search…" class="hidden">
  <div id="chips" class="chips"></div>
  <div id="stats" class="stats"></div>
</header>
<main>
  <div id="runs"></div>
  <div id="timeline"></div>
</main>
<button id="jump">jump to latest</button>
<script>
(() => {
  const $ = (s) => document.querySelector(s);
  const file = new URLSearchParams(location.search).get("file");
  if (!file) return showRuns();
  showTranscript(file);

  async function showRuns() {
    const runs = await (await fetch("/runs")).json();
    const el = $("#runs");
    if (!runs.length) { el.textContent = "No transcripts in runs/ yet."; return; }
    for (const r of runs.sort((a, b) => b.mtime - a.mtime)) {
      const a = document.createElement("a");
      a.href = `/?file=${encodeURIComponent(r.file)}`;
      a.innerHTML = `<strong>${esc(r.name)}</strong> <span class="badge">${r.kind}</span>
        <div class="meta">${fmtSize(r.size)} · ${new Date(r.mtime * 1000).toLocaleString()}</div>`;
      el.appendChild(a);
    }
  }

  function showTranscript(file) {
    const kind = file.endsWith(".machine.jsonl") ? "machine" : "agent";
    document.title = `dreamer · ${file}`;
    $("#title").textContent = file;
    $("#search").classList.remove("hidden");
    const types = kind === "machine"
      ? ["session", "user", "output", "result", "compact", "error"]
      : ["session", "user", "text", "thinking", "tool_use", "tool_result", "result", "compact", "error"];
    const on = new Set(types.filter((t) => !["session", "result"].includes(t)));
    const chips = $("#chips");
    for (const t of types) {
      const c = document.createElement("span");
      c.className = "chip" + (on.has(t) ? " on" : ""); c.textContent = t; c.dataset.type = t;
      c.onclick = () => { on.has(t) ? on.delete(t) : on.add(t); c.classList.toggle("on"); applyFilters(); };
      chips.appendChild(c);
    }

    const timeline = $("#timeline");
    const stats = { events: 0, tools: 0, fails: 0, turns: 0, cost: 0, compacts: 0 };
    const cards = [];
    let follow = true;
    let query = "";

    $("#search").oninput = (e) => { query = e.target.value.toLowerCase(); applyFilters(); };
    window.addEventListener("scroll", () => {
      const atBottom = window.innerHeight + window.scrollY >= document.body.offsetHeight - 40;
      follow = atBottom; $("#jump").classList.toggle("show", !atBottom);
    });
    $("#jump").onclick = () => { follow = true; window.scrollTo(0, document.body.scrollHeight); };

    const es = new EventSource(`/events?file=${encodeURIComponent(file)}&from=0`);
    es.onmessage = (m) => {
      const { event } = JSON.parse(m.data);
      addCard(event); updateStats(event);
      if (follow) window.scrollTo(0, document.body.scrollHeight);
    };

    function addCard(e) {
      const card = document.createElement("div");
      if (e.type === "compact") {
        card.className = "divider"; card.textContent = `context compacted (${e.trigger ?? "?"}) · ${fmtTs(e.ts)}`;
        card.dataset.type = "compact"; card.dataset.text = "compact";
        timeline.appendChild(card); cards.push(card); return;
      }
      card.className = "card"; card.dataset.type = e.type;
      const { preview, mono, sections, error, exit } = describe(e);
      if (error) card.classList.add("err");
      card.dataset.text = (preview + " " + sections.map((s) => s.body).join(" ")).toLowerCase();
      const head = document.createElement("div"); head.className = "head";
      head.innerHTML = `<span class="ts">${fmtTs(e.ts)}</span><span class="badge ${e.type}">${e.type}</span>`;
      if (exit !== undefined) {
        const x = document.createElement("span"); x.className = "exit" + (exit ? " bad" : ""); x.textContent = `exit ${exit}`;
        head.appendChild(x);
      }
      const p = document.createElement("span"); p.className = "preview" + (mono ? " mono" : "");
      p.textContent = preview; p.dataset.raw = preview; head.appendChild(p);
      card.appendChild(head);
      const full = document.createElement("div"); full.className = "full";
      for (const s of sections) {
        if (s.title) { const h = document.createElement("h4"); h.textContent = s.title; full.appendChild(h); }
        const pre = document.createElement("pre"); pre.textContent = s.body; pre.dataset.raw = s.body; full.appendChild(pre);
      }
      card.appendChild(full);
      card.onclick = () => card.classList.toggle("open");
      timeline.appendChild(card); cards.push(card);
      applyFilterTo(card);
    }

    function describe(e) {
      const j = (v) => JSON.stringify(v, null, 2);
      switch (e.type) {
        case "session": return { preview: `${e.resumed ? "resumed" : "started"} ${e.name} · ${e.model ?? "default model"} · ${e.session_id}`, mono: false, sections: [{ body: j(e) }] };
        case "user": return kind === "machine"
          ? { preview: `$ ${e.text}`, mono: true, sections: [{ body: e.text }] }
          : { preview: e.text, mono: false, sections: [{ body: e.text }] };
        case "text": case "thinking": return { preview: e.text, mono: false, sections: [{ body: e.text }] };
        case "tool_use": return { preview: `$ ${e.input?.command ?? ""}`, mono: true,
          sections: [{ title: "command", body: e.input?.command ?? "" }, { title: "input", body: j(e.input) }] };
        case "tool_result": { const body = flat(e.content); return { preview: firstLine(body), mono: true, error: !!e.is_error, sections: [{ body }] }; }
        case "output": return { preview: firstLine(e.stdout) || firstLine(e.stderr) || "(no output)", mono: true, exit: e.exit_code,
          error: e.exit_code !== 0, sections: [{ title: "stdout", body: e.stdout }, ...(e.stderr ? [{ title: "stderr", body: e.stderr }] : [])] };
        case "result": return { preview: `${e.subtype} · ${e.num_turns} turns · ${(e.duration_ms / 1000).toFixed(1)}s` + (e.cost_usd != null ? ` · $${e.cost_usd.toFixed(4)}` : ""), mono: false, sections: [{ body: j(e) }] };
        case "error": return { preview: e.message, mono: false, error: true, sections: [{ body: j(e) }] };
        default: return { preview: j(e), mono: true, sections: [{ body: j(e) }] };
      }
    }

    function updateStats(e) {
      stats.events++;
      if (e.type === "tool_use") stats.tools++;
      if ((e.type === "tool_result" && e.is_error) || (e.type === "output" && e.exit_code !== 0)) stats.fails++;
      if (e.type === "result") { stats.turns++; stats.cost += e.cost_usd ?? 0; }
      if (e.type === "compact") stats.compacts++;
      $("#stats").innerHTML = [["events", stats.events], [kind === "machine" ? "commands" : "tool calls", kind === "machine" ? stats.turns : stats.tools],
        ["failures", stats.fails], ["turns", stats.turns], ["cost", "$" + stats.cost.toFixed(4)], ["compactions", stats.compacts]]
        .map(([k, v]) => `<span>${k}: <b>${v}</b></span>`).join("");
    }

    function applyFilters() { cards.forEach(applyFilterTo); }
    function applyFilterTo(card) {
      const typeOk = on.has(card.dataset.type);
      const textOk = !query || card.dataset.text.includes(query);
      card.classList.toggle("hidden", !(typeOk && textOk));
      for (const el of card.querySelectorAll("[data-raw]")) el.innerHTML = highlight(el.dataset.raw, query);
    }
    function highlight(text, q) {
      const safe = esc(text);
      if (!q) return safe;
      const re = new RegExp(q.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "gi");
      return safe.replace(re, (m) => `<mark>${m}</mark>`);
    }
  }

  function flat(c) { return typeof c === "string" ? c : Array.isArray(c) ? c.map((b) => b.text ?? JSON.stringify(b)).join("\n") : c == null ? "" : JSON.stringify(c, null, 2); }
  function firstLine(s) { return (s ?? "").split("\n").find((l) => l.trim()) ?? ""; }
  function fmtTs(ts) { return ts ? ts.slice(11, 23) : ""; }
  function fmtSize(n) { return n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`; }
  function esc(s) { return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }
})();
</script>
</body>
</html>
```

- [ ] **Step 2: Manual check against the transcripts from Task 5**

Run: `uv run dreamer-view runs/demo.agent.jsonl` and, in another terminal, `uv run dreamer-view runs/demo.machine.jsonl`.
Check:
- Both pages open; cards appear in order; clicking expands.
- Start `uv run dreamer --agent demo --machine demo` in a third terminal and send a prompt: new cards appear in both tabs within a second.
- Search for a word in a command: only matching cards stay, with the word highlighted.
- Toggle the `tool_use` chip off: command cards disappear. Toggle `result` on: turn summaries appear.
- Scroll up: auto-scroll stops and "jump to latest" appears; click it to resume.
- Stats strip shows non-zero counts for events, tool calls, turns.
- `uv run dreamer-view` with no argument lists both files and links to them.
- Both color schemes read fine (toggle the OS theme).

- [ ] **Step 3: Run tests (the index test still passes), then commit**

Run: `uv run pytest -q`
Expected: pass.

```bash
git add src/dreamer/viewer.html
git commit -m "Add transcript viewer page"
```

---

### Task 8: License, README, and release checks

**Files:**
- Create: `LICENSE`
- Modify: `README.md`, `.gitignore` (add `.dreamer-scratch/`)

- [ ] **Step 1: Write `LICENSE`**

```
MIT License

Copyright (c) 2026 Anty0

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

Replace the copyright holder with the name the user wants on GitHub if it differs.

- [ ] **Step 2: Write `README.md`**

```markdown
# dreamer-agent

A coding agent whose only tool is `Bash`. The tool runs nothing. Every command is
answered by a second agent that has been told one thing: *you are a Linux-based
operating system*. It invents the filesystem, the packages, the error messages, and
has to stay consistent with everything it already said.

The question this project exists to ask: how far can an agent build and "run" a
project on a machine that is itself a language model, and where does it break?

## How it works

```
you ──> agent A (engineer, one tool: Bash) ──> agent B (the machine) ──> JSON {stdout, stderr, exit_code}
```

- Agent A is a `ClaudeSDKClient` with built-in tools removed and one in-process MCP
  tool whose schema mirrors Claude Code's Bash tool.
- Agent B is a `ClaudeSDKClient` with no tools, a short system prompt, and a JSON
  output schema enforced by the SDK. It never sees A's reasoning, only commands.
- Both sides append every event to `runs/<name>.agent.jsonl` and
  `runs/<name>.machine.jsonl`. A local viewer tails those files live.

Both agents run through the Claude Agent SDK, so a Claude subscription login is
enough. No API key needed.

## Install

```bash
uv tool install git+https://github.com/Anty0/dreamer-agent
# or run without installing
uvx --from git+https://github.com/Anty0/dreamer-agent dreamer
```

Requires Python 3.12+ and a working `claude` login (`claude` CLI installed and signed in).

## Use

```bash
dreamer                                   # new agent, new machine, chat
dreamer "Build a Flask todo app and run its tests"
dreamer --agent fox --machine box         # resume both by name next time
dreamer --agent new-dev --machine box     # fresh engineer, same old machine
dreamer --model claude-opus-5-5 --machine-model claude-sonnet-5-5
```

The terminal shows only A's prose and each command it runs. Open the viewers for
everything else:

```bash
dreamer-view                              # pick a transcript
dreamer-view runs/fox.agent.jsonl         # A's side
dreamer-view runs/box.machine.jsonl       # B's side
```

Each viewer tails its file, searches, filters by event type, and expands on click.

## A taste

```
$ cat /etc/os-release
PRETTY_NAME="Ubuntu 22.04.3 LTS"
```

(Replace with an excerpt from a real run.)

## What tends to break

Filled in as experiments happen. Things to watch:

- Context compaction on the machine side (shown as a red divider in the viewer).
- Long file contents round-tripping through `cat`.
- Anything that depends on real time, randomness, or the network.

## Development

```bash
uv sync
uv run pytest
uv run ruff check && uv run mypy --strict src
uv run python scripts/smoke_machine.py    # talks to the real model
```

## License

MIT.
```

- [ ] **Step 3: Add scratch dir to `.gitignore` and run the full check**

```bash
printf '.dreamer-scratch/\n' >> .gitignore
uv run pytest -q && uv run ruff check && uv run mypy --strict src && uv build
```

Expected: tests pass, no lint or type errors, `dist/` contains a wheel. Confirm `unzip -l dist/*.whl | grep -E 'viewer.html|prompts/'` lists both prompt files and the page.

- [ ] **Step 4: Commit**

```bash
git add LICENSE README.md .gitignore
git commit -m "Add license and README"
```
