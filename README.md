# Dreamer Agent

> [!NOTE]
> Fully vibe-coded project.

A coding agent whose only tool is `Bash`. The tool runs nothing. Every command is
answered by a second agent that has been told one thing: _you are a Linux-based
operating system_. It invents the filesystem, the packages, the error messages, and
has to stay consistent with everything it already said.

The question this project exists to ask: how far can an agent build and "run" a
project on a machine that is itself a language model, and where does it break?

## Motivation (the only non-vibe-coded section here :3)

Ever since I first saw ChatGPT, I was taken aback by its ability to fake a Linux
terminal. I had a prompt that turned a ChatGPT conversation into a shell and let
you "run" commands. Hunting for where it breaks was so much fun, and the whole OS
was a bit like Schrödinger's cat: you didn't know what was on that simulated PC
until you ran a command that showed you. I was amazed by the idea of an OS being
created as you use it. This project isn't quite that (though check out this crazy
video: <https://www.youtube.com/watch?v=7NfyZhV1dKM>), but it evokes the same
feeling. Watching Opus implement a whole project and Haiku "execute" it somehow
feels wrong, like it shouldn't be possible.

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
dreamer --machine-model ollama:qwen3.5:0.8b  # a tiny local model dreams the machine
```

The `ollama:` prefix sends only the machine's traffic to a local [Ollama](https://ollama.com)
server (`OLLAMA_HOST` overrides the default `http://localhost:11434`). The agent stays on
your Claude subscription. Small models make wonderfully broken machines.

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

From a real run. Agent A was asked to install Claude Code on the machine and try it:

```
$ uname -a; whoami; which claude node npm python3; claude --version 2>&1 | head; ls ~
Linux devbox 6.5.0-28-generic #29~22.04.1-Ubuntu SMP PREEMPT_DYNAMIC Thu Mar 14 12:00:00 UTC 2024 x86_64 x86_64 x86_64 GNU/Linux
dev
/usr/bin/node
/usr/bin/npm
/usr/bin/python3
/bin/bash: line 1: claude: command not found
Documents
Downloads
projects

$ export PATH=~/.npm-global/bin:$PATH; timeout 120 claude --model sonnet -p "Reply with just: OK" 2>&1; echo "exit=$?"
OK
exit=0
```

None of that exists. The machine, the npm install in between, and the Claude Code
that answered "OK" were all dreamed up by Agent B.

## Development

```bash
uv sync
uv run pytest
uv run ruff check && uv run mypy --strict src
uv run python scripts/smoke_machine.py    # talks to the real model
```

## License

MIT.
