# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""The GitHub client: what it sends, what it understands, and what it says when it fails."""
import http.client
import json

import pytest

import githubapi
from githubapi import EVENTS, GitHub, GitHubError, Team, ensure_hook, teams_above

TOKEN = "ghp_abcdefghijklmnopqrstuvwxyz0123456789"


class FakeGitHub:
    """Answers requests from a table and remembers what was asked."""

    def __init__(self, replies):
        self.replies = replies            # {(method, path): (status, headers, body-as-python)}
        self.asked = []

    def __call__(self, request, timeout):
        path = request.full_url.removeprefix("https://api.github.com")
        body = json.loads(request.data) if request.data else None
        self.asked.append((request.get_method(), path, body, dict(request.header_items())))
        status, headers, data = self.replies[(request.get_method(), path)]
        return status, headers, json.dumps(data).encode()


def hook(id_, url, active=True):
    return {"id": id_, "active": active, "events": ["push"], "config": {"url": url}}


def test_whoami_reads_the_login_and_a_classic_tokens_scopes():
    fake = FakeGitHub({("GET", "/user"): (200, {"x-oauth-scopes": "repo, read:org"}, {"login": "djp3"})})
    me = GitHub(TOKEN, fake).whoami()
    assert (me.login, me.scopes, me.can_manage_hooks) == ("djp3", ["repo", "read:org"], True)
    method, path, body, headers = fake.asked[0]
    assert headers["Authorization"] == f"Bearer {TOKEN}" and headers["User-agent"] == "agent-frolic"


def test_a_fine_grained_token_has_no_scopes_to_read():
    fake = FakeGitHub({("GET", "/user"): (200, {}, {"login": "djp3"})})
    me = GitHub(TOKEN, fake).whoami()
    assert me.scopes is None and me.can_manage_hooks is None


def test_which_classic_tokens_may_list_their_users_teams():
    def may(scopes):
        fake = FakeGitHub({("GET", "/user"): (200, {} if scopes is None else {"x-oauth-scopes": scopes}, {"login": "x"})})
        return GitHub(TOKEN, fake).whoami().can_list_teams

    assert may("repo") and may("gist, read:org") and may("user") and may("admin:org")
    assert may("gist, workflow") is False and may("") is False
    assert may(None) is None                                     # a fine-grained token: only trying tells


def test_a_classic_token_without_repo_cannot_manage_hooks():
    fake = FakeGitHub({("GET", "/user"): (200, {"x-oauth-scopes": "read:org, gist"}, {"login": "djp3"})})
    assert GitHub(TOKEN, fake).whoami().can_manage_hooks is False


def test_repo_reports_what_the_token_may_do():
    fake = FakeGitHub({("GET", "/repos/djp3/Agent-Frolic"): (200, {}, {
        "full_name": "djp3/agent-frolic", "private": True, "permissions": {"admin": True, "push": True}})})
    info = GitHub(TOKEN, fake).repo("djp3/Agent-Frolic")
    assert (info.name, info.private, info.admin) == ("djp3/agent-frolic", True, True)   # GitHub's spelling


@pytest.mark.parametrize("status, headers, said, path, expect", [
    (401, {}, "Bad credentials", "/user", "did not accept the token"),
    (404, {}, "Not Found", "/repos/a/b", "No such repository, or the token cannot see it"),
    (404, {}, "Not Found", "/repos/a/b/hooks?per_page=100", "cannot manage this repository's webhooks"),
    (403, {}, "Resource protected by organization SAML enforcement.", "/repos/a/b", "single sign-on"),
    (403, {"x-ratelimit-remaining": "0"}, "API rate limit exceeded", "/user", "rate limit"),
    (403, {}, "Must have admin rights", "/repos/a/b", "not allowed"),
])
def test_failures_are_one_plain_sentence_and_never_hold_the_token(status, headers, said, path, expect):
    fake = FakeGitHub({("GET", path): (status, headers, {"message": said})})
    with pytest.raises(GitHubError) as caught:
        GitHub(TOKEN, fake).call("GET", path)
    assert caught.value.status == status and expect in str(caught.value)
    assert TOKEN not in str(caught.value) and TOKEN not in repr(caught.value)


def test_no_network_is_a_githuberror_too():
    def down(request, timeout):
        raise OSError("Network is unreachable")

    with pytest.raises(GitHubError) as caught:
        GitHub(TOKEN, down).whoami()
    assert caught.value.status == 0 and "could not be reached" in str(caught.value)


def team(org, slug, name, parent=None):
    return {"id": 7, "slug": slug, "name": name, "organization": {"login": org}, "parent": parent}


TEAMS_1, TEAMS_2 = "/user/teams?per_page=100&page=1", "/user/teams?per_page=100&page=2"


def test_the_teams_are_read_a_page_at_a_time_until_one_comes_back_short():
    first = [team("Acme", f"squad-{n}", f"Squad {n}") for n in range(100)]
    second = [team("Acme", "platform", "Platform", parent={"slug": "engineering", "name": "Engineering"}),
              team("Other", "docs", "Docs")]
    fake = FakeGitHub({("GET", TEAMS_1): (200, {}, first), ("GET", TEAMS_2): (200, {}, second)})
    teams = GitHub(TOKEN, fake).teams()
    assert [path for _, path, _, _ in fake.asked] == [TEAMS_1, TEAMS_2] and len(teams) == 102
    found = teams[100]
    assert (found.org, found.slug, found.name) == ("Acme", "platform", "Platform")
    assert found.parent == ("engineering", "Engineering")          # the team it is nested in, with its name
    assert (teams[101].org, teams[101].parent) == ("Other", None)


def test_being_on_no_team_is_an_empty_list_and_one_full_page_is_followed_by_a_look_for_more():
    fake = FakeGitHub({("GET", TEAMS_1): (200, {}, [])})
    assert GitHub(TOKEN, fake).teams() == [] and len(fake.asked) == 1
    full = [team("Acme", f"squad-{n}", f"Squad {n}") for n in range(100)]
    fake = FakeGitHub({("GET", TEAMS_1): (200, {}, full), ("GET", TEAMS_2): (200, {}, [])})
    assert len(GitHub(TOKEN, fake).teams()) == 100 and len(fake.asked) == 2


def test_the_teams_are_not_read_without_end(monkeypatch):
    monkeypatch.setattr(githubapi, "TEAM_PAGES", 2)
    full = [team("Acme", f"squad-{n}", f"Squad {n}") for n in range(100)]
    fake = FakeGitHub({("GET", TEAMS_1): (200, {}, full), ("GET", TEAMS_2): (200, {}, full)})
    assert len(GitHub(TOKEN, fake).teams()) == 200 and len(fake.asked) == 2


def test_a_token_that_may_not_read_the_teams_is_said_plainly_and_a_team_without_its_organization_is_unreadable():
    fake = FakeGitHub({("GET", TEAMS_1): (403, {}, {"message": "Resource not accessible by personal access token"})})
    with pytest.raises(GitHubError) as caught:
        GitHub(TOKEN, fake).teams()
    assert caught.value.status == 403 and str(caught.value) == (                 # in terms of teams, with
        "The token is not allowed to list your teams. "                           # what GitHub said is missing
        "GitHub says: Resource not accessible by personal access token.")
    fake = FakeGitHub({("GET", TEAMS_1): (404, {}, {"message": "Not Found"})})    # not "no such repository"
    with pytest.raises(GitHubError) as caught:
        GitHub(TOKEN, fake).teams()
    assert str(caught.value) == "The token is not allowed to list your teams. GitHub says: Not Found."
    fake = FakeGitHub({("GET", TEAMS_1): (403, {}, {})})
    with pytest.raises(GitHubError) as caught:
        GitHub(TOKEN, fake).teams()
    assert str(caught.value) == "The token is not allowed to list your teams."
    fake = FakeGitHub({("GET", TEAMS_1): (200, {}, [{"slug": "platform", "name": "Platform"}])})
    with pytest.raises(GitHubError) as caught:
        GitHub(TOKEN, fake).teams()
    assert caught.value.status == 0 and "could not be read" in str(caught.value)


def test_the_nesting_is_followed_upward_as_far_as_github_will_say():
    web = Team("Acme", "web", "Web", ("frontend", "Frontend"))
    replies = {
        ("GET", "/orgs/Acme/teams/frontend"): (200, {}, team("Acme", "frontend", "Frontend",
                                                             parent={"slug": "engineering", "name": "Engineering"})),
        ("GET", "/orgs/Acme/teams/engineering"): (200, {}, team("Acme", "engineering", "Engineering")),
    }
    fake = FakeGitHub(replies)
    above = teams_above(GitHub(TOKEN, fake), [web])
    assert [(t.org, t.slug, t.name) for t in above] == [("Acme", "frontend", "Frontend"),
                                                        ("Acme", "engineering", "Engineering")]     # nearest first
    assert [path for _, path, _, _ in fake.asked] == ["/orgs/Acme/teams/frontend", "/orgs/Acme/teams/engineering"]

    frontend = Team("Acme", "frontend", "Frontend", ("engineering", "Engineering"))     # the viewer is on it too:
    fake = FakeGitHub(replies)                                                          # it is not asked about
    assert [t.slug for t in teams_above(GitHub(TOKEN, fake), [web, frontend])] == ["frontend", "engineering"]
    assert [path for _, path, _, _ in fake.asked] == ["/orgs/Acme/teams/engineering"]

    assert teams_above(GitHub(TOKEN, FakeGitHub({})), [Team("Acme", "docs", "Docs")]) == []      # nested in nothing


def test_when_github_will_not_say_what_a_team_is_nested_in_the_asking_stops_and_nothing_fails(monkeypatch):
    web = Team("Acme", "web", "Web", ("frontend", "Frontend"))
    api = Team("Acme", "api", "API", ("backend", "Backend"))
    fake = FakeGitHub({("GET", "/orgs/Acme/teams/frontend"): (404, {}, {"message": "Not Found"}),
                       ("GET", "/orgs/Acme/teams/backend"): (200, {}, team("Acme", "backend", "Backend"))})
    above = teams_above(GitHub(TOKEN, fake), [web, api])
    assert [t.slug for t in above] == ["frontend", "backend"]           # what each names itself still stands
    assert [path for _, path, _, _ in fake.asked] == ["/orgs/Acme/teams/frontend"]      # refused once: not asked again
    assert teams_above(GitHub(TOKEN, cut_off), [web]) == [Team("Acme", "frontend", "Frontend")]   # no network

    monkeypatch.setattr(githubapi, "TEAMS_ABOVE", 2)                    # and it does not go on for ever
    chain = {("GET", f"/orgs/Acme/teams/t{n}"): (200, {}, team("Acme", f"t{n}", f"T{n}",
                                                               parent={"slug": f"t{n + 1}", "name": f"T{n + 1}"}))
             for n in range(1, 9)}
    fake = FakeGitHub(chain)
    above = teams_above(GitHub(TOKEN, fake), [Team("Acme", "t0", "T0", ("t1", "T1"))])
    assert [t.slug for t in above] == ["t1", "t2", "t3"] and len(fake.asked) == 2


@pytest.mark.parametrize("status, headers, said, passing", [
    (0, {}, "", True),                                           # not reached (set below, not by a reply)
    (502, {}, "Bad Gateway", True), (429, {}, "Too many requests", True),
    (403, {"x-ratelimit-remaining": "0"}, "API rate limit exceeded", True),
    (403, {}, "Resource not accessible by personal access token", False),
    (401, {}, "Bad credentials", False), (404, {}, "Not Found", False)])
def test_a_failure_says_whether_trying_again_later_may_work(status, headers, said, passing):
    if status == 0:
        with pytest.raises(GitHubError) as caught:
            GitHub(TOKEN, cut_off).teams()
    else:
        fake = FakeGitHub({("GET", TEAMS_1): (status, headers, {"message": said})})
        with pytest.raises(GitHubError) as caught:
            GitHub(TOKEN, fake).teams()
    assert caught.value.passing is passing


def test_ensure_hook_creates_one_when_the_repository_has_none():
    url = "https://frolic.ngrok-free.dev/hook"
    fake = FakeGitHub({("GET", "/repos/a/b/hooks?per_page=100"): (200, {}, [hook(7, "https://elsewhere/x")]),
                       ("POST", "/repos/a/b/hooks"): (201, {}, hook(42, url))})
    made = ensure_hook(GitHub(TOKEN, fake), "a/b", url, "s3cret")
    assert made.id == 42
    method, path, body, _ = fake.asked[-1]
    assert (method, path) == ("POST", "/repos/a/b/hooks")
    assert body["config"] == {"url": url, "content_type": "json", "secret": "s3cret", "insecure_ssl": "0"}
    assert body["events"] == list(EVENTS) and body["active"] is True


def test_ensure_hook_updates_the_one_it_remembers():
    url = "https://frolic.ngrok-free.dev/hook"
    fake = FakeGitHub({("GET", "/repos/a/b/hooks?per_page=100"): (200, {}, [hook(42, "https://old/hook", False)]),
                       ("PATCH", "/repos/a/b/hooks/42"): (200, {}, hook(42, url))})
    assert ensure_hook(GitHub(TOKEN, fake), "a/b", url, "s3cret", known_id=42).id == 42
    assert fake.asked[-1][2]["config"]["url"] == url and fake.asked[-1][2]["active"] is True


def test_ensure_hook_adopts_one_already_pointing_at_the_same_address():
    url = "https://frolic.ngrok-free.dev/hook"
    fake = FakeGitHub({("GET", "/repos/a/b/hooks?per_page=100"): (200, {}, [hook(9, url)]),
                       ("PATCH", "/repos/a/b/hooks/9"): (200, {}, hook(9, url))})
    assert ensure_hook(GitHub(TOKEN, fake), "a/b", url, "s3cret", known_id=0).id == 9
    assert [a[0] for a in fake.asked] == ["GET", "PATCH"]         # no second hook was created


def test_what_github_says_is_cleaned_of_escape_sequences_before_anything_sees_it():
    said = {"data": {"repository": {"pullRequest": {"title": "Fix \x1b]52;c;aGk=\x07it", "number": 3}}}}
    fake = FakeGitHub({("POST", "/graphql"): (200, {}, said),
                       ("GET", "/repos/a/b"): (404, {}, {"message": "Not\x1b[2J Found"})})
    assert GitHub(TOKEN, fake).graphql("query { x }") == {
        "repository": {"pullRequest": {"title": "Fix ]52;c;aGk=it", "number": 3}}}
    with pytest.raises(GitHubError) as failed:
        GitHub(TOKEN, fake).repo("a/b")
    assert "\x1b" not in failed.value.said + str(failed.value)


def test_graphql_returns_the_data_and_sends_the_variables():
    fake = FakeGitHub({("POST", "/graphql"): (200, {}, {"data": {"repository": {"nameWithOwner": "a/b"}}})})
    assert GitHub(TOKEN, fake).graphql("query { x }", {"owner": "a"}) == {"repository": {"nameWithOwner": "a/b"}}
    assert fake.asked[0][2] == {"query": "query { x }", "variables": {"owner": "a"}}


@pytest.mark.parametrize("error, expect", [
    ({"type": "NOT_FOUND", "message": "Could not resolve to a Repository with the name 'a/b'."},
     "No such repository, or the token cannot see it"),
    ({"type": "FORBIDDEN", "message": "Resource protected by organization SAML enforcement."}, "single sign-on"),
    ({"type": "RATE_LIMITED", "message": "API rate limit exceeded"}, "rate limit"),
    ({"message": "Field 'nope' doesn't exist on type 'PullRequest'"}, "GitHub refused the query: Field 'nope'"),
])
def test_a_graphql_reply_with_errors_is_a_failure_even_with_status_200(error, expect):
    fake = FakeGitHub({("POST", "/graphql"): (200, {}, {"data": None, "errors": [error]})})
    with pytest.raises(GitHubError) as caught:
        GitHub(TOKEN, fake).graphql("query { x }")
    assert expect in str(caught.value) and TOKEN not in str(caught.value)


def cut_off(request, timeout):
    """The connection broke while the reply was arriving (Wi-Fi gone mid-download)."""
    raise http.client.IncompleteRead(b'{"log', 40)


def answering(raw):
    """A reply of status 200 carrying exactly these bytes."""
    return lambda request, timeout: (200, {}, raw)


def garbled(request, timeout):
    """Something answered, but not in HTTP."""
    raise http.client.BadStatusLine("SSH-2.0-OpenSSH")


@pytest.mark.parametrize("transport", [cut_off, garbled])
def test_a_connection_cut_off_mid_reply_is_github_not_reached(transport):
    with pytest.raises(GitHubError) as caught:
        GitHub(TOKEN, transport).whoami()
    assert caught.value.status == 0 and "could not be reached" in str(caught.value)


SIGN_IN_PAGE = b"<!doctype html><title>Hotel Wi-Fi</title><form>Accept the terms</form>"


CALLS = {"whoami": lambda g: g.whoami(), "repo": lambda g: g.repo("a/b"), "hooks": lambda g: g.hooks("a/b"),
         "teams": lambda g: g.teams(),
         "create_hook": lambda g: g.create_hook("a/b", "https://x.dev/hook", "s"),
         "update_hook": lambda g: g.update_hook("a/b", 9, "https://x.dev/hook", "s"),
         "graphql": lambda g: g.graphql("query { x }")}


@pytest.mark.parametrize("name", CALLS)
@pytest.mark.parametrize("raw", [b"", SIGN_IN_PAGE, b"[]", b'"a string"', b'{"unexpected": true}'])
def test_a_reply_not_shaped_as_github_sends_it_is_a_github_error_not_a_crash(name, raw):
    """A network's sign-in page, an empty reply, or JSON of the wrong shape: every call says
    so plainly, as GitHub not reached, and nothing else escapes to stop the app. (A GraphQL
    reply with no "data" is not an empty repository either.)"""
    call = CALLS[name]
    if (name, raw) in (("hooks", b"[]"), ("teams", b"[]")):      # a repository with no webhooks; on no team
        assert call(GitHub(TOKEN, answering(raw))) == []
        return
    with pytest.raises(GitHubError) as caught:
        call(GitHub(TOKEN, answering(raw)))
    assert caught.value.status == 0 and "could not be read" in str(caught.value)
    assert TOKEN not in str(caught.value)
