# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""Stand-ins for GitHub, ngrok, the pull request fetch, the agent-grange and the browser,
and the small helpers the UI tests share. conftest.py puts the stand-ins in place for every
test, so no test can reach the real GitHub, open a real tunnel, ask the grange that may be
running on this Mac, or open a page in the real browser."""
import asyncio
import functools
import queue
import threading
import time
from datetime import datetime, timezone

from textual.widgets import Input

import main
from githubapi import GitHubError, Hook, Identity, RepoInfo
from grange import Farm, GrangeError, Reading, Reported
from pulls import RepoPulls, parse_pull, stack

TOKEN = "ghp_abcdefghijklmnopqrstuvwxyz0123456789"
AUTHTOKEN = "2abcDEFghiJKLmnoPQRstuVWXyz_3abcdefghijklmnop"


class FakeGitHub:
    """GitHub as the app sees it. Tokens starting "ghp_bad" are refused; repositories under
    Acme are private and not ours to administer. While `unreachable` is above zero, each
    check of a token fails as a network failure does, and counts it down. The viewer is on
    the teams in `teams_listed`; reading them fails with `teams_broken` when that is set."""
    hooks_made: dict = {}
    deleted: list = []
    tokens_seen: list = []
    unreachable = 0
    renamed: dict = {}            # "owner/old" (lower case) -> the name GitHub answers with now
    gates: dict = {}              # a token, a repository (lower case), "teams" (every read of the
                                  # teams) or ("teams", token) (that token's) -> a threading.Event: the
    waiting: set = set()          # answer about it waits for the event; `waiting` says it was asked
    logins: dict = {}             # a token -> the login it belongs to, when that is not djp3
    scopes: list | None = ["repo", "read:org"]    # the token's scopes; None makes it a fine-grained one
    teams_listed: list = []       # githubapi.Team, as GitHub lists them
    teams_by_name: dict = {}      # (org, slug) in lower case -> the githubapi.Team asking for it by
                                  # name finds; any other is refused, as one the token may not see is
    teams_broken: Exception | None = None
    teams_asked: list = []        # the token of each read of the teams, in order

    def __init__(self, token):
        self.token = token
        FakeGitHub.tokens_seen.append(token)

    @staticmethod
    def _hold(key):
        gate = FakeGitHub.gates.get(key)
        if gate is not None:
            FakeGitHub.waiting.add(key)
            gate.wait(5)

    def whoami(self):
        FakeGitHub._hold(self.token)
        if FakeGitHub.unreachable > 0:
            FakeGitHub.unreachable -= 1
            raise GitHubError(0, "could not reach GitHub: timed out",
                              "GitHub could not be reached. Check the network and try again.")
        if self.token.startswith("ghp_bad"):
            raise GitHubError(401, "Bad credentials", "GitHub did not accept the token. It may be mistyped, expired or revoked.")
        return Identity(FakeGitHub.logins.get(self.token, "djp3"),
                        None if FakeGitHub.scopes is None else list(FakeGitHub.scopes))

    def repo(self, name):
        FakeGitHub._hold(name.lower())
        if "missing" in name:
            raise GitHubError(404, "Not Found", "No such repository, or the token cannot see it.")
        name = FakeGitHub.renamed.get(name.lower(), name)             # GitHub follows a rename
        theirs = name.lower().startswith("acme/")
        return RepoInfo(name, private=theirs, admin=not theirs, default_branch="main")

    def teams(self):
        FakeGitHub.teams_asked.append(self.token)
        FakeGitHub._hold("teams")
        FakeGitHub._hold(("teams", self.token))
        if FakeGitHub.teams_broken is not None:
            raise FakeGitHub.teams_broken
        return list(FakeGitHub.teams_listed)

    def team(self, org, slug):
        found = FakeGitHub.teams_by_name.get((org.lower(), slug.lower()))
        if found is None:
            raise GitHubError(404, "Not Found", "No such repository, or the token cannot see it.")
        return found

    def hooks(self, name):
        return [h for h in FakeGitHub.hooks_made.values() if h.repo == name]

    def create_hook(self, name, url, secret, events=()):
        hook = Hook(100 + len(FakeGitHub.hooks_made), url)
        hook.repo, hook.secret = name, secret
        FakeGitHub.hooks_made[hook.id] = hook
        return hook

    def update_hook(self, name, hook_id, url, secret, events=(), active=True):
        hook = FakeGitHub.hooks_made[hook_id]
        hook.url, hook.secret = url, secret
        return hook

    def delete_hook(self, name, hook_id):
        FakeGitHub.deleted.append((name, hook_id))
        FakeGitHub.hooks_made.pop(hook_id, None)


class FakeTunnel:
    opened: list = []
    made: list = []               # every tunnel, in the order made
    gate = None                   # a threading.Event: when set here, start waits for it

    def __init__(self, domain):
        self.domain, self.running = domain, False
        FakeTunnel.made.append(self)

    @property
    def url(self):
        return f"https://{self.domain}"

    def start(self, port, authtoken):
        if FakeTunnel.gate is not None:
            FakeTunnel.gate.wait(5)
        if authtoken.startswith("bad"):
            raise main.TunnelError("ngrok did not accept the authtoken. Copy it again from your ngrok dashboard.")
        FakeTunnel.opened.append((self.domain, port, authtoken))
        self.running = True
        return self.url

    def stop(self):
        self.running = False


class FakePulls:
    """What is open on GitHub, per repository (lower case), and which repositories were asked
    for. A read made with a token waits while FakeGitHub.gates holds ("pulls", that token)."""
    open_: dict = {}
    asked: list = []
    broken: dict = {}
    tips: dict = {}               # repository (lower case) -> the commit its default branch points at
    spelled: dict = {}            # repository (lower case) -> how GitHub spells it, when not as asked
    costs: dict = {}              # repository (lower case) -> (what a reading costs, the hourly allowance)
    access: dict = {}             # a token -> what its account may do in every repository ("" unsaid)
    orgs: set = set()             # the repositories (lower case) an organization owns

    @staticmethod
    def fetch(github, name, now=None, long_lived=None):
        FakePulls.asked.append(name)
        FakeGitHub._hold(("pulls", github.token))
        if name.lower() in FakePulls.broken:
            raise GitHubError(200, "x", FakePulls.broken[name.lower()])
        theirs = FakePulls.spelled.get(name.lower(), name)
        pulls = stack([parse_pull(theirs, n) for n in FakePulls.open_.get(name.lower(), [])], "main", long_lived)
        cost, limit = FakePulls.costs.get(name.lower(), (0, 0))
        return RepoPulls(theirs, "main", pulls, len(pulls), datetime.now(timezone.utc),
                         FakePulls.tips.get(name.lower(), ""), access=FakePulls.access.get(github.token, ""),
                         cost=cost, limit=limit, org=name.lower() in FakePulls.orgs)


class FakeBrowser:
    """Stands in for the user's browser: it remembers the pages it was asked to show."""
    opened: list = []
    broken: Exception | None = None       # set it, and showing a page fails this way

    @staticmethod
    def open(url):
        if FakeBrowser.broken is not None:
            raise FakeBrowser.broken
        FakeBrowser.opened.append(url)


class FakeGrange:
    """The agent-grange on this Mac. It holds every question, as a grange with nothing new
    to say does, until a test hands it an answer: `FakeGrange.answers.put(...)` with a
    Reading, or with a GrangeError for a grange that is down, out of date or in trouble."""
    answers: "queue.Queue" = queue.Queue()
    asked: list[tuple[int, int | None]] = []      # each question: the port, and the cursor it came with
    made: list["FakeGrange"] = []

    def __init__(self, port=7788):
        self.port, self.closed = port, threading.Event()
        FakeGrange.made.append(self)

    def read(self, after=None, wait=30):
        FakeGrange.asked.append((self.port, after))
        while not self.closed.is_set():
            try:
                answer = FakeGrange.answers.get(timeout=0.01)
            except queue.Empty:
                continue
            if isinstance(answer, Exception):
                raise answer
            return answer
        raise GrangeError("closed", "The question was given up.")

    def close(self):
        self.closed.set()


def team(*agents, cursor=1, quiet=()):
    """A grange's answer: this Mac's grange (djp3's) with `agents`, each (owner, name,
    state), and one more grange for each other owner among them. `quiet` names the owners
    whose farm has gone silent."""
    owners = ["djp3"] + sorted({owner for owner, _, _ in agents} - {"djp3"})
    return Reading(cursor, "djp3", tuple(
        Farm(owner, owner in quiet,
             tuple(Reported(o, name, state, owner in quiet) for o, name, state in agents if o == owner))
        for owner in owners))


async def until(pilot, done, seconds=5.0):
    """Let the app run until `done()`: for what arrives from a thread of its own."""
    end = time.monotonic() + seconds
    while not done():
        assert time.monotonic() < end, "it never happened"
        await pilot.pause(0.02)


def drive(test):
    """Run an async test with plain pytest, the way agent farm's UI tests do."""
    @functools.wraps(test)
    def run(*args, **kwargs):
        return asyncio.run(test(*args, **kwargs))
    return run


async def settle(app, pilot):
    """Let the workers finish, and the workers they start, and the screen catch up."""
    for _ in range(6):
        await app.workers.wait_for_complete()
        await pilot.pause()


def age(app, minutes=None, only=None):
    """Let time pass for the timers: as if `minutes` had gone by since each repository was
    last read (the poll's own minutes when not said; `only` names the ones it passes for),
    and since the viewer's teams were last asked for."""
    seconds = (app.settings.poll_minutes if minutes is None else minutes) * 60 + 1
    for key in app.read_at:
        if only is None or key in {name.lower() for name in only}:
            app.read_at[key] -= seconds
    if only is None:
        app.teams_tried_at -= seconds


async def to_setup(app, pilot):
    """With a token stored the app starts on the board; these tests are about the setup."""
    if app.screen is not app.setup:
        await pilot.press("s")
        await pilot.pause()


def screen(app) -> str:
    return "\n".join(strip.text.rstrip() for strip in app.screen._compositor.render_strips())


async def fill(app, pilot, **values):
    """Type into the open dialog's inputs and press enter on each."""
    for name, value in values.items():
        box = app.screen.query_one(f"#{name}", Input)
        box.focus()
        box.value = value
        await pilot.press("enter")
    await settle(app, pilot)
