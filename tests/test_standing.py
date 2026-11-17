# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""Who must act next: the rule, line by line, and its order."""
import pytest

from pulls import parse_pull, stack
from standing import settle, standing
from test_pulls import node, review

ASKED_BOB = {"nodes": [{"requestedReviewer": {"__typename": "User", "login": "bob"}}]}
ASKED_TEAM = {"nodes": [{"requestedReviewer": {"__typename": "Team", "slug": "backend"}}]}
BOB_APPROVED = {"nodes": [review("bob", "APPROVED", "2026-11-12T10:00:00Z")]}
BOB_CHANGES = {"nodes": [review("bob", "CHANGES_REQUESTED", "2026-11-12T10:00:00Z")]}
BOB_COMMENTED = {"nodes": [review("bob", "COMMENTED", "2026-11-12T10:00:00Z")]}
PASSING = {"nodes": [{"commit": {"statusCheckRollup": {"state": "SUCCESS"}}}]}
FAILING = {"nodes": [{"commit": {"statusCheckRollup": {"state": "FAILURE"}}}]}
RUNNING = {"nodes": [{"commit": {"statusCheckRollup": {"state": "PENDING"}}}]}
TWO_OPEN = {"totalCount": 3, "nodes": [{"isResolved": False}, {"isResolved": False}, {"isResolved": True}]}
ONE_OPEN = {"totalCount": 1, "nodes": [{"isResolved": False}]}


def stands(**facts):
    s = standing(parse_pull("o/r", node(17, "feature", **facts)))
    return s.who, s.what, s.by


@pytest.mark.parametrize("facts, expected", [
    (dict(isDraft=True), (["alice"], "finish the draft", "author")),
    (dict(mergeable="CONFLICTING"), (["alice"], "resolve the conflict", "author")),
    (dict(commits=FAILING), (["alice"], "fix the checks", "author")),
    (dict(commits=RUNNING), ([], "checks running", "nobody")),
    (dict(reviewRequests=ASKED_BOB), (["bob"], "review", "reviewers")),
    (dict(reviewRequests=ASKED_TEAM), (["team/o/backend"], "review", "reviewers")),
    (dict(reviewRequests={"nodes": [{"requestedReviewer": None}]}),          # asked of someone GitHub does not
     (["a hidden reviewer"], "review", "reviewers")),                        # name: not "request review"
    (dict(reviewRequests=ASKED_BOB, latestOpinionatedReviews=BOB_CHANGES, reviewDecision="CHANGES_REQUESTED"),
     (["bob"], "re-review", "reviewers")),                       # asked again after their first review
    (dict(reviewDecision="CHANGES_REQUESTED", latestOpinionatedReviews=BOB_CHANGES, reviewThreads=TWO_OPEN),
     (["alice"], "resolve 2 comments", "author")),
    (dict(latestReviews=BOB_COMMENTED, reviewThreads=ONE_OPEN), (["alice"], "resolve 1 comment", "author")),
    (dict(reviewDecision="CHANGES_REQUESTED", latestOpinionatedReviews=BOB_CHANGES),
     (["alice"], "address the requested changes", "author")),
    (dict(reviewDecision="APPROVED", latestOpinionatedReviews=BOB_APPROVED), (["alice"], "merge", "author")),
    (dict(latestOpinionatedReviews=BOB_APPROVED), (["alice"], "merge", "author")),      # no review rule on the branch
    (dict(latestReviews=BOB_COMMENTED), (["alice"], "merge", "author")),                 # looked, nothing left open
    (dict(), (["alice"], "request review", "author")),
    (dict(reviewDecision="REVIEW_REQUIRED"), (["alice"], "request review", "author")),
    (dict(reviewDecision="REVIEW_REQUIRED", latestOpinionatedReviews=BOB_APPROVED),
     (["alice"], "request review", "author")),                   # one approval, and the branch wants more
    (dict(authorAssociation="MEMBER"), (["alice"], "request review", "author")),
    # An author with no rights in the repository (a pull request from a fork): GitHub offers
    # them no reviewers and no merge button, so those two moves fall to the maintainer, "o".
    (dict(authorAssociation="CONTRIBUTOR"), (["o"], "review", "maintainer")),
    (dict(authorAssociation="FIRST_TIME_CONTRIBUTOR", reviewDecision="REVIEW_REQUIRED"),
     (["o"], "review", "maintainer")),
    (dict(authorAssociation="NONE", reviewDecision="APPROVED", latestOpinionatedReviews=BOB_APPROVED),
     (["o"], "merge", "maintainer")),
    (dict(authorAssociation="CONTRIBUTOR", latestReviews=BOB_COMMENTED), (["o"], "merge", "maintainer")),
    # Everything else is still theirs to do, or the asked reviewers'.
    (dict(authorAssociation="CONTRIBUTOR", isDraft=True), (["alice"], "finish the draft", "author")),
    (dict(authorAssociation="CONTRIBUTOR", commits=FAILING), (["alice"], "fix the checks", "author")),
    (dict(authorAssociation="CONTRIBUTOR", latestReviews=BOB_COMMENTED, reviewThreads=ONE_OPEN),
     (["alice"], "resolve 1 comment", "author")),
    (dict(authorAssociation="CONTRIBUTOR", reviewRequests=ASKED_BOB), (["bob"], "review", "reviewers")),
])
def test_each_line_of_the_rule(facts, expected):
    assert stands(**facts) == expected


def test_the_lines_are_tried_in_order():
    everything = dict(isDraft=True, mergeable="CONFLICTING", commits=FAILING, reviewRequests=ASKED_BOB,
                      reviewDecision="CHANGES_REQUESTED", reviewThreads=TWO_OPEN)
    assert stands(**everything)[1] == "finish the draft"
    del everything["isDraft"]
    assert stands(**everything)[1] == "resolve the conflict"
    del everything["mergeable"]
    assert stands(**everything)[1] == "fix the checks"
    everything["commits"] = RUNNING
    assert stands(**everything)[1] == "checks running"
    everything["commits"] = PASSING
    assert stands(**everything) == (["bob"], "review", "reviewers")     # a reviewer who was asked comes
    del everything["reviewRequests"]                                    # before the comments to resolve
    assert stands(**everything)[1] == "resolve 2 comments"


def test_a_stacked_pull_request_that_is_ready_waits_on_the_one_below_and_through_it():
    pulls = stack([parse_pull("o/r", n) for n in (
        node(12, "client", reviewRequests=ASKED_BOB),                                     # waits on bob
        node(13, "state", base="client", reviewDecision="APPROVED", latestOpinionatedReviews=BOB_APPROVED),
        node(15, "inference", base="state", reviewDecision="APPROVED", latestOpinionatedReviews=BOB_APPROVED),
        node(16, "icons", base="state", isDraft=True),                                    # has its own move
        node(9, "jira", reviewDecision="APPROVED", latestOpinionatedReviews=BOB_APPROVED))], "main")
    found = settle(pulls)
    assert (found[12].queued, found[12].movers, found[12].move) == (False, ["bob"], "review")
    assert (found[13].chain, found[13].movers, found[13].move) == ([12], ["bob"], "review")
    assert (found[15].chain, found[15].movers, found[15].move) == ([13, 12], ["bob"], "review")
    assert (found[16].queued, found[16].movers, found[16].move) == (False, ["alice"], "finish the draft")
    assert (found[9].queued, found[9].movers, found[9].move) == (False, ["alice"], "merge")   # on main: just merge
    assert found[15].who == ["alice"] and found[15].what == "merge"     # its own move, once the ones below land


def test_checks_github_did_not_run_for_lack_of_payment_count_as_passing():
    pull = parse_pull("o/r", node(17, "feature", commits=FAILING, reviewDecision="APPROVED",
                                  latestOpinionatedReviews=BOB_APPROVED))
    assert standing(pull).what == "fix the checks"
    pull.checks = "unpaid"                                      # what pulls.look_into_failures found
    assert (standing(pull).who, standing(pull).what) == (["alice"], "merge")


def test_an_author_who_may_ask_but_not_merge_asks_and_then_waits_on_the_maintainer():
    """An organization's triage role: reviewers can be picked, the merge is someone else's."""
    pull = parse_pull("o/r", node(17, "feature"))
    pull.can_merge = False
    assert (standing(pull).who, standing(pull).what) == (["alice"], "request review")
    pull = parse_pull("o/r", node(17, "feature", reviewDecision="APPROVED", latestOpinionatedReviews=BOB_APPROVED))
    pull.can_merge = False
    found = standing(pull)
    assert (found.who, found.what, found.by, found.others) == (["o"], "merge", "maintainer", True)
