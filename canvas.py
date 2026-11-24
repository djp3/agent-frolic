# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""
A grid of styled characters, for the parts of the screen a widget tree cannot draw: rows
whose columns line up across the whole list, rules that cross other rules, and backgrounds
that wash over a row and fade.

`Segments` is a list of (text, style) pairs laid end to end; a style is a Rich style string.
A style may be a `Lit` one: words a light passes through, when `write` is told where the
light stands. That is how something still at work is shown to be: see `lit`.

Widths are terminal cells, as Rich and Textual count them: an emoji or a CJK character
takes two (the second cell holds "", and draws nothing), a mark that joins the letter
before it takes none. So a title with ✨ in it leaves the next column where it belongs.
"""
from __future__ import annotations

from rich.cells import cell_len, get_character_cell_size
from rich.text import Text

Segments = list[tuple[str, str]]
LIGHT_EVERY = 20                 # cells from one light to the next along a line
LIGHT_REACH = 1.8                # cells either side of where a light stands that take its color
LIGHT_HEART = 0.8                # and, of those, the ones it makes bold


class Lit(str):
    """The style of words a light passes through: the style they have at rest, which is
    all it is wherever nobody asks for the light, and `shine`, the color the light gives
    them as it passes (`lit`)."""
    shine: str

    def __new__(cls, style: str, shine: str) -> "Lit":
        made = super().__new__(cls, style)
        made.shine = shine
        return made


def lit(text: str, style: Lit, x: int, light: float) -> Segments:
    """`text`, which starts at cell `x`, as the light finds it. `light` is a cell a light
    stands on, and there is another every LIGHT_EVERY cells either way, so as `light`
    grows they run through the line from left to right, one after another. A cell within
    LIGHT_REACH of one has its color, and at its heart is bold; the rest keep the style
    they have at rest. Words that are bold at rest stay bold in the light."""
    rest = str(style)
    near = f"bold {style.shine}" if "bold" in rest.split() else style.shine
    out: Segments = []
    for ch in text:
        far = abs((x - light + LIGHT_EVERY / 2) % LIGHT_EVERY - LIGHT_EVERY / 2)
        ink = f"bold {style.shine}" if far < LIGHT_HEART else near if far < LIGHT_REACH else rest
        if out and out[-1][1] == ink:
            out[-1] = (out[-1][0] + ch, ink)
        else:
            out.append((ch, ink))
        x += get_character_cell_size(ch)
    return out


class Canvas:
    def __init__(self, width: int, height: int) -> None:
        self.width, self.height = width, height
        self.cells: list[list[tuple[str, str]]] = [[(" ", "")] * width for _ in range(height)]

    def put(self, x: int, y: int, text: str, style: str = "") -> None:
        if not 0 <= y < self.height:
            return
        row = self.cells[y]
        for ch in text:
            size = get_character_cell_size(ch)
            if size == 0:                                  # joins the letter before it
                at = x - 1
                if 0 <= at < self.width and row[at][0] == "":
                    at -= 1                                # that letter is wide: its first half
                if 0 <= at < self.width and row[at][0]:
                    row[at] = (row[at][0] + ch, row[at][1])
                continue
            if 0 <= x < self.width:
                if size == 2 and x + 1 >= self.width:     # half of it would fall off the edge
                    self._set(row, x, " ", style)
                else:
                    self._set(row, x, ch, style)
                    if size == 2:
                        self._set(row, x + 1, "", style)
            x += size

    def _set(self, row: list[tuple[str, str]], x: int, ch: str, style: str) -> None:
        """One cell. Writing over either half of a wide character clears its other half."""
        old = row[x][0]
        if old == "" and ch != "" and x > 0:
            row[x - 1] = (" ", row[x - 1][1])
        if old and get_character_cell_size(old[0]) == 2 and x + 1 < self.width and row[x + 1][0] == "":
            row[x + 1] = (" ", row[x + 1][1])
        row[x] = (ch, style)

    def text(self) -> Text:
        out = Text(no_wrap=True)
        for y, line in enumerate(self.cells):
            run, run_style = "", line[0][1] if line else ""
            for ch, style in line:
                if style != run_style:
                    out.append(run, style=run_style)
                    run, run_style = "", style
                run += ch
            out.append(run.rstrip() if not run_style else run, style=run_style)
            if y < self.height - 1:
                out.append("\n")
        return out


def write(canvas: Canvas, x: int, y: int, segments: Segments, width: int, style: str = "",
          light: float | None = None) -> int:
    """Lay segments end to end from x, cut at `width` cells with an ellipsis. `style`, when
    given, replaces every segment's own. `light`, when given, is where the light stands
    now: the `Lit` segments are drawn as it finds them (`lit`), after they are cut, so
    what is cut does not change as the light moves. Returns the cells used."""
    room = width
    for text, own in segments:
        if room <= 0:
            break
        text = cut(text, room)
        if light is not None and not style and isinstance(own, Lit):
            at = x
            for piece, ink in lit(text, own, x, light):
                canvas.put(at, y, piece, ink)
                at += cell_len(piece)
        else:
            canvas.put(x, y, text, style or own)
        x += cell_len(text)
        room -= cell_len(text)
    return width - room


def wash(canvas: Canvas, y: int, x0: int, x1: int, color: str) -> None:
    """A background behind part of a row, replacing any background already there."""
    if not 0 <= y < canvas.height:
        return
    for x in range(max(0, x0), min(canvas.width, x1)):
        ch, style = canvas.cells[y][x]
        canvas.cells[y][x] = (ch, f"{style.split(' on ')[0]} on {color}".strip())


def cut(text: str, width: int) -> str:
    """The text in at most `width` cells, ending in "…" when some had to go."""
    if cell_len(text) <= width:
        return text
    out, used = "", 0
    for ch in text:
        size = get_character_cell_size(ch)
        if used + size > width - 1:
            break
        out, used = out + ch, used + size
    return out + "…" if width >= 1 else ""


def seg_len(segments: Segments) -> int:
    return sum(cell_len(text) for text, _ in segments)


def plain(segments: Segments) -> str:
    return "".join(text for text, _ in segments)
