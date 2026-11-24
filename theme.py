# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""
The colors, in one place. They are the ones the chosen mock-up (mockup/optionx.py) settled on.

  • text wears the ink tokens, lightest to faintest: INK, INK2, MUTED;
  • the four status colors go on state marks only, never on words;
  • GOOD is also the wheel that turns before an agent at work, as agent-farm has a busy
    session green. The wheel of one that waits on a command it started is INK;
  • a light passes through the words of an agent whose wheel turns (canvas.lit), and it
    is the one color that goes on words, for as long as it is passing: LIGHT_AT_WORK, a
    lighter GOOD, picked by eye to read as light on the near-black behind it, where
    GOOD itself read as a shadow crossing the white of a name; and LIGHT_ON_COMMAND, a
    white past INK, which is the lightest that words are at rest;
  • WAITING (agent farm's golden yellow) means one thing: idle, and you can fix it;
  • DIVIDER (agent farm's divider blue) is every rule and line, and nothing else;
  • AGE is the filled part of an age's bar, and nothing else. A repository's timer has the
    same bar in INK2, so the two are told apart: time since something, and time until it.
    It was measured, not picked by eye: the gentlest violet that stays apart from INK2 for
    full and for color-blind vision, and from the status colors, the yellow and the blue.
  • KEY is the key in a hint at the foot of a screen ("q" in "q Quit"), and nothing else.
    agent-farm's footer has its keys in orange, and that orange is, to the eye, WARN: the
    amber of a review asked for and a check still running. So the key has a color of its
    own here, measured as AGE was: a cyan that stays apart from the status colors, the
    yellow and the violet for full and for color-blind vision. Beside its words (INK2) a
    key is told by being bold as well, since to some eyes cyan and grey are close.
"""
from __future__ import annotations

PLANE, SURFACE, RAISED = "#0d0d0d", "#1a1a19", "#262624"          # backgrounds, back to front
INK, INK2, MUTED, AXIS = "#f0efec", "#c3c2b7", "#898781", "#383835"
GOOD, WARN, SERIOUS, CRIT = "#0ca30c", "#fab219", "#ec835a", "#d03b3b"
LIGHT_AT_WORK, LIGHT_ON_COMMAND = "#33cc33", "#ffffff"
WAITING = "#ffd75f"
DIVIDER = "#0053AA"
AGE = "#9f90e6"
KEY = "#45d3dd"


def _parts(color: str) -> tuple[int, int, int]:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def darker(one: str, other: str) -> str:
    """Whichever of two colors gives less light."""
    return min(one, other, key=lambda color: sum(_parts(color)))
