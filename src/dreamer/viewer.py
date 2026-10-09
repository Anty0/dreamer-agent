"""`dreamer-view`: a local page that tails a transcript as it grows."""

from __future__ import annotations

import contextlib
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
        items.append(
            {"file": p.name, "name": m[1], "kind": m[2], "size": st.st_size, "mtime": st.st_mtime}
        )
    return items


def _complete_lines(path: Path, offset: int) -> tuple[list[str], int]:
    """Lines fully terminated by newline after byte `offset`, and the new offset."""
    with path.open("rb") as f:
        f.seek(offset)
        chunk = f.read()
    end = chunk.rfind(b"\n")
    if end < 0:
        return [], offset
    complete = chunk[:end]
    return complete.decode("utf-8", errors="replace").split("\n"), offset + end + 1


class _Handler(BaseHTTPRequestHandler):
    runs_dir: Path

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_GET(self) -> None:
        url = urlparse(self.path)
        if url.path == "/":
            page = files("dreamer").joinpath("viewer.html").read_bytes()
            self._send(200, "text/html; charset=utf-8", page)
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
                self.wfile.write(b":\n\n")
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
    host, port = str(server.server_address[0]), server.server_address[1]
    url = f"http://{host}:{port}{url_path}"
    print(f"dreamer-view: {url}  (runs in {runs_dir})")
    webbrowser.open(url)
    with contextlib.suppress(KeyboardInterrupt):
        server.serve_forever()
