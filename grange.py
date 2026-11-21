# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""
The agents the team has at work, read from the agent-grange on this Mac.

agent-farm watches the agents on one Mac and reports them to that Mac's agent-grange; the
granges of a team pass what they know to each other. So the grange here can answer for the
whole team, and it answers this machine alone: the question below goes to 127.0.0.1 and
nowhere else, whatever the settings say.

  • ONE QUESTION. GET /v1/agents?after=<cursor>&wait=<seconds>. The answer is always the
    whole picture: every grange now live, this Mac's first, each with the agents its farm
    last reported (owner, name, state). There are no deltas: a new answer replaces the last.
    Only what the app shows is read from it: the ages and the restart count are left be.
  • HELD UNTIL SOMETHING CHANGES. Each answer has a cursor. Asked again with that cursor
    as `after`, the grange holds the question until the picture changes, WAIT seconds at
    the most, and then answers either way. So one question after another, each with the
    cursor of the last answer, keeps the board current and costs nothing while nothing
    happens. Cursors are compared for equality only: after a grange restart they start
    again from 1, and passing back a larger one is answered at once.
  • WHAT IS READ IS FROM OUTSIDE. An agent's name was typed on a teammate's Mac. Control
    characters are taken out here, as they are from what GitHub sends, a newline or a tab
    becomes a space (each of these is drawn on one line of the board, and would break
    it), and an entry that is not shaped as an agent is left out rather than trusted:
    its owner must be shaped as a GitHub login, since that is what it is used as. A state this app has not
    seen is kept as it came: what to draw for it is the picture's to say.
  • WHAT CAN BE WRONG is said in one plain sentence, GrangeError.said, with what kind of
    wrong it is: "down" (nothing is listening: no grange is running, which is how things
    are on a Mac without one), "stale" (the grange is out of date and refuses every
    question until it is updated; its answer says so, and how), "trouble" (anything else,
    a 503 that does not say the grange is out of date among it), and
    "closed" (this app gave the question up itself, on its way out).
  • ASKED FROM A THREAD OF ITS OWN. Watch asks, hands on each answer and each failure, and
    after a failure waits before it asks again. A question may be held for half a minute,
    so the app must be able to leave without waiting for it: `stop` gives it up.

Nothing here knows about pull requests or the screen. No secret is involved: the grange
answers anyone on this Mac.
"""
from __future__ import annotations

import http.client
import json
import logging
import math
import re
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from cleantext import clean

log = logging.getLogger("agent_frolic.grange")

PORT = 7788                       # the grange's port unless its .env says otherwise (GRANGE_PORT)
WAIT = 30                         # seconds a question is held: the grange's own most
SLACK = 10                        # seconds past that before a question is given up as unanswered
MOST_BYTES = 16 * 1024 * 1024     # an answer larger than this is not a picture of a team
FIELD = 256                       # the grange's own limit on a field, in bytes; held to here in characters
RETRY = 15.0                      # seconds before asking again after something went wrong,
RETRY_DOWN = 60.0                 # after finding no grange, or one out of date that names no wait,
RETRY_MOST = 300.0                # and the longest wait a grange may ask for
GRACE = 60.0                      # a grange that was answering and stops is given this long to be back
RETRY_SOON = 5.0                  # (it may be restarting), and is asked this often meanwhile
SPACING = 1.0                     # the least from one question to the next, however fast the answers come
# A GitHub login: letters, digits and single hyphens, not at either end, 39 at the most; and
# a single underscore as a hyphen may be, which the logins an enterprise manages have
# ("mira_acme"). An agent's owner is used as a login (whose card it is on, whether a move is
# theirs), so nothing else is taken for an owner: not "team/o/platform", which is how a team
# is written here, nor anything with a space in it.
LOGIN = re.compile(r"[A-Za-z0-9](?:[-_]?[A-Za-z0-9]){0,38}")


def now() -> float:
    """The clock the watch counts on: one that stands still while the Mac sleeps, so that a
    minute of silence is a minute awake."""
    return time.monotonic()


@dataclass(frozen=True)
class Reported:
    """One agent, as its farm reported it."""
    owner: str                    # the GitHub login of the human who runs it
    name: str                     # the session's name, as that human gave it
    state: str                    # the farm's own word for what it is doing
    stale: bool = False           # its farm has gone quiet: this is the last it said, some time ago


@dataclass(frozen=True)
class Farm:
    """One grange and what its farm last reported. The answer says more of each (how long
    since it was heard from, how long since its farm reported): nothing here shows those,
    so they are not read. What is read from outside is what can be wrong."""
    grange: str
    farm_stale: bool              # that farm has been silent too long: its agents are its last word
    agents: tuple[Reported, ...]


@dataclass(frozen=True)
class Reading:
    cursor: int
    grange: str                   # this Mac's grange
    granges: tuple[Farm, ...]

    def reported(self) -> list[Reported]:
        """Every agent of every grange, this Mac's first, in the order they were reported."""
        return [agent for farm in self.granges for agent in farm.agents]


class GrangeError(Exception):
    def __init__(self, kind: str, said: str, retry: float = RETRY, why: str = "", fix: str = "") -> None:
        super().__init__(said)
        self.kind = kind          # "down" | "stale" | "trouble" | "closed"
        self.said = said          # all of it, in a sentence or two: what the log gets
        self.retry = retry        # seconds before it is worth asking again
        self.why = why            # for "stale": the grange's own words for how it is out of date,
        self.fix = fix            # and the command it names to update it ("" when it gave none)


def _text(value: object) -> str | None:
    """A field as it is shown: on one line, whatever white space it came with (a newline
    in a name would break the row it is drawn on), and no longer than a field may be.
    None for what is not text."""
    return " ".join(value.split())[:FIELD] if isinstance(value, str) else None


def _seconds(value: object) -> float | None:
    """A count of seconds, or None for what is not one: not a number, not finite, below
    nought. (For how long a grange asks to be left alone.)"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        seconds = float(value)
    except OverflowError:
        return None
    return seconds if math.isfinite(seconds) and seconds >= 0 else None


def parse(data: object) -> Reading:
    """The grange's answer as a Reading. An answer that is not one is "trouble"; inside one,
    a grange or an agent that is not shaped as it should be is left out."""
    data = clean(data)
    cursor = data.get("cursor") if isinstance(data, dict) else None
    if isinstance(cursor, bool) or not isinstance(cursor, int):
        raise GrangeError("trouble", "The agent-grange on this Mac answered with something that is not its picture.")
    farms = []
    for entry in data.get("granges") if isinstance(data.get("granges"), list) else []:
        its_id = _text(entry.get("grange")) if isinstance(entry, dict) else None
        if its_id is None:
            continue
        stale = entry.get("farm_stale") is True
        agents = []
        for one in entry.get("agents") if isinstance(entry.get("agents"), list) else []:
            if not isinstance(one, dict):
                continue
            owner, name, state = _text(one.get("owner")), _text(one.get("name")), _text(one.get("state"))
            if owner and LOGIN.fullmatch(owner) and name is not None and state is not None:
                agents.append(Reported(owner, name, state, stale))
        farms.append(Farm(its_id, stale, tuple(agents)))
    return Reading(cursor, _text(data.get("grange")) or "", tuple(farms))


class Grange:
    """The grange on this Mac, asked one question at a time. `close` gives up a question
    that is being held, from another thread, so that the app need not wait for it to leave."""

    def __init__(self, port: int = PORT) -> None:
        self.port = port
        self._lock = threading.Lock()
        self._sock: socket.socket | None = None   # the socket a question is out on, for `close` to
        self._closed = False                      # wake. Read and set under the lock alone

    def read(self, after: int | None = None, wait: float = WAIT) -> Reading:
        """The team's picture. With `after`, the cursor of the last answer, the grange holds
        the question until the picture differs from that one, `wait` seconds at the most."""
        path = "/v1/agents" if after is None else f"/v1/agents?after={after}&wait={wait:g}"
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=wait + SLACK)
        answer = None
        try:
            conn.connect()
            with self._lock:
                # The socket is kept here, and not asked of the connection when it is wanted:
                # for an answer that says the connection will close, http.client lets go of
                # its own before the body is read, and `close` must still wake that read.
                if self._closed:          # `close` came before there was a socket to wake
                    raise GrangeError("closed", "The question was given up.")
                self._sock = conn.sock
            conn.request("GET", path, headers={"Accept": "application/json", "User-Agent": "agent-frolic"})
            answer = conn.getresponse()
            body = answer.read(MOST_BYTES + 1)
            status, again = answer.status, answer.getheader("Retry-After")
        except GrangeError:
            raise
        except ConnectionRefusedError:
            raise self._gone() or GrangeError(
                "down", f"No agent-grange is running on this Mac (port {self.port}).", RETRY_DOWN) from None
        except (OSError, http.client.HTTPException) as e:
            raise self._gone() or GrangeError(
                "trouble", f"The agent-grange on this Mac did not answer ({type(e).__name__}).") from None
        finally:
            with self._lock:
                self._sock = None
            if answer is not None:
                answer.close()
            conn.close()
        given_up = self._gone()           # given up as the answer came: nothing is handed on once
        if given_up is not None:          # `close` has been called
            raise given_up
        if len(body) > MOST_BYTES:
            raise GrangeError("trouble", "The agent-grange on this Mac answered with more than a picture holds.")
        try:
            try:
                data = json.loads(body)
            except ValueError:
                data = None
            if status == 503 and says_out_of_date(data):
                asked = _seconds(_number(again))
                raise out_of_date(data, RETRY_DOWN if asked is None else min(max(asked, 1.0), RETRY_MOST))
            if status != 200:
                raise GrangeError("trouble", f"The agent-grange on this Mac refused the question (HTTP {status}).")
            return parse(data)
        except GrangeError:
            raise
        except Exception as e:            # an answer nested deeper than can be read, say: it is
            raise GrangeError(            # trouble like any other, not a failure of this app's
                "trouble", f"The agent-grange on this Mac answered with something that cannot be read "
                           f"({type(e).__name__}).") from None

    def close(self) -> None:
        """Give up the question being held, if one is, and ask no more."""
        with self._lock:
            self._closed = True
            sock = self._sock
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)   # wakes the thread that waits on it
            except OSError:               # the question ended as this was called: the socket is
                pass                      # shut already, and there is nothing left to wake

    def _gone(self) -> GrangeError | None:
        """The error for a question this app gave up itself, when it did."""
        with self._lock:
            return GrangeError("closed", "The question was given up.") if self._closed else None


def _number(text: str | None) -> object:
    try:
        return float(text) if text is not None else None
    except ValueError:
        return None


def says_out_of_date(data: object) -> bool:
    """Whether a 503's body is the grange's own way of saying it is out of date: an object
    with its `error` and at least one of the things it then gives (why, the fix, what it
    was built from). Anything else that answers 503 on the port is not saying that: a
    container that is restarting, or something that is no grange. That is trouble of the
    passing kind, and is asked again soon, not told to update."""
    return (isinstance(data, dict) and isinstance(data.get("error"), str)
            and any(key in data for key in ("fix", "summary", "built_commit", "tag")))


def out_of_date(data: object, retry: float = RETRY_DOWN) -> GrangeError:
    """The error for a grange that refuses every question until it is updated: with why,
    in its own words, and the command it names as the fix, when its answer has them. Its
    words are from outside, as an agent's name is: cleaned, and held to a field's length."""
    data = clean(data) if isinstance(data, dict) else {}
    why, fix = _text(data.get("summary")) or "", _text(data.get("fix")) or ""
    said = "The agent-grange on this Mac is out of date and answers nothing until it is updated"
    said += f": {why}." if why else "."
    if fix:
        said += f" To update it, in its folder: {fix}"
    return GrangeError("stale", said, retry, why, fix)


class Watch(threading.Thread):
    """Keeps asking the grange, from a thread of its own, and hands on what it learns:
    `on_reading` gets each answer, `on_error` each failure, and `on_lost` is told, once,
    when a grange that was answering has been silent for GRACE: what it last said is not
    to be believed any longer. All three are called on this thread.

    That minute is kept here and nowhere else. It runs from the first failure after an
    answer, on the clock that stands still while the Mac sleeps, and when it ends the
    grange is asked once more before anything is said: so a question is always due at
    its end, whatever the failures before it asked to wait.

    An answer's cursor goes with the next question, so that one is held until the picture
    changes. After a failure the next question comes when the error says it is worth it,
    and carries no cursor: whatever is there then is news. A grange that was answering and
    has stopped is asked sooner than that, every RETRY_SOON seconds for GRACE: it may be
    restarting. Trouble that goes on
    past that, or on a port where no grange ever answered, is asked about less and less
    often: each try that fails waits twice as long as the one before, RETRY_MOST at the
    most. It may be something else that listens there, and that is not to be asked every
    quarter of a minute for as long as the app runs. `stop` ends it at once, a held
    question included.

    It is the grange that sets the pace, by holding a question until it has news. Should
    whatever answers on its port not hold them (it need not be a grange at all), the
    questions are still SPACING apart, not one on the heels of the last."""

    def __init__(self, grange: Grange, on_reading: Callable[[Reading], None],
                 on_error: Callable[[GrangeError], None], on_lost: Callable[[], None] = lambda: None) -> None:
        super().__init__(name="grange-watch", daemon=True)
        self.grange = grange
        self.on_reading = on_reading
        self.on_error = on_error
        self.on_lost = on_lost
        self.stopped = threading.Event()

    def run(self) -> None:
        cursor: int | None = None
        answering = False                 # its last try was answered
        lost_at: float | None = None      # when a grange that was answering first failed to
        told = False                      # that it is lost has been said, for this time it was
        troubled = 0                      # tries in a row that ended in trouble
        while not self.stopped.is_set():
            wait, asked_at = 0.0, now()
            try:
                try:
                    reading = self.ask(cursor)
                except GrangeError as e:
                    if e.kind == "closed":
                        return
                    at = now()
                    if answering:
                        answering, lost_at, told = False, at, False
                    cursor, wait = None, e.retry
                    left = GRACE - (at - lost_at) if lost_at is not None else 0.0
                    if left > 0:
                        # Its minute: asked often (unless it has said it is out of date, and
                        # for how long to leave it), and asked when the minute ends, whatever
                        # it asked to wait. None of these tries counts toward the longer
                        # waits: those begin at their beginning once the minute is over.
                        if e.kind in ("down", "trouble"):
                            wait = min(wait, RETRY_SOON)
                        wait, troubled = min(wait, left), 0
                    else:
                        troubled = troubled + 1 if e.kind == "trouble" else 0
                        if troubled > 1:
                            wait = min(wait * 2 ** min(troubled - 1, 16), RETRY_MOST)
                    self.on_error(e)
                    if lost_at is not None and left <= 0 and not told:
                        told = True
                        self.on_lost()
                else:
                    answering, lost_at, troubled = True, None, 0
                    cursor = reading.cursor
                    self.on_reading(reading)
                    wait = max(0.0, SPACING - (now() - asked_at))
            except Exception:                       # a mistake in what was handed the news: it is
                log.exception("watching the agent-grange failed")    # logged, and the watch goes on
                cursor, wait = None, RETRY_DOWN
            if wait and self.stopped.wait(wait):
                return

    def ask(self, cursor: int | None) -> Reading:
        """One question. Whatever goes wrong is a GrangeError: a failure nobody foresaw is
        logged and handed on as trouble, so that the app knows the grange is not answering
        and does not go on showing what it last said as if it were."""
        try:
            return self.grange.read(after=cursor)
        except GrangeError:
            raise
        except Exception as e:
            log.exception("asking the agent-grange failed")
            raise GrangeError("trouble", f"Asking the agent-grange on this Mac failed ({type(e).__name__}).",
                              RETRY_DOWN) from None

    def stop(self) -> None:
        self.stopped.set()
        self.grange.close()
