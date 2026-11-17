# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""Text from outside is stripped of what a terminal would obey before anything shows it."""
from cleantext import clean, clean_text


def test_escape_sequences_and_other_controls_are_taken_out():
    assert clean_text("Fix \x1b]52;c;aGk=\x07the parser") == "Fix ]52;c;aGk=the parser"      # no ESC, no BEL
    assert clean_text("a\x00b\x08c\x7fd\x9be\x9df") == "abcdef"                               # C0, DEL, C1
    assert clean_text("one\r\ntwo\ttabbed") == "one\ntwo\ttabbed"                             # newline and tab stay


def test_characters_that_reorder_text_are_taken_out():
    assert clean_text("user‮gnp.exe") == "usergnp.exe"                                   # an override
    assert clean_text("a⁦b⁩c d e") == "abcde"                             # isolates, separators


def test_ordinary_text_in_any_language_is_left_alone():
    for text in ("Añadir soporte", "修复解析器", "🚀 ship it", "naïve café", "tabs\tand\nlines"):
        assert clean_text(text) == text


def test_clean_reaches_every_string_in_parsed_json():
    dirty = {"ti\x1btle": "a\x1b[31mb", "labels": ["x\x07", {"name": "y\x9b"}], "number": 7, "draft": False,
             "none": None}
    assert clean(dirty) == {"title": "a[31mb", "labels": ["x", {"name": "y"}], "number": 7, "draft": False,
                            "none": None}
