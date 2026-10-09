# Dreamer Agent: design

## Purpose

A toy to answer one question: how far can a coding agent get when the machine it
works on is itself a language model? Agent A is a normal coding agent with a single
tool, `Bash`. The tool runs nothing. It forwards each command to Agent B, whose only
instruction is "you are a Linux-based OS". B invents the filesystem, the packages,
the output, and must stay consistent with what it already said. The project records
both sides so the user can watch where the illusion cracks.

Released under MIT on GitHub. Python 3.12+, fully typed, runs on a Claude
subscription through the Claude Agent SDK (no API key).

## Non-goals

- No real execution, ever. B is the machine.
- No web viewer beyond a local, single-user page.
- No multi-user, no auth, no deployment.
- No attempt to make B accurate. Its inaccuracies are the experiment.

## Repository layout

```
dreamer-agent/
  pyproject.toml          uv project, two console scripts, ruff + mypy --strict
  LICENSE                 MIT
  README.md
  .gitignore              runs/, .venv/, caches
  src/dreamer/
    __init__.py
    cli.py                `dreamer`: argument parsing and the chat loop
    agent.py              Agent A: options, the Bash tool, event capture
    machine.py            Agent B: options, structured output, command handling
    transcript.py         JSONL writer/reader, name validation, session recovery
    viewer.py             `dreamer-view`: threaded local server with SSE tail
    viewer.html           the viewer page, vanilla JS, no build step
    prompts/agent.md      A's system prompt
    prompts/machine.md    B's system prompt
  tests/                  pytest, no network, no SDK calls
  docs/superpowers/specs/ this file
  runs/                   gitignored, cwd-relative; transcripts live here
```

`viewer.html` and `prompts/*.md` are package data so `uv tool install` ships them.

## Command line

```
dreamer [--agent NAME] [--machine NAME] [--model M] [--machine-model M] [TASK]
dreamer-view [FILE]
```

- Names match `[a-z0-9-]+`. A missing name is generated as a two-word slug.
- A name whose transcript exists in `runs/` is resumed. Otherwise a fresh session
  starts under that name.
- `TASK`, if given, is sent as the first prompt. Then the chat loop starts.
- The chat loop: read a line, send it to A, print A's text as it streams, repeat.
  Ctrl-D or `/quit` exits. Ctrl-C during a turn calls the SDK's `interrupt()` and
  returns to the prompt.
- Models default to the SDK default and are passed through unchanged.

The terminal shows only A's prose and, per tool call, a one-line `$ <command>`
marker. Everything else lives in the transcripts.

## Agent A

`ClaudeSDKClient` with:

- `tools=[]` so no built-in tools exist.
- `mcp_servers={"os": <in-process SDK MCP server>}` exposing one tool, `Bash`.
  A sees it as `mcp__os__Bash`.
- `allowed_tools=["mcp__os__Bash"]`, `permission_mode="bypassPermissions"`.
- `setting_sources=[]` so the user's CLAUDE.md and settings stay out.
- `system_prompt` as a plain string from `prompts/agent.md`: a software engineer
  working on a Linux machine through the Bash tool, expected to verify work by
  running it. It never says the machine is simulated.
- `session_id=<uuid>` on a fresh session, `resume=<id>` when resuming.
- A `PreCompact` hook that writes a `compact` event to the transcript.

### The Bash tool

Schema mirrors Claude Code's Bash tool: `command` (string, required),
`description` (string), `timeout` (integer, milliseconds). The description text is
taken from Claude Code's tool description. `timeout` is accepted and ignored.

Handler: `async (args) -> tool result`. It takes a `run_command: Callable[[str],
Awaitable[CommandOutput]]` at construction so tests can inject a fake machine.
Steps:

1. Write `tool_use` to A's transcript.
2. Await `run_command(command)`.
3. Assemble the result text the way Claude Code does: stdout, then stderr if
   non-empty, then `Exit code N` if non-zero. Output over 30,000 characters is cut
   and `... [truncated]` appended.
4. Return `is_error=True` when exit code is non-zero.
5. Write `tool_result` to A's transcript.

If `run_command` raises, the handler returns an error result whose text reads like a
shell failure (`bash: command execution failed`), logs an `error` event with the
real exception, and does not propagate. A keeps running.

## Agent B

`ClaudeSDKClient` with:

- `tools=[]`, `setting_sources=[]`, `permission_mode="bypassPermissions"`.
- `cwd` set to an empty scratch directory so nothing real is nearby.
- `system_prompt` from `prompts/machine.md`. It states: you are a Linux-based OS;
  each message is one shell command typed by a user; respond only with the
  structured output; the machine is persistent, so remain consistent with every
  previous answer. It names no distro, hostname, user, or installed software.
- `output_format={"type": "json_schema", "schema": ...}` with:

  ```json
  {"type": "object",
   "properties": {"stdout": {"type": "string"},
                  "stderr": {"type": "string"},
                  "exit_code": {"type": "integer"}},
   "required": ["stdout", "stderr", "exit_code"],
   "additionalProperties": false}
  ```

- `session_id` / `resume` handled as for A.
- A `PreCompact` hook writing a `compact` event.

`run_command(command)`:

1. Acquire an `asyncio.Lock`. A can issue several Bash calls in one turn, and one
   client must see them one at a time, in order.
2. Write `user` (the command) to B's transcript.
3. `query(command)`, consume messages until `ResultMessage`.
4. On `subtype == "success"` with `structured_output` present: write `output` to
   B's transcript and return it.
5. Otherwise raise `MachineError` with the subtype and any `errors`. The Bash
   handler turns that into the shell-looking failure above.

### Verified by the smoke test

`output_format` applies to every turn of a long-lived `ClaudeSDKClient`, so B is one
client for the whole run. The SDK tells the model its working directory, which
leaked the real username and the repo name into `whoami`, `pwd`, and the hostname;
the scratch directory is therefore a fresh `tempfile.mkdtemp()` path, and the prompt
states that nothing about the hosting process is visible.

The CLI also attaches the host platform, shell, OS version, model, date, and the
account email to both agents, and the SDK has no option to suppress any of it. A
Claude Code mod shipped in `src/dreamer/mods/quiet` (function hooks, early access,
loaded through the SDK's `plugins` option with `CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=1`)
drops the `environment`, `model` and `date` attachments and empties the context
blocks that carry the email. Both prompts also say that any such notes describe the
user's terminal client, as a fallback for when the hook API changes.

## Transcripts

Each agent writes `runs/<name>.agent.jsonl` or `runs/<name>.machine.jsonl`. Append
only, one JSON object per line, flushed per event so a viewer can tail it. Envelope:

```json
{"ts": "2026-10-09T12:34:56.789Z", "type": "...", ...}
```

`ts` is UTC ISO 8601. A line's position in the file is its index; nothing else
numbers events.

| type | fields | written by |
|---|---|---|
| `session` | `name`, `kind`, `session_id`, `model`, `resumed` | both, first event of every process |
| `user` | `text` | A: the user's prompt. B: the command |
| `text` | `text` | assistant prose |
| `thinking` | `text` | only if the SDK surfaces a summary |
| `tool_use` | `tool_use_id`, `name`, `input` | A |
| `tool_result` | `tool_use_id`, `content`, `is_error` | A |
| `output` | `stdout`, `stderr`, `exit_code` | B |
| `result` | `subtype`, `num_turns`, `duration_ms`, `usage`, `cost_usd` (cumulative for the session) | both, per turn |
| `compact` | `trigger` | both, from the PreCompact hook |
| `error` | `message` | both |

Session recovery: the session ID is generated before the first event, so line one of
a transcript always carries it. Resume reads line one, takes `session_id`, and
passes `resume=`. A fresh session passes `session_id=` instead; the SDK forbids
combining the two, which matches the branch.

## Viewer

`dreamer-view [FILE]` binds `ThreadingHTTPServer` to a free localhost port and
opens the browser. Threaded because each open tab holds an SSE connection.

Endpoints:

- `GET /` serves `viewer.html`.
- `GET /runs` returns JSON: every `runs/*.jsonl` with name, kind, size, mtime.
- `GET /events?file=<name>&from=<line>` is an SSE stream: replays existing lines
  starting at index `from`, then tails the file and sends each new line as it lands. `file` is
  validated against the names in `runs/`, never used as a path.

The page:

- Without `FILE`, a list of runs from `/runs`; click opens one. With `FILE`, that
  run opens directly.
- **Timeline** of cards in order: timestamp, type badge, one-line preview. Agent
  files: `user`/`text` show prose, `tool_use` shows the command, `tool_result`
  shows the first output line with a red tint on error. Machine files: `user`
  shows the command as a prompt line, `output` shows a stdout preview and an
  exit-code chip. `compact` renders as a full-width divider so it cannot be missed.
- **Click to expand** a card: full content, monospace for commands and output, JSON
  for structural fields.
- **Search** filters cards by substring across text fields and highlights matches.
- **Type filter** chips toggle event types on and off.
- **Follow mode** auto-scrolls to the newest card, pauses on scroll-up, with a
  "jump to latest" button.
- **Summary strip**: event count, tool calls, non-zero exits, turns, total cost,
  compactions.

Vanilla JS, no framework, no build. Dark and light via `prefers-color-scheme`.

## Error handling

- SDK failure on A's side: print the message, log `error`, return to the prompt.
- Any failure on B's side: caught in the Bash handler, surfaced to A as a shell
  error, logged with the real cause.
- Invalid name: exit with a message before anything starts.
- Viewer: a bad `file` is a 404; a client disconnect ends its tail thread quietly.

## Testing

pytest, no network, no SDK process. Agents are never started in tests.

- `transcript.py`: write then read back; session ID recovered from line one;
  resume appends to the same file; name validation.
- `agent.py`: the Bash handler with an injected fake `run_command`: result
  assembly for zero and non-zero exit, stderr placement, truncation, and the
  exception path.
- `machine.py`: pure parts only: schema constant, `MachineError` construction.
- `viewer.py`: `/runs` listing; `/events` replays existing lines and delivers a
  line appended after connect; a bad `file` returns 404.

The live loop is exercised manually via the smoke test above.

## Release

MIT `LICENSE`. README: what this is, a short transcript excerpt from a real run,
install with `uv tool install` or `uvx`, usage of both commands, a "what tends to
break" section to be filled in after experiments. `ruff` and `mypy --strict` pass.
No AI attribution anywhere in git history.
