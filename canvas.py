# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""
A grid of styled characters, for the parts of the screen a widget tree cannot draw: rows
whose columns line up across the whole list, rules that cross other rules, and backgrounds
that wash over a row and fade.

`Segments` is a list of (text, style) pairs laid end to end; a style is a Rich style string.

Widths are terminal cells, as Rich and Textual count them: an emoji or a CJK character
takes two (the second cell holds "", and draws nothing), a mark that joins the letter
before it takes none. So a title with ✨ in it leaves the next column where it belongs.
"""
from __future__ import annotations

from rich.cells import cell_len, get_character_cell_size
from rich.text import Text

Segments = list[tuple[str, str]]


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


def write(canvas: Canvas, x: int, y: int, segments: Segments, width: int, style: str = "") -> int:
    """Lay segments end to end from x, cut at `width` cells with an ellipsis. `style`, when
    given, replaces every segment's own. Returns the cells used."""
    room = width
    for text, own in segments:
        if room <= 0:
            break
        text = cut(text, room)
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
