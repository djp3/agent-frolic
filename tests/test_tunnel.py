# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""The tunnel wrapper: what it asks ngrok for, and how it explains ngrok's refusals."""
import threading
import time

import pytest

from tunnel import Tunnel, TunnelError, clean_domain, hook_url, reachable, wait_reachable
from webhook import Receiver

AUTHTOKEN = "2abcDEFghiJKLmnoPQRstuVWXyz_3abcdefghijklmnop"


@pytest.mark.parametrize("typed, host", [
    ("frolic.ngrok-free.dev", "frolic.ngrok-free.dev"),
    ("  https://Frolic.ngrok-free.dev/  ", "frolic.ngrok-free.dev"),
    ("http://frolic.ngrok.app/hook", "frolic.ngrok.app"),
])
def test_clean_domain_keeps_the_bare_host(typed, host):
    assert clean_domain(typed) == host


@pytest.mark.parametrize("typed", ["", "  ", "frolic", "frolic ngrok dev", "-x.ngrok.dev", "https://"])
def test_clean_domain_refuses_what_is_not_a_domain(typed):
    with pytest.raises(ValueError):
        clean_domain(typed)


def test_hook_url_is_https_on_the_domain():
    assert hook_url("frolic.ngrok-free.dev") == "https://frolic.ngrok-free.dev/hook"


def test_start_asks_for_the_domain_and_the_local_port_and_stop_closes_it():
    opened, closed = [], []

    def open_(port, authtoken, domain):
        opened.append((port, authtoken, domain))
        return "the listener"

    tunnel = Tunnel("frolic.ngrok-free.dev", open_, closed.append)
    assert tunnel.start(51234, AUTHTOKEN) == "https://frolic.ngrok-free.dev" and tunnel.running
    assert tunnel.start(51234, AUTHTOKEN) == "https://frolic.ngrok-free.dev"     # already open: no second one
    assert opened == [(51234, AUTHTOKEN, "frolic.ngrok-free.dev")]
    tunnel.stop()
    tunnel.stop()
    assert closed == ["the listener"] and not tunnel.running
    assert AUTHTOKEN not in repr(vars(tunnel))                   # the authtoken is not kept


@pytest.mark.parametrize("said, expect", [
    ("authentication failed: The authtoken you specified is invalid. ERR_NGROK_107", "did not accept the authtoken"),
    ("Your account is limited to 1 simultaneous ngrok agent session. ERR_NGROK_108", "one free agent session"),
    ("failed to start tunnel: The domain is not reserved for your account ERR_NGROK_320", "would not serve that domain"),
    ("The endpoint is already online. ERR_NGROK_334", "already in use"),
    ("something nobody has seen before\nwith a second line", "could not open the tunnel: something nobody has seen before"),
])
def test_ngroks_refusals_become_one_plain_sentence(said, expect):
    def refuse(port, authtoken, domain):
        raise RuntimeError(said)

    with pytest.raises(TunnelError) as caught:
        Tunnel("frolic.ngrok-free.dev", refuse).start(1, AUTHTOKEN)
    assert expect in str(caught.value)


def test_an_error_that_quotes_the_authtoken_has_it_struck_out():
    def refuse(port, authtoken, domain):
        raise RuntimeError(f"could not log in with {authtoken}")

    with pytest.raises(TunnelError) as caught:
        Tunnel("frolic.ngrok-free.dev", refuse).start(1, AUTHTOKEN)
    assert AUTHTOKEN not in str(caught.value)


def test_reachable_is_true_only_when_the_address_leads_to_this_runs_receiver():
    receiver = Receiver("secret", lambda d: None)
    port = receiver.start()
    try:
        here = f"http://127.0.0.1:{port}"
        assert reachable(here, receiver.nonce, timeout=5)
        assert not reachable(here, "some other run's nonce", timeout=5)
    finally:
        receiver.stop()
    assert not reachable(here, receiver.nonce, timeout=2)        # nobody is listening any more


def test_a_tunnel_that_is_slow_to_come_up_is_asked_again_before_it_is_called_broken():
    answers, pauses = [False, False, True], []
    assert wait_reachable("https://x", "n", tries=5, pause=1.5, check=lambda u, n: answers.pop(0),
                          sleep=pauses.append)
    assert pauses == [1.5, 1.5] and answers == []              # it stopped asking once it got through

    pauses.clear()
    assert not wait_reachable("https://x", "n", tries=3, pause=2, check=lambda u, n: False, sleep=pauses.append)
    assert pauses == [2, 2]                                     # three tries, no pause after the last


def test_the_address_check_stops_as_soon_as_it_is_told_to():
    tried = []
    assert not wait_reachable("https://x", "n", tries=8, check=lambda u, n: tried.append(1) or False,
                              sleep=lambda seconds: None, stop=lambda: len(tried) == 2)
    assert len(tried) == 2                                      # not the eight it had


def test_closing_cuts_the_address_checks_pause_short():
    closing = threading.Event()
    threading.Timer(0.2, closing.set).start()
    began = time.monotonic()
    assert not wait_reachable("https://x", "n", tries=8, pause=1.5, check=lambda u, n: False,
                              sleep=closing.wait, stop=closing.is_set)
    assert time.monotonic() - began < 1.0                       # not the ten seconds of its pauses
