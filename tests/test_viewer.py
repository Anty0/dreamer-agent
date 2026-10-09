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
