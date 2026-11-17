# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""The character grid counts terminal cells, as the terminal does: an emoji or a CJK
character takes two, a mark that joins the letter before it takes none."""
from rich.cells import cell_len

import keychain
import main
from canvas import Canvas, cut, seg_len, write
from repos import RepoStore
from standins import TOKEN, FakePulls, drive, screen, settle
from test_pulls import node, review


def row(canvas, y=0) -> str:
    return canvas.text().plain.split("\n")[y]


def test_a_wide_character_takes_two_cells_and_the_next_column_stays_where_it_belongs():
    for title in ("xx Add board", "✨ Add board", "中文 Add board"[:4] + "rd"):
        c = Canvas(30, 1)
        c.put(0, 0, title)
        c.put(20, 0, "|")
        line = row(c)
        assert cell_len(line[:line.index("|")]) == 20, title        # the column, on screen
        assert cell_len(line) == 21


def test_text_is_cut_to_its_room_in_cells():
    assert cut("中文标题很长", 7) == "中文标…" and cell_len(cut("中文标题很长", 7)) == 7
    assert cut("✨ Add board", 12) == "✨ Add board" and cut("✨ Add board", 11) == "✨ Add boa…"
    assert cut("abc", 0) == "" and cut("abc", 1) == "…"
    assert seg_len([("✨ ", ""), ("ok", "")]) == 5
    c = Canvas(10, 1)
    assert write(c, 0, 0, [("中文", ""), ("标题很长", "")], 7) == 7
    assert cell_len(row(c).rstrip()) == 7


def test_writing_over_half_of_a_wide_character_leaves_no_half_behind():
    c = Canvas(6, 1)
    c.put(0, 0, "中中中")
    c.put(1, 0, "a")                                             # over the second half of the first
    c.put(4, 0, "b")                                             # over the first half of the third
    assert row(c) == " a中b" and cell_len(row(c)) == 5
    edge = Canvas(3, 1)
    edge.put(2, 0, "中")                                          # half of it would fall off
    assert cell_len(row(edge)) <= 3 and "中" not in row(edge)


def test_a_mark_that_joins_the_letter_before_it_takes_no_cell():
    c = Canvas(10, 1)
    c.put(0, 0, "Café")                                    # "Café", the accent as a mark of its own
    c.put(5, 0, "|")
    line = row(c)
    assert "é" in line and cell_len(line[:line.index("|")]) == 5


def approved(who):
    return {"nodes": [review(who, "APPROVED", "2026-11-12T10:00:00Z")]}


@drive
async def test_on_the_board_an_emoji_title_does_not_push_the_review_column_out_of_line(isolated):
    keychain.put(keychain.GITHUB_TOKEN, TOKEN)
    RepoStore().add("djp3/agent-frolic", admin=True)
    FakePulls.open_ = {"djp3/agent-frolic": [
        node(1, "a", title="xx Add board", reviewDecision="APPROVED", latestOpinionatedReviews=approved("bob")),
        node(2, "b", title="✨ Add board", reviewDecision="APPROVED", latestOpinionatedReviews=approved("bob"))]}
    app = main.Frolic()
    async with app.run_test(size=(170, 40)) as pilot:
        await settle(app, pilot)
        rows = [line.replace("▌", " ") for line in screen(app).splitlines() if "Add board" in line and "bob" in line]
        assert len(rows) == 2                                        # the two rows of the map
        assert len({cell_len(line[:line.index("bob")]) for line in rows}) == 1
        assert len({cell_len(line) for line in rows}) == 1
