"""Small in-cluster Wanderer API test double for the Kind integration test."""
from __future__ import annotations

import cgi
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

TOKEN = os.environ["MOCK_TOKEN"]
UPLOAD_DIR = Path("/tmp/uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *_args: object) -> None:
        # Do not print request headers or route names in the test log.
        return

    def _send(self, status: int, body: bytes, content_type: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        if self.path == "/healthz":
            self._send(200, b"ok\n", "text/plain; charset=utf-8")
            return
        if self.path == "/received":
            files = sorted(path.name for path in UPLOAD_DIR.iterdir() if path.is_file())
            self._send(200, json.dumps({"count": len(files), "files": files}).encode())
            return
        self._send(404, b"{}")

    def do_PUT(self) -> None:  # noqa: N802 - stdlib handler API
        if self.path != "/api/v1/trail/upload":
            self._send(404, b"{}")
            return
        if self.headers.get("Authorization") != f"Bearer {TOKEN}":
            self._send(401, b"{}")
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            form = cgi.FieldStorage(
                fp=self.rfile,  # type: ignore[arg-type]
                headers=self.headers,
                environ={
                    "REQUEST_METHOD": "PUT",
                    "CONTENT_TYPE": self.headers.get("Content-Type", ""),
                    "CONTENT_LENGTH": str(length),
                },
            )
            file_field = form["file"]
            filename = Path(file_field.filename or "upload").name
            content = file_field.file.read()
        except (KeyError, TypeError, ValueError, OSError):
            self._send(400, b"{}")
            return

        if not content:
            self._send(400, b"{}")
            return
        (UPLOAD_DIR / filename).write_bytes(content)
        self._send(204, b"", "text/plain; charset=utf-8")


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
