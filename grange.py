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
  • HELD UNTIL SOMETHING CHANGES. Each answer has a cursor. Asked again with that cursor
    as `after`, the grange holds the question until the picture changes, WAIT seconds at
    the most, and then answers either way. So one question after another, each with the
    cursor of the last answer, keeps the board current and costs nothing while nothing
    happens. Cursors are compared for equality only: after a grange restart they start
    again from 1, and passing back a larger one is answered at once.
  • WHAT IS READ IS FROM OUTSIDE. An agent's name was typed on a teammate's Mac. Control
    characters are taken out here, as they are from what GitHub sends, a newline or a tab
    becomes a space (each of these is drawn on one line of the board, and would break
    it), and an entry that is not shaped as an agent is left out rather than trusted. A state this app has not
    seen is kept as it came: what to draw for it is the picture's to say.
  • WHAT CAN BE WRONG is said in one plain sentence, GrangeError.said, with what kind of
    wrong it is: "down" (nothing is listening: no grange is running, which is how things
    are on a Mac without one), "stale" (the grange is out of date and refuses every
    question until it is updated; its answer says how), "trouble" (anything else), and
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


@dataclass(frozen=True)
class Reported:
    """One agent, as its farm reported it."""
    owner: str                    # the GitHub login of the human who runs it
    name: str                     # the session's name, as that human gave it
    state: str                    # the farm's own word for what it is doing
    stale: bool = False           # its farm has gone quiet: this is the last it said, some time ago


@dataclass(frozen=True)
class Farm:
    """One grange and what its farm last reported."""
    grange: str
    seen_age_s: float             # seconds since this Mac's grange last heard that one was alive
    farm_age_s: float | None      # seconds since that Mac's farm last reported; None if it never has
    farm_stale: bool              # that farm has been silent too long: its agents are its last word
    agents: tuple[Reported, ...]


@dataclass(frozen=True)
class Reading:
    cursor: int
    grange: str                   # this Mac's grange
    incarnation: int
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
    nought, or a whole number too long to be a time at all."""
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
            if owner and name is not None and state is not None:
                agents.append(Reported(owner, name, state, stale))
        farms.append(Farm(its_id, _seconds(entry.get("seen_age_s")) or 0.0,
                          _seconds(entry.get("farm_age_s")), stale, tuple(agents)))
    incarnation = data.get("incarnation")
    return Reading(cursor, _text(data.get("grange")) or "",
                   incarnation if isinstance(incarnation, int) and not isinstance(incarnation, bool) else 0,
                   tuple(farms))


class Grange:
    """The grange on this Mac, asked one question at a time. `close` gives up a question
    that is being held, from another thread, so that the app need not wait for it to leave."""

    def __init__(self, port: int = PORT) -> None:
        self.port = port
        self._lock = threading.Lock()
        self._asking: http.client.HTTPConnection | None = None
        self._closed = False

    def read(self, after: int | None = None, wait: float = WAIT) -> Reading:
        """The team's picture. With `after`, the cursor of the last answer, the grange holds
        the question until the picture differs from that one, `wait` seconds at the most."""
        path = "/v1/agents" if after is None else f"/v1/agents?after={after}&wait={wait:g}"
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=wait + SLACK)
        with self._lock:
            self._asking = conn
        try:
            conn.connect()
            given_up = self._gone()       # `close` came before there was a connection to wake: it
            if given_up is not None:      # is looked for again here, now that there is one. From
                raise given_up            # here on `close` reaches the socket itself.
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
                self._asking = None
            conn.close()
        if len(body) > MOST_BYTES:
            raise GrangeError("trouble", "The agent-grange on this Mac answered with more than a picture holds.")
        try:
            try:
                data = json.loads(body)
            except ValueError:
                data = None
            if status == 503:
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
            conn, self._asking = self._asking, None
        if conn is not None and conn.sock is not None:
            try:
                conn.sock.shutdown(socket.SHUT_RDWR)       # wakes the thread that waits on it
            except OSError:
                pass

    def _gone(self) -> GrangeError | None:
        """The error for a question this app gave up itself, when it did."""
        with self._lock:
            return GrangeError("closed", "The question was given up.") if self._closed else None


def _number(text: str | None) -> object:
    try:
        return float(text) if text is not None else None
    except ValueError:
        return None


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
    `on_reading` gets each answer, `on_error` each failure. Both are called on this thread.

    An answer's cursor goes with the next question, so that one is held until the picture
    changes. After a failure the next question comes when the error says it is worth it,
    and carries no cursor: whatever is there then is news. A grange that was answering and
    has stopped is asked sooner than that, every RETRY_SOON seconds for GRACE: it may be
    restarting, and the app keeps what it last said only that long. `stop` ends it at
    once, a held question included."""

    def __init__(self, grange: Grange, on_reading: Callable[[Reading], None],
                 on_error: Callable[[GrangeError], None]) -> None:
        super().__init__(name="grange-watch", daemon=True)
        self.grange = grange
        self.on_reading = on_reading
        self.on_error = on_error
        self.stopped = threading.Event()

    def run(self) -> None:
        cursor: int | None = None
        answering = False                 # its last try was answered
        lost_at: float | None = None      # when a grange that was answering first failed to
        while not self.stopped.is_set():
            wait = 0.0
            try:
                try:
                    reading = self.ask(cursor)
                except GrangeError as e:
                    if e.kind == "closed":
                        return
                    if answering:
                        answering, lost_at = False, time.monotonic()
                    cursor, wait = None, e.retry
                    if (lost_at is not None and e.kind in ("down", "trouble")
                            and time.monotonic() - lost_at < GRACE):
                        wait = min(wait, RETRY_SOON)
                    self.on_error(e)
                else:
                    answering, lost_at = True, None
                    cursor = reading.cursor
                    self.on_reading(reading)
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
