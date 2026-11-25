#!/usr/bin/env python3
"""Gate in front of the Tailscale address for SearXNG.

The container stays on 127.0.0.1. Open WebUI calls it by container name and
voice-gateway calls 127.0.0.1, both with format=json. Those paths do not pass
through here.

This process is the only listener on the Tailscale address. It forwards the
HTML UI and refuses the machine API (json, csv, rss), the image fetcher, and
the stats and config pages. A browser on the tailnet must not be able to turn
this host into an unauthenticated search API or an image proxy.
"""

from __future__ import annotations

import email.utils
import logging
import os
import sys
import threading
import time
import urllib.parse
from http.client import HTTPConnection, HTTPException
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOG = logging.getLogger("searxng-tailnet")

BLOCKED_FORMATS = frozenset({"json", "csv", "rss"})
BLOCKED_PATHS = ("/image", "/image_proxy", "/stats", "/config")
ALLOWED_METHODS = frozenset({"GET", "HEAD", "POST"})
HOP_HEADERS = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
    }
)
STRIP_REQUEST_HEADERS = HOP_HEADERS | frozenset(
    {
        "forwarded",
        "x-forwarded-for",
        "x-forwarded-host",
        "x-forwarded-proto",
        "x-real-ip",
    }
)
# Reinforced on every response. frame-ancestors and Permissions-Policy are not
# set by SearXNG. nosniff and no-referrer match what SearXNG already sends.
SECURITY_HEADERS = (
    ("X-Frame-Options", "DENY"),
    ("Content-Security-Policy", "frame-ancestors 'none'"),
    ("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()"),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
)

MAX_TARGET = 4096
MAX_HEADER_BYTES = 16384
MAX_BODY = 64 * 1024
MAX_RESPONSE = 8 * 1024 * 1024
UPSTREAM_TIMEOUT = 20

# Human browsing: a handful of searches back to back, then a steady rate.
# Static assets are not counted here.
SEARCH_BURST = 8
SEARCH_PER_MINUTE = 30
# Parallel /search calls are what fan out to the upstream engines.
SEARCH_SLOTS = threading.BoundedSemaphore(3)


def env_hosts() -> frozenset[str]:
    raw = os.environ.get(
        "SEARXNG_ALLOWED_HOSTS",
        "ser5,ser5:8888,ser5.tail6bfbde.ts.net,ser5.tail6bfbde.ts.net:8888,"
        "100.125.180.85,100.125.180.85:8888",
    )
    return frozenset(part.strip().lower() for part in raw.split(",") if part.strip())


def upstream_addr() -> tuple[str, int]:
    raw = os.environ.get("SEARXNG_UPSTREAM", "127.0.0.1:8888")
    host, _, port = raw.rpartition(":")
    return host or "127.0.0.1", int(port or "8888")


def bind_addr() -> tuple[str, int]:
    host = os.environ.get("SEARXNG_TAILNET_BIND", "100.125.180.85")
    port = int(os.environ.get("SEARXNG_TAILNET_PORT", "8888"))
    return host, port


def default_host() -> str:
    return os.environ.get("SEARXNG_DEFAULT_HOST", "ser5:8888")


class TokenBucket:
    """Per-key token bucket. `now` is injectable so the tests do not sleep."""

    def __init__(self, burst: float, per_minute: float) -> None:
        self.burst = burst
        self.rate = per_minute / 60.0
        self._lock = threading.Lock()
        self._buckets: dict[str, tuple[float, float]] = {}

    def allow(self, key: str, now: float | None = None) -> bool:
        moment = time.monotonic() if now is None else now
        with self._lock:
            tokens, updated = self._buckets.get(key, (self.burst, moment))
            tokens = min(self.burst, tokens + (moment - updated) * self.rate)
            if tokens < 1.0:
                self._buckets[key] = (tokens, moment)
                self._prune(moment)
                return False
            self._buckets[key] = (tokens - 1.0, moment)
            self._prune(moment)
            return True

    def _prune(self, now: float) -> None:
        if len(self._buckets) < 4096:
            return
        stale = [key for key, (_, updated) in self._buckets.items() if now - updated > 600]
        for key in stale:
            del self._buckets[key]


SEARCH_LIMIT = TokenBucket(SEARCH_BURST, SEARCH_PER_MINUTE)


def _path_of(target: str) -> str:
    return urllib.parse.urlsplit(target).path or "/"


def _blocked_path(path: str) -> bool:
    base = path.rstrip("/") or "/"
    return any(base == item or base.startswith(item + "/") for item in BLOCKED_PATHS)


def _format_values(query: str, body: bytes, content_type: str) -> list[str]:
    values = urllib.parse.parse_qs(query, keep_blank_values=True).get("format", [])
    media = content_type.split(";", 1)[0].strip().lower()
    if media in ("", "application/x-www-form-urlencoded"):
        text = body.decode("utf-8", "replace")
        values.extend(urllib.parse.parse_qs(text, keep_blank_values=True).get("format", []))
    return values


def refuse(method: str, target: str, body: bytes, content_type: str) -> int | None:
    """Return an HTTP status to stop the request, or None to forward it.

    Query text is not inspected. The decision uses the method, the path, and
    the format parameter only.
    """

    if method not in ALLOWED_METHODS:
        return 405
    if len(target) > MAX_TARGET or any(char in target for char in "\r\n\x00"):
        return 414
    parts = urllib.parse.urlsplit(target)
    # An absolute request target (http://other-host/...) must not be forwarded.
    # HTTPConnection would put that string on the request line.
    if parts.scheme or parts.netloc:
        return 400
    path = parts.path or "/"
    if not path.startswith("/") or path.startswith("//"):
        return 400
    if _blocked_path(path):
        return 403
    formats = [item.strip().lower() for item in _format_values(parts.query, body, content_type)]
    if any(item in BLOCKED_FORMATS or (item and item != "html") for item in formats):
        return 403
    return None


def safe_host(client_host: str, allowed: frozenset[str], fallback: str) -> str:
    candidate = client_host.strip().lower()
    if candidate in allowed and "\r" not in candidate and "\n" not in candidate:
        return candidate
    return fallback


class Gate(BaseHTTPRequestHandler):
    server_version = "searxng-gate"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def handle_one_request(self) -> None:
        self.close_connection = True
        super().handle_one_request()

    def do_GET(self) -> None:  # noqa: N802
        self._proxy()

    def do_HEAD(self) -> None:  # noqa: N802
        self._proxy()

    def do_POST(self) -> None:  # noqa: N802
        self._proxy()

    def do_PUT(self) -> None:  # noqa: N802
        self._refuse(405, "method not allowed")

    def do_DELETE(self) -> None:  # noqa: N802
        self._refuse(405, "method not allowed")

    def do_PATCH(self) -> None:  # noqa: N802
        self._refuse(405, "method not allowed")

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._refuse(405, "method not allowed")

    def log_message(self, fmt: str, *args: object) -> None:
        # The default log line includes the request target, and that carries
        # the search query. Decisions are logged in _proxy without the query.
        return

    def _client(self) -> str:
        return self.client_address[0]

    def _read_body(self) -> bytes | int:
        raw = self.headers.get("Content-Length")
        if raw is None:
            return b""
        try:
            length = int(raw)
        except ValueError:
            return 400
        if length < 0 or length > MAX_BODY:
            return 413
        return self.rfile.read(length)

    def _proxy(self) -> None:
        header_blob = str(self.headers)
        if len(header_blob) > MAX_HEADER_BYTES:
            self._refuse(431, "headers too large")
            return
        body = self._read_body()
        if isinstance(body, int):
            message = "too large" if body == 413 else "bad request"
            self._refuse(body, message)
            return
        content_type = self.headers.get("Content-Type", "")
        status = refuse(self.command, self.path, body, content_type)
        path = _path_of(self.path)
        limited = status is None and path.rstrip("/") == "/search"
        if limited and not SEARCH_LIMIT.allow(self._client()):
            status = 429
        if status is not None:
            LOG.info("deny %s %s %s", self._client(), self.command, path)
            message = {
                400: "bad request",
                403: "forbidden",
                405: "method not allowed",
                413: "too large",
                414: "uri too long",
                429: "too many requests",
                431: "headers too large",
            }.get(status, "forbidden")
            self._refuse(status, message, retry=status == 429)
            return
        if not limited:
            self._forward(body)
            return
        if not SEARCH_SLOTS.acquire(blocking=False):
            LOG.info("busy %s %s %s", self._client(), self.command, path)
            self._refuse(429, "too many requests", retry=True)
            return
        try:
            self._forward(body)
        finally:
            SEARCH_SLOTS.release()

    def _forward(self, body: bytes) -> None:
        host, port = upstream_addr()
        upstream_host = safe_host(
            self.headers.get("Host", ""),
            env_hosts(),
            default_host(),
        )
        headers = {"Host": upstream_host, "Connection": "close"}
        for key, value in self.headers.items():
            if key.lower() in STRIP_REQUEST_HEADERS or key.lower() == "host":
                continue
            headers[key] = value
        if body:
            headers["Content-Length"] = str(len(body))
        conn = HTTPConnection(host, port, timeout=UPSTREAM_TIMEOUT)
        try:
            conn.request(self.command, self.path, body=body if body else None, headers=headers)
            resp = conn.getresponse()
            payload = resp.read(MAX_RESPONSE + 1)
        except (OSError, HTTPException, TimeoutError):
            LOG.info("upstream down %s %s", self.command, _path_of(self.path))
            self._refuse(502, "upstream unavailable")
            return
        finally:
            conn.close()
        if len(payload) > MAX_RESPONSE:
            self._refuse(502, "upstream unavailable")
            return
        self.send_response_only(resp.status)
        sent: set[str] = set()
        for key, value in resp.getheaders():
            lowered = key.lower()
            if lowered in HOP_HEADERS or lowered in ("server", "date", "content-length"):
                continue
            if lowered in {name.lower() for name, _ in SECURITY_HEADERS}:
                continue
            self.send_header(key, value)
            sent.add(lowered)
        for key, value in SECURITY_HEADERS:
            self.send_header(key, value)
        self.send_header("Date", email.utils.formatdate(usegmt=True))
        self.send_header("Connection", "close")
        if self.command != "HEAD":
            self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    def _refuse(self, status: int, message: str, retry: bool = False) -> None:
        payload = (message + "\n").encode("ascii")
        self.send_response_only(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(payload) if self.command != "HEAD" else 0))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.send_header("Date", email.utils.formatdate(usegmt=True))
        if retry:
            self.send_header("Retry-After", "3")
        for key, value in SECURITY_HEADERS:
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)


def self_test() -> int:
    assert refuse("GET", "/search?q=test&format=json", b"", "") == 403
    assert refuse("GET", "/search?q=test&format=csv", b"", "") == 403
    assert refuse("GET", "/search?q=test&format=rss", b"", "") == 403
    assert refuse("GET", "/search?q=test&format=HTML", b"", "") is None
    assert refuse("GET", "/search?q=test", b"", "") is None
    assert refuse("GET", "/", b"", "") is None
    assert refuse("GET", "/static/themes/simple/sxng-ltr.min.css", b"", "") is None
    assert refuse("GET", "/image?url=http://127.0.0.1/", b"", "") == 403
    assert refuse("GET", "/image_proxy", b"", "") == 403
    assert refuse("GET", "/stats", b"", "") == 403
    assert refuse("GET", "/stats/errors", b"", "") == 403
    assert refuse("GET", "/config", b"", "") == 403
    assert refuse("POST", "/search", b"q=test&format=json", "application/x-www-form-urlencoded") == 403
    assert refuse("POST", "/preferences", b"image_proxy=1", "application/x-www-form-urlencoded") is None
    assert refuse("PUT", "/", b"", "") == 405
    assert refuse("GET", "//evil.example/search", b"", "") == 400
    assert refuse("GET", "http://evil.example/search?q=1", b"", "") == 400
    assert refuse("GET", "/search?q=" + ("a" * 5000), b"", "") == 414
    assert refuse("GET", "/search?q=ok\r\nHost:evil", b"", "") == 414

    allowed = env_hosts()
    assert safe_host("ser5:8888", allowed, "ser5:8888") == "ser5:8888"
    assert safe_host("Evil.example", allowed, "ser5:8888") == "ser5:8888"
    assert safe_host("ser5:8888\r\nX-Injected:1", allowed, "ser5:8888") == "ser5:8888"

    bucket = TokenBucket(2, 60)
    assert bucket.allow("10.0.0.1", now=0) is True
    assert bucket.allow("10.0.0.1", now=0) is True
    assert bucket.allow("10.0.0.1", now=0) is False
    assert bucket.allow("10.0.0.2", now=0) is True
    assert bucket.allow("10.0.0.1", now=60) is True
    return 0


def serve() -> None:
    logging.basicConfig(level=logging.INFO, format="%(name)s %(message)s")
    host, port = bind_addr()
    httpd = ThreadingHTTPServer((host, port), Gate)
    LOG.info("listening on %s:%s", host, port)
    httpd.serve_forever()


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        raise SystemExit(self_test())
    serve()
