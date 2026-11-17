# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""
The receiving end of GitHub's webhooks: a small HTTP server on this Mac only.

It listens on 127.0.0.1, so nothing outside the machine can reach it directly; deliveries
arrive through the ngrok tunnel (tunnel.py), which is the only thing that connects to it
from outside. Because the tunnel's address is public, every delivery is checked before it
is believed: GitHub signs the body with the shared secret (HMAC-SHA256, the
X-Hub-Signature-256 header), and a body whose signature does not match is refused and
never parsed. A body has to be read before it can be checked, so a stranger's slow or
endless requests are bounded too: in time, and in how many are heard at once (Receiver).

  POST /hook      a delivery: 204 when accepted, 401 when the signature is wrong
  GET  /health    this run's nonce, so the tunnel can be checked end to end
  anything else   404

Each delivery is handed to `on_delivery` on the connection's own thread, and GitHub is
answered once that returns, so `on_delivery` must return at once (the app queues the
delivery for its screen and does not wait): GitHub gives a delivery ten seconds. The
server writes nothing to stderr: Textual draws there.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import socketserver
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

from cleantext import clean, clean_text

log = logging.getLogger("agent_frolic.webhook")

HOOK_PATH = "/hook"
HEALTH_PATH = "/health"
MAX_BODY = 25 * 1024 * 1024       # GitHub caps a payload at 25 MB
REMEMBERED = 2000                 # delivery ids kept, to drop a delivery sent twice
CHUNK = 64 * 1024                 # a body is read this much at a time, against its deadline


def sign(secret: str, body: bytes) -> str:
    """The header value GitHub sends for this body: "sha256=<hex>"."""
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def verify(secret: str, body: bytes, header: str | None) -> bool:
    """Whether the signature header matches the body. Compared in constant time. A header
    that is not plain ASCII is no signature: compare_digest would raise on it."""
    if not secret or not header or not header.isascii():
        return False
    return hmac.compare_digest(sign(secret, body), header.strip())


@dataclass
class Delivery:
    event: str                    # "pull_request", "pull_request_review", "ping", …
    id: str                       # GitHub's delivery id
    repo: str                     # "owner/name", or "" when the payload names none
    action: str                   # "opened", "submitted", …, or ""
    payload: dict = field(repr=False, default_factory=dict)
    at: float = 0.0               # when it arrived (time.time())

    @property
    def said(self) -> str:
        """One line for a log: "pull_request opened · owner/name"."""
        what = f"{self.event} {self.action}".strip()
        return f"{what} · {self.repo}" if self.repo else what


class Receiver:
    """Start it, and every delivery GitHub signs correctly is handed to `on_delivery`,
    which must not wait for anything: GitHub's answer waits for it.

    The tunnel's address is public, and a body must be read before its signature can be
    checked, so what a stranger can make it hold is bounded: each read or write gives up
    after READ_TIMEOUT_S of silence, a whole body must arrive within BODY_DEADLINE_S, and at
    most MAX_OPEN connections are handled at once; one more is closed at once and counted
    in `turned_away`. A delivery from GitHub takes milliseconds."""
    READ_TIMEOUT_S = 10.0
    BODY_DEADLINE_S = 30.0
    MAX_OPEN = 8

    def __init__(self, secret: str, on_delivery: Callable[[Delivery], None],
                 host: str = "127.0.0.1", port: int = 0) -> None:
        self._secret = secret
        self._on_delivery = on_delivery
        self._host, self._port = host, port
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._seen: deque[str] = deque(maxlen=REMEMBERED)
        self._lock = threading.Lock()
        self.nonce = secrets.token_hex(16)        # what /health answers, new each run
        self.accepted = 0
        self.refused = 0
        self.turned_away = 0                      # connections closed because MAX_OPEN were open, or too slow
        self.last: Delivery | None = None
        self._slots = threading.BoundedSemaphore(self.MAX_OPEN)
        self._warned_at = 0.0                     # when turning one away was last written to the log

    @property
    def port(self) -> int:
        return self._server.server_address[1] if self._server is not None else 0

    @property
    def running(self) -> bool:
        return self._server is not None

    def start(self) -> int:
        """Begin listening. Returns the port (a free one is picked when none was given)."""
        if self._server is not None:
            return self.port
        receiver = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "agent-frolic"
            sys_version = ""
            timeout = receiver.READ_TIMEOUT_S              # each read and write of the connection

            def log_message(self, *args) -> None:          # nothing on stderr
                pass

            def _answer(self, status: int, body: bytes = b"") -> None:
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                if body:
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                if body:
                    self.wfile.write(body)

            def do_GET(self) -> None:
                if self.path.split("?", 1)[0] == HEALTH_PATH:
                    self._answer(200, receiver.nonce.encode())
                else:
                    self._answer(404)

            def do_POST(self) -> None:
                if self.path.split("?", 1)[0] != HOOK_PATH:
                    return self._answer(404)
                try:
                    length = int(self.headers.get("Content-Length", ""))
                except ValueError:
                    return self._answer(411)
                if not 0 < length <= MAX_BODY:
                    return self._answer(413)
                body = receiver._read(self.rfile, length)
                if body is None:                           # too slow: whoever it is, drop them
                    self.close_connection = True
                    return
                status = receiver._take(body, self.headers.get("X-Hub-Signature-256"),
                                        self.headers.get("X-GitHub-Event", ""),
                                        self.headers.get("X-GitHub-Delivery", ""))
                self._answer(status)

        class Server(ThreadingHTTPServer):
            def server_bind(self) -> None:                 # without the name lookup of this machine
                socketserver.TCPServer.server_bind(self)
                self.server_name, self.server_port = self.server_address[:2]

            def handle_error(self, request, client_address) -> None:      # to the log, not stderr
                log.exception("the webhook receiver failed on a request")

            def process_request(self, request, client_address) -> None:
                if not receiver._slots.acquire(blocking=False):            # MAX_OPEN already: not heard
                    receiver._turn_away("too many connections at once")
                    self.shutdown_request(request)
                    return
                try:
                    super().process_request(request, client_address)       # its own thread
                except BaseException:
                    receiver._slots.release()
                    raise

            def process_request_thread(self, request, client_address) -> None:
                try:
                    super().process_request_thread(request, client_address)
                finally:
                    receiver._slots.release()

        self._server = Server((self._host, self._port), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, name="webhook-receiver",
                                        daemon=True)
        self._thread.start()
        log.info("webhook receiver listening on %s:%d", self._host, self.port)
        return self.port

    def stop(self) -> None:
        server, self._server = self._server, None
        if server is not None:
            server.shutdown()
            server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    def _read(self, rfile, length: int) -> bytes | None:
        """The body, or None when it did not all arrive within BODY_DEADLINE_S (or a read
        waited READ_TIMEOUT_S for nothing): a trickle cannot hold a connection open."""
        deadline = time.monotonic() + self.BODY_DEADLINE_S
        parts, left = [], length
        try:
            while left > 0:
                if time.monotonic() > deadline:
                    raise TimeoutError
                part = rfile.read1(min(left, CHUNK))
                if not part:                               # the other end gave up
                    return None
                parts.append(part)
                left -= len(part)
        except (TimeoutError, OSError):
            self._turn_away("a request body did not arrive in time")
            return None
        return b"".join(parts)

    def _turn_away(self, why: str) -> None:
        with self._lock:
            self.turned_away += 1
            now = time.monotonic()
            quiet = now - self._warned_at >= 60
            if quiet:
                self._warned_at = now
        if quiet:                                          # at most a line a minute: a flood stays out of the log
            log.warning("webhook receiver turned a connection away: %s (%d so far)", why, self.turned_away)

    def _take(self, body: bytes, signature: str | None, event: str, delivery_id: str) -> int:
        """Judge one delivery. Returns the HTTP status to answer with."""
        if not verify(self._secret, body, signature):
            with self._lock:
                self.refused += 1
            log.warning("webhook refused: the signature did not match (event %r)", event[:40])
            return 401
        try:
            payload = clean(json.loads(body))                   # what people typed, safe for a terminal
        except ValueError:
            return 400
        event, delivery_id = clean_text(event), clean_text(delivery_id)    # headers are not signed
        if not isinstance(payload, dict):
            return 400
        with self._lock:
            if delivery_id and delivery_id in self._seen:      # GitHub sent it twice
                return 204
            if delivery_id:
                self._seen.append(delivery_id)
            self.accepted += 1
        repository = payload.get("repository")
        delivery = Delivery(event=event or "unknown", id=delivery_id,
                            repo=repository.get("full_name", "") if isinstance(repository, dict) else "",
                            action=payload.get("action", "") if isinstance(payload.get("action"), str) else "",
                            payload=payload, at=time.time())
        self.last = delivery
        try:
            self._on_delivery(delivery)
        except Exception:                                      # the app's trouble is not GitHub's
            log.exception("handling a webhook delivery failed")
        return 204
