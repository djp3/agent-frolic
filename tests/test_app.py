# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""The setup screen, driven end to end with stand-ins for GitHub and ngrok. The Keychain,
the settings and the repository list are the in-memory / temporary ones from conftest."""
import asyncio
import io
import json
import logging
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from textual.widgets import Input

import keychain
import main
from githubapi import GitHubError, Team
from main import open_page as real_open_page       # conftest swaps main.open_page for a stand-in
from main import copy_text as real_copy_text       # and main.copy_text: no test touches the real clipboard
from picture import counts
from repos import RepoStore
from standins import (AUTHTOKEN, TOKEN, FakeGitHub, FakePulls, FakeTunnel, age, drive, fill, screen, settle,
                      to_setup)
from test_pulls import node, review
from settings import Settings
from webhook import Delivery, sign


@drive
async def test_a_pasted_token_is_checked_stored_and_only_ever_shown_masked(isolated):
    app = main.Frolic()
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(app, pilot)
        assert "none yet" in screen(app) and keychain.get(keychain.GITHUB_TOKEN) is None
        await pilot.press("t")
        await pilot.pause()
        assert app.screen.query_one("#token", Input).password                 # typed out of sight
        await fill(app, pilot, token=TOKEN)
        assert keychain.get(keychain.GITHUB_TOKEN) == TOKEN
        shown = screen(app)
        assert "signed in as djp3" in shown and "ghp_…6789" in shown and "scopes: repo, read:org" in shown
        assert TOKEN not in shown and TOKEN not in repr(vars(app))             # not on screen, not kept on the app


@drive
async def test_a_refused_token_is_not_stored(isolated):
    app = main.Frolic()
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(app, pilot)
        await pilot.press("t")
        await pilot.pause()
        await fill(app, pilot, token="ghp_bad" + "x" * 33)
        assert keychain.get(keychain.GITHUB_TOKEN) is None
        assert "did not accept the token" in screen(app)


@drive
async def test_a_replacement_token_that_fails_leaves_the_stored_one_in_use(isolated):
    """A mistyped new token, or one pasted while GitHub is out of reach, does not switch the
    app off: the token in the Keychain still stands, and the log says why nothing changed."""
    keychain.put(keychain.GITHUB_TOKEN, TOKEN)
    RepoStore().add("djp3/agent-frolic")
    app = main.Frolic()
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        await to_setup(app, pilot)
        for paste, unreachable, said in (("ghp_bad" + "x" * 33, 0, "did not accept the token"),
                                         ("ghp_" + "n" * 36, 1, "could not be reached")):
            FakeGitHub.unreachable = unreachable
            await pilot.press("t")
            await pilot.pause()
            await fill(app, pilot, token=paste)
            assert (app.token_state, app.identity.login, app.token_mask) == ("ok", "djp3", "ghp_…6789")
            assert keychain.get(keychain.GITHUB_TOKEN) == TOKEN
            last = app.lines[-1][1]
            assert said in last and "The one in your Keychain is still in use" in last
        FakePulls.asked.clear()
        age(app)                                                 # and the timer still runs
        app.tick()
        await settle(app, pilot)
        assert FakePulls.asked == ["djp3/agent-frolic"]


@drive
async def test_the_stored_token_is_checked_at_start_and_can_be_forgotten(isolated):
    keychain.put(keychain.GITHUB_TOKEN, TOKEN)
    app = main.Frolic()
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(app, pilot)
        await to_setup(app, pilot)
        assert "signed in as djp3" in screen(app)
        await pilot.press("f")
        await pilot.pause()
        await pilot.press("y")
        await settle(app, pilot)
        assert keychain.get(keychain.GITHUB_TOKEN) is None and "none yet" in screen(app)


@drive
async def test_a_stored_token_github_could_not_be_reached_for_is_checked_again_until_it_is(isolated):
    """The app opened before the network was up: it keeps trying, and once GitHub answers
    everything starts as it does at launch."""
    keychain.put(keychain.GITHUB_TOKEN, TOKEN)
    RepoStore().add("djp3/agent-frolic")
    FakeGitHub.unreachable = 2
    app = main.Frolic()
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        assert app.token_state == "unchecked" and FakePulls.asked == []
        assert "GitHub could not be reached to check the token" in screen(app)      # the board says so

        app.tick()                                                # not yet time to try again
        await settle(app, pilot)
        assert FakeGitHub.unreachable == 1

        app.token_tried_at -= main.Frolic.TOKEN_RETRY_S           # half a minute later, still offline
        app.tick()
        await settle(app, pilot)
        assert app.token_state == "unchecked" and FakeGitHub.unreachable == 0
        await to_setup(app, pilot)
        assert "trying again every 30 s" in screen(app)

        app.token_tried_at -= main.Frolic.TOKEN_RETRY_S           # and later still, back online
        app.tick()
        await settle(app, pilot)
        assert app.token_state == "ok" and "signed in as djp3" in screen(app)
        assert FakePulls.asked == ["djp3/agent-frolic"]           # read as at launch


@drive
async def test_a_connection_cut_off_mid_reply_does_not_stop_the_app(isolated, monkeypatch):
    """The real GitHub client, over a network that breaks while the first reply arrives:
    the app stays open, says the token is not checked yet, and recovers by itself."""
    import http.client

    from githubapi import GitHub

    calls = []

    def network(request, timeout):
        calls.append(request.full_url)
        if len(calls) == 1:
            raise http.client.IncompleteRead(b'{"lo', 40)
        if request.full_url.endswith("/user"):
            return 200, {"x-oauth-scopes": "repo"}, b'{"login": "djp3"}'
        return 200, {}, b'{"full_name": "djp3/agent-frolic", "default_branch": "main"}'

    monkeypatch.setattr(main, "GitHub", lambda token: GitHub(token, network))
    keychain.put(keychain.GITHUB_TOKEN, TOKEN)
    app = main.Frolic()
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        assert app.is_running and app.token_state == "unchecked"
        app.token_tried_at -= main.Frolic.TOKEN_RETRY_S
        app.tick()
        await settle(app, pilot)
        assert app.token_state == "ok" and app.identity.login == "djp3"


@drive
async def test_u_checks_an_unchecked_token_at_once(isolated):
    keychain.put(keychain.GITHUB_TOKEN, TOKEN)
    FakeGitHub.unreachable = 1
    app = main.Frolic()
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        assert app.token_state == "unchecked"
        await pilot.press("u")
        await settle(app, pilot)
        assert app.token_state == "ok"


@drive
async def test_repositories_are_added_shown_hidden_and_removed(isolated):
    keychain.put(keychain.GITHUB_TOKEN, TOKEN)
    app = main.Frolic()
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(app, pilot)
        await to_setup(app, pilot)
        await pilot.press("a")
        await pilot.pause()
        await fill(app, pilot, repo="not a repo")                             # refused in the dialog
        assert isinstance(app.screen, main.Ask) and "owner/name" in screen(app)
        await fill(app, pilot, repo="https://github.com/djp3/agent-frolic.git")
        await pilot.press("a")
        await pilot.pause()
        await fill(app, pilot, repo="Acme/ledger-api")
        await pilot.press("a")
        await pilot.pause()
        await fill(app, pilot, repo="djp3/missing-repo")
        assert [(r.name, r.shown, r.private, r.admin) for r in RepoStore().repos] == [
            ("djp3/agent-frolic", True, False, True), ("Acme/ledger-api", True, True, False)]
        shown = screen(app)
        assert "you administer it" in shown and "read only for you" in shown
        assert "djp3/missing-repo: No such repository" in shown

        await pilot.press("space")                                            # the last one added is selected
        await pilot.pause()
        assert [r.shown for r in RepoStore().repos] == [True, False]
        await pilot.press("up", "x")
        await pilot.pause()
        await pilot.press("y")
        await settle(app, pilot)
        assert [r.name for r in RepoStore().repos] == ["Acme/ledger-api"]


@drive
async def test_the_chosen_repository_moves_up_and_down_the_list_and_the_board_follows(isolated):
    keychain.put(keychain.GITHUB_TOKEN, TOKEN)
    store = RepoStore()
    for name in ("djp3/agent-frolic", "Acme/ledger-api", "Acme/web-console"):
        store.add(name)
    app = main.Frolic()
    async with app.run_test(size=(150, 40)) as pilot:
        await settle(app, pilot)
        await to_setup(app, pilot)
        assert "moves the chosen one" in screen(app)
        await pilot.press("down", "down", "shift+up")               # the third one, up a place
        await pilot.pause()
        assert [r.name for r in RepoStore().repos] == ["djp3/agent-frolic", "Acme/web-console", "Acme/ledger-api"]
        assert app.selected().name == "Acme/web-console"            # the choice goes with it
        await pilot.press("[", "[")                                 # to the top, and no further
        await pilot.pause()
        assert [r.name for r in RepoStore().repos] == ["Acme/web-console", "djp3/agent-frolic", "Acme/ledger-api"]
        await pilot.press("shift+down", "]")
        await pilot.pause()
        assert [r.name for r in RepoStore().repos] == ["djp3/agent-frolic", "Acme/ledger-api", "Acme/web-console"]
        shown = screen(app)
        assert shown.index("djp3/agent-frolic") < shown.index("Acme/ledger-api") < shown.index("Acme/web-console")
        await pilot.press("[", "s")                                 # back on the board, in the new order
        await settle(app, pilot)
        assert app.board.pic.names == ["djp3/agent-frolic", "Acme/web-console", "Acme/ledger-api"]


@drive
async def test_forgetting_the_token_forgets_what_it_read_and_closes_the_tunnel(isolated):
    keychain.put(keychain.GITHUB_TOKEN, TOKEN)
    keychain.put(keychain.NGROK_AUTHTOKEN, AUTHTOKEN)
    RepoStore().add("djp3/agent-frolic", admin=True)
    settings = Settings.load()
    settings.webhooks, settings.ngrok_domain = True, "frolic.ngrok-free.dev"
    settings.save()
    FakePulls.open_ = {"djp3/agent-frolic": [node(12, "client", title="GraphQL client")]}
    app = main.Frolic()
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        assert app.pulls and app.tunnel_state == "open" and app.receiver is not None
        tunnel = app.tunnel
        late = app.pulls["djp3/agent-frolic"]                     # a reading still on its way

        await to_setup(app, pilot)
        await pilot.press("f")
        await pilot.pause()
        await pilot.press("y")
        await settle(app, pilot)
        assert app.pulls == {} and app.news is None
        assert (app.tunnel, app.receiver, app.tunnel_state) == (None, None, "off") and not tunnel.running
        assert Settings.load().webhooks                           # the setting stays
        app.pulls_known("djp3/agent-frolic", late, app.token_used)
        assert app.pulls == {}                                     # dropped: there is no token
        await pilot.press("s")
        await settle(app, pilot)
        assert "No GitHub token yet" in screen(app) and "GraphQL client" not in screen(app)

        await pilot.press("s", "t")                                # a token again: it all comes back
        await pilot.pause()
        await fill(app, pilot, token=TOKEN)
        assert app.pulls and app.tunnel_state == "open"


@drive
async def test_a_poll_of_zero_minutes_in_the_file_does_not_poll_every_second(isolated, tmp_path):
    keychain.put(keychain.GITHUB_TOKEN, TOKEN)
    RepoStore().add("djp3/agent-frolic")
    (tmp_path / "settings.json").write_text(json.dumps({"poll_minutes": 0}))
    app = main.Frolic()
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        assert FakePulls.asked == ["djp3/agent-frolic"]           # read at start
        app.tick()
        await settle(app, pilot)
        assert FakePulls.asked == ["djp3/agent-frolic"]           # and not again a second later


@drive
async def test_adding_a_repository_needs_a_token_first(isolated):
    app = main.Frolic()
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(app, pilot)
        await pilot.press("a")
        await pilot.pause()
        assert not isinstance(app.screen, main.Ask) and "add a GitHub token first" in screen(app)


@drive
async def test_webhooks_open_a_tunnel_register_hooks_and_show_what_arrives(isolated):
    keychain.put(keychain.GITHUB_TOKEN, TOKEN)
    store = RepoStore()
    store.add("djp3/agent-frolic", admin=True)
    store.add("Acme/ledger-api", private=True, admin=False)
    store.add("djp3/hidden-one", admin=True)
    store.show("djp3/hidden-one", False)

    app = main.Frolic()
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(app, pilot)
        await to_setup(app, pilot)
        await pilot.press("w")
        await pilot.pause()
        await fill(app, pilot, authtoken=AUTHTOKEN, domain="https://Frolic.ngrok-free.dev/")

        assert keychain.get(keychain.NGROK_AUTHTOKEN) == AUTHTOKEN
        assert Settings.load().webhooks and Settings.load().ngrok_domain == "frolic.ngrok-free.dev"
        (domain, port, used) = FakeTunnel.opened[0]
        assert (domain, used) == ("frolic.ngrok-free.dev", AUTHTOKEN) and port == app.receiver.port

        # one hook, on the shown repository we administer, pointing at the tunnel, signed with our secret
        (hook,) = FakeGitHub.hooks_made.values()
        assert (hook.repo, hook.url) == ("djp3/agent-frolic", "https://frolic.ngrok-free.dev/hook")
        assert hook.secret == keychain.get(keychain.WEBHOOK_SECRET)
        assert RepoStore().get("djp3/agent-frolic").hook_id == hook.id
        shown = screen(app)
        assert f"webhook #{hook.id} registered" in shown
        assert "no webhook: you do not administer this repository" in shown
        assert "reaches this app on 127.0.0.1" in shown
        for secret in (TOKEN, AUTHTOKEN, hook.secret):
            assert secret not in shown and secret not in repr(vars(app))

        # a delivery GitHub signed shows in the log; one it did not sign is counted and ignored
        body = json.dumps({"action": "opened", "repository": {"full_name": "djp3/agent-frolic"}}).encode()

        def post(signature):
            request = urllib.request.Request(
                f"http://127.0.0.1:{app.receiver.port}/hook", data=body,
                headers={"X-GitHub-Event": "pull_request", "X-GitHub-Delivery": signature[-8:],
                         "X-Hub-Signature-256": signature})
            try:
                return urllib.request.urlopen(request, timeout=5).status
            except urllib.error.HTTPError as e:
                return e.code

        assert await asyncio.to_thread(post, sign(hook.secret, body)) == 204
        assert await asyncio.to_thread(post, sign("someone else's secret", body)) == 401
        await settle(app, pilot)
        app.draw()                                         # the counts refresh once a second; do it now
        await pilot.pause()
        shown = screen(app)
        assert "webhook · pull_request opened · djp3/agent-frolic" in shown
        assert "1 accepted · 1 refused" in shown

        # turning them off closes the tunnel and deletes the hook this app made
        await pilot.press("W")
        await pilot.pause()
        await pilot.press("y")
        await settle(app, pilot)
        assert FakeGitHub.deleted == [("djp3/agent-frolic", hook.id)] and app.receiver is None
        assert not Settings.load().webhooks and RepoStore().get("djp3/agent-frolic").hook_id == 0


@drive
async def test_a_refused_authtoken_leaves_nothing_running(isolated):
    keychain.put(keychain.GITHUB_TOKEN, TOKEN)
    app = main.Frolic()
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(app, pilot)
        await to_setup(app, pilot)
        await pilot.press("w")
        await pilot.pause()
        await fill(app, pilot, authtoken="bad" + AUTHTOKEN, domain="frolic.ngrok-free.dev")
        assert "ngrok did not accept the authtoken" in screen(app)
        assert app.receiver is None and app.tunnel is None and FakeGitHub.hooks_made == {}


@drive
async def test_drawing_the_setup_never_reads_the_keychain(isolated, monkeypatch):
    """The setup redraws every second. A Keychain read there could hold the screen up while
    macOS asks, and one the user refused would be asked again every second."""
    keychain.put(keychain.GITHUB_TOKEN, TOKEN)
    settings = Settings.load()
    settings.webhooks, settings.ngrok_domain = True, "frolic.ngrok-free.dev"
    settings.save()
    asked, read = [], isolated.get_password

    def get_password(service, name):
        asked.append(name)
        if name == keychain.NGROK_AUTHTOKEN:
            raise RuntimeError("the user clicked Deny")
        return read(service, name)

    monkeypatch.setattr(isolated, "get_password", get_password)
    app = main.Frolic()
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(app, pilot)
        await to_setup(app, pilot)
        for _ in range(5):
            app.draw()
            await pilot.pause()
        assert asked.count(keychain.NGROK_AUTHTOKEN) == 1             # once, when the tunnel was to open
        assert "authtoken unreadable" in screen(app)


def webhooks_on():
    """A token, an authtoken and a domain stored, webhooks on, one repository we administer."""
    keychain.put(keychain.GITHUB_TOKEN, TOKEN)
    keychain.put(keychain.NGROK_AUTHTOKEN, AUTHTOKEN)
    settings = Settings.load()
    settings.webhooks, settings.ngrok_domain = True, "frolic.ngrok-free.dev"
    settings.save()
    RepoStore().add("djp3/agent-frolic", admin=True)


async def until(pilot, done, seconds=5.0):
    """Let the app run until `done()`: settle() cannot, while a worker is held."""
    end = time.monotonic() + seconds
    while not done():
        assert time.monotonic() < end, "it never happened"
        await pilot.pause(0.02)


async def turn_webhooks_off(app, pilot):
    await to_setup(app, pilot)
    await pilot.press("W")
    await pilot.pause()
    await pilot.press("y")
    await pilot.pause()


@drive
async def test_webhooks_turned_off_while_the_tunnel_opens_stay_off(isolated):
    webhooks_on()
    FakeTunnel.gate = threading.Event()                            # the tunnel takes its time to open
    app = main.Frolic()
    async with app.run_test(size=(120, 40)) as pilot:
        await until(pilot, lambda: FakeTunnel.made)
        await turn_webhooks_off(app, pilot)
        FakeTunnel.gate.set()
        await settle(app, pilot)
        assert [t.running for t in FakeTunnel.made] == [False]       # what it opened, it closed
        assert (app.tunnel, app.receiver, app.tunnel_state) == (None, None, "off")
        assert FakeGitHub.hooks_made == {}


@drive
async def test_webhooks_turned_off_while_the_address_is_checked_are_not_registered(isolated, monkeypatch):
    webhooks_on()
    checking, answer = threading.Event(), threading.Event()

    def wait_reachable(url, nonce, **how):
        checking.set()
        return answer.wait(5)

    monkeypatch.setattr(main, "wait_reachable", wait_reachable)
    app = main.Frolic()
    async with app.run_test(size=(120, 40)) as pilot:
        await until(pilot, checking.is_set)
        await turn_webhooks_off(app, pilot)
        answer.set()                                               # the address does lead back, too late
        await settle(app, pilot)
        assert [t.running for t in FakeTunnel.made] == [False]
        assert (app.tunnel, app.receiver, app.tunnel_state) == (None, None, "off")
        assert FakeGitHub.hooks_made == {}                         # none made again on GitHub
        assert "tunnel open" not in screen(app)


@drive
async def test_webhooks_started_twice_in_a_row_leave_one_tunnel(isolated):
    webhooks_on()
    FakeTunnel.gate = threading.Event()
    app = main.Frolic()
    async with app.run_test(size=(120, 40)) as pilot:
        await until(pilot, lambda: FakeTunnel.made)
        await to_setup(app, pilot)
        await pilot.press("r")                                     # again, while the first is opening
        await pilot.pause()
        FakeTunnel.gate.set()
        await settle(app, pilot)
        running = [t for t in FakeTunnel.made if t.running]
        assert len(running) == 1 and app.tunnel is running[0] and app.tunnel_state == "open"
        assert len(FakeGitHub.hooks_made) == 1


@drive
async def test_a_delivery_is_answered_without_waiting_for_the_screen(isolated, monkeypatch):
    """GitHub gives a delivery ten seconds; the screen can be busy for longer than that."""
    webhooks_on()
    app = main.Frolic()
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(app, pilot)
        assert app.tunnel_state == "open"
        handled = []

        def delivered(delivery):                                    # a screen slow to get to it
            handled.append(delivery)
            time.sleep(2)

        monkeypatch.setattr(app, "delivered", delivered)
        body = json.dumps({"action": "opened", "repository": {"full_name": "djp3/agent-frolic"}}).encode()
        headers = {"X-GitHub-Event": "pull_request", "X-GitHub-Delivery": "d-9",
                   "X-Hub-Signature-256": sign(keychain.get(keychain.WEBHOOK_SECRET), body)}

        def post():
            began = time.monotonic()
            request = urllib.request.Request(f"http://127.0.0.1:{app.receiver.port}/hook", data=body, headers=headers)
            with urllib.request.urlopen(request, timeout=10) as reply:
                return reply.status, time.monotonic() - began

        status, took = await asyncio.to_thread(post)
        assert status == 204 and took < 1.0                         # not the screen's two seconds
        await settle(app, pilot)
        assert [d.id for d in handled] == ["d-9"]                   # and it was handed on all the same


def a_clock(monkeypatch):
    """The app's time.time, held still until the test moves it on."""
    now = [time.time()]
    monkeypatch.setattr(main, "time", SimpleNamespace(time=lambda: now[0], monotonic=time.monotonic,
                                                      localtime=time.localtime, strftime=time.strftime))
    return now


def check_run(n):
    return Delivery("check_run", f"d-{n}", "djp3/agent-frolic", "completed")


@drive
async def test_a_burst_of_deliveries_is_read_once_now_and_once_when_its_turn_comes(isolated, monkeypatch):
    keychain.put(keychain.GITHUB_TOKEN, TOKEN)
    RepoStore().add("djp3/agent-frolic", admin=True)
    app = main.Frolic()
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(app, pilot)
        now = a_clock(monkeypatch)
        FakePulls.asked.clear()
        for n in range(5):                                          # a CI run reporting, one check at a time
            app.delivered(check_run(n))
            await settle(app, pilot)
        assert FakePulls.asked == ["djp3/agent-frolic"]             # one read now; the rest wait their turn
        now[0] += app.WEBHOOK_SOONEST_S + 1
        app.tick()
        await settle(app, pilot)
        assert FakePulls.asked == ["djp3/agent-frolic"] * 2         # and are read together
        app.tick()
        await settle(app, pilot)
        assert len(FakePulls.asked) == 2


@drive
async def test_a_costly_repository_is_read_for_deliveries_only_as_often_as_its_share_allows(isolated,
                                                                                            monkeypatch):
    keychain.put(keychain.GITHUB_TOKEN, TOKEN)
    RepoStore().add("djp3/agent-frolic", admin=True)
    FakePulls.costs = {"djp3/agent-frolic": (300, 5000)}            # six pages of fifty, say
    Settings(poll_minutes=60).save()                                # no poll comes due meanwhile
    app = main.Frolic()
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(app, pilot)
        now = a_clock(monkeypatch)
        FakePulls.asked.clear()
        app.delivered(check_run(1))
        await settle(app, pilot)
        now[0] += 60
        app.delivered(check_run(2))
        await settle(app, pilot)
        assert FakePulls.asked == ["djp3/agent-frolic"]             # not again a minute later
        gap = 300 * 3600 / (5000 * app.WEBHOOK_SHARE)               # half the allowance: 432 seconds
        assert app.seconds_a_point("djp3/agent-frolic") * 300 == pytest.approx(gap)
        now[0] += gap - 60 - 1
        app.tick()
        await settle(app, pilot)
        assert len(FakePulls.asked) == 1
        now[0] += 2
        app.tick()
        await settle(app, pilot)
        assert len(FakePulls.asked) == 2                            # its turn
        RepoStore().add("acme/web-console")                         # two repositories share the allowance
        app.store.load()
        assert app.seconds_a_point("djp3/agent-frolic") * 300 == pytest.approx(2 * gap)


# ---- a delivery that names a pull request has that one read, on its own -------------------

OURS = "djp3/agent-frolic"
COPILOT_ASKED = {"nodes": [{"requestedReviewer": {"__typename": "Bot", "login": "copilot-pull-request-reviewer"}}]}


def about(number, n=1, event="pull_request", action="edited", repo=OURS):
    """A delivery that says this pull request changed."""
    return Delivery(event, f"p-{n}", repo, action,
                    {"repository": {"id": 7, "full_name": repo}, "pull_request": {"number": number}})


def now_on_github(number, **facts):
    """GitHub now has this of one of our open pull requests."""
    FakePulls.open_[OURS] = [dict(n, **facts) if n["number"] == number else n for n in FakePulls.open_[OURS]]


def held(app):
    return {pull.number: pull for pull in app.pulls[OURS].pulls}


@drive
async def test_a_delivery_that_names_a_pull_request_has_that_one_read_and_not_the_repository(isolated):
    three_repos()
    app = main.Frolic()
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        FakePulls.asked.clear()
        timers, whole = dict(app.read_at), app.pulls[OURS]
        now_on_github(12, reviewRequests=COPILOT_ASKED)
        now_on_github(13, title="Changed, and nobody said so")
        app.delivered(about(12, action="review_requested"))
        await settle(app, pilot)
        assert FakePulls.asked == [] and FakePulls.asked_alone == [(OURS, 12)]
        assert held(app)[12].asked == ["copilot-pull-request-reviewer"]
        assert held(app)[13].title == "Review state model" and held(app)[13].parent == 12      # as it was held
        assert app.news[1] == "agent-frolic · #12 copilot asked to review · waits on copilot to review"
        assert "copilot" in screen(app)                                                        # and the board
        # It is still the same reading of the repository, with one pull request newer: the
        # timer that reads the whole of it runs on.
        assert app.read_at == timers and app.pulls[OURS].at == whole.at
        app.delivered(about(12, n=2, repo="djp3/hidden-one"))                                  # one not shown:
        app.delivered(about(12, n=3, event="ping"))                                            # and a ping
        await settle(app, pilot)
        assert FakePulls.asked == [] and FakePulls.asked_alone == [(OURS, 12)]


@drive
async def test_what_github_sends_about_a_pull_request_through_the_tunnel_has_that_one_read(isolated):
    three_repos()
    keychain.put(keychain.NGROK_AUTHTOKEN, AUTHTOKEN)
    Settings(webhooks=True, ngrok_domain="frolic.ngrok-free.dev").save()
    app = main.Frolic()
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        await to_setup(app, pilot)
        assert app.tunnel_state == "open"
        secret = keychain.get(keychain.WEBHOOK_SECRET)
        FakePulls.asked.clear()

        def post():
            body = json.dumps({"action": "submitted", "review": {"state": "commented"},
                               "pull_request": {"number": 13, "title": "Review state model"},
                               "repository": {"id": 7, "full_name": OURS}}).encode()
            request = urllib.request.Request(
                f"http://127.0.0.1:{app.receiver.port}/hook", data=body,
                headers={"X-GitHub-Event": "pull_request_review", "X-GitHub-Delivery": "d1",
                         "X-Hub-Signature-256": sign(secret, body)})
            return urllib.request.urlopen(request, timeout=5).status

        assert await asyncio.to_thread(post) == 204
        await until(pilot, lambda: FakePulls.asked_alone)
        await settle(app, pilot)
        assert FakePulls.asked_alone == [(OURS, 13)] and FakePulls.asked == []


@drive
async def test_one_that_closed_leaves_the_board_and_one_just_opened_joins_its_stack(isolated):
    three_repos()
    app = main.Frolic()
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        FakePulls.asked.clear()
        FakePulls.open_[OURS] = [n for n in FakePulls.open_[OURS] if n["number"] != 13]        # merged
        app.delivered(about(13, action="closed"))
        await settle(app, pilot)
        assert list(held(app)) == [12] and app.pulls[OURS].total == 1
        assert "djp3/agent-frolic: 1 open pull request" in [text for _, text, _ in app.lines]
        FakePulls.open_[OURS].append(node(14, "badges", base="client", title="Ticket badges"))
        app.delivered(about(14, n=2, action="opened"))              # moments later: it waits for nothing
        await settle(app, pilot)
        assert list(held(app)) == [12, 14] and held(app)[14].parent == 12 and held(app)[12].children == [14]
        assert "Ticket badges" in screen(app) and "Review state model" not in screen(app)
        assert FakePulls.asked == [] and FakePulls.asked_alone == [(OURS, 13), (OURS, 14)]


@drive
async def test_with_more_open_than_are_held_the_count_is_what_github_says_with_each_one_read(isolated):
    """One that is not held may have been open all along; one that closed may never have been
    held. The count does not move with who joins and who leaves what is held."""
    three_repos()
    FakePulls.more[OURS] = 398                                      # 400 open: more than the app reads
    app = main.Frolic()
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        assert app.pulls[OURS].total == 400 and list(held(app)) == [12, 13]
        FakePulls.open_[OURS].append(node(7, "old-one", title="Open all along"))
        FakePulls.more[OURS] = 397                                  # it was one of the 398: 400 still
        app.delivered(about(7))
        await settle(app, pilot)
        assert sorted(held(app)) == [7, 12, 13] and app.pulls[OURS].total == 400     # held now; no more are open
        FakePulls.more[OURS] = 396                                  # another, never held, was merged
        app.delivered(about(999, n=2, action="closed"))
        await settle(app, pilot)
        assert sorted(held(app)) == [7, 12, 13] and app.pulls[OURS].total == 399
        assert FakePulls.asked_alone == [(OURS, 7), (OURS, 999)]


@drive
async def test_a_burst_about_one_pull_request_is_read_once_now_and_once_when_it_is_over(isolated, monkeypatch):
    three_repos()
    app = main.Frolic()
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        now = a_clock(monkeypatch)
        for n in range(5):                                          # a CI run reporting, one check at a time
            app.delivered(Delivery("check_run", f"c-{n}", OURS, "completed", {
                "repository": {"id": 7}, "check_run": {"pull_requests": [{"number": 13, "base": {"repo": {"id": 7}}}]}}))
            await settle(app, pilot)
        assert FakePulls.asked_alone == [(OURS, 13)]                # one read now; the rest wait their turn
        now[0] += app.WEBHOOK_SOONEST_S - 1
        app.tick()
        await settle(app, pilot)
        assert len(FakePulls.asked_alone) == 1
        now[0] += 2
        app.tick()
        await settle(app, pilot)
        assert FakePulls.asked_alone == [(OURS, 13)] * 2            # and are read together
        now[0] += 60
        app.tick()
        await settle(app, pilot)
        assert len(FakePulls.asked_alone) == 2 and FakePulls.asked.count(OURS) == 1    # only the read at the start


@drive
async def test_each_pull_request_waits_its_own_turn_and_too_many_are_read_as_the_repository(isolated, monkeypatch):
    three_repos()
    app = main.Frolic()
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        now = a_clock(monkeypatch)
        FakePulls.asked.clear()
        app.delivered(about(12))
        await settle(app, pilot)
        app.delivered(about(13, n=2))                               # another one: it does not wait for 12's turn
        await settle(app, pilot)
        assert FakePulls.asked_alone == [(OURS, 12), (OURS, 13)]
        for n, number in enumerate((13, 12, 13), 3):                # both again, seconds after they were read
            app.delivered(about(number, n=n))
        await settle(app, pilot)
        assert len(FakePulls.asked_alone) == 2 and app.pull_wanted == {OURS: [13, 12]}
        now[0] += app.WEBHOOK_SOONEST_S + 1
        app.tick()
        await settle(app, pilot)
        assert FakePulls.asked_alone[2:] == [(OURS, 12), (OURS, 13)] and FakePulls.asked == []     # in one go
        gate = FakeGitHub.gates[("pull", TOKEN)] = threading.Event()
        app.delivered(about(20, n=20))
        await until(pilot, lambda: ("pull", TOKEN) in FakeGitHub.waiting)
        for number in range(21, 21 + app.ALONE_MOST):               # while that one is read, as many are named
            app.delivered(about(number, n=number))                  # as are read one by one,
        assert len(app.pull_wanted[OURS]) == app.ALONE_MOST
        app.delivered(about(99, n=99))                              # and one more
        assert app.pull_wanted == {}
        gate.set()
        await settle(app, pilot)
        assert FakePulls.asked_alone[4:] == [(OURS, 20)] and app.pull_wanted == {}
        assert FakePulls.asked == [OURS]                            # the repository, for all the rest


@drive
async def test_a_pull_request_is_read_on_its_own_as_soon_as_the_ration_covers_it(isolated, monkeypatch):
    """A read of the whole repository uses the whole ration, and the next waits for it to be
    whole again. A pull request on its own needs a point of it, back in seconds."""
    keychain.put(keychain.GITHUB_TOKEN, TOKEN)
    RepoStore().add(OURS, admin=True)
    FakePulls.open_ = {OURS: [node(12, "client", title="GraphQL client")]}
    FakePulls.costs, FakePulls.alone_costs = {OURS: (50, 5000)}, {OURS: 1}
    Settings(poll_minutes=60).save()                                # no poll comes due meanwhile
    app = main.Frolic()
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(app, pilot)
        now = a_clock(monkeypatch)
        point = app.seconds_a_point(OURS)
        assert point == pytest.approx(3600 / 2500)                  # half of 5000 an hour, all this repository's
        FakePulls.asked.clear()
        app.delivered(Delivery("push", "d-1", OURS, ""))            # names no pull request: the whole of it
        await settle(app, pilot)
        assert FakePulls.asked == [OURS]
        app.delivered(about(12))                                    # the ration is spent: not yet
        await settle(app, pilot)
        assert FakePulls.asked_alone == []
        now[0] += point - 0.1
        app.tick()
        await settle(app, pilot)
        assert FakePulls.asked_alone == []
        now[0] += 0.2                                               # one point is back
        app.tick()
        await settle(app, pilot)
        assert FakePulls.asked_alone == [(OURS, 12)]
        app.delivered(Delivery("push", "d-2", OURS, ""))            # the whole repository again:
        now[0] += 50 * point - 0.2                                  # its ration would have been whole by now,
        app.tick()                                                  # but for the point the one on its own took
        await settle(app, pilot)
        assert FakePulls.asked == [OURS]
        now[0] += point + 0.2
        app.tick()
        await settle(app, pilot)
        assert FakePulls.asked == [OURS] * 2 and FakePulls.asked_alone == [(OURS, 12)]


@drive
async def test_as_many_of_those_waiting_are_read_as_the_ration_covers_and_the_first_named_first(isolated, monkeypatch):
    """One waiting is not held up until there is a point for each of them."""
    keychain.put(keychain.GITHUB_TOKEN, TOKEN)
    RepoStore().add(OURS, admin=True)
    FakePulls.open_ = {OURS: [node(12, "client", title="GraphQL client"),
                              node(13, "review-state", title="Review state model"),
                              node(14, "badges", title="Ticket badges")]}
    FakePulls.costs, FakePulls.alone_costs = {OURS: (50, 5000)}, {OURS: 1}
    Settings(poll_minutes=60).save()                                # no poll comes due meanwhile
    app = main.Frolic()
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(app, pilot)
        now = a_clock(monkeypatch)
        point = app.seconds_a_point(OURS)
        app.delivered(Delivery("push", "d-1", OURS, ""))            # the whole of it: the ration is spent
        await settle(app, pilot)
        for n, number in enumerate((14, 12, 13, 14), 1):
            app.delivered(about(number, n=n))
        await settle(app, pilot)
        assert FakePulls.asked_alone == [] and app.pull_wanted == {OURS: [14, 12, 13]}
        now[0] += point + 0.1                                       # one point has come: one is read
        app.tick()
        await settle(app, pilot)
        assert FakePulls.asked_alone == [(OURS, 14)] and app.pull_wanted == {OURS: [12, 13]}
        app.delivered(about(14, n=5))                               # named again, it goes to the back
        now[0] += 2 * point                                         # two more points: the next two, in one go
        app.tick()
        await settle(app, pilot)
        assert FakePulls.asked_alone[1:] == [(OURS, 12), (OURS, 13)] and app.pull_wanted == {OURS: [14]}
        now[0] += app.WEBHOOK_SOONEST_S
        app.tick()
        await settle(app, pilot)
        assert FakePulls.asked_alone[3:] == [(OURS, 14)] and app.pull_wanted == {}


@drive
async def test_what_a_pull_request_cost_beyond_a_point_is_taken_from_the_ration_too(isolated, monkeypatch):
    keychain.put(keychain.GITHUB_TOKEN, TOKEN)
    RepoStore().add(OURS, admin=True)
    FakePulls.open_ = {OURS: [node(12, "client", title="GraphQL client")]}
    FakePulls.costs, FakePulls.alone_costs = {OURS: (50, 5000)}, {OURS: 3}       # its checks fail, say: more to ask
    Settings(poll_minutes=60).save()
    app = main.Frolic()
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(app, pilot)
        now = a_clock(monkeypatch)
        app.delivered(about(12))
        await settle(app, pilot)
        assert FakePulls.asked_alone == [(OURS, 12)]
        assert app.ration_now(OURS) == pytest.approx(50 - 3)


@drive
async def test_a_read_of_the_repository_that_a_delivery_asked_for_is_charged_what_it_cost_itself(isolated, monkeypatch):
    """The ration is charged before the read, with what the read before it cost. When this
    one costs more (a review grown long, checks that fail: more to ask), the rest is taken
    once it says so, and when less, the difference comes back. The timer's reads and u's are
    not rationed, whatever they cost."""
    keychain.put(keychain.GITHUB_TOKEN, TOKEN)
    RepoStore().add(OURS, admin=True)
    FakePulls.open_ = {OURS: [node(12, "client", title="GraphQL client")]}
    FakePulls.costs, FakePulls.alone_costs = {OURS: (50, 5000)}, {OURS: 1}
    Settings(poll_minutes=60).save()                                # no poll comes due meanwhile
    app = main.Frolic()
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(app, pilot)
        now = a_clock(monkeypatch)
        point = app.seconds_a_point(OURS)
        FakePulls.costs[OURS] = (56, 5000)                          # this time there is more to ask
        app.delivered(Delivery("push", "d-1", OURS, ""))
        await settle(app, pilot)
        assert app.pulls[OURS].cost == 56 and app.hook_aside == {}
        assert app.ration_now(OURS) == pytest.approx(-6)            # 50 were taken before it, and 6 since
        app.delivered(about(12))                                    # so one on its own waits for seven
        now[0] += 7 * point - 0.1                                   # points to come, not for one
        app.tick()
        await settle(app, pilot)
        assert FakePulls.asked_alone == []
        now[0] += 0.2
        app.tick()
        await settle(app, pilot)
        assert FakePulls.asked_alone == [(OURS, 12)]
        now[0] += 60 * point
        assert app.ration_now(OURS) == 56                           # whole again, and a whole one is 56 now
        FakePulls.costs[OURS] = (53, 5000)                          # this time there is less
        app.delivered(Delivery("push", "d-2", OURS, ""))
        await settle(app, pilot)
        assert app.ration_now(OURS) == pytest.approx(3)             # 56 taken, 3 back
        FakePulls.costs[OURS] = (70, 5000)
        await pilot.press("u")                                      # not one a delivery asked for
        await settle(app, pilot)
        assert app.pulls[OURS].cost == 70 and app.ration_now(OURS) == pytest.approx(3)


@drive
async def test_a_read_a_delivery_asked_for_that_fails_keeps_what_was_taken_for_it(isolated, monkeypatch):
    keychain.put(keychain.GITHUB_TOKEN, TOKEN)
    RepoStore().add(OURS, admin=True)
    FakePulls.open_ = {OURS: [node(12, "client", title="GraphQL client")]}
    FakePulls.costs = {OURS: (50, 5000)}
    Settings(poll_minutes=60).save()
    app = main.Frolic()
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(app, pilot)
        a_clock(monkeypatch)
        FakePulls.broken[OURS] = "GitHub could not be reached."
        app.delivered(Delivery("push", "d-1", OURS, ""))
        await settle(app, pilot)
        assert OURS in app.pull_trouble and app.hook_aside == {}    # it will not say what it cost
        assert app.ration_now(OURS) == pytest.approx(0)
        del FakePulls.broken[OURS]
        FakePulls.costs[OURS] = (70, 5000)
        await pilot.press("u")                                      # nor does a later read say it for it
        await settle(app, pilot)
        assert app.pulls[OURS].cost == 70 and app.ration_now(OURS) == pytest.approx(0)


@drive
async def test_showing_another_repository_puts_nothing_back_into_a_ration_that_was_spent(isolated, monkeypatch):
    """Each repository's part of the allowance is smaller when one more is shown. What a
    ration holds is the same points after that as before: one just spent covers no read
    because of it."""
    keychain.put(keychain.GITHUB_TOKEN, TOKEN)
    RepoStore().add(OURS, admin=True)
    FakePulls.open_ = {OURS: [node(12, "client", title="GraphQL client")]}
    FakePulls.costs, FakePulls.alone_costs = {OURS: (50, 5000)}, {OURS: 1}
    Settings(poll_minutes=60).save()
    app = main.Frolic()
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(app, pilot)
        now = a_clock(monkeypatch)
        point = app.seconds_a_point(OURS)
        app.delivered(Delivery("push", "d-1", OURS, ""))            # the whole of it: the ration is spent
        await settle(app, pilot)
        assert app.ration_now(OURS) == pytest.approx(0)
        RepoStore().add("acme/web-console")                         # one more to share the allowance with
        app.store.load()
        assert app.seconds_a_point(OURS) == pytest.approx(2 * point)
        assert app.ration_now(OURS) == pytest.approx(0)             # it holds what it held,
        app.delivered(about(12))
        await settle(app, pilot)
        assert FakePulls.asked_alone == []                          # which covers nothing
        now[0] += 2 * point - 0.1
        app.tick()
        await settle(app, pilot)
        assert FakePulls.asked_alone == []
        now[0] += 0.2                                               # a point has come, at the pace of now
        app.tick()
        await settle(app, pilot)
        assert FakePulls.asked_alone == [(OURS, 12)]


@drive
async def test_what_came_into_a_ration_before_the_pace_changed_is_counted_at_the_pace_of_then(isolated, monkeypatch):
    keychain.put(keychain.GITHUB_TOKEN, TOKEN)
    RepoStore().add(OURS, admin=True)
    FakePulls.open_ = {OURS: [node(12, "client", title="GraphQL client")]}
    FakePulls.costs = {OURS: (50, 5000)}
    Settings(poll_minutes=60).save()
    app = main.Frolic()
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(app, pilot)
        now = a_clock(monkeypatch)
        point = app.seconds_a_point(OURS)
        app.delivered(Delivery("push", "d-1", OURS, ""))            # the ration is spent
        await settle(app, pilot)
        now[0] += 10 * point                                        # ten points come, at this pace
        RepoStore().add("acme/web-console")
        app.store.load()
        app.tick()                                                  # the clock counts them, as every second
        await settle(app, pilot)
        now[0] += 10 * point                                        # and five more, at half that pace
        assert app.ration_now(OURS) == pytest.approx(15)
        now[0] += 3600                                              # never more than a whole one
        assert app.ration_now(OURS) == 50


@drive
async def test_one_named_while_the_repository_is_being_read_is_read_when_that_ends(isolated):
    """The read of the whole repository may have begun before it changed."""
    three_repos()
    app = main.Frolic()
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        FakePulls.asked.clear()
        gate = FakeGitHub.gates[("pulls", TOKEN)] = threading.Event()
        FakeGitHub.waiting.clear()
        await pilot.press("u")
        await until(pilot, lambda: ("pulls", TOKEN) in FakeGitHub.waiting)
        app.delivered(about(12))
        await pilot.pause()
        assert FakePulls.asked_alone == [] and app.pull_wanted == {OURS: [12]}
        gate.set()
        await settle(app, pilot)
        assert FakePulls.asked_alone == [(OURS, 12)] and app.pull_wanted == {}


@drive
async def test_one_named_before_the_repository_is_read_comes_with_that_read(isolated, monkeypatch):
    three_repos()
    app = main.Frolic()
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        now = a_clock(monkeypatch)
        app.delivered(about(12))
        await settle(app, pilot)
        app.delivered(about(12, n=2))                               # waits: it was read seconds ago
        await settle(app, pilot)
        assert app.pull_wanted == {OURS: [12]}
        await pilot.press("u")                                      # everything is read now, this one too
        await settle(app, pilot)
        now[0] += app.WEBHOOK_SOONEST_S + 1
        app.tick()
        await settle(app, pilot)
        assert FakePulls.asked_alone == [(OURS, 12)] and app.pull_wanted == {}


@drive
async def test_a_read_of_the_whole_repository_that_began_later_is_not_undone_by_an_older_one_of_a_pull_request(isolated):
    three_repos()
    app = main.Frolic()
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        gate = FakeGitHub.gates[("pull", TOKEN)] = threading.Event()   # its answer is slow in coming
        now_on_github(12, title="As it was when asked")
        app.delivered(about(12))
        await until(pilot, lambda: ("pull", TOKEN) in FakeGitHub.waiting)
        now_on_github(12, title="As it is now")
        await pilot.press("u")
        await until(pilot, lambda: held(app)[12].title == "As it is now")
        gate.set()
        await settle(app, pilot)
        assert held(app)[12].title == "As it is now" and app.pull_reading == {}


@drive
async def test_a_pull_request_that_cannot_be_read_on_its_own_has_the_repository_read_in_its_turn(isolated, monkeypatch):
    three_repos()
    app = main.Frolic()
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        FakePulls.asked.clear()
        FakePulls.alone_broken[OURS] = "GitHub refused the query: no such pull request"
        now_on_github(12, title="Read with the rest")
        app.delivered(about(12))
        await settle(app, pilot)
        assert FakePulls.asked_alone == [(OURS, 12)] and FakePulls.asked == [OURS]
        assert held(app)[12].title == "Read with the rest"
        said = [text for _, text, _ in app.lines]
        assert "djp3/agent-frolic: #12 was not read on its own: GitHub refused the query: no such pull request" in said
        assert OURS not in app.pull_trouble                         # the repository itself is in no trouble


@drive
async def test_with_nothing_held_to_put_it_into_the_repository_is_read(isolated):
    three_repos()
    app = main.Frolic()
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        FakePulls.broken[OURS] = "GitHub could not be reached."
        await pilot.press("u")
        await settle(app, pilot)
        assert OURS in app.pull_trouble
        FakePulls.asked.clear()
        del FakePulls.broken[OURS]
        app.delivered(about(12))                                    # what is held may be long out of date
        await settle(app, pilot)
        assert FakePulls.asked == [OURS] and FakePulls.asked_alone == [] and OURS not in app.pull_trouble


@drive
async def test_a_pull_request_read_under_a_name_github_has_changed_has_the_repository_read(isolated):
    three_repos()
    app = main.Frolic()
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        FakePulls.asked.clear()
        FakePulls.spelled[OURS] = "djp3/agent-revel"
        FakePulls.open_["djp3/agent-revel"] = FakePulls.open_[OURS]
        app.delivered(about(12))
        await settle(app, pilot)
        assert FakePulls.asked[0] == OURS                           # the whole of it, and the list takes the name
        assert app.store.get("djp3/agent-revel") is not None and app.store.get(OURS) is None
        assert "djp3/agent-revel" in app.pulls and OURS not in app.pulls


@drive
async def test_what_was_read_of_a_pull_request_with_the_token_before_is_not_used(isolated):
    three_repos()
    app = main.Frolic()
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        gate = FakeGitHub.gates[("pull", TOKEN)] = threading.Event()
        now_on_github(12, title="Seen with the token before")
        app.delivered(about(12))
        await until(pilot, lambda: ("pull", TOKEN) in FakeGitHub.waiting)
        app.token_used += 1                                         # another token was taken meanwhile
        gate.set()
        await settle(app, pilot)
        assert held(app)[12].title == "GraphQL client" and app.pull_reading == {}


@drive
async def test_a_repository_removed_while_its_pull_request_is_read_stays_removed(isolated):
    three_repos()
    app = main.Frolic()
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        gate = FakeGitHub.gates[("pull", TOKEN)] = threading.Event()
        app.delivered(about(12))
        await until(pilot, lambda: ("pull", TOKEN) in FakeGitHub.waiting)
        app.store.remove(OURS)
        app.forget_repo(OURS)
        gate.set()
        await settle(app, pilot)
        assert OURS not in app.pulls and app.pull_reading == {} and app.pull_wanted == {}
        assert not any(OURS in held for held in (app.ration_held, app.pull_read_at, app.pulls_began))


@drive
async def test_an_answer_from_before_a_repository_left_and_came_back_takes_nothing_of_what_is_read_now(isolated):
    """Removed and added again while one of its pull requests was being read, with another
    being read by the time that answer comes: the answer is from before, and what is kept of
    the read under way is not its to take."""
    three_repos()
    app = main.Frolic()
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        answered, taken = [], app.pulls_read

        def pulls_read(name, run, read, *rest):
            taken(name, run, read, *rest)
            answered.append([one.number for one in read])

        app.pulls_read = pulls_read
        slow = FakeGitHub.gates[("pull", TOKEN)] = threading.Event()   # its answer is slow in coming
        now_on_github(12, title="As it was before")
        app.delivered(about(12))
        await until(pilot, lambda: ("pull", TOKEN) in FakeGitHub.waiting)
        app.store.remove(OURS)                                      # it leaves the list,
        app.forget_repo(OURS)
        now_on_github(12, title="As it is now")
        app.store.add(OURS, admin=True)                             # and is back, and read
        app.update_pulls([OURS], "added")
        await until(pilot, lambda: OURS in app.pulls)
        gate = FakeGitHub.gates[("pull", TOKEN)] = threading.Event()
        FakeGitHub.waiting.clear()
        now_on_github(13, title="Newer still")
        app.delivered(about(13, n=2))                               # now another of them is being read
        await until(pilot, lambda: ("pull", TOKEN) in FakeGitHub.waiting)
        slow.set()                                                  # and the answer from before comes first
        await until(pilot, lambda: answered == [[12]])
        assert held(app)[12].title == "As it is now"                # nothing is put back as it was,
        assert OURS in app.pull_reading                             # and #13 is still being read
        gate.set()
        await settle(app, pilot)
        assert answered == [[12], [13]] and app.pull_reading == {}
        assert held(app)[13].title == "Newer still"                 # its answer is the one used


async def answer_yes(pilot, key):
    await pilot.press(key)
    await pilot.pause()
    await pilot.press("y")
    await pilot.pause()


@drive
async def test_a_repository_removed_while_github_is_asked_about_it_stays_removed(isolated):
    keychain.put(keychain.GITHUB_TOKEN, TOKEN)
    store = RepoStore()
    store.add("djp3/agent-frolic", admin=True)
    store.add("djp3/old-one", admin=True)
    FakeGitHub.gates["djp3/old-one"] = threading.Event()        # its answer is slow in coming
    app = main.Frolic()
    async with app.run_test(size=(120, 40)) as pilot:
        await until(pilot, lambda: "djp3/old-one" in FakeGitHub.waiting)
        await to_setup(app, pilot)
        await pilot.press("down")
        await answer_yes(pilot, "x")
        assert app.store.get("djp3/old-one") is None
        FakeGitHub.gates["djp3/old-one"].set()
        await settle(app, pilot)
        assert app.store.get("djp3/old-one") is None and RepoStore().get("djp3/old-one") is None


@drive
async def test_a_token_forgotten_while_github_checks_it_stays_forgotten(isolated):
    keychain.put(keychain.GITHUB_TOKEN, TOKEN)
    RepoStore().add("djp3/agent-frolic", admin=True)
    FakeGitHub.gates[TOKEN] = threading.Event()                 # GitHub is slow to say whose it is
    app = main.Frolic()
    async with app.run_test(size=(120, 40)) as pilot:
        await until(pilot, lambda: TOKEN in FakeGitHub.waiting)
        await to_setup(app, pilot)
        await answer_yes(pilot, "f")
        FakeGitHub.gates[TOKEN].set()                           # then says it is djp3's
        await settle(app, pilot)
        assert app.token_state == "none" and keychain.get(keychain.GITHUB_TOKEN) is None
        assert FakePulls.asked == [] and "signed in" not in screen(app)


@drive
async def test_of_two_tokens_pasted_one_after_the_other_the_second_is_kept(isolated):
    first, second = TOKEN, TOKEN[:-4] + "4321"
    FakeGitHub.gates[first] = threading.Event()                 # the first one's answer comes last

    async def paste(token):
        await pilot.press("t")
        await pilot.pause()
        app.screen.query_one("#token", Input).value = token
        await pilot.press("enter")
        await pilot.pause()

    app = main.Frolic()
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(app, pilot)
        await paste(first)
        await until(pilot, lambda: first in FakeGitHub.waiting)
        await paste(second)
        await until(pilot, lambda: keychain.get(keychain.GITHUB_TOKEN) == second)
        FakeGitHub.gates[first].set()
        await settle(app, pilot)
        assert keychain.get(keychain.GITHUB_TOKEN) == second
        assert app.token_state == "ok" and app.token_mask == keychain.mask(second)


def three_repos():
    keychain.put(keychain.GITHUB_TOKEN, TOKEN)
    store = RepoStore()
    store.add("djp3/agent-frolic", admin=True)
    store.add("Acme/ledger-api", private=True, admin=False)
    store.add("djp3/hidden-one", admin=True)
    store.show("djp3/hidden-one", False)
    FakePulls.open_ = {
        "djp3/agent-frolic": [
            node(12, "client", title="GraphQL client", reviewDecision="APPROVED",
                 latestOpinionatedReviews={"nodes": [review("bob", "APPROVED", "2026-11-12T10:00:00Z")]}),
            node(13, "review-state", base="client", title="Review state model",
                 reviewRequests={"nodes": [{"requestedReviewer": {"__typename": "User", "login": "carol"}}]},
                 latestOpinionatedReviews={"nodes": [review("dave", "CHANGES_REQUESTED", "2026-11-13T10:00:00Z")]},
                 reviewThreads={"totalCount": 3, "nodes": [{"isResolved": False}, {"isResolved": False},
                                                            {"isResolved": True}]})],
        "acme/ledger-api": [node(341, "export", title="Ledger export", isDraft=True, mergeable="CONFLICTING")],
        "djp3/hidden-one": [node(1, "x")],
    }


@drive
async def test_pull_requests_are_read_at_start_for_the_shown_repositories_and_listed_in_stack_order(isolated):
    three_repos()
    app = main.Frolic()
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        await to_setup(app, pilot)
        assert sorted(FakePulls.asked) == ["Acme/ledger-api", "djp3/agent-frolic"]      # not the hidden one
        assert "2 open" in screen(app) and "1 open" in screen(app)

        await pilot.press("p")
        await pilot.pause()
        shown = screen(app)
        first, second = shown.index("#12 GraphQL client"), shown.index("└ #13 Review state model")
        assert first < second                                                                 # 13 is stacked on 12
        assert "✓ bob" in shown and "✗ dave" in shown and "◔ carol" in shown and "◇2 to resolve" in shown
        assert "#341 Ledger export" in shown and "◌ draft" in shown and "⚠ conflict" in shown
        assert "default branch main" in shown and "#1 Change 1" not in shown

        before = app.sel
        await pilot.press("down", "down")                         # in the list the arrows scroll, not pick
        assert app.sel == before

        FakePulls.open_["acme/ledger-api"] = []                   # the last one there is merged
        await pilot.press("u")
        await settle(app, pilot)
        shown = screen(app)
        head = next(row for row in shown.splitlines() if "Acme/ledger-api" in row and "default branch" in row)
        assert "0 open" in head                                   # its head line says so,
        assert "nothing is open" not in shown                     # and no line under it says so again

        await pilot.press("p")
        await pilot.pause()
        assert "started" in screen(app)                           # back to the log


@drive
async def test_a_repository_renamed_on_github_is_one_entry_under_its_new_name(isolated):
    keychain.put(keychain.GITHUB_TOKEN, TOKEN)
    store = RepoStore()
    store.add("acme/old")
    store.set_hook("acme/old", 7)
    FakeGitHub.renamed = {"acme/old": "Acme/new"}
    FakePulls.open_ = {"acme/old": [node(1, "a")], "acme/new": [node(1, "a")]}   # GraphQL follows it too
    app = main.Frolic()
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        assert [(r.name, r.hook_id) for r in RepoStore().repos] == [("Acme/new", 7)]
        assert list(app.pulls) == ["acme/new"]                    # read once, under the new name
        assert app.board.pic.names == ["Acme/new"]
        assert any(text == "acme/old is now Acme/new on GitHub" for _, text, _ in app.lines)


@drive
async def test_the_list_takes_githubs_capitals_as_soon_as_the_pull_requests_are_read(isolated):
    keychain.put(keychain.GITHUB_TOKEN, TOKEN)
    RepoStore().add("djp3/agent-frolic", admin=True)
    FakePulls.spelled = {"djp3/agent-frolic": "djp3/Agent-Frolic"}
    FakePulls.open_ = {"djp3/agent-frolic": [node(5, "fix", author={"login": "djp3"}, reviewDecision="APPROVED",
                                                  latestOpinionatedReviews={"nodes": [
                                                      review("bob", "APPROVED", "2026-11-14T10:00:00Z")]})]}
    app = main.Frolic()
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        assert [r.name for r in RepoStore().repos] == ["djp3/Agent-Frolic"]
        assert app.board.pic.names == ["djp3/Agent-Frolic"]
        assert "Agent-Frolic ●1" in app.board.top_line().plain             # yours to merge
        row = next(row for row in screen(app).splitlines() if "╪ djp3/Agent-Frolic" in row)
        assert "●1" in row                                                  # the map's row for it, too
        assert not any("is now" in text for _, text, _ in app.lines)        # capitals are not a rename


@drive
async def test_a_renamed_repository_read_before_it_is_checked_again_takes_its_new_name_at_once(isolated):
    keychain.put(keychain.GITHUB_TOKEN, TOKEN)
    store = RepoStore()
    store.add("acme/old")
    store.set_hook("acme/old", 7)
    FakeGitHub.renamed = {"acme/old": "Acme/new"}
    FakeGitHub.gates["acme/old"] = threading.Event()               # checking the list again is slow
    FakePulls.spelled = {"acme/old": "Acme/new"}                    # the pull requests come under the new name
    FakePulls.open_ = {"acme/old": [node(1, "a")]}
    app = main.Frolic()
    async with app.run_test(size=(140, 40)) as pilot:
        await until(pilot, lambda: "acme/new" in app.pulls)
        assert [(r.name, r.hook_id) for r in RepoStore().repos] == [("Acme/new", 7)]
        FakeGitHub.gates["acme/old"].set()
        await settle(app, pilot)
        assert [(r.name, r.hook_id) for r in RepoStore().repos] == [("Acme/new", 7)]
        assert list(app.pulls) == ["acme/new"] and app.board.pic.names == ["Acme/new"]
        assert FakePulls.asked == ["acme/old"]                       # read once
        assert [text for _, text, _ in app.lines if "is now" in text] == ["acme/old is now Acme/new on GitHub"]
        assert sorted(app.read_at) == ["acme/new"]                   # its timer is the new name's,
        FakePulls.asked.clear()                                      # so the clock goes on reading it
        age(app)
        app.tick()
        await settle(app, pilot)
        assert FakePulls.asked == ["Acme/new"] and app.timer("Acme/new")[0] < 0.05


@drive
async def test_b_sets_the_long_lived_branches_and_the_stacks_are_drawn_again_at_once(isolated):
    keychain.put(keychain.GITHUB_TOKEN, TOKEN)
    RepoStore().add("djp3/agent-frolic")                          # from before the list existed
    FakePulls.open_ = {"djp3/agent-frolic": [node(50, "develop", base="main"), node(51, "login-form", base="develop")]}
    app = main.Frolic()
    async with app.run_test(size=(160, 40)) as pilot:
        await settle(app, pilot)
        await to_setup(app, pilot)
        assert RepoStore().repos[0].long_lived == ["main"]        # GitHub's default branch, filled in once
        assert "long-lived: main" in screen(app)
        parents = lambda: {p.number: p.parent for p in app.pulls["djp3/agent-frolic"].pulls}
        assert parents() == {50: 0, 51: 50}                       # develop is not known to be long-lived yet

        await pilot.press("b")
        await pilot.pause()
        assert app.screen.query_one("#branches", Input).value == "main"
        await fill(app, pilot, branches="main, develop")
        assert RepoStore().repos[0].long_lived == ["main", "develop"]
        assert parents() == {50: 0, 51: 0}                        # without reading GitHub again
        assert "long-lived: main, develop" in screen(app)


@drive
async def test_a_webhook_for_a_shown_repository_reads_it_again_and_a_ping_does_not(isolated):
    three_repos()
    keychain.put(keychain.NGROK_AUTHTOKEN, AUTHTOKEN)
    Settings(webhooks=True, ngrok_domain="frolic.ngrok-free.dev").save()
    app = main.Frolic()
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        await to_setup(app, pilot)
        assert app.tunnel_state == "open"
        secret = keychain.get(keychain.WEBHOOK_SECRET)
        FakePulls.asked.clear()

        def post(event, repo, delivery):
            body = json.dumps({"action": "opened", "repository": {"full_name": repo}}).encode()
            request = urllib.request.Request(
                f"http://127.0.0.1:{app.receiver.port}/hook", data=body,
                headers={"X-GitHub-Event": event, "X-GitHub-Delivery": delivery,
                         "X-Hub-Signature-256": sign(secret, body)})
            return urllib.request.urlopen(request, timeout=5).status

        assert await asyncio.to_thread(post, "ping", "djp3/agent-frolic", "d1") == 204
        assert await asyncio.to_thread(post, "pull_request", "djp3/hidden-one", "d2") == 204
        await settle(app, pilot)
        assert FakePulls.asked == []                              # a ping, and a hidden repository: nothing to read
        FakePulls.open_["djp3/agent-frolic"].append(node(14, "badges", title="Ticket badges"))
        assert await asyncio.to_thread(post, "pull_request", "djp3/agent-frolic", "d3") == 204
        await settle(app, pilot)
        assert FakePulls.asked == ["djp3/agent-frolic"]
        await pilot.press("p")
        await pilot.pause()
        assert "#14 Ticket badges" in screen(app) and "3 open" in screen(app)


@drive
async def test_the_poll_reads_every_shown_repository_when_it_is_due_and_u_reads_them_now(isolated):
    three_repos()
    app = main.Frolic()
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        await to_setup(app, pilot)
        FakePulls.asked.clear()
        app.tick()
        await settle(app, pilot)
        assert FakePulls.asked == []                              # not due yet
        age(app)
        app.tick()
        await settle(app, pilot)
        assert sorted(FakePulls.asked) == ["Acme/ledger-api", "djp3/agent-frolic"]
        assert all(time.time() - at < 5 for at in app.read_at.values())    # and their clocks start again
        FakePulls.asked.clear()
        await pilot.press("u")
        await settle(app, pilot)
        assert sorted(FakePulls.asked) == ["Acme/ledger-api", "djp3/agent-frolic"]


@drive
async def test_a_fetch_that_fails_says_why_and_keeps_what_it_had(isolated):
    three_repos()
    app = main.Frolic()
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        await to_setup(app, pilot)
        FakePulls.broken["djp3/agent-frolic"] = "GitHub's rate limit is used up for now. Try again in a few minutes."
        await pilot.press("u")
        await settle(app, pilot)
        assert "pull requests not read" in screen(app)
        await pilot.press("p")
        await pilot.pause()
        shown = screen(app)
        assert "rate limit is used up" in shown and "#12 GraphQL client" in shown    # the earlier reading stays


def test_the_self_check_draws_the_setup_without_the_keychain_and_leaves_things_as_they_were(isolated, capsys,
                                                                                            monkeypatch):
    keychain.put(keychain.GITHUB_TOKEN, TOKEN)                     # a token is stored, and it must not be read
    before = (main.os.environ["AGENT_FROLIC_SETTINGS"], main.os.environ["AGENT_FROLIC_REPOS"])
    assert main.selfcheck() == 0
    assert "self-check passed" in capsys.readouterr().out
    assert FakeGitHub.tokens_seen == [] and FakeTunnel.opened == [] and FakePulls.asked == []
    assert keychain.get(keychain.GITHUB_TOKEN) == TOKEN            # the store that was in use is back
    assert (main.os.environ["AGENT_FROLIC_SETTINGS"], main.os.environ["AGENT_FROLIC_REPOS"]) == before


def test_once_the_app_starts_https_certificates_are_judged_by_macos(isolated, tmp_path, monkeypatch):
    """Not by the certificate file of the Python it was built with, which a packaged copy
    names but another Mac may not have: every HTTPS connection, GitHub's and the tunnel
    check's, goes through urllib's default context, and that must be macOS's."""
    import http.client
    import ssl

    import truststore
    assert not isinstance(http.client.HTTPSConnection("api.github.com")._context, truststore.SSLContext)
    monkeypatch.setattr(main, "log_dir", lambda: tmp_path)
    monkeypatch.setattr(main, "start_log", lambda folder: None)    # no handler left on the root logger
    monkeypatch.setattr(main.Frolic, "run", lambda self, **options: None)
    monkeypatch.setattr(main.sys, "argv", ["agent-frolic-core"])
    assert main.main() == 0
    assert isinstance(http.client.HTTPSConnection("api.github.com")._context, truststore.SSLContext)
    assert isinstance(ssl.create_default_context(), truststore.SSLContext)


def test_starting_the_board_starts_the_log_where_it_has_always_been(isolated, tmp_path, monkeypatch):
    started = []
    monkeypatch.setattr(main, "log_dir", lambda: tmp_path)
    monkeypatch.setattr(main, "start_log", started.append)
    monkeypatch.setattr(main.Frolic, "run", lambda self, **options: None)
    monkeypatch.setattr(main.sys, "argv", ["agent-frolic-core"])
    assert main.main() == 0 and started == [tmp_path]


def test_the_log_starts_afresh_past_its_size_and_keeps_three_before_it(tmp_path, monkeypatch):
    """A board left open for months must not fill the disk, nor keep every private name it read."""
    monkeypatch.setattr(main, "LOG_BYTES", 2000)
    root = logging.getLogger()
    level = root.level
    handler = main.start_log(tmp_path / "logs")
    try:
        for i in range(400):
            logging.getLogger("agent_frolic.test").info("line %d of a long day on a busy repository", i)
    finally:
        root.removeHandler(handler)
        handler.close()
        root.setLevel(level)
    logs = tmp_path / "logs"
    assert sorted(p.name for p in logs.iterdir()) == [
        "agent-frolic.log", "agent-frolic.log.1", "agent-frolic.log.2", "agent-frolic.log.3"]
    assert all(p.stat().st_size <= 2000 for p in logs.iterdir())
    assert "line 399 of a long day" in (logs / "agent-frolic.log").read_text()     # the newest, where it was


QUITS = """
import time
from textual import work
from textual.app import App
import main

class Board(App):
    def on_mount(self):
        if STUCK:
            self.wait()
        self.set_timer(0.2, lambda: self.exit(return_code=3))

    @work(thread=True)
    def wait(self):
        time.sleep(30)                  # a job inside a network call that will not hurry

main.finish(main.run(Board(), headless=True), grace=0.5)
"""


@pytest.mark.parametrize("stuck", [True, False])
def test_quitting_does_not_wait_for_a_stuck_background_job(stuck):
    """Run as its own process, to time a real exit. The Mac app waits for the board to
    end before it starts one again, and ngrok holds the account's one session till then."""
    began = time.monotonic()
    done = subprocess.run([sys.executable, "-c", f"STUCK = {stuck}\n" + QUITS], cwd=Path(main.__file__).parent,
                          capture_output=True, text=True, timeout=25)
    assert done.returncode == 3, done.stderr                     # the app's own exit code
    assert time.monotonic() - began < 10                         # not the job's thirty seconds
    assert ("leaving with 1 background job(s) unfinished" in done.stderr) == stuck


class Terminal(io.StringIO):
    """What the board writes to a terminal, kept."""

    def isatty(self) -> bool:
        return True


def test_while_the_board_runs_the_terminal_sends_application_cursor_keys():
    """So the Mac app's terminal hands Page Up and Page Down (Fn+up, Fn+down) to the board."""
    seen = Terminal()
    with main.cursor_keys(seen):
        assert seen.getvalue() == "\x1b[?1h"                    # on before the board draws
    assert seen.getvalue() == "\x1b[?1h\x1b[?1l"                # and back off after
    crashed = Terminal()
    with pytest.raises(RuntimeError), main.cursor_keys(crashed):
        raise RuntimeError("the board tripped")
    assert crashed.getvalue().endswith("\x1b[?1l")              # back off however it ends
    redirected = io.StringIO()                                  # not a terminal: nothing is written
    with main.cursor_keys(redirected), main.cursor_keys(None):
        pass
    assert redirected.getvalue() == ""


def test_the_board_asks_for_them_on_the_stream_textual_draws_on(monkeypatch):
    asked = []

    @contextmanager
    def cursor_keys(stream):
        asked.append(stream)
        yield

    class Board:
        return_code = 0

        def run(self, loop=None, **options):
            pass

    monkeypatch.setattr(main, "cursor_keys", cursor_keys)
    main.run(Board())
    main.run(Board(), headless=True)
    assert asked == [sys.__stderr__, None]                      # Textual draws on stderr; headless, nowhere


def test_textual_reads_the_keys_application_cursor_mode_sends():
    """The arrows, Home and End come as ESC O A and the like in that mode. Textual's table of
    them is private, so this guards against an upgrade that drops one."""
    from textual._ansi_sequences import ANSI_SEQUENCES_KEYS
    from textual.keys import Keys
    for sent, key in (("\x1bOA", Keys.Up), ("\x1bOB", Keys.Down), ("\x1bOC", Keys.Right),
                      ("\x1bOD", Keys.Left), ("\x1bOH", Keys.Home), ("\x1bOF", Keys.End)):
        assert ANSI_SEQUENCES_KEYS[sent] == (key,), sent


def test_the_self_check_fails_when_https_would_not_be_judged_by_macos(isolated, capsys, monkeypatch):
    monkeypatch.setattr(main, "use_macos_trust", lambda: False)
    assert main.selfcheck() == 1
    assert "self-check FAILED: HTTPS is not judged by macOS's certificate trust" in capsys.readouterr().out


def test_version_and_selfcheck_return_without_starting_the_board(isolated, capsys, monkeypatch):
    monkeypatch.setenv("AGENT_FROLIC_VERSION", "9.8.7")
    monkeypatch.setattr(main.Frolic, "run", lambda self: pytest.fail("the board was started"))
    monkeypatch.setattr(main.sys, "argv", ["agent-frolic-core", "--version"])
    assert main.main() == 0 and capsys.readouterr().out == "9.8.7\n"
    monkeypatch.setattr(main.sys, "argv", ["agent-frolic-core", "--selfcheck"])
    assert main.main() == 0 and "agent-frolic 9.8.7: self-check passed" in capsys.readouterr().out


def test_the_setup_says_days_not_seventy_two_hours():
    """How long since the last webhook, or since a repository was read."""
    now = time.time()
    assert [main.age(now - back) for back in (12, 40 * 60, 5 * 3600, 3 * 86400, 35 * 86400)] == [
        "12s", "40m", "5h", "3d", "5w"]


class Opener:
    """Stands in for `open` as subprocess.Popen starts it: how it was started, and how it ends."""
    started: list = []
    code: int | None = 0          # what it exits with; None: still running when the wait is up
    stopped = False

    def __init__(self, argv, **how):
        Opener.started.append((argv, how))

    def wait(self, timeout=None):
        if Opener.code is None:
            raise subprocess.TimeoutExpired("open", timeout)
        return Opener.code

    def kill(self):
        Opener.stopped = True

    terminate = kill


@pytest.fixture
def opener(monkeypatch):
    Opener.started, Opener.code, Opener.stopped = [], 0, False
    monkeypatch.setattr(main, "ON_MAC", True)                 # main's own word for it, not sys.platform:
    monkeypatch.setattr(main.subprocess, "Popen", Opener)     # nothing else in the test run sees a change
    return Opener


def test_only_a_github_page_over_https_reaches_the_browser(monkeypatch, opener):
    ran = opener.started
    page = "https://github.com/acme/web-console/pull/143"
    real_open_page(page)
    argv, how = ran[0]
    assert argv == ["/usr/bin/open", page]                    # the program by its full path, the address as one argument
    assert "shell" not in how and how["stdout"] == how["stderr"] == subprocess.DEVNULL   # no shell, nothing on the screen
    refused = ["http://github.com/acme/web-console/pull/143",      # not https
               "https://example.com/acme/web-console/pull/143",    # not GitHub
               "https://github.com.example.com/a/b/pull/1", "https://github.com@example.com/a/b/pull/1",
               "file:///etc/passwd", "javascript:alert(1)", "-a Calculator", ""]
    for address in refused:
        with pytest.raises(ValueError):
            real_open_page(address)
    assert len(ran) == 1                                      # none of those reached `open`

    monkeypatch.setattr(main, "ON_MAC", False)                # elsewhere: Python's own way to a browser
    shown = []
    monkeypatch.setattr(main.webbrowser, "open", lambda url: shown.append(url) or True)
    real_open_page(page)
    assert shown == [page] and len(ran) == 1
    monkeypatch.setattr(main.webbrowser, "open", lambda url: False)
    with pytest.raises(OSError):
        real_open_page(page)


def test_a_browser_slow_to_start_is_not_one_that_failed(opener):
    """`open` can take longer than the wait while a browser starts from cold: the page still
    opens, so nothing is reported and `open` is left to finish."""
    page = "https://github.com/acme/web-console/pull/143"
    opener.code = None                                        # still running when the wait is up
    real_open_page(page)                                      # no error,
    assert len(opener.started) == 1 and not opener.stopped    # and it was not stopped
    opener.code = 1                                           # `open` itself says it could not
    with pytest.raises(subprocess.CalledProcessError):
        real_open_page(page)


@drive
async def test_what_open_itself_says_reaches_the_board(isolated, monkeypatch, opener):
    """End to end through the real way to the browser, with `open` stood in for: that it
    could not, and that it is slow, as the board then says them."""
    monkeypatch.setattr(main, "open_page", real_open_page)
    keychain.put(keychain.GITHUB_TOKEN, TOKEN)
    RepoStore().add("acme/web-console")
    FakePulls.open_ = {"acme/web-console": [node(143, "dark")]}
    app = main.Frolic()
    async with app.run_test(size=(150, 40)) as pilot:
        await settle(app, pilot)
        app.OPEN_AGAIN_S = 0

        def ticker() -> str:
            return app.board.ticker().plain

        await pilot.press("o")
        await settle(app, pilot)
        assert [argv for argv, _ in opener.started] == [
            ["/usr/bin/open", "https://github.com/acme/web-console/pull/143"]]
        assert ticker() == "#143 · opening its page in the browser"
        opener.code = 1                                           # `open` exits saying it could not
        await pilot.press("o")
        await settle(app, pilot)
        assert ticker() == "#143 · its page was not opened: the browser could not be started"
        opener.code = None                                        # `open` still at it when the wait is up
        await pilot.press("o")
        await settle(app, pilot)
        assert ticker() == "#143 · opening its page in the browser" and not opener.stopped


def test_an_address_that_only_reads_as_github_is_refused(opener):
    """Python's reading of an address and a browser's part ways on these: they pass a check
    of the parsed host, and the raw text is what would be opened."""
    for address in ("https://evil.example\\@github.com/a/b/pull/1",    # a browser goes to evil.example
                    " https://github.com/a/b/pull/1", "\thttps://github.com/a/b/pull/1",
                    "https://github.com/a/b/pull/1\n", "https://github.com/a\x00b/pull/1",
                    "https://github.com/a b/pull/1",
                    "https://someone:secret@github.com/a/b/pull/1",     # a user in the address
                    "https://github.com:8443/a/b/pull/1"):              # a port
        with pytest.raises(ValueError):
            real_open_page(address)
    with pytest.raises(main.NotOnGitHub, match="it is not a page on github.com"):
        real_open_page("https://[github.com]/a/b/pull/1")        # not Python's words about an IPv6 address
    assert opener.started == []                                  # none of them reached `open`
    real_open_page("https://github.com/acme/web-console/pull/143#discussion_r1")
    assert [argv for argv, _ in opener.started] == [
        ["/usr/bin/open", "https://github.com/acme/web-console/pull/143#discussion_r1"]]


def test_what_is_copied_is_handed_to_pbcopy_by_its_path_and_not_through_a_shell(monkeypatch):
    ran = []
    monkeypatch.setattr(main, "ON_MAC", True)
    monkeypatch.setattr(main.subprocess, "run", lambda *argv, **how: ran.append((argv, how)))
    real_copy_text("acme-web-pr-98")
    (argv, how), = ran
    assert argv == (["/usr/bin/pbcopy"],)                     # the program by its full path, and no argument
    assert how["input"] == b"acme-web-pr-98" and "shell" not in how           # the text is its input, never a command
    assert how["check"] is True and how["timeout"] == main.COPY_WAIT_S        # one that fails or hangs is an error
    assert how["stdout"] == how["stderr"] == subprocess.DEVNULL


def test_off_a_mac_nothing_is_run_and_the_copy_fails_plainly(monkeypatch):
    monkeypatch.setattr(main, "ON_MAC", False)
    monkeypatch.setattr(main.subprocess, "run", lambda *argv, **how: pytest.fail("nothing is to be run"))
    with pytest.raises(OSError):
        real_copy_text("acme-web-pr-98")


# ---- the teams the viewer is on -----------------------------------------------------------

PLATFORM = Team("Acme", "platform", "Platform", ("engineering", "Engineering"))
DOCS = Team("Acme", "docs", "Docs", ("engineering", "Engineering"))
ASKED = ("acme/web-console", 201)
OUT_OF_REACH = GitHubError(0, "could not reach GitHub: timed out",
                           "GitHub could not be reached. Check the network and try again.")


def a_team_request(*teams):
    """The viewer (djp3) is on Acme's teams Platform and Docs, both nested in Engineering.
    One pull request of Acme's asks Platform for a review."""
    keychain.put(keychain.GITHUB_TOKEN, TOKEN)
    RepoStore().add("acme/web-console", private=True, admin=False)
    FakeGitHub.teams_listed = list(teams or (PLATFORM, DOCS))
    FakePulls.open_ = {"acme/web-console": [node(
        201, "bump-a", author={"login": "dependabot"}, title="Bump a",
        reviewRequests={"nodes": [{"requestedReviewer": {"__typename": "Team", "slug": "platform"}}]})]}


def theirs(app) -> bool:
    """Whether the board has the team's request as the viewer's, from what the app now holds.
    It looks for itself: while the setup is showing, nothing else makes the board look."""
    app.board.look()
    return app.board.pic.mine(app.board.pic.find(ASKED))


def said(app) -> list[str]:
    return [text for _, text, _ in app.lines]


def rows(app) -> list[str]:
    """The screen's lines, each without the space around it: the setup's own line about the
    teams is a line by itself, where the log beside it only mentions them."""
    return [row.strip() for row in screen(app).splitlines()]


async def paste(app, pilot, token):
    await pilot.press("t")
    await pilot.pause()
    app.screen.query_one("#token", Input).value = token
    await pilot.press("enter")
    await pilot.pause()


async def one_read_of_the_teams_left(app, pilot):
    """Wait until all but one of the reads of the teams have ended and been answered."""
    await until(pilot, lambda: len([w for w in app.workers if w.group == "teams" and w.is_running]) == 1)
    await pilot.pause()
    await pilot.pause()


@drive
async def test_the_teams_are_read_once_the_token_is_accepted_and_reach_the_board(isolated):
    a_team_request()
    app = main.Frolic()
    async with app.run_test(size=(150, 46)) as pilot:
        await settle(app, pilot)
        assert FakeGitHub.teams_asked == [TOKEN]
        assert app.teams == {"team/acme/platform": "Platform", "team/acme/docs": "Docs",
                             "team/acme/engineering": "Engineering"}     # and the team both are nested in
        assert theirs(app) and "● Platform" in screen(app)
        # The line about the read says how many, not which. Nothing has changed yet, so no
        # other line names a team either (one that says what changed may: see the next test).
        assert "GitHub lists you on 2 teams" in said(app)
        assert not any("platform" in text.lower() or "docs" in text.lower() for text in said(app))
        for kept in ("AGENT_FROLIC_SETTINGS", "AGENT_FROLIC_REPOS"):      # the list is in no settings file
            path = Path(os.environ[kept])
            assert not path.exists() or "platform" not in path.read_text().lower()
        await to_setup(app, pilot)
        assert "on 2 teams" in rows(app)                                  # Engineering, added, is not counted
        await pilot.press("p")                                            # the plain list of pull requests
        await pilot.pause()
        assert "◔ team/platform" in screen(app) and "team/acme" not in screen(app).lower()
        await pilot.press("t")
        await pilot.pause()
        assert "A fine-grained token sees the teams of one organization only." in screen(app)


@drive
async def test_the_teams_a_picture_is_drawn_from_are_not_copied_at_every_draw_and_cannot_be_written_to(isolated):
    """The board asks for them five times a second. The app hands out the mapping it keeps,
    which nothing can change; a read that answers puts another in its place."""
    a_team_request()
    app = main.Frolic()
    async with app.run_test(size=(150, 46)) as pilot:
        await settle(app, pilot)
        kept = app.teams
        assert app.teams_now()[0] is kept and app.teams_now()[0] is kept and app.board.pic.teams is kept
        with pytest.raises(TypeError):
            kept["team/acme/sales"] = "Sales"
        await pilot.press("u")                                            # read again, and the same as before:
        await settle(app, pilot)
        assert app.teams is kept                                          # the mapping stays the one it was
        FakeGitHub.teams_listed = [PLATFORM]
        await pilot.press("u")
        await settle(app, pilot)
        assert app.teams is not kept and "team/acme/docs" in kept and "team/acme/docs" not in app.teams
        await to_setup(app, pilot)
        await answer_yes(pilot, "f")
        assert app.teams_now() == ({}, False) and app.teams_now()[0] is app.teams_now()[0]    # none: one empty one


@drive
async def test_a_review_asked_of_a_team_further_up_the_nesting_is_the_viewers_too(isolated):
    """Platform is nested in Engineering, which is nested in Company. GitHub says only the
    first of those when it lists the viewer's teams."""
    def ask_of(slug):
        return {"nodes": [{"requestedReviewer": {"__typename": "Team", "slug": slug}}]}

    a_team_request(PLATFORM)
    FakePulls.open_["acme/web-console"] = [
        node(201, "bump-a", author={"login": "dependabot"}, title="Bump a", reviewRequests=ask_of("company"))]
    app = main.Frolic()
    async with app.run_test(size=(150, 46)) as pilot:
        await settle(app, pilot)                                          # GitHub will not say what Engineering
        assert app.teams == {"team/acme/platform": "Platform",            # is nested in: one level, and no failure
                             "team/acme/engineering": "Engineering"}
        assert app.teams_note == "" and not theirs(app)
        FakeGitHub.teams_by_name = {("acme", "engineering"): Team("Acme", "engineering", "Engineering",
                                                                  ("company", "Company")),
                                    ("acme", "company"): Team("Acme", "company", "Company")}
        await pilot.press("u")
        await settle(app, pilot)
        assert app.teams == {"team/acme/platform": "Platform", "team/acme/engineering": "Engineering",
                             "team/acme/company": "Company"}
        assert theirs(app) and "● Company" in screen(app)
        await to_setup(app, pilot)
        assert "on 1 team" in rows(app)                                   # the one GitHub lists; not those above it


@drive
async def test_a_change_line_names_the_team_as_it_names_people(isolated, caplog):
    """And so a team's name reaches the log, which is a file: a line that says what changed
    is said, and what is said is logged."""
    caplog.set_level(logging.INFO, logger="agent_frolic")
    a_team_request()
    app = main.Frolic()
    async with app.run_test(size=(150, 46)) as pilot:
        await settle(app, pilot)
        FakePulls.open_["acme/web-console"].append(node(
            204, "bump-b", author={"login": "dependabot"}, title="Bump b",
            reviewRequests={"nodes": [{"requestedReviewer": {"__typename": "Team", "slug": "platform"}}]}))
        await pilot.press("u")
        await settle(app, pilot)
        assert "acme/web-console · #204 opened by dependabot · waits on Platform to review" in said(app)
        assert "acme/web-console · #204 opened by dependabot · waits on Platform to review" in caplog.messages
        assert ("acme/web-console", 204) in app.flashes


@drive
async def test_u_reads_the_teams_again_and_a_read_that_fails_then_keeps_the_ones_known(isolated):
    a_team_request()
    app = main.Frolic()
    async with app.run_test(size=(150, 46)) as pilot:
        await settle(app, pilot)
        FakeGitHub.teams_listed = [PLATFORM]                              # the viewer has left Docs
        FakeGitHub.gates["teams"] = threading.Event()
        await pilot.press("u")
        await until(pilot, lambda: "teams" in FakeGitHub.waiting)
        assert app.teams_run and not app.teams_held and theirs(app)       # read again, and nothing held meanwhile
        FakeGitHub.gates.pop("teams").set()
        await settle(app, pilot)
        assert FakeGitHub.teams_asked == [TOKEN, TOKEN]
        assert app.teams == {"team/acme/platform": "Platform", "team/acme/engineering": "Engineering"}
        assert app.teams_listed == 1 and theirs(app)

        FakeGitHub.teams_broken = OUT_OF_REACH                            # the network drops for a moment
        await pilot.press("u")
        await settle(app, pilot)
        assert len(FakeGitHub.teams_asked) == 3 and not app.teams_held
        assert app.teams == {"team/acme/platform": "Platform", "team/acme/engineering": "Engineering"}
        assert theirs(app) and "● Platform" in screen(app)                # the dot stays where it was
        assert ("your teams could not be read again: GitHub could not be reached. Check the network and try "
                "again. The ones read before still stand.") in said(app)
        await to_setup(app, pilot)
        assert "on 1 team · not read again: GitHub could not be reached." in screen(app)

        FakeGitHub.teams_broken = None
        await pilot.press("u")
        await settle(app, pilot)
        assert app.teams_note == "" and "not read again" not in screen(app) and "on 1 team" in rows(app)


@drive
async def test_teams_that_cannot_be_read_leave_the_token_good_and_a_teams_request_anyone_elses(isolated):
    a_team_request()
    FakeGitHub.teams_broken = GitHubError(403, "Resource not accessible by personal access token",
                                          "The token is not allowed to do this.")
    app = main.Frolic()
    async with app.run_test(size=(150, 46)) as pilot:
        await settle(app, pilot)
        assert app.token_state == "ok" and FakePulls.asked == ["acme/web-console"]    # signed in, and reading
        assert app.teams is None and not app.teams_held                   # let go: not held for ever
        assert not theirs(app) and "team/platform" in screen(app)
        assert counts(app.board.pic, "acme/web-console") == (0, 1)        # anyone else's, as before there were teams
        await to_setup(app, pilot)
        shown = screen(app)
        assert "signed in as djp3" in shown
        assert "your teams could not be read: The token is not allowed to do this." in shown
        assert "A request to a team will not show as yours.  u tries again" in shown
        assert "fine-grained" not in shown and "lacks a scope" not in shown    # a classic token, with the repo scope
        age(app)                                                          # a refusal would be one again:
        app.tick()                                                        # the poll does not ask
        await settle(app, pilot)
        assert FakeGitHub.teams_asked == [TOKEN] and not app.teams_retry


@pytest.mark.parametrize("scopes, advice, not_said", [
    (None, "A fine-grained token sees the teams of one organization only.", "lacks a scope"),
    (["gist"], "This token lacks a scope that lists teams: repo, read:org or user.", "fine-grained"),
    (["read:org"], "", "lacks a scope")])
def test_the_advice_under_teams_that_could_not_be_read_is_for_the_kind_of_token_there_is(scopes, advice, not_said):
    @drive
    async def run():
        a_team_request()
        FakeGitHub.scopes = scopes
        FakeGitHub.teams_broken = GitHubError(403, "x", "The token is not allowed to list your teams.")
        app = main.Frolic()
        async with app.run_test(size=(150, 46)) as pilot:
            await settle(app, pilot)
            await to_setup(app, pilot)
            shown = screen(app)
            assert "your teams could not be read: The token is not allowed to list your teams." in shown
            assert advice in shown and not_said not in shown
            if not advice:
                assert "fine-grained" not in shown
    run()


@drive
async def test_a_read_of_the_teams_that_github_was_not_reached_for_is_tried_again_with_the_poll(isolated):
    """The board is left open for days. One request failing at sign-in must not leave every
    request to the viewer's teams reading as someone else's until they happen to press u."""
    def poll():
        age(app)
        app.tick()

    def told():
        return [text for text in said(app) if "your teams could not be read" in text]

    a_team_request()
    FakeGitHub.teams_broken = OUT_OF_REACH
    app = main.Frolic()
    async with app.run_test(size=(150, 46)) as pilot:
        await settle(app, pilot)
        assert app.token_state == "ok" and app.teams is None and app.teams_retry and not theirs(app)
        assert told() == ["your teams could not be read: GitHub could not be reached. Check the network and try "
                          "again. A request to a team will not show as yours. It is tried again with each poll."]
        app.draw()
        await pilot.pause()
        assert "your teams could not be read · a request to a team shows as someone else's" in screen(app)
        poll()                                                            # still out of reach
        await settle(app, pilot)
        assert FakeGitHub.teams_asked == [TOKEN, TOKEN] and len(told()) == 1    # asked again; said once
        await to_setup(app, pilot)
        assert "A request to a team will not show as yours.  tried again with each poll · u tries now" in screen(app)
        FakeGitHub.teams_broken = None                                    # the network is back
        poll()
        await settle(app, pilot)
        assert len(FakeGitHub.teams_asked) == 3 and theirs(app) and not app.teams_retry
        assert "on 2 teams" in rows(app)
        poll()                                                            # known: the poll leaves them be
        await settle(app, pilot)
        assert len(FakeGitHub.teams_asked) == 3

        FakeGitHub.teams_broken = OUT_OF_REACH                            # a read on u that fails keeps them,
        await pilot.press("u")                                            # and the poll does not go after it
        await settle(app, pilot)
        poll()
        await settle(app, pilot)
        assert len(FakeGitHub.teams_asked) == 4 and not app.teams_retry and theirs(app)


@drive
async def test_a_read_of_the_teams_always_answers_whatever_goes_wrong(isolated, monkeypatch):
    """A request to a team is held back until the read answers, so it must: a Keychain that
    cannot be read, or anything nobody expected, ends as "could not be read"."""
    a_team_request()
    FakeGitHub.teams_broken = RuntimeError("nobody expected this")
    app = main.Frolic()
    async with app.run_test(size=(150, 46)) as pilot:
        await settle(app, pilot)
        assert app.token_state == "ok" and app.teams is None and app.teams_run == 0 and not app.teams_held
        assert app.teams_note == "GitHub's reply could not be read: RuntimeError."

        app.forget_teams()                                                # as at a sign-in, with none known
        real = keychain.get

        def locked(name):
            if name == keychain.GITHUB_TOKEN and threading.current_thread() is not threading.main_thread():
                raise keychain.KeychainError("the Keychain is locked")
            return real(name)

        monkeypatch.setattr(keychain, "get", locked)
        app.start_teams_read()
        assert app.teams_held                                             # held, until it answers
        await settle(app, pilot)
        assert app.teams is None and app.teams_run == 0 and not app.teams_held
        assert app.teams_note == "The Keychain could not be read: the Keychain is locked"
        assert not theirs(app)


@drive
async def test_while_the_teams_are_read_a_teams_request_is_held_and_settling_says_nothing(isolated):
    a_team_request()
    FakeGitHub.gates["teams"] = threading.Event()                         # GitHub is slow to say which teams
    app = main.Frolic()
    async with app.run_test(size=(150, 46)) as pilot:
        await until(pilot, lambda: "teams" in FakeGitHub.waiting and "acme/web-console" in app.pulls
                    and not app.fetching)
        assert app.token_state == "ok" and app.teams_held                 # signed in and reading: not kept waiting
        assert not theirs(app) and app.board.pic.held
        assert counts(app.board.pic, "acme/web-console") == (0, 0)        # neither theirs nor anyone else's
        await pilot.press("u")                                            # GitHub is read again meanwhile
        await until(pilot, lambda: FakePulls.asked.count("acme/web-console") == 2 and not app.fetching)
        assert FakeGitHub.teams_asked == [TOKEN]                          # the teams are being asked already
        assert app.news is None and not app.flashes                       # nothing changed, nothing said
        await to_setup(app, pilot)
        assert "reading your teams…" in screen(app)

        FakeGitHub.gates["teams"].set()
        await settle(app, pilot)
        assert not app.teams_held and theirs(app) and "on 2 teams" in rows(app)
        assert app.news is None and not app.flashes                       # settling is not a change
        await pilot.press("u")                                            # nor does the next reading find one
        await settle(app, pilot)
        assert app.news is None and not app.flashes and not app.teams_held
        assert not any("#201" in text for text in said(app))


@drive
async def test_an_answer_about_the_teams_of_a_forgotten_token_is_dropped(isolated):
    a_team_request()
    FakeGitHub.gates["teams"] = threading.Event()
    app = main.Frolic()
    async with app.run_test(size=(150, 46)) as pilot:
        await until(pilot, lambda: "teams" in FakeGitHub.waiting)
        await to_setup(app, pilot)
        await answer_yes(pilot, "f")
        assert app.token_state == "none" and app.teams_run == 0 and not app.teams_held
        FakeGitHub.gates["teams"].set()                                   # then GitHub says which teams
        await settle(app, pilot)
        assert app.teams is None and app.teams_note == "" and not app.teams_held
        assert not any("GitHub lists you" in text for text in said(app))


@drive
async def test_forgetting_the_token_forgets_the_teams(isolated):
    a_team_request()
    app = main.Frolic()
    async with app.run_test(size=(150, 46)) as pilot:
        await settle(app, pilot)
        assert app.teams and app.teams_for == "djp3"
        await to_setup(app, pilot)
        assert "on 2 teams" in rows(app)
        await answer_yes(pilot, "f")
        await settle(app, pilot)
        assert (app.teams, app.teams_note, app.teams_listed, app.teams_for, app.teams_run) == (None, "", 0, "", 0)
        assert "on 2 teams" not in rows(app) and "none yet" in screen(app)


@drive
async def test_the_answer_for_the_account_before_does_not_let_go_of_what_the_read_for_the_new_one_holds(isolated):
    """Two reads under way, one for each account. The first to answer is for the account
    whose token was replaced: it is not used, and it must not end the hold that the read
    for the account now signed in still keeps."""
    first, second = TOKEN, TOKEN[:-4] + "4321"
    FakeGitHub.logins = {second: "mira"}
    a_team_request()
    FakeGitHub.gates[("teams", first)] = threading.Event()
    FakeGitHub.gates[("teams", second)] = threading.Event()
    app = main.Frolic()
    async with app.run_test(size=(150, 46)) as pilot:
        await until(pilot, lambda: ("teams", first) in FakeGitHub.waiting)
        await to_setup(app, pilot)
        await paste(app, pilot, second)
        await until(pilot, lambda: ("teams", second) in FakeGitHub.waiting)
        assert app.teams_held and app.teams_run == app.token_run
        FakeGitHub.gates[("teams", first)].set()
        await one_read_of_the_teams_left(app, pilot)
        assert app.teams is None and app.teams_held and app.teams_run == app.token_run   # still held
        assert not any("GitHub lists you" in text for text in said(app))                 # and its answer unused
        FakeGitHub.gates[("teams", second)].set()
        await settle(app, pilot)
        assert not app.teams_held and app.teams_run == 0 and app.teams_for == "mira" and theirs(app)
        assert FakeGitHub.teams_asked == [first, second]


@drive
async def test_an_answer_for_the_account_signed_in_is_used_though_its_token_has_changed_since(isolated):
    """A second token of the same account is pasted while the teams are being read. What
    the first read finds is as true as it was. But an answer older than one already used
    is not used after it."""
    first, second = TOKEN, TOKEN[:-4] + "4321"
    a_team_request()
    FakeGitHub.gates[("teams", first)] = threading.Event()
    FakeGitHub.gates[("teams", second)] = threading.Event()
    app = main.Frolic()
    async with app.run_test(size=(150, 46)) as pilot:
        await until(pilot, lambda: ("teams", first) in FakeGitHub.waiting)
        await to_setup(app, pilot)
        await paste(app, pilot, second)
        await until(pilot, lambda: ("teams", second) in FakeGitHub.waiting)
        assert app.teams_held
        FakeGitHub.gates[("teams", first)].set()                          # asked under the token before
        await one_read_of_the_teams_left(app, pilot)
        assert app.teams is not None and not app.teams_held and theirs(app)     # used: the same account
        FakeGitHub.gates[("teams", second)].set()
        await settle(app, pilot)
        assert app.teams_seen == app.token_run and FakeGitHub.teams_asked == [first, second]

        third = TOKEN[:-4] + "8765"                                       # the other way about: the read begun
        FakeGitHub.gates[("teams", second)] = threading.Event()           # first answers last
        await pilot.press("u")
        await until(pilot, lambda: FakeGitHub.teams_asked == [first, second, second])
        await paste(app, pilot, third)
        await until(pilot, lambda: app.teams_seen == app.token_run and len(FakeGitHub.teams_asked) == 4)
        newest = app.teams
        FakeGitHub.teams_listed = [PLATFORM]                              # what the slow one will say: Docs is gone
        FakeGitHub.gates[("teams", second)].set()
        await settle(app, pilot)
        assert app.teams is newest and "team/acme/docs" in app.teams      # older than the one used: not used


@drive
async def test_a_refused_replacement_token_does_not_leave_the_teams_unread(isolated):
    """The read under way is for the stored token. A token is pasted, and refused: the
    stored one stands. What the read finds is used, whenever it comes, and nothing has to
    be asked a second time."""
    bad = "ghp_bad" + "x" * 33
    a_team_request()
    FakeGitHub.gates[("teams", TOKEN)] = threading.Event()
    FakeGitHub.gates[bad] = threading.Event()                             # GitHub is slow to refuse it
    app = main.Frolic()
    async with app.run_test(size=(150, 46)) as pilot:
        await until(pilot, lambda: ("teams", TOKEN) in FakeGitHub.waiting)
        await to_setup(app, pilot)
        await paste(app, pilot, bad)
        await until(pilot, lambda: bad in FakeGitHub.waiting)
        FakeGitHub.gates[("teams", TOKEN)].set()                          # the answer for the stored token comes
        await until(pilot, lambda: not [w for w in app.workers if w.group == "teams" and w.is_running])
        await pilot.pause()
        assert app.teams is not None and app.token_state == "checking"    # used, while the paste is checked
        FakeGitHub.gates[bad].set()
        await settle(app, pilot)
        assert app.token_state == "ok" and keychain.get(keychain.GITHUB_TOKEN) == TOKEN
        assert FakeGitHub.teams_asked == [TOKEN] and theirs(app) and not app.teams_held    # asked once


@drive
async def test_another_account_signing_in_does_not_keep_the_teams_of_the_one_before(isolated):
    second, third = TOKEN[:-4] + "4321", TOKEN[:-4] + "8765"
    FakeGitHub.logins = {second: "mira", third: "mira"}
    a_team_request()
    app = main.Frolic()
    async with app.run_test(size=(150, 46)) as pilot:
        await settle(app, pilot)
        assert theirs(app) and app.teams_for == "djp3"
        FakeGitHub.teams_listed = [DOCS]                                  # mira is on Docs alone
        FakeGitHub.gates[("teams", second)] = threading.Event()
        await to_setup(app, pilot)
        await paste(app, pilot, second)
        await until(pilot, lambda: ("teams", second) in FakeGitHub.waiting)
        assert app.identity.login == "mira" and app.teams is None and app.teams_held   # cleared at once, and held
        assert not theirs(app)
        FakeGitHub.gates[("teams", second)].set()
        await settle(app, pilot)
        assert app.teams == {"team/acme/docs": "Docs", "team/acme/engineering": "Engineering"}
        assert not theirs(app) and app.teams_for == "mira"

        FakeGitHub.gates[("teams", third)] = threading.Event()            # the same account, another token:
        await paste(app, pilot, third)
        await until(pilot, lambda: ("teams", third) in FakeGitHub.waiting)
        assert app.teams == {"team/acme/docs": "Docs", "team/acme/engineering": "Engineering"}
        assert not app.teams_held                                         # its teams stand meanwhile
        FakeGitHub.gates[("teams", third)].set()
        await settle(app, pilot)


# ---- the token a reading was made with -----------------------------------------------------

ORG_MOVE = ("acme/web-console", 301)


def an_org_move():
    """Acme owns acme/web-console, where a bot's pull request nobody has looked at waits on
    the maintainer, Acme. The stored token (djp3's) is an admin's there."""
    keychain.put(keychain.GITHUB_TOKEN, TOKEN)
    RepoStore().add("acme/web-console", private=True, admin=False)
    FakePulls.orgs, FakePulls.access = {"acme/web-console"}, {TOKEN: "admin"}
    FakePulls.open_ = {"acme/web-console": [node(301, "deps", author={"login": "dependabot"},
                                                 authorAssociation="NONE", title="Bump b")]}


def org_move_mine(app) -> bool:
    """Whether the board has Acme's move as the viewer's, from what the app now holds."""
    app.board.look()
    pull = app.board.pic.find(ORG_MOVE)
    return pull is not None and app.board.pic.mine(pull)


@drive
async def test_a_reading_made_with_the_token_before_is_not_used(isolated):
    """A read is under way with an admin's token when a read-only account's token is
    pasted. What that read finds is not used when it ends: Acme's move would be taken for
    the new account's. The repository is read again with the new token, and that is used."""
    second = TOKEN[:-4] + "4321"
    FakeGitHub.logins = {second: "mira"}
    an_org_move()
    FakePulls.access[second] = "read"
    FakeGitHub.gates[("pulls", TOKEN)] = threading.Event()
    FakeGitHub.gates[("pulls", second)] = threading.Event()
    app = main.Frolic()
    async with app.run_test(size=(150, 46)) as pilot:
        await until(pilot, lambda: ("pulls", TOKEN) in FakeGitHub.waiting)
        await to_setup(app, pilot)
        await paste(app, pilot, second)
        await until(pilot, lambda: app.token_state == "ok" and app.identity.login == "mira")
        FakeGitHub.gates[("pulls", TOKEN)].set()                          # the read with the token before ends,
        await until(pilot, lambda: ("pulls", second) in FakeGitHub.waiting)   # and one with this token begins
        assert "acme/web-console" not in app.pulls and not org_move_mine(app)
        FakeGitHub.gates[("pulls", second)].set()
        await settle(app, pilot)
        assert app.pulls["acme/web-console"].access == "read" and not org_move_mine(app)
        assert counts(app.board.pic, "acme/web-console") == (0, 1)       # Acme is someone else


@drive
async def test_a_new_token_does_not_lend_the_rights_read_with_the_one_before(isolated):
    """Once another token is in use, what the token before read stays on the board until
    it is read again, but not what its account could do there: until then Acme's move is
    not the viewer's. Read again, the rights are the new token's."""
    second = TOKEN[:-4] + "4321"                                          # the same account, djp3
    an_org_move()
    FakePulls.access[second] = "admin"
    app = main.Frolic()
    async with app.run_test(size=(150, 46)) as pilot:
        await settle(app, pilot)
        assert org_move_mine(app)
        FakeGitHub.gates[("pulls", second)] = threading.Event()
        await to_setup(app, pilot)
        await paste(app, pilot, second)
        await until(pilot, lambda: ("pulls", second) in FakeGitHub.waiting)
        held = app.pulls["acme/web-console"]
        assert held.pulls and held.access == "" and not org_move_mine(app)   # still shown, not lent
        FakeGitHub.gates[("pulls", second)].set()
        await settle(app, pilot)
        assert org_move_mine(app)
        assert app.news is None and not app.flashes                       # not taken for a change on GitHub


@drive
async def test_another_account_signing_in_does_not_keep_what_the_one_before_read(isolated):
    """What one account read is not shown once another signs in, not even until the new
    account's reads come: it may hold a private repository the new account cannot see, and
    one it cannot read at all would keep it for good. Another token of the same account
    keeps it all until read again."""
    second, third = TOKEN[:-4] + "4321", TOKEN[:-4] + "8765"
    FakeGitHub.logins = {second: "mira", third: "mira"}
    an_org_move()
    FakePulls.access.update({second: "read", third: "read"})
    app = main.Frolic()
    async with app.run_test(size=(150, 46)) as pilot:
        await settle(app, pilot)
        FakePulls.open_["acme/web-console"].append(node(302, "more", author={"login": "dependabot"},
                                                        authorAssociation="NONE", title="Bump c"))
        await pilot.press("u")                                            # djp3 sees one open: said and washed
        await settle(app, pilot)
        assert app.news is not None and app.flashes and app.pulls_for == "djp3"

        FakePulls.broken["acme/web-console"] = "No such repository, or the token cannot see it."
        await to_setup(app, pilot)
        await paste(app, pilot, second)                                   # mira cannot read it at all
        await settle(app, pilot)
        assert app.identity.login == "mira" and app.pulls_for == "mira"
        assert app.pulls == {} and app.news is None and not app.flashes   # nothing of djp3's is left
        app.board.look()
        assert app.board.pic.all() == []
        assert app.board.pic.notes["acme/web-console"].startswith("could not be read")

        del FakePulls.broken["acme/web-console"]
        await pilot.press("u")
        await settle(app, pilot)
        assert sorted(p.number for p in app.pulls["acme/web-console"].pulls) == [301, 302]
        FakeGitHub.gates[("pulls", third)] = threading.Event()
        await paste(app, pilot, third)                                    # mira again, another token
        await until(pilot, lambda: ("pulls", third) in FakeGitHub.waiting)
        assert sorted(p.number for p in app.pulls["acme/web-console"].pulls) == [301, 302]    # kept meanwhile
        FakeGitHub.gates[("pulls", third)].set()
        await settle(app, pilot)


# ---- each repository's timer ---------------------------------------------------------------

async def read_since_hooked(app, pilot):
    """Let the short timer run out once and the repositories be read: one whose webhook was
    confirmed after its last read began is on the slow timer only from this read on."""
    age(app)
    app.tick()
    await settle(app, pilot)


@drive
async def test_each_repository_is_read_when_its_own_timer_runs_out(isolated):
    three_repos()
    app = main.Frolic()
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        assert sorted(app.read_at) == ["acme/ledger-api", "djp3/agent-frolic"]    # the hidden one has no timer
        FakePulls.asked.clear()
        age(app, only=["Acme/ledger-api"])
        app.tick()
        await settle(app, pilot)
        assert FakePulls.asked == ["Acme/ledger-api"]                 # it alone was due,
        assert time.time() - app.read_at["acme/ledger-api"] < 5       # and its timer starts again
        app.tick()
        await settle(app, pilot)
        assert FakePulls.asked == ["Acme/ledger-api"]

        FakePulls.broken["acme/ledger-api"] = "GitHub's rate limit is used up for now. Try again in a few minutes."
        age(app, only=["Acme/ledger-api"])                            # a read that fails starts the timer
        app.tick()                                                    # again too: it is not tried every second
        await settle(app, pilot)
        app.tick()
        await settle(app, pilot)
        assert FakePulls.asked == ["Acme/ledger-api"] * 2 and "acme/ledger-api" in app.pull_trouble
        assert time.time() - app.read_at["acme/ledger-api"] < 5


@drive
async def test_a_repository_that_leaves_the_list_takes_its_timers_with_it(isolated):
    three_repos()
    app = main.Frolic()
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        app.delivered(Delivery("pull_request", "d-1", "Acme/ledger-api", "opened"))
        await settle(app, pilot)
        kept = (app.read_at, app.read_began, app.hook_read_at, app.pulls)
        assert all("acme/ledger-api" in held for held in kept)
        await to_setup(app, pilot)
        await pilot.press("down")
        await answer_yes(pilot, "x")
        assert app.store.get("Acme/ledger-api") is None
        assert not any("acme/ledger-api" in held for held in kept)
        app.fetch_ended("Acme/ledger-api")                            # a read of it that was under way ends:
        assert "acme/ledger-api" not in app.read_at                   # there is nothing left to time
        assert sorted(app.read_at) == ["djp3/agent-frolic"]

        await answer_yes(pilot, "f")                                  # the token is forgotten,
        assert app.token_state == "none" and app.read_at == {}
        app.fetch_ended("djp3/agent-frolic")                          # and a read ends after that
        assert app.read_at == {}


@drive
async def test_a_repository_shown_while_the_token_is_checked_is_read_by_the_clock(isolated):
    """Showing it asks for a read, but not while a pasted token is with GitHub. That one is
    refused, the stored one stands, and nothing else would ever read it."""
    bad = "ghp_bad" + "x" * 33
    three_repos()
    FakeGitHub.gates[bad] = threading.Event()                     # GitHub is slow to refuse it
    app = main.Frolic()
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        await to_setup(app, pilot)
        await paste(app, pilot, bad)
        await until(pilot, lambda: bad in FakeGitHub.waiting)
        FakePulls.asked.clear()
        app.show_repo("djp3/hidden-one", True)                        # shown, while the token is checked
        await pilot.pause()
        assert FakePulls.asked == [] and "djp3/hidden-one" not in app.read_at     # no read, and no timer
        FakeGitHub.gates[bad].set()
        await settle(app, pilot)
        assert app.token_state == "ok" and FakePulls.asked == []
        app.tick()                                                    # the clock finds it has never been read
        await settle(app, pilot)
        assert FakePulls.asked == ["djp3/hidden-one"] and "djp3/hidden-one" in app.read_at
        app.tick()
        await settle(app, pilot)
        assert FakePulls.asked == ["djp3/hidden-one"]                 # once: now it has a timer


@drive
async def test_a_read_that_a_webhook_asked_for_starts_that_repositorys_timer_again(isolated):
    """GitHub has just said what changed there, and it was read: the clock has nothing to add."""
    three_repos()
    app = main.Frolic()
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        FakePulls.asked.clear()
        age(app, minutes=app.settings.poll_minutes - 1)               # four of the five minutes have gone
        app.delivered(Delivery("pull_request", "d-1", "djp3/agent-frolic", "opened"))
        await settle(app, pilot)
        assert FakePulls.asked == ["djp3/agent-frolic"]
        assert time.time() - app.read_at["djp3/agent-frolic"] < 5
        age(app, minutes=1)                                           # the fifth: the other one's time is up,
        app.tick()                                                    # and this one's is a minute old
        await settle(app, pilot)
        assert FakePulls.asked == ["djp3/agent-frolic", "Acme/ledger-api"]


@drive
async def test_a_repository_github_sends_webhooks_for_is_read_on_a_slow_timer(isolated):
    """There, silence means nothing changed. The timer only catches a delivery that never came."""
    three_repos()
    keychain.put(keychain.NGROK_AUTHTOKEN, AUTHTOKEN)
    Settings(webhooks=True, ngrok_domain="frolic.ngrok-free.dev").save()
    app = main.Frolic()
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        ours, theirs_ = app.store.get("djp3/agent-frolic"), app.store.get("Acme/ledger-api")
        assert app.tunnel_state == "open" and app.covered(ours) and not app.covered(theirs_)    # not ours to hook
        await read_since_hooked(app, pilot)
        assert app.period(theirs_) == 5 * 60 and app.period(ours) == app.SAFETY_POLLS * 5 * 60 == 30 * 60
        ran, left = app.timer("djp3/agent-frolic")
        assert ran < 0.05 and 29 * 60 < left <= 30 * 60               # just read: its half hour is ahead of it
        assert 4 * 60 < app.timer("Acme/ledger-api")[1] <= 5 * 60     # and that one's five minutes
        FakePulls.asked.clear()
        age(app)                                                      # the poll's five minutes
        app.tick()
        await settle(app, pilot)
        assert FakePulls.asked == ["Acme/ledger-api"]
        assert 0.15 < app.timer("djp3/agent-frolic")[0] < 0.20        # five of its thirty have run
        age(app, minutes=app.settings.poll_minutes * (app.SAFETY_POLLS - 1))      # half an hour in all
        app.tick()
        await settle(app, pilot)
        assert sorted(FakePulls.asked) == ["Acme/ledger-api", "Acme/ledger-api", "djp3/agent-frolic"]

        app.delivered(Delivery("pull_request", "d-2", "djp3/agent-frolic", "opened"))
        await settle(app, pilot)
        assert app.timer("djp3/agent-frolic")[0] < 0.05               # a delivery's read starts its timer again
        FakePulls.asked.clear()
        await turn_webhooks_off(app, pilot)                           # no more deliveries will come:
        await settle(app, pilot)
        assert not app.covered(ours) and app.period(ours) == 5 * 60   # it is back on the short timer
        assert 4 * 60 < app.timer("djp3/agent-frolic")[1] <= 5 * 60   # and counts from five minutes
        age(app)
        app.tick()
        await settle(app, pilot)
        assert sorted(FakePulls.asked) == ["Acme/ledger-api", "djp3/agent-frolic"]


@drive
async def test_a_repository_is_on_the_slow_timer_only_once_it_has_been_read_since_its_hook_went_live(isolated):
    """What changed between its last read and the hook going live was told to nobody."""
    three_repos()
    keychain.put(keychain.NGROK_AUTHTOKEN, AUTHTOKEN)
    Settings(ngrok_domain="frolic.ngrok-free.dev").save()             # webhooks are off, for now
    app = main.Frolic()
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        ours = app.store.get("djp3/agent-frolic")
        age(app, minutes=4)                                           # read four minutes ago,
        await to_setup(app, pilot)
        await pilot.press("w")                                        # and now webhooks are turned on
        await pilot.pause()
        await fill(app, pilot, authtoken=AUTHTOKEN, domain="frolic.ngrok-free.dev")
        await settle(app, pilot)
        assert app.tunnel_state == "open" and app.covered(ours)
        assert app.period(ours) == 5 * 60                             # not yet: a change in those four minutes
        FakePulls.asked.clear()                                       # was told to nobody
        age(app, minutes=1)
        app.tick()                                                    # so the fifth minute still reads it,
        await settle(app, pilot)
        assert "djp3/agent-frolic" in FakePulls.asked
        assert app.period(ours) == 30 * 60                            # and from that read on, silence means quiet
        FakePulls.asked.clear()
        age(app)
        app.tick()
        await settle(app, pilot)
        assert FakePulls.asked == ["Acme/ledger-api"]


@drive
async def test_a_tunnel_that_stops_leading_here_is_noticed_and_the_short_timers_take_over(isolated, monkeypatch):
    """It is checked when it opens. It can die later: another ngrok session takes the address,
    the network changes. Then no delivery arrives, and silence means nothing."""
    three_repos()
    keychain.put(keychain.NGROK_AUTHTOKEN, AUTHTOKEN)
    Settings(webhooks=True, ngrok_domain="frolic.ngrok-free.dev").save()
    app = main.Frolic()
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        await read_since_hooked(app, pilot)
        ours = app.store.get("djp3/agent-frolic")
        assert app.tunnel_state == "open" and app.period(ours) == 30 * 60
        asked = []
        leads_here = [True]
        monkeypatch.setattr(main, "wait_reachable", lambda url, nonce, **how: asked.append(url) or leads_here[0])
        app.tick()
        await settle(app, pilot)
        assert asked == []                                            # checked a moment ago: not yet again
        app.tunnel_checked_at -= app.settings.poll_minutes * 60 + 1
        app.tick()
        await settle(app, pilot)
        assert asked == ["https://frolic.ngrok-free.dev"] and app.tunnel_state == "open"     # still good

        leads_here[0] = False                                         # the tunnel dies
        app.tunnel_checked_at -= app.settings.poll_minutes * 60 + 1
        app.tick()
        await settle(app, pilot)
        assert app.tunnel_state == "unreachable" and not app.covered(ours) and app.period(ours) == 5 * 60
        assert any("no longer leads back to this app" in text for text in said(app))
        FakePulls.asked.clear()
        age(app)
        app.tick()
        await settle(app, pilot)
        assert sorted(FakePulls.asked) == ["Acme/ledger-api", "djp3/agent-frolic"]      # both, every five minutes

        leads_here[0] = True                                          # and comes back
        confirmed = app.hooked.get("djp3/agent-frolic")
        app.tunnel_checked_at -= app.settings.poll_minutes * 60 + 1
        app.tick()
        await settle(app, pilot)
        assert app.tunnel_state == "open" and app.covered(ours)
        assert app.hooked["djp3/agent-frolic"] > (confirmed or 0)     # its hook is confirmed afresh,
        assert app.period(ours) == 5 * 60                             # and it is read once more
        await read_since_hooked(app, pilot)                           # before silence means quiet again
        assert app.period(ours) == 30 * 60


@drive
async def test_a_repository_renamed_on_github_goes_on_being_one_github_sends_webhooks_for(isolated):
    """Its webhook moved with it, and goes on sending."""
    three_repos()
    keychain.put(keychain.NGROK_AUTHTOKEN, AUTHTOKEN)
    Settings(webhooks=True, ngrok_domain="frolic.ngrok-free.dev").save()
    app = main.Frolic()
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        await read_since_hooked(app, pilot)
        hook_id = app.store.get("djp3/agent-frolic").hook_id
        assert hook_id and app.period(app.store.get("djp3/agent-frolic")) == 30 * 60
        for hook in FakeGitHub.hooks_made.values():                   # renamed on GitHub: the hook goes along
            hook.repo = "djp3/agent-barn" if hook.repo == "djp3/agent-frolic" else hook.repo
        FakePulls.spelled = {"djp3/agent-frolic": "djp3/agent-barn"}
        FakePulls.open_["djp3/agent-barn"] = FakePulls.open_["djp3/agent-frolic"]
        age(app, minutes=30, only=["djp3/agent-frolic"])
        app.tick()                                                    # the next read finds the new name
        await settle(app, pilot)
        ours = app.store.get("djp3/agent-barn")
        assert ours is not None and app.store.get("djp3/agent-frolic") is None
        assert sorted(app.hooked) == ["djp3/agent-barn"] and app.covered(ours)    # confirmed under it at once,
        assert ours.hook_id == hook_id and len(FakeGitHub.hooks_made) == 1        # the same hook, not a second
        await read_since_hooked(app, pilot)
        assert app.period(ours) == 30 * 60                            # and back on the slow timer

        app.repo_known("Acme/ledger", True, False, False, "main", "Acme/ledger-api")     # one with no hook
        await settle(app, pilot)
        assert not any(text.startswith("Acme/ledger: ") and "webhook" in text for text in said(app))


@drive
async def test_a_hook_confirmed_for_the_tunnel_before_does_not_count_for_the_one_open_now(isolated, monkeypatch):
    """Webhooks are started again, with another address say. Until GitHub has each hook
    pointing there, its deliveries go to the old one."""
    three_repos()
    keychain.put(keychain.NGROK_AUTHTOKEN, AUTHTOKEN)
    Settings(webhooks=True, ngrok_domain="frolic.ngrok-free.dev").save()
    app = main.Frolic()
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        ours = app.store.get("djp3/agent-frolic")
        assert app.covered(ours)
        before = app.webhooks_run

        def refused(*args, **kwargs):
            raise main.GitHubError(0, "x", "GitHub could not be reached. Check the network and try again.")

        moved = main.ensure_hook
        monkeypatch.setattr(main, "ensure_hook", refused)             # the hooks cannot be moved just now
        app.start_webhooks()
        await settle(app, pilot)
        assert app.tunnel_state == "open" and app.webhooks_run > before
        assert not app.covered(ours) and app.period(ours) == 5 * 60   # not taken for covered meanwhile
        app.hook_done("djp3/agent-frolic", 100, "webhook #100 registered", before)    # an answer for the old tunnel
        assert not app.covered(ours)                                                  # comes late: it is not this one's
        monkeypatch.setattr(main, "ensure_hook", moved)
        await to_setup(app, pilot)
        await pilot.press("r")                                        # registered again, for the tunnel open now
        await settle(app, pilot)
        assert app.covered(ours)
        await turn_webhooks_off(app, pilot)
        await settle(app, pilot)
        assert app.hooked == {}


@drive
async def test_while_github_works_out_whether_a_pull_request_can_merge_the_timer_stays_short(isolated):
    """It works that out a little after a push to the base branch, and sends no webhook
    when it knows. Left to the slow timer, "ready to merge" would be missing for half an hour."""
    three_repos()
    keychain.put(keychain.NGROK_AUTHTOKEN, AUTHTOKEN)
    Settings(webhooks=True, ngrok_domain="frolic.ngrok-free.dev").save()
    app = main.Frolic()
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        await read_since_hooked(app, pilot)
        ours = app.store.get("djp3/agent-frolic")
        assert app.period(ours) == 30 * 60
        settled = FakePulls.open_["djp3/agent-frolic"]
        FakePulls.open_["djp3/agent-frolic"] = [dict(n, mergeable="UNKNOWN") for n in settled]
        app.delivered(Delivery("push", "d-1", "djp3/agent-frolic", ""))       # something landed on the base
        await settle(app, pilot)
        assert {p.mergeable for p in app.pulls["djp3/agent-frolic"].pulls} == {"unknown"}
        assert app.period(ours) == 5 * 60                             # GitHub will not say when it knows
        FakePulls.open_["djp3/agent-frolic"] = settled
        FakePulls.asked.clear()
        age(app, only=["djp3/agent-frolic"])
        app.tick()
        await settle(app, pilot)
        assert FakePulls.asked == ["djp3/agent-frolic"]
        assert app.period(ours) == 30 * 60                            # known: silence means quiet again


@drive
async def test_what_counts_as_covered_is_a_record_of_confirmed_hooks_not_the_words_about_them(isolated):
    three_repos()
    keychain.put(keychain.NGROK_AUTHTOKEN, AUTHTOKEN)
    Settings(webhooks=True, ngrok_domain="frolic.ngrok-free.dev").save()
    app = main.Frolic()
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        ours, theirs_ = app.store.get("djp3/agent-frolic"), app.store.get("Acme/ledger-api")
        assert sorted(app.hooked) == ["djp3/agent-frolic"] and app.covered(ours) and not app.covered(theirs_)
        app.hook_state["djp3/agent-frolic"] = "said some other way"       # the words may change:
        assert app.covered(ours)                                          # they decide nothing
        app.hook_done("Acme/ledger-api", 0, "webhook failed: it could not be registered")
        assert not app.covered(theirs_)                                   # nor does a failure that ends alike
        app.hook_done("djp3/agent-frolic", 0, "webhook failed: GitHub could not be reached")
        assert not app.covered(ours) and app.period(ours) == 5 * 60       # a hook that failed is not coverage
        app.hook_done("djp3/agent-frolic", 7, "webhook #7 registered", app.webhooks_run)
        assert app.covered(ours)
        app.hook_gone("djp3/agent-frolic")
        assert not app.covered(ours) and "djp3/agent-frolic" not in app.hooked


@drive
async def test_a_read_that_failed_is_tried_again_on_the_short_timer_webhook_or_not(isolated):
    """Silence from GitHub means nothing changed only if the last read of it worked."""
    three_repos()
    keychain.put(keychain.NGROK_AUTHTOKEN, AUTHTOKEN)
    Settings(webhooks=True, ngrok_domain="frolic.ngrok-free.dev").save()
    app = main.Frolic()
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        ours = app.store.get("djp3/agent-frolic")
        await read_since_hooked(app, pilot)
        assert app.covered(ours) and app.period(ours) == 30 * 60
        FakePulls.broken["djp3/agent-frolic"] = "GitHub's rate limit is used up for now. Try again in a few minutes."
        FakePulls.asked.clear()
        app.delivered(Delivery("pull_request", "d-1", "djp3/agent-frolic", "opened"))     # GitHub says it changed,
        await settle(app, pilot)                                                          # and the read fails
        assert FakePulls.asked == ["djp3/agent-frolic"] and "djp3/agent-frolic" in app.pull_trouble
        assert app.period(ours) == 5 * 60                             # so it is not left for half an hour
        age(app, only=["djp3/agent-frolic"])
        del FakePulls.broken["djp3/agent-frolic"]
        app.tick()
        await settle(app, pilot)
        assert FakePulls.asked == ["djp3/agent-frolic"] * 2 and "djp3/agent-frolic" not in app.pull_trouble
        assert app.period(ours) == 30 * 60                            # read: back on the slow timer


@drive
async def test_after_the_mac_has_slept_a_repository_that_relies_on_its_webhook_is_read_at_once(isolated, monkeypatch):
    """What GitHub sent meanwhile reached nobody, and GitHub does not send it again."""
    three_repos()
    keychain.put(keychain.NGROK_AUTHTOKEN, AUTHTOKEN)
    Settings(webhooks=True, ngrok_domain="frolic.ngrok-free.dev").save()
    app = main.Frolic()
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        assert app.covered(app.store.get("djp3/agent-frolic"))
        now = a_clock(monkeypatch)
        for key in app.read_at:
            app.read_at[key] = now[0]                                 # both just read
        app.tick()
        FakePulls.asked.clear()
        now[0] += 30                                                  # a slow moment is not a sleep
        app.tick()
        await settle(app, pilot)
        assert FakePulls.asked == []
        now[0] += 3 * 60                                              # three minutes with the lid shut: the one
        app.tick()                                                    # on the short timer is not due yet, and the
        await settle(app, pilot)                                      # other would not be for half an hour
        assert FakePulls.asked == ["djp3/agent-frolic"]
        assert "the Mac was asleep: reading again the repositories GitHub sends webhooks for" in said(app)
        now[0] += 1
        app.tick()
        await settle(app, pilot)
        assert FakePulls.asked == ["djp3/agent-frolic"]               # once

        now[0] += 20 * 60                                             # a long sleep: both, and each once
        app.tick()
        await settle(app, pilot)
        assert sorted(FakePulls.asked) == ["Acme/ledger-api", "djp3/agent-frolic", "djp3/agent-frolic"]


@drive
async def test_repositories_whose_timers_run_out_moments_apart_are_read_by_one_worker(isolated, monkeypatch):
    """Read one after another, they end a second or two apart, and their timers with them.
    Left at that, each would have a worker and a look in the Keychain of its own from then on."""
    three_repos()
    RepoStore().add("djp3/quiet-one", admin=True)
    app = main.Frolic()
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        now = a_clock(monkeypatch)
        app.tick()
        span = app.settings.poll_minutes * 60
        app.read_at["djp3/agent-frolic"] = now[0] - span              # its timer has just run out,
        app.read_at["acme/ledger-api"] = now[0] - span + 2            # the next one's does in two seconds,
        app.read_at["djp3/quiet-one"] = now[0] - span + 60            # and the last one's in a minute
        workers = []
        fetch = app.fetch
        monkeypatch.setattr(app, "fetch", lambda wanted, token_used: workers.append([name for name, _ in wanted])
                            or fetch(wanted, token_used))
        app.tick()
        await settle(app, pilot)
        assert workers == [["djp3/agent-frolic", "Acme/ledger-api"]]  # one worker, in the list's order

        now[0] += 3                                                   # nothing more when the second one's
        app.tick()                                                    # own moment comes: it has been read
        await settle(app, pilot)
        assert len(workers) == 1
        now[0] += 57                                                  # the last is read at its own time:
        app.tick()                                                    # nothing else is near
        await settle(app, pilot)
        assert workers[1:] == [["djp3/quiet-one"]]


@drive
async def test_the_log_says_what_asked_for_each_read(isolated, caplog):
    """Each read costs part of the hour's allowance. What used it up is a question for the log."""
    caplog.set_level(logging.INFO, logger="agent_frolic")
    three_repos()
    app = main.Frolic()
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        assert "reading (start): djp3/agent-frolic, Acme/ledger-api" in caplog.messages
        age(app, only=["Acme/ledger-api"])
        app.tick()
        await settle(app, pilot)
        assert "reading (poll): Acme/ledger-api" in caplog.messages
        app.delivered(Delivery("pull_request", "d-1", "djp3/agent-frolic", "opened"))
        await settle(app, pilot)
        assert "reading (webhook): djp3/agent-frolic" in caplog.messages
        await pilot.press("u")
        await settle(app, pilot)
        assert "reading (asked): djp3/agent-frolic, Acme/ledger-api" in caplog.messages
        assert not any(text.startswith("reading (") for text in said(app))       # the log file's, not the screen's


@drive
async def test_a_repository_not_read_yet_or_not_in_the_list_is_due(isolated):
    """What the app says of its timer agrees with itself: all of it has run, none is left."""
    three_repos()
    app = main.Frolic()
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        ran, left = app.timer("djp3/agent-frolic")
        assert ran < 0.05 and 4 * 60 < left <= 5 * 60                 # just read
        assert app.timer("nobody/nothing") == (1.0, 0.0)              # not in the list
        app.read_at.pop("djp3/agent-frolic")
        assert app.timer("djp3/agent-frolic") == (1.0, 0.0)           # in it, and not read yet


@drive
async def test_a_timer_is_never_shorter_than_a_minute_whatever_the_setting_holds(isolated):
    """Settings.load keeps the poll's minutes from 1 up. A setting that reached the app some
    other way, at 0, would make a timer of no length: every repository read every second,
    and the bars dividing by nothing."""
    three_repos()
    app = main.Frolic()
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        app.settings.poll_minutes = 0
        ours = app.store.get("djp3/agent-frolic")
        assert app.period(ours) == 60
        assert app.timer("djp3/agent-frolic")[0] < 0.1                # and its bar still draws
        FakePulls.asked.clear()
        app.tick()
        await settle(app, pilot)
        app.draw()
        assert FakePulls.asked == []                                  # not every second


@drive
async def test_u_reads_every_shown_repository_now_and_every_timer_starts_again(isolated):
    three_repos()
    app = main.Frolic()
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        FakePulls.asked.clear()
        age(app, minutes=3)
        await pilot.press("u")
        await settle(app, pilot)
        assert sorted(FakePulls.asked) == ["Acme/ledger-api", "djp3/agent-frolic"]
        assert all(time.time() - at < 5 for at in app.read_at.values())
        app.tick()
        await settle(app, pilot)
        assert len(FakePulls.asked) == 2
