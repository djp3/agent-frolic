# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Where agent-frolic keeps its own files.

Run from a checkout (`python main.py`) everything stays next to the code: logs/,
settings.json and repos.json, all of them ignored by git. Run as the bundled Mac app
(PyInstaller sets sys.frozen) the bundle is signed and read-only, so logs go to
~/Library/Logs/agent-frolic and the rest to ~/Library/Application Support/agent-frolic.

Secrets are in neither place: they live in the macOS Keychain (see keychain.py).
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

log = logging.getLogger("agent_frolic.files")

APP_NAME = "agent-frolic"
FROZEN = bool(getattr(sys, "frozen", False))
CODE_DIR = Path(__file__).resolve().parent


def log_dir() -> Path:
    if FROZEN:
        return Path.home() / "Library" / "Logs" / APP_NAME
    return CODE_DIR / "logs"


def data_dir() -> Path:
    if FROZEN:
        return Path.home() / "Library" / "Application Support" / APP_NAME
    return CODE_DIR


def write_json(path: Path, data: object) -> bool:
    """Save `data` to `path` as JSON, whole or not at all: it is written to a file beside
    it, made sure of on disk, then put in its place in one step, so a crash or a full disk
    leaves the last good file. Trouble is logged, never raised: the app goes on with what it
    has in memory. Returns whether it was saved."""
    tmp = path.with_suffix(".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "w") as f:
            f.write(json.dumps(data, indent=2) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        return True
    except Exception as e:
        log.warning("could not write %s: %r", path, e)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def app_version() -> str:
    """The release number without a leading v, or "" if unknown. The Mac app passes its
    bundle version in AGENT_FROLIC_VERSION; a run from a checkout reads the VERSION file."""
    v = os.environ.get("AGENT_FROLIC_VERSION", "").strip()
    if not v:
        try:
            v = (CODE_DIR / "VERSION").read_text().strip()
        except OSError:
            v = ""
    return v.removeprefix("v")
