# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""
The secrets agent-frolic holds, kept in the macOS Keychain and nowhere else.

There are three, all under the Keychain service "agent-frolic":

  • github-token      the GitHub token the user pasted
  • webhook-secret    made here; GitHub signs every webhook delivery with it
  • ngrok-authtoken   the user's ngrok token, for the tunnel the webhooks arrive through

Why the Keychain, through the Security framework (the `keyring` library's macOS backend)
and not the `security` command-line tool: an item made this way is tied to the program
that made it. The signed app reads its own items silently; anything else that asks gets
macOS's allow / deny dialog. An item made by the `security` tool trusts that tool, so any
program on the Mac could read it by running the tool.

Rules for the rest of the code:
  • a secret is never written to a file, a setting, a log line or an error message;
  • show `mask(secret)` on screen, never the secret;
  • a secret is passed in memory to the one call that needs it, not kept on an object
    that outlives the call when that can be avoided.

From a checkout the item belongs to the Python in .venv, so a different Python (or a
rebuilt, ad-hoc signed app) is asked for permission once.
"""
from __future__ import annotations

import logging
import secrets as _random
import sys

log = logging.getLogger("agent_frolic.keychain")

SERVICE = "agent-frolic"
GITHUB_TOKEN = "github-token"
WEBHOOK_SECRET = "webhook-secret"
NGROK_AUTHTOKEN = "ngrok-authtoken"
NAMES = (GITHUB_TOKEN, WEBHOOK_SECRET, NGROK_AUTHTOKEN)


class KeychainError(Exception):
    """The Keychain could not be read or written. The message never contains a secret."""


_backend = None


def backend():
    """The store behind get / put / forget. On a Mac this is the login Keychain, chosen
    explicitly: the library's own discovery reads package metadata that a PyInstaller build
    does not carry."""
    global _backend
    if _backend is None:
        if sys.platform == "darwin":
            from keyring.backends import macOS
            _backend = macOS.Keyring()
        else:                                    # not a Mac: whatever the library finds (tests, mostly)
            import keyring
            _backend = keyring.get_keyring()
    return _backend


def use(store) -> None:
    """Swap the store. Tests pass an in-memory one so they never touch the real Keychain;
    pass None to go back to the real one."""
    global _backend
    _backend = store


def _check(name: str) -> None:
    if name not in NAMES:
        raise KeychainError(f"{name!r} is not one of agent-frolic's secrets")


def get(name: str) -> str | None:
    """The secret, or None when there is none (or the user refused the Keychain dialog)."""
    _check(name)
    try:
        return backend().get_password(SERVICE, name) or None
    except Exception as e:                       # KeyringLocked, a denied dialog, a broken Keychain
        log.warning("Keychain read of %s failed: %s", name, type(e).__name__)
        raise KeychainError(f"the Keychain did not give up {name}: {type(e).__name__}") from None


def put(name: str, value: str) -> None:
    _check(name)
    value = value.strip()
    if not value:
        raise KeychainError(f"refusing to store an empty {name}")
    try:
        backend().set_password(SERVICE, name, value)
    except Exception as e:
        log.warning("Keychain write of %s failed: %s", name, type(e).__name__)
        raise KeychainError(f"the Keychain did not accept {name}: {type(e).__name__}") from None


def forget(name: str) -> None:
    """Remove the secret. Not having one is not an error."""
    _check(name)
    try:
        if backend().get_password(SERVICE, name) is not None:
            backend().delete_password(SERVICE, name)
    except Exception as e:
        log.warning("Keychain delete of %s failed: %s", name, type(e).__name__)
        raise KeychainError(f"the Keychain did not delete {name}: {type(e).__name__}") from None


def has(name: str) -> bool:
    try:
        return get(name) is not None
    except KeychainError:
        return False


def webhook_secret() -> str:
    """The secret GitHub signs deliveries with: made on first use, then kept."""
    secret = get(WEBHOOK_SECRET)
    if secret is None:
        secret = _random.token_hex(32)
        put(WEBHOOK_SECRET, secret)
    return secret


def mask(secret: str | None) -> str:
    """What may be shown of a secret: its public prefix and its last four characters,
    "ghp_…a1b2". Short ones show nothing at all."""
    if not secret:
        return ""
    head = ""
    for prefix in ("github_pat_", "ghp_", "gho_", "ghu_", "ghs_"):
        if secret.startswith(prefix):
            head = prefix
            break
    if len(secret) - len(head) < 12:
        return f"{head}…"
    return f"{head}…{secret[-4:]}"
