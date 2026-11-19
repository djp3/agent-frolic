# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""
The open pull requests of a repository, as GitHub records them.

One GraphQL query per repository (per page of 50) brings everything the picture needs:
who wrote each pull request, who was asked to review and has not answered, each reviewer's
latest word, how many review comments are still to resolve, whether the checks pass,
whether it can merge, and which branch it is based on. Nothing here is guessed or inferred
from anything but GitHub's records.

Stacks come from the branches: a pull request whose base branch is the head branch of
another open pull request in the same repository sits on top of that one.

Two things an author might be expected to do need rights in the repository: asking
someone to review, and merging. GitHub offers them only to people with write access (an
organization's triage role may also ask). Someone who sends a pull request from a fork of
a repository they do not belong to has neither, and GitHub shows them no way to pick a
reviewer. `can_ask` and `can_merge` record that, so the rule (standing.py) never names a
move its author cannot make. For the viewer's own pull requests GitHub says exactly what
the viewer may do; for everyone else it says only how they are tied to the repository
(owner, member, collaborator, or an outside contributor), and the first three are taken
to have the rights.

The words used in this module, and on screen:
  • a review's `state` is "approved", "changes" (changes requested), "commented" or
    "dismissed": that reviewer's latest word. With it go the comment threads that
    reviewer began (how many, and how many are still to resolve) and their `trail`: what
    they said along the way, oldest first, each step with its own threads;
  • `checks` is "pass", "fail", "running", "none" or "unpaid". "unpaid" is a failure
    that is not the code's: every failing check is a job GitHub did not start because the
    account could not pay for it (payments failed, or the spending limit is reached). It
    is shown as its own mark and counts as passing. When the checks fail, a second query
    reads each failing check's note to tell the two apart;
  • `mergeable` is "yes", "conflict" or "unknown" (GitHub is still working it out);
  • `decision` is GitHub's overall review decision: "approved", "changes",
    "review required", or "" when the branch has no rule that asks for one.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone

from githubapi import GitHub, GitHubError
from repos import is_name

log = logging.getLogger("agent_frolic.pulls")

PAGE = 50                         # pull requests per query
MOST = 300                        # stop after this many, however many are open
MOST_THREADS = 1000               # review threads read per pull request; any beyond count as open

# What is read of each review thread: whether it is resolved, and who began it.
_THREAD = "isResolved comments(first: 1) { nodes { author { login } pullRequestReview { id author { login } } } }"

QUERY = """
query($owner: String!, $name: String!, $page: Int!, $cursor: String) {
  viewer { login }
  rateLimit { cost limit }
  repository(owner: $owner, name: $name) {
    nameWithOwner
    owner { __typename }
    viewerPermission
    defaultBranchRef { name target { oid } }
    pullRequests(states: OPEN, first: $page, after: $cursor, orderBy: {field: UPDATED_AT, direction: DESC}) {
      totalCount
      pageInfo { hasNextPage endCursor }
      nodes {
        number title url isDraft createdAt updatedAt
        author { login }
        authorAssociation
        baseRefName baseRefOid headRefName headRefOid isCrossRepository
        headRepositoryOwner { login }
        mergeable reviewDecision
        reviewRequests(first: 30) { nodes { requestedReviewer {
          __typename ... on User { login } ... on Bot { login } ... on Mannequin { login }
          ... on Team { slug } } } }
        latestOpinionatedReviews(first: 30) { nodes { author { login } state submittedAt } }
        latestReviews(first: 30) { nodes { author { login } state submittedAt } }
        reviews(last: 100) { nodes { id author { login } state submittedAt } }
        reviewThreads(first: 100) { totalCount pageInfo { hasNextPage endCursor } nodes { %s } }
        commits(last: 1) { nodes { commit { committedDate statusCheckRollup { state } } } }
      }
    }
  }
}
""" % _THREAD

# The rest of one pull request's review threads, when it has more than the first query sends.
THREADS_QUERY = """
query($owner: String!, $name: String!, $number: Int!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 100, after: $cursor) { pageInfo { hasNextPage endCursor } nodes { %s } }
    }
  }
}
""" % _THREAD

# Why the checks of some pull requests fail: one aliased block per pull request, at most
# LOOK_AT pull requests per query.
WHY_QUERY = """
query($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
%s
  }
}
"""
WHY_PULL = """    p%d: pullRequest(number: %d) { commits(last: 1) { nodes { commit { statusCheckRollup {
      contexts(first: 100) { nodes {
        __typename
        ... on CheckRun { name status conclusion title summary annotations(first: 5) { nodes { message } } }
        ... on StatusContext { context state description }
      } } } } } } }"""
LOOK_AT = 10
_FAILED = {"FAILURE", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE"}
# GitHub's note on a job it did not run for lack of payment: "The job was not started
# because recent account payments have failed or your spending limit needs to be increased."
_NOT_PAID = re.compile(r"was not started because.{0,200}?(payment|spending limit|billing)", re.I | re.S)

_REVIEW = {"APPROVED": "approved", "CHANGES_REQUESTED": "changes", "COMMENTED": "commented",
           "DISMISSED": "dismissed"}
_CHECKS = {"SUCCESS": "pass", "FAILURE": "fail", "ERROR": "fail", "PENDING": "running",
           "EXPECTED": "running"}
_MERGEABLE = {"MERGEABLE": "yes", "CONFLICTING": "conflict", "UNKNOWN": "unknown"}
_DECISION = {"APPROVED": "approved", "CHANGES_REQUESTED": "changes", "REVIEW_REQUIRED": "review required"}
# How the author is tied to the repository, when that tie gives them no rights in it.
_OUTSIDE = {"CONTRIBUTOR", "FIRST_TIME_CONTRIBUTOR", "FIRST_TIMER", "MANNEQUIN", "NONE"}
# The viewer's permission in a repository -> (may ask for a review, may merge).
_RIGHTS = {"ADMIN": (True, True), "MAINTAIN": (True, True), "WRITE": (True, True),
           "TRIAGE": (True, False), "MODERATE": (True, False), "TRIAGE_PLUS": (True, False),
           "READ": (False, False)}


# A bot's login -> the name GitHub shows for it. Copilot signs its reviews with the long
# login and the comments inside them as "Copilot": both are the same reviewer.
KNOWN_AS = {"copilot-pull-request-reviewer": "copilot"}
HIDDEN = "a hidden reviewer"      # one GitHub lists as asked and does not name; no login has a space


def team_of(org: str, slug: str) -> str:
    """How a team is named here: "team/org/slug". The same team wherever it is read."""
    return f"team/{org}/{slug}"


def is_team(who: str) -> bool:
    """Whether one of those asked to review is a team ("team/org/slug"), not a login."""
    return who.startswith("team/")


def team_slug(who: str) -> str:
    """A team as it reads where its name is not known: "team/slug", as GitHub gives it."""
    return "team/" + who.rsplit("/", 1)[-1]


def known_as(login: str) -> str:
    """A login as it should read: a bot loses its "[bot]" and goes by its known name."""
    bare = login.removesuffix("[bot]")
    return KNOWN_AS.get(bare.lower(), bare)


@dataclass
class Step:
    """One step of a reviewer's trail: a review, or a run of their reviews of one kind."""
    state: str                    # "approved" | "changes" | "commented" | "dismissed"
    threads: int = 0              # the comment threads begun in it
    open: int = 0                 # how many of those are still to resolve
    at: datetime | None = None


@dataclass
class Review:
    who: str
    state: str                    # "approved" | "changes" | "commented" | "dismissed"
    at: datetime | None = None
    threads: int = 0              # the comment threads this reviewer began
    open: int = 0                 # how many of those are still to resolve
    trail: list[Step] = field(default_factory=list)      # what they said along the way, oldest first


@dataclass
class PullRequest:
    repo: str                     # "owner/name"
    number: int
    title: str
    url: str                      # the address in GitHub's reply: free text, so never opened (see `page`)
    author: str                   # login; "ghost" when the account is gone
    draft: bool
    head: str                     # the branch it comes from
    base: str                     # the branch it goes into
    base_oid: str                 # the commit of the base branch it stands on
    head_oid: str
    fork: bool                    # the head branch lives in another repository (a fork)
    head_owner: str
    created: datetime | None
    updated: datetime | None      # the last time anything happened on it
    mergeable: str                # "yes" | "conflict" | "unknown"
    decision: str                 # "approved" | "changes" | "review required" | ""
    asked: list[str] = field(default_factory=list)       # asked to review and not answered yet;
                                                          # a team shows as "team/org/slug", one GitHub
                                                          # does not name as HIDDEN
    reviews: list[Review] = field(default_factory=list)  # each reviewer's latest word, oldest first
    comments_open: int = 0        # review comments still to resolve
    comments_total: int = 0
    checks: str = "none"          # "pass" | "fail" | "running" | "none"
    can_ask: bool = True          # the author has the rights here to ask someone to review
    can_merge: bool = True        # the author has the rights here to merge
    parent: int = 0               # the pull request this one is stacked on, 0 when it stands on a branch
    children: list[int] = field(default_factory=list)
    depth: int = 0                # 0 on a branch, 1 on a pull request, 2 on one of those, …

    @property
    def short_sha(self) -> str:
        return self.base_oid[:7]

    @property
    def page(self) -> str:
        """The address of its page on GitHub, built from what the app already trusts: the
        repository's name, which GitHub keeps to a few plain characters, and the number.
        This is what is shown and what `o` opens, not the address in GitHub's reply. ""
        when the repository's name is not a plain owner/name."""
        return f"https://github.com/{self.repo}/pull/{self.number}" if is_name(self.repo) else ""


@dataclass
class RepoPulls:
    repo: str                     # as GitHub spells it
    default_branch: str
    pulls: list[PullRequest]      # in stack order: each pull request, then the ones stacked on it
    total: int                    # how many are open on GitHub (more than len(pulls) past MOST)
    at: datetime                  # when this was fetched
    tip: str = ""                 # the commit the default branch points at now, "" when unknown
    access: str = ""              # what the viewer may do here: "admin", "maintain", "write",
                                  # "triage", "read"; "" when GitHub did not say
    cost: int = 0                 # what reading it took of GitHub's hourly allowance, in its points
    limit: int = 0                # that allowance, as GitHub said; 0 when it did not
    org: bool = False             # its owner is an organization, not a person

    @property
    def rights(self) -> tuple[bool, bool]:
        """What the viewer may do here: (ask someone to review, merge). Neither when GitHub
        did not say."""
        return _RIGHTS.get(self.access.upper().replace(" ", "_"), (False, False))


def when(text: str | None) -> datetime | None:
    """GitHub's timestamp ("2026-11-14T16:11:02Z") as an aware datetime."""
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _login(node: dict | None) -> str:
    return (node or {}).get("login") or "ghost"          # GitHub shows a deleted account as ghost


def _reviews(node: dict, author: str) -> list[Review]:
    """Each reviewer's latest word. Someone's latest opinion (approved, changes requested)
    stands even when they commented again afterwards; someone who has only ever commented
    shows as "commented". The author's own comments on their pull request are not a review,
    and a review still being written ("PENDING") has not been said."""
    latest: dict[str, Review] = {}
    for source in ("latestReviews", "latestOpinionatedReviews"):      # opinions win, so they go last
        for item in (node.get(source) or {}).get("nodes") or []:
            who = _login(item.get("author"))
            state = _REVIEW.get(item.get("state") or "")
            if state is None or who == author:
                continue
            latest[who] = Review(who, state, when(item.get("submittedAt")))
    far_past = datetime.min.replace(tzinfo=timezone.utc)
    return sorted(latest.values(), key=lambda r: r.at or far_past)


def _first(thread: dict | None) -> dict:
    """The comment that began a review thread."""
    return (((thread or {}).get("comments") or {}).get("nodes") or [None])[0] or {}


def _starter(thread: dict | None) -> str:
    """Who began a review thread: the author of the review its first comment belongs to
    (the account that review is listed under), else that comment's author; "" when GitHub
    does not say."""
    first = _first(thread)
    review = first.get("pullRequestReview") or {}
    return (review.get("author") or {}).get("login") or (first.get("author") or {}).get("login") or ""


def _trails(node: dict, reviews: list[Review], threads: list) -> None:
    """Give each reviewer the comment threads they began and their trail.

    The trail is every review they submitted, oldest first, made readable:
      • a comment-only review that began no thread is left out (a reply inside someone's
        thread is recorded by GitHub as such a review), unless it is all they ever said;
      • reviews of one kind in a row count as one step, except requests for changes: each
        round of those is a step of its own;
      • a thread belongs to the review it was begun in. When GitHub does not say which,
        it goes to that reviewer's latest review that is not an approval.
    Without the list of reviews the trail is one step: their latest word."""
    by_name = {known_as(review.who).lower(): review for review in reviews}
    made: dict[str, list[Step]] = {}                             # reviewer -> one Step per review, for now
    by_id: dict[str, Step] = {}
    far_past = datetime.min.replace(tzinfo=timezone.utc)
    listed = [item for item in (node.get("reviews") or {}).get("nodes") or [] if item]
    for item in sorted(listed, key=lambda item: when(item.get("submittedAt")) or far_past):
        state = _REVIEW.get(item.get("state") or "")
        who = known_as(_login(item.get("author"))).lower()
        if state is None or who not in by_name:                  # still being written, or the author's own
            continue
        one = Step(state, at=when(item.get("submittedAt")))
        made.setdefault(who, []).append(one)
        if item.get("id"):
            by_id[item["id"]] = one
    for who, review in by_name.items():
        made.setdefault(who, [Step(review.state, at=review.at)])
    for thread in threads:
        one = by_id.get((_first(thread).get("pullRequestReview") or {}).get("id") or "")
        if one is None:
            theirs = made.get(known_as(_starter(thread)).lower())
            if not theirs:
                continue                                         # the author's own thread, or nobody's
            one = next((step for step in reversed(theirs) if step.state != "approved"), theirs[-1])
        one.threads += 1
        one.open += not (thread or {}).get("isResolved")
    for who, review in by_name.items():
        every = made[who]
        review.threads, review.open = sum(s.threads for s in every), sum(s.open for s in every)
        said = [step for step in every if step.state != "commented" or step.threads] or every[-1:]
        trail: list[Step] = []
        for step in said:                                        # folded into copies: `every` is counted above
            last = trail[-1] if trail else None
            if last is not None and last.state == step.state and step.state != "changes":
                last.threads, last.open, last.at = last.threads + step.threads, last.open + step.open, step.at
            else:
                trail.append(replace(step))
        review.trail = trail


def _asked(node: dict, org: str) -> list[str]:
    """Who was asked to review and has not answered. GitHub names a team by its slug alone;
    it is one of the teams of `org`, the owner of the repository, since no other
    organization's team can be asked. So a team is "team/org/slug": a name that means the
    same team wherever it is read. A login cannot hold a slash, so nothing else starts so.

    A request whose reviewer GitHub leaves out is kept, once, as HIDDEN. It is taken to be
    a team the token may not see (not checked against GitHub). Dropped, it would leave a
    pull request that waits on a review looking as if nobody had been asked, and the board
    telling its author to ask."""
    out = []
    for item in (node.get("reviewRequests") or {}).get("nodes") or []:
        reviewer = (item or {}).get("requestedReviewer")
        if reviewer is None:
            if HIDDEN not in out:
                out.append(HIDDEN)
        elif reviewer.get("login"):
            out.append(reviewer["login"])
        elif reviewer.get("slug"):
            out.append(team_of(org, reviewer["slug"]))
        elif reviewer.get("__typename"):
            out.append(reviewer["__typename"].lower())           # a kind this query does not name
    return out


def parse_pull(repo: str, node: dict) -> PullRequest:
    author = _login(node.get("author"))
    threads = node.get("reviewThreads") or {}
    commit = ((((node.get("commits") or {}).get("nodes") or [{}])[-1] or {}).get("commit")) or {}
    rollup = commit.get("statusCheckRollup") or {}
    seen = threads.get("nodes") or []
    total = max(int(threads.get("totalCount") or 0), len(seen))
    unseen = total - len(seen)                                   # past MOST_THREADS: not known to be resolved
    still_open = sum(1 for thread in seen if not (thread or {}).get("isResolved")) + unseen
    inside = (node.get("authorAssociation") or "") not in _OUTSIDE
    reviews = _reviews(node, author)
    _trails(node, reviews, seen)
    return PullRequest(
        repo=repo, number=int(node["number"]), title=node.get("title") or "", url=node.get("url") or "",
        author=author, draft=bool(node.get("isDraft")),
        head=node.get("headRefName") or "", base=node.get("baseRefName") or "",
        base_oid=node.get("baseRefOid") or "", head_oid=node.get("headRefOid") or "",
        fork=bool(node.get("isCrossRepository")), head_owner=_login(node.get("headRepositoryOwner")),
        created=when(node.get("createdAt")), updated=when(node.get("updatedAt")),
        mergeable=_MERGEABLE.get(node.get("mergeable") or "", "unknown"),
        decision=_DECISION.get(node.get("reviewDecision") or "", ""),
        asked=_asked(node, repo.split("/", 1)[0]), reviews=reviews,
        comments_open=still_open, comments_total=total,
        checks=_CHECKS.get(rollup.get("state") or "", "none"),
        can_ask=inside, can_merge=inside)


def stack(pulls: list[PullRequest], default_branch: str,
          long_lived: list[str] | None = None) -> list[PullRequest]:
    """Link each pull request to the one it is stacked on, and return them in stack order:
    the ones standing on a branch first (the default branch's before the others, then by
    number), each followed by the ones stacked on it.

    A pull request is stacked on another when its base branch is that one's head branch
    and that head branch lives in this repository. A fork's branch of the same name is a
    different branch, so it is never a parent. Nor is a long-lived branch: a release pull
    request from develop into main does not carry every pull request into develop. The
    repository's list of them is `long_lived`; until it has one, the default branch."""
    fixed = set(long_lived) if long_lived is not None else {default_branch}
    by_head = {p.head: p for p in pulls if not p.fork and p.head not in fixed}
    for p in pulls:
        p.parent, p.children, p.depth = 0, [], 0
    for p in pulls:
        parent = by_head.get(p.base)
        if parent is not None and parent is not p:
            p.parent = parent.number
    by_number = {p.number: p for p in pulls}
    for p in pulls:                                              # a loop of bases cannot be a stack
        path, at = [p.number], p
        while at.parent:
            if at.parent in path:
                # Cut the loop itself, not the walk that ran into it, and always at its
                # lowest number, so the tree is the same in whatever order GitHub lists them.
                loop = path[path.index(at.parent):]
                by_number[min(loop)].parent = 0
                break
            path.append(at.parent)
            at = by_number[at.parent]
    for p in pulls:
        if p.parent:
            by_number[p.parent].children.append(p.number)
    out: list[PullRequest] = []

    def visit(p: PullRequest, depth: int) -> None:
        p.depth = depth
        p.children.sort()
        out.append(p)
        for number in p.children:
            visit(by_number[number], depth + 1)

    for root in sorted((p for p in pulls if not p.parent),
                       key=lambda p: (p.base != default_branch, p.base, p.number)):
        visit(root, 0)
    return out


def judge(contexts: list) -> tuple[str, int, int, str]:
    """What failing checks amount to, from each check's record: ("unpaid" | "running" |
    "fail", how many fail, how many of those GitHub did not start for lack of payment, a
    note from a failure that is something else).

    "unpaid" needs every failure to be one of those; with other checks still running it is
    "running". A single real failure keeps it "fail"."""
    failing = unpaid = 0
    pending, note = False, ""
    for item in contexts:
        if not item:
            continue
        if item.get("__typename") == "StatusContext":
            if item.get("state") in ("FAILURE", "ERROR"):
                failing += 1
                note = note or item.get("description") or item.get("context") or ""
            elif item.get("state") in ("PENDING", "EXPECTED"):
                pending = True
            continue
        conclusion = item.get("conclusion")
        if conclusion is None:
            pending = pending or item.get("status") != "COMPLETED"
            continue
        if conclusion not in _FAILED:
            continue
        failing += 1
        said = [item.get("title") or "", item.get("summary") or ""]
        said += [(a or {}).get("message") or "" for a in (item.get("annotations") or {}).get("nodes") or []]
        if any(_NOT_PAID.search(text) for text in said):
            unpaid += 1
        else:
            note = note or next((text for text in said if text), "") or item.get("name") or ""
    if failing and unpaid == failing:
        return ("running" if pending else "unpaid"), failing, unpaid, note
    return "fail", failing, unpaid, note


def look_into_failures(github: GitHub, repo: str, pulls: list[PullRequest]) -> None:
    """For the pull requests whose checks fail, ask GitHub why, and reclassify the ones
    that fail only because the account could not pay. Trouble here is logged and changes
    nothing: the checks stay "fail"."""
    owner, name = repo.split("/", 1)
    failing = [pull for pull in pulls if pull.checks == "fail"]
    for start in range(0, len(failing), LOOK_AT):
        batch = failing[start:start + LOOK_AT]
        query = WHY_QUERY % "\n".join(WHY_PULL % (pull.number, pull.number) for pull in batch)
        try:
            data = github.graphql(query, {"owner": owner, "name": name})
        except GitHubError as e:
            log.warning("could not look into the failing checks of %s: %s", repo, e)
            return
        repository = data.get("repository") or {}
        for pull in batch:
            commits = ((repository.get(f"p{pull.number}") or {}).get("commits") or {}).get("nodes") or [{}]
            rollup = ((commits[-1] or {}).get("commit") or {}).get("statusCheckRollup") or {}
            checks, bad, unpaid, note = judge((rollup.get("contexts") or {}).get("nodes") or [])
            pull.checks = checks
            log.info("%s#%d: %d failing checks, %d of them not started for lack of payment%s", repo, pull.number,
                     bad, unpaid, f"; another says: {note[:100]!r}" if note and checks == "fail" else "")


def read_every_thread(github: GitHub, owner: str, name: str, node: dict) -> None:
    """GitHub sends a pull request's first 100 review threads with it. Read the rest into
    `node`, 100 at a time, up to MOST_THREADS; parse_pull counts any beyond as open. Raises
    GitHubError."""
    threads = node.get("reviewThreads") or {}
    seen = list(threads.get("nodes") or [])
    info = threads.get("pageInfo") or {}
    while info.get("hasNextPage") and info.get("endCursor") and len(seen) < MOST_THREADS:
        data = github.graphql(THREADS_QUERY, {"owner": owner, "name": name, "number": int(node["number"]),
                                              "cursor": info["endCursor"]})
        more = (((data.get("repository") or {}).get("pullRequest") or {}).get("reviewThreads")) or {}
        got = more.get("nodes") or []
        if not got:
            break
        seen += got
        info = more.get("pageInfo") or {}
    if len(seen) > len(threads.get("nodes") or []):
        node["reviewThreads"] = {**threads, "nodes": seen}


def fetch_pulls(github: GitHub, repo: str, now: datetime | None = None,
                long_lived: list[str] | None = None) -> RepoPulls:
    """Every open pull request of one repository (up to MOST), in stack order, where no
    pull request from one of the `long_lived` branches is a stack (see `stack`). Raises
    GitHubError."""
    owner, name = repo.split("/", 1)
    pulls: list[PullRequest] = []
    cursor, total, default_branch, spelled, tip, viewer, permission = None, 0, "", repo, "", "", ""
    cost = limit = 0
    org = False
    while True:
        data = github.graphql(QUERY, {"owner": owner, "name": name, "page": PAGE, "cursor": cursor})
        viewer = (data.get("viewer") or {}).get("login") or viewer
        spent = data.get("rateLimit") or {}
        cost += spent.get("cost") if isinstance(spent.get("cost"), int) else 0
        limit = spent.get("limit") if isinstance(spent.get("limit"), int) else limit
        repository = data.get("repository") or {}
        spelled = repository.get("nameWithOwner") or spelled
        permission = repository.get("viewerPermission") or permission
        org = org or (repository.get("owner") or {}).get("__typename") == "Organization"
        branch =repository.get("defaultBranchRef") or {}
        default_branch = branch.get("name") or default_branch
        tip = (branch.get("target") or {}).get("oid") or tip
        page = repository.get("pullRequests") or {}
        total = int(page.get("totalCount") or 0)
        nodes = [node for node in page.get("nodes") or [] if node]
        for node in nodes:
            read_every_thread(github, owner, name, node)
        pulls += [parse_pull(spelled, node) for node in nodes]
        info = page.get("pageInfo") or {}
        cursor = info.get("endCursor")
        if not info.get("hasNextPage") or not cursor or len(pulls) >= MOST:
            break
    look_into_failures(github, spelled, pulls)
    if viewer and permission in _RIGHTS:                         # for the viewer's own, GitHub says exactly
        for pull in pulls:
            if pull.author.lower() == viewer.lower():
                pull.can_ask, pull.can_merge = _RIGHTS[permission]
    access = permission.lower().replace("_", " ")
    log.info("fetched %d of %d open pull requests of %s (your access: %s; it cost %d of %d points)",
             len(pulls), total, spelled, access or "not said", cost, limit)
    return RepoPulls(spelled, default_branch, stack(pulls, default_branch, long_lived), max(total, len(pulls)),
                     now or datetime.now(timezone.utc), tip, access, cost, limit, org)


def span(seconds: float, fine: bool = False) -> str:
    """A stretch of time, shortly: "40m", "3h", "9d", "5w". Under a minute it is "now", or,
    `fine`, the seconds: "12s". Every "how long ago" the app says among words is said this
    way: in the ticker, the drawer and what needs effort. The list's age column is not:
    its figure is in days and hours (picture.age_said)."""
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s" if fine else "now"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    if seconds < 14 * 86400:
        return f"{seconds // 86400}d"
    return f"{seconds // (7 * 86400)}w"


def ago(then: datetime | None, now: datetime | None = None) -> str:
    """How long since: "now", "40m", "3h", "9d", "5w"."""
    if then is None:
        return ""
    return span(((now or datetime.now(timezone.utc)) - then).total_seconds())
