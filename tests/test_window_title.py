# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""The terminal's title (the Mac app's title bar, iTerm2's tab) reads the rooster, the name
and the version, then a hammer for each repository being watched. Textual never sets a
terminal title, so the app writes OSC 0 itself: on mount, when the count changes, and
cleared as it quits."""
from textual.drivers.headless_driver import HeadlessDriver

import apppaths
import keychain
import main
from repos import RepoStore
from standins import TOKEN, drive, settle, to_setup


def test_title_sequence_is_osc_zero():
    assert main.title_sequence("🐓 agent-frolic") == "\x1b]0;🐓 agent-frolic\x07"
    assert main.title_sequence("") == "\x1b]0;\x07"


def test_the_title_carries_the_version():
    v = apppaths.app_version()
    assert v                                   # a checkout has a VERSION file
    assert main.WINDOW_TITLE == f"🐓 agent-frolic v{v}"


def test_a_hammer_for_each_repository_watched(monkeypatch):
    monkeypatch.setattr(main, "WINDOW_TITLE", "🐓 agent-frolic v0.9.1")
    assert main.window_title(0) == "🐓 agent-frolic v0.9.1"
    assert main.window_title(1) == "🐓 agent-frolic v0.9.1 🔨"
    assert main.window_title(3) == "🐓 agent-frolic v0.9.1 🔨🔨🔨"


def titles(monkeypatch) -> list[str]:
    """What the app writes as the terminal's title. Textual writes other escapes too (OSC 22
    cursor shapes, say); only title sequences are kept."""
    writes: list[str] = []
    monkeypatch.setattr(HeadlessDriver, "write",
                        lambda self, data: writes.append(data) if data.startswith("\x1b]0;") else None)
    return writes


@drive
async def test_the_title_counts_the_shown_repositories_and_is_cleared_on_quit(isolated, monkeypatch):
    writes = titles(monkeypatch)
    keychain.put(keychain.GITHUB_TOKEN, TOKEN)
    store = RepoStore()
    for name in ("djp3/agent-frolic", "Acme/ledger-api", "Acme/web-console"):
        store.add(name)
    store.show("Acme/web-console", False)                         # kept, but not watched
    app = main.Frolic()
    async with app.run_test(size=(150, 40)) as pilot:
        await settle(app, pilot)
        assert writes[0] == main.title_sequence(main.WINDOW_TITLE)   # before GitHub has said yes to the token
        assert writes[-1] == main.title_sequence(f"{main.WINDOW_TITLE} 🔨🔨")
        seen = len(writes)
        await pilot.pause(1.5)                                     # ticks redraw, the count is the same:
        assert len(writes) == seen                                 # no title is written again
        await to_setup(app, pilot)
        await pilot.press("space")                                 # hide the first one
        await settle(app, pilot)
        assert writes[-1] == main.title_sequence(f"{main.WINDOW_TITLE} 🔨")
        await pilot.press("q")
    assert writes[-1] == main.title_sequence("")                   # the Mac app's own title comes back


@drive
async def test_without_a_token_nothing_is_watched(isolated, monkeypatch):
    writes = titles(monkeypatch)
    RepoStore().add("djp3/agent-frolic")
    app = main.Frolic()
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(app, pilot)
        await pilot.pause(1.2)                                     # a tick or so
        assert writes == [main.title_sequence(main.WINDOW_TITLE)]
        await pilot.press("q")
