# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared test setup: no test ever touches the real Keychain, the real settings, the real
list of repositories, the real GitHub, a real tunnel or the agent-grange that may be running
on this Mac, and none reaches the network."""
import queue
import urllib.request

import pytest
import truststore

import grange
import keychain
import main
from standins import FakeBrowser, FakeGitHub, FakeGrange, FakePulls, FakeTunnel


class MemoryKeychain:
    """Stands in for the macOS Keychain."""

    def __init__(self):
        self.items = {}

    def get_password(self, service, name):
        return self.items.get((service, name))

    def set_password(self, service, name, value):
        self.items[(service, name)] = value

    def delete_password(self, service, name):
        del self.items[(service, name)]


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    store = MemoryKeychain()
    keychain.use(store)
    monkeypatch.setenv("AGENT_FROLIC_SETTINGS", str(tmp_path / "settings.json"))
    monkeypatch.setenv("AGENT_FROLIC_REPOS", str(tmp_path / "repos.json"))
    yield store
    keychain.use(None)


@pytest.fixture(autouse=True)
def stand_ins(monkeypatch):
    """The app talks to stand-ins for GitHub, ngrok, the pull request fetch, the agent-grange
    and the browser."""
    FakeGitHub.hooks_made, FakeGitHub.deleted, FakeGitHub.tokens_seen, FakeTunnel.opened = {}, [], [], []
    FakeGitHub.unreachable, FakeGitHub.renamed, FakeGitHub.gates, FakeGitHub.waiting = 0, {}, {}, set()
    FakeGitHub.teams_listed, FakeGitHub.teams_broken, FakeGitHub.teams_asked = [], None, []
    FakeGitHub.teams_by_name = {}
    FakeGitHub.logins, FakeGitHub.scopes = {}, ["repo", "read:org"]
    FakeTunnel.made, FakeTunnel.gate = [], None
    FakePulls.open_, FakePulls.asked, FakePulls.broken, FakePulls.tips, FakePulls.spelled = {}, [], {}, {}, {}
    FakePulls.costs, FakePulls.access, FakePulls.orgs = {}, {}, set()
    monkeypatch.setattr(main, "fetch_pulls", FakePulls.fetch)
    monkeypatch.setattr(main, "GitHub", FakeGitHub)
    monkeypatch.setattr(main, "Tunnel", FakeTunnel)
    monkeypatch.setattr(main, "wait_reachable", lambda url, nonce, **how: True)
    FakeBrowser.opened, FakeBrowser.broken = [], None
    monkeypatch.setattr(main, "open_page", FakeBrowser.open)
    FakeGrange.answers, FakeGrange.asked, FakeGrange.made = queue.Queue(), [], []
    monkeypatch.setattr(main, "Grange", FakeGrange)
    monkeypatch.setattr(grange, "SPACING", 0.0)   # the stand-ins answer at once: no test waits out the gap
    yield
    for made in FakeGrange.made:                  # an app a test left running asks no more
        made.close()


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """A test may talk to this machine only (the webhook receiver). Anything else is a
    mistake in the test, and fails loudly instead of reaching out."""
    real = urllib.request.urlopen

    def local_only(request, *args, **kwargs):
        url = request if isinstance(request, str) else request.full_url
        if not url.startswith(("http://127.0.0.1:", "http://localhost:")):
            raise AssertionError(f"a test tried to reach {url.split('?')[0]}")
        return real(request, *args, **kwargs)

    monkeypatch.setattr(urllib.request, "urlopen", local_only)


@pytest.fixture(autouse=True)
def ssl_as_it_was():
    """main() and the self-check hand HTTPS certificates to macOS for the whole process;
    each test ends with Python's own ssl module back."""
    yield
    truststore.extract_from_ssl()
