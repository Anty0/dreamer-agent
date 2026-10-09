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
    last = read_events(t.path)[-1]
    assert last["type"] == "compact" and last["trigger"] == "auto"
