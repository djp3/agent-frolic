# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""
agent-frolic, the real program: the app, its state, and everything that talks to the
outside (GitHub, the ngrok tunnel, the Keychain, the agent-grange on this Mac). It has two
screens.

  • THE BOARD (board.py) is the main screen: who is on which pull request and what each
    one waits on, drawn from what this app has read.
  • THE SETUP (the SetupScreen below) is the plumbing, in four parts: the GitHub token
    (pasted once, checked with GitHub, kept in the Keychain), the repositories (added by
    name or address, kept in repos.json, shown or hidden), the webhooks (an ngrok
    tunnel to a receiver on this Mac, and a webhook on each shown repository you
    administer), and the agents (whether the agent-grange on this Mac answers, and what
    it reports). Below them is a log, or on p the pull requests as a plain list.

The app starts on the setup while there is no token, otherwise on the board. s goes from
the board to the setup; s or escape goes back.

    python3 -m venv .venv && .venv/bin/pip install --require-hashes -r requirements.lock
    .venv/bin/python main.py
    .venv/bin/python -m pytest                     # the tests: no network, no real Keychain

The pull requests are read when the app starts, whenever a webhook arrives for a
repository, on a timer, and on request (u). Each repository has a timer of its own, which
starts again whenever it has been read, whatever asked for the read: so one that a webhook
has just had read is not read again by the clock a moment later. A repository GitHub
sends webhooks for is read on its timer only now and then, to catch a delivery that never
came; the others every few minutes (Frolic.period). When a reading differs from the one
before, the board washes the rows that changed and says what changed.

The teams the viewer is on are read once the token is accepted and again on u, on their
own: a review asked of one of them is the viewer's to give, and the board draws it so.
The list of them is kept in memory, and written to no settings file. A line saying what
changed may name a team, as it names people, and such a line goes to the log. Until they
are known, a request to a team is held back as nobody's; if they cannot be read, it is
anyone else's, as if there were no teams.

The team's agents come from the agent-grange on this Mac (grange.py), asked on 127.0.0.1
alone, from mount to unmount, by a thread of its own: each question is held by the grange
until its picture changes, so an agent that starts or stops shows within moments. Each
answer is the whole picture and replaces the last; the board's picture puts each agent on
the pull request its name says (picture.placed). A Mac with no grange is no trouble: the
board is then of people and teams alone. When a grange that was answering stops, what it
last reported stays for a minute and is then let go.

Setup keys: t paste a token, f forget it, a add a repository, up/down pick one, shift+up /
shift+down (or [ and ]) move it up or down the list, space show or hide it, x remove it,
b set its long-lived branches (the ones never drawn as a stack), w set up webhooks,
W turn them off, r register the hooks again, p switch between the pull requests and the
log, u read GitHub now, s or escape back to the board, q quit. In the pull request list the arrows and page up / page down scroll.

Everything that talks to GitHub, ngrok or the grange runs on a thread of its own and reports
back to the screen; the screen never waits on the network. A secret is read from the Keychain by the
worker that needs it and is not kept on the app: the screen only ever holds its mask.
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from collections import deque
from contextlib import contextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Callable, Iterator, Mapping, NoReturn, TextIO
from urllib.parse import urlsplit

from rich.cells import cell_len
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import Input, Static

import keychain
from apppaths import app_version, log_dir
from board import Board, remember_change
from canvas import cut
from githubapi import GitHub, GitHubError, Identity, RepoInfo, Team, ensure_hook, teams_above
from grange import Grange, GrangeError, Reading, Reported, Watch
from picture import AGE_FIGURE, CHECK_MARK, Picture, changes, labels, located, quiet, review_marks
from pulls import PullRequest, RepoPulls, fetch_pulls, is_team, span, stack, team_of, team_slug
from repos import Repo, RepoStore, parse
from settings import Settings
from theme import (AXIS, CRIT, DIVIDER, GOOD, INK, INK2, MUTED, PLANE, RAISED, SURFACE,
                   WAITING, WARN)
from tunnel import Tunnel, TunnelError, clean_domain, hook_url, wait_reachable
from webhook import Delivery, Receiver

log = logging.getLogger("agent_frolic")
NO_TEAMS: Mapping[str, str] = MappingProxyType({})   # the viewer's teams while none are known
# The terminal's title: the Mac app shows it in its title bar (and so in the Dock, Mission
# Control and the window switcher), iTerm2 in its tab. After the name and version, a hammer
# for each repository being watched: everyone brings one to a barn raising.
VERSION = app_version()
WINDOW_TITLE = f"🐓 agent-frolic v{VERSION}" if VERSION else "🐓 agent-frolic"
HAMMER = "🔨"


def title_sequence(title: str) -> str:
    """OSC 0 sets the terminal's window title; Textual never sets one itself. An empty
    title hands the choice back to the terminal."""
    return f"\x1b]0;{title}\x07"


def window_title(watched: int) -> str:
    return f"{WINDOW_TITLE} {HAMMER * watched}" if watched > 0 else WINDOW_TITLE


OPEN_WAIT_S = 10.0             # how long `open` is waited on to say whether it could start the browser
ON_MAC = sys.platform == "darwin"   # where `open` shows a page (elsewhere Python's webbrowser is asked,
                                    # which nothing here promises: only the Mac is supported for now)


class NotOnGitHub(ValueError):
    """An address that is not a plain page on github.com, so it is not opened. str() is fit
    to show; no other error's words are (they are Python's, about its own insides)."""


def open_page(url: str) -> None:
    """Show a page of github.com in the user's browser.

    The address comes out of GitHub's reply, and whatever opens it (`open` on a Mac) would
    act on any kind of address it is handed: a file, another program's scheme. So only a
    plain https address on github.com itself is passed on, checked as the very text that
    will be opened: printable ASCII with no spaces and no backslashes (where Python's
    reading of an address and a browser's part ways), and no user, password or port.
    Anything else raises NotOnGitHub. OSError or subprocess.SubprocessError says the browser
    could not be started."""
    plain = url.isascii() and url.isprintable() and " " not in url and "\\" not in url
    try:
        parts = urlsplit(url) if plain else None
    except ValueError:                                     # "https://[github.com]/x": no address at all
        parts = None
    if parts is None or parts.scheme != "https" or parts.netloc.lower() != "github.com":
        raise NotOnGitHub("it is not a page on github.com")
    if ON_MAC:
        # By its full path, and as one argument: nothing here goes through a shell.
        opener = subprocess.Popen(["/usr/bin/open", url], stdin=subprocess.DEVNULL,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            code = opener.wait(timeout=OPEN_WAIT_S)
        except subprocess.TimeoutExpired:
            return             # still at it: a browser slow to start has not failed, so `open` is
                               # left to finish, not stopped and reported as a browser that would not start
        if code:
            raise subprocess.CalledProcessError(code, "open")
    elif not webbrowser.open(url):
        raise OSError("no browser could be started")

TOKEN_HELP = [
    "It is checked with GitHub, then kept in your Mac's Keychain and nowhere else.",
    "",
    "A classic token needs the repo scope.",
    "A fine-grained token needs, on each repository: Pull requests (read),",
    "Contents (read), Metadata (read), and Webhooks (read and write) for webhooks.",
    "A fine-grained token sees the teams of one organization only.",
]
WEBHOOK_HELP = [
    "GitHub delivers changes to a public address; ngrok gives this Mac one.",
    "Both values are on your ngrok dashboard. A free account has one domain,",
    "and it looks like your-name.ngrok-free.dev.",
    "",
    "The authtoken is kept in your Keychain. The domain is not a secret.",
]


# ---- small dialogs ---------------------------------------------------------------------

@dataclass
class Field:
    name: str
    label: str
    secret: bool = False
    value: str = ""
    placeholder: str = ""


class Ask(ModalScreen[dict | None]):
    """A title, a few lines of explanation and one or more inputs. Enter moves to the next
    input and, on the last, answers; escape cancels. `check` may refuse the answer with a
    sentence, which is shown and keeps the dialog open."""

    BINDINGS = [Binding("escape", "cancel", show=False)]

    def __init__(self, title: str, about: list[str], fields: list[Field],
                 check: Callable[[dict], str | None] | None = None) -> None:
        super().__init__()
        self.title_text, self.about, self.fields, self.check = title, about, fields, check

    def compose(self) -> ComposeResult:
        with Vertical(id="box"):
            yield Static(Text(self.title_text, style=f"bold {INK}"), id="title")
            yield Static(Text("\n".join(self.about), style=INK2), id="about")
            for f in self.fields:
                yield Static(Text(f.label, style=MUTED), classes="label")
                yield Input(value=f.value, placeholder=f.placeholder, password=f.secret, id=f.name)
            yield Static(id="error")
            yield Static(Text("enter saves · esc cancels", style=MUTED), id="keys")

    def on_mount(self) -> None:
        self.query_one(f"#{self.fields[0].name}", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        names = [f.name for f in self.fields]
        at = names.index(event.input.id)
        if at + 1 < len(names):
            self.query_one(f"#{names[at + 1]}", Input).focus()
            return
        values = {name: self.query_one(f"#{name}", Input).value.strip() for name in names}
        trouble = self.check(values) if self.check else None
        if trouble:
            self.query_one("#error", Static).update(Text(trouble, style=INK))
            return
        self.dismiss(values)

    def action_cancel(self) -> None:
        self.dismiss(None)


class Confirm(ModalScreen[bool]):
    BINDINGS = [Binding("y", "answer(True)", show=False), Binding("n", "answer(False)", show=False),
                Binding("escape", "answer(False)", show=False)]

    def __init__(self, question: str, detail: str = "") -> None:
        super().__init__()
        self.question, self.detail = question, detail

    def compose(self) -> ComposeResult:
        with Vertical(id="box"):
            yield Static(Text(self.question, style=f"bold {INK}"), id="title")
            if self.detail:
                yield Static(Text(self.detail, style=INK2), id="about")
            yield Static(Text("y yes · n no", style=MUTED), id="keys")

    def action_answer(self, yes: bool) -> None:
        self.dismiss(yes)


# ---- the setup screen ------------------------------------------------------------------

class SetupScreen(Screen):
    """The four parts of the setup, and the log or the plain list below them. It only
    lays the parts out and passes the keys on: the words and the work are the app's."""

    AUTO_FOCUS = None                              # nothing holds the keys: the bindings below act
    BINDINGS = [
        Binding("t", "app.token", show=False), Binding("f", "app.forget_token", show=False),
        Binding("a", "app.add", show=False),
        Binding("up", "app.move(-1)", show=False), Binding("down", "app.move(1)", show=False),
        Binding("pageup", "app.page(-1)", show=False), Binding("pagedown", "app.page(1)", show=False),
        Binding("shift+up", "app.reorder(-1)", show=False), Binding("shift+down", "app.reorder(1)", show=False),
        Binding("left_square_bracket", "app.reorder(-1)", show=False),     # for a terminal that does
        Binding("right_square_bracket", "app.reorder(1)", show=False),     # not pass shift+arrow on
        Binding("space", "app.toggle", show=False), Binding("x", "app.remove", show=False),
        Binding("b", "app.branches", show=False),
        Binding("w", "app.webhooks", show=False), Binding("W", "app.webhooks_off", show=False),
        Binding("r", "app.register", show=False),
        Binding("p", "app.view", show=False), Binding("u", "app.update", show=False),
        Binding("s", "app.setup_done", show=False), Binding("escape", "app.setup_done", show=False),
        Binding("q", "app.quit", show=False),
    ]

    def compose(self) -> ComposeResult:
        yield Static(id="top", classes="line")
        yield Static("─" * 400, classes="rule")
        yield Static(id="token", classes="part")
        yield Static("─" * 400, classes="rule")
        yield Static(id="repos", classes="part")
        yield Static("─" * 400, classes="rule")
        yield Static(id="hooks", classes="part")
        yield Static("─" * 400, classes="rule")
        yield Static(id="agents", classes="part")
        yield Static("─" * 400, classes="rule")
        with VerticalScroll(id="lower"):
            yield Static(id="log")
        yield Static("t token  f forget  a add repo  ↑↓ pick  [ ] move  space show/hide  x remove  b branches  "
                     "w webhooks  W off  r hooks  p pulls / log  u update  s board  q quit",
                     id="hints", classes="line")

    def on_mount(self) -> None:
        self.draw()

    def on_screen_resume(self) -> None:
        self.draw()

    def draw(self) -> None:
        if not self.is_mounted:
            return
        app = self.app
        self.query_one("#top", Static).update(app.top_text())
        self.query_one("#token", Static).update(app.token_text())
        self.query_one("#repos", Static).update(app.repos_text())
        self.query_one("#hooks", Static).update(app.hooks_text())
        self.query_one("#agents", Static).update(app.agents_text())
        self.query_one("#log", Static).update(app.pulls_text() if app.view == "pulls" else app.log_text())


# ---- the app ---------------------------------------------------------------------------

class Frolic(App):
    TOKEN_RETRY_S = 30            # how often a stored token GitHub could not be reached for is checked again
    WEBHOOK_SHARE = 0.5           # of GitHub's hourly allowance, the most that reads webhooks ask for may use
    WEBHOOK_SOONEST_S = 10.0      # the least time between two of them for one repository: a burst is read once
    SAFETY_POLLS = 6              # a repository GitHub sends webhooks for is read on its timer this many times
                                  # less often than one it does not: every 30 minutes at the usual five
    ASLEEP_S = 60.0               # the clock ticks every second: this long between two ticks, and the Mac slept
    TOGETHER_S = 15.0             # a repository due within this long of a read that is starting goes with it
    OPEN_AGAIN_S = 2.0            # o on the same pull request sooner than this after the last o is the key repeating
    NOTICE_S = 3.0                # how long a word about what the user just did takes the ticker's line,
    NOTICE_FAILED_S = 10.0        # and how long when it says that it failed
    GRANGE_GRACE_S = 60.0         # how long the agents last reported stay on the board once the grange
                                  # stops answering: it answers twice in that time when all is well
    GRANGE_STALE = "agent-grange on this Mac is out of date: s shows how to update it"
    GRANGE_LOST = "agent-grange stopped answering: no agents are shown"
    CSS = f"""
    Screen {{ background: {PLANE}; color: {INK}; }}
    .line {{ height: 1; margin: 0 2; text-wrap: nowrap; text-overflow: ellipsis; }}
    .rule {{ height: 1; margin: 0 1; color: {DIVIDER}; }}
    .part {{ height: auto; margin: 0 2; }}
    #lower {{ height: 1fr; margin: 0 2; }}
    #log {{ height: auto; }}
    #hints {{ color: {MUTED}; }}
    ModalScreen {{ align: center middle; background: {PLANE} 60%; }}
    #box {{ width: 78; height: auto; padding: 1 2; background: {SURFACE}; border: round {DIVIDER}; }}
    #about {{ margin: 1 0; }}
    .label {{ margin: 1 0 0 0; }}
    #error {{ height: auto; margin: 1 0 0 0; }}
    #keys {{ margin: 1 0 0 0; }}
    Input {{ background: {RAISED}; color: {INK}; border: tall {AXIS}; }}
    Input:focus {{ border: tall {DIVIDER}; }}
    """
    def __init__(self) -> None:
        super().__init__()
        self.settings = Settings.load()
        self.store = RepoStore()
        self.sel = 0
        # the token: only what may be shown is kept here
        self.token_state = "none"                 # none | checking | ok | bad | unchecked
        self.token_mask = ""
        self.token_note = ""
        self.token_tried_at = 0.0                 # when GitHub was last asked whose token it is
        self.token_kept: tuple = ("none", "", None, "")   # how things stood before a token was pasted
        self.token_run = 0                        # bumped by every check and by forgetting: an answer
                                                  # whose number is no longer this one is dropped
        self.token_used = 0                       # the token run whose check accepted the token in use:
                                                  # each read of the pull requests is made with that token,
                                                  # and one made with another is not used (pulls_known)
        self.pulls_for = ""                       # the login what is read of the pull requests is for,
                                                  # lower case: another account's sign-in forgets it
        self.token_lock = threading.Lock()        # storing a checked token and forgetting take turns
        self.identity: Identity | None = None
        # the teams the viewer is on: a review asked of one of them is theirs to give. The list
        # is kept here, and written to neither settings.json nor repos.json. That is not to say
        # no team is ever named in the log: a change line names a team as it names people
        self.teams: Mapping[str, str] | None = None   # "team/org/slug" in lower case -> the name
                                                  # people read; None while they are not known. Never
                                                  # changed in place: a read that answers replaces it
        self.teams_note = ""                      # why the last read of them failed, "" if it did not
        self.teams_retry = False                  # that failure may pass (no network, say), and none
                                                  # are known: the poll tries the read again
        self.teams_listed = 0                     # how many GitHub listed: the teams those are nested
                                                  # in are added to `teams`, and are not counted
        self.teams_for = ""                       # the login they are read for, lower case
        self.teams_seen = 0                       # the token run of the answer last used: an older one
                                                  # that comes after it is not used
        self.teams_run = 0                        # the token run whose read of them is under way, 0
                                                  # when none is
        # the webhooks
        self.receiver: Receiver | None = None
        self.tunnel: Tunnel | None = None
        self.tunnel_state = "off"                 # off | starting | open | unreachable | failed
        self.tunnel_note = ""
        self.webhooks_run = 0                     # bumped by every start and stop: a start under way
                                                  # whose number is no longer this one gives up
        self.webhooks_lock = threading.Lock()     # one start at a time: ngrok serves a domain once
        self.closing = threading.Event()          # set as the app closes: workers stop at their next chance
        self.authtoken_mask = ""                  # what was last read of the ngrok authtoken: its mask,
                                                  # "stored", "missing" or "unreadable"; "" until then
        self.hook_state: dict[str, str] = {}      # repository (lower case) -> what happened to its hook,
                                                  # in words, for the setup to show
        self.hooked: dict[str, float] = {}        # repository (lower case) -> when its webhook was confirmed:
                                                  # GitHub sends deliveries about it from then. What decides
                                                  # things is this record, never the words above
        self.hook_read_at: dict[str, float] = {}  # repository (lower case) -> when a delivery last had it read
        self.hook_read_due: dict[str, float] = {}  # repository (lower case) -> when its waiting read may start
        self.repo_note: dict[str, str] = {}       # repository (lower case) -> trouble seeing it
        self.lines: deque[tuple[float, str, str]] = deque(maxlen=300)
        # the pull requests
        self.view = "log"                         # what the lower part shows: log | pulls
        self.pulls: dict[str, RepoPulls] = {}     # repository (lower case) -> its open pull requests
        self.pull_trouble: dict[str, str] = {}    # repository (lower case) -> why the last fetch failed
        self.fetching: set[str] = set()           # repositories being fetched right now
        self.stale: set[str] = set()              # repositories that changed while being fetched
        self.read_at: dict[str, float] = {}       # repository (lower case) -> when a read of it last ended,
                                                  # whether it worked or not: its timer runs from then
        self.read_began: dict[str, float] = {}    # repository (lower case) -> when its last read was asked for
        self.teams_tried_at = 0.0                 # when the viewer's teams were last asked for
        self.ticked_at = 0.0                      # when the clock last ticked
        self.tunnel_checked_at = 0.0              # when the tunnel was last checked to lead back here (or
        self.tunnel_checking = False              # opened); and whether such a check is under way
        self.reported: tuple[Reported, ...] = ()  # the team's agents, as the agent-grange on this Mac last
                                                  # said: the picture puts each on its pull request
        self.grange_state = "asking"              # "asking" | "ok" | "down" | "stale" | "trouble"
        self.grange_wrong: GrangeError | None = None  # what is wrong, unless it is "asking" or "ok"
        self.grange_reading: Reading | None = None    # its last answer, for as long as that is believed
        self.grange_read_at = 0.0                 # when that answer came
        self.watch: Watch | None = None           # the thread that asks it, from mount to unmount
        self.flashes: dict[tuple[str, int], float] = {}   # a pull request that changed on a re-read, and when
        self.page_asked: tuple[tuple[str, int], float] | None = None   # the pull request o last asked for, and when
        self.news: tuple[float, str] | None = None        # the last thing that happened on GitHub, for the
                                                          # board's ticker (set by `happened` alone)
        self.notice: tuple[float, str] | None = None      # words in the ticker's place, and until when (announce)
        self.board = Board()
        self.setup = SetupScreen()
        self.title_shown: str | None = None       # the terminal title last written: none is written twice
        self.leaving = False                      # set as the app quits: the title stays cleared

    def on_mount(self) -> None:
        self.install_screen(self.board, "board")
        self.install_screen(self.setup, "setup")
        self.push_screen("board")
        if not keychain.has(keychain.GITHUB_TOKEN):    # nothing to draw from yet: begin at the setup
            self.push_screen("setup")
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGHUP):    # the Mac app stops the board this way: leave cleanly
            try:
                loop.add_signal_handler(sig, self.exit)
            except (NotImplementedError, RuntimeError, ValueError):
                pass
        self.say("started")
        self.set_window_title(window_title(self.watched()))
        self.set_interval(1.0, self.tick)
        self.check_stored_token()
        self.start_grange()

    def tick(self) -> None:
        """Once a second: each shown repository whose timer has run out is read again, with
        those whose timers are about to (TOGETHER_S), by one worker (and,
        at the pace of the poll, another try at the viewer's teams while a read of them has
        failed and none are known), another try at checking a stored token GitHub could not
        be reached for, and the ages ("3s ago") move on. A tick that comes long after the
        one before says the Mac slept (ASLEEP_S)."""
        now = time.time()
        slept, self.ticked_at = bool(self.ticked_at) and now - self.ticked_at > self.ASLEEP_S, now
        if self.token_state == "ok":
            shown = self.store.shown()
            due = [repo for repo in shown if self.due(repo, now)]
            missed = []
            if slept:
                # While the Mac slept, what GitHub sent reached nobody, and GitHub does not send
                # it again. A repository that relies on its webhook is read now, not when its
                # slow timer says. (The others are overdue by now, or soon will be.)
                missed = [repo for repo in shown
                          if repo not in due and repo.name.lower() not in self.fetching and self.covered(repo)]
                if missed:
                    self.say("the Mac was asleep: reading again the repositories GitHub sends webhooks for")
            if due or missed:
                # One worker reads its repositories one after another, with one look in the
                # Keychain, so the timers of those read together run out moments apart. What
                # is nearly due goes with what is due: or from the second round on, each would
                # be read by a worker of its own, a second or two after the last.
                soon = [repo for repo in shown
                        if repo not in (due + missed) and self.due(repo, now + self.TOGETHER_S)]
                together = due + missed + soon
                self.update_pulls([repo.name for repo in shown if repo in together], "woke" if missed else "poll")
            if self.teams_retry and now >= self.teams_tried_at + self.settings.poll_minutes * 60:
                self.start_teams_read()                    # a read of the teams that failed, with none
                                                           # known: until it works. Known ones are left be
        if self.token_state == "unchecked" and time.time() >= self.token_tried_at + self.TOKEN_RETRY_S:
            self.retry_token()
        if (self.tunnel is not None and self.receiver is not None and not self.tunnel_checking
                and self.tunnel_state in ("open", "unreachable")
                and (slept or now >= self.tunnel_checked_at + self.settings.poll_minutes * 60)):
            self.tunnel_checking, self.tunnel_checked_at = True, now       # does it still lead back here?
            self.check_tunnel(self.webhooks_run, self.tunnel.url, self.receiver.nonce)
        for key, at in list(self.hook_read_due.items()):
            if time.time() >= at:
                del self.hook_read_due[key]
                self.read_for_webhook(key)
        if (self.grange_reading is not None and self.grange_state != "ok"
                and now >= self.grange_read_at + self.GRANGE_GRACE_S):
            self.forget_agents()
        self.draw()

    # ---- each repository's timer -----------------------------------------------------------
    def covered(self, repo: Repo) -> bool:
        """Whether GitHub tells the app when this repository changes: webhooks are on, the
        tunnel is open, and the repository's webhook has been confirmed (hook_done)."""
        return self.settings.webhooks and self.tunnel_state == "open" and repo.name.lower() in self.hooked

    def period(self, repo: Repo) -> float:
        """How long a repository goes between two reads on its timer, in seconds. The poll's
        minutes for one GitHub sends no webhooks for. SAFETY_POLLS times that for one it
        does: there silence means nothing changed, and the timer is only there to catch a
        delivery that never came (GitHub does not send one again). When the tunnel closes,
        or webhooks are turned off, such a repository is back on the short timer at once.

        The slow timer is for a repository whose last read worked. One whose last read
        failed is on the short one until a read works: what the board shows of it is not
        known to be right, however quiet GitHub is.

        And it is for a repository read since its webhook was confirmed. What changed
        between an earlier read and the hook going live was told to nobody: the hook was not
        there, or pointed elsewhere. So until a read has begun after that, the timer stays
        short, and one ordinary read closes the gap.

        And not for a repository where GitHub is still working out whether a pull request
        can merge. It does that a little after it is asked, following a push to the base
        branch, and sends no delivery when it knows: only another read finds out."""
        key = repo.name.lower()
        got = self.pulls.get(key)
        slow = (self.covered(repo) and key not in self.pull_trouble
                and self.read_began.get(key, 0.0) >= self.hooked.get(key, 0.0)
                and not (got is not None and any(pull.mergeable == "unknown" for pull in got.pulls)))
        # At least a minute, whatever the setting holds: Settings.load keeps it from 1 up, but
        # a timer of no length would read every repository every second.
        minutes = max(1, self.settings.poll_minutes) * (self.SAFETY_POLLS if slow else 1)
        return min(minutes, Settings.POLL_MOST) * 60

    def due(self, repo: Repo, now: float) -> bool:
        """Whether a shown repository is to be read now: its timer has run out, or it has
        none. The timer runs from the end of its last read, whatever asked for that read: a
        webhook, u, the start, or the timer.

        One with no timer has never been read. What shows or adds a repository has it
        read, but not while the token is being checked, and if that check ends with the
        old token still in use nothing asks again. So the clock reads it."""
        key = repo.name.lower()
        if key in self.fetching:
            return False
        return key not in self.read_at or now >= self.read_at[key] + self.period(repo)

    def timer(self, name: str) -> tuple[float, float]:
        """What a repository's line shows of its timer: how far it has run toward its next
        read, from 0 (just read) to 1 (due), and how many seconds are left until then. One
        that has not been read yet is due, and so is one that is not in the list."""
        repo = self.store.get(name)
        if repo is None:
            return 1.0, 0.0
        span, read = self.period(repo), self.read_at.get(repo.name.lower(), 0.0)
        gone = min(span, max(0.0, time.time() - read)) if read else span
        return gone / span, span - gone

    def retry_token(self) -> None:
        """Ask GitHub again about the stored token. The screen keeps saying "not checked"
        until it answers, and once it does everything starts as it does at launch."""
        self.token_tried_at = time.time()
        self.ask_github(None)

    def on_unmount(self) -> None:
        self.closing.set()
        self.stop_services()
        if self.watch is not None:
            self.watch.stop()             # a question the grange is holding is given up, not waited for

    # ---- the team's agents, from the agent-grange on this Mac ------------------------------
    def start_grange(self) -> None:
        """Begin asking the agent-grange on this Mac for the team's agents, and go on asking
        for as long as the app runs (grange.Watch). On a Mac with no grange nothing answers,
        the setup says so, and the board is of people and teams alone."""
        self.watch = Watch(Grange(self.settings.grange_port),
                           lambda reading: self.later(self.grange_answered, reading),
                           lambda wrong: self.later(self.grange_failed, wrong))
        self.watch.start()

    def grange_answered(self, reading: Reading) -> None:
        """The grange's picture of the team: it replaces the one before, whole."""
        back = self.grange_state != "ok"
        self.grange_state, self.grange_wrong = "ok", None
        self.grange_reading, self.grange_read_at = reading, time.time()
        self.reported = tuple(reading.reported())
        if self.notice is not None and self.notice[1] in (self.GRANGE_STALE, self.GRANGE_LOST):
            self.notice = None                    # what it said was wrong no longer is
        if back:
            granges, agents = len(reading.granges), len(self.reported)
            self.say(f"agent-grange answers: {granges} grange{'' if granges == 1 else 's'}, "
                     f"{agents} agent{'' if agents == 1 else 's'}")
        self.draw()

    def grange_failed(self, wrong: GrangeError) -> None:
        """The grange did not answer, or refused. It is said once, not at every try. What it
        last reported stays on the board for a while yet (GRANGE_GRACE_S, see `tick`): a
        grange being restarted is back within that. Finding no grange when none has ever
        answered is how things are on a Mac without one, and is no trouble."""
        before = self.grange_wrong
        if before is None or (wrong.kind, wrong.said) != (before.kind, before.said):
            plain = wrong.kind == "down" and self.grange_reading is None
            self.say(wrong.said, INK2 if plain else INK)
            if wrong.kind == "stale":
                self.announce(self.GRANGE_STALE, self.NOTICE_FAILED_S)
        self.grange_state, self.grange_wrong = wrong.kind, wrong
        self.draw()

    def forget_agents(self) -> None:
        """The grange has not answered for too long: what it last said is no longer shown."""
        had = len(self.reported)
        self.grange_reading, self.reported = None, ()
        if had:
            self.say(f"agent-grange has not answered for a minute: the {had} "
                     f"agent{'' if had == 1 else 's'} it reported {'is' if had == 1 else 'are'} no longer shown", INK)
            self.announce(self.GRANGE_LOST, self.NOTICE_FAILED_S)

    # ---- the terminal's title ----------------------------------------------------------
    def watched(self) -> int:
        """How many repositories are being watched: the shown ones, while GitHub has
        accepted the token. Without that nothing is read, so there is nothing to count."""
        return len(self.store.shown()) if self.token_state == "ok" else 0

    def set_window_title(self, title: str) -> None:
        """Write the terminal's title when it has changed (draw runs every second, and an
        escape on every tick would be wasted). Once the app is quitting it is left cleared."""
        driver = self._driver
        if driver is None or self.leaving or title == self.title_shown:
            return
        try:
            driver.write(title_sequence(title))
            self.title_shown = title
        except Exception:                 # headless, or already out of application mode
            log.debug("window title not set", exc_info=True)

    def exit(self, *args, **kwargs) -> None:
        self.set_window_title("")         # the Mac app goes back to its own title, iTerm2's tab to its
        self.leaving = True
        super().exit(*args, **kwargs)

    # ---- helpers -----------------------------------------------------------------------
    def say(self, text: str, style: str = INK2) -> None:
        self.lines.append((time.time(), text, style))
        log.info("%s", text)

    def tell(self, text: str, style: str = INK2) -> None:
        """Say it and show it now. For news that arrives from a worker thread."""
        self.say(text, style)
        self.draw()

    def later(self, fn: Callable, *args) -> None:
        """From a worker thread: run this on the screen's thread. Quietly does nothing if
        the app has gone."""
        try:
            self.call_from_thread(fn, *args)
        except RuntimeError:
            pass

    def in_dialog(self) -> bool:
        return isinstance(self.screen, ModalScreen)

    def draw(self) -> None:
        """Redraw whichever of the two screens is showing (under a dialog, the one beneath),
        and the terminal's title, should the number of repositories watched have changed."""
        self.set_window_title(window_title(self.watched()))
        for screen in reversed(self.screen_stack):
            if isinstance(screen, (Board, SetupScreen)):
                try:
                    screen.draw()
                except Exception:                            # a last tick while the app is closing
                    pass
                return

    def action_setup(self) -> None:
        """From the board: open the setup."""
        if not self.in_dialog() and self.screen is self.board:
            self.push_screen("setup")

    def action_setup_done(self) -> None:
        """From the setup: back to the board."""
        if not self.in_dialog() and self.screen is self.setup:
            self.pop_screen()

    def show_repo(self, name: str, on: bool) -> None:
        """Put a repository on screen or take it off. It is saved; one that is newly shown
        is read, and gets its webhook when the tunnel is open."""
        repo = self.store.get(name)
        if repo is None or repo.shown == on:
            return
        self.store.show(repo.name, on)
        self.say(f"{repo.name} is {'shown' if on else 'hidden'}")
        if on:
            self.update_pulls([repo.name], "shown")
            if self.tunnel_state == "open":
                self.sync_hooks([repo.name])
        self.draw()

    def selected(self) -> Repo | None:
        repos = self.store.repos
        if not repos:
            return None
        self.sel = max(0, min(len(repos) - 1, self.sel))
        return repos[self.sel]

    # ---- 1: the token ------------------------------------------------------------------
    def action_token(self) -> None:
        if self.in_dialog():
            return
        self.push_screen(Ask("Paste a GitHub token", TOKEN_HELP,
                             [Field("token", "token", secret=True, placeholder="ghp_… or github_pat_…")],
                             lambda v: None if v["token"] else "Paste the token, or press esc."),
                         self.token_entered)

    def token_entered(self, values: dict | None) -> None:
        if values:
            self.token_kept = (self.token_state, self.token_mask, self.identity, self.token_note)
            self.token_state, self.token_note = "checking", ""
            self.draw()
            self.ask_github(values["token"])

    def paste_failed(self, mask: str, note: str, stored: bool) -> None:
        """A pasted token GitHub refused, or that could not be checked or stored. A token
        already in the Keychain stands: things stay as they were, and the log says why."""
        if not stored:
            return self.token_checked("bad", mask, None, note)
        self.say(f"the new token was not taken: {note} The one in your Keychain is still in use.", INK)
        state, kept_mask, me, kept_note = self.token_kept
        if state in ("ok", "unchecked"):
            self.token_state, self.token_mask, self.identity, self.token_note = state, kept_mask, me, kept_note
            return self.draw()
        self.check_stored_token()                          # it was not known yet how the stored one stands

    def check_stored_token(self) -> None:
        self.token_state, self.token_tried_at = "checking", time.time()
        self.draw()
        self.ask_github(None)

    def ask_github(self, pasted: str | None) -> None:
        """Check a token (None: the stored one). A check still under way is out of date from
        now on: what it finds is dropped, and a token it was checking is not stored."""
        self.token_run += 1
        self.verify_token(self.token_run, pasted)

    @work(thread=True, group="token")
    def verify_token(self, run: int, pasted: str | None) -> None:
        """Ask GitHub who the token belongs to. A pasted token is stored only once GitHub
        has accepted it, and only if no check or forgetting has come since (`run`)."""
        def answer(fn: Callable, *args) -> None:
            self.later(self.token_answer, run, fn, *args)

        try:
            token = pasted or keychain.get(keychain.GITHUB_TOKEN)
        except keychain.KeychainError as e:
            return answer(self.token_checked, "bad", "", None, f"The Keychain could not be read: {e}")
        if not token:
            return answer(self.token_checked, "none", "", None, "")
        try:
            me = GitHub(token).whoami()
        except GitHubError as e:
            if pasted:
                return answer(self.paste_failed, keychain.mask(token), str(e), keychain.has(keychain.GITHUB_TOKEN))
            if e.status == 0:                              # no network: the stored token stays
                return answer(self.token_checked, "unchecked", keychain.mask(token), None, str(e))
            return answer(self.token_checked, "bad", keychain.mask(token), None, str(e))
        if pasted:
            with self.token_lock:
                if run != self.token_run:                  # another token pasted, or this one forgotten
                    return
                try:
                    keychain.put(keychain.GITHUB_TOKEN, token)
                except keychain.KeychainError as e:
                    return answer(self.paste_failed, "", f"The Keychain did not take it: {e}",
                                  keychain.has(keychain.GITHUB_TOKEN))
        answer(self.token_checked, "ok", keychain.mask(token), me, "")

    def token_answer(self, run: int, fn: Callable, *args) -> None:
        if run == self.token_run:                          # else the user has moved on since it was asked
            fn(*args)

    def token_checked(self, state: str, mask: str, me: Identity | None, note: str) -> None:
        self.token_state, self.token_mask, self.identity, self.token_note = state, mask, me, note
        if state == "ok":
            self.say(f"GitHub token accepted: signed in as {me.login}", INK)
            self.take_token(me.login)
            if me.login.lower() != self.teams_for:         # another account: the teams of the one before
                self.forget_teams()                        # are not this one's
                self.teams_for = me.login.lower()
            self.start_teams_read()
            self.refresh_repos(self.token_run)             # what this token may do with each repository
            self.update_pulls([r.name for r in self.store.shown()], "start")
            if self.settings.webhooks and self.tunnel_state == "off":
                self.start_webhooks()
        elif state == "bad":
            self.say(f"GitHub token problem: {note}", INK)
        self.draw()

    def take_token(self, login: str) -> None:
        """The token just accepted, `login`'s, is the one in use from now on. Reads still
        under way with the one before are not used when they end.

        Another account's: what the account before read goes. It may hold repositories this
        one cannot see, and one this account cannot read at all would keep it on the board
        for good. Each shown repository is read again at once, and says "reading…" till then.

        The same account's: what was read stays on the board until each repository is read
        again, but not what the token before could do there: its rights are not this
        token's to lend."""
        self.token_used = self.token_run
        if login.lower() != self.pulls_for:
            self.forget_readings()
            self.pulls_for = login.lower()
        else:
            self.pulls = {key: replace(got, access="") for key, got in self.pulls.items()}

    def forget_readings(self) -> None:
        """What was read of the pull requests, and all that was made of it on screen: the
        readings, why some could not be read, the wash on what changed, the ticker's line.
        Not the timers, nor which reads are under way: their answers go to `pulls_known`,
        which tells the ones made with a token no longer in use."""
        for held in (self.pulls, self.pull_trouble, self.flashes):
            held.clear()
        self.news = None
        self.pulls_for = ""

    # ---- the teams the viewer is on --------------------------------------------------------
    @property
    def teams_held(self) -> bool:
        """A request to a team is held back, as neither the viewer's nor anyone else's: the
        teams are not known yet, and a read that will say is under way. Once a read has
        failed they are let go, as anyone else's."""
        return self.teams is None and not self.teams_note and self.teams_run != 0

    def teams_now(self) -> tuple[Mapping[str, str], bool]:
        """What a picture is drawn from: (the viewer's teams, none when they are not known;
        whether a team's request is held back). The board asks at every draw, so the teams
        are the very mapping kept here, not a copy: it cannot be written to (teams_known),
        so what a picture was drawn from cannot change under it."""
        known = self.teams if self.identity is not None and self.teams is not None else NO_TEAMS
        return known, self.teams_held

    def start_teams_read(self) -> None:
        """Ask GitHub which teams the viewer is on, unless it is being asked for this token
        already. At each sign-in and on u; there is no timer for it."""
        if self.teams_run != self.token_run:
            self.teams_run, self.teams_tried_at = self.token_run, time.time()
            self.read_teams(self.token_run, self.teams_for)

    def forget_teams(self) -> None:
        self.teams, self.teams_note, self.teams_listed, self.teams_for, self.teams_run = None, "", 0, "", 0
        self.teams_retry, self.teams_seen = False, 0

    @work(thread=True, group="teams")
    def read_teams(self, run: int, login: str) -> None:
        """Read the teams the token's user, `login`, is on. Apart from the token check, so
        that it can neither delay the sign-in nor fail it. It answers whatever happens:
        requests to a team are held back until it does, and must never be left held."""
        def answer(fn: Callable, *args) -> None:
            self.later(self.teams_answer, run, login, fn, *args)

        try:
            github = GitHub(keychain.get(keychain.GITHUB_TOKEN) or "")
            found = github.teams()
            above = teams_above(github, found)              # as far up as GitHub will say; it does not fail
        except keychain.KeychainError as e:                 # locked, perhaps: it may open again
            return answer(self.teams_failed, f"The Keychain could not be read: {e}", True)
        except GitHubError as e:
            return answer(self.teams_failed, str(e), e.passing)
        except Exception as e:                              # a reply shaped in a way nobody expected
            log.exception("reading the teams failed")
            return answer(self.teams_failed, f"GitHub's reply could not be read: {type(e).__name__}.", False)
        answer(self.teams_known, found, above)

    def teams_answer(self, run: int, login: str, fn: Callable, *args) -> None:
        """What read_teams found for `login`, asked under the token run `run`.

        That read is over: it stops holding requests back only if no later read has been
        started, so an answer for the account before cannot let go of what the read for
        the account now signed in holds.

        The answer is used when it is about the account now signed in and is not older
        than one already used. That the token has changed since does not matter by itself:
        a token of the same account, or one pasted and refused, leaves the answer as good
        as it was. Forgetting the token leaves no account for an answer to be about."""
        if self.teams_run == run:
            self.teams_run = 0
        if login == self.teams_for and run >= self.teams_seen:
            self.teams_seen = run
            fn(*args)
        self.draw()

    def teams_known(self, found: list[Team], above: list[Team] | tuple = ()) -> None:
        """`found` are the teams GitHub lists the viewer on; `above`, the ones those are
        nested in, however far up. A review asked of any of those is asked of the people
        on the teams beneath it too, so they all count as the viewer's."""
        teams = {team_of(team.org, team.slug).lower(): team.name for team in found}
        for team in found:                                 # the one each is directly nested in, which it
            if team.parent is not None:                    # names itself
                teams.setdefault(team_of(team.org, team.parent[0]).lower(), team.parent[1])
        for team in above:                                 # and the ones further up
            teams.setdefault(team_of(team.org, team.slug).lower(), team.name)
        news = teams != self.teams or bool(self.teams_note)    # said once, not at every u that finds the same
        if teams != self.teams:                            # the same as before: the mapping stays the one it
            self.teams = MappingProxyType(teams)           # was, and no picture is built again for it
        self.teams_note, self.teams_listed, self.teams_retry = "", len(found), False
        n = len(found)                                     # this line says how many, not which
        if news:
            self.say(f"GitHub lists you on {n} team{'' if n == 1 else 's'}" if n else "GitHub lists you on no team")

    def teams_failed(self, note: str, passing: bool) -> None:
        """A read with none known leaves them unknown: a request to a team is then anyone
        else's, and the board says so, since it looks no different from having no teams.
        When the failure may pass (`passing`), the poll tries again until it does. A read
        that fails when they are known keeps the ones known, and nothing is tried again."""
        again, self.teams_note = note == self.teams_note, note
        self.teams_retry = passing and self.teams is None
        if again:                                          # the poll's next try, failing the same way:
            return                                         # it has been said
        if self.teams is None:
            how = "It is tried again with each poll." if self.teams_retry else "u tries again."
            self.say(f"your teams could not be read: {note} A request to a team will not show as yours. {how}", INK)
            self.happened(time.time(), "your teams could not be read · a request to a team shows as someone "
                                       f"else's · {'tried again with each poll' if self.teams_retry else 'u tries again'}")
        else:
            self.say(f"your teams could not be read again: {note} The ones read before still stand.", INK)

    def action_forget_token(self) -> None:
        if self.in_dialog() or self.token_state == "none":
            return

        def answered(yes: bool | None) -> None:
            if not yes:
                return
            try:
                with self.token_lock:                      # not while a checked token is being stored
                    self.token_run += 1                    # a check under way is out of date
                    keychain.forget(keychain.GITHUB_TOKEN)
            except keychain.KeychainError as e:
                self.say(f"could not forget the token: {e}", INK)
            else:
                self.token_state, self.token_mask, self.identity, self.token_note = "none", "", None, ""
                self.forget_what_was_read()
                self.say("GitHub token forgotten: removed from the Keychain")
            self.draw()

        self.push_screen(Confirm("Forget the GitHub token?",
                                 "It is removed from your Keychain. Webhooks already on GitHub stay there."),
                         answered)

    def forget_what_was_read(self) -> None:
        """With the token gone, what it read goes too, and the tunnel closes: nothing on
        screen is left to go stale, and no delivery arrives that could not be acted on. The
        webhooks setting stays, so a new token opens the tunnel again."""
        self.forget_readings()
        for held in (self.hook_state, self.hooked):
            held.clear()
        self.forget_teams()
        self.stale.clear()
        self.read_at.clear()
        self.read_began.clear()
        if self.tunnel is not None or self.receiver is not None:
            self.stop_services()
            self.say("webhooks: tunnel closed until there is a token again")
        self.tunnel_state, self.tunnel_note = "off", ""

    # ---- 2: the repositories -----------------------------------------------------------
    def action_add(self) -> None:
        if self.in_dialog():
            return
        if self.token_state != "ok":
            self.say("add a GitHub token first (t): a repository is checked with it", INK)
            return self.draw()

        def check(values: dict) -> str | None:
            try:
                parse(values["repo"])
            except ValueError as e:
                return str(e)
            return None

        self.push_screen(Ask("Add a repository", ["Type owner/name, or paste its GitHub address."],
                             [Field("repo", "repository", placeholder="owner/name")], check),
                         lambda values: values and self.add_repo(parse(values["repo"])))

    @work(thread=True, group="repos")
    def add_repo(self, name: str) -> None:
        try:
            info = GitHub(keychain.get(keychain.GITHUB_TOKEN) or "").repo(name)
        except (GitHubError, keychain.KeychainError) as e:
            return self.later(self.tell, f"{name}: {e}", INK)
        self.later(self.repo_known, info.name, info.private, info.admin, True, info.default_branch)

    def repo_known(self, name: str, private: bool, admin: bool, added: bool, default_branch: str = "",
                   asked: str = "") -> None:
        """What GitHub said about a repository. `asked` is the name it was asked by: when
        GitHub answers under another (renamed or transferred), that entry takes the new name."""
        repo, renamed = self.take_name(asked, name, private, admin, default_branch)
        self.repo_note.pop(name.lower(), None)
        if renamed and repo.shown:
            self.update_pulls([repo.name], "renamed")
        if added:
            self.sel = self.store.repos.index(repo)
            self.say(f"added {name}", INK)
            if repo.shown:
                self.update_pulls([repo.name], "added")
            if self.tunnel_state == "open" and repo.shown:
                self.sync_hooks([repo.name])
        self.draw()

    def forget_repo(self, key: str) -> None:
        """Drop all that is kept about a repository under this name (lower case): it has
        left the list, or goes by another name now. Its timers too, or they would outlive it."""
        for held in (self.pulls, self.pull_trouble, self.hook_state, self.hooked, self.repo_note, self.read_at,
                     self.read_began, self.hook_read_at, self.hook_read_due):
            held.pop(key, None)

    def take_name(self, asked: str, name: str, private: bool, admin: bool,
                  default_branch: str = "") -> tuple[Repo, bool]:
        """The list takes GitHub's name for a repository it has as `asked`: other capitals,
        or a new name GitHub followed (renamed or transferred). This is the one place the
        two spellings meet; everywhere else compares the list's names as they are. What was
        read under an old name is dropped. Returns the entry, and whether it was renamed.

        Its webhook moved with it on GitHub and goes on sending. So one whose hook was
        confirmed under the old name has it confirmed again under the new one, now: left to
        the next time the webhooks are registered, it would be read on the short timer
        until then, for no reason anyone could see."""
        repo = self.store.add(name, private, admin, default_branch, was=asked)
        old, new = asked.lower(), name.lower()
        if not asked or old == new:
            return repo, False
        hooked = old in self.hooked
        self.forget_repo(old)
        self.say(f"{asked} is now {name} on GitHub", INK)
        if hooked and repo.shown and self.tunnel_state == "open":
            self.sync_hooks([repo.name])
        return repo, True

    @work(thread=True, group="repos")
    def refresh_repos(self, run: int) -> None:
        """Ask GitHub again what the token may do with each repository. Each answer is for
        the token of `run`: once that has changed, the rest are not used (repo_refreshed)."""
        try:
            github = GitHub(keychain.get(keychain.GITHUB_TOKEN) or "")
        except keychain.KeychainError:
            return
        for name in [r.name for r in self.store.repos]:
            if self.closing.is_set():
                return
            try:
                info = github.repo(name)
            except GitHubError as e:
                self.later(self.repo_trouble, name, str(e))
            else:
                self.later(self.repo_refreshed, run, name, info)

    def repo_refreshed(self, run: int, asked: str, info: RepoInfo) -> None:
        """A refresh's answer, used only if it is still wanted: the token that asked is the
        one in use, and the repository is still in the list (removed meanwhile, it would
        come back)."""
        if run != self.token_run or self.store.get(asked) is None:
            return
        self.repo_known(info.name, info.private, info.admin, False, info.default_branch, asked)

    def repo_trouble(self, name: str, note: str) -> None:
        self.repo_note[name.lower()] = note
        self.draw()

    def action_move(self, step: int) -> None:
        """The arrows pick a repository; while the pull requests are shown they scroll them."""
        if self.in_dialog():
            return
        if self.view == "pulls":
            lower = self.setup.query_one("#lower", VerticalScroll)
            lower.scroll_to(y=max(0, lower.scroll_y + step), animate=False)
            return
        self.sel += step
        self.draw()

    def action_reorder(self, step: int) -> None:
        """Move the chosen repository up or down the list. The order is saved, and the
        board lists the repositories in it."""
        repo = None if self.in_dialog() else self.selected()
        if repo is not None and self.store.move(repo.name, step):
            self.sel = self.store.repos.index(repo)
            self.draw()

    def action_page(self, step: int) -> None:
        if self.view == "pulls" and not self.in_dialog():
            lower = self.setup.query_one("#lower", VerticalScroll)
            lower.scroll_to(y=max(0, lower.scroll_y + step * max(1, lower.size.height - 2)), animate=False)

    def action_toggle(self) -> None:
        repo = None if self.in_dialog() else self.selected()
        if repo is None:
            return
        self.show_repo(repo.name, not repo.shown)

    def action_remove(self) -> None:
        repo = None if self.in_dialog() else self.selected()
        if repo is None:
            return

        def answered(yes: bool | None) -> None:
            if not yes:
                return
            self.store.remove(repo.name)
            self.forget_repo(repo.name.lower())
            self.say(f"removed {repo.name}", INK)
            if repo.hook_id:
                self.delete_hooks([(repo.name, repo.hook_id)])
            self.draw()

        self.push_screen(Confirm(f"Remove {repo.name}?",
                                 "It leaves this list." + (" Its webhook on GitHub is deleted too."
                                                           if repo.hook_id else "")), answered)

    def action_branches(self) -> None:
        """The chosen repository's long-lived branches: a pull request from one of them is
        never drawn as a stack that others are built on."""
        repo = None if self.in_dialog() else self.selected()
        if repo is None:
            return

        def entered(values: dict | None) -> None:
            if values is None:
                return
            self.store.set_long_lived(repo.name, [values["branches"]])
            listed = ", ".join(repo.long_lived or []) or "none"
            self.say(f"{repo.name}: long-lived branches {listed}", INK)
            key = repo.name.lower()
            got = self.pulls.get(key)
            if got is not None:                            # drawn again from what was read, now
                self.pulls[key] = replace(got, pulls=stack(got.pulls, got.default_branch, repo.long_lived))
            if key in self.fetching:                       # a reading under way used the old list
                self.stale.add(key)
            self.draw()

        self.push_screen(Ask(f"Long-lived branches of {repo.name}",
                             ["Branches that are only ever landed on, such as main and develop.",
                              "A pull request from one of them is never drawn as a stack that",
                              "others are built on. Separate them with commas."],
                             [Field("branches", "branches", value=", ".join(repo.long_lived or []),
                                    placeholder="main, develop")]), entered)

    # ---- 3: the webhooks ---------------------------------------------------------------
    def action_webhooks(self) -> None:
        if self.in_dialog():
            return
        stored = keychain.has(keychain.NGROK_AUTHTOKEN)

        def check(values: dict) -> str | None:
            if not values["authtoken"] and not stored:
                return "Paste your ngrok authtoken."
            try:
                clean_domain(values["domain"])
            except ValueError as e:
                return str(e)
            return None

        def entered(values: dict | None) -> None:
            if not values:
                return
            if values["authtoken"]:
                try:
                    keychain.put(keychain.NGROK_AUTHTOKEN, values["authtoken"])
                except keychain.KeychainError as e:
                    self.say(f"the Keychain did not take the ngrok authtoken: {e}", INK)
                    return self.draw()
            self.settings.ngrok_domain = clean_domain(values["domain"])
            self.settings.webhooks = True
            self.settings.save()
            self.start_webhooks()

        self.push_screen(Ask("Set up webhooks", WEBHOOK_HELP, [
            Field("authtoken", "ngrok authtoken" + (" (leave empty to keep the one in your Keychain)" if stored else ""),
                  secret=True),
            Field("domain", "ngrok domain", value=self.settings.ngrok_domain,
                  placeholder="your-name.ngrok-free.dev")], check), entered)

    def stop_services(self) -> None:
        """Close the tunnel and the receiver. A start still under way is out of date from
        now on: it closes what it opens and says nothing. No repository's hook is confirmed
        any more: a confirmation is about the tunnel it was made for."""
        self.webhooks_run += 1
        self.hooked.clear()
        tunnel, receiver, self.tunnel, self.receiver = self.tunnel, self.receiver, None, None
        close_services(tunnel, receiver)

    def start_webhooks(self) -> None:
        """Open the webhooks afresh. What is open is closed first (by the worker), and a
        start still under way gives up. Each hook has to be confirmed again for the tunnel
        this opens: until GitHub has it pointing there, its deliveries go to the old one."""
        self.webhooks_run += 1
        self.hooked.clear()
        old, self.tunnel, self.receiver = (self.tunnel, self.receiver), None, None
        self.tunnel_changed("starting", "")
        self.open_webhooks(self.webhooks_run, old)

    @work(thread=True, group="webhooks")
    def open_webhooks(self, run: int, old: tuple[Tunnel | None, Receiver | None]) -> None:
        """Receiver, then tunnel, then a check that the public address reaches the
        receiver, then (webhooks_changed) a webhook on each shown repository. One run at a
        time; whatever it tells the app carries its number, and is dropped once the user
        has turned the webhooks off or started them again."""
        with self.webhooks_lock:
            close_services(*old)
            if run != self.webhooks_run:
                return
            try:
                authtoken = keychain.get(keychain.NGROK_AUTHTOKEN)
            except keychain.KeychainError as e:
                self.later(self.set_authtoken_mask, "unreadable")
                return self.later(self.webhooks_changed, run, "failed", f"The Keychain could not be read: {e}")
            self.later(self.set_authtoken_mask, (keychain.mask(authtoken) or "stored") if authtoken else "missing")
            try:
                secret = keychain.webhook_secret()
            except keychain.KeychainError as e:
                return self.later(self.webhooks_changed, run, "failed", f"The Keychain could not be read: {e}")
            domain = self.settings.ngrok_domain
            if not authtoken or not domain:
                return self.later(self.webhooks_changed, run, "off", "")
            receiver = Receiver(secret, self.hand_on)
            port = receiver.start()
            tunnel = Tunnel(domain)
            try:
                tunnel.start(port, authtoken)
            except TunnelError as e:
                receiver.stop()
                return self.later(self.webhooks_changed, run, "failed", str(e))
            self.later(self.webhooks_opened, run, receiver, tunnel)
            if self.tunnel is not tunnel:                    # turned off or started again meanwhile
                return close_services(tunnel, receiver)
            if not wait_reachable(tunnel.url, receiver.nonce, sleep=self.closing.wait,
                                  stop=lambda: self.closing.is_set() or run != self.webhooks_run):
                return self.later(self.webhooks_changed, run, "unreachable",
                                  "The tunnel opened, but its address did not lead back to this app.")
            self.later(self.webhooks_changed, run, "open", "")

    @work(thread=True, group="webhooks")
    def check_tunnel(self, run: int, url: str, nonce: str) -> None:
        """Ask again whether the public address leads back to this app: the check made when
        the tunnel opened, repeated for as long as it is open (tick). A tunnel can die
        later, another ngrok session taking its address or the network changing, and
        nothing else would say so."""
        ok = wait_reachable(url, nonce, tries=3, pause=2.0, sleep=self.closing.wait,
                            stop=lambda: self.closing.is_set() or run != self.webhooks_run)
        self.later(self.tunnel_checked, run, ok)

    def tunnel_checked(self, run: int, ok: bool) -> None:
        """What check_tunnel found. A tunnel that no longer leads here is "unreachable": no
        repository is covered then, so each is back on the short timer. One that leads here
        again is open again, and the hooks are confirmed afresh (webhooks_changed): what
        changed meanwhile was told to nobody, and each repository is read once before its
        timer is the slow one again (period)."""
        self.tunnel_checking = False
        if run != self.webhooks_run or self.tunnel is None:  # turned off or started again meanwhile
            return
        if ok and self.tunnel_state == "unreachable":
            self.webhooks_changed(run, "open", "")
        elif not ok and self.tunnel_state == "open":
            self.webhooks_changed(run, "unreachable", "The tunnel's address no longer leads back to this app. "
                                  "Every repository is read on the short timer until it does.")

    def hand_on(self, delivery: Delivery) -> None:
        """From the receiver's thread: queue the delivery for the screen's thread, and do not
        wait for it. GitHub's answer waits for this, and the screen may be busy. (Textual's
        post_message, under call_later, is safe from any thread.)"""
        self.call_later(self.delivered, delivery)

    def webhooks_opened(self, run: int, receiver: Receiver, tunnel: Tunnel) -> None:
        if run == self.webhooks_run:                         # else the worker closes them
            self.receiver, self.tunnel = receiver, tunnel

    def webhooks_changed(self, run: int, state: str, note: str) -> None:
        if run != self.webhooks_run:                         # a start the user has since overtaken
            return
        self.tunnel_changed(state, note)
        if state == "open":
            self.sync_hooks([r.name for r in self.store.shown()])

    def set_authtoken_mask(self, mask: str) -> None:
        self.authtoken_mask = mask

    def tunnel_changed(self, state: str, note: str) -> None:
        self.tunnel_state, self.tunnel_note = state, note
        if state == "open":
            self.tunnel_checked_at = time.time()
            self.say(f"webhooks: tunnel open at https://{self.settings.ngrok_domain}", INK)
        elif state in ("failed", "unreachable"):
            self.say(f"webhooks: {note}", INK)
        self.draw()

    def delivered(self, delivery: Delivery) -> None:
        repo = self.store.get(delivery.repo) if delivery.repo else None
        known = repo is not None or not delivery.repo
        self.say(f"webhook · {delivery.said}" + ("" if known else "  (not in the list)"), INK)
        now = time.time()
        self.happened(now, f"webhook · {delivery.said}")
        if repo is not None and repo.shown and delivery.event != "ping":
            self.read_for_webhook(repo.name)                # something changed there: read it again
        self.draw()

    def read_for_webhook(self, name: str) -> None:
        """Read a repository again because a delivery says it changed: now, or when its turn
        comes (webhook_gap). Deliveries while it waits are read with it."""
        key = name.lower()
        repo = self.store.get(name)
        if repo is None or not repo.shown or key in self.hook_read_due:
            return
        soonest = self.hook_read_at.get(key, 0.0) + self.webhook_gap(key)
        if time.time() < soonest:
            self.hook_read_due[key] = soonest               # tick starts it
            return
        self.hook_read_at[key] = time.time()
        self.update_pulls([repo.name], "webhook")

    def webhook_gap(self, key: str) -> float:
        """How long a repository waits between reads that deliveries ask for. A busy CI run
        sends a stream of them, and each read costs what GitHub said the last one did: they
        may use WEBHOOK_SHARE of its hourly allowance at most, split among the shown
        repositories, so polls and the user's other tools have the rest."""
        got = self.pulls.get(key)
        if got is None or got.cost <= 0 or got.limit <= 0:
            return self.WEBHOOK_SOONEST_S
        points_an_hour = got.limit * self.WEBHOOK_SHARE / max(1, len(self.store.shown()))
        return max(self.WEBHOOK_SOONEST_S, got.cost * 3600 / points_an_hour)

    def sync_hooks(self, names: list[str]) -> None:
        """Make sure each named repository has our webhook, pointing at the tunnel that is
        open now. What it finds is for that tunnel (hook_done drops it otherwise)."""
        self.sync_hooks_for(self.webhooks_run, names)

    @work(thread=True, group="hooks")
    def sync_hooks_for(self, run: int, names: list[str]) -> None:
        try:
            github = GitHub(keychain.get(keychain.GITHUB_TOKEN) or "")
            secret = keychain.webhook_secret()
        except keychain.KeychainError as e:
            return self.later(self.tell, f"webhooks: the Keychain could not be read: {e}", INK)
        url = hook_url(self.settings.ngrok_domain)
        for name in names:
            if self.closing.is_set():
                return
            repo = self.store.get(name)
            if repo is None:
                continue
            if not repo.admin:
                self.later(self.hook_done, name, 0, "no webhook: you do not administer this repository")
                continue
            try:
                hook = ensure_hook(github, repo.name, url, secret, repo.hook_id)
            except GitHubError as e:
                self.later(self.hook_done, name, repo.hook_id, f"webhook failed: {e}")
            else:
                self.later(self.hook_done, name, hook.id, f"webhook #{hook.id} registered", run)

    def hook_done(self, name: str, hook_id: int, note: str, confirmed_for: int = 0) -> None:
        """What became of a repository's webhook: `note` says it in words, for the setup, and
        `confirmed_for` is the run of the tunnel GitHub now has the hook pointing at, 0 when
        it has not. Only a hook confirmed for the tunnel that is open now makes the
        repository one GitHub sends deliveries about (covered): after a restart, one
        confirmed for the tunnel before points at an address nobody listens on."""
        if not self.settings.webhooks:                       # turned off while this one was being made
            if hook_id:
                self.delete_hooks([(name, hook_id)])
            return
        self.hook_state[name.lower()] = note
        if confirmed_for and confirmed_for == self.webhooks_run:
            self.hooked[name.lower()] = time.time()
        else:
            self.hooked.pop(name.lower(), None)
        if hook_id:
            self.store.set_hook(name, hook_id)
        self.say(f"{name}: {note}")
        self.draw()

    @work(thread=True, group="hooks")
    def delete_hooks(self, hooks: list[tuple[str, int]]) -> None:
        try:
            github = GitHub(keychain.get(keychain.GITHUB_TOKEN) or "")
        except keychain.KeychainError:
            return
        for name, hook_id in hooks:
            try:
                github.delete_hook(name, hook_id)
            except GitHubError as e:
                if e.status != 404:                         # already gone is fine
                    self.later(self.tell, f"{name}: its webhook could not be deleted: {e}", INK)
                    continue
            self.later(self.hook_gone, name)

    def hook_gone(self, name: str) -> None:
        self.store.set_hook(name, 0)
        self.hook_state.pop(name.lower(), None)
        self.hooked.pop(name.lower(), None)
        self.say(f"{name}: webhook deleted on GitHub")
        self.draw()

    def action_register(self) -> None:
        if self.in_dialog():
            return
        if self.tunnel_state == "open":
            self.sync_hooks([r.name for r in self.store.shown()])
        elif self.settings.webhooks:
            self.start_webhooks()
        else:
            self.say("webhooks are off: w sets them up", INK)
            self.draw()

    def action_webhooks_off(self) -> None:
        if self.in_dialog() or not self.settings.webhooks:
            return

        def answered(yes: bool | None) -> None:
            if not yes:
                return
            self.settings.webhooks = False
            self.settings.save()
            self.stop_services()
            self.tunnel_state, self.tunnel_note = "off", ""
            self.say("webhooks turned off", INK)
            self.delete_hooks([(r.name, r.hook_id) for r in self.store.repos if r.hook_id])
            self.draw()

        self.push_screen(Confirm("Turn webhooks off?",
                                 "The tunnel closes and the webhooks this app made on GitHub are deleted. "
                                 "The ngrok authtoken stays in your Keychain."), answered)

    # ---- the pull requests -------------------------------------------------------------
    def update_pulls(self, names: list[str], why: str) -> None:
        """Fetch the open pull requests of these repositories. One fetch per repository at
        a time: a repository that changes while it is being fetched is fetched once more
        when that fetch ends.

        `why` is what asked for the read: the timer ("poll"), a webhook, u ("asked"), and so
        on. It goes in the log file with the names, and nowhere else: each read costs part
        of the hour's allowance from GitHub, and what used it up is a question for the log."""
        if self.token_state != "ok":
            return
        wanted = []
        for name in names:
            key = name.lower()
            if key in self.fetching:
                self.stale.add(key)
            else:
                self.fetching.add(key)
                self.read_began[key] = time.time()
                repo = self.store.get(name)                  # its long-lived branches, as they are now
                wanted.append((name, None if repo is None or repo.long_lived is None else list(repo.long_lived)))
        if wanted:
            log.info("reading (%s): %s", why, ", ".join(name for name, _ in wanted))
            self.fetch(wanted, self.token_used)
        self.draw()

    @work(thread=True, group="pulls")
    def fetch(self, wanted: list[tuple[str, list[str] | None]], token_used: int) -> None:
        """Read these repositories with the token in use; `token_used` says which that is
        (`self.token_used` when the read was asked for). Each answer carries it."""
        try:
            github = GitHub(keychain.get(keychain.GITHUB_TOKEN) or "")
        except keychain.KeychainError as e:
            for name, _ in wanted:
                self.later(self.pulls_failed, name, f"The Keychain could not be read: {e}", token_used)
            return
        for name, long_lived in wanted:
            if self.closing.is_set():
                return
            try:
                got = fetch_pulls(github, name, long_lived=long_lived)
            except GitHubError as e:
                self.later(self.pulls_failed, name, str(e), token_used)
            except Exception as e:                           # a reply shaped in a way nobody expected
                log.exception("reading the pull requests of %s failed", name)
                self.later(self.pulls_failed, name, f"could not read GitHub's reply: {type(e).__name__}", token_used)
            else:
                self.later(self.pulls_known, name, got, token_used)

    def read_before(self, name: str, token_used: int) -> bool:
        """A read that ends now was made with a token no longer in use. What it found is not
        used: the new token's account may not see the same, nor have the same rights. Taking
        the new token asked for every shown repository to be read again, and a read under
        way then is made again once it ends (`fetch_ended`), so nothing is left unread."""
        if token_used == self.token_used:
            return False
        log.info("%s: a reading made with the token before is not used", name)
        return True

    def pulls_known(self, name: str, got: RepoPulls, token_used: int) -> None:
        repo = self.store.get(name)
        if repo is None or self.token_state == "none":     # renamed or removed while it was read,
            return self.fetch_ended(name)                   # or the token forgotten
        if self.read_before(name, token_used):
            return self.fetch_ended(name)
        if got.repo and got.repo != repo.name:              # GitHub spells it otherwise: the list takes that
            repo, _ = self.take_name(repo.name, got.repo, repo.private, repo.admin, got.default_branch)
        key = repo.name.lower()
        before = self.pulls.get(key)
        self.pulls[key] = got
        self.pull_trouble.pop(key, None)
        if before is None or len(before.pulls) != len(got.pulls):
            self.say(f"{repo.name}: {got.total} open pull request{'' if got.total == 1 else 's'}")
        self.note_changes(repo.name, before, got)
        self.fetch_ended(name, repo.name)                    # under the name it has now, if that changed

    def note_changes(self, name: str, before: RepoPulls | None, got: RepoPulls) -> None:
        """Compare this reading with the one before: the pull requests that changed wash on
        the board, and the ticker says what changed."""
        if before is None:
            return
        viewer = self.identity.login if self.identity is not None else ""
        key = name.lower()
        # Both pictures are of this moment's teams: what changed is what GitHub changed, so the
        # teams becoming known between two readings makes no line here and no wash.
        teams, held = self.teams_now()
        found = changes(Picture([name], {key: before}, viewer, teams=teams, held=held),
                        Picture([name], {key: got}, viewer, teams=teams, held=held), name)
        if not found:
            return
        now = time.time()
        short = labels([r.name for r in self.store.repos]).get(name, name)
        for change in found:
            remember_change(self.flashes, change.key, now)
            self.say(f"{name} · {change.said}", INK)
        more = f"  (and {len(found) - 1} more)" if len(found) > 1 else ""
        self.happened(now, f"{short} · {found[0].said}{more}")

    # ---- the ticker: what happened on GitHub, and a word about what the user just did -------
    def happened(self, at: float, text: str) -> None:
        """Something happened on GitHub: the ticker says so until the next thing does. It
        shows at once, in place of any notice still up."""
        self.news, self.notice = (at, text), None

    def announce(self, text: str, seconds: float) -> None:
        """A word about what the user just did (o is opening a page; it could not), in the
        ticker's place for `seconds`. The last thing that happened on GitHub is not lost:
        it is back, as it was, when the notice ends."""
        self.notice = (time.time() + seconds, text)
        self.draw()

    def notice_now(self) -> str:
        """The notice to show in the ticker's place; "" when there is none, or it has ended."""
        if self.notice is not None and time.time() >= self.notice[0]:
            self.notice = None
        return self.notice[1] if self.notice is not None else ""

    def pulls_failed(self, name: str, note: str, token_used: int) -> None:
        if self.read_before(name, token_used):              # what the token before could not do says
            return self.fetch_ended(name)                   # nothing about this one
        self.pull_trouble[name.lower()] = note
        self.say(f"{name}: pull requests not read: {note}", INK)
        self.fetch_ended(name)

    def fetch_ended(self, name: str, now_named: str = "") -> None:
        """A read of a repository is over, whether it worked or not, and whatever asked for
        it: its timer starts again from here. One that failed is tried again when the timer
        runs out, not at once.

        `name` is what the read was asked for under. `now_named` is the repository's name
        when the read found it renamed on GitHub: the timer is that name's, and so is a
        read that was asked for while this one was under way. Left under the old name,
        the timer would be nobody's, and the repository would never be read by the clock.
        A repository that left the list while it was read gets no timer at all."""
        key = name.lower()
        self.fetching.discard(key)
        again = key in self.stale                            # it changed while we were reading it
        self.stale.discard(key)
        name = now_named or name
        if self.store.get(name) is not None and self.token_state != "none":
            self.read_at[name.lower()] = time.time()         # not for one removed, or a token forgotten,
            if again:                                        # while it was read: there is nothing to time
                self.update_pulls([name], "again")
        self.draw()

    def open_pull(self, pull: PullRequest, ref: str) -> None:
        """Show a pull request's page on GitHub in the browser. `ref` is how the board
        names it ("#12", or "name#12"), for the ticker. A key held down repeats some thirty
        times a second: o again on the same pull request, sooner than OPEN_AGAIN_S after the
        last o, opens nothing more."""
        asked, now = (pull.repo.lower(), pull.number), time.time()
        repeat = (self.page_asked is not None and self.page_asked[0] == asked
                  and now - self.page_asked[1] < self.OPEN_AGAIN_S)
        self.page_asked = (asked, now)
        if repeat:
            return
        self.announce(f"{ref} · opening its page in the browser", self.NOTICE_S)
        self.show_page(pull.page, ref)                     # built here, not the address GitHub sent

    @work(thread=True, group="pages")
    def show_page(self, url: str, ref: str) -> None:
        try:
            open_page(url)
        except Exception as e:                             # whatever it is, it must not close the board:
            # a worker's uncaught error ends the app, and a page not opened is not worth that
            why = str(e) if isinstance(e, NotOnGitHub) else "the browser could not be started"
            log.warning("could not open %s: %r", url, e)
            self.later(self.page_failed, ref, why)

    def page_failed(self, ref: str, why: str) -> None:
        """The page of the pull request the board names `ref` was not opened, and why."""
        text = f"{ref} · its page was not opened: {why}"
        self.page_asked = None                             # o may be pressed again at once
        self.say(text, INK)                                # kept in the setup's log: the notice passes
        self.announce(text, self.NOTICE_FAILED_S)

    def action_update(self) -> None:
        if self.in_dialog():
            return
        if self.token_state == "unchecked":                # GitHub was out of reach: try it now
            self.say("checking the GitHub token again")
            self.retry_token()
            return self.draw()
        if self.token_state == "checking":
            self.say("still checking the GitHub token")
            return self.draw()
        if self.token_state != "ok":
            self.say("add a GitHub token first (t)", INK)
            return self.draw()
        self.start_teams_read()                            # which teams the viewer is on may have changed too
        self.update_pulls([r.name for r in self.store.shown()], "asked")

    def action_view(self) -> None:
        if self.in_dialog():
            return
        self.view = "log" if self.view == "pulls" else "pulls"
        self.setup.query_one("#lower", VerticalScroll).scroll_home(animate=False)
        self.draw()

    # ---- the setup screen's words ------------------------------------------------------
    def top_text(self) -> Text:
        out = Text("agent-frolic", style=f"bold {INK}")
        out.append("  setup", style=MUTED)
        version = app_version()
        return out.append(f"   {version}" if version else "", style=MUTED)

    def token_text(self) -> Text:
        out = Text("1  GitHub token\n", style=f"bold {INK}")
        if self.token_state == "none":
            out.append("   ● ", style=WAITING).append("none yet", style=f"bold {INK}")
            return out.append("   t pastes one", style=MUTED)
        if self.token_state == "checking":
            return out.append("   checking with GitHub…", style=INK2)
        if self.token_state == "bad":
            out.append("   ✗ ", style=CRIT).append(self.token_note, style=INK)
            return out.append("   t pastes another", style=MUTED)
        if self.token_state == "unchecked":
            out.append(f"   {self.token_mask}", style=INK2).append(" in your Keychain", style=MUTED)
            out.append(f"\n   not checked: {self.token_note}", style=INK2)
            return out.append(f"   trying again every {self.TOKEN_RETRY_S} s · u tries now", style=MUTED)
        me = self.identity
        out.append("   ✓ ", style=GOOD).append(f"signed in as {me.login}", style=INK)
        out.append(f" · {self.token_mask}", style=INK2).append(" kept in your Keychain", style=MUTED)
        if me.scopes is None:
            out.append("\n   a fine-grained token: what it may do shows when it is used", style=MUTED)
        else:
            out.append(f"\n   scopes: {', '.join(me.scopes) or 'none'}", style=MUTED)
            if me.can_manage_hooks is False:
                out.append("\n   ● ", style=WAITING)
                out.append("this token cannot set up webhooks: it lacks the repo scope", style=INK)
        if self.teams is not None:
            n = self.teams_listed
            out.append(f"\n   on {n} team{'' if n == 1 else 's'}" if n else "\n   on no team", style=MUTED)
            if self.teams_note:                            # the ones read before still stand
                out.append(f" · not read again: {self.teams_note}", style=INK2)
        elif self.teams_note:
            out.append("\n   ● ", style=WAITING).append(f"your teams could not be read: {self.teams_note}", style=INK)
            out.append("\n     A request to a team will not show as yours.", style=INK2)
            out.append("  tried again with each poll · u tries now" if self.teams_retry else "  u tries again",
                       style=MUTED)
            if me.can_list_teams is None:                  # advice for the token there is, not for another kind
                out.append("\n     A fine-grained token sees the teams of one organization only.", style=MUTED)
            elif me.can_list_teams is False:
                out.append("\n     This token lacks a scope that lists teams: repo, read:org or user.", style=INK2)
        elif self.teams_run:
            out.append("\n   reading your teams…", style=MUTED)
        return out

    def repos_text(self) -> Text:
        out = Text("2  Repositories", style=f"bold {INK}")
        repos = self.store.repos
        if not repos:
            out.append("\n   ● ", style=WAITING).append("none yet", style=f"bold {INK}")
            return out.append("   a adds one", style=MUTED)
        out.append("   ✓ shown   · hidden", style=MUTED)
        if len(repos) > 1:
            out.append("   shift+↑ ↓ or [ ] moves the chosen one", style=MUTED)
        chosen = self.selected()
        width = max(len(r.name) for r in repos)
        for repo in repos:
            line = Text("\n ")
            line.append("▸ " if repo is chosen else "  ", style=INK)
            line.append("✓ " if repo.shown else "· ", style=INK if repo.shown else MUTED)
            line.append(f"{repo.name:<{width}}", style=f"bold {INK}" if repo.shown else INK2)
            trouble = self.repo_note.get(repo.name.lower())
            if trouble:
                line.append(f"   {trouble}", style=INK)
            else:
                line.append("   private" if repo.private else "   public ", style=MUTED)
                line.append("   you administer it" if repo.admin else "   read only for you", style=MUTED)
                got = self.pulls.get(repo.name.lower())
                if repo.name.lower() in self.pull_trouble:
                    line.append("   pull requests not read", style=INK)
                elif got is not None and repo.shown:
                    line.append(f"   {got.total} open", style=INK2)
                state = self.hook_state.get(repo.name.lower())
                if state:
                    line.append(f"   {state}", style=INK2)
                elif repo.hook_id:
                    line.append(f"   webhook #{repo.hook_id} from an earlier run", style=MUTED)
            if repo.long_lived is not None:
                line.append(f"   long-lived: {', '.join(repo.long_lived) or 'none'}", style=MUTED)
            if repo is chosen:
                line.stylize(f"on {RAISED}", 1)
            out.append_text(line)
        return out

    def hooks_text(self) -> Text:
        out = Text("3  Webhooks", style=f"bold {INK}")
        if not self.settings.webhooks:
            out.append("\n   off", style=INK2)
            return out.append("   w sets them up: your ngrok authtoken and domain", style=MUTED)
        mask = self.authtoken_mask or "not read yet"      # drawn every second: never a Keychain read here
        out.append("\n   ngrok     ", style=MUTED)
        out.append(f"authtoken {mask}", style=INK2).append(" in your Keychain · domain ", style=MUTED)
        out.append(self.settings.ngrok_domain, style=INK2)
        out.append("\n   tunnel    ", style=MUTED)
        if self.tunnel_state == "open":
            out.append("✓ ", style=GOOD).append(f"https://{self.settings.ngrok_domain}", style=INK)
            out.append(f" reaches this app on 127.0.0.1:{self.receiver.port if self.receiver else 0}", style=MUTED)
        elif self.tunnel_state == "starting":
            out.append("opening…", style=INK2)
        elif self.tunnel_state == "off":
            out.append("closed", style=INK2).append("   r opens it", style=MUTED)
        else:
            out.append("✗ ", style=CRIT).append(self.tunnel_note, style=INK)
            out.append("   r tries again", style=MUTED)
        out.append("\n   received  ", style=MUTED)
        receiver = self.receiver
        if receiver is None:
            return out.append("nothing: the receiver is not running", style=INK2)
        out.append(f"{receiver.accepted} accepted", style=INK2)
        out.append(f" · {receiver.refused} refused", style=INK if receiver.refused else MUTED)
        if receiver.last is not None:
            out.append(f" · last {age(receiver.last.at)} ago: {receiver.last.said}", style=INK2)
        return out

    def agents_text(self) -> Text:
        """The agent-grange on this Mac: whether it answers, and what it reports."""
        out = Text("4  Agents", style=f"bold {INK}")
        out.append("\n   grange    ", style=MUTED)
        port = self.settings.grange_port
        if self.grange_state == "asking":
            return out.append(f"asking the agent-grange on this Mac (port {port})…", style=INK2)
        reading, wrong = self.grange_reading, self.grange_wrong
        if wrong is None:
            out.append("✓ ", style=GOOD).append(f"answers on 127.0.0.1:{port}", style=INK)
            out.append(f" as {reading.grange}" if reading.grange else "", style=INK2)
        elif wrong.kind == "down" and reading is None:        # as on any Mac without one: no trouble
            out.append(f"none is running on this Mac (port {port})", style=INK2)
            return out.append("   agent-grange brings your team's agents onto the board", style=MUTED)
        elif wrong.kind == "stale":
            out.append("✗ ", style=CRIT).append("out of date" + (f": {wrong.why}" if wrong.why else ""), style=INK)
            out.append("\n   to fix    ", style=MUTED)
            out.append(wrong.fix or "update it", style=INK)
            out.append("   in the grange's folder: it answers nothing until then", style=MUTED)
        else:                                                 # trouble, or one that was answering and is gone
            out.append("✗ ", style=CRIT).append(wrong.said, style=INK)
        if reading is None:
            return out
        quiet_farms = sum(farm.farm_stale for farm in reading.granges)
        granges = len(reading.granges)
        out.append("\n   team      ", style=MUTED)
        out.append(f"{granges} grange{'' if granges == 1 else 's'}", style=INK2)
        if quiet_farms:
            out.append(f" · {quiet_farms} with no word from its agent-farm", style=INK)
        if self.grange_state != "ok":
            out.append(f" · as of {age(self.grange_read_at)} ago", style=MUTED)
        shown = [pull for name in (repo.name.lower() for repo in self.store.shown()) if name in self.pulls
                 for pull in self.pulls[name].pulls]
        here, agents = len(located(self.reported, shown)), len(self.reported)
        out.append("\n   agents    ", style=MUTED)
        out.append(f"{agents} reported", style=INK2)
        if agents:
            out.append(f" · {here} on the pull requests shown here", style=INK2)
            out.append("   by the ticket and number in their names: proj-86-pr-98", style=MUTED)
        return out

    def log_text(self) -> Text:
        out = Text("log", style=f"bold {INK}")
        out.append("   p shows the pull requests\n", style=MUTED)
        room = max(1, self.setup.query_one("#lower", VerticalScroll).size.height - 1)
        for at, said, style in list(self.lines)[-room:]:
            out.append(time.strftime("  %H:%M:%S  ", time.localtime(at)), style=MUTED)
            out.append(said + "\n", style=style)
        return out

    def pulls_text(self) -> Text:
        """The open pull requests of the shown repositories, as GitHub records them, each
        repository in stack order. One with nothing open is its head line and no more: that
        line says "0 open", as its line on the board does."""
        out = Text("pull requests", style=f"bold {INK}")
        out.append("   p shows the log · u updates now · ↑↓ scroll\n", style=MUTED)
        width = max(60, self.setup.query_one("#lower", VerticalScroll).size.width - 1)
        shown = self.store.shown()
        if not shown:
            return out.append("  no repository is shown\n", style=INK2)
        for repo in shown:
            key = repo.name.lower()
            got, trouble = self.pulls.get(key), self.pull_trouble.get(key)
            head = Text(f"\n  {repo.name}", style=f"bold {INK}")
            if key in self.fetching:
                head.append("   reading…", style=INK2)
            if got is not None:
                head.append(f"   {got.total} open", style=INK2)
                head.append(f" · default branch {got.default_branch} · read {age(got.at.timestamp())} ago",
                            style=MUTED)
                if got.total > len(got.pulls):
                    head.append(f" · the {len(got.pulls)} most recent are listed", style=MUTED)
            out.append_text(head).append("\n")
            if trouble:
                out.append("    ✗ ", style=CRIT).append(trouble + "\n", style=INK)
            if got is None:
                continue
            for pull in got.pulls:
                out.append_text(pull_line(pull, got.default_branch, width)).append("\n")
        return out


def close_services(tunnel: Tunnel | None, receiver: Receiver | None) -> None:
    if tunnel is not None:
        tunnel.stop()
    if receiver is not None:
        receiver.stop()


def pull_line(pull: PullRequest, default_branch: str, width: int) -> Text:
    """One pull request: number and title (indented when it is stacked on another), who
    wrote it, each reviewer's latest word and who is still asked, the comments to resolve,
    the checks, and how long it has been quiet. Color is on the marks only."""
    lead = "    " + "  " * max(0, pull.depth - 1) + ("└ " if pull.depth else "")
    said = Text(lead, style=AXIS)
    said.append(f"#{pull.number} ", style=f"bold {INK}")
    title_room = max(12, min(46, width - 86) - len(lead))
    title = cut(pull.title, title_room)                  # in terminal cells: ✨ and 中 take two
    said.append(title + " " * (title_room - cell_len(title)), style=INK2)
    said.append(f"  {pull.author[:14]:<14}", style=INK2)

    facts = Text()
    if pull.draft:
        facts.append("◌ ", style=MUTED).append("draft  ", style=INK2)
    if pull.mergeable == "conflict":
        facts.append("⚠ ", style=CRIT).append("conflict  ", style=INK)
    if not pull.parent and pull.base != default_branch:
        facts.append(f"into {pull.base}  ", style=MUTED)
    for review in pull.reviews:
        for mark, color in review_marks(review):
            facts.append(mark, style=color)
        facts.append(f" {review.who}  ", style=INK2)
    for who in pull.asked:                                        # a team by its slug, as GitHub gives it
        facts.append("◔", style=WARN).append(f" {team_slug(who) if is_team(who) else who}  ", style=INK2)
    if not pull.reviews and not pull.asked:                       # an author without rights here cannot ask
        facts.append("request review  " if pull.can_ask else "waits on the maintainer  ", style=MUTED)
    if pull.comments_open:
        facts.append("◇", style=MUTED).append(f"{pull.comments_open} to resolve  ", style=INK)
    said.append("  ").append_text(facts)

    mark, color = CHECK_MARK[pull.checks]
    tail = Text(mark, style=color).append(f" {quiet(pull):>{AGE_FIGURE}}", style=MUTED)
    room = width - tail.cell_len - 1
    if said.cell_len > room:
        said.truncate(room - 1)
        said.append("…", style=MUTED)
    said.append(" " * max(1, room - said.cell_len + 1))
    return said.append_text(tail)


def age(at: float) -> str:
    """How long since a moment (time.time()): "12s", "40m", "3h", "9d", "5w"."""
    return span(time.time() - at, fine=True)


class _NoSecrets:
    """An empty stand-in for the Keychain, for the self-check: it holds nothing and keeps nothing."""

    def get_password(self, service: str, name: str) -> None:
        return None

    def set_password(self, service: str, name: str, value: str) -> None:
        raise RuntimeError("the self-check stores nothing")

    def delete_password(self, service: str, name: str) -> None:
        raise RuntimeError("the self-check stores nothing")


def use_macos_trust() -> bool:
    """Have macOS judge every HTTPS certificate this process meets (GitHub's API, the
    tunnel's reachability check, anything added later), the way Safari does, instead of
    Python's default: the certificate file of the OpenSSL it was built with, whose path a
    packaged copy carries but whose file another Mac may not have. Returns whether it is
    in use."""
    import ssl

    import truststore
    truststore.inject_into_ssl()
    return isinstance(ssl.create_default_context(), truststore.SSLContext)


def selfcheck() -> int:
    """For the build (scripts/build_app.sh): prove this copy of the app is whole, and say so.

    It loads the parts a packaged copy could be missing (ngrok's library, the Keychain
    backend, macOS's certificate trust), then starts the app without a screen, with an
    empty stand-in for the Keychain and empty settings in a temporary folder. So it reads
    no secret, and with no token the app talks to nobody. Returns 0 when the setup screen
    drew and HTTPS is judged by macOS."""
    import ngrok                                               # noqa: F401  only that it loads
    if sys.platform == "darwin":
        from keyring.backends import macOS                     # noqa: F401
    trusted = use_macos_trust()

    async def look() -> bool:
        app = Frolic()
        async with app.run_test(size=(150, 46)) as pilot:
            await pilot.pause()
            shown = "\n".join(strip.text for strip in app.screen._compositor.render_strips())
            return app.screen is app.setup and "GitHub token" in shown

    names = ("AGENT_FROLIC_SETTINGS", "AGENT_FROLIC_REPOS")
    before = {name: os.environ.get(name) for name in names}
    store = keychain._backend                                  # whatever was in use, to put back
    try:
        with tempfile.TemporaryDirectory() as folder:
            for name in names:
                os.environ[name] = os.path.join(folder, name.lower() + ".json")
            keychain.use(_NoSecrets())
            drew = asyncio.run(look())
    finally:
        keychain.use(store)
        for name, value in before.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
    trouble = "; ".join(why for ok, why in ((drew, "the setup screen did not draw"),
                                            (trusted, "HTTPS is not judged by macOS's certificate trust")) if not ok)
    print(f"agent-frolic {app_version() or '(no version)'}: "
          + (f"self-check FAILED: {trouble}" if trouble else "self-check passed"))
    return 1 if trouble else 0


def main() -> int:
    """Run the board. `--version` prints the version and `--selfcheck` checks a packaged
    copy; both return without starting it. The return value is the exit code: not zero
    after a crash, so the Mac app knows to say so instead of closing."""
    if "--version" in sys.argv[1:]:
        print(app_version() or "(no version)")
        return 0
    use_macos_trust()                    # before anything connects anywhere
    if "--selfcheck" in sys.argv[1:]:
        return selfcheck()
    start_log(log_dir())
    app = Frolic()
    try:
        return run(app)
    except Exception:                    # trouble outside Textual's own handling: starting, the terminal
        log.exception("agent-frolic crashed")
        raise
    finally:
        log.info("exiting (return code %s)", app.return_code)


QUIT_GRACE_S = 3.0                       # how long a quit waits for background jobs (see finish)
LOG_BYTES = 2_000_000                    # the log starts afresh past this size,
LOG_KEPT = 3                             # and this many before it are kept: agent-frolic.log.1, .2, .3


def start_log(folder: Path) -> logging.Handler:
    """The log, agent-frolic.log in `folder`. Past LOG_BYTES it starts afresh, with the
    LOG_KEPT before it kept beside it and older ones deleted: a board left open for months
    does not fill the disk, nor keep every private repository name it ever read."""
    folder.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(folder / "agent-frolic.log", maxBytes=LOG_BYTES, backupCount=LOG_KEPT,
                                  encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    return handler


CURSOR_KEYS_ON, CURSOR_KEYS_OFF = "\x1b[?1h", "\x1b[?1l"     # DECCKM: application cursor keys


@contextmanager
def cursor_keys(stream: TextIO | None) -> Iterator[None]:
    """While the board runs, the terminal sends application cursor keys. The Mac app's
    terminal (SwiftTerm) keeps plain Page Up and Page Down for its own scrollback otherwise,
    and the board scrolls its list with them; in that mode SwiftTerm hands them to the
    program, Shift and all. The arrows, Home and End then come as ESC O A and the like,
    which Textual reads as the same keys. Only when `stream` is a terminal; put back
    however the board ends."""
    on = stream is not None and stream.isatty()
    if on:
        stream.write(CURSOR_KEYS_ON)
        stream.flush()
    try:
        yield
    finally:
        if on:
            stream.write(CURSOR_KEYS_OFF)
            stream.flush()


def run(app: App, **options) -> int:
    """Run the app on an event loop of its own, and return its exit code. asyncio.run would
    wait (up to five minutes) for every background job before returning; this does not.
    The terminal sends application cursor keys meanwhile (cursor_keys), on stderr, where
    Textual draws."""
    loop = asyncio.new_event_loop()
    try:
        with cursor_keys(None if options.get("headless") else sys.__stderr__):
            app.run(loop=loop, **options)
        loop.run_until_complete(loop.shutdown_asyncgens())
    finally:
        loop.close()                     # lets the idle job threads go; does not wait on busy ones
    return app.return_code or 0


def finish(code: int, grace: float = QUIT_GRACE_S) -> NoReturn:
    """End the process with `code`, giving background jobs `grace` seconds to end. Once
    the app closes they stop at their next chance, but one inside a network call waits for
    its timeout, and the process must not wait for that: the Mac app waits for it to end
    before it starts a board again, and ngrok holds this account's one session until it
    does."""
    deadline = time.monotonic() + grace
    for thread in threading.enumerate():
        if thread is not threading.current_thread() and not thread.daemon:
            thread.join(max(0.0, deadline - time.monotonic()))
    left = [t.name for t in threading.enumerate()
            if t is not threading.current_thread() and not t.daemon and t.is_alive()]
    if not left:
        sys.exit(code)
    log.warning("leaving with %d background job(s) unfinished: %s", len(left), ", ".join(left))
    logging.shutdown()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)


if __name__ == "__main__":
    finish(main())
