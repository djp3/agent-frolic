# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""The agent-grange on this Mac, asked for the team's agents: a small server stands in for it."""
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import grange
from grange import Farm, Grange, GrangeError, Reading, Reported, Watch

PICTURE = {
    "cursor": 9124, "grange": "djp3", "incarnation": 1763323200123,
    "granges": [
        {"grange": "djp3", "seen_age_s": 0.0, "farm_age_s": 7.2, "farm_stale": False,
         "agents": [{"owner": "djp3", "name": "proj-86-pr-98 fix tests", "state": "busy"}]},
        {"grange": "mira", "seen_age_s": 2.4, "farm_age_s": 95.0, "farm_stale": True,
         "agents": [{"owner": "mira", "name": "proj-12-pr-7 docs", "state": "idle"}]},
    ],
}


class StandIn:
    """A server on this machine that answers as a test says: `answer(path)` gives
    (status, headers, body). It keeps the paths it was asked for."""

    def __init__(self, answer):
        self.asked: list[str] = []
        self.hold = threading.Event()             # an answer that waits on this is a held question
        stand_in = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                stand_in.asked.append(self.path)
                status, headers, body = answer(self.path)
                raw = body if isinstance(body, bytes) else json.dumps(body).encode()
                try:
                    self.send_response(status)
                    for name, value in headers.items():
                        self.send_header(name, value)
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                except OSError:
                    pass                           # the question was given up: nobody is listening

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, args=(0.02,), daemon=True).start()

    def close(self):
        self.hold.set()
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def serve():
    made = []

    def start(answer):
        made.append(StandIn(answer))
        return made[-1]
    yield start
    for one in made:
        one.close()


def answering(body, status=200, **headers):
    return lambda path: (status, headers, body)


def failure(read) -> GrangeError:
    with pytest.raises(GrangeError) as caught:
        read()
    return caught.value


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---- the answer ------------------------------------------------------------------------

def test_the_whole_picture_is_read_this_macs_grange_first(serve):
    got = Grange(serve(answering(PICTURE)).port).read()
    assert (got.cursor, got.grange, got.incarnation) == (9124, "djp3", 1763323200123)
    assert got.granges == (
        Farm("djp3", 0.0, 7.2, False, (Reported("djp3", "proj-86-pr-98 fix tests", "busy"),)),
        Farm("mira", 2.4, 95.0, True, (Reported("mira", "proj-12-pr-7 docs", "idle", stale=True),)))
    assert [(a.owner, a.state) for a in got.reported()] == [("djp3", "busy"), ("mira", "idle")]


def test_an_agent_of_a_farm_gone_quiet_is_marked_as_its_last_word(serve):
    got = Grange(serve(answering(PICTURE)).port).read()
    assert [a.stale for a in got.reported()] == [False, True]


def test_the_first_question_has_no_cursor_and_the_next_carries_the_last(serve):
    stand_in = serve(answering(PICTURE))
    there = Grange(stand_in.port)
    first = there.read()
    there.read(after=first.cursor)
    there.read(after=first.cursor, wait=2.5)
    assert stand_in.asked == ["/v1/agents", "/v1/agents?after=9124&wait=30", "/v1/agents?after=9124&wait=2.5"]


def test_only_this_machine_is_asked(monkeypatch):
    asked = []

    class Refuses:
        sock = None

        def __init__(self, host, port, timeout):
            asked.append((host, port))

        def connect(self):
            raise ConnectionRefusedError

        def close(self):
            pass

    monkeypatch.setattr(grange.http.client, "HTTPConnection", Refuses)
    assert failure(Grange(7788).read).kind == "down"
    assert asked == [("127.0.0.1", 7788)]


def test_a_state_never_seen_before_is_kept_as_it_came(serve):
    body = {"cursor": 1, "grange": "djp3", "incarnation": 5, "granges": [
        {"grange": "djp3", "seen_age_s": 0, "farm_age_s": 1, "farm_stale": False,
         "agents": [{"owner": "djp3", "name": "proj-1-pr-2", "state": "pondering"}]}]}
    assert Grange(serve(answering(body)).port).read().reported() == [Reported("djp3", "proj-1-pr-2", "pondering")]


def test_two_agents_alike_are_two_agents(serve):
    one = {"owner": "djp3", "name": "proj-1-pr-2", "state": "busy"}
    body = {"cursor": 1, "grange": "djp3", "incarnation": 5, "granges": [
        {"grange": "djp3", "seen_age_s": 0, "farm_age_s": 1, "farm_stale": False, "agents": [one, one]}]}
    assert len(Grange(serve(answering(body)).port).read().reported()) == 2


def test_what_is_not_shaped_as_it_should_be_is_left_out():
    got = grange.parse({"cursor": 3, "grange": 7, "incarnation": "soon", "granges": [
        "a grange", {"seen_age_s": 1}, {"grange": 12},
        {"grange": "djp3", "seen_age_s": -4, "farm_age_s": None, "farm_stale": "yes", "agents": [
            "an agent", {"owner": "djp3", "name": "proj-1-pr-2"}, {"owner": "", "name": "n", "state": "busy"},
            {"owner": "djp3", "name": 98, "state": "busy"}, {"owner": "djp3", "name": "proj-1-pr-2", "state": None},
            {"owner": "djp3", "name": "proj-1-pr-2", "state": "busy", "path": "/Users/djp3/secret"}]},
        {"grange": "mira", "seen_age_s": True, "farm_age_s": float("nan"), "farm_stale": True, "agents": "none"}]})
    assert (got.cursor, got.grange, got.incarnation) == (3, "", 0)
    assert got.granges == (Farm("djp3", 0.0, None, False, (Reported("djp3", "proj-1-pr-2", "busy"),)),
                           Farm("mira", 0.0, None, True, ()))


@pytest.mark.parametrize("body", [[], "a picture", None, {}, {"cursor": "9"}, {"cursor": True}, {"cursor": 1.5}])
def test_an_answer_that_is_not_a_picture_is_trouble(body):
    with pytest.raises(GrangeError) as caught:
        grange.parse(body)
    assert caught.value.kind == "trouble"


def test_a_picture_of_no_granges_is_a_picture():
    assert grange.parse({"cursor": 1}) == Reading(1, "", 0, ())


def test_control_characters_are_taken_out_of_what_was_typed_elsewhere():
    got = grange.parse({"cursor": 1, "grange": "dj\x1bp3", "granges": [{"grange": "mi\x07ra", "agents": [
        {"owner": "mi‮ra", "name": "proj-1-pr-2 \x1b]52;c;aGk=\x07", "state": "bu\x00sy"}]}]})
    assert got.grange == "djp3" and got.granges[0].grange == "mira"
    assert got.reported() == [Reported("mira", "proj-1-pr-2 ]52;c;aGk=", "busy")]


def test_a_newline_or_a_tab_in_a_field_becomes_a_space_so_each_stays_on_its_line():
    """Every one of these is drawn on one line of the board or the setup."""
    got = grange.parse({"cursor": 1, "grange": "dj\tp3\n", "granges": [{"grange": "mi\r\nra", "agents": [
        {"owner": " mira\n", "name": "proj-86-pr-98 fix\ntests\t\tnow \u2028 then", "state": "bu\nsy"}]}]})
    assert (got.grange, got.granges[0].grange) == ("dj p3", "mi ra")
    assert got.reported() == [Reported("mira", "proj-86-pr-98 fix tests now then", "bu sy")]
    shown = [got.grange, got.granges[0].grange, *(part for a in got.reported() for part in (a.owner, a.name, a.state))]
    assert not any(c in text for text in shown for c in "\n\r\t")
    wrong = grange.out_of_date({"summary": "image is\n3 behind", "fix": "make update\t(then make up)"})
    assert (wrong.why, wrong.fix) == ("image is 3 behind", "make update (then make up)") and "\n" not in wrong.said


def test_an_owner_of_nothing_but_white_space_is_no_owner():
    got = grange.parse({"cursor": 1, "granges": [{"grange": "djp3", "agents": [
        {"owner": " \n\t", "name": "proj-86-pr-98", "state": "busy"}]}]})
    assert got.reported() == []


@pytest.mark.parametrize("owner", ["team/o/platform", "a hidden reviewer", "o/app", "-djp3", "djp3-", "dj--p3",
                                   "d" * 40, "djp3!", "dj p3", "@djp3", ""])
def test_an_owner_that_is_not_shaped_as_a_github_login_is_no_owner(owner):
    """The picture uses an owner as a login: whose card the agent is on, whether a move is
    theirs. Taken as it came, "team/o/platform" would put an agent on that team's card."""
    got = grange.parse({"cursor": 1, "granges": [{"grange": "djp3", "agents": [
        {"owner": owner, "name": "proj-86-pr-98", "state": "busy"},
        {"owner": "djp3", "name": "proj-86-pr-98", "state": "busy"}]}]})
    assert got.reported() == [Reported("djp3", "proj-86-pr-98", "busy")]


@pytest.mark.parametrize("owner", ["djp3", "DJP3", "d", "mira-k", "a1-b2-c3", "d" * 39])
def test_a_github_login_is_an_owner(owner):
    got = grange.parse({"cursor": 1, "granges": [{"grange": "djp3", "agents": [
        {"owner": owner, "name": "proj-86-pr-98", "state": "busy"}]}]})
    assert [a.owner for a in got.reported()] == [owner]


def test_a_field_is_held_to_the_granges_own_limit():
    got = grange.parse({"cursor": 1, "granges": [{"grange": "g" * 999, "agents": [
        {"owner": "djp3", "name": "n" * 999, "state": "s" * 999}]}]})
    agent = got.reported()[0]
    assert [len(got.granges[0].grange), len(agent.name), len(agent.state)] == [grange.FIELD] * 3


# ---- what can be wrong -----------------------------------------------------------------

def test_nothing_listening_means_no_grange_is_running():
    port = free_port()
    wrong = failure(Grange(port).read)
    assert (wrong.kind, wrong.retry) == ("down", grange.RETRY_DOWN)
    assert wrong.said == f"No agent-grange is running on this Mac (port {port})."


def test_a_grange_out_of_date_says_why_and_how_to_fix_it(serve):
    body = {"error": "stale", "summary": "the release moved on", "details": "…", "built_commit": "4fc66bd",
            "tag": "release", "tag_commit": "0a1b2c3", "fix": "make update\n  (in the grange's checkout)",
            "checked": "2026-11-19T18:00:00Z"}
    wrong = failure(Grange(serve(answering(body, 503, **{"Retry-After": "45", "Connection": "close"})).port).read)
    assert (wrong.kind, wrong.retry) == ("stale", 45.0)
    assert (wrong.why, wrong.fix) == ("the release moved on", "make update (in the grange's checkout)")
    assert wrong.said == ("The agent-grange on this Mac is out of date and answers nothing until it is updated: "
                          "the release moved on. To update it, in its folder: make update (in the grange's checkout)")


@pytest.mark.parametrize("header, waits", [({}, grange.RETRY_DOWN), ({"Retry-After": "soon"}, grange.RETRY_DOWN),
                                           ({"Retry-After": "86400"}, grange.RETRY_MOST),
                                           ({"Retry-After": "0"}, 1.0), ({"Retry-After": "-5"}, grange.RETRY_DOWN)])
def test_how_long_an_out_of_date_grange_is_left_alone(serve, header, waits):
    body = {"error": "image out of date", "tag": "agent-grange-latest"}
    wrong = failure(Grange(serve(answering(body, 503, **header)).port).read)
    assert (wrong.kind, wrong.retry, wrong.why, wrong.fix) == ("stale", waits, "", "")
    assert wrong.said == "The agent-grange on this Mac is out of date and answers nothing until it is updated."


@pytest.mark.parametrize("body", [b"<html>503 Service Unavailable</html>", b"", {"error": "upstream is restarting"},
                                  {"message": "try later"}, ["error", "fix"], "out of date"])
def test_a_503_that_does_not_say_the_grange_is_out_of_date_is_passing_trouble(serve, body):
    """A container that is restarting, a proxy, something that is no grange: told to update,
    the user would update a grange that is not out of date, and it would be asked again
    only once a minute."""
    wrong = failure(Grange(serve(answering(body, 503, **{"Retry-After": "60"})).port).read)
    assert (wrong.kind, wrong.retry) == ("trouble", grange.RETRY)
    assert wrong.said == "The agent-grange on this Mac refused the question (HTTP 503)." and wrong.fix == ""


def test_what_an_out_of_date_grange_says_is_cleaned_and_held_short():
    wrong = grange.out_of_date({"summary": "mo\x1bved", "fix": "make\x07 update " + "x" * 999})
    assert (wrong.why, wrong.fix[:12], len(wrong.fix)) == ("moved", "make update ", grange.FIELD)
    assert "\x1b" not in wrong.said and "\x07" not in wrong.said and len(wrong.said) < 2 * grange.FIELD + 150
    assert grange.out_of_date(["not", "a", "body"]).said == grange.out_of_date(None).said
    assert grange.out_of_date({"summary": 7, "fix": None}).said == grange.out_of_date(None).said


@pytest.mark.parametrize("status", [400, 404, 500])
def test_any_other_refusal_is_trouble(serve, status):
    wrong = failure(Grange(serve(answering({"error": "no"}, status)).port).read)
    assert (wrong.kind, wrong.said) == ("trouble", f"The agent-grange on this Mac refused the question (HTTP {status}).")


def test_an_answer_nested_too_deep_to_read_is_trouble_and_not_a_failure_of_the_apps_own(serve):
    deep = b'{"cursor": 1, "granges": ' + b"[" * 200_000 + b"]" * 200_000 + b"}"
    wrong = failure(Grange(serve(answering(deep)).port).read)
    assert wrong.kind == "trouble"
    assert wrong.said == "The agent-grange on this Mac answered with something that cannot be read (RecursionError)."


def test_one_age_that_is_no_time_at_all_does_not_void_the_answer(serve):
    body = {"cursor": 1, "grange": "djp3", "granges": [
        {"grange": "djp3", "seen_age_s": 10 ** 400, "farm_age_s": float("inf"), "farm_stale": False,
         "agents": [{"owner": "djp3", "name": "proj-86-pr-98", "state": "busy"}]}]}
    got = Grange(serve(answering(body)).port).read()
    assert got.granges == (Farm("djp3", 0.0, None, False, (Reported("djp3", "proj-86-pr-98", "busy"),)),)


def test_an_answer_that_is_not_json_is_trouble(serve):
    assert failure(Grange(serve(answering(b"<html>hello</html>")).port).read).kind == "trouble"


def test_an_answer_too_large_to_be_a_picture_is_not_read_to_its_end(serve, monkeypatch):
    monkeypatch.setattr(grange, "MOST_BYTES", 200)
    wrong = failure(Grange(serve(answering({"cursor": 1, "grange": "g" * 500})).port).read)
    assert wrong.kind == "trouble"
    assert wrong.said == "The agent-grange on this Mac answered with more than a picture holds."


def test_a_grange_that_never_answers_is_given_up_on(serve, monkeypatch):
    monkeypatch.setattr(grange, "SLACK", 0.2)
    stand_in = serve(lambda path: (stand_in.hold.wait(5), (200, {}, PICTURE))[1])
    began = time.monotonic()
    wrong = failure(lambda: Grange(stand_in.port).read(after=9124, wait=0.1))
    assert wrong.kind == "trouble" and "TimeoutError" in wrong.said and time.monotonic() - began < 3


# ---- giving a held question up ---------------------------------------------------------

def test_a_held_question_is_given_up_at_once_when_the_app_leaves(serve):
    stand_in = serve(lambda path: (stand_in.hold.wait(20), (200, {}, PICTURE))[1])
    there = Grange(stand_in.port)
    ended = []

    def ask():
        try:
            there.read(after=9124)
        except GrangeError as e:
            ended.append(e.kind)

    thread = threading.Thread(target=ask, daemon=True)
    thread.start()
    end = time.monotonic() + 5
    while not stand_in.asked and time.monotonic() < end:
        time.sleep(0.01)
    assert stand_in.asked, "the question never arrived"
    began = time.monotonic()
    there.close()
    thread.join(5)
    assert ended == ["closed"] and time.monotonic() - began < 2


def held_mid_answer(head: bytes):
    """A server that sends `head`, the start of an answer, and then stalls. Returns its port
    and the event that lets it go."""
    listening = socket.socket()
    listening.bind(("127.0.0.1", 0))
    listening.listen(1)
    let_go = threading.Event()

    def serve_one():
        conn, _ = listening.accept()
        conn.recv(4096)
        conn.sendall(head)
        let_go.wait(20)
        conn.close()
        listening.close()

    threading.Thread(target=serve_one, daemon=True).start()
    return listening.getsockname()[1], let_go


@pytest.mark.parametrize("head", [
    b'HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: 50\r\n\r\n{"cursor":',      # as its 503 is sent
    b'HTTP/1.0 200 OK\r\n\r\n{"cursor":',
    b'HTTP/1.1 200 OK\r\nContent-Length: 50\r\n\r\n{"cursor":',
    b"HTTP/1.1 200 OK\r\nContent-Le"])
def test_a_question_stalled_in_the_middle_of_its_answer_is_given_up_at_once_too(head):
    """For an answer that says the connection will close, http.client has let go of its
    socket by the time the body is read: this one keeps its own."""
    port, let_go = held_mid_answer(head)
    there, ended = Grange(port), []

    def ask():
        try:
            there.read(after=9124)
        except GrangeError as e:
            ended.append(e.kind)

    thread = threading.Thread(target=ask, daemon=True)
    thread.start()
    time.sleep(0.3)                                                      # the head has arrived by now
    began = time.monotonic()
    there.close()
    thread.join(5)
    let_go.set()
    assert ended == ["closed"] and time.monotonic() - began < 2


def test_closing_as_a_question_ends_is_harmless():
    """The socket it would wake may be shut, or gone, by the time it gets to it."""
    there, shut = Grange(free_port()), socket.socket()
    shut.close()
    there._sock = shut
    there.close()
    assert failure(there.read).kind == "closed"


def test_an_answer_that_arrives_as_the_app_leaves_is_not_handed_on(serve):
    there = Grange(0)
    stand_in = serve(lambda path: (there.close(), (200, {}, PICTURE))[1])
    there.port = stand_in.port
    assert failure(there.read).kind == "closed"


def test_nothing_is_asked_once_it_is_closed(serve):
    stand_in = serve(answering(PICTURE))
    there = Grange(stand_in.port)
    there.close()
    assert failure(there.read).kind == "closed" and stand_in.asked == []
    assert failure(Grange(free_port()).read).kind == "down"         # each grange is closed on its own


def test_closing_twice_or_with_nothing_asked_is_harmless():
    there = Grange(free_port())
    there.close()
    there.close()


# ---- the watch -------------------------------------------------------------------------

class _Raises:
    """In a script: raise this, though it is no GrangeError."""

    def __init__(self, wrong):
        self.wrong = wrong


class Scripted:
    """A grange that answers from a list, then holds the question until it is closed."""

    def __init__(self, *answers):
        self.answers, self.asked, self.closed = list(answers), [], threading.Event()

    def read(self, after=None, wait=grange.WAIT):
        self.asked.append(after)
        if not self.answers:
            self.closed.wait(10)
            raise GrangeError("closed", "given up")
        answer = self.answers.pop(0)
        if isinstance(answer, _Raises):
            raise answer.wrong
        if isinstance(answer, Exception):
            raise answer
        return answer

    def close(self):
        self.closed.set()


def watched(there, on_reading=None, on_error=None):
    """Run a watch until it has asked every scripted answer and holds; what it handed on."""
    readings, errors = [], []
    watch = Watch(there, on_reading or readings.append, on_error or errors.append)
    watch.start()
    end = time.monotonic() + 5
    while there.answers and time.monotonic() < end:
        time.sleep(0.005)
    time.sleep(0.05)
    watch.stop()
    watch.join(5)
    assert not watch.is_alive()
    return readings, errors


def test_the_watch_asks_again_with_the_cursor_of_the_last_answer():
    one, two = Reading(4, "djp3", 1, ()), Reading(9, "djp3", 1, ())
    there = Scripted(one, two)
    readings, errors = watched(there)
    assert (readings, errors, there.asked) == ([one, two], [], [None, 4, 9])


def test_after_a_failure_the_watch_waits_and_then_asks_without_a_cursor():
    one, down = Reading(4, "djp3", 1, ()), GrangeError("down", "nothing there", retry=0.05)
    there = Scripted(one, down, one)
    began = time.monotonic()
    readings, errors = watched(there)
    assert (readings, errors, there.asked) == ([one, one], [down], [None, 4, None, 4])
    assert time.monotonic() - began >= 0.05


def test_a_grange_that_was_answering_and_stops_is_asked_again_soon(monkeypatch):
    """It may be restarting. Waiting a minute, as for a Mac with no grange, would be longer
    than the app keeps what it last said."""
    monkeypatch.setattr(grange, "RETRY_SOON", 0.01)
    one = Reading(4, "djp3", 1, ())
    there = Scripted(one, GrangeError("trouble", "it dropped the question", retry=600),
                     GrangeError("down", "nothing there", retry=600), one)
    began = time.monotonic()
    readings, errors = watched(there)
    assert (readings, [e.kind for e in errors]) == ([one, one], ["trouble", "down"])
    assert there.asked == [None, 4, None, None, 4] and time.monotonic() - began < 3


def test_one_that_stays_silent_past_its_grace_is_asked_at_the_slow_pace_again(monkeypatch):
    monkeypatch.setattr(grange, "RETRY_SOON", 0.01)
    monkeypatch.setattr(grange, "GRACE", 0.0)
    there = Scripted(Reading(4, "djp3", 1, ()), GrangeError("down", "nothing there", retry=600))
    assert [e.kind for e in watched(there)[1]] == ["down"] and there.asked == [None, 4]      # and then it waits


def test_a_grange_out_of_date_is_not_asked_sooner_than_it_says(monkeypatch):
    monkeypatch.setattr(grange, "RETRY_SOON", 0.01)
    there = Scripted(Reading(4, "djp3", 1, ()), GrangeError("stale", "out of date", retry=600))
    assert [e.kind for e in watched(there)[1]] == ["stale"] and there.asked == [None, 4]


def test_a_mac_that_never_had_a_grange_is_not_asked_often(monkeypatch):
    monkeypatch.setattr(grange, "RETRY_SOON", 0.01)
    there = Scripted(GrangeError("down", "nothing there", retry=600))
    assert [e.kind for e in watched(there)[1]] == ["down"] and there.asked == [None]


def test_questions_are_kept_apart_however_fast_the_answers_come(monkeypatch):
    """Something on the port that answers at once, every time, would otherwise be asked
    flat out, with the screen drawn again at each answer."""
    monkeypatch.setattr(grange, "SPACING", 0.15)
    asked_at = []

    class Timed(Scripted):
        def read(self, after=None, wait=grange.WAIT):
            asked_at.append(time.monotonic())
            return super().read(after, wait)

    there = Timed(*(Reading(0, "djp3", 1, ()) for _ in range(3)))            # a cursor nothing is held for
    readings, errors = watched(there)
    assert len(readings) == 3 and errors == [] and there.asked[:3] == [None, 0, 0]
    assert len(asked_at) >= 3 and all(later - sooner >= 0.14 for sooner, later in zip(asked_at, asked_at[1:]))


def test_a_question_that_was_held_is_followed_at_once(monkeypatch):
    monkeypatch.setattr(grange, "SPACING", 0.05)
    asked_at = []

    class Held(Scripted):
        def read(self, after=None, wait=grange.WAIT):
            asked_at.append(time.monotonic())
            if self.answers:
                time.sleep(0.2)                                          # held longer than the gap
            return super().read(after, wait)

    watched(Held(Reading(4, "djp3", 1, ()), Reading(9, "djp3", 1, ())))
    assert 0.2 <= asked_at[1] - asked_at[0] < 0.25 + 0.15                # the hold, and no gap on top


class Recorded(threading.Event):
    """Stands in for the watch's `stopped`: a wait is noted and over at once."""

    def __init__(self):
        super().__init__()
        self.waits = []

    def wait(self, timeout=None):
        self.waits.append(timeout)
        return self.is_set()


def waits_of(there) -> list:
    """How long the watch waited after each of the scripted answers, in turn."""
    watch = Watch(there, lambda reading: None, lambda wrong: None)
    watch.stopped = waited = Recorded()
    watch.start()
    end = time.monotonic() + 5
    while there.answers and time.monotonic() < end:
        time.sleep(0.005)
    watch.stop()
    watch.join(5)
    assert not watch.is_alive()
    return waited.waits


def test_trouble_that_goes_on_is_asked_about_less_and_less_often():
    """Something else may listen on the grange's port: it is not to be asked every quarter
    of a minute for as long as the app runs."""
    refused = lambda: GrangeError("trouble", "refused the question (HTTP 404)")          # noqa: E731
    waits = waits_of(Scripted(*(refused() for _ in range(8))))
    assert waits[:8] == [15, 30, 60, 120, 240, 300, 300, 300] and max(waits) == grange.RETRY_MOST


def test_no_grange_at_all_is_asked_for_at_the_same_slow_pace_throughout():
    waits = waits_of(Scripted(*(GrangeError("down", "nothing there", retry=grange.RETRY_DOWN) for _ in range(4))))
    assert waits[:4] == [60, 60, 60, 60]


def test_a_grange_that_answers_puts_an_end_to_the_longer_waits(monkeypatch):
    monkeypatch.setattr(grange, "GRACE", 0.0)                             # so the minute of asking often is over
    refused = lambda: GrangeError("trouble", "refused the question (HTTP 500)")          # noqa: E731
    waits = waits_of(Scripted(refused(), refused(), refused(), Reading(4, "djp3", 1, ()), refused(), refused()))
    assert [w for w in waits if w] == [15, 30, 60, 15, 30]


def test_a_grange_restarted_is_asked_with_whatever_cursor_it_gave_last():
    """Its cursor starts again from 1: the watch compares nothing, it only hands it back."""
    there = Scripted(Reading(812, "djp3", 1, ()), Reading(1, "djp3", 2, ()))
    assert watched(there)[1] == [] and there.asked == [None, 812, 1]


def test_stopping_the_watch_ends_a_wait_between_two_tries():
    there = Scripted(GrangeError("down", "nothing there", retry=600))
    began = time.monotonic()
    readings, errors = watched(there)
    assert [e.kind for e in errors] == ["down"] and there.asked == [None] and time.monotonic() - began < 3


def test_the_watch_goes_on_when_what_it_hands_the_news_to_fails(monkeypatch):
    monkeypatch.setattr(grange, "RETRY_DOWN", 0.01)
    one, two = Reading(4, "djp3", 1, ()), Reading(9, "djp3", 1, ())
    there, seen = Scripted(one, two), []

    def breaks(reading):
        seen.append(reading)
        if reading is one:
            raise RuntimeError("the screen has gone")

    watched(there, on_reading=breaks)
    assert seen == [one, two] and there.asked == [None, None, 9]


def test_a_failure_nobody_foresaw_is_handed_on_as_trouble_and_the_watch_goes_on(monkeypatch):
    """Or the app would go on saying the grange answers, and showing what it last said."""
    monkeypatch.setattr(grange, "RETRY_DOWN", 0.01)
    one = Reading(4, "djp3", 1, ())
    there = Scripted(one, _Raises(RuntimeError("nobody foresaw this")), one)
    readings, errors = watched(there)
    assert readings == [one, one] and [(e.kind, e.said) for e in errors] == [
        ("trouble", "Asking the agent-grange on this Mac failed (RuntimeError).")]
    assert there.asked == [None, 4, None, 4]


def test_the_watch_against_a_real_server_follows_a_change(serve):
    answers = [dict(PICTURE, cursor=1), dict(PICTURE, cursor=2)]
    held = lambda: (stand_in.hold.wait(20), (200, {}, PICTURE))[1]                                    # noqa: E731
    stand_in = serve(lambda path: (200, {}, answers.pop(0)) if answers else held())
    readings = []
    watch = Watch(Grange(stand_in.port), readings.append, lambda e: None)
    watch.start()
    end = time.monotonic() + 5
    while len(stand_in.asked) < 3 and time.monotonic() < end:
        time.sleep(0.01)
    watch.stop()
    watch.join(5)
    assert not watch.is_alive() and [r.cursor for r in readings] == [1, 2]
    assert stand_in.asked == ["/v1/agents", "/v1/agents?after=1&wait=30", "/v1/agents?after=2&wait=30"]
