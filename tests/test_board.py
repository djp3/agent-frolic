# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""The board, driven with the same stand-ins as the setup tests: what it draws from the
pull requests the app has read, and what its keys do."""
import asyncio
import json
import re
import subprocess
import time
import urllib.request
import webbrowser
from dataclasses import replace
from types import SimpleNamespace

from rich.cells import cell_len

import board
import keychain
import main
from repos import RepoStore
from settings import Settings
from githubapi import Team
from picture import Agent
from standins import AUTHTOKEN, TOKEN, FakeBrowser, FakeGitHub, FakePulls, age, drive, screen, settle
from test_pulls import node, review
from textual._xterm_parser import XTermParser
from textual.events import MouseEvent, MouseScrollDown, MouseScrollUp
from webhook import sign

TIP, OLD = "a1b2c3d" + "0" * 33, "c3d4e5f" + "0" * 33


def ask(*who):
    return {"nodes": [{"requestedReviewer": {"__typename": "User", "login": w}} for w in who]}


def approved(who):
    return {"nodes": [review(who, "APPROVED", "2026-11-12T10:00:00Z")]}


def a_board():
    """The viewer is djp3 (that is who the stand-in GitHub says the token belongs to)."""
    keychain.put(keychain.GITHUB_TOKEN, TOKEN)
    store = RepoStore()
    store.add("djp3/agent-frolic", admin=True)
    store.add("acme/web-console", private=True, admin=False)
    store.add("djp3/quiet-one", admin=True)
    FakePulls.tips = {"djp3/agent-frolic": TIP}
    FakePulls.open_ = {
        "djp3/agent-frolic": [
            node(12, "client", author={"login": "djp3"}, baseRefOid=TIP, title="GraphQL client",
                 reviewRequests=ask("bob"), updatedAt="2026-11-13T16:00:00Z"),
            node(13, "state", base="client", author={"login": "djp3"}, title="Review state",
                 reviewDecision="APPROVED", latestOpinionatedReviews=approved("bob")),
            node(9, "jira", author={"login": "mira"}, baseRefOid=OLD, title="Jira client",
                 reviewRequests=ask("djp3"), updatedAt="2026-11-11T16:00:00Z"),
            node(16, "reader", author={"login": "tobias"}, baseRefOid=OLD, title="Transcript reader",
                 mergeable="CONFLICTING", updatedAt="2026-11-10T16:00:00Z"),
        ],
        "acme/web-console": [node(143, "dark", author={"login": "mira"}, title="Dark theme tokens",
                                  reviewRequests=ask("djp3"), updatedAt="2026-11-12T16:00:00Z")],
        "djp3/quiet-one": [],
    }


def line(app, needle) -> str:
    """The screen line that holds `needle`, without the mark of the selected row."""
    return next(row for row in screen(app).splitlines() if needle in row).replace("▌", " ")


def light(ink) -> int:
    return sum(int(ink[i:i + 2], 16) for i in (1, 3, 5))


def colors(app, needle):
    """The (ink, background) pairs on the screen line that holds `needle`."""
    for strip in app.screen._compositor.render_strips():
        if needle in strip.text:
            found = set()
            for segment in strip:
                style = segment.style
                if style is None or not segment.text.strip():
                    continue
                ink = style.color.get_truecolor().hex.lower() if style.color else ""
                back = style.bgcolor.get_truecolor().hex.lower() if style.bgcolor else ""
                found.add((ink, back))
            return found
    raise AssertionError(f"{needle!r} is not on screen")


@drive
async def test_the_board_is_the_first_screen_once_there_is_a_token_and_draws_what_was_read(isolated):
    a_board()
    app = main.Frolic()
    async with app.run_test(size=(150, 46)) as pilot:
        await settle(app, pilot)
        assert app.screen is app.board
        shown = screen(app)
        top = shown.splitlines()[0]
        assert "agent-frolic ●1 ○2" in top and "web-console ●1" in top and "quiet-one ·" in top
        # the tree: repo, base, commit (the tip is named), pull requests in stack order
        order = [shown.index(text) for text in (
            "djp3/agent-frolic", "└ main", "┬ a1b2c3d tip", "#12 GraphQL client", "└ ▲ #13 Review state",
            "┼ c3d4e5f", "#9 Jira client", "#16 Transcript reader", "acme/web-console", "#143 Dark theme tokens",
            "djp3/quiet-one", "nothing is open")]
        assert order == sorted(order)
        # column 1: owner | state | waits on
        assert line(app, "#12 GraphQL").split("│")[0].split() == ["you", "waiting", "bob"]
        assert line(app, "#13 Review").split("│")[0].split() == ["you", "waiting", "#12", "→"]
        assert line(app, "#9 Jira").split("│")[0].split() == ["mira", "waiting", "●", "you"]
        assert line(app, "#16 Transcript").split("│")[0].split() == ["tobias", "to", "act"]
        assert "merge once #12 lands" in shown                           # the extra line of a queued one
        assert "◔ bob" in line(app, "#12 GraphQL") and "◔ you" in line(app, "#9 Jira")
        # the cards: you, then whoever is waited on longest
        cards = shown.splitlines()[2]
        assert cards.split("│")[0].split() == ["●", "you"]
        assert [part.strip() for part in cards.split("│")[1:]] == ["tobias", "bob"]
        # what needs effort, most urgent first, and the drawer for the selected one
        efforts = [shown.index(text) for text in ("#16 tobias · resolve the conflict", "#9  you · review",
                                                  "#143 you · review", "#12 bob · review · holds up #13")]
        assert efforts == sorted(efforts)
        assert "https://github.com/djp3/agent-frolic/pull/12" in shown and "owner    you" in shown
        # quiet is a bar under its scale, at the right edge; the drawer has the figure
        head = next(row for row in shown.splitlines() if "pull request" in row and "progress" in row)
        assert head.endswith("m h d w")
        at = head.index("m h d w")
        for number in ("#12 GraphQL", "#9 Jira", "#16 Transcript"):
            row = next(r for r in shown.splitlines() if number in r)
            assert row[at:at + 4] == "████" and set(row[at:]) <= set("█▏▎▍▌▋▊▉")    # days, at the least
        assert "last changed " in shown and " ago" in shown
        for secret in (TOKEN,):
            assert secret not in shown


def test_a_narrow_list_gives_up_progress_then_the_names_and_the_quiet_bar():
    x_review, x_ci, x_prog, names, x_quiet = board.columns(148)
    assert (x_review, x_ci, x_prog, names, x_quiet) == (90, 114, 118, True, 140)
    assert board.columns(143)[2] is not None and board.columns(142)[2] is None       # progress goes first
    x_review, x_ci, x_prog, names, x_quiet = board.columns(130)
    assert (x_review, x_ci, x_prog, names, x_quiet) == (93, 117, None, True, 122)     # the bar stays
    assert board.columns(120)[3] and not board.columns(119)[3]                        # then the names
    x_review, x_ci, x_prog, names, x_quiet = board.columns(100)
    assert (x_review, x_ci, x_prog, names, x_quiet) == (82, 91, None, False, 95)      # one block under "quiet"
    assert board.columns(87)[0] is not None and board.columns(86)[0] is None          # then the review column
    assert board.columns(86)[1:] == (77, None, False, 81)
    assert board.columns(78)[1] is not None and board.columns(77)[1] is None          # then the checks
    assert board.columns(74) == (None, None, None, False, 69)


def test_no_column_is_ever_drawn_over_the_tree():
    for width in range(70, 200):
        x_review, x_ci, x_prog, names, x_quiet = cols = board.columns(width)
        for x in (x_review, x_ci, x_prog):
            assert x is None or x >= board.X_TREE + board.TREE_ROOM, width
        assert x_quiet >= board.X_TREE + 12 and board.title_end(cols) > board.X_TREE + 10, width


@drive
async def test_in_a_narrow_window_every_row_keeps_its_number_and_the_headings_keep_apart(isolated):
    a_board()
    app = main.Frolic()
    async with app.run_test(size=(78, 46)) as pilot:
        await settle(app, pilot)
        shown = screen(app)
        for number in ("#12 ", "#13 ", "#9 ", "#16 "):
            assert any(number in row for row in shown.splitlines()), number
        head = next(row for row in shown.splitlines() if "pull request" in row and "quiet" in row)
        assert "review" not in head and head.index("pull request") < head.index("quiet")


@drive
async def test_a_narrow_window_shows_quiet_as_one_block(isolated):
    a_board()
    app = main.Frolic()
    async with app.run_test(size=(104, 46)) as pilot:
        await settle(app, pilot)
        shown = screen(app)
        head = next(row for row in shown.splitlines() if "pull request" in row and "review" in row)
        assert head.endswith("quiet") and "m h d w" not in shown
        row = next(r for r in shown.splitlines() if "#16 Transcript" in r)
        assert row[head.index("quiet"):] in set("▅▆▇█")                               # days, at the least


@drive
async def test_yellow_is_on_the_viewers_moves_only_and_dividers_are_the_divider_blue(isolated):
    a_board()
    app = main.Frolic()
    async with app.run_test(size=(150, 46)) as pilot:
        await settle(app, pilot)
        yellow, blue = board.WAITING.lower(), board.DIVIDER.lower()
        assert yellow in {ink for ink, _ in colors(app, "#9 Jira")}          # mira's waits on you
        assert yellow not in {ink for ink, _ in colors(app, "#12 GraphQL")}  # yours, but it waits on bob
        assert yellow not in {ink for ink, _ in colors(app, "#16 Transcript")}
        assert blue in {ink for ink, _ in colors(app, "└ main")} and blue in {ink for ink, _ in colors(app, "#12 GraphQL")}


@drive
async def test_the_arrows_and_n_pick_a_pull_request_and_the_drawer_follows(isolated):
    a_board()
    app = main.Frolic()
    async with app.run_test(size=(150, 46)) as pilot:
        await settle(app, pilot)
        assert app.board.sel == ("djp3/agent-frolic", 12)
        await pilot.press("down", "down")
        await pilot.pause()
        assert app.board.sel == ("djp3/agent-frolic", 9) and "https://github.com/djp3/agent-frolic/pull/9" in screen(app)
        assert "waits on ● you to review" in screen(app)
        await pilot.press("n")                                                # 9 needs effort; the next that does
        await pilot.pause()
        assert app.board.sel == ("acme/web-console", 143)                     # is in the other repository
        await pilot.press("n", "n")
        await pilot.pause()
        assert app.board.sel == ("djp3/agent-frolic", 16)                     # round to the most urgent: quiet longest
        await pilot.press("n", "n")
        await pilot.pause()
        assert app.board.sel == ("acme/web-console", 143)
        await pilot.press("d")
        await pilot.pause()
        await pilot.pause()
        assert "https://github.com/acme/web-console/pull/143" not in screen(app)           # the drawer is folded away
        await pilot.press("d")
        await pilot.pause()
        await pilot.pause()
        assert "https://github.com/acme/web-console/pull/143" in screen(app)


@drive
async def test_a_pull_request_from_a_fork_waits_on_the_maintainer_not_on_its_author(isolated):
    """The viewer sent 143 to a repository they have no rights in: GitHub offers them no
    reviewers, so asking for one is not their move."""
    a_board()
    FakePulls.open_["acme/web-console"] = [node(143, "dark", author={"login": "djp3"}, title="Dark theme tokens",
                                                authorAssociation="CONTRIBUTOR", updatedAt="2026-11-12T16:00:00Z")]
    app = main.Frolic()
    async with app.run_test(size=(150, 46)) as pilot:
        await settle(app, pilot)
        app.board.sel = ("acme/web-console", 143)
        app.draw()
        await pilot.pause()
        shown = screen(app)
        assert "waits on acme, the maintainer, to review" in shown
        assert "nobody has been asked · you cannot ask here" in shown
        row = line(app, "#143 Dark theme tokens")
        assert "you" in row and "waiting" in row and "acme" in row and "to act" not in row
        assert "#ffd75f" not in {ink for ink, _ in colors(app, "#143 Dark theme tokens")}   # not the viewer's move
        await pilot.press("s")
        await pilot.pause()
        await pilot.press("p")
        await pilot.pause()
        assert "waits on the maintainer" in line(app, "#143")


@drive
async def test_focus_dims_what_is_not_theirs_and_escape_brings_everyone_back(isolated):
    a_board()
    app = main.Frolic()
    async with app.run_test(size=(150, 46)) as pilot:
        await settle(app, pilot)
        dim = board.DIM.lower()
        assert dim not in {ink for ink, _ in colors(app, "#16 Transcript")}
        await pilot.press("f", "F")                                           # not f: on the setup it forgets
        await pilot.pause()                                                   # the token
        assert app.board.focus == board.Focus()
        await pilot.press("tab", "tab", "tab")                                # everyone → you → tobias → bob
        await pilot.pause()
        assert app.board.focus == board.Focus("crew", "bob")
        # not bob's: dimmed, but for the rule, and a quiet bar that has faded darker than that already
        other = {ink for ink, _ in colors(app, "#16 Transcript")} - {dim, board.DIVIDER.lower()}
        assert all(light(ink) < light(dim) for ink in other)
        assert dim not in {ink for ink, _ in colors(app, "#13 Review")}       # queued behind what waits on bob
        await pilot.press("shift+tab")
        await pilot.pause()
        assert app.board.focus == board.Focus("crew", "tobias")
        await pilot.press("escape")
        await pilot.pause()
        assert app.board.focus == board.Focus() and dim not in {ink for ink, _ in colors(app, "#16 Transcript")}


@drive
async def test_a_webhook_reads_the_repository_again_washes_what_changed_and_says_so(isolated):
    a_board()
    keychain.put(keychain.NGROK_AUTHTOKEN, AUTHTOKEN)
    Settings(webhooks=True, ngrok_domain="frolic.ngrok-free.dev").save()
    app = main.Frolic()
    async with app.run_test(size=(150, 46)) as pilot:
        await settle(app, pilot)
        assert app.tunnel_state == "open" and "nothing has happened yet" in screen(app)
        nodes = FakePulls.open_["djp3/agent-frolic"]                          # bob approves 12
        nodes[0] = dict(nodes[0], reviewRequests={"nodes": []}, reviewDecision="APPROVED",
                        latestOpinionatedReviews=approved("bob"))
        secret = keychain.get(keychain.WEBHOOK_SECRET)
        body = json.dumps({"action": "submitted", "repository": {"full_name": "djp3/agent-frolic"}}).encode()

        def post():
            request = urllib.request.Request(
                f"http://127.0.0.1:{app.receiver.port}/hook", data=body,
                headers={"X-GitHub-Event": "pull_request_review", "X-GitHub-Delivery": "d-9",
                         "X-Hub-Signature-256": sign(secret, body)})
            return urllib.request.urlopen(request, timeout=5).status

        assert await asyncio.to_thread(post) == 204
        await settle(app, pilot)
        app.draw()
        await pilot.pause()
        shown = screen(app)
        assert line(app, "#12 GraphQL").split("│")[0].split() == ["●", "you", "to", "act"]   # now yours: merge
        assert "✓ bob" in line(app, "#12 GraphQL")
        assert "agent-frolic · #12 bob approved · waits on you to merge" in shown            # the ticker
        assert ("djp3/agent-frolic", 12) in app.flashes and ("djp3/agent-frolic", 13) in app.flashes
        backs = {back for _, back in colors(app, "#13 Review")}
        assert backs & set(board.FLASH)                                       # the row washes for a moment
        assert not {back for _, back in colors(app, "#16 Transcript")} & set(board.FLASH)    # that one did not change


@drive
async def test_the_repository_menu_hides_and_shows_a_repository_and_saves_it(isolated):
    a_board()
    app = main.Frolic()
    async with app.run_test(size=(150, 46)) as pilot:
        await settle(app, pilot)
        await pilot.press("m")
        await pilot.pause()
        assert "repositories on screen" in screen(app) and "✓ acme/web-console" in screen(app)
        await pilot.press("down", "space")                                    # hide the second one
        await settle(app, pilot)
        assert [r.shown for r in RepoStore().repos] == [True, False, True]    # saved
        assert "· acme/web-console" in screen(app)
        await pilot.press("enter")
        await pilot.pause()
        assert "#143 Dark theme tokens" not in screen(app) and "repositories on screen" not in screen(app)
        FakePulls.asked.clear()
        await pilot.press("m", "space")                                       # the cursor is still on it: show it again
        await settle(app, pilot)
        await pilot.press("escape")
        await pilot.pause()
        assert [r.shown for r in RepoStore().repos] == [True, True, True]
        assert FakePulls.asked == ["acme/web-console"] and "#143 Dark theme tokens" in screen(app)   # and it is read


@drive
async def test_s_opens_the_setup_and_s_or_escape_returns(isolated):
    a_board()
    app = main.Frolic()
    async with app.run_test(size=(150, 46)) as pilot:
        await settle(app, pilot)
        await pilot.press("s")
        await pilot.pause()
        assert app.screen is app.setup and "1  GitHub token" in screen(app) and "signed in as djp3" in screen(app)
        await pilot.press("escape")
        await pilot.pause()
        assert app.screen is app.board and "#12 GraphQL client" in screen(app)
        await pilot.press("s")
        await pilot.pause()
        await pilot.press("s")
        await pilot.pause()
        assert app.screen is app.board


@drive
async def test_without_a_token_it_starts_on_the_setup_and_the_board_says_what_is_missing(isolated):
    app = main.Frolic()
    async with app.run_test(size=(150, 46)) as pilot:
        await settle(app, pilot)
        assert app.screen is app.setup and "none yet" in screen(app)
        await pilot.press("escape")
        await pilot.pause()
        assert app.screen is app.board
        assert "No GitHub token yet. s opens the setup, where t pastes one." in screen(app)
        assert "no repositories yet" in screen(app).splitlines()[0]


@drive
async def test_the_other_empty_states_say_what_to_do(isolated):
    keychain.put(keychain.GITHUB_TOKEN, TOKEN)
    app = main.Frolic()
    async with app.run_test(size=(150, 46)) as pilot:
        await settle(app, pilot)
        assert "No repositories yet. s opens the setup, where a adds one." in screen(app)
        app.store.add("djp3/agent-frolic", admin=True)
        app.store.add("djp3/broken", admin=True)
        FakePulls.broken["djp3/broken"] = "GitHub's rate limit is used up for now. Try again in a few minutes."
        await pilot.press("u")
        await settle(app, pilot)
        shown = screen(app)
        assert "nothing is open" in shown                                     # read, and empty
        assert "✗ could not be read: GitHub's rate limit is used up for now." in shown
        assert "Nothing is open." in shown                                    # the needs-effort pane
        app.store.show("djp3/agent-frolic", False)
        app.store.show("djp3/broken", False)
        app.draw()
        await pilot.pause()
        assert "No repository is on screen. m shows one." in screen(app)


@drive
async def test_the_ticker_says_hours_and_days_not_hundreds_of_minutes(isolated):
    a_board()
    app = main.Frolic()
    async with app.run_test(size=(150, 46)) as pilot:
        await settle(app, pilot)
        for back, said in ((1, "now"), (12, "12s ago"), (40 * 60, "40m ago"), (5 * 3600, "5h ago"),
                           (3 * 86400, "3d ago")):
            app.news = (time.time() - back, "something happened")
            assert app.board.ticker().plain.split("something")[0].strip() == said


def test_a_repositorys_counts_are_drawn_one_way():
    assert board.count_marks(3, 2) == [(" ●", board.WAITING), ("3", board.INK), (" ○", board.MUTED), ("2", board.MUTED)]
    assert board.count_marks(3, 0, gap="  ") == [("  ●", board.WAITING), ("3", board.INK)]
    assert board.count_marks(0, 2) == [(" ○", board.MUTED), ("2", board.MUTED)]
    assert board.count_marks(0, 0) == []                                  # the caller says what nothing is


def marks(text):
    """Each ●n and ○n in a line, with the style of its mark and of its number."""
    def style_at(i):
        return [str(span.style) for span in text.spans if span.start <= i < span.end][-1]
    return [(m.group(0), style_at(m.start(1)), style_at(m.start(2)))
            for m in re.finditer(r"([●○])(\d+)", text.plain)]


@drive
async def test_the_top_line_and_the_menu_show_the_same_counts_the_same_way(isolated):
    a_board()
    app = main.Frolic()
    async with app.run_test(size=(150, 46)) as pilot:
        await settle(app, pilot)
        top = marks(app.board.top_line())
        menu = marks(app.board.menu_text())
        assert top == menu and [m for m, _, _ in top] == ["●1", "○2", "●1"]


@drive
async def test_the_picture_is_built_again_only_when_what_it_is_drawn_from_changes(isolated, monkeypatch):
    """The board is drawn five times a second; a new reading comes every few minutes."""
    a_board()
    app = main.Frolic()
    async with app.run_test(size=(150, 46)) as pilot:
        await settle(app, pilot)
        b = app.board

        def drawn_again() -> bool:
            before = b.pic
            b.draw()
            return b.pic is not before

        assert not any(drawn_again() for _ in range(5))                     # nothing new: the same picture
        got = app.pulls["djp3/agent-frolic"]
        app.pulls["djp3/agent-frolic"] = replace(got)                       # a new reading, even of the same
        assert drawn_again() and not drawn_again()
        app.store.show("djp3/quiet-one", False)
        assert drawn_again()
        app.pull_trouble["acme/web-console"] = "GitHub's rate limit is used up for now."
        assert drawn_again()
        later = time.time() + 60                                            # how long things have been quiet
        monkeypatch.setattr(board, "time", SimpleNamespace(time=lambda: later, monotonic=time.monotonic))
        assert drawn_again() and not drawn_again()


def test_a_change_is_remembered_for_a_minute_and_then_let_go():
    marks = {"long ago": 100.0, "a moment ago": 150.0}
    board.remember_change(marks, "now", 200.0)
    assert marks == {"a moment ago": 150.0, "now": 200.0}                  # 100 s old: gone; 50 s: kept


@drive
async def test_what_changed_long_ago_is_let_go_as_new_changes_come(isolated):
    """The board stays open for days: the records of what just changed must not grow forever."""
    a_board()
    app = main.Frolic()
    async with app.run_test(size=(150, 46)) as pilot:
        await settle(app, pilot)
        long_ago = ("djp3/agent-frolic", 99)
        app.flashes[long_ago] = time.time() - 120
        app.board.entered[long_ago] = time.monotonic() - 120
        nodes = FakePulls.open_["djp3/agent-frolic"]                          # bob approves 12
        nodes[0] = dict(nodes[0], reviewRequests={"nodes": []}, reviewDecision="APPROVED",
                        latestOpinionatedReviews=approved("bob"))
        nodes.append(node(20, "icons", author={"login": "mira"}, reviewRequests=ask("djp3")))   # and one more
        await pilot.press("u")
        await settle(app, pilot)
        assert ("djp3/agent-frolic", 12) in app.flashes and long_ago not in app.flashes
        assert ("djp3/agent-frolic", 20) in app.board.entered and long_ago not in app.board.entered
        assert app.board.shade(app.board.flashed(("djp3/agent-frolic", 12))) is not None   # and it washes


# ---- a list longer than its box ---------------------------------------------------------

def a_long_board():
    """Two repositories with more open than the list's box holds, then one with nothing open."""
    keychain.put(keychain.GITHUB_TOKEN, TOKEN)
    store = RepoStore()
    store.add("djp3/agent-frolic", admin=True)
    store.add("acme/web-console", private=True, admin=False)
    store.add("djp3/quiet-one", admin=True)
    FakePulls.open_ = {
        "djp3/agent-frolic": [node(n, f"a{n}", author={"login": "mira"}, title=f"First change {n}")
                              for n in range(1, 25)],
        "acme/web-console": [node(n, f"b{n}", author={"login": "tobias"}, title=f"Second change {n}")
                             for n in range(101, 121)],
        "djp3/quiet-one": [],
    }


def bar(app) -> str:
    """The scroll bar, top to bottom: the window's last column beside the list, read by
    screen cell (a wide character to its left takes two cells but is one in a string)."""
    box = app.board.query_one("#scroll").region
    strips = app.screen._compositor.render_strips()
    return "".join(strips[y].crop(box.x, box.x + 1).text for y in range(box.y, box.y + box.height))


async def wheel(pilot, event, times=1, over="#map", offset=(60, 4)):
    """Turn the wheel with the pointer over a widget, as the terminal reports it: the event
    says where on the screen the pointer is and names no widget (Textual names one on a
    click only), and it goes to the app, which finds what is under the pointer."""
    widget = pilot.app.screen.query_one(over)
    x, y = widget.region.x + offset[0], widget.region.y + offset[1]
    for _ in range(times):
        pilot.app.post_message(event(None, x, y, 0, 1 if event is MouseScrollDown else -1, 0, False, False, False,
                                     screen_x=x, screen_y=y))
        await pilot.pause()


def test_the_hints_leave_out_the_least_needed_first_and_keep_setup_and_quit():
    full = board.HINT_GAP.join(board.HINTS)
    assert board.hint_line(cell_len(full)) == full
    assert board.hint_line(116) == ("↑↓ pull request   o open on GitHub   PgUp PgDn scroll   "
                                    "n next needing effort   tab focus   s setup   q quit")
    last = "s setup   q quit"
    for width in range(cell_len(last), cell_len(full) + 1):
        line = board.hint_line(width)
        shown = line.split(board.HINT_GAP)
        assert cell_len(line) <= width and line.endswith(last), width
        assert shown == [h for h in board.HINTS if h in shown], width              # in their order


@drive
async def test_a_narrow_window_still_shows_how_to_reach_the_setup_and_quit(isolated):
    a_board()
    app = main.Frolic()
    async with app.run_test(size=(120, 46)) as pilot:
        await settle(app, pilot)
        hints = screen(app).splitlines()[-1]
        assert "s setup   q quit" in hints and "…" not in hints
        assert "d drawer" not in hints                                             # the least needed went first
        await pilot.resize_terminal(170, 46)                                       # widened: all of them
        await settle(app, pilot)
        assert board.HINT_GAP.join(board.HINTS) in screen(app).splitlines()[-1]


@drive
async def test_textuals_scroll_bar_styles_do_not_reach_the_lists_bar(isolated):
    """Textual's styles pick widgets by class name, and its own scroll bar is a ScrollBar:
    a query by that name, as its styles make, must not find the list's."""
    a_long_board()
    app = main.Frolic()
    async with app.run_test(size=(150, 34)) as pilot:
        await settle(app, pilot)
        ours = app.board.query_one("#scroll")
        assert type(ours).__name__ == "ListBar" and ours not in app.board.query("ScrollBar")


@drive
async def test_the_bar_is_read_by_screen_cell_beside_wide_titles(isolated):
    """An emoji in a title takes two cells: read by string index, the bar's column would land
    on the list."""
    a_long_board()
    for n in FakePulls.open_["djp3/agent-frolic"]:
        n["title"] = "✨ " + n["title"]
    app = main.Frolic()
    async with app.run_test(size=(150, 34)) as pilot:
        await settle(app, pilot)
        shown = bar(app)
        assert len(shown) == app.board.page and set(shown) <= {board.THUMB, board.TRACK_LINE}
        assert shown[0] == board.THUMB and shown[-1] == board.TRACK_LINE


def test_the_thumb_touches_an_end_of_its_track_only_when_the_list_is_at_that_end():
    assert board.thumb(30, 0, 100) == (0, 9) and board.thumb(30, 70, 100) == (21, 9)
    assert board.thumb(30, 1, 100) == (1, 9) and board.thumb(30, 69, 100) == (20, 9)      # a line out of sight shows
    assert board.thumb(30, 35, 100) == (10, 9)
    assert board.thumb(30, 0, 31) == (0, 29) and board.thumb(30, 1, 31) == (1, 29)
    assert board.thumb(30, 0, 3000) == (0, 1) and board.thumb(30, 2970, 3000) == (29, 1)  # never thinner than a cell
    assert board.thumb(30, 0, 20) == (0, 30)                                              # it all fits


def test_in_a_small_box_the_thumb_still_has_a_middle():
    """A thumb one cell shorter than its track has only the two ends to stand at; once the
    list can be scrolled to somewhere between, it needs a middle as well."""
    for rows, top, total in ((3, 1, 5), (3, 1, 6), (4, 1, 6), (5, 1, 7), (3, 2, 6)):
        start, size = board.thumb(rows, top, total)
        assert 0 < start and start + size < rows, (rows, top, total)
    for rows in range(3, 41):                                   # the rule, everywhere
        for total in range(rows + 1, 201):
            for top in range(total - rows + 1):
                start, size = board.thumb(rows, top, total)
                assert (start == 0) == (top == 0) and (start + size == rows) == (top == total - rows), (
                    rows, top, total)


def test_the_thumbs_size_is_the_same_wherever_the_list_stands():
    """Dragging needs only the size; it does not depend on where the list is scrolled to."""
    for rows in range(1, 41):
        for total in range(1, 201):
            assert {board.thumb(rows, top, total)[1] for top in range(max(1, total - rows + 1))} == {
                board.thumb_size(rows, total)}, (rows, total)


@drive
async def test_the_wheel_scrolls_the_list_and_the_selection_stays_where_it_was(isolated):
    a_long_board()
    app = main.Frolic()
    async with app.run_test(size=(150, 34)) as pilot:
        await settle(app, pilot)
        deck = app.board
        first, last, total = deck.where
        assert (first, total) == (1, 52) and last < total and "djp3/quiet-one  " not in screen(app)
        assert f"rows 1–{last} of 52" in screen(app)
        assert bar(app)[0] == board.THUMB and bar(app)[-1] == board.TRACK_LINE     # at the top, more below
        chosen = deck.sel
        await wheel(pilot, MouseScrollDown, 3)
        step = round(app.scroll_sensitivity_y)
        assert deck.top == 3 * step and deck.sel == chosen                         # the selection did not move
        assert f"rows {3 * step + 1}–" in screen(app) and bar(app)[0] == board.TRACK_LINE
        await pilot.pause(0.6)                                                     # later draws leave it there,
        assert deck.top == 3 * step                                                # though the selection is out of sight
        assert "#1 First change 1 " not in screen(app).split("needs effort")[0]
        await wheel(pilot, MouseScrollUp, 1)
        assert deck.top == 2 * step
        await pilot.press("down")                                                  # picking a pull request brings
        await pilot.pause()                                                        # the list back to it
        assert deck.sel != chosen and not deck.roam and deck.top < 2 * step
        assert f"#{deck.sel[1]} First change {deck.sel[1]} " in screen(app).split("needs effort")[0]


@drive
async def test_a_list_scrolled_by_hand_stays_put_when_the_selected_one_goes(isolated):
    """Only the user picking a pull request brings the list back to the selection: the board
    choosing another, because the selected one was merged, does not."""
    a_long_board()
    app = main.Frolic()
    async with app.run_test(size=(150, 34)) as pilot:
        await settle(app, pilot)
        deck = app.board
        gone = deck.sel
        await wheel(pilot, MouseScrollDown, 3)
        top = deck.top
        assert deck.roam and top > 0
        FakePulls.open_["djp3/agent-frolic"] = [n for n in FakePulls.open_["djp3/agent-frolic"]
                                                if n["number"] != gone[1]]       # merged
        await pilot.press("u")
        await settle(app, pilot)
        assert deck.sel != gone and deck.sel == deck.keys()[0]                   # another is selected,
        assert deck.roam and deck.top == top                                     # and the list stayed put
        await pilot.press("n")                                                   # picking one brings it back
        await pilot.pause()
        assert not deck.roam and deck.top < top


@drive
async def test_scrolling_draws_the_list_again_and_leaves_the_rest_of_the_board(isolated, monkeypatch):
    """A trackpad sends dozens of wheel ticks a second: each draws the list, its bar and its
    header again, not the cards, the needs-effort pane and the drawer."""
    a_long_board()
    app = main.Frolic()
    async with app.run_test(size=(150, 34)) as pilot:
        await settle(app, pilot)
        deck = app.board
        drawn = {"map": 0, "scroll_bar": 0, "drawer": 0, "effort": 0, "strip": 0}
        for name in drawn:
            def counted(*args, _name=name, _draw=getattr(deck, name)):
                drawn[_name] += 1
                return _draw(*args)
            monkeypatch.setattr(deck, name, counted)
        await wheel(pilot, MouseScrollDown, 5)
        # the board's own steady redraw (four times a second) draws everything; the ticks
        # must have drawn the list five times more than that
        assert drawn["map"] - drawn["drawer"] >= 5 and drawn["scroll_bar"] == drawn["map"]
        assert drawn["drawer"] == drawn["effort"] == drawn["strip"]
        assert deck.top > 0 and f"rows {deck.top + 1}–" in screen(app)         # and it did scroll


@drive
async def test_the_bytes_a_terminal_sends_for_a_turn_of_the_wheel_scroll_the_list(isolated):
    """What the Mac app's terminal writes when two fingers move on the trackpad: an SGR
    mouse report, button 65 for down and 64 for up, at the pointer's cell (counted from 1).
    Textual makes of it an event that names no widget: the board has to tell from the
    cell alone that the pointer is over the list."""
    def turned(report):
        found = [event for event in XTermParser().feed(report) if isinstance(event, MouseEvent)]
        assert len(found) == 1 and found[0].widget is None
        app.post_message(found[0])

    a_long_board()
    app = main.Frolic()
    async with app.run_test(size=(150, 34)) as pilot:
        await settle(app, pilot)
        deck, step = app.board, round(app.scroll_sensitivity_y)
        box = deck.query_one("#map").region
        x, y = box.x + 60 + 1, box.y + 4 + 1
        turned(f"\x1b[<65;{x};{y}M")
        await pilot.pause()
        assert deck.top == step and deck.roam
        turned(f"\x1b[<65;{x};{y}M")
        turned(f"\x1b[<64;{x};{y}M")
        await pilot.pause()
        assert deck.top == step
        turned(f"\x1b[<65;{x};2M")                                       # over the cards: not the list's
        await pilot.pause()
        assert deck.top == step


@drive
async def test_the_wheel_scrolls_the_list_only_over_the_list(isolated):
    """Not over the cards, the needs-effort pane or the drawer, where a hand resting on the
    trackpad would quietly scroll the list away from the selection."""
    a_long_board()
    app = main.Frolic()
    async with app.run_test(size=(150, 34)) as pilot:
        await settle(app, pilot)
        deck = app.board
        for over in ("#effort", "#strip", "#drawer"):
            await wheel(pilot, MouseScrollDown, 2, over=over, offset=(2, 0))
            assert (deck.top, deck.roam) == (0, False), over
        await wheel(pilot, MouseScrollDown, 1, over="#scroll", offset=(0, 3))     # over its bar it does
        assert deck.top > 0 and deck.roam


@drive
async def test_the_page_keys_and_end_reach_a_repository_with_nothing_open(isolated):
    a_long_board()
    app = main.Frolic()
    async with app.run_test(size=(150, 34)) as pilot:
        await settle(app, pilot)
        deck = app.board
        rows = deck.page
        await pilot.press("pagedown")
        await pilot.pause()
        assert deck.top == rows - 2                                               # two lines of the last page stay
        await pilot.press("end")
        await pilot.pause()
        shown = screen(app)
        assert deck.where == (52 - rows + 1, 52, 52) and "rows" in shown
        assert "djp3/quiet-one" in shown.split("needs effort")[0] and "nothing is open" in shown
        assert bar(app)[-1] == board.THUMB and bar(app)[0] == board.TRACK_LINE     # at the bottom, more above
        await pilot.press("pageup")
        await pilot.pause()
        assert deck.top == 52 - rows - (rows - 2)
        await pilot.press("home")
        await pilot.pause()
        assert deck.top == 0 and bar(app)[0] == board.THUMB
        await pilot.press("m", "pagedown", "end")                                 # with the menu open the list stays
        await pilot.pause()
        assert deck.top == 0


@drive
async def test_the_last_pull_request_brings_what_follows_it_into_view(isolated):
    a_long_board()
    app = main.Frolic()
    async with app.run_test(size=(150, 34)) as pilot:
        await settle(app, pilot)
        deck = app.board
        await pilot.press(*["down"] * (len(deck.keys()) - 1))
        await pilot.pause()
        assert deck.sel == deck.keys()[-1] and not deck.roam
        above = screen(app).split("needs effort")[0]
        assert f"#{deck.sel[1]} Second change" in above                           # the selected one shows,
        assert "djp3/quiet-one" in above and "nothing is open" in above           # and so does the repository after it
        assert deck.where[1] == deck.where[2]


@drive
async def test_dragging_the_scroll_bar_moves_the_list(isolated):
    a_long_board()
    app = main.Frolic()
    async with app.run_test(size=(150, 34)) as pilot:
        await settle(app, pilot)
        deck = app.board
        rows = deck.page
        await pilot.mouse_down("#scroll", offset=(0, rows - 1))                    # a press at the track's foot
        await pilot.pause()
        assert deck.top == 52 - rows and "djp3/quiet-one" in screen(app).split("needs effort")[0]
        await pilot.hover("#scroll", offset=(0, rows // 2))                        # dragged to the middle
        await pilot.pause()
        assert 0 < deck.top < 52 - rows
        await pilot.hover("#scroll", offset=(0, 0))                                # and to the head
        await pilot.pause()
        assert deck.top == 0
        await pilot.mouse_up("#scroll", offset=(0, 0))
        await pilot.hover("#scroll", offset=(0, rows - 1))                         # let go: the pointer passing over
        await pilot.pause()                                                        # it moves nothing
        assert deck.top == 0


@drive
async def test_in_the_smallest_window_the_list_counts_the_rows_it_really_has(isolated):
    """The Mac app's smallest window leaves the list one row: End, the bar and the selection
    all have to work in that one row."""
    a_long_board()
    app = main.Frolic()
    async with app.run_test(size=(150, 18)) as pilot:
        await settle(app, pilot)
        deck = app.board
        assert deck.page == deck.query_one("#map").size.height == 1

        def listed() -> str:
            return screen(app).split("needs effort")[0]

        for _ in range(3):                                                         # the selected one stays in sight
            await pilot.press("down")
            await pilot.pause()
            assert f"#{deck.sel[1]} First change {deck.sel[1]}" in listed()
        await pilot.press("end")
        await pilot.pause()
        assert deck.where == (52, 52, 52) and "nothing is open" in listed()      # the very last line
        assert bar(app) == board.THUMB                                             # one cell, the thumb in it


@drive
async def test_the_thumb_is_dragged_from_where_it_was_taken_hold_of(isolated):
    a_long_board()
    app = main.Frolic()
    async with app.run_test(size=(150, 34)) as pilot:
        await settle(app, pilot)
        deck = app.board
        await pilot.press("pagedown")
        await pilot.pause()
        top = deck.top
        start, size = board.thumb(deck.page, top, deck.where[2])
        assert size >= 3                                                           # a thumb with a top and a middle
        for cell in (start, start + size // 2):                                    # taking hold of it moves nothing
            await pilot.mouse_down("#scroll", offset=(0, cell))
            await pilot.pause()
            assert deck.top == top, cell
            await pilot.mouse_up("#scroll", offset=(0, cell))
        await pilot.mouse_down("#scroll", offset=(0, start))
        await pilot.hover("#scroll", offset=(0, start + 1))                        # one cell down: the list follows
        await pilot.pause()
        assert deck.top > top and board.thumb(deck.page, deck.top, deck.where[2])[0] == start + 1
        await pilot.mouse_up("#scroll", offset=(0, start + 1))


@drive
async def test_only_the_main_button_takes_hold_of_the_scroll_bar(isolated):
    a_long_board()
    app = main.Frolic()
    async with app.run_test(size=(150, 34)) as pilot:
        await settle(app, pilot)
        deck = app.board
        foot = deck.page - 1
        for button in (2, 3):                                                      # middle, right
            await pilot.mouse_down("#scroll", offset=(0, foot), button=button)
            await pilot.pause()
            assert (deck.top, deck.roam, app.mouse_captured) == (0, False, None), button
            await pilot.mouse_up("#scroll", offset=(0, foot))
        await pilot.mouse_down("#scroll", offset=(0, foot))                       # the main one does
        await pilot.pause()
        assert deck.top > 0 and app.mouse_captured is deck.query_one("#scroll")
        await pilot.mouse_up("#scroll", offset=(0, foot))


@drive
async def test_dragging_the_scroll_bar_selects_no_text(isolated):
    """A drag of the bar is a drag of the bar: nothing on it, nor on the list beside it if the
    pointer drifts left, is left highlighted as a text selection."""
    a_long_board()
    app = main.Frolic()
    async with app.run_test(size=(150, 34)) as pilot:
        await settle(app, pilot)
        deck = app.board
        rows = deck.page
        await pilot.mouse_down("#scroll", offset=(0, 2))
        await pilot.hover("#scroll", offset=(0, rows - 3))                         # down the bar,
        await pilot.hover("#map", offset=(60, rows - 3))                           # then off it onto the list
        await pilot.mouse_up("#map", offset=(60, rows - 3))
        await pilot.pause()
        assert deck.top > 0                                                        # it scrolled,
        assert app.screen.selections == {}                                         # and selected nothing


@drive
async def test_a_list_that_fits_has_no_scroll_bar_and_nowhere_to_scroll(isolated):
    a_board()
    app = main.Frolic()
    async with app.run_test(size=(150, 46)) as pilot:
        await settle(app, pilot)
        deck = app.board
        assert not bar(app).strip() and "rows " not in screen(app)
        await wheel(pilot, MouseScrollDown, 2)
        await pilot.press("pagedown", "end")
        await pilot.pause()
        assert deck.top == 0 and not deck.roam


@drive
async def test_o_opens_the_selected_pull_requests_page_and_tab_steps_the_focus(isolated):
    a_board()
    app = main.Frolic()
    async with app.run_test(size=(150, 46)) as pilot:
        await settle(app, pilot)
        app.OPEN_AGAIN_S = 0                                                  # (a key held down has its own test)
        assert "o open on GitHub" in screen(app) and "tab focus" in screen(app)
        await pilot.press("o")
        await settle(app, pilot)
        assert FakeBrowser.opened == ["https://github.com/djp3/agent-frolic/pull/12"]   # the selected one, and
                                                                              # only it: built from its repository
                                                                              # and number, not the address sent
        assert "agent-frolic#12 · opening its page in the browser" in screen(app)      # the ticker says so
        assert app.board.focus == board.Focus()                               # o no longer steps the focus
        await pilot.press("down", "down", "O")                                # O does what o does
        await settle(app, pilot)
        assert FakeBrowser.opened == ["https://github.com/djp3/agent-frolic/pull/12",
                                      "https://github.com/djp3/agent-frolic/pull/9"]
        assert "https://github.com/djp3/agent-frolic/pull/9" in screen(app)   # the drawer shows what o opens
        await pilot.press("m", "o", "O")                                      # not from under the menu
        await settle(app, pilot)
        assert len(FakeBrowser.opened) == 2
        await pilot.press("escape")
        FakeBrowser.broken = OSError("no such program")                       # the browser does not start
        await pilot.press("o")
        await settle(app, pilot)
        assert "agent-frolic#9 · its page was not opened: the browser could not be started" in screen(app)
        FakeBrowser.broken = main.NotOnGitHub("it is not a page on github.com")   # an address that is refused
        await pilot.press("o")
        await settle(app, pilot)
        assert "its page was not opened: it is not a page on github.com" in screen(app)
        FakeBrowser.broken = ValueError("embedded null byte")                 # Python's words about its own insides
        await pilot.press("o")                                                # are not shown as the reason
        await settle(app, pilot)
        assert "embedded null byte" not in screen(app)
        assert "its page was not opened: the browser could not be started" in screen(app)


@drive
async def test_a_word_about_o_takes_the_tickers_line_for_a_moment_and_gives_it_back(isolated, monkeypatch):
    """The ticker is the board's one place for the last thing that happened on GitHub: o
    must not push that off the board for good."""
    a_board()
    app = main.Frolic()
    async with app.run_test(size=(150, 46)) as pilot:
        await settle(app, pilot)
        now = [time.time()]
        monkeypatch.setattr(main, "time", SimpleNamespace(time=lambda: now[0], monotonic=time.monotonic,
                                                          localtime=time.localtime, strftime=time.strftime))
        event = "2m ago   agent-frolic · #13 approved by bob"
        app.happened(now[0] - 120, "agent-frolic · #13 approved by bob")

        def ticker() -> str:
            return app.board.ticker().plain

        assert ticker() == event
        await pilot.press("o")
        await settle(app, pilot)
        assert ticker() == "agent-frolic#12 · opening its page in the browser"
        assert "agent-frolic#12 · opening its page in the browser" in screen(app).splitlines()[-2]
        now[0] += app.NOTICE_S + 0.1                                          # the moment is over:
        assert ticker() == event                                              # the event is back, as it was

        FakeBrowser.broken = OSError("no such program")                       # a failure stays up longer,
        await pilot.press("o")
        await settle(app, pilot)
        failed = "agent-frolic#12 · its page was not opened: the browser could not be started"
        now[0] += app.NOTICE_S + 0.1
        assert ticker() == failed
        assert any(text == failed for _, text, _ in app.lines)                # and is kept in the setup's log
        now[0] += app.NOTICE_FAILED_S
        assert ticker() == event

        FakeBrowser.broken = None
        await pilot.press("o")
        await settle(app, pilot)
        app.happened(now[0], "agent-frolic · #12 merged")                     # something new on GitHub
        assert ticker() == "now      agent-frolic · #12 merged"               # shows at once


@drive
async def test_holding_o_down_opens_the_page_once(isolated, monkeypatch):
    """A held key repeats some thirty times a second, and each repeat used to open a tab."""
    a_board()
    app = main.Frolic()
    async with app.run_test(size=(150, 46)) as pilot:
        await settle(app, pilot)
        now = [time.time()]
        monkeypatch.setattr(main, "time", SimpleNamespace(time=lambda: now[0], monotonic=time.monotonic,
                                                          localtime=time.localtime, strftime=time.strftime))
        for _ in range(90):                                                   # held for three seconds
            await pilot.press("o")
            now[0] += 0.033
        await settle(app, pilot)
        assert FakeBrowser.opened == ["https://github.com/djp3/agent-frolic/pull/12"]
        now[0] += app.OPEN_AGAIN_S + 0.1                                      # let go, then asked for again
        await pilot.press("o")
        await settle(app, pilot)
        assert len(FakeBrowser.opened) == 2
        await pilot.press("down", "down", "o")                                # another one: at once
        await settle(app, pilot)
        assert FakeBrowser.opened[2:] == ["https://github.com/djp3/agent-frolic/pull/9"]
        FakeBrowser.broken = OSError("no such program")                       # one that failed: again at once
        now[0] += app.OPEN_AGAIN_S + 0.1
        await pilot.press("o")
        await settle(app, pilot)
        FakeBrowser.broken = None
        await pilot.press("o")
        await settle(app, pilot)
        assert FakeBrowser.opened[3:] == ["https://github.com/djp3/agent-frolic/pull/9"]


@drive
async def test_a_page_that_cannot_be_opened_never_closes_the_board(isolated):
    """A worker's uncaught error ends the app: whatever goes wrong starting the browser is
    said on the board instead."""
    a_board()
    app = main.Frolic()
    async with app.run_test(size=(150, 46)) as pilot:
        await settle(app, pilot)
        for trouble in (RuntimeError("something nobody expected"), webbrowser.Error("no browser"),
                        subprocess.TimeoutExpired("open", 10)):
            FakeBrowser.broken = trouble
            await pilot.press("o")
            await settle(app, pilot)
            assert app.is_running and app.return_code is None, trouble
            assert "agent-frolic#12 · its page was not opened: the browser could not be started" in screen(app)


@drive
async def test_o_does_nothing_when_no_pull_request_is_selected(isolated):
    keychain.put(keychain.GITHUB_TOKEN, TOKEN)
    app = main.Frolic()
    async with app.run_test(size=(150, 46)) as pilot:
        await settle(app, pilot)
        assert app.screen is app.board and app.board.sel is None
        await pilot.press("o", "O")
        await settle(app, pilot)
        assert FakeBrowser.opened == [] and app.news is None and app.notice is None


# ---- a review asked of a team -----------------------------------------------------------

def ask_team(*slugs):
    return {"nodes": [{"requestedReviewer": {"__typename": "Team", "slug": slug}} for slug in slugs]}


def a_team_board():
    """Acme's repository, where three pull requests wait on a review: one asked of the team
    Platform, which the viewer (djp3) is on, one of a team they are not on, one of bob."""
    keychain.put(keychain.GITHUB_TOKEN, TOKEN)
    RepoStore().add("acme/web-console", private=True, admin=False)
    FakeGitHub.teams_listed = [Team("Acme", "platform", "Platform")]
    FakePulls.open_ = {"acme/web-console": [
        node(201, "bump-a", author={"login": "dependabot"}, title="Bump a", reviewRequests=ask_team("platform"),
             updatedAt="2026-11-12T16:00:00Z"),
        node(202, "bump-b", author={"login": "dependabot"}, title="Bump b", reviewRequests=ask_team("docs"),
             updatedAt="2026-11-13T16:00:00Z"),
        node(203, "theme", author={"login": "mira"}, title="Dark theme", reviewRequests=ask("bob"),
             updatedAt="2026-11-14T10:00:00Z")]}


def who(app, needle):
    """Column 1 of the row that holds `needle`: owner, state, waits on."""
    return line(app, needle).split("│")[0].split()


@drive
async def test_a_request_to_a_team_the_viewer_is_on_is_drawn_as_theirs_and_the_team_has_a_card(isolated):
    a_team_board()
    app = main.Frolic()
    async with app.run_test(size=(150, 46)) as pilot:
        await settle(app, pilot)
        yellow = board.WAITING.lower()
        assert who(app, "#201 Bump a") == ["dependabot", "waiting", "●", "Platform"]      # as "● you" is drawn
        assert yellow in {ink for ink, _ in colors(app, "#201 Bump a")}
        assert who(app, "#202 Bump b") == ["dependabot", "waiting", "team/docs"]          # not their team: as before
        assert yellow not in {ink for ink, _ in colors(app, "#202 Bump b")}
        assert [m for m, _, _ in marks(app.board.top_line())] == ["●1", "○2"]             # theirs; team/docs, bob
        cards = line(app, "Platform")                                                     # the strip is the first
        assert cards.index("you") < cards.index("● Platform") < cards.index("team/docs") < cards.index("bob")
        assert yellow in {ink for ink, _ in colors(app, "team/docs")}                     # the dot on the team's card
        assert app.board.sel == ("acme/web-console", 201)
        assert "waits on ● Platform to review" in screen(app)                             # the drawer
        assert line(app, "Platform · review").split()[0] == "●"                           # what needs effort
        assert line(app, "team/docs · review").split()[0] == "○"
        await pilot.press("tab", "tab")                                                   # → you → Platform
        await pilot.pause()
        assert app.board.focus == board.Focus("crew", "team/acme/platform")
        dim = board.DIM.lower()
        assert dim not in {ink for ink, _ in colors(app, "#201 Bump a")}
        assert dim in {ink for ink, _ in colors(app, "#203 Dark theme")}
        await pilot.press("escape")
        app.agents = {("acme/web-console", 201): [Agent("bob", "01", "acme/web-console", 201, "review",
                                                        "at its prompt")]}
        app.draw()
        await pilot.pause()
        assert "● Platform › ▷ agent 01 at its prompt" in screen(app)                     # a teammate's agent


@drive
async def test_teams_that_arrive_after_the_board_is_drawn_build_the_picture_again_and_settle_without_a_wash(isolated):
    """Until the viewer's teams are known a team's request is nobody's: no dot, in neither
    count, no card, no mark where it needs effort, not a stop of n. Then it settles."""
    a_team_board()
    app = main.Frolic()
    async with app.run_test(size=(150, 46)) as pilot:
        await settle(app, pilot)
        app.teams, app.teams_run = None, app.token_run            # as while GitHub is still being asked
        app.draw()
        await pilot.pause()
        assert app.teams_held and app.board.pic.held
        assert who(app, "#201 Bump a") == ["dependabot", "waiting", "team/platform"]
        assert who(app, "#202 Bump b") == ["dependabot", "waiting", "team/docs"]
        assert board.WAITING.lower() not in {ink for ink, _ in colors(app, "#201 Bump a")}
        assert [m for m, _, _ in marks(app.board.top_line())] == ["○1"]                   # bob alone
        assert "team/" not in line(app, "bob") and "Platform" not in line(app, "bob")     # the strip: no team's card
        assert line(app, "team/platform · review").split()[0] not in ("●", "○")           # no mark, either way
        assert line(app, "bob · review").split()[0] == "○"
        nodes = FakePulls.open_["acme/web-console"]                                       # 203 asks a team as well
        nodes[2] = dict(nodes[2], reviewRequests={"nodes": ask("bob")["nodes"] + ask_team("platform")["nodes"]})
        app.update_pulls(["acme/web-console"], "asked")
        await settle(app, pilot)
        app.draw()
        await pilot.pause()
        assert line(app, "bob, team/platform · review").split()[0] not in ("●", "○")      # held whole: not "○" now
        assert [m for m, _, _ in marks(app.board.top_line())] == ["○1"]                   # and "●" a moment later
        app.flashes.clear()
        app.news = None
        nodes[2] = dict(nodes[2], reviewRequests=ask("bob"))                              # (as it was)
        app.update_pulls(["acme/web-console"], "asked")
        await settle(app, pilot)
        app.flashes.clear()
        app.news = None
        app.draw()
        await pilot.pause()
        await pilot.press("n", "n")
        await pilot.pause()
        assert app.board.sel == ("acme/web-console", 203)                                 # the only stop there is
        held, entered, washed = app.board.pic, dict(app.board.entered), dict(app.board.card_flash)
        app.teams_answer(app.token_run, app.teams_for, app.teams_known, [Team("Acme", "platform", "Platform")])
        await pilot.pause()
        assert app.board.pic is not held and not app.board.pic.held                       # built again
        assert who(app, "#201 Bump a") == ["dependabot", "waiting", "●", "Platform"]
        assert [m for m, _, _ in marks(app.board.top_line())] == ["●1", "○2"]
        assert not app.flashes and app.news is None                                       # no row washed, no line said
        assert app.board.entered == entered and app.board.card_flash == washed            # nor a card, nor an entry
        same = app.board.pic
        app.draw()
        assert app.board.pic is same                                                      # and then it stands


# ---- a click picks a pull request ---------------------------------------------------------

async def click(pilot, over, offset, button=0):
    """A click with the pointer over a widget, as the terminal reports it: the button down
    and up again at one cell, in two SGR mouse reports (0 is the main button, 2 the other
    one). Textual makes of them a press and a release that name no widget, and of those
    two, the click."""
    widget = pilot.app.screen.query_one(over)
    x, y = widget.region.x + offset[0] + 1, widget.region.y + offset[1] + 1
    for report in (f"\x1b[<{button};{x};{y}M", f"\x1b[<{button};{x};{y}m"):
        for event in XTermParser().feed(report):
            if isinstance(event, MouseEvent):
                assert event.widget is None
                pilot.app.post_message(event)
    await pilot.pause()
    await pilot.pause()


def row_of(deck, number, part=0):
    """The row of the list's box on which a line of a pull request is drawn."""
    for i, drawn in enumerate(deck.lines()):
        if drawn.row.pull is not None and drawn.row.pull.number == number and drawn.part == part:
            return i - deck.top
    raise AssertionError(f"#{number} has no line {part}")


@drive
async def test_a_click_on_any_line_of_a_pull_request_picks_it(isolated):
    a_board()
    app = main.Frolic()
    async with app.run_test(size=(150, 46)) as pilot:
        await settle(app, pilot)
        deck = app.board
        assert deck.sel == ("djp3/agent-frolic", 12)
        await click(pilot, "#map", (70, row_of(deck, 9)))                     # on its title
        assert deck.sel == ("djp3/agent-frolic", 9)
        assert "https://github.com/djp3/agent-frolic/pull/9" in screen(app)   # and the drawer follows
        await click(pilot, "#map", (5, row_of(deck, 13, part=1)))             # on a later line of one, in column 1
        assert deck.sel == ("djp3/agent-frolic", 13)
        await click(pilot, "#map", (70, row_of(deck, 143)))                   # one of another repository
        assert deck.sel == ("acme/web-console", 143)

        rule = next(i for i, drawn in enumerate(deck.lines()) if drawn.row.pull is None) - deck.top
        await click(pilot, "#map", (70, rule))                                # a rule is no pull request,
        await click(pilot, "#map", (70, len(deck.lines()) - deck.top + 1))    # nor is the space below the list;
        await click(pilot, "#map", (70, row_of(deck, 9)), button=2)           # the other button picks nothing,
        await click(pilot, "#strip", (3, 0))                                  # nor does a click on a card
        assert deck.sel == ("acme/web-console", 143)
        await pilot.press("m")
        await pilot.pause()
        await click(pilot, "#map", (70, row_of(deck, 9)))                     # nor one under the menu
        assert deck.sel == ("acme/web-console", 143)
        await pilot.press("escape")
        await pilot.press("up")                                               # the arrows go on from it
        await pilot.pause()
        order = deck.keys()
        assert deck.sel == order[order.index(("acme/web-console", 143)) - 1]


@drive
async def test_a_click_in_the_list_leaves_the_list_where_it_is(isolated):
    """The arrows keep two lines in view above the one they pick. A click picks what is
    under the pointer: if the list moved to make that room, something else would be."""
    a_long_board()
    app = main.Frolic()
    async with app.run_test(size=(150, 34)) as pilot:
        await settle(app, pilot)
        deck = app.board
        await wheel(pilot, MouseScrollDown, 3)
        top = deck.top
        first = next(drawn.row.pull.number for drawn in deck.lines()[top:] if drawn.row.pull is not None)
        assert top > 0 and row_of(deck, first) <= 1                           # at the very top of the box
        await click(pilot, "#map", (70, row_of(deck, first)))
        assert deck.sel[1] == first and deck.top == top and deck.roam         # picked, and nothing moved
        assert line(app, f"#{first} First change {first} ") != line(app, f"#{first + 1} First change")
        await pilot.press("down")                                             # a key picks: the list follows again
        await pilot.pause()
        assert deck.sel[1] == first + 1 and not deck.roam


@drive
async def test_a_click_where_it_needs_effort_picks_that_pull_request_and_the_list_goes_to_it(isolated):
    a_long_board()
    app = main.Frolic()
    async with app.run_test(size=(150, 34)) as pilot:
        await settle(app, pilot)
        deck = app.board
        listed = list(deck.effort_rows)
        assert len(listed) >= 3 and len(deck.efforts) > len(listed)           # more need effort than fit
        await pilot.press("end")                                              # the list is at its far end,
        await pilot.pause()
        far = listed[2]
        assert deck.roam and not 0 <= row_of(deck, far[1]) < deck.page        # and this one out of view
        await click(pilot, "#effort", (8, 2 + listed.index(far)))             # (the pane's edge, then its heading)
        assert deck.sel == far and not deck.roam
        assert 0 <= row_of(deck, far[1]) < deck.page                          # the list went to it
        await click(pilot, "#effort", (8, 1))                                 # the heading picks nothing,
        await click(pilot, "#effort", (8, 2 + len(listed)))                   # nor does "and N more"
        await click(pilot, "#drawer", (8, 3))                                 # nor the drawer
        assert deck.sel == far


# ---- each repository's two bars -----------------------------------------------------------

def test_the_two_bars_are_at_most_three_runs_of_like_cells():
    """From the left: where neither bar reaches, where only the longer does, where both do."""
    track, read, hook = board.TRACK, board.POLL_INK, board.HOOK
    full, half, quarter = board.BAR, board.BAR // 2, board.BAR // 4
    assert board.fresh_bar(1, 1) == [("▀" * full, f"{read} on {hook}")]
    assert board.fresh_bar(0, 0) == [("▀" * full, f"{track} on {track}")]
    assert board.fresh_bar(1, 0) == [("▀" * full, f"{read} on {track}")]
    assert board.fresh_bar(0.5, 0.75) == [("▀" * quarter, f"{track} on {track}"),
                                          ("▀" * quarter, f"{track} on {hook}"),
                                          ("▀" * half, f"{read} on {hook}")]
    assert board.fresh_bar(0.75, 0.5) == [("▀" * quarter, f"{track} on {track}"),
                                          ("▀" * quarter, f"{read} on {track}"),
                                          ("▀" * half, f"{read} on {hook}")]
    for read_left, hook_left in ((0.01, 0), (0.3, 0.9), (2, -1)):         # always BAR cells, whatever it is given
        assert sum(len(text) for text, _ in board.fresh_bar(read_left, hook_left)) == full


def bars(app, needle):
    """The two thin bars on the screen line that holds `needle`: how many cells of the
    upper one are lit, how many of the lower one, and how many cells there are."""
    for strip in app.screen._compositor.render_strips():
        if needle not in strip.text:
            continue
        upper = lower = cells = 0
        for segment in strip:
            n = segment.text.count("▀")
            if not n or segment.style is None:
                continue
            ink = segment.style.color.get_truecolor().hex.lower() if segment.style.color else ""
            back = segment.style.bgcolor.get_truecolor().hex.lower() if segment.style.bgcolor else ""
            cells += n
            upper += n if ink == board.POLL_INK.lower() else 0
            lower += n if back == board.HOOK.lower() else 0
        return upper, lower, cells
    raise AssertionError(f"no line with {needle!r}")


@drive
async def test_each_repositorys_line_ends_in_two_bars_of_its_own(isolated):
    """The upper one empties until that repository is next read on its timer, the lower one
    since GitHub last sent a delivery about it. They used to be one pair, in the top line."""
    a_board()
    keychain.put(keychain.NGROK_AUTHTOKEN, AUTHTOKEN)
    Settings(webhooks=True, ngrok_domain="frolic.ngrok-free.dev").save()
    app = main.Frolic()
    async with app.run_test(size=(150, 46)) as pilot:
        await settle(app, pilot)
        app.draw()
        await pilot.pause()
        ours, theirs, quiet = "╪ djp3/agent-frolic", "╪ acme/web-console", "╪ djp3/quiet-one"
        assert bars(app, ours) == (board.BAR, board.BAR, board.BAR)          # just read, and GitHub tells us
        assert bars(app, theirs) == (board.BAR, 0, board.BAR)                # not ours to hook: no lower bar
        assert bars(app, quiet) == (board.BAR, board.BAR, board.BAR)         # one with nothing open has them too
        top = screen(app).splitlines()[0]
        assert "▀▀" not in top and "▀ read  ▄ webhook" in top                # the top line keeps only their key

        app.hooked["djp3/agent-frolic"] -= 3600                              # its hook was confirmed long ago,
        span = app.period(app.store.get("djp3/agent-frolic"))                # so it is on the slow timer
        assert span == 30 * 60
        app.read_at["djp3/agent-frolic"] -= span / 2
        app.hook_at["djp3/agent-frolic"] = time.time() - span / 4
        app.draw()
        await pilot.pause()
        assert bars(app, ours) == (board.BAR // 2, board.BAR * 3 // 4, board.BAR)    # this one's have drained
        assert bars(app, theirs) == (board.BAR, 0, board.BAR)                        # that one's have not
        age(app, only=["acme/web-console"])                                  # its five minutes are up: it is
        app.tick()                                                           # read, and its bar is full again
        await settle(app, pilot)
        app.draw()
        await pilot.pause()
        assert bars(app, theirs) == (board.BAR, 0, board.BAR) and bars(app, ours)[0] == board.BAR // 2

        app.hooked["djp3/quiet-one"] -= 3600                                 # another's hook, an hour old with
        app.draw()                                                           # nothing heard since: its lower
        await pilot.pause()                                                  # bar has run out,
        assert bars(app, quiet)[1] == 0
        app.hook_done("djp3/quiet-one", 9, "webhook #9 registered", app.webhooks_run)    # and is full again when the
        await pilot.pause()                                                  # hook is confirmed anew, however
        assert bars(app, quiet)[1] == board.BAR                              # long the tunnel has been open


@drive
async def test_in_a_narrow_window_a_repositorys_name_and_counts_come_before_its_bars(isolated):
    a_board()
    app = main.Frolic()
    async with app.run_test(size=(100, 40)) as pilot:
        await settle(app, pilot)
        app.draw()
        await pilot.pause()
        assert bars(app, "╪ djp3/agent-frolic")[2] == 0                      # no room: they are left out,
        assert "djp3/agent-frolic  ●1  ○2  4 open" in line(app, "╪ djp3/agent-frolic")     # not drawn over it
        assert bars(app, "╪ djp3/quiet-one")[2] == board.BAR                 # a shorter line has room for them
