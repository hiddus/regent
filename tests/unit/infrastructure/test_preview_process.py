"""Unit tests for PreviewProcessSupervisor (R1)."""

from __future__ import annotations

from pathlib import Path

from regent.infrastructure.preview_process import (
    PreviewProcessSupervisor,
    rewrite_start_command,
)


def test_rewrite_start_command_port() -> None:
    assert "--port 9999" in rewrite_start_command(
        "python -m flask --app src.app run --host 127.0.0.1 --port 8080",
        port=9999,
    )
    assert "--port 4242" in rewrite_start_command(
        "uvicorn src.app:app --host 127.0.0.1",
        port=4242,
    )


def test_supervisor_start_ready_stop(tmp_path: Path) -> None:
    (tmp_path / "serve.py").write_text(
        "import os\n"
        "from http.server import BaseHTTPRequestHandler, HTTPServer\n"
        "PORT = int(os.environ['PORT'])\n"
        "class H(BaseHTTPRequestHandler):\n"
        "    def do_GET(self):\n"
        "        self.send_response(200); self.end_headers(); self.wfile.write(b'ok')\n"
        "    def log_message(self, *a): pass\n"
        "HTTPServer(('127.0.0.1', PORT), H).serve_forever()\n",
        encoding="utf-8",
    )
    supervisor = PreviewProcessSupervisor()
    handle = supervisor.start(
        deployment_id="d1",
        workspace=tmp_path,
        start_command="python serve.py",
    )
    try:
        ready = supervisor.wait_ready(handle, routes=["/"], timeout_seconds=15.0)
        assert ready.get("ready") is True
        assert handle.port > 0
    finally:
        supervisor.stop("d1")
