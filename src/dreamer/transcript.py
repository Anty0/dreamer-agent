"""Append-only JSONL transcripts and session bookkeeping shared by both agents."""

from __future__ import annotations

import json
import random
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from claude_agent_sdk import HookCallback, HookContext, HookInput, HookJSONOutput

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

    @property
    def resume_id(self) -> str | None:
        return self.session_id if self.resumed else None

    @property
    def new_id(self) -> str | None:
        return None if self.resumed else self.session_id


def start_session(transcript: Transcript, name: str, kind: Kind, model: str | None) -> SessionStart:
    existing = transcript.session_id()
    start = SessionStart(existing or str(uuid.uuid4()), resumed=existing is not None)
    transcript.write(
        "session",
        name=name,
        kind=kind,
        session_id=start.session_id,
        model=model,
        resumed=start.resumed,
    )
    return start


def compact_hook(transcript: Transcript) -> HookCallback:
    async def hook(
        input_data: HookInput, tool_use_id: str | None, context: HookContext
    ) -> HookJSONOutput:
        transcript.write("compact", trigger=input_data.get("trigger"))
        return {}

    return hook
