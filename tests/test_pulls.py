# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""Pull requests are read from GitHub's reply as it is documented, and stacked by branch."""
import itertools
from datetime import datetime, timedelta, timezone

import pytest

from githubapi import GitHubError
from picture import Picture, mark
from pulls import (PAGE, QUERY, THREADS_QUERY, RepoPulls, ago, fetch_pulls, is_team, judge, known_as, parse_pull,
                   span, stack, when)


def node(number, head, base="main", **over):
    """One pull request, the way GitHub's GraphQL reply spells it."""
    made = {
        "number": number, "title": f"Change {number}", "url": f"https://github.com/o/r/pull/{number}",
        "isDraft": False, "createdAt": "2026-11-10T09:00:00Z", "updatedAt": "2026-11-14T15:00:00Z",
        "author": {"login": "alice"},
        "baseRefName": base, "baseRefOid": "a" * 40, "headRefName": head, "headRefOid": "b" * 40,
        "isCrossRepository": False, "headRepositoryOwner": {"login": "o"},
        "mergeable": "MERGEABLE", "reviewDecision": None,
        "reviewRequests": {"nodes": []},
        "latestOpinionatedReviews": {"nodes": []}, "latestReviews": {"nodes": []},
        "reviewThreads": {"totalCount": 0, "nodes": []},
        "commits": {"nodes": [{"commit": {"committedDate": "2026-11-14T14:00:00Z",
                                           "statusCheckRollup": {"state": "SUCCESS"}}}]},
    }
    made.update(over)
    return made


def review(who, state, at, id=None):
    made = {"author": {"login": who}, "state": state, "submittedAt": at}
    return {**made, "id": id} if id else made


def thread(resolved, by=None, under=None, review=None):
    """One review thread: whether it is resolved, who wrote its first comment (`by`), and
    the review that comment belongs to: whose it is (`under`) and its id (`review`)."""
    inside = {"id": review, "author": {"login": under} if under else None} if under or review else None
    first = {"author": {"login": by} if by else None, "pullRequestReview": inside}
    return {"isResolved": resolved, "comments": {"nodes": [first] if by or under or review else []}}


def test_the_plain_facts_are_read_as_they_stand():
    pr = parse_pull("o/r", node(17, "feature", base="main", isDraft=True, mergeable="CONFLICTING",
                                reviewDecision="CHANGES_REQUESTED", baseRefOid="1234567890" + "c" * 30,
                                isCrossRepository=True, headRepositoryOwner={"login": "carol"}))
    assert (pr.repo, pr.number, pr.title, pr.author) == ("o/r", 17, "Change 17", "alice")
    assert (pr.head, pr.base, pr.short_sha) == ("feature", "main", "1234567")
    assert (pr.draft, pr.mergeable, pr.decision, pr.fork, pr.head_owner) == (True, "conflict", "changes", True, "carol")
    assert pr.updated == datetime(2026, 11, 14, 15, 0, tzinfo=timezone.utc)
    assert pr.checks == "pass" and pr.url.endswith("/pull/17")


def test_each_reviewers_latest_word():
    pr = parse_pull("o/r", node(
        17, "feature",
        latestOpinionatedReviews={"nodes": [review("bob", "APPROVED", "2026-11-12T10:00:00Z"),
                                            review("dave", "CHANGES_REQUESTED", "2026-11-13T10:00:00Z")]},
        latestReviews={"nodes": [review("bob", "COMMENTED", "2026-11-13T12:00:00Z"),      # after approving
                                 review("carol", "COMMENTED", "2026-11-11T10:00:00Z"),     # only ever commented
                                 review("alice", "COMMENTED", "2026-11-13T13:00:00Z"),     # the author, replying
                                 review("erin", "PENDING", None),                          # still being written
                                 review("dave", "CHANGES_REQUESTED", "2026-11-13T10:00:00Z")]}))
    assert [(r.who, r.state) for r in pr.reviews] == [
        ("carol", "commented"), ("bob", "approved"), ("dave", "changes")]                  # oldest first


def test_who_was_asked_and_has_not_answered():
    pr = parse_pull("o/r", node(17, "feature", reviewRequests={"nodes": [
        {"requestedReviewer": {"__typename": "User", "login": "bob"}},
        {"requestedReviewer": {"__typename": "Team", "slug": "backend"}},
        {"requestedReviewer": {"__typename": "Bot", "login": "copilot-pull-request-reviewer"}},
        {"requestedReviewer": {"__typename": "Mannequin", "login": "imported-ann"}},
        {"requestedReviewer": {"__typename": "Robot"}},              # a kind GitHub may add one day
        {"requestedReviewer": None},                                 # one GitHub does not name: kept,
        {"requestedReviewer": None}]}))                              # and said once
    assert pr.asked == ["bob", "team/o/backend", "copilot-pull-request-reviewer", "imported-ann", "robot",
                        "a hidden reviewer"]
    assert [is_team(who) for who in pr.asked] == [False, True, False, False, False, False]
    assert known_as(pr.asked[2]) == "copilot"                       # how it reads, as when it has reviewed


def test_the_query_asks_the_login_of_every_kind_of_reviewer_that_has_one():
    flat = " ".join(QUERY.split())
    for kind in ("User", "Bot", "Mannequin"):
        assert f"... on {kind} {{ login }}" in flat
    assert "... on Team { slug }" in flat


def test_a_pull_requests_page_is_built_from_its_repository_and_number():
    """Not taken from the address in GitHub's reply, which is free text: what is opened in
    the browser has to be a page on github.com whatever that says."""
    pr = parse_pull("Acme/web-console", node(143, "dark", url="https://evil.example/login"))
    assert pr.page == "https://github.com/Acme/web-console/pull/143"
    for name in ("acme/web console", "acme/../x", "acme/web/console", "acme", "acme/web-console\n",
                 "https://evil.example/a/b"):
        assert parse_pull(name, node(1, "a")).page == "", name      # not a plain owner/name: no page


def test_each_reviewer_carries_the_threads_they_began_and_how_many_are_still_to_resolve():
    bot = "copilot-pull-request-reviewer"
    pr = parse_pull("o/r", node(17, "feature",
        latestReviews={"nodes": [review(bot, "COMMENTED", "2026-11-11T10:00:00Z"),
                                 review("carol", "COMMENTED", "2026-11-12T10:00:00Z"),
                                 review("dave", "COMMENTED", "2026-11-13T10:00:00Z")]},
        latestOpinionatedReviews={"nodes": [review("bob", "APPROVED", "2026-11-14T10:00:00Z")]},
        reviewThreads={"totalCount": 8, "nodes": [
            thread(True, by="Copilot", under=bot),          # the review's author counts, however the
            thread(True, by="Copilot", under=bot),          # comment inside it is signed
            thread(False, by="Copilot"),                    # no review named: the bot's other name is known
            thread(False, by="carol", under="carol"),
            thread(True, by="Carol", under="Carol"),        # capitals do not matter
            thread(False, by="bob"),                        # no review named: the comment's author
            thread(False, by="alice", under="alice"),       # the author's own thread is nobody's review
            thread(False)]}))                               # GitHub did not say who began it
    assert [(r.who, r.state, r.open, r.threads) for r in pr.reviews] == [
        (bot, "commented", 1, 3), ("carol", "commented", 1, 2), ("dave", "commented", 0, 0),
        ("bob", "approved", 1, 1)]
    assert (pr.comments_open, pr.comments_total) == (5, 8)   # the totals count every thread


def trail_of(who, reviews, threads=(), latest=None, opinions=None):
    """The trail `who` ends up with, as (state, threads, open) per step."""
    last = {}
    for item in reviews:
        last[item["author"]["login"]] = item
    held = {}
    for item in reviews:
        if item["state"] in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
            held[item["author"]["login"]] = item
    pr = parse_pull("o/r", node(17, "feature", reviews={"nodes": list(reviews)},
                                latestReviews={"nodes": list(last.values())},
                                latestOpinionatedReviews={"nodes": list(held.values())},
                                reviewThreads={"totalCount": len(threads), "nodes": list(threads)}))
    found = next(r for r in pr.reviews if r.who == who)
    return found.state, [(s.state, s.threads, s.open) for s in found.trail]


def totals_of(who, reviews, threads=()):
    """The threads `who` began and how many are still to resolve, over all their reviews."""
    pr = parse_pull("o/r", node(17, "feature", reviews={"nodes": list(reviews)},
                                latestReviews={"nodes": [list(reviews)[-1]]},
                                reviewThreads={"totalCount": len(threads), "nodes": list(threads)}))
    found = next(r for r in pr.reviews if r.who == who)
    return found.threads, found.open, pr.comments_open


def test_reviews_folded_into_one_step_are_counted_once():
    """Two comment-only reviews in a row are one step of the trail; the reviewer's totals
    still count each thread once, and agree with the pull request's own count."""
    at = "2026-11-1{}T10:00:00Z".format
    said = [review("bob", "COMMENTED", at(1), "A"), review("bob", "COMMENTED", at(2), "B")]
    threads = [thread(False, review="A"), thread(True, review="B"), thread(False, review="B")]
    assert trail_of("bob", said, threads) == ("commented", [("commented", 3, 2)])
    assert totals_of("bob", said, threads) == (3, 2, 2)


def test_a_reviewers_trail_is_what_they_said_along_the_way():
    bot = "copilot-pull-request-reviewer"
    said = [review(bot, "COMMENTED", "2026-11-10T10:00:00Z", "R1"),
            review("rafael", "CHANGES_REQUESTED", "2026-11-11T10:00:00Z", "R2"),
            review("alice", "COMMENTED", "2026-11-11T12:00:00Z", "R3"),       # the author answering: not a review
            review("rafael", "COMMENTED", "2026-11-11T13:00:00Z", "R4"),      # a reply inside a thread
            review("rafael", "APPROVED", "2026-11-12T10:00:00Z", "R5"),
            review("rafael", "PENDING", None, "R6")]                          # still being written
    threads = [thread(True, by="Copilot", review="R1")] * 7 + [thread(True, by="rafael", review="R2")]
    assert trail_of("rafael", said, threads) == ("approved", [("changes", 1, 0), ("approved", 0, 0)])
    assert trail_of(bot, said, threads) == ("commented", [("commented", 7, 0)])


def test_the_rules_of_a_trail():
    at = "2026-11-1{}T10:00:00Z".format
    # Each round of requested changes is a step of its own.
    assert trail_of("bob", [review("bob", "CHANGES_REQUESTED", at(1), "A"), review("bob", "CHANGES_REQUESTED", at(2), "B")],
                    [thread(True, review="A")]) == ("changes", [("changes", 1, 0), ("changes", 0, 0)])
    # Comment-only reviews in a row are one step, and so are approvals in a row.
    assert trail_of("bob", [review("bob", "COMMENTED", at(1), "A"), review("bob", "COMMENTED", at(2), "B")],
                    [thread(True, review="A"), thread(False, review="B")]) == ("commented", [("commented", 2, 1)])
    assert totals_of("bob", [review("bob", "COMMENTED", at(1), "A"), review("bob", "COMMENTED", at(2), "B")],
                     [thread(True, review="A"), thread(False, review="B")]) == (2, 1, 1)
    assert trail_of("bob", [review("bob", "APPROVED", at(1), "A"), review("bob", "COMMENTED", at(2), "B"),
                            review("bob", "APPROVED", at(3), "C")]) == ("approved", [("approved", 0, 0)])
    assert totals_of("bob", [review("bob", "APPROVED", at(1), "A"), review("bob", "APPROVED", at(3), "C")],
                     [thread(True, review="A"), thread(False, review="C")]) == (2, 1, 1)
    # A comment after approving shows when it began a thread, and not when it was only a reply.
    assert trail_of("bob", [review("bob", "APPROVED", at(1), "A"), review("bob", "COMMENTED", at(2), "B")],
                    [thread(False, review="B")]) == ("approved", [("approved", 0, 0), ("commented", 1, 1)])
    assert trail_of("bob", [review("bob", "APPROVED", at(1), "A"), review("bob", "COMMENTED", at(2), "B")]) == (
        "approved", [("approved", 0, 0)])
    # Someone who only ever replied still has their one word.
    assert trail_of("bob", [review("bob", "COMMENTED", at(1), "A"), review("bob", "COMMENTED", at(2), "B")]) == (
        "commented", [("commented", 0, 0)])
    # GitHub lists reviews oldest first, and the trail does not depend on it.
    assert trail_of("bob", [review("bob", "APPROVED", at(2), "B"), review("bob", "CHANGES_REQUESTED", at(1), "A")],
                    [thread(True, review="A")])[1] == [("changes", 1, 0), ("approved", 0, 0)]
    # A thread whose review is not named goes to that reviewer's latest review that is not an approval.
    assert trail_of("bob", [review("bob", "CHANGES_REQUESTED", at(1), "A"), review("bob", "APPROVED", at(2), "B")],
                    [thread(False, by="bob")])[1] == [("changes", 1, 1), ("approved", 0, 0)]


def test_without_the_list_of_reviews_the_trail_is_the_latest_word():
    pr = parse_pull("o/r", node(17, "feature",
                                latestOpinionatedReviews={"nodes": [review("bob", "CHANGES_REQUESTED", "2026-11-12T10:00:00Z")]},
                                reviewThreads={"totalCount": 2, "nodes": [thread(True, under="bob"), thread(False, under="bob")]}))
    bob = pr.reviews[0]
    assert [(s.state, s.threads, s.open) for s in bob.trail] == [("changes", 2, 1)] and (bob.threads, bob.open) == (2, 1)


def test_comments_to_resolve_are_the_unresolved_threads():
    pr = parse_pull("o/r", node(17, "feature", reviewThreads={"totalCount": 5, "nodes": [
        {"isResolved": True}, {"isResolved": False}, {"isResolved": False}, {"isResolved": True},
        {"isResolved": True}]}))
    assert (pr.comments_open, pr.comments_total) == (2, 5)


@pytest.mark.parametrize("rollup, checks", [
    ({"state": "SUCCESS"}, "pass"), ({"state": "FAILURE"}, "fail"), ({"state": "ERROR"}, "fail"),
    ({"state": "PENDING"}, "running"), ({"state": "EXPECTED"}, "running"), (None, "none")])
def test_checks_are_the_last_commits_rollup(rollup, checks):
    commits = {"nodes": [{"commit": {"committedDate": "2026-11-14T14:00:00Z", "statusCheckRollup": rollup}}]}
    assert parse_pull("o/r", node(17, "feature", commits=commits)).checks == checks


def test_missing_pieces_do_not_break_the_reading():
    bare = {"number": 9, "author": None, "commits": {"nodes": []}, "mergeable": "UNKNOWN"}
    pr = parse_pull("o/r", bare)
    assert (pr.author, pr.checks, pr.mergeable, pr.decision, pr.asked, pr.reviews) == (
        "ghost", "none", "unknown", "", [], [])
    assert pr.updated is None and pr.comments_open == 0


def test_a_stack_is_a_pull_request_based_on_anothers_head_branch():
    pulls = [parse_pull("o/r", n) for n in (
        node(12, "client"),                                   # on main
        node(13, "review-state", base="client"),              # on 12
        node(15, "inference", base="review-state"),           # on 13
        node(16, "icons", base="review-state"),               # also on 13
        node(24, "token-fix", base="release-0.2"),            # on another branch
        node(9, "jira"),                                      # on main
        node(30, "from-a-fork", base="main", isCrossRepository=True),
        node(31, "on-the-forks-branch", base="from-a-fork"))]  # a fork's branch is not ours: no parent
    ordered = stack(pulls, "main")
    assert [(p.number, p.parent, p.depth) for p in ordered] == [
        (9, 0, 0), (12, 0, 0), (13, 12, 1), (15, 13, 2), (16, 13, 2), (30, 0, 0),
        (31, 0, 0), (24, 0, 0)]                               # main's first, then the other branches
    assert next(p for p in ordered if p.number == 13).children == [15, 16]


def test_branches_that_point_at_each_other_are_not_a_stack():
    ordered = stack([parse_pull("o/r", node(1, "a", base="b")), parse_pull("o/r", node(2, "b", base="a"))], "main")
    assert sorted(p.number for p in ordered) == [1, 2]        # both are still listed, once each
    assert not all(p.parent for p in ordered)                 # and the loop was cut


def test_a_loop_is_cut_inside_itself_the_same_way_in_any_order():
    """A pull request built on one in a loop keeps its parent; the loop is cut at its lowest
    number, whatever order GitHub lists them in."""
    nodes = [node(1, "a", base="b"), node(2, "b", base="a"),    # 1 and 2 loop
             node(3, "c", base="a")]                            # 3 is built on 1
    for order in itertools.permutations(nodes):
        ordered = stack([parse_pull("o/r", n) for n in order], "main")
        assert [(p.number, p.parent, p.depth) for p in ordered] == [
            (1, 0, 0), (2, 1, 1), (3, 1, 1)], [n["number"] for n in order]


def git_flow():
    return [parse_pull("o/r", n) for n in (
        node(50, "develop", base="main"),                     # the release
        node(51, "login-form", base="develop"),
        node(52, "icons", base="develop"),
        node(53, "login-tests", base="login-form"),           # a real stack, on 51
        node(54, "main", base="develop"))]                    # main merged back into develop


def test_a_pull_request_from_a_long_lived_branch_carries_nothing():
    """The release pull request from develop into main is not a stack under which every
    pull request into develop waits; a real stack beside it still is one."""
    ordered = stack(git_flow(), "main", ["main", "develop"])
    assert [(p.number, p.parent, p.depth) for p in ordered] == [
        (50, 0, 0), (51, 0, 0), (53, 51, 1), (52, 0, 0), (54, 0, 0)]


def test_until_the_repository_has_its_list_the_default_branch_is_the_long_lived_one():
    ordered = {p.number: p.parent for p in stack(git_flow(), "main")}
    assert ordered[51] == 50 and ordered[53] == 51            # develop is not known to be long-lived yet
    onto_main = [parse_pull("o/r", n) for n in (node(60, "main", base="release-0.2"), node(61, "hotfix"))]
    assert [p.parent for p in stack(onto_main, "main")] == [0, 0]     # but main, the default branch, is
    assert {p.number: p.parent for p in stack(onto_main, "main", [])} == {60: 0, 61: 60}   # unless the
                                                              # user emptied the list: then nothing is


class FakeGitHub:
    def __init__(self, pages):
        self.pages, self.asked = pages, []

    def graphql(self, query, variables):
        self.asked.append((query, dict(variables)))
        return self.pages[len(self.asked) - 1]


def page(nodes, total, more=False, cursor=None):
    return {"repository": {"nameWithOwner": "Owner/Repo",
                           "defaultBranchRef": {"name": "trunk", "target": {"oid": "f" * 40}},
                           "pullRequests": {"totalCount": total, "nodes": nodes,
                                            "pageInfo": {"hasNextPage": more, "endCursor": cursor}}}}


def test_fetch_reads_every_page_and_keeps_githubs_spelling():
    fake = FakeGitHub([page([node(2, "b", base="trunk"), node(1, "a", base="b")], 3, more=True, cursor="CUR"),
                       page([node(3, "c", base="trunk")], 3)])
    now = datetime(2026, 11, 14, 16, 0, tzinfo=timezone.utc)
    got = fetch_pulls(fake, "owner/repo", now)
    assert (got.repo, got.default_branch, got.total, got.at) == ("Owner/Repo", "trunk", 3, now)
    assert got.tip == "f" * 40                                  # where the default branch points now
    assert [(p.number, p.parent, p.repo) for p in got.pulls] == [(2, 0, "Owner/Repo"), (1, 2, "Owner/Repo"),
                                                                 (3, 0, "Owner/Repo")]
    assert [v for _, v in fake.asked] == [
        {"owner": "owner", "name": "repo", "page": PAGE, "cursor": None},
        {"owner": "owner", "name": "repo", "page": PAGE, "cursor": "CUR"}]
    assert fake.asked[0][0] is QUERY


def test_fetch_stacks_with_the_repositorys_long_lived_branches():
    nodes = [node(2, "develop", base="trunk"), node(1, "a", base="develop")]
    assert [p.parent for p in fetch_pulls(FakeGitHub([page(nodes, 2)]), "owner/repo").pulls] == [0, 2]
    got = fetch_pulls(FakeGitHub([page(nodes, 2)]), "owner/repo", long_lived=["trunk", "develop"])
    assert [(p.number, p.parent) for p in got.pulls] == [(2, 0), (1, 0)]


def test_fetch_says_what_reading_cost_of_the_hourly_allowance():
    assert "rateLimit { cost limit }" in QUERY
    first = page([node(2, "b", base="trunk")], 2, more=True, cursor="CUR")
    second = page([node(3, "c", base="trunk")], 2)
    first["rateLimit"], second["rateLimit"] = {"cost": 53, "limit": 5000}, {"cost": 20, "limit": 5000}
    got = fetch_pulls(FakeGitHub([first, second]), "owner/repo")
    assert (got.cost, got.limit) == (73, 5000)                      # every page counts
    unsaid = fetch_pulls(FakeGitHub([page([], 0)]), "owner/repo")
    assert (unsaid.cost, unsaid.limit) == (0, 0)                     # GitHub did not say


def threads_page(threads, more=False, cursor=None):
    """GitHub's reply to THREADS_QUERY."""
    return {"repository": {"pullRequest": {"reviewThreads": {
        "nodes": threads, "pageInfo": {"hasNextPage": more, "endCursor": cursor}}}}}


def long_review(total, first, more=True):
    """An approved, mergeable pull request whose checks pass, with `total` review threads
    of which GitHub sent `first` with it."""
    return node(7, "long", base="trunk", reviewDecision="APPROVED",
                latestOpinionatedReviews={"nodes": [review("bob", "APPROVED", "2026-11-14T10:00:00Z")]},
                commits={"nodes": [{"commit": {"statusCheckRollup": {"state": "SUCCESS"}}}]},
                reviewThreads={"totalCount": total, "nodes": first,
                               "pageInfo": {"hasNextPage": more, "endCursor": f"T{len(first)}"}})


def test_a_long_review_is_read_to_its_last_thread():
    """GitHub sends the first 100 review threads with each pull request. The rest are asked
    for, so an open one among them is counted and the pull request is not ready to merge."""
    rest = [thread(True, under="bob") for _ in range(48)] + [thread(False, under="bob") for _ in range(2)]
    fake = FakeGitHub([page([long_review(150, [thread(True, under="bob") for _ in range(100)])], 1),
                       threads_page(rest)])
    got = fetch_pulls(fake, "owner/repo")
    (pr,) = got.pulls
    assert (pr.comments_open, pr.comments_total) == (2, 150)
    assert (pr.reviews[0].threads, pr.reviews[0].open) == (150, 2)      # bob's count, too
    assert fake.asked[1] == (THREADS_QUERY, {"owner": "owner", "name": "repo", "number": 7, "cursor": "T100"})
    assert mark(Picture(["Owner/Repo"], {"owner/repo": got}, "alice", {}, {}), pr)[2] == "approved"
    assert len(fake.asked) == 2                                         # and then no more was asked


def test_threads_beyond_the_most_read_count_as_open(monkeypatch):
    monkeypatch.setattr("pulls.MOST_THREADS", 200)
    fake = FakeGitHub([page([long_review(250, [thread(True) for _ in range(100)])], 1),
                       threads_page([thread(True) for _ in range(100)], more=True, cursor="T200")])
    (pr,) = fetch_pulls(fake, "owner/repo").pulls
    assert len(fake.asked) == 2                                         # stopped at 200
    assert (pr.comments_open, pr.comments_total) == (50, 250)           # 50 never read: not known resolved


def test_a_reply_with_no_more_threads_ends_the_reading():
    fake = FakeGitHub([page([long_review(120, [thread(True) for _ in range(100)])], 1),
                       threads_page([], more=True, cursor="T100")])     # says there is more, sends none
    (pr,) = fetch_pulls(fake, "owner/repo").pulls
    assert len(fake.asked) == 2 and (pr.comments_open, pr.comments_total) == (20, 120)


@pytest.mark.parametrize("association, inside", [
    ("OWNER", True), ("MEMBER", True), ("COLLABORATOR", True), (None, True),
    ("CONTRIBUTOR", False), ("FIRST_TIME_CONTRIBUTOR", False), ("FIRST_TIMER", False), ("NONE", False)])
def test_an_author_from_outside_the_repository_has_no_rights_in_it(association, inside):
    pr = parse_pull("o/r", node(17, "feature", authorAssociation=association))
    assert (pr.can_ask, pr.can_merge) == (inside, inside)


@pytest.mark.parametrize("permission, rights, access", [
    ("READ", (False, False), "read"), ("TRIAGE", (True, False), "triage"), ("WRITE", (True, True), "write"),
    ("MAINTAIN", (True, True), "maintain"), ("ADMIN", (True, True), "admin"),
    (None, (False, False), "")])                                # not said: what the tie says stands
def test_for_the_viewers_own_pull_requests_github_says_exactly_what_they_may_do(permission, rights, access):
    reply = page([node(1, "a", base="trunk", author={"login": "Alice"}, authorAssociation="CONTRIBUTOR"),
                  node(2, "b", base="trunk", author={"login": "bob"}, authorAssociation="MEMBER")], 2)
    reply["viewer"] = {"login": "alice"}
    reply["repository"]["viewerPermission"] = permission
    got = fetch_pulls(FakeGitHub([reply]), "owner/repo")
    mine, theirs = got.pulls
    assert (mine.can_ask, mine.can_merge) == rights
    assert (theirs.can_ask, theirs.can_merge) == (True, True)   # the permission is the viewer's alone
    assert got.access == access


@pytest.mark.parametrize("access, rights", [
    ("admin", (True, True)), ("maintain", (True, True)), ("write", (True, True)), ("triage", (True, False)),
    ("read", (False, False)), ("", (False, False))])
def test_the_viewers_rights_in_a_repository_follow_their_access(access, rights):
    got = RepoPulls("o/r", "main", [], 0, datetime(2026, 11, 14, tzinfo=timezone.utc), access=access)
    assert got.rights == rights


@pytest.mark.parametrize("owner, org", [
    ({"__typename": "Organization"}, True), ({"__typename": "User"}, False), (None, False)])
def test_fetch_says_whether_an_organization_owns_the_repository(owner, org):
    assert "owner { __typename }" in QUERY
    reply = page([], 0)
    if owner is not None:
        reply["repository"]["owner"] = owner
    assert fetch_pulls(FakeGitHub([reply]), "owner/repo").org is org


def test_the_query_asks_who_is_looking_and_what_they_and_each_author_may_do():
    assert "viewer { login }" in QUERY and "viewerPermission" in QUERY and "authorAssociation" in QUERY
    assert "pullRequestReview { id author { login } }" in QUERY  # the review each thread was begun in
    assert "reviews(last: 100)" in QUERY                         # every review, for each reviewer's trail


NOT_PAID = ("The job was not started because recent account payments have failed or your spending limit "
            "needs to be increased. Please check the 'Billing & plans' section in your settings")
BROKE = "Process completed with exit code 1."


def run(conclusion, *notes, status="COMPLETED"):
    """One check run, the way GitHub's reply spells it, with the notes attached to it."""
    return {"__typename": "CheckRun", "name": "tests", "status": status, "conclusion": conclusion,
            "title": None, "summary": None, "annotations": {"nodes": [{"message": note} for note in notes]}}


def test_failing_checks_are_unpaid_only_when_every_failure_is_a_job_github_would_not_start():
    assert judge([run("FAILURE", NOT_PAID)] * 3) == ("unpaid", 3, 3, "")
    assert judge([run("FAILURE", NOT_PAID), run("SUCCESS"), run("SKIPPED")])[0] == "unpaid"
    assert judge([run("FAILURE", NOT_PAID), run("FAILURE", BROKE)]) == ("fail", 2, 1, BROKE)    # one real failure
    assert judge([run("FAILURE", NOT_PAID), run(None, status="IN_PROGRESS")])[0] == "running"   # others still run
    status = {"__typename": "StatusContext", "context": "ci/build", "state": "FAILURE", "description": "build broke"}
    assert judge([run("FAILURE", NOT_PAID), status]) == ("fail", 2, 1, "build broke")
    assert judge([run("FAILURE", "the billing module's test failed")])[0] == "fail"             # the word is not enough
    assert judge([])[0] == "fail" and judge([run("FAILURE")])[0] == "fail"                      # nothing to go by


def why(**pulls):
    """GitHub's reply to the second query: per pull request, the checks of its last commit."""
    return {"repository": {alias: {"commits": {"nodes": [{"commit": {"statusCheckRollup": {"contexts": {
        "nodes": contexts}}}}]}} for alias, contexts in pulls.items()}}


FAILED = {"nodes": [{"commit": {"statusCheckRollup": {"state": "FAILURE"}}}]}


def test_fetch_asks_why_the_checks_fail_and_only_for_the_pull_requests_where_they_do():
    fake = FakeGitHub([page([node(1, "a", base="trunk", commits=FAILED), node(2, "b", base="trunk"),
                             node(3, "c", base="trunk", commits=FAILED)], 3),
                       why(p1=[run("FAILURE", NOT_PAID)] * 3, p3=[run("FAILURE", NOT_PAID), run("FAILURE", BROKE)])])
    got = fetch_pulls(fake, "owner/repo")
    assert {p.number: p.checks for p in got.pulls} == {1: "unpaid", 2: "pass", 3: "fail"}
    asked, variables = fake.asked[1]
    assert "p1: pullRequest(number: 1)" in asked and "p3: pullRequest(number: 3)" in asked and "p2:" not in asked
    assert variables == {"owner": "Owner", "name": "Repo"}
    assert len(FakeGitHub([page([node(2, "b", base="trunk")], 1)]).asked) == 0     # nothing fails: no second query


def test_when_github_will_not_say_why_the_checks_stay_failing():
    class Refuses(FakeGitHub):
        def graphql(self, query, variables):
            if self.asked:
                raise GitHubError(200, "x", "GitHub refused the query")
            return super().graphql(query, variables)

    got = fetch_pulls(Refuses([page([node(1, "a", base="trunk", commits=FAILED)], 1)]), "owner/repo")
    assert [p.checks for p in got.pulls] == ["fail"]


def test_a_repository_with_nothing_open():
    got = fetch_pulls(FakeGitHub([page([], 0)]), "owner/repo")
    assert got.pulls == [] and got.total == 0


@pytest.mark.parametrize("seconds, plain, fine", [
    (-5, "now", "0s"), (0, "now", "0s"), (12.7, "now", "12s"), (59, "now", "59s"), (60, "1m", "1m"),
    (40 * 60, "40m", "40m"), (3 * 3600, "3h", "3h"), (72 * 3600, "3d", "3d"), (9 * 86400, "9d", "9d"),
    (35 * 86400, "5w", "5w")])
def test_every_how_long_is_said_one_way(seconds, plain, fine):
    assert (span(seconds), span(seconds, fine=True)) == (plain, fine)


def test_when_and_ago():
    now = datetime(2026, 11, 14, 16, 0, tzinfo=timezone.utc)
    assert when("2026-11-14T16:00:00Z") == now and when(None) is None and when("nonsense") is None
    for back, said in ((timedelta(seconds=20), "now"), (timedelta(minutes=40), "40m"), (timedelta(hours=3), "3h"),
                       (timedelta(days=9), "9d"), (timedelta(days=35), "5w")):
        assert ago(now - back, now) == said
    assert ago(None, now) == ""
