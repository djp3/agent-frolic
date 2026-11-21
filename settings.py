# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""
User settings for agent-frolic, persisted as settings.json next to the code, or under
~/Library/Application Support/agent-frolic when running as the bundled Mac app
(override the path with AGENT_FROLIC_SETTINGS). Add a setting by adding a field with a
default; unknown or malformed keys in the file are ignored so an old file never breaks
startup.

Nothing secret belongs here. Tokens are in the Keychain (keychain.py); the repos are in
their own file (repos.py).
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, fields
from typing import ClassVar
from pathlib import Path

from apppaths import data_dir, write_json

log = logging.getLogger("agent_frolic.settings")

DEFAULT_PATH = data_dir() / "settings.json"


@dataclass
class Settings:
    webhooks: bool = False          # receive GitHub webhooks through an ngrok tunnel while the app is open
    ngrok_domain: str = ""          # the ngrok domain the tunnel uses: "your-name.ngrok-free.dev"
    poll_minutes: int = 5           # minutes between two reads of a repository on its timer (1 to POLL_MOST).
                                    # One GitHub sends webhooks for, with nothing amiss, is read every
                                    # Frolic.SAFETY_POLLS times that (Frolic.period)

    grange_port: int = 7788         # where the agent-grange on this Mac listens: GRANGE_PORT in its .env.
                                    # It is asked on 127.0.0.1 alone

    POLL_MOST: ClassVar[int] = 24 * 60

    @classmethod
    def path(cls) -> Path:
        return Path(os.environ.get("AGENT_FROLIC_SETTINGS") or DEFAULT_PATH)

    @classmethod
    def load(cls) -> "Settings":
        s = cls()
        p = cls.path()
        try:
            data = json.loads(p.read_text())
        except FileNotFoundError:
            return s
        except Exception as e:
            log.warning("could not read %s: %r (using defaults)", p, e)
            return s
        if not isinstance(data, dict):
            return s
        for f in fields(cls):
            if f.name in data and type(data[f.name]) is type(getattr(s, f.name)):
                setattr(s, f.name, data[f.name])
        kept = max(1, min(s.poll_minutes, cls.POLL_MOST))   # 0 would poll every second: the hourly
        if kept != s.poll_minutes:                           # allowance gone in minutes
            log.warning("poll_minutes %d in %s is out of range; using %d", s.poll_minutes, p, kept)
            s.poll_minutes = kept
        if not 1 <= s.grange_port <= 65535:
            usual = cls().grange_port
            log.warning("grange_port %d in %s is not a port; using %d", s.grange_port, p, usual)
            s.grange_port = usual
        return s

    def save(self) -> None:
        write_json(self.path(), asdict(self))
