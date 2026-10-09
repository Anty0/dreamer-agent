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
uv tool install git+https://github.com/<you>/dreamer-agent
# or run without installing
uvx --from git+https://github.com/<you>/dreamer-agent dreamer
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

Type `/quit` or press Ctrl-D to leave. Ctrl-C asks the agent to stop; a command already
in flight finishes first.

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
$ uname -a
Linux devbox 6.8.0-45-generic #45-Ubuntu SMP PREEMPT_DYNAMIC Fri Aug 30 12:02:04 UTC 2024 x86_64 x86_64 x86_64 GNU/Linux
$ whoami
dev
```

None of that exists. It is the machine's answer to the agent's first commands.

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
