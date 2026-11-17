# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""
The ngrok tunnel that GitHub's webhooks arrive through.

GitHub needs a public https address to deliver to, and this Mac sits behind a router. The
ngrok library (it runs inside this program; there is no separate ngrok to install) opens a
tunnel from the user's own ngrok domain to the receiver on 127.0.0.1 (webhook.py).

The user supplies two things, once: their ngrok authtoken, which is kept in the Keychain,
and their ngrok domain ("your-name.ngrok-free.dev"; a free account has one, and it stays
the same across restarts, so the address GitHub delivers to never changes).

The tunnel is open only while the app runs. Whatever GitHub sends while it is closed is
lost, which is why the app also polls.
"""
from __future__ import annotations

import logging
import re
import time
import urllib.error
import urllib.request
from typing import Callable

from webhook import HEALTH_PATH, HOOK_PATH

log = logging.getLogger("agent_frolic.tunnel")

_HOST = re.compile(r"^(?=.{4,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$")


class TunnelError(Exception):
    """The tunnel could not be opened. str() is fit to show; it never holds the authtoken."""


def clean_domain(text: str) -> str:
    """The bare host from what the user typed: "https://Name.ngrok-free.dev/" becomes
    "name.ngrok-free.dev". Raises ValueError with a sentence to show."""
    host = text.strip().lower()
    host = re.sub(r"^[a-z]+://", "", host).split("/", 1)[0]
    if not host:
        raise ValueError("Type your ngrok domain, for example your-name.ngrok-free.dev.")
    if not _HOST.match(host):
        raise ValueError(f"“{text.strip()}” is not a domain. It looks like your-name.ngrok-free.dev.")
    return host


def hook_url(domain: str) -> str:
    """The address GitHub is told to deliver to."""
    return f"https://{domain}{HOOK_PATH}"


def _ngrok_open(port: int, authtoken: str, domain: str):
    import ngrok                                   # imported late: only people who use webhooks need it
    return ngrok.forward(f"127.0.0.1:{port}", authtoken=authtoken, domain=domain)


def _ngrok_close(listener) -> None:
    import ngrok
    ngrok.disconnect(listener.url())


class Tunnel:
    """One tunnel from https://<domain> to 127.0.0.1:<port>."""

    def __init__(self, domain: str, open_: Callable = _ngrok_open, close: Callable = _ngrok_close) -> None:
        self.domain = domain
        self._open, self._close = open_, close
        self._listener = None

    @property
    def running(self) -> bool:
        return self._listener is not None

    @property
    def url(self) -> str:
        return f"https://{self.domain}"

    def start(self, port: int, authtoken: str) -> str:
        """Open the tunnel to the local port. Returns its public address. The authtoken is
        used for this call and not kept. Raises TunnelError."""
        if self._listener is not None:
            return self.url
        try:
            self._listener = self._open(port, authtoken, self.domain)
        except Exception as e:
            raise TunnelError(_explain(str(e), authtoken)) from None
        log.info("tunnel open: %s -> 127.0.0.1:%d", self.url, port)
        return self.url

    def stop(self) -> None:
        listener, self._listener = self._listener, None
        if listener is not None:
            try:
                self._close(listener)
            except Exception as e:
                log.warning("closing the tunnel failed: %s", type(e).__name__)


def _explain(said: str, authtoken: str) -> str:
    """ngrok's error, as one sentence for the user, with the authtoken struck out in case
    the library quoted it."""
    if authtoken:
        said = said.replace(authtoken, "…")
    code = re.search(r"ERR_NGROK_(\d+)", said)
    number = int(code.group(1)) if code else 0
    if number in (105, 106, 107) or "authtoken" in said.lower() and "invalid" in said.lower():
        return "ngrok did not accept the authtoken. Copy it again from your ngrok dashboard."
    if number == 108:
        return ("Your ngrok account already has its one free agent session running somewhere else. "
                "Close that one, or this one cannot start.")
    if number in (319, 320, 326) or "domain" in said.lower() and ("not" in said.lower() or "reserved" in said.lower()):
        return ("ngrok would not serve that domain for this account. Check the domain on your ngrok "
                "dashboard; a free account has exactly one.")
    if number == 334:
        return "That ngrok domain is already in use by another running tunnel."
    first = said.strip().splitlines()[0] if said.strip() else "no reason given"
    return f"ngrok could not open the tunnel: {first[:200]}"


def wait_reachable(url: str, nonce: str, tries: int = 8, pause: float = 1.5,
                   check: Callable[[str, str], bool] | None = None,
                   sleep: Callable[[float], object] = time.sleep,
                   stop: Callable[[], bool] = lambda: False) -> bool:
    """`reachable`, asked a few times. A tunnel that has just opened can take a few seconds
    before ngrok's edge sends traffic to it (longer when the app was restarted a moment ago
    and its old tunnel is still winding down), so one early miss does not mean it is broken.
    False as soon as `stop()` says so, before the next try: the app is closing, or the
    webhooks were turned off. A `sleep` that is an Event's wait ends a pause early, too."""
    check = check or reachable
    for attempt in range(tries):
        if stop():
            return False
        if check(url, nonce):
            return True
        if attempt + 1 < tries:
            sleep(pause)
    return False


def reachable(url: str, nonce: str, timeout: float = 10.0,
              opener: Callable = urllib.request.urlopen) -> bool:
    """Whether the public address leads to this run's receiver: its /health must answer
    with this run's nonce. This proves the whole path GitHub's deliveries will take."""
    request = urllib.request.Request(url.rstrip("/") + HEALTH_PATH,
                                     headers={"ngrok-skip-browser-warning": "1",
                                              "User-Agent": "agent-frolic"})
    try:
        with opener(request, timeout=timeout) as reply:
            return reply.read(200).decode("utf-8", "replace").strip() == nonce
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return False
