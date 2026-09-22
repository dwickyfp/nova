"""Authenticated, read-only bridge for Ranger plugin policy downloads."""

from __future__ import annotations

import base64
import os
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

UPSTREAM = os.environ.get("RANGER_ADMIN_URL", "http://ranger-admin:6080").rstrip("/")
USERNAME = os.environ.get("RANGER_USERNAME", "admin")
PASSWORD = os.environ["RANGER_PASSWORD"]
MAX_RESPONSE_BYTES = 64 * 1024 * 1024
SECURE_PATHS = {
    "/service/plugins/policies/download/": "/service/plugins/secure/policies/download/",
    "/service/roles/download/": "/service/roles/secure/download/",
    "/service/tags/download/": "/service/tags/secure/download/",
    "/service/xusers/download/": "/service/xusers/secure/download/",
}
AUTHORIZATION = "Basic " + base64.b64encode(
    f"{USERNAME}:{PASSWORD}".encode()
).decode()


def is_allowed_path(path: str) -> bool:
    parsed = urlsplit(path)
    return not parsed.scheme and not parsed.netloc and any(
        parsed.path.startswith(prefix) for prefix in SECURE_PATHS
    )


def secure_path(path: str) -> str:
    parsed = urlsplit(path)
    for public_prefix, secure_prefix in SECURE_PATHS.items():
        if parsed.path.startswith(public_prefix):
            suffix = parsed.path.removeprefix(public_prefix)
            query = f"?{parsed.query}" if parsed.query else ""
            return f"{secure_prefix}{suffix}{query}"
    raise ValueError("path is not an allowed Ranger download endpoint")


class PolicyProxyHandler(BaseHTTPRequestHandler):
    server_version = "NovaRangerPolicyProxy/1"

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/healthz":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
            return
        if not is_allowed_path(self.path):
            self.send_error(404)
            return

        headers = {
            "Authorization": AUTHORIZATION,
            "Accept": self.headers.get("Accept", "application/json"),
        }
        cookie = self.headers.get("Cookie")
        if cookie:
            headers["Cookie"] = cookie
        request = urllib.request.Request(
            f"{UPSTREAM}{secure_path(self.path)}", headers=headers
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                body = response.read(MAX_RESPONSE_BYTES + 1)
                if len(body) > MAX_RESPONSE_BYTES:
                    self.send_error(502, "Ranger response exceeded proxy limit")
                    return
                self.send_response(response.status)
                for name in ("Content-Type", "ETag", "Last-Modified", "Set-Cookie"):
                    value = response.headers.get(name)
                    if value:
                        self.send_header(name, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        except urllib.error.HTTPError as exc:
            body = exc.read(MAX_RESPONSE_BYTES)
            self.send_response(exc.code)
            self.send_header("Content-Type", exc.headers.get("Content-Type", "text/plain"))
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except OSError:
            self.send_error(502, "Ranger Admin unavailable")

    def log_message(self, _format: str, *_args: object) -> None:
        return


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 6081), PolicyProxyHandler).serve_forever()
