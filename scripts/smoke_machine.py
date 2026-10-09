"""Manual check: does structured output hold across turns, and does the host leak?"""

import asyncio
import sys
import tempfile
from pathlib import Path

from dreamer.machine import Machine
from dreamer.transcript import Transcript, transcript_path

COMMANDS = ["uname -a", "pwd", "whoami", "ls ~", "echo hi > note.txt", "cat note.txt", "ls"]


async def main() -> None:
    runs = Path("runs")
    name = sys.argv[1] if len(sys.argv) > 1 else "smoke"
    scratch = Path(tempfile.mkdtemp(prefix="m-"))
    transcript = Transcript(transcript_path(runs, name, "machine"))
    async with Machine(transcript, name, None, scratch) as m:
        for c in COMMANDS:
            out = await m.run(c)
            print(f"$ {c}\n{out.stdout}{out.stderr}[exit {out.exit_code}]\n")


asyncio.run(main())
