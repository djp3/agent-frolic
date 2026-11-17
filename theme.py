# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""
The colors, in one place. They are the ones the chosen mock-up (mockup/optionx.py) settled on.

  • text wears the ink tokens, lightest to faintest: INK, INK2, MUTED;
  • the four status colors go on state marks only, never on words;
  • WAITING (agent farm's golden yellow) means one thing: idle, and you can fix it;
  • DIVIDER (agent farm's divider blue) is every rule and line, and nothing else;
  • UNITS are the quiet bar's minutes, hours, days and weeks, and nothing else. They were
    measured, not picked by eye: each pair stays apart for full and for color-blind
    vision, and each stays apart from the status colors, the yellow and the blue.
"""
from __future__ import annotations

PLANE, SURFACE, RAISED = "#0d0d0d", "#1a1a19", "#262624"          # backgrounds, back to front
INK, INK2, MUTED, AXIS = "#f0efec", "#c3c2b7", "#898781", "#383835"
GOOD, WARN, SERIOUS, CRIT = "#0ca30c", "#fab219", "#ec835a", "#d03b3b"
WAITING = "#ffd75f"
DIVIDER = "#0053AA"
UNITS = ("#19a9b0", "#0b6eff", "#9584e5", "#bf2fb9")              # minutes, hours, days, weeks


def _parts(color: str) -> tuple[int, int, int]:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def blend(start: str, end: str, part: float) -> str:
    """The color that is `part` of the way from one to the other, 0 to 1."""
    part = min(1.0, max(0.0, part))
    return "#" + "".join(f"{round(a + (b - a) * part):02x}" for a, b in zip(_parts(start), _parts(end)))


def darker(one: str, other: str) -> str:
    """Whichever of two colors gives less light."""
    return min(one, other, key=lambda color: sum(_parts(color)))
