# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Text from outside, made safe to put on a terminal.

Everything agent-frolic shows about a pull request was typed by someone on GitHub: a
title, a branch name, a review. The screen is a terminal, and a terminal obeys escape
sequences: a title holding ESC ] 52 would travel through Textual untouched and rewrite the
clipboard. So control characters are taken out where outside text comes in (GitHub's
replies in githubapi.py, webhook deliveries in webhook.py), and nothing further in has to
think about it.

Taken out: the C0 controls except newline and tab, DEL, the C1 controls, the line and
paragraph separators, and the characters that reorder text on screen (bidirectional
overrides and isolates), which can make a name read as another name.
"""
from __future__ import annotations

import re
from typing import Any

_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f\x80-\x9f  ‪-‮⁦-⁩]")


def clean_text(text: str) -> str:
    return _CONTROL.sub("", text)


def clean(data: Any) -> Any:
    """The same data with every string in it cleaned, keys included. For parsed JSON."""
    if isinstance(data, str):
        return clean_text(data)
    if isinstance(data, list):
        return [clean(item) for item in data]
    if isinstance(data, dict):
        return {clean(key): clean(value) for key, value in data.items()}
    return data
