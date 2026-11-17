# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""
The few GitHub REST calls the setup needs: who a token belongs to, which teams they are
on, whether it can see a repository, and the repository's webhooks. Standard library only.

The token is held by a `GitHub` object for as long as that object lives and is sent only
to api.github.com, in the Authorization header. It is never logged and never put in an
error message. Every failure is a `GitHubError` whose text is fit to show the user: a
connection cut off mid-reply, and a reply not shaped as GitHub documents it, included.

Tests pass their own `transport`, so nothing here needs the network to be tested.
"""
from __future__ import annotations

import http.client
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

from cleantext import clean

log = logging.getLogger("agent_frolic.github")

API = "https://api.github.com"
TIMEOUT_S = 20
USER_AGENT = "agent-frolic"
TEAMS_A_PAGE, TEAM_PAGES = 100, 20   # GitHub's largest page, and the most pages read: 2000 teams
TEAMS_ABOVE = 40                     # the most teams asked about when following the nesting upward
# Any one of these on a classic token lists its user's teams: the three GitHub's documentation
# names, and the two that include read:org.
TEAM_SCOPES = ("repo", "read:org", "user", "write:org", "admin:org")

# What a webhook is asked to send: everything that changes how a pull request stands.
EVENTS = ("pull_request", "pull_request_review", "pull_request_review_comment",
          "pull_request_review_thread", "issue_comment", "check_suite", "check_run",
          "status", "push", "create", "delete")

Reply = tuple[int, dict[str, str], bytes]                       # status, headers (lower-case), body
Transport = Callable[[urllib.request.Request, float], Reply]


class GitHubError(Exception):
    """A call to GitHub failed. `status` is the HTTP status, or 0 when GitHub was not
    reached. str() is one plain sentence, safe to show: it never holds the token."""

    def __init__(self, status: int, said: str, hint: str = "") -> None:
        self.status, self.said, self.hint = status, said, hint
        super().__init__(hint or said or f"GitHub answered {status}")

    @property
    def passing(self) -> bool:
        """Whether the same call may work later: GitHub was not reached, failed on its own
        side, or its rate limit is used up. A refusal would be one the next time too."""
        return self.status == 0 or self.status >= 500 or self.status == 429 or "rate limit" in self.hint.lower()


@dataclass
class Identity:
    login: str
    scopes: list[str] | None      # a classic token's scopes; None for a fine-grained token, which has none

    @property
    def can_manage_hooks(self) -> bool | None:
        """Whether the token may create webhooks: True / False for a classic token, None
        when only trying will tell (a fine-grained token)."""
        if self.scopes is None:
            return None
        return any(s in self.scopes for s in ("repo", "admin:repo_hook", "write:repo_hook"))

    @property
    def can_list_teams(self) -> bool | None:
        """Whether the token may list the teams its user is on: True / False for a classic
        token, None when only trying will tell (a fine-grained token)."""
        if self.scopes is None:
            return None
        return any(s in self.scopes for s in TEAM_SCOPES)


@dataclass
class RepoInfo:
    name: str                     # "owner/name", spelled the way GitHub spells it
    private: bool
    admin: bool                   # the token's user may administer it, which webhooks need
    archived: bool = False
    default_branch: str = ""      # "main", "" when GitHub did not say


@dataclass
class Team:
    org: str                      # the organization it belongs to, as GitHub spells it
    slug: str
    name: str                     # as people read it: "Platform"
    parent: tuple[str, str] | None = None     # the team it is nested in: (slug, name)


@dataclass
class Hook:
    id: int
    url: str
    active: bool = True
    events: list[str] = field(default_factory=list)


def _urlopen(request: urllib.request.Request, timeout: float) -> Reply:
    try:
        with urllib.request.urlopen(request, timeout=timeout) as reply:
            return reply.status, {k.lower(): v for k, v in reply.headers.items()}, reply.read()
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in e.headers.items()}, e.read()


class GitHub:
    def __init__(self, token: str, transport: Transport = _urlopen, api: str = API) -> None:
        self._token = token.strip()
        self._transport = transport
        self._api = api

    # ---- one call ----------------------------------------------------------------------
    def call(self, method: str, path: str, body: dict | None = None) -> tuple[Any, dict[str, str]]:
        """One request. Returns (decoded JSON or None, headers). Raises GitHubError."""
        request = urllib.request.Request(
            self._api + path, method=method,
            data=None if body is None else json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {self._token}",
                     "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28",
                     "User-Agent": USER_AGENT,
                     **({"Content-Type": "application/json"} if body is not None else {})})
        try:
            status, headers, raw = self._transport(request, TIMEOUT_S)
        except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as e:
            reason = getattr(e, "reason", None) or type(e).__name__      # HTTPException: cut off mid-reply
            raise GitHubError(0, f"could not reach GitHub: {reason}",
                              "GitHub could not be reached. Check the network and try again.") from None
        try:
            data = clean(json.loads(raw)) if raw else None      # what people typed, safe for a terminal
        except ValueError:
            data = None
        if status >= 400:
            said = data.get("message", "") if isinstance(data, dict) else ""
            log.info("GitHub %s %s -> %d %s", method, path, status, said)
            raise GitHubError(status, said, _hint(status, said, headers, path))
        return data, headers

    def graphql(self, query: str, variables: dict | None = None) -> dict:
        """One GraphQL query. Returns the reply's "data". A reply that carries errors is a
        failure, even when GitHub sent it with status 200. Raises GitHubError."""
        reply, headers = self.call("POST", "/graphql", {"query": query, "variables": variables or {}})
        errors = reply.get("errors") if isinstance(reply, dict) else None
        if errors:
            listed = errors if isinstance(errors, list) else [errors]
            first = listed[0] if isinstance(listed[0], dict) else {}
            said = str(first.get("message") or "the query was refused")
            kind = str(first.get("type") or "")
            log.info("GitHub GraphQL error %s: %s", kind, said)
            if kind == "NOT_FOUND":
                hint = "No such repository, or the token cannot see it."
            elif "saml" in said.lower() or "sso" in said.lower():
                hint = _hint(403, said, headers, "/graphql")
            elif kind in ("RATE_LIMITED", "RATE_LIMIT") or "rate limit" in said.lower():
                hint = "GitHub's rate limit is used up for now. Try again in a few minutes."
            elif kind == "FORBIDDEN":
                hint = "The token is not allowed to read this."
            else:
                hint = f"GitHub refused the query: {said[:200]}"
            raise GitHubError(200, said, hint)
        if not isinstance(reply, dict) or "data" not in reply or not isinstance(reply["data"], (dict, type(None))):
            raise _unreadable("POST /graphql")            # not "nothing there": a reply GitHub does not send
        return reply["data"] or {}

    # ---- who, and what they can see ----------------------------------------------------
    def whoami(self) -> Identity:
        data, headers = self.call("GET", "/user")
        scopes = headers.get("x-oauth-scopes")
        with _reading("GET /user"):
            return Identity(_text(data["login"]),
                            None if scopes is None else [s.strip() for s in scopes.split(",") if s.strip()])

    def repo(self, name: str) -> RepoInfo:
        data, _ = self.call("GET", f"/repos/{name}")
        with _reading(f"GET /repos/{name}"):
            permissions = data.get("permissions") or {}
            return RepoInfo(_text(data["full_name"]), bool(data.get("private")), bool(permissions.get("admin")),
                            bool(data.get("archived")), str(data.get("default_branch") or ""))

    def teams(self) -> list[Team]:
        """The teams the token's user is on, in every organization the token can see. Who
        else is on them is not asked for."""
        out: list[Team] = []
        for page in range(1, TEAM_PAGES + 1):
            data, _ = self.call("GET", f"/user/teams?per_page={TEAMS_A_PAGE}&page={page}")
            with _reading("GET /user/teams"):
                if not isinstance(data, list):
                    raise TypeError("not a list")
                out += [_team(item) for item in data]
            if len(data) < TEAMS_A_PAGE:
                break
        return out

    def team(self, org: str, slug: str) -> Team:
        """One team by its organization and slug: its name, and the team it is nested in."""
        path = f"/orgs/{urllib.parse.quote(org, safe='')}/teams/{urllib.parse.quote(slug, safe='')}"
        data, _ = self.call("GET", path)
        with _reading(f"GET {path}"):
            return _team(data)

    # ---- webhooks ----------------------------------------------------------------------
    def hooks(self, name: str) -> list[Hook]:
        data, _ = self.call("GET", f"/repos/{name}/hooks?per_page=100")
        with _reading(f"GET /repos/{name}/hooks"):
            if not isinstance(data, list):
                raise TypeError("not a list")
            return [_hook(item) for item in data]

    def create_hook(self, name: str, url: str, secret: str, events: tuple[str, ...] = EVENTS) -> Hook:
        data, _ = self.call("POST", f"/repos/{name}/hooks", {
            "name": "web", "active": True, "events": list(events),
            "config": {"url": url, "content_type": "json", "secret": secret, "insecure_ssl": "0"}})
        with _reading(f"POST /repos/{name}/hooks"):
            return _hook(data)

    def update_hook(self, name: str, hook_id: int, url: str, secret: str,
                    events: tuple[str, ...] = EVENTS, active: bool = True) -> Hook:
        data, _ = self.call("PATCH", f"/repos/{name}/hooks/{hook_id}", {
            "active": active, "events": list(events),
            "config": {"url": url, "content_type": "json", "secret": secret, "insecure_ssl": "0"}})
        with _reading(f"PATCH /repos/{name}/hooks/{hook_id}"):
            return _hook(data)

    def delete_hook(self, name: str, hook_id: int) -> None:
        self.call("DELETE", f"/repos/{name}/hooks/{hook_id}")

    def ping_hook(self, name: str, hook_id: int) -> None:
        """Ask GitHub to send the hook a "ping" delivery now."""
        self.call("POST", f"/repos/{name}/hooks/{hook_id}/pings")


def _team(item: dict) -> Team:
    parent = item.get("parent")
    return Team(_text(item["organization"]["login"]), _text(item["slug"]), _text(item.get("name") or item["slug"]),
                None if parent is None else (_text(parent["slug"]), _text(parent.get("name") or parent["slug"])))


def teams_above(github: GitHub, teams: list[Team]) -> list[Team]:
    """Every team that `teams` are nested in, however far up, nearest first. A review asked
    of any of them is asked of the people on `teams` too.

    Each team names the one it is nested in, and no further. So each of those is asked for
    in turn (`GitHub.team`) to learn what it is nested in, unless it is one of `teams`
    already. This never fails: when GitHub will not say, the asking stops, and what was
    learnt so far stands, which is at least the team each one is directly nested in."""
    listed = {(team.org.lower(), team.slug.lower()): team for team in teams}
    todo = [(team.org, *team.parent) for team in teams if team.parent is not None]
    out: list[Team] = []
    seen: set[tuple[str, str]] = set()
    asking, asked = True, 0
    while todo:
        org, slug, name = todo.pop(0)
        key = (org.lower(), slug.lower())
        if key in seen:
            continue
        seen.add(key)
        found = listed.get(key)                              # the viewer is on it too: it says so itself
        if found is None and asking and asked < TEAMS_ABOVE:
            asked += 1
            try:
                found = github.team(org, slug)
            except GitHubError as e:
                log.info("the teams further up could not be read: %s", e)
                asking = False
        out.append(Team(org, slug, name, found.parent if found is not None else None))
        if found is not None and found.parent is not None:
            todo.append((org, *found.parent))
    return out


def _hook(item: dict) -> Hook:
    return Hook(int(item["id"]), (item.get("config") or {}).get("url", ""), bool(item.get("active", True)),
                list(item.get("events") or []))


def _text(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise TypeError("not a name")
    return value


def _unreadable(what: str) -> GitHubError:
    """A reply that is not shaped as GitHub documents it, such as the sign-in page of a
    network that wants one first. Counted as GitHub not reached: try again later."""
    log.info("GitHub %s: the reply was not what GitHub sends", what)
    return GitHubError(0, f"unreadable reply to {what}",
                       "GitHub's reply could not be read. A network that asks you to sign in can cause "
                       "this. Try again in a moment.")


@contextmanager
def _reading(what: str) -> Iterator[None]:
    """Read a reply inside this: anything missing or of the wrong kind is a GitHubError."""
    try:
        yield
    except (KeyError, TypeError, ValueError, AttributeError, IndexError):
        raise _unreadable(what) from None


def _hint(status: int, said: str, headers: dict[str, str], path: str) -> str:
    """One plain sentence for the user about a failed call."""
    low = said.lower()
    if status == 401:
        return "GitHub did not accept the token. It may be mistyped, expired or revoked."
    if status == 403 and ("saml" in low or "sso" in low):
        return "The token must be authorized for this organization's single sign-on before it can be used there."
    if status in (403, 429) and (headers.get("x-ratelimit-remaining") == "0" or "rate limit" in low):
        return "GitHub's rate limit is used up for now. Try again in a few minutes."
    if status in (403, 404) and path.startswith("/user/teams"):
        said = said.rstrip(". ")                            # GitHub's own words name what is missing
        return "The token is not allowed to list your teams." + (f" GitHub says: {said}." if said else "")
    if status == 403:
        return "The token is not allowed to do this."
    if status == 404 and "/hooks" in path:
        return ("The token cannot manage this repository's webhooks. That takes admin rights on the "
                "repository, and a token that covers webhooks.")
    if status == 404:
        return "No such repository, or the token cannot see it."
    if status == 422:
        return f"GitHub refused the request: {said or 'it was not valid'}."
    return f"GitHub answered {status}{': ' + said if said else ''}."


def ensure_hook(github: GitHub, name: str, url: str, secret: str, known_id: int = 0) -> Hook:
    """Make sure the repository has our webhook, pointing at `url` and signed with `secret`.

    Ours is the one remembered from last time (`known_id`), else one already pointing at
    the same URL. It is updated in place (the secret cannot be read back, so it is always
    set again); only when there is none is one created. Raises GitHubError."""
    existing = github.hooks(name)
    ours = next((h for h in existing if known_id and h.id == known_id), None)
    ours = ours or next((h for h in existing if h.url == url), None)
    if ours is not None:
        return github.update_hook(name, ours.id, url, secret)
    return github.create_hook(name, url, secret)
