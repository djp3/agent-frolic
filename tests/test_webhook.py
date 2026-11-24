# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""The webhook receiver believes only what GitHub signed."""
import json
import socket
import time
import urllib.error
import urllib.request

import pytest

from webhook import ABOUT_ONE, MOST_NAMED, Delivery, Receiver, sign, verify

SECRET = "0123456789abcdef" * 4


def test_sign_matches_githubs_documented_example():
    # From GitHub's "Validating webhook deliveries" page.
    assert sign("It's a Secret to Everybody", b"Hello, World!") == (
        "sha256=757107ea0eb2509fc211221cce984b8a37570b6d7586c22c46f4379c8b043e17")


def test_verify_needs_the_right_secret_the_right_body_and_a_header():
    body = b'{"zen":"Keep it logically awesome."}'
    good = sign(SECRET, body)
    assert verify(SECRET, body, good)
    assert not verify(SECRET, body + b" ", good)
    assert not verify("another secret", body, good)
    assert not verify(SECRET, body, None) and not verify(SECRET, body, "") and not verify("", body, good)
    assert not verify(SECRET, body, good.replace("sha256=", "sha1="))
    assert not verify(SECRET, body, good[:-1] + "é")              # not plain ASCII: no match, not an error


@pytest.fixture
def receiver():
    got = []
    r = Receiver(SECRET, got.append)
    r.start()
    r.got = got
    yield r
    r.stop()


def post(receiver, payload, *, secret=SECRET, path="/hook", event="pull_request", delivery="d-1", signature=None):
    body = json.dumps(payload).encode() if not isinstance(payload, bytes) else payload
    headers = {"Content-Type": "application/json", "X-GitHub-Event": event, "X-GitHub-Delivery": delivery}
    if signature is not False:
        headers["X-Hub-Signature-256"] = signature or sign(secret, body)
    request = urllib.request.Request(f"http://127.0.0.1:{receiver.port}{path}", data=body, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=5) as reply:
            return reply.status
    except urllib.error.HTTPError as e:
        return e.code


def test_a_signed_delivery_is_accepted_and_handed_on(receiver):
    payload = {"action": "opened", "repository": {"full_name": "djp3/agent-frolic"}, "number": 17}
    assert post(receiver, payload) == 204
    (delivery,) = receiver.got
    assert (delivery.event, delivery.id, delivery.repo, delivery.action) == (
        "pull_request", "d-1", "djp3/agent-frolic", "opened")
    assert delivery.payload["number"] == 17 and delivery.said == "pull_request opened · djp3/agent-frolic"
    assert (receiver.accepted, receiver.refused) == (1, 0) and receiver.last is delivery


def test_a_wrong_or_missing_signature_is_refused_and_nothing_is_handed_on(receiver):
    payload = {"action": "opened"}
    assert post(receiver, payload, secret="not the secret") == 401
    assert post(receiver, payload, signature=False) == 401
    assert post(receiver, payload, signature="sha256=" + "0" * 64) == 401
    assert receiver.got == [] and (receiver.accepted, receiver.refused) == (0, 3)


def test_a_signature_that_is_not_plain_ascii_is_refused_like_any_wrong_one(receiver):
    assert post(receiver, {"action": "opened"}, signature="sha256=" + "é" * 64) == 401   # sent as Latin-1
    assert receiver.got == [] and (receiver.accepted, receiver.refused) == (0, 1)


def test_the_same_delivery_sent_twice_is_handed_on_once(receiver):
    assert post(receiver, {"action": "opened"}, delivery="same") == 204
    assert post(receiver, {"action": "opened"}, delivery="same") == 204
    assert post(receiver, {"action": "closed"}, delivery="other") == 204
    assert [d.action for d in receiver.got] == ["opened", "closed"]


def test_a_ping_and_a_payload_without_a_repository_are_fine(receiver):
    assert post(receiver, {"zen": "Design for failure.", "hook_id": 42}, event="ping", delivery="p") == 204
    assert (receiver.got[0].event, receiver.got[0].repo, receiver.got[0].action) == ("ping", "", "")
    assert receiver.got[0].said == "ping"


def test_signed_nonsense_is_refused_without_reaching_the_app(receiver):
    assert post(receiver, b"not json at all") == 400
    assert post(receiver, b"[1, 2, 3]", delivery="list") == 400
    assert receiver.got == []


def test_only_the_hook_path_takes_deliveries_and_health_answers_this_runs_nonce(receiver):
    assert post(receiver, {"action": "opened"}, path="/elsewhere") == 404
    with urllib.request.urlopen(f"http://127.0.0.1:{receiver.port}/health", timeout=5) as reply:
        assert reply.read().decode() == receiver.nonce
    with pytest.raises(urllib.error.HTTPError) as caught:
        urllib.request.urlopen(f"http://127.0.0.1:{receiver.port}/", timeout=5)
    assert caught.value.code == 404
    assert receiver.got == []


def test_a_handler_that_fails_does_not_fail_the_delivery():
    def explode(delivery):
        raise RuntimeError("the app tripped")

    r = Receiver(SECRET, explode)
    r.start()
    try:
        assert post(r, {"action": "opened"}) == 204
    finally:
        r.stop()


def test_it_listens_on_this_machine_only_and_says_nothing_on_stderr(receiver, capfd):
    assert receiver._server.server_address[0] == "127.0.0.1"
    post(receiver, {"action": "opened"})
    post(receiver, {"action": "opened"}, secret="wrong")
    out, err = capfd.readouterr()
    assert out == "" and err == ""


def test_a_delivery_is_cleaned_of_escape_sequences_headers_too(receiver):
    payload = {"action": "opened\x1b[2J", "repository": {"full_name": "djp3/agent\x1b]52;c;aGk=\x07-frolic"}}
    assert post(receiver, payload, event="pull\x1b[31m_request", delivery="d\x1b-9") == 204
    (delivery,) = receiver.got
    assert (delivery.event, delivery.id, delivery.repo, delivery.action) == (
        "pull[31m_request", "d-9", "djp3/agent]52;c;aGk=-frolic", "opened[2J")
    assert "\x1b" not in delivery.said


def test_a_request_the_server_fails_on_goes_to_the_log_not_to_stderr(receiver, capfd, caplog):
    try:
        raise OSError("the other end hung up")
    except OSError:
        receiver._server.handle_error(None, ("127.0.0.1", 1))
    out, err = capfd.readouterr()
    assert out == "" and err == ""
    assert "the webhook receiver failed on a request" in caplog.text


def test_stop_frees_the_port_and_start_twice_is_one_server():
    r = Receiver(SECRET, lambda d: None)
    port = r.start()
    assert r.start() == port and r.running
    r.stop()
    assert not r.running and r.port == 0
    r.stop()                                                     # stopping twice is fine


# ---- what a stranger who knows the tunnel's address can make it hold -----------------

@pytest.fixture
def impatient(monkeypatch):
    """A receiver with small limits, so the tests take a second rather than a minute."""
    monkeypatch.setattr(Receiver, "READ_TIMEOUT_S", 0.3)
    monkeypatch.setattr(Receiver, "BODY_DEADLINE_S", 0.8)
    monkeypatch.setattr(Receiver, "MAX_OPEN", 2)
    got = []
    r = Receiver(SECRET, got.append)
    r.start()
    r.got = got
    yield r
    r.stop()


def promise(receiver, length=1000, sent=b"{"):
    """A connection that promises a body of `length` bytes and sends only `sent`."""
    conn = socket.create_connection(("127.0.0.1", receiver.port), timeout=5)
    conn.sendall(f"POST /hook HTTP/1.1\r\nHost: x\r\nContent-Length: {length}\r\n"
                 f"X-Hub-Signature-256: sha256={'0' * 64}\r\n\r\n".encode() + sent)
    return conn


def closed(conn, within=3.0):
    """Whether the receiver closes the connection within `within` seconds, answering nothing."""
    conn.settimeout(within)
    try:
        return conn.recv(100) == b""
    except ConnectionResetError:
        return True
    except TimeoutError:
        return False
    finally:
        conn.close()


def test_a_body_that_never_arrives_is_given_up_on(impatient):
    started = time.monotonic()
    assert closed(promise(impatient))                            # not held open for ever
    assert time.monotonic() - started < 2 and impatient.got == [] and impatient.turned_away == 1


def test_a_body_sent_a_byte_at_a_time_is_given_up_on_at_its_deadline(impatient):
    conn, started = promise(impatient), time.monotonic()
    try:
        while time.monotonic() - started < 3:
            conn.sendall(b" ")                                   # never silent for a whole timeout
            time.sleep(0.1)
    except OSError:                                              # the receiver hung up
        pass
    finally:
        conn.close()
    assert time.monotonic() - started < 2.5                      # at its 0.8 s deadline, not after 3 s
    assert impatient.turned_away == 1 and impatient.got == []


def test_only_so_many_are_heard_at_once_and_the_rest_are_closed_without_a_thread(impatient):
    stalled = [promise(impatient), promise(impatient)]           # every slot taken
    time.sleep(0.05)
    started = time.monotonic()
    assert closed(promise(impatient), within=1.0)                # the next is shut at once
    assert time.monotonic() - started < 0.5 and impatient.turned_away == 1
    for conn in stalled:
        assert closed(conn)                                      # these time out and free their slots
    assert post(impatient, {"action": "opened"}) == 204          # and GitHub is heard again
    assert len(impatient.got) == 1


# ---- which pull requests a delivery is about ---------------------------------------------

def about(event, **payload):
    """A delivery about djp3/agent-frolic, which GitHub knows as repository 7."""
    return Delivery(event, "d-1", "djp3/agent-frolic", "",
                    {"repository": {"id": 7, "full_name": "djp3/agent-frolic"}, **payload})


@pytest.mark.parametrize("event", ABOUT_ONE)
def test_a_delivery_about_a_pull_request_names_it(event):
    assert about(event, pull_request={"number": 13, "title": "Agents on the board"}).pulls == [13]


def test_a_comment_names_its_pull_request_and_one_on_an_issue_names_none():
    assert about("issue_comment", issue={"number": 13, "pull_request": {"url": "https://example.net"}}).pulls == [13]
    assert about("issue_comment", issue={"number": 13}).pulls == []


def test_a_check_names_the_pull_requests_into_this_repository_that_it_ran_for():
    here = {"number": 13, "base": {"ref": "develop", "repo": {"id": 7}}}
    upstream = {"number": 2, "base": {"ref": "main", "repo": {"id": 99}}}      # a branch here, sent to another
    assert about("check_run", check_run={"pull_requests": [here, upstream, dict(here, number=11)]}).pulls == [11, 13]
    assert about("check_suite", check_suite={"pull_requests": [here, here]}).pulls == [13]
    assert about("check_run", check_run={"pull_requests": []}).pulls == []    # for a fork's, GitHub lists none
    assert about("check_run", check_suite={"pull_requests": [here]}).pulls == []     # the other event's place
    many = [dict(here, number=n) for n in range(1, MOST_NAMED + 2)]
    assert about("check_suite", check_suite={"pull_requests": many[:MOST_NAMED]}).pulls == list(range(1, MOST_NAMED + 1))
    assert about("check_suite", check_suite={"pull_requests": many}).pulls == []     # about the repository


@pytest.mark.parametrize("event", ["push", "create", "delete", "status", "ping", "unknown"])
def test_what_is_about_no_pull_request_in_particular_names_none(event):
    assert about(event, ref="refs/heads/develop", pull_request={"number": 13}, issue={"number": 13}).pulls == []


@pytest.mark.parametrize("named", [
    None, "13", [13], {}, {"number": "13"}, {"number": True}, {"number": 13.0}, {"number": 0}, {"number": -4},
    {"number": 2 ** 31}])
def test_a_pull_request_not_named_as_github_names_one_is_not_taken_for_one(named):
    assert Delivery("pull_request", "d-1", "djp3/agent-frolic", "", {"pull_request": named}).pulls == []
    issue = dict(named, pull_request={}) if isinstance(named, dict) else named
    assert Delivery("issue_comment", "d-1", "djp3/agent-frolic", "", {"issue": issue}).pulls == []
    assert Delivery("pull_request", "d-1", "djp3/agent-frolic", "").pulls == []          # a payload of nothing


@pytest.mark.parametrize("payload", [
    {"check_run": "tests"}, {"check_run": {}}, {"check_run": {"pull_requests": "13"}},
    {"check_run": {"pull_requests": [None, 13, "13", {"number": 13}, {"number": 13, "base": "develop"},
                                     {"number": 13, "base": {"repo": {"id": "7"}}}]}}])
def test_a_check_not_shaped_as_github_shapes_one_names_none(payload):
    assert about("check_run", **payload).pulls == []
    there = {"number": 13, "base": {"repo": {"id": 7}}}
    for repository in (None, {}, {"id": "7"}, {"id": 0}, {"id": True}):          # nor when "here" is not known
        assert Delivery("check_run", "d-1", "djp3/agent-frolic", "",
                        {"repository": repository, "check_run": {"pull_requests": [there]}}).pulls == []
    assert Delivery("pull_request", "d-1", "djp3/agent-frolic", "", payload="not a payload").pulls == []


def test_what_a_delivery_names_is_read_from_the_body_github_signed(receiver):
    body = {"action": "review_requested", "number": 13, "pull_request": {"number": 13},
            "repository": {"id": 7, "full_name": "djp3/agent-frolic"}}
    assert post(receiver, body) == 204
    (delivery,) = receiver.got
    assert (delivery.said, delivery.pulls) == ("pull_request review_requested · djp3/agent-frolic", [13])
