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


class _FakeClient:
    def __init__(self, messages: list[object]) -> None:
        self.messages = messages
        self.queries: list[str] = []
        self.interrupts = 0

    async def connect(self) -> None:
        pass

    async def disconnect(self) -> None:
        pass

    async def query(self, prompt: str) -> None:
        self.queries.append(prompt)

    async def interrupt(self) -> None:
        self.interrupts += 1

    async def receive_response(self):  # type: ignore[no-untyped-def]
        for m in self.messages:
            yield m


def _result(**kw: object) -> object:
    from claude_agent_sdk import ResultMessage

    base: dict[str, object] = {
        "subtype": "success",
        "duration_ms": 1,
        "duration_api_ms": 1,
        "is_error": False,
        "num_turns": 1,
        "session_id": "s",
        "structured_output": {"stdout": "a", "stderr": "", "exit_code": 0},
    }
    base.update(kw)
    return ResultMessage(**base)  # type: ignore[arg-type]


def _machine(tmp_path: object, client: object) -> object:
    from pathlib import Path

    from dreamer.machine import Machine
    from dreamer.transcript import Transcript

    root = Path(str(tmp_path))
    transcript = Transcript(root / "m.machine.jsonl")
    return Machine(transcript, "m", None, root / "s", client=client)  # type: ignore[arg-type]


async def test_run_logs_error_event_on_sdk_failure(tmp_path: object) -> None:
    from pathlib import Path

    from dreamer.transcript import read_events

    class Boom(_FakeClient):
        async def query(self, prompt: str) -> None:
            raise RuntimeError("process died")

    m = _machine(tmp_path, Boom([]))
    with pytest.raises(RuntimeError):
        await m.run("ls")  # type: ignore[attr-defined]
    events = read_events(Path(str(tmp_path)) / "m.machine.jsonl")
    assert events[-1]["type"] == "error" and "process died" in events[-1]["message"]


async def test_run_after_cancel_drains_stale_turn(tmp_path: object) -> None:
    import asyncio

    stale = _result(structured_output={"stdout": "stale", "stderr": "", "exit_code": 0})
    fresh = _result(structured_output={"stdout": "fresh", "stderr": "", "exit_code": 0})

    class Cancelling(_FakeClient):
        first = True

        async def receive_response(self):  # type: ignore[no-untyped-def]
            if self.first:
                self.first = False
                raise asyncio.CancelledError
            for m in self.messages:
                yield m

    client = Cancelling([stale, fresh])
    m = _machine(tmp_path, client)
    with pytest.raises(asyncio.CancelledError):
        await m.run("sleep 100")  # type: ignore[attr-defined]
    client.messages = [stale]
    drained_then_fresh = {"n": 0}

    async def receive() -> object:
        drained_then_fresh["n"] += 1
        return [stale] if drained_then_fresh["n"] == 1 else [fresh]

    async def receive_response():  # type: ignore[no-untyped-def]
        for x in await receive():
            yield x

    client.receive_response = receive_response  # type: ignore[method-assign]
    out = await m.run("ls")  # type: ignore[attr-defined]
    assert client.interrupts == 1
    assert out.stdout == "fresh"


async def test_run_writes_user_result_output_events(tmp_path: object) -> None:
    from pathlib import Path

    from dreamer.transcript import read_events

    m = _machine(tmp_path, _FakeClient([_result()]))
    out = await m.run("ls")  # type: ignore[attr-defined]
    assert out.stdout == "a"
    types = [e["type"] for e in read_events(Path(str(tmp_path)) / "m.machine.jsonl")]
    assert types == ["session", "user", "result", "output"]


def test_quiet_mod_ships_with_package() -> None:
    from pathlib import Path

    from dreamer.machine import QUIET_ENV, QUIET_PLUGINS

    root = Path(QUIET_PLUGINS[0]["path"])
    assert (root / ".claude-plugin" / "plugin.json").is_file()
    assert (root / "hooks" / "hooks.json").is_file()
    assert (root / "hooks" / "register.mjs").is_file()
    assert QUIET_ENV["CLAUDE_CODE_ENABLE_FUNCTION_HOOKS"] == "1"


def test_machine_backend_routes_ollama_prefix_to_local_server() -> None:
    from dreamer.machine import machine_backend

    assert machine_backend("ollama:qwen3.5:0.8b") == (
        "qwen3.5:0.8b",
        {"ANTHROPIC_BASE_URL": "http://localhost:11434", "ANTHROPIC_AUTH_TOKEN": "ollama"},
    )
    assert machine_backend("claude-haiku-5-5") == ("claude-haiku-5-5", {})
    assert machine_backend(None) == (None, {})
