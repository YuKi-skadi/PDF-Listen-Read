"""HTTP bridge implementing the PDF reader's local TTS protocol."""

from __future__ import annotations

import json
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse


SCHEMA_VERSION = "pdf-local-tts-1"


class BridgeServer:
    def __init__(self, manager: Any, host: str = "127.0.0.1", port: int = 47840):
        self.manager = manager
        self.host = host
        self.port = int(port)
        self.httpd: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None

    def start(self) -> tuple[bool, str]:
        if self.httpd:
            return True, f"http://{self.host}:{self.port}"
        try:
            handler = self._handler_class()
            self.httpd = ThreadingHTTPServer((self.host, self.port), handler)
            self.httpd.daemon_threads = True
            self.thread = threading.Thread(target=self.httpd.serve_forever, name="pdf-bridge-http", daemon=True)
            self.thread.start()
            return True, f"http://{self.host}:{self.port}"
        except OSError as exc:
            self.httpd = None
            return False, str(exc)

    def stop(self) -> None:
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
        self.thread = None

    def _handler_class(self):
        manager = self.manager

        class Handler(BaseHTTPRequestHandler):
            server_version = "PdfLocalTTSBridge/1.0"

            def log_message(self, fmt: str, *args: Any) -> None:
                manager.log_event(fmt % args)

            def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _send_error_json(self, message: str, status: int = 400) -> None:
                self._send_json({"ok": False, "error": message}, status)

            def _read_json(self) -> dict[str, Any]:
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    data = self.rfile.read(length)
                    value = json.loads(data.decode("utf-8"))
                    if not isinstance(value, dict):
                        raise ValueError("JSON body must be an object")
                    return value
                except Exception as exc:
                    raise ValueError(f"invalid JSON body: {exc}") from exc

            def do_GET(self) -> None:
                path = urlparse(self.path).path.rstrip("/") or "/"
                if path == "/health":
                    self._send_json(manager.health_payload())
                    return
                if path.startswith("/v1/jobs/"):
                    job_id = path.removeprefix("/v1/jobs/").strip("/")
                    if job_id.endswith("/package"):
                        job_id = job_id.removesuffix("/package").strip("/")
                        package_path = manager.package_path(job_id)
                        if not package_path:
                            self._send_error_json("package is not ready", 404)
                            return
                        body = package_path.read_bytes()
                        self.send_response(200)
                        self.send_header("Content-Type", "application/zip")
                        self.send_header("Content-Disposition", f'attachment; filename="{package_path.name}"')
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)
                        return
                    job = manager.get_job(job_id)
                    if not job:
                        self._send_error_json("job not found", 404)
                    else:
                        self._send_json({"job": manager.public_job(job)})
                    return
                self._send_error_json("not found", 404)

            def do_POST(self) -> None:
                path = urlparse(self.path).path.rstrip("/") or "/"
                try:
                    if path == "/v1/jobs":
                        request = self._read_json()
                        job = manager.create_job(request)
                        self._send_json({"job": manager.public_job(job)}, 202)
                        return
                    if path.startswith("/v1/jobs/") and path.endswith("/cancel"):
                        job_id = path.removeprefix("/v1/jobs/").removesuffix("/cancel").strip("/")
                        job = manager.cancel_job(job_id)
                        if not job:
                            self._send_error_json("job not found", 404)
                        else:
                            self._send_json({"job": manager.public_job(job)})
                        return
                    self._send_error_json("not found", 404)
                except ValueError as exc:
                    manager.log_event(f"请求失败：{exc}", level="error")
                    self._send_error_json(str(exc), 400)
                except Exception as exc:  # keep the HTTP service alive on task errors
                    manager.log_event(f"请求处理失败：{exc}", level="error", details=traceback.format_exc())
                    self._send_error_json("internal bridge error", 500)

        return Handler
