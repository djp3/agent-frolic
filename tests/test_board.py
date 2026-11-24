# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""The board, driven with the same stand-ins as the setup tests: what it draws from the
pull requests the app has read, and what its keys do."""
import asyncio
import json
import random
import re
import subprocess
import threading
import time
import urllib.request
import webbrowser
from dataclasses import replace
from types import SimpleNamespace

from rich.cells import cell_len

import board
import keychain
import main
import theme
from repos import RepoStore
from settings import Settings
from githubapi import Team
from grange import Reported
from standins import (AUTHTOKEN, TOKEN, FakeBrowser, FakeClipboard, FakeGitHub, FakePulls, age, drive, screen,
                      settle, until)
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
    async with app.run_test(size=(160, 46)) as pilot:
        await settle(app, pilot)
        assert app.screen is app.board
        shown = screen(app)
        top = shown.splitlines()[0]
        assert "agent-frolic ●1 ○2" in top and "web-console ●1" in top and "quiet-one ·" in top
        # the tree: repo, base, commit (the tip is named), pull requests in stack order
        order = [shown.index(text) for text in (
            "djp3/agent-frolic", "└ main", "┬ a1b2c3d tip", "#12 GraphQL client", "└ ▲ #13 Review state",
            "┼ c3d4e5f", "#9 Jira client", "#16 Transcript reader", "acme/web-console", "#143 Dark theme tokens",
            "djp3/quiet-one  0 open")]
        assert order == sorted(order)
        assert "nothing is open" not in shown             # that line says it: there is no other under it
        # column 1: owner | state | waits on
        assert line(app, "#12 GraphQL").split("│")[0].split() == ["you", "waiting", "bob"]
        assert line(app, "#13 Review").split("│")[0].split() == ["you", "waiting", "#12", "→"]
        assert line(app, "#9 Jira").split("│")[0].split() == ["mira", "waiting", "●", "you"]
        assert line(app, "#16 Transcript").split("│")[0].split() == ["tobias", "to", "act"]
        assert "merge once #12 lands" in shown                           # the extra line of a queued one
        assert "◔ bob" in line(app, "#12 GraphQL") and "◔ you" in line(app, "#9 Jira")
        # the cards: you, then whoever holds up something of yours. tobias's #16 is his own to fix: no card
        cards = shown.splitlines()[2]
        assert cards.split("│")[0].split() == ["●", "you"]
        assert [part.strip() for part in cards.split("│")[1:]] == ["bob"]
        # what needs effort, most urgent first, and the drawer for the selected one
        efforts = [shown.index(text) for text in ("#16 tobias · resolve the conflict", "#9  you · review",
                                                  "#143 you · review", "#12 bob · review · holds up #13")]
        assert efforts == sorted(efforts)
        assert "https://github.com/djp3/agent-frolic/pull/12" in shown and "owner    you" in shown
        # the age is a bar and a figure in days and hours, at the right edge under "age"; the drawer
        # says how long too
        head = next(row for row in shown.splitlines() if "pull request" in row and "progress" in row)
        assert head.endswith(" age")
        for number in ("#12 GraphQL", "#9 Jira", "#16 Transcript"):
            row = next(r for r in shown.splitlines() if number in r)
            bar = re.fullmatch(r".* (━+─*) +\d+d \d\dh", row).group(1)              # days, at the least
            assert len(bar) == board.AGE_BAR and len(row) == len(head)               # the figure ends where "age" does
        assert "last changed " in shown and " ago" in shown
        for secret in (TOKEN,):
            assert secret not in shown


def test_a_narrow_list_gives_up_progress_first_then_the_ages_bar_then_its_figure_then_the_names():
    x_review, x_ci, x_prog, names, x_quiet = board.columns(160)
    assert (x_review, x_ci, x_prog, names, x_quiet) == (88, 112, 116, True, 138)
    assert board.columns(157)[2] is not None                                          # progress goes first,
    assert board.columns(156) == (105, 129, None, True, 156 - board.QUIET)            # and the age keeps its bar
    assert board.columns(134)[4] == 134 - board.QUIET                                 # then the age's bar: its
    assert board.columns(133) == (95, 119, None, True, 133 - (board.AGE_FIGURE + 1))  # figure has the column
    assert board.columns(121)[4] == 121 - (board.AGE_FIGURE + 1)                      # then its figure: the
    assert board.columns(120) == (91, 115, None, True, 120)                           # column has no room at all
    assert board.columns(112)[3] and not board.columns(111)[3]                        # then the names
    x_review, x_ci, x_prog, names, x_quiet = board.columns(100)
    assert (x_review, x_ci, x_prog, names, x_quiet) == (87, 96, None, False, 100)
    assert board.columns(82)[0] is not None and board.columns(81)[0] is None          # then the review column
    assert board.columns(81)[1:] == (77, None, False, 81)
    assert board.columns(73)[1] is not None and board.columns(72)[1] is None          # then the checks
    assert board.columns(72) == (None, None, None, False, 72)


def test_each_layout_gives_up_one_thing_more_than_the_one_before_it():
    """The order a narrowing window gives things up in is the order of the layouts: progress,
    the age's bar, its figure, the reviewers' names, the review column, the checks."""
    held = [(layout.progress, layout.age >= board.QUIET, layout.age > 0, layout.names, layout.review, layout.checks)
            for layout in board.LAYOUTS]
    assert held[0] == (True,) * 6 and held[-1] == (False,) * 6
    gone = []
    for before, after in zip(held, held[1:]):
        assert all(now <= was for was, now in zip(before, after))                     # nothing comes back,
        gone.append([i for i, (was, now) in enumerate(zip(before, after)) if was and not now])
    assert gone == [[0], [1], [2], [3], [4], [5]]                                     # and one thing goes at a time
    for wide in range(60, 220):                                                       # the fullest that fits
        chosen = next(layout for layout in board.LAYOUTS if layout.at(wide) == board.columns(wide))
        fuller = board.LAYOUTS[:board.LAYOUTS.index(chosen)]
        assert all(board.title_end(layout.at(wide)) - board.X_TREE < layout.tree for layout in fuller), wide


def test_progress_fits_its_column_and_leaves_a_cell_before_the_age():
    """The age's bar starts where the progress column ends: what ran on was drawn over, and
    "10/12" read "10/1"."""
    def said(word, done, total):
        return "".join(text for text, _ in board.progress_cells(word, done, total))

    assert said("stalled", 1, 3) == "stalled ▰▱▱ 1/3" and said("in progress", 2, 3) == "in progress ▰▰▱ 2/3"
    assert said("resolved", 8, 8) == "resolved ▰▰▰▰▰ 8/8"                              # five blocks at the most
    assert said("in progress", 9, 12) == "in progress ▰▰▰▱ 9/12"                      # the longest word and a count
    assert said("in progress", 10, 12) == "in progress ▰▰▱ 10/12"                     # of two figures: fewer blocks
    for done, total in ((9, 12), (10, 12), (99, 99), (100, 120)):
        assert len(said("in progress", done, total)) <= board.PROGRESS - 1, (done, total)
    assert said("not started", 0, 0) == "not started" and said("ready to merge", 0, 0) == "ready to merge"
    x_review, x_ci, x_prog, names, x_quiet = board.columns(160)
    assert x_quiet - x_prog == board.PROGRESS                                         # that is the column it has


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
        head = next(row for row in shown.splitlines() if "pull request" in row)
        assert "review" not in head and " age" not in head and head.endswith(" ci")
        row = next(r for r in shown.splitlines() if "#16 " in r)
        assert not re.search(r"\d+[dh]$", row) and "━" not in row              # no age: it went after progress


@drive
async def test_however_narrow_the_window_a_repositorys_line_says_how_many_are_open(isolated):
    """For a repository with nothing open that count is the only place the board says so:
    there is no line beneath it. So the count stays, and what gives way is the rest."""
    a_board()
    long = "acme-platform/regional-ledger-reconciliation"
    RepoStore().add(long, admin=True)
    FakePulls.open_[long] = []
    app = main.Frolic()
    async with app.run_test(size=(150, 46)) as pilot:
        await settle(app, pilot)
        assert f"{long}  0 open" in screen(app)                                    # room for all of it
        assert "djp3/agent-frolic  ●1  ○2  4 open" in screen(app)

        await pilot.resize_terminal(100, 46)
        await pilot.pause()
        row = line(app, "acme-platform/")
        assert "…  0 open" in row and long not in row                              # the name is cut, the count kept
        assert "acme-platform/regional-ledger" in row
        assert "djp3/agent-frolic  4 open ═" in screen(app)                        # ●n ○n go before its timer does

        await pilot.resize_terminal(82, 46)
        await pilot.pause()
        assert "djp3/agent-frolic  4 open" in screen(app)                          # ●n ○n go before the name is cut:
        assert "agent-frolic ●1 ○2" in screen(app).splitlines()[0]                 # the top line has them too
        assert "djp3/quiet-one  0 open" in screen(app)

        await pilot.resize_terminal(75, 46)                                        # the narrowest the Mac's window goes
        await pilot.pause()
        assert "djp3/quie…  0 open" in screen(app) and "djp3/agen…  4 open" in screen(app)
        assert "acme-plat…  0 open" in screen(app)
        assert "nothing is open" not in screen(app)

        # more open than were read: the line says both, and "most recent shown" is the first to go
        key = "djp3/agent-frolic"
        app.pulls[key] = replace(app.pulls[key], total=240)
        await pilot.resize_terminal(150, 46)
        app.draw()
        await pilot.pause()
        assert "djp3/agent-frolic  ●1  ○2  240 open, the 4 most recent shown" in screen(app)
        await pilot.resize_terminal(130, 46)
        await pilot.pause()
        assert "djp3/agent-frolic  ●1  ○2  240 open ═" in screen(app)
        await pilot.resize_terminal(100, 46)                                       # then ●n ○n, before its timer
        await pilot.pause()
        assert "djp3/agent-frolic  240 open ═" in screen(app)


@drive
async def test_as_the_window_narrows_progress_goes_then_the_ages_bar_then_its_figure(isolated):
    """A pull request's row gives them up in that order. A repository's line keeps its bar and
    its figure throughout: its room is its own."""
    a_board()
    seen = {}
    app = main.Frolic()
    async with app.run_test(size=(162, 46)) as pilot:
        await settle(app, pilot)
        for wide in (162, 150, 130, 118):                                             # one window, narrowed
            await pilot.resize_terminal(wide, 46)
            await pilot.pause()
            rows = screen(app).splitlines()
            head = next(row for row in rows if "pull request" in row and "review" in row)
            selected, row = (next(r for r in rows if number in r) for number in ("#12 GraphQL", "#16 Transcript"))
            seen[wide] = ("progress" in head, "━" in row, bool(re.search(r" \d+d \d\dh$", row)), head.endswith(" age"),
                          timer(app, "╪ djp3/quiet-one")[1:], timer(app, "╪ djp3/agent-frolic")[1:])
            if seen[wide][2]:                                                         # a figure, with its bar or alone,
                assert len(row) == len(selected) == len(head), wide                   # ends where "age" does: on the
                assert re.search(r" \d+d \d\dh$", selected), wide                    # selected row too
                assert ("━" in selected) == seen[wide][1], wide
    assert seen[162][:4] == (True, True, True, True)                                  # all of it is there;
    assert seen[150][:4] == (False, True, True, True)                                 # progress goes first,
    assert seen[130][:4] == (False, False, True, True)                                # then the age's bar
    assert seen[118][:4] == (False, False, False, False)                              # then its figure, and its heading
    for wide in seen:                                                                 # each repository's line
        assert seen[wide][4:] == ((board.AGE_BAR, "-5m"), (board.AGE_BAR, "-5m")), wide     # has both all the while


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
        await pilot.press("tab", "tab")                                       # everyone → you → bob
        await pilot.pause()
        assert app.board.focus == board.Focus("crew", "bob")
        # not bob's: dimmed, but for the rule, and what is left of its age's bar, darker than that already
        # (nothing is left of it once the pull request is three months old: its date here is fixed)
        other = {ink for ink, _ in colors(app, "#16 Transcript")} - {dim, board.DIVIDER.lower()}
        assert other <= {board.BAR_TRACK.lower()} and light(board.BAR_TRACK) < light(dim)
        assert dim not in {ink for ink, _ in colors(app, "#13 Review")}       # queued behind what waits on bob
        await pilot.press("shift+tab")
        await pilot.pause()
        assert app.board.focus == board.Focus("crew", "djp3")
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
        assert "djp3/agent-frolic  0 open" in shown and "nothing is open" not in shown    # read, and empty
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
    """The board is drawn several times a second; a new reading comes every few minutes."""
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


# How many lines the list of a_long_board() has: for each of the two repositories its own line,
# its base branch, the commit and its pull requests; then the line of the one with nothing open.
LONG = (3 + 24) + (3 + 20) + 1


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
    every = [f"{key} {does}" for key, does in board.HINTS]
    full = board.HINT_GAP.join(every)
    assert board.hint_line(cell_len(full)).plain == full
    assert board.hint_line(102).plain == ("↑↓ Pull request  o Open on GitHub  PgUp PgDn Scroll  "
                                          "n Next needing effort  tab Focus  s Setup  q Quit")
    without = [hint for hint in every if hint != "C Copy as /rename"]              # c's other way goes first of all
    assert len(without) == len(every) - 1 and board.hint_line(cell_len(full) - 1).plain == board.HINT_GAP.join(without)
    last = "s Setup  q Quit"
    for width in range(cell_len(last), cell_len(full) + 1):
        line = board.hint_line(width).plain
        shown = line.split(board.HINT_GAP)
        assert cell_len(line) <= width and line.endswith(last), width
        assert shown == [h for h in every if h in shown], width                    # in their order


def test_a_hints_key_is_lit_and_its_words_are_plain():
    """As agent-farm's footer is: the key bold and in the key's color, the words beside it."""
    line = board.hint_text([("q", "Quit"), ("PgUp PgDn", "Scroll")])
    assert line.plain == "q Quit  PgUp PgDn Scroll"
    drawn = [(line.plain[span.start:span.end], str(span.style)) for span in line.spans]
    assert drawn == [("q", f"bold {board.KEY}"), (" Quit", board.INK2),
                     ("PgUp PgDn", f"bold {board.KEY}"), (" Scroll", board.INK2)]


def test_the_keys_color_is_no_color_that_already_means_something():
    """Not a status, not the yellow of what is yours, not an age's violet, not the ink of words."""
    taken = (theme.GOOD, theme.WARN, theme.SERIOUS, theme.CRIT, theme.WAITING, theme.AGE, theme.DIVIDER,
             theme.INK, theme.INK2, theme.MUTED)
    assert theme.KEY.lower() not in {color.lower() for color in taken}


def test_the_setups_hints_leave_out_the_seldom_wanted_first_and_keep_the_board_and_quit():
    every = [f"{key} {does}" for key, does in main.SETUP_HINTS]
    full = board.HINT_GAP.join(every)

    def fitted(width):
        return board.fit_hints(main.SETUP_HINTS, main.SETUP_LEFT_OUT_FIRST, width).plain

    assert fitted(cell_len(full)) == full and fitted(cell_len(full) - 1) == full.replace("W Off  ", "")
    last = "s Board  q Quit"
    for width in range(cell_len(last), cell_len(full) + 1):
        shown = fitted(width).split(board.HINT_GAP)
        assert cell_len(fitted(width)) <= width and fitted(width).endswith(last), width
        assert shown == [h for h in every if h in shown], width                    # in their order
    assert set(main.SETUP_LEFT_OUT_FIRST) == {key for key, _ in main.SETUP_HINTS} - {"s", "q"}


@drive
async def test_the_setups_foot_is_fitted_to_the_window_with_its_keys_lit(isolated):
    a_board()
    app = main.Frolic()
    async with app.run_test(size=(120, 46)) as pilot:
        await settle(app, pilot)
        await pilot.press("s")
        await pilot.pause()
        foot = screen(app).splitlines()[-1]
        assert "s Board  q Quit" in foot and "t Token" in foot and "…" not in foot
        assert "W Off" not in foot                                                 # the seldom wanted went first
        await pilot.resize_terminal(170, 46)                                       # widened: all of them
        await settle(app, pilot)
        assert board.hint_text(main.SETUP_HINTS).plain in screen(app).splitlines()[-1]
        drawn = next(strip for strip in app.screen._compositor.render_strips() if "q Quit" in strip.text)
        lit = "".join(seg.text if seg.style and seg.style.bold and seg.style.color
                      and seg.style.color.get_truecolor().hex.lower() == board.KEY.lower() else " " for seg in drawn)
        assert lit.split() == "t f a ↑↓ [ ] space x b w W r p u s q".split()       # the keys, and only the keys


@drive
async def test_a_narrow_window_still_shows_how_to_reach_the_setup_and_quit(isolated):
    a_board()
    app = main.Frolic()
    async with app.run_test(size=(120, 46)) as pilot:
        await settle(app, pilot)
        hints = screen(app).splitlines()[-1]
        assert "s Setup  q Quit" in hints and "…" not in hints
        assert "d Drawer" not in hints                                             # the least needed went first
        await pilot.resize_terminal(190, 46)                                       # widened: all of them
        await settle(app, pilot)
        assert board.hint_line(9999).plain in screen(app).splitlines()[-1]
        foot = next(strip for strip in app.screen._compositor.render_strips() if "q Quit" in strip.text)
        lit = "".join(seg.text if seg.style and seg.style.bold and seg.style.color
                      and seg.style.color.get_truecolor().hex.lower() == board.KEY.lower() else " " for seg in foot)
        assert lit.split() == "↑↓ o c C PgUp PgDn n tab esc m u d s q".split()     # the keys, and only the keys


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
        assert (first, total) == (1, LONG) and last < total and "djp3/quiet-one  " not in screen(app)
        assert f"rows 1–{last} of {LONG}" in screen(app)
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
        assert deck.where == (LONG - rows + 1, LONG, LONG) and "rows" in shown
        assert "djp3/quiet-one  0 open" in shown.split("needs effort")[0] and "nothing is open" not in shown
        assert bar(app)[-1] == board.THUMB and bar(app)[0] == board.TRACK_LINE     # at the bottom, more above
        await pilot.press("pageup")
        await pilot.pause()
        assert deck.top == LONG - rows - (rows - 2)
        await pilot.press("home")
        await pilot.pause()
        assert deck.top == 0 and bar(app)[0] == board.THUMB
        await pilot.press("m", "pagedown", "end")                                 # with the menu open the list stays
        await pilot.pause()
        assert deck.top == 0


@drive
async def test_the_last_pull_request_brings_what_follows_it_into_view(isolated):
    """Two repositories with nothing open follow it here, so two lines. One line is kept
    below whatever is selected anyway: the second shows only by the rule for the last one."""
    a_long_board()
    RepoStore().add("djp3/still-one", admin=True)
    FakePulls.open_["djp3/still-one"] = []
    app = main.Frolic()
    async with app.run_test(size=(150, 34)) as pilot:
        await settle(app, pilot)
        deck = app.board
        assert [drawn.row.pull for drawn in deck.lines()[-2:]] == [None, None] and deck.where[2] == LONG + 1
        await pilot.press(*["down"] * (len(deck.keys()) - 1))
        await pilot.pause()
        assert deck.sel == deck.keys()[-1] and not deck.roam
        above = screen(app).split("needs effort")[0]
        assert f"#{deck.sel[1]} Second change" in above                           # the selected one shows,
        assert "djp3/quiet-one  0 open" in above and "djp3/still-one  0 open" in above    # and so do both after it
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
        assert deck.top == LONG - rows and "djp3/quiet-one" in screen(app).split("needs effort")[0]
        await pilot.hover("#scroll", offset=(0, rows // 2))                        # dragged to the middle
        await pilot.pause()
        assert 0 < deck.top < LONG - rows
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
        assert deck.where == (LONG, LONG, LONG) and "djp3/quiet-one  0 open" in listed()    # the very last line
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
        assert "o Open on GitHub" in screen(app) and "tab Focus" in screen(app)
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
async def test_the_drawer_gives_the_name_for_an_agent_and_c_copies_it_and_C_the_command(isolated):
    """Nobody should have to work the name out, or type it. C copies the whole command that
    gives a Claude Code session the name, to paste at the agent's prompt as it is."""
    a_board()
    app = main.Frolic()
    async with app.run_test(size=(150, 46)) as pilot:
        await settle(app, pilot)
        app.OPEN_AGAIN_S = 0                                                  # (a key held down has its own test)
        assert "c Copy agent name" in screen(app)
        assert "name     djp3-agent-frolic-pr-12 · c copies it, C as /rename" in screen(app)   # the drawer, for the
                                                                              # selected one
        await pilot.press("c")
        await settle(app, pilot)
        assert FakeClipboard.copied == ["djp3-agent-frolic-pr-12"]
        assert "agent-frolic#12 · copied djp3-agent-frolic-pr-12, the name for an agent on it" in screen(app)
        app.reported = (Reported("djp3", FakeClipboard.copied[0] + " fix", "busy"),)    # a session named so
        app.draw()
        await pilot.pause()
        assert [a.note for a in app.board.pic.agents[("djp3/agent-frolic", 12)]] == ["fix"]     # is on it
        await pilot.press("down", "down", "C")                                # C: the command, with the name in it
        await settle(app, pilot)
        assert FakeClipboard.copied[1:] == ["/rename djp3-agent-frolic-pr-9"]  # and no new line: it is theirs to send
        assert main.RENAME == "/rename "
        assert ("agent-frolic#9 · copied /rename djp3-agent-frolic-pr-9, the command that names an agent's session "
                "for it") in screen(app)
        assert "name     djp3-agent-frolic-pr-9 · c copies it" in screen(app)
        await pilot.press("c")                                                # and c still the name alone
        await settle(app, pilot)
        assert FakeClipboard.copied[2:] == ["djp3-agent-frolic-pr-9"]
        assert "agent-frolic#9 · copied djp3-agent-frolic-pr-9, the name for an agent on it" in screen(app)
        await pilot.press("m", "c", "C")                                      # not from under the menu
        await settle(app, pilot)
        assert len(FakeClipboard.copied) == 3
        assert FakeBrowser.opened == []                                       # and c opens nothing


@drive
async def test_holding_c_down_copies_the_name_once_and_a_copy_that_fails_is_said(isolated, monkeypatch):
    """As for o: the key repeats while it is held, and a worker's uncaught error would end the app."""
    a_board()
    app = main.Frolic()
    async with app.run_test(size=(150, 46)) as pilot:
        await settle(app, pilot)
        now = [time.time()]
        monkeypatch.setattr(main, "time", SimpleNamespace(time=lambda: now[0], monotonic=time.monotonic,
                                                          localtime=time.localtime, strftime=time.strftime))
        for _ in range(30):                                                   # held for a second
            await pilot.press("c")
            now[0] += 0.033
        await settle(app, pilot)
        assert FakeClipboard.copied == ["djp3-agent-frolic-pr-12"]
        now[0] += app.OPEN_AGAIN_S + 0.1                                      # let go, then asked for again
        await pilot.press("c")
        await settle(app, pilot)
        assert len(FakeClipboard.copied) == 2
        # The other key, at once: what it asks for is something else, so it is copied. Held, once.
        for _ in range(30):
            await pilot.press("C")
            now[0] += 0.033
        await settle(app, pilot)
        assert FakeClipboard.copied[2:] == ["/rename djp3-agent-frolic-pr-12"]
        await pilot.press("c")                                                # and back, at once
        await settle(app, pilot)
        assert FakeClipboard.copied[3:] == ["djp3-agent-frolic-pr-12"]
        FakeClipboard.broken = OSError("no clipboard")
        now[0] += app.OPEN_AGAIN_S + 0.1
        await pilot.press("C")                                                # the command, when it fails, is said whole
        await settle(app, pilot)
        assert ("agent-frolic#12 · /rename djp3-agent-frolic-pr-12 was not copied: the clipboard could not be reached"
                in screen(app))
        FakeClipboard.broken = None
        del FakeClipboard.copied[2:]                                          # (what follows counts from here)
        failed = "agent-frolic#12 · djp3-agent-frolic-pr-12 was not copied: the clipboard could not be reached"
        for trouble in (OSError("no clipboard"), subprocess.TimeoutExpired("pbcopy", 5),
                        RuntimeError("something nobody expected")):
            FakeClipboard.broken = trouble
            now[0] += app.OPEN_AGAIN_S + 0.1
            await pilot.press("c")
            await settle(app, pilot)
            assert app.is_running and app.return_code is None, trouble         # it never closes the board
            assert failed in screen(app), trouble
        assert [said for _, said, _ in app.lines].count(failed) == 3          # the setup's log keeps it
        FakeClipboard.broken = None
        await pilot.press("c")                                                # one that failed: again at once
        await settle(app, pilot)
        assert len(FakeClipboard.copied) == 3


def a_slow_copy(monkeypatch, fails=False):
    """The clipboard, slow to take the first name it is handed, and failing at it when told
    to: `started` is set once that copy is under way, and it ends only when `held` is set."""
    held, started = threading.Event(), threading.Event()

    def copy(text):
        if not started.is_set():
            started.set()
            held.wait(5)
            if fails:
                raise OSError("no clipboard")
        FakeClipboard.copy(text)

    monkeypatch.setattr(main, "copy_text", copy)
    return held, started


@drive
async def test_a_copy_slow_to_end_does_not_replace_the_name_asked_for_after_it(isolated, monkeypatch):
    """Each c starts a copy on a thread of its own, and nothing made them end in the order
    they were asked for: the slower one was what the clipboard was left with, and what the
    ticker said, though it was asked for first."""
    a_board()
    app = main.Frolic()
    held, started = a_slow_copy(monkeypatch)
    async with app.run_test(size=(150, 46)) as pilot:
        await settle(app, pilot)
        await pilot.press("c")                                                # the name for #12
        await until(pilot, started.is_set)
        await pilot.press("down", "c", "down", "c")                           # before it ends: #13's, then #9's
        await pilot.pause(0.2)
        assert app.name_asked[0] == "djp3-agent-frolic-pr-9"                  # all three have been asked for,
        assert FakeClipboard.copied == []                                     # and the later two wait their turn
        held.set()                                                            # the first one ends only now
        await settle(app, pilot)
        assert FakeClipboard.copied == ["djp3-agent-frolic-pr-12",            # it was already being written;
                                        "djp3-agent-frolic-pr-9"]             # the last asked for is what is left
        assert "agent-frolic#9 · copied djp3-agent-frolic-pr-9, the name for an agent on it" in screen(app)


@drive
async def test_a_copy_that_fails_after_a_later_name_was_asked_for_says_nothing(isolated, monkeypatch):
    a_board()
    app = main.Frolic()
    held, started = a_slow_copy(monkeypatch, fails=True)
    async with app.run_test(size=(150, 46)) as pilot:
        await settle(app, pilot)
        await pilot.press("c")                                                # the name for #12: it will fail
        await until(pilot, started.is_set)
        await pilot.press("down", "down", "c")                                # before it does, #9's is asked for
        await pilot.pause(0.2)
        held.set()
        await settle(app, pilot)
        assert FakeClipboard.copied == ["djp3-agent-frolic-pr-9"]
        assert "agent-frolic#9 · copied djp3-agent-frolic-pr-9, the name for an agent on it" in screen(app)
        assert not any("was not copied" in said for _, said, _ in app.lines)  # the one that failed is not what
        assert app.name_asked[0] == "djp3-agent-frolic-pr-9"                  # the user is waiting on: no word of it


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
        node(201, "sec-4-bump-a", author={"login": "dependabot"}, title="Bump a",
             reviewRequests=ask_team("platform"), updatedAt="2026-11-12T16:00:00Z"),
        node(202, "bump-b", author={"login": "dependabot"}, title="Bump b", reviewRequests=ask_team("docs"),
             updatedAt="2026-11-13T16:00:00Z"),
        node(203, "theme", author={"login": "mira"}, title="Dark theme", reviewRequests=ask("bob"),
             updatedAt="2026-11-14T10:00:00Z")]}


def who(app, needle):
    """Column 1 of the row that holds `needle`: owner, state, waits on."""
    return line(app, needle).split("│")[0].split()


@drive
async def test_a_request_to_a_team_the_viewer_is_on_is_drawn_as_theirs_and_the_team_is_named_among_the_cards(isolated):
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
        assert cards.index("you") < cards.index("● Platform")
        assert "team/docs" not in cards and "bob" not in cards                            # no wait of the viewer's
        assert yellow in {ink for ink, _ in colors(app, "● Platform")}                    # the dot on the team's name
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
        app.reported = (Reported("bob", "acme-web-console-pr-201 review", "idle"),)     # as the grange reports it
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


# ---- the bars: each repository's timer, and an age -----------------------------------------

def test_a_thin_bar_is_anchored_at_the_event_the_time_is_from_or_to_and_colored_by_which():
    age, timer, track, full = board.AGE_INK, board.TIMER_INK, board.BAR_TRACK, board.AGE_BAR
    # time since: the filled part starts at the left and grows, whole only when all of it has gone by
    assert board.thin_bar(0) == [("─" * full, track)]
    assert board.thin_bar(1) == [("━" * full, age)]
    assert board.thin_bar(0.5) == [("━" * (full // 2), age), ("─" * (full // 2), track)]
    assert board.thin_bar(0.99) == [("━" * (full - 1), age), ("─", track)]
    # time until: it is what is left, ends at the right, and is gone only when the time is up
    assert board.thin_bar(1, until=True) == [("━" * full, timer)]
    assert board.thin_bar(0, until=True) == [("─" * full, track)]
    assert board.thin_bar(0.5, until=True) == [("─" * (full // 2), track), ("━" * (full // 2), timer)]
    assert board.thin_bar(0.01, until=True) == [("─" * (full - 1), track), ("━", timer)]
    for part in (0.01, 0.3, 2, -1):                                           # always that many cells
        assert sum(len(text) for text, _ in board.thin_bar(part)) == full
        assert sum(len(text) for text, _ in board.thin_bar(part, until=True)) == full
    # the two time different things, one over the other: they are not one color, nor any other
    # color on the board that means something
    import theme
    assert age != timer and age == theme.AGE and timer == theme.INK2
    meaning = (theme.GOOD, theme.WARN, theme.SERIOUS, theme.CRIT, theme.WAITING, theme.DIVIDER, theme.INK, theme.MUTED)
    assert age not in meaning


def test_an_age_is_drawn_in_the_room_its_column_has():
    age = 4 * 86400 + 7 * 3600

    def said(room, seconds=age):
        return "".join(text for text, _ in board.age_cells(seconds, room))

    assert said(board.QUIET) == "━━━━━━━━────   4d 07h"                        # its bar and its figure
    assert said(board.QUIET, 128 * 86400 + 11 * 3600) == "━━━━━━━━━━━━ 128d 11h"
    assert said(board.QUIET, 20) == "────────────       0h"
    assert said(board.AGE_FIGURE + 1) == "4d 07h"                             # less room: the figure alone
    assert said(0) == ""                                                      # none: the column is given up
    assert said(board.QUIET, 1000 * 86400) == "━━━━━━━━━━━━    1000d"             # past 999 days: the days alone
    assert len(said(board.QUIET)) == board.QUIET - 1                          # a cell is left before the edge
    # within the hour the figure is "0h" throughout, and the bar has three steps and no more
    within = [said(board.QUIET, seconds) for seconds in (5, 3 * 60, 5 * 60, 14 * 60, 16 * 60, 59 * 60)]
    assert [drawn.count("━") for drawn in within] == [0, 0, 1, 1, 2, 2] and all(d.endswith(" 0h") for d in within)
    assert board.age_cells(None, board.QUIET) == []                           # an age nobody knows


def test_what_the_age_column_holds_follows_from_the_room_the_columns_leave_it():
    """(its bar, its figure): what is drawn there and where the columns stand cannot come
    apart."""
    forms = {wide: board.age_form(wide - board.columns(wide)[4]) for wide in (160, 156, 134, 133, 121, 120, 100)}
    assert forms == {160: (True, True), 156: (True, True), 134: (True, True), 133: (False, True),
                     121: (False, True), 120: (False, False), 100: (False, False)}


def timer(app, needle):
    """The timer on the screen line that holds `needle`: how many cells of its bar are
    white, how many cells the bar has (0 when it is left out), and the figure after it
    ("" for none)."""
    for strip in app.screen._compositor.render_strips():
        if needle not in strip.text:
            continue
        filled = cells = 0
        for segment in strip:
            text, style = segment.text, segment.style
            ink = style.color.get_truecolor().hex.lower() if style is not None and style.color else ""
            if text and set(text) <= {"━"} and ink == board.TIMER_INK.lower():
                filled, cells = filled + len(text), cells + len(text)
            elif text and set(text) <= {"─"} and ink == board.BAR_TRACK.lower():
                cells += len(text)
        figure = re.search(r" (-\d+m|0m)$", strip.text.rstrip())
        return filled, cells, figure.group(1) if figure else ""
    raise AssertionError(f"no line with {needle!r}")


@drive
async def test_each_repositorys_line_ends_in_its_own_timer(isolated):
    """A thin bar whose white is the time left until that repository is next read, and after
    it the minutes left, counted up to nought. It stands in the age column, over its pull
    requests' bars, and its white ends at the right where theirs starts at the left."""
    a_board()
    keychain.put(keychain.NGROK_AUTHTOKEN, AUTHTOKEN)
    Settings(webhooks=True, ngrok_domain="frolic.ngrok-free.dev").save()
    app = main.Frolic()
    async with app.run_test(size=(160, 46)) as pilot:
        await settle(app, pilot)
        app.draw()
        await pilot.pause()
        ours, theirs, quiet = "╪ djp3/agent-frolic", "╪ acme/web-console", "╪ djp3/quiet-one"
        full = board.AGE_BAR
        assert timer(app, theirs) == (full, full, "-5m")                     # just read: five minutes to go
        assert timer(app, ours)[:2] == timer(app, quiet)[:2] == (full, full)     # one with nothing open has one too
        top = screen(app).splitlines()[0]
        assert "━" not in top and "read" not in top                          # and the top line has no key to them

        app.hooked["djp3/agent-frolic"] -= 3600                              # its hook was confirmed long ago,
        span = app.period(app.store.get("djp3/agent-frolic"))                # so it is on the slow timer
        assert span == 30 * 60
        app.read_at["djp3/agent-frolic"] -= span / 2
        app.draw()
        await pilot.pause()
        assert timer(app, ours) == (full // 2, full, "-15m")                 # half its time is left
        assert timer(app, theirs) == (full, full, "-5m")                     # all of that one's is
        # its bar stands over its pull requests' bars, its white at the right end where theirs is
        # at the left, and its figure ends where theirs do
        rows = screen(app).splitlines()
        its, under = next(r for r in rows if ours in r), next(r for r in rows if "#12 GraphQL" in r)
        assert its.rindex("━") == under.index("━") + full - 1 and its.index("─") == under.index("━")
        assert len(its) == len(under)

        told, app.timer = app.timer, lambda name: (1.0, 0.0)                 # a timer that is up, whatever the
        app.draw()                                                           # app's own tick reads meanwhile:
        await pilot.pause()                                                  # the white is gone, and the
        assert timer(app, theirs) == (0, full, "0m")                         # figure is nought
        app.timer = told
        age(app, only=["acme/web-console"])                                  # its five minutes are up: it is
        app.tick()                                                           # read, and both start again
        await settle(app, pilot)
        app.draw()
        await pilot.pause()
        assert timer(app, theirs) == (full, full, "-5m") and timer(app, ours)[0] == full // 2


@drive
async def test_in_a_narrow_window_a_repositorys_name_and_count_come_before_its_timer(isolated):
    """And its timer before ●n ○n, which the top line has too."""
    a_board()
    app = main.Frolic()
    async with app.run_test(size=(100, 40)) as pilot:
        await settle(app, pilot)
        app.draw()
        await pilot.pause()
        assert timer(app, "╪ djp3/agent-frolic") == (0, 0, "-5m")            # no room for a bar: the figure alone,
        assert "djp3/agent-frolic  4 open ═" in line(app, "╪ djp3/agent-frolic")           # after the name and count
    async with main.Frolic().run_test(size=(82, 40)) as pilot:               # narrower still: the figure goes
        app = pilot.app                                                      # too, where the line is full
        await settle(app, pilot)
        app.draw()
        await pilot.pause()
        assert timer(app, "╪ djp3/agent-frolic") == (0, 0, "")
        assert "djp3/agent-frolic  4 open ═" in line(app, "╪ djp3/agent-frolic")           # and the count stays


@drive
async def test_a_long_countdown_is_whole_in_the_narrowest_window(isolated):
    """The longest timer counts from -1440m: six cells, drawn whole wherever the repository's
    line has room for them, however narrow the window."""
    keychain.put(keychain.GITHUB_TOKEN, TOKEN)
    RepoStore().add("djp3/app", admin=True)
    FakePulls.open_ = {"djp3/app": []}
    Settings(poll_minutes=Settings.POLL_MOST).save()
    app = main.Frolic()
    async with app.run_test(size=(82, 40)) as pilot:
        await settle(app, pilot)
        app.draw()
        await pilot.pause()
        head = next(row for row in screen(app).splitlines() if "pull request" in row)
        its = line(app, "╪ djp3/app")
        assert " age" not in head                                            # the age column is given up there;
        assert its.endswith("═ -1440m") and len(its) == 82 - 2               # the figure is whole, a cell from the edge


@drive
async def test_however_old_a_pull_request_its_age_ends_where_the_others_do(isolated):
    """Past 999 days the figure is the days alone, with its bar or without it."""
    keychain.put(keychain.GITHUB_TOKEN, TOKEN)
    RepoStore().add("djp3/app", admin=True)
    long_ago, lately = (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - seconds))
                        for seconds in (1001 * 86400 + 3 * 3600, 90000))
    FakePulls.open_ = {"djp3/app": [node(7, "old", author={"login": "mira"}, title="Old", updatedAt=long_ago),
                                    node(8, "new", author={"login": "mira"}, title="New", updatedAt=lately)]}
    for wide, bar in ((160, True), (130, False)):                             # with its bar, and without it
        app = main.Frolic()
        async with app.run_test(size=(wide, 46)) as pilot:
            await settle(app, pilot)
            rows = screen(app).splitlines()
            head = next(row for row in rows if "pull request" in row)
            its, other = next(row for row in rows if "#7 " in row), next(row for row in rows if "#8 " in row)
            assert its.endswith(" 1001d") and other.endswith(" 1d 01h") and len(its) == len(other) == len(head), wide
            assert ("━" in its, "━" in other) == (bar, bar), wide


@drive
async def test_an_age_is_said_one_way_in_the_list_the_drawer_what_needs_effort_and_the_setup(isolated):
    """In days and hours. The drawer and what needs effort said "4d", and the setup's list too."""
    keychain.put(keychain.GITHUB_TOKEN, TOKEN)
    RepoStore().add("djp3/app", admin=True)
    long_ago, lately = (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - seconds))
                        for seconds in (4 * 86400 + 7 * 3600 + 60, 20 * 60))
    FakePulls.open_ = {"djp3/app": [
        node(7, "old", author={"login": "mira"}, title="Old", reviewRequests=ask("djp3"), updatedAt=long_ago),
        node(8, "new", author={"login": "mira"}, title="New", reviewRequests=ask("djp3"), updatedAt=lately)]}
    app = main.Frolic()
    async with app.run_test(size=(160, 46)) as pilot:
        await settle(app, pilot)
        one = screen(app)
        assert line(app, "#7 Old").endswith(" 4d 07h") and line(app, "#8 New").endswith(" 0h")     # the list
        assert "quiet 4d 07h" in one and "quiet 0h" in one                   # what needs effort
        await pilot.press("down")
        await pilot.pause()
        said = {("last changed 4d 07h ago" in shown, "changed within the hour" in shown) for shown in (one, screen(app))}
        assert said == {(True, False), (False, True)}                        # the drawer, for each in turn
        listed = sorted(main.pull_line(pull, "main", 150).plain[-9:] for pull in app.pulls["djp3/app"].pulls)
        assert listed == ["       0h", "   4d 07h"]                            # and the setup's list


@drive
async def test_what_a_repositorys_line_gives_up_as_the_window_narrows_does_not_come_back(isolated):
    """The "most recent shown", then ●n ○n, then the timer's bar, then its countdown: each for
    good before the next. The bar used to go while the others were there, and come back when
    one of them went. How many are open stays to the last."""
    a_board()
    app = main.Frolic()
    async with app.run_test(size=(170, 46)) as pilot:
        await settle(app, pilot)
        app.pulls["djp3/agent-frolic"] = replace(app.pulls["djp3/agent-frolic"], total=240)     # more than were read
        seen = []
        for wide in range(170, 75, -1):
            await pilot.resize_terminal(wide, 46)
            await pilot.pause()
            row = line(app, "╪ djp3/a")
            cells, figure = timer(app, "╪ djp3/a")[1:]
            seen.append(("most recent shown" in row, "●1" in row, cells > 0, bool(figure), "240 open" in row))
        assert seen[0] == (True, True, True, True, True) and seen[-1][:4] == (False, False, False, False)
        assert all(step[4] for step in seen)                                 # how many are open: at every width
        went = []
        for part in range(4):
            there = [step[part] for step in seen]
            assert there == sorted(there, reverse=True), part                # there, then gone, and never back
            went.append(there.index(False))
        assert went == sorted(went) and len(set(went)) == 4                  # and in that order, one at a time


@drive
async def test_an_ages_bar_and_a_repositorys_timer_are_not_one_color(isolated):
    """A repository's timer counts down to its next read; an age counts up from a pull
    request's last change. One stands over the others, so each has its own color."""
    a_board()
    app = main.Frolic()
    async with app.run_test(size=(160, 46)) as pilot:
        await settle(app, pilot)
        age, timer_ = board.AGE_INK.lower(), board.TIMER_INK.lower()

        def bars(needle):
            """The colors of the filled cells of the bar on the line that holds `needle`."""
            for strip in app.screen._compositor.render_strips():
                if needle in strip.text:
                    return {segment.style.color.get_truecolor().hex.lower() for segment in strip
                            if "━" in segment.text and segment.style and segment.style.color}
            raise AssertionError(needle)

        assert bars("#12 GraphQL") == {age} and bars("#16 Transcript") == {age}
        assert bars("╪ djp3/agent-frolic") == {timer_} and bars("╪ acme/web-console") == {timer_}
        await pilot.press("tab", "tab")                                      # bob's: the others dim, their bars too
        await pilot.pause()
        assert bars("#16 Transcript") == {board.DIM.lower()} and bars("╪ djp3/agent-frolic") == {timer_}


# ---- Copilot at work on a review ---------------------------------------------------------

COPILOT_ASKED = {"nodes": [{"requestedReviewer": {"__typename": "Bot", "login": "copilot-pull-request-reviewer"}}]}


@drive
async def test_while_copilot_reviews_its_wheel_turns_in_the_row_on_its_card_and_in_the_drawer(isolated, monkeypatch):
    keychain.put(keychain.GITHUB_TOKEN, TOKEN)
    RepoStore().add("djp3/agent-frolic", admin=True)
    FakePulls.open_ = {"djp3/agent-frolic": [
        node(12, "client", author={"login": "djp3"}, title="GraphQL client", reviewRequests=COPILOT_ASKED),
        node(14, "badges", author={"login": "djp3"}, title="Ticket badges", reviewRequests=ask("bob"))]}
    app = main.Frolic()
    async with app.run_test(size=(150, 40)) as pilot:
        await settle(app, pilot)
        for wheel in ("⡿", "⣯"):                                    # as the clock moves it on
            monkeypatch.setattr(app.board, "spin", lambda offset=0: wheel)
            app.draw()
            await pilot.pause()
            row = line(app, "#12 GraphQL client")
            assert who(app, "#12 GraphQL client") == ["you", "waiting", wheel, "copilot"]     # whom it waits on
            assert f"{wheel} copilot" in row.split("│")[1]                                    # its review
            assert f"{wheel} copilot" in line(app, "bob").split("─")[0]                       # its card in the strip
            assert f"review   {wheel} copilot reviewing" in screen(app)                       # the drawer
            assert "waits on copilot to review" in screen(app)
        assert who(app, "#14 Ticket badges") == ["you", "waiting", "bob"]                     # a person asked: no wheel
        assert "◔ bob" in line(app, "#14 Ticket badges")
        card = next(person for person in board.humans(app.board.pic) if person.name == "copilot")
        assert board.plain(board.head_line(card)) == "⣾ copilot"                               # laid out at rest
        # With the focus on its card the name is underlined, and the wheel before it is not. The
        # light still runs through the name, bold and underlined as it goes.
        while app.board.focus.human != "copilot-pull-request-reviewer":
            await pilot.press("tab")
            await pilot.pause()

        async def name(light: float | None = None) -> list:
            """The pieces of Copilot's name on its card, with the light held on `light` cells
            into it (or off it): (text, underlined, bold, ink)."""
            row = next(s.text for s in app.screen._compositor.render_strips() if "copilot" in s.text and "bob" in s.text)
            at = cell_len(row[:row.index("copilot")])
            inside = at - app.board.query_one("#strip").content_region.x      # the light counts the strip's own cells
            monkeypatch.setattr(app.board, "light", lambda offset=0: inside + (10 if light is None else light))
            app.draw()
            await pilot.pause()
            strip = next(s for s in app.screen._compositor.render_strips() if "copilot" in s.text and "bob" in s.text)
            found, x = [], 0
            for part in strip:
                if at <= x < at + len("copilot"):
                    found.append((part.text, bool(part.style.underline), bool(part.style.bold),
                                  part.style.color.get_truecolor().hex.lower()))
                x += cell_len(part.text)
            return found

        white, green = theme.INK.lower(), theme.LIGHT_AT_WORK.lower()
        assert await name() == [("copilot", True, True, white)]
        assert await name(3) == [("co", True, True, white), ("pil", True, True, green), ("ot", True, True, white)]
        strip = next(s for s in app.screen._compositor.render_strips() if "copilot" in s.text and "bob" in s.text)
        under = {part.text.strip(): bool(part.style and part.style.underline) for part in strip if part.text.strip()}
        assert not under[wheel]
        await pilot.press("escape")
        assert await name(3) == [("co", False, False, white), ("p", False, False, green),     # out of focus, as it
                                 ("i", False, True, green), ("l", False, False, green),       # was: bold only at
                                 ("ot", False, False, white)]                                 # the light's heart
        FakePulls.open_["djp3/agent-frolic"][0] = node(                                       # it has answered
            12, "client", author={"login": "djp3"}, title="GraphQL client",
            latestReviews={"nodes": [review("copilot-pull-request-reviewer", "COMMENTED", "2026-11-14T15:30:00Z")]})
        await pilot.press("u")
        await settle(app, pilot)
        assert "copilot" in line(app, "#12 GraphQL client") and wheel not in screen(app)


def test_a_line_laid_out_with_its_wheel_at_rest_is_drawn_with_it_where_it_stands():
    assert board.turned([("⣾ ", "a"), ("copilot", "b"), ("  ", "c")], "⡿") == [
        ("⡿ ", "a"), ("copilot", "b"), ("  ", "c")]
    assert board.turned([("● ", "a"), ("you", "b")], "⡿") == [("● ", "a"), ("you", "b")]
    # Only a line's mark, its first piece, is ever the wheel: a name made of the same
    # characters is left as it is.
    assert board.turned([("  ", "a"), ("⣾x", "b")], "⡿") == [("  ", "a"), ("⣾x", "b")]
    assert board.turned([("⣾ ", "a"), ("⣽⣽", "b")], "⡿") == [("⡿ ", "a"), ("⣽⣽", "b")]
    assert board.turned([], "⡿") == []


def test_the_wheel_takes_one_place_a_beat_though_a_beat_comes_a_moment_early_or_late(monkeypatch):
    """Counted from the clock alone, a beat that came just before or after the clock's own
    step drew a place twice, or skipped one: the wheel stuttered for as long as the board
    was open."""
    now = [0.0]
    monkeypatch.setattr(board, "time", SimpleNamespace(monotonic=lambda: now[0]))
    deck = SimpleNamespace(began=500.0)
    seen = []
    for beat in range(17):
        now[0] = 500.0 + beat * board.BEAT + (0.04, -0.04, 0.0)[beat % 3]
        seen.append(board.Board.spin(deck))
    assert "".join(seen) == (board.SPIN * 3)[:17] and len(set(board.SPIN)) == 8
    assert board.Board.spin(deck, 3) == board.SPIN[(16 + 3) % 8]            # a line's own place, set apart
    assert board.BEAT * len(board.SPIN) == 1.0                              # once round, each second
    now[0] = 502.0
    assert board.Board.light(deck, 6) == 2.0 * board.LIGHT_SPEED + 6        # and the light, two seconds on


@drive
async def test_the_board_is_drawn_on_every_beat_only_while_a_wheel_turns(isolated, monkeypatch):
    """Eight times a second shows each place of a wheel. With none turning it is four, as
    it was: that is all the washes and the timers ask, and a board is open all day."""
    a_board()
    app = main.Frolic()
    async with app.run_test(size=(150, 46)) as pilot:
        await settle(app, pilot)
        deck, draw, drawn = app.board, app.board.draw, []

        def draws(beats: int) -> int:
            monkeypatch.setattr(deck, "draw", lambda: drawn.append(1))
            drawn.clear()
            for _ in range(beats):
                deck.animate_tick()
            monkeypatch.setattr(deck, "draw", draw)
            return len(drawn)

        async def reported(*agents) -> None:
            app.reported = agents
            app.draw()
            await pilot.pause()

        assert not deck.moving and draws(8) == 4
        for word in ("busy", "shell"):                                       # at work, and on a command
            await reported(Reported("djp3", "djp3-agent-frolic-pr-12", word))
            assert deck.moving and draws(8) == 8, word
        await reported(Reported("djp3", "djp3-agent-frolic-pr-12", "idle"))  # still: at its prompt, or waiting
        assert not deck.moving and draws(8) == 4
        await reported(Reported("djp3", "djp3-agent-frolic", "busy"))        # at work with no pull request yet
        assert deck.moving
        await reported()
        FakePulls.open_["djp3/agent-frolic"][0] = node(12, "client", author={"login": "djp3"}, baseRefOid=TIP,
                                                       title="GraphQL client", reviewRequests=COPILOT_ASKED)
        await pilot.press("u")
        await settle(app, pilot)
        assert deck.moving and draws(8) == 8                                 # Copilot is reviewing


@drive
async def test_a_wheel_and_a_light_show_an_agent_at_work_in_green_and_one_on_a_command_in_white(isolated, monkeypatch):
    """In the list, on its human's card and in the drawer. The light is a color that comes
    and goes on the words: where it stands is the clock's, here held still."""
    a_board()
    app = main.Frolic()
    async with app.run_test(size=(150, 46)) as pilot:
        await settle(app, pilot)
        green, lit_green, white = theme.GOOD.lower(), theme.LIGHT_AT_WORK.lower(), theme.LIGHT_ON_COMMAND.lower()
        ink = theme.INK.lower()

        async def inks(needle: str, light: float) -> set:
            monkeypatch.setattr(app.board, "light", lambda offset=0: light)
            app.draw()
            await pilot.pause()
            return {color for color, _ in colors(app, needle)}

        app.reported = (Reported("djp3", "djp3-agent-frolic-pr-12 fix", "busy"),        # #12 waits on bob
                        Reported("djp3", "djp3-agent-frolic-pr-13 watch", "shell"))     # #13 is queued behind it
        monkeypatch.setattr(app.board, "spin", lambda offset=0: "⡿")
        at_work, on_command = "└ ⡿ agent 01   working", "└ ⡿ agent 02   command"
        # In the list an agent's name starts at cell 7: a light on cell 9 is on both names.
        there = await inks(at_work, 9.0)
        assert {green, lit_green} <= there and white not in there                       # the wheel, and the light
        there = await inks(on_command, 9.0)
        assert {ink, white} <= there and not {green, lit_green} & there                 # white, both of them
        # On cell 16 it is between the name and the state's word, and on neither.
        assert lit_green not in await inks(at_work, 16.0) and white not in await inks(on_command, 16.0)
        assert green in await inks(at_work, 16.0)                                       # the wheel is green all along
        # On a card "#12" starts at cell 3.
        assert {green, lit_green} <= await inks("⡿ #12", 4.0) and white in await inks("⡿ #13", 4.0)
        assert lit_green not in await inks("⡿ #12", 12.0)
        assert "owner    you › ⡿ agent 01 at work" in screen(app)                       # the drawer, on #12
        assert {green, lit_green} <= await inks("owner    you › ⡿ agent 01 at work", 2.0)
        await pilot.press("down")
        await pilot.pause()
        assert app.board.sel == ("djp3/agent-frolic", 13)
        assert "owner    you › ⡿ agent 02 waiting on a command" in screen(app)
        assert "agents   you › agent 02 waiting on a command" in screen(app)
        assert white in await inks("owner    you › ⡿ agent 02 waiting on a command", 2.0)
        # A command is not work: #13 is no further along for it.
        pic = app.board.pic
        assert [board.progress(pic, pic.find(("djp3/agent-frolic", number)))[0] for number in (12, 13)] == [
            "in progress", "queued"]


@drive
async def test_a_card_whose_name_looks_like_the_turning_wheel_keeps_its_name(isolated, monkeypatch):
    """A team may be called anything, two characters that begin with one of the wheel's
    included. Its name is not taken for the wheel and turned, on a card that has nothing to
    do with Copilot, and it is underlined like any other name when its card is picked."""
    a_team_board()
    FakeGitHub.teams_listed = [Team("Acme", "platform", "⣾x")]
    app = main.Frolic()
    async with app.run_test(size=(150, 46)) as pilot:
        await settle(app, pilot)
        monkeypatch.setattr(app.board, "spin", lambda offset=0: "⡿")
        app.draw()
        await pilot.pause()
        assert who(app, "#201 Bump a") == ["dependabot", "waiting", "●", "⣾x"]
        cards = line(app, "⣾x")                                                           # the strip is the first
        assert "● ⣾x" in cards and "you" in cards and "⡿" not in cards                    # its name, and no wheel
        await pilot.press("tab", "tab")                                                   # → you → the team
        await pilot.pause()
        assert app.board.focus == board.Focus("crew", "team/acme/platform")
        strip = next(s for s in app.screen._compositor.render_strips() if "⣾x" in s.text)
        under = {part.text.strip(): bool(part.style and part.style.underline) for part in strip if part.text.strip()}
        assert under["⣾x"] and not under["●"] and not under["you"]                        # the name, not its mark


# ---- the strip: the names with no agents share a card ---------------------------------------

NAMES = ("bob", "elif", "farid", "greta", "hugo", "ines", "jonas", "kira", "luca", "mona", "nils", "omar")


def you_with(agents):
    """The viewer, with this many agents: four to a column of their card."""
    return board.Person("djp3", "you", viewer=True, yours=True, agents=[
        board.Agent("djp3", f"{n:02d}", "o/app", n, "author", "working") for n in range(1, agents + 1)])


def bare(*names):
    return [board.Person(name, name, holds=True) for name in names]


def laid(people, room, keep=""):
    """The cards as `lay_cards` makes them: (whose, its columns as text) for each, and
    whether names are left out with no line to say so."""
    cards, unseen = board.lay_cards(people, room, keep)
    return [(card.human, [[board.plain(said).strip() for said, _ in col] for col in card.cols]) for card in cards], unseen


def test_the_names_with_no_agents_share_one_card_five_to_a_column():
    """Each was a card of its own: a name, and four empty rows under it."""
    cards, unseen = board.lay_cards([you_with(11), *bare(*NAMES[:7])], 136)
    yours, shared = cards
    assert not unseen and (yours.human, shared.human) == ("djp3", "")
    assert [len(col) for col in yours.cols] == [4, 4, 3] and yours.rows == 5      # all eleven: nothing is folded
    assert [[board.plain(said).strip() for said, _ in col] for col in shared.cols] == [
        ["bob", "elif", "farid", "greta", "hugo"], ["ines", "jonas"]]
    assert [who for col in shared.cols for _, who in col] == list(NAMES[:7])      # each line knows whose it is
    assert (shared.top, shared.rows, shared.head) == (0, 5, [])                   # from the first row: it has no name
    assert shared.x == yours.width + 3 and shared.width == 16                     # "  farid" and "  jonas", two apart
    # One with agents keeps a card of their own, between yours and the names.
    mira = board.Person("mira", "mira", agents=[board.Agent("mira", "01", "o/app", 7, "author", "working")])
    assert [who for who, _ in laid([you_with(11), mira, *bare("bob")], 136)[0]] == ["djp3", "mira", ""]


def test_the_names_never_fold_the_viewers_card_and_are_the_first_to_go():
    """Twelve names beside eleven agents folded the viewer's card to one column, three agents
    and "8 more, folded", and the strip ran off the edge all the same."""
    people = [you_with(11), *bare(*NAMES)]
    whole = [["#1", "#2", "#3", "#4"], ["#5", "#6", "#7", "#8"], ["#9", "#10", "#11"]]

    def agents(cards):
        return [[said.split()[-1] for said in col] for col in cards[0][1]]

    cards, unseen = laid(people, 44)                              # room for them all, three columns of names
    assert agents(cards) == whole and not unseen
    assert cards[1][1] == [list(NAMES[:5]), list(NAMES[5:10]), list(NAMES[10:])]
    cards, unseen = laid(people, 43)                              # a cell short: nine, and a line for the rest
    assert agents(cards) == whole and not unseen
    assert cards[1][1] == [list(NAMES[:5]), [*NAMES[5:9], "3 more"]]
    cards, unseen = laid(people, 36)                              # one column
    assert agents(cards) == whole and cards[1][1] == [[*NAMES[:4], "8 more"]] and not unseen
    cards, unseen = laid(people, 28)
    assert agents(cards) == whole and cards[1][1] == [[*NAMES[:4], "8 more"]]
    cards, unseen = laid(people, 27)                              # not even that: the caller marks the edge
    assert agents(cards) == whole and len(cards) == 1 and unseen
    assert not laid([you_with(11)], 28)[1]                        # with no names there is nothing to mark
    # The line that says how many more is a note: it is nobody's, and no stop of the focus.
    shared = board.lay_cards(people, 36)[0][1]
    assert [who for _, who in shared.cols[0]] == [*NAMES[:4], None]


def test_the_names_stand_as_tall_as_the_strip_is_and_taller_only_to_fit_them_all():
    """The strip is as tall as the agents need. Names that fit beside one another in a row
    do not take four more rows from the list to stand in a column."""
    three = bare("bob", "kenji", "tobias")
    assert laid([you_with(0), *three], 136)[0][1][1] == [["bob"], ["kenji"], ["tobias"]]      # nobody has agents: a row
    assert laid([you_with(0), *three], 32)[0][1][1] == [["bob"], ["kenji"], ["tobias"]]
    assert laid([you_with(0), *three], 31)[0][1][1] == [["bob", "kenji"], ["tobias"]]         # two rows fit them
    assert laid([you_with(0), *three], 24)[0][1][1] == [["bob", "kenji", "tobias"]]           # three do
    assert laid([you_with(0), *three], 16)[0][1][1] == [["bob", "kenji", "tobias"]]
    assert laid([you_with(0), *three], 15) == ([("djp3", [])], True)                          # nothing does
    # Two agents make the strip three rows: the names are three to a column, in the widest of windows.
    assert laid([you_with(2), *bare(*NAMES[:7])], 136)[0][1][1] == [
        ["bob", "elif", "farid"], ["greta", "hugo", "ines"], ["jonas"]]
    assert board.lay_cards([you_with(2), *bare(*NAMES[:7])], 136)[0][1].rows == 3
    # Never taller than five, the most the strip takes.
    assert max(len(col) for col in laid([you_with(0), *bare(*NAMES)], 20)[0][1][1]) == board.STRIP


def test_the_one_in_focus_keeps_a_place_among_the_names_that_fit():
    """The focus is shown on the strip and nowhere else."""
    people = [you_with(11), *bare(*NAMES)]
    assert laid(people, 40)[0][1][1] == [list(NAMES[:5]), [*NAMES[5:9], "3 more"]]
    assert laid(people, 40, keep="omar")[0][1][1] == [list(NAMES[:5]), [*NAMES[5:8], "omar", "3 more"]]
    assert laid(people, 40, keep="elif")[0][1][1] == [list(NAMES[:5]), [*NAMES[5:9], "3 more"]]    # it has one already
    assert laid(people, 40, keep="djp3")[0][1][1] == [list(NAMES[:5]), [*NAMES[5:9], "3 more"]]    # not one of them
    assert laid(people, 36, keep="omar")[0][1][1] == [[*NAMES[:3], "omar", "8 more"]]


def the_long_way(people, room, rows, keep=""):
    """What `names_card` must answer, worked out by laying each way out whole and measuring
    all of it: every height that might fit them all, then every shorter run of the names
    with a line for the rest. Its columns as (text, whose), and its width; None for no card."""
    lines = [(board.plain(board.head_line(person)), person.login.lower()) for person in people]

    def made(entries, height):
        cols = [entries[at:at + height] for at in range(0, len(entries), height)]
        return cols, sum(max(len(said) for said, _ in col) for col in cols) + 2 * (len(cols) - 1)

    for height in range(max(1, min(rows, board.STRIP)), board.STRIP + 1):
        cols, width = made(lines, height)
        if width <= room:
            return cols, width
    for count in range(len(lines) - 1, -1, -1):
        shown = lines[:count]
        if count and keep in [who for _, who in lines] and keep not in [who for _, who in shown]:
            shown = shown[:-1] + [entry for entry in lines if entry[1] == keep]
        cols, width = made(shown + [(f"  {len(lines) - count} more", None)], board.STRIP)
        if width <= room:
            return cols, width
    return None


def test_the_names_card_is_what_trying_every_shorter_run_of_names_would_give():
    """It works a run's width out from the widths of the names, without laying the run out.
    Two thousand boards, of none to twenty-three names of every length, in rooms from none
    to wide, at every height, with one to keep or none: the same card each time."""
    rng = random.Random(27)
    for _ in range(2000):
        people = bare(*(f"n{at}" + "x" * rng.randrange(0, 18) for at in range(rng.randrange(0, 24))))
        room, rows = rng.randrange(0, 90), rng.randrange(1, 7)
        keep = rng.choice(["", "nobody", *(person.login for person in people)])
        card = board.names_card(people, room, rows, keep)
        got = card and ([[(board.plain(said), who) for said, who in col] for col in card.cols], card.width)
        assert got == the_long_way(people, room, rows, keep), (len(people), room, rows, keep)
        assert card is None or card.widths == [max(len(board.plain(said)) for said, _ in col) for col in card.cols]


def test_names_that_do_not_fit_are_each_measured_once(monkeypatch):
    """Every shorter run of the names was laid out and measured again, to find the longest
    that fits: 45,450 measurements for 300 names with no room, on every one of the eight
    draws a second while a wheel turns."""
    measured = []
    seg_len = board.seg_len
    monkeypatch.setattr(board, "seg_len", lambda line: measured.append(line) or seg_len(line))
    for count in (50, 300, 600):
        people = bare(*(f"user{n}" for n in range(count)))
        measured.clear()
        assert board.names_card(people, 0, 5) is None                         # no room for any of it
        assert len(measured) == 2 * count                                     # each name, and each line for the rest
        measured.clear()
        card = board.names_card(people, 40, 5)                                # room for some
        assert [who for col in card.cols for _, who in col][-2:] == ["user18", None] and count <= len(measured) < 2 * count
        measured.clear()
        assert board.names_card(people, 10 * count, 5).width <= 10 * count and len(measured) == count     # and for all


def a_crowded_board():
    """a_board, with more people on it: six whose review the viewer's own pull requests
    wait on, and three strangers whose own drafts wait on themselves."""
    a_board()
    FakePulls.open_["djp3/agent-frolic"] += [
        node(300 + i, f"mine-{i}", author={"login": "djp3"}, baseRefOid=TIP, title=f"Mine {i}",
             reviewRequests=ask(name), updatedAt=f"2026-11-0{i + 1}T16:00:00Z")
        for i, name in enumerate(NAMES[1:7])]
    FakePulls.open_["djp3/agent-frolic"] += [
        node(200 + i, f"theirs-{i}", author={"login": name}, baseRefOid=TIP, title=f"Theirs {i}", isDraft=True)
        for i, name in enumerate(("aiko", "bruno", "chidi"))]


CROWD = (*(Reported("djp3", f"djp3-agent-frolic task {n}", "busy") for n in range(11)),
         Reported("mira", "djp3-agent-frolic-pr-9 docs", "busy"))


def strip_rows(app):
    """The rows of the strip, as text: they follow the top line and the rule under it."""
    return screen(app).splitlines()[2:2 + app.board.strip_rows]


async def steps_of(app, pilot):
    """Every stop of the focus key, from everyone round to everyone again."""
    assert app.board.focus == board.Focus()
    stops = []
    for _ in range(60):
        await pilot.press("tab")
        await pilot.pause()
        if app.board.focus == board.Focus():
            return stops
        stops.append(app.board.focus)
    raise AssertionError("the focus never came round")


@drive
async def test_on_the_board_the_names_stand_in_columns_beside_the_cards_of_those_with_agents(isolated):
    """A card for every one of them, eleven wide, and the viewer's own folded to make room."""
    a_crowded_board()
    app = main.Frolic()
    async with app.run_test(size=(150, 46)) as pilot:
        await settle(app, pilot)
        app.reported = CROWD
        app.draw()
        await pilot.pause(0.3)
        rows = strip_rows(app)
        assert len(rows) == 5 and "folded" not in "\n".join(rows) and "more" not in "\n".join(rows)
        yours, miras, names = (part for part in zip(*(row.split("│") for row in rows)))
        assert yours[0].split() == ["●", "you"] and sum(part.count("agent-frolic") for part in yours[1:]) == 11
        assert miras[0].split() == ["mira"] and "#9" in miras[1]
        # elif has been waited on longest and bob least: five to a column, then the next.
        assert [part.split() for part in names] == [["elif", "jonas"], ["farid", "bob"], ["greta"], ["hugo"], ["ines"]]
        shown = "\n".join(rows)
        assert all(name not in shown for name in ("tobias", "aiko", "bruno", "chidi"))     # their own to fix: no place
        assert "tobias · resolve the conflict" in screen(app)                            # and in what needs effort still
        # The focus key stops at you, at mira, and at each name: at nobody the strip has no place for.
        stops = await steps_of(app, pilot)
        assert [f.human for f in stops if f.kind == "crew"] == ["djp3", "mira", *sorted(NAMES[1:7]), "bob"]
        assert len([f for f in stops if f.kind == "agent"]) == 12 and len(stops) == 2 + 11 + 2 + 1 + 7
        # On a name, that name is underlined, and it alone: no card is raised around it.
        for _ in range(stops.index(board.Focus("crew", "greta")) + 1):
            await pilot.press("tab")
        await pilot.pause()
        assert app.board.focus == board.Focus("crew", "greta")
        strips = [s for s in app.screen._compositor.render_strips()][2:7]
        under = {part.text.strip() for strip in strips for part in strip if part.style and part.style.underline}
        assert under == {"greta"}
        assert board.DIM.lower() in {ink for ink, _ in colors(app, "Mine 0")}              # #300 waits on elif
        assert board.DIM.lower() not in {ink for ink, _ in colors(app, "Mine 2")}          # #302 waits on greta


@drive
async def test_on_a_narrow_board_names_drop_out_the_viewers_agents_stay_and_the_focus_keeps_to_what_shows(isolated):
    a_crowded_board()
    app = main.Frolic()
    async with app.run_test(size=(76, 46)) as pilot:
        await settle(app, pilot)
        app.reported = CROWD
        app.draw()
        await pilot.pause(0.3)
        rows = strip_rows(app)
        assert sum(row.split("│")[0].count("agent-frolic") for row in rows[1:]) == 11 and "folded" not in "\n".join(rows)
        names = [part.strip() for part in (row.split("│")[2] for row in rows)]
        assert names == ["elif", "farid", "greta", "hugo", "3 more"]
        assert app.board.on_strip == {"djp3", "mira", "elif", "farid", "greta", "hugo"}
        stops = await steps_of(app, pilot)
        assert [f.human for f in stops if f.kind == "crew"] == ["djp3", "mira", "elif", "farid", "greta", "hugo"]
        # In a wider window the focus goes to jonas. Narrowed again, jonas keeps a place for as long as it is on him.
        await pilot.resize_terminal(150, 46)
        await pilot.pause(0.3)
        await pilot.press("shift+tab", "shift+tab")                                      # everyone → bob → jonas
        await pilot.pause()
        assert app.board.focus == board.Focus("crew", "jonas")
        await pilot.resize_terminal(76, 46)
        await pilot.pause(0.3)
        names = [part.strip() for part in (row.split("│")[2] for row in strip_rows(app))]
        assert names == ["elif", "farid", "greta", "jonas", "3 more"] and app.board.focus == board.Focus("crew", "jonas")
        # With no room for any name, the focus on one is let go, and the edge says there is more.
        await pilot.resize_terminal(62, 46)
        await pilot.pause(0.3)
        rows = strip_rows(app)
        assert app.board.focus == board.Focus() and app.board.on_strip == {"djp3", "mira"}
        assert rows[0].rstrip().endswith("…") and "jonas" not in "\n".join(rows)
        assert sum(row.split("│")[0].count("agent-frolic") for row in rows[1:]) == 11     # and still not folded
