# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""The picture as plain data: the tree, column 1, what needs effort, the humans, focus,
the counts, and what changed between two readings."""
import re
from datetime import datetime, timezone

import picture as pictures
from canvas import plain, seg_len
from picture import (CHECK_MARK, CHECK_SAID, Agent, Focus, Picture, cells, changes, counts, fingerprint, fit,
                     focuses, humans, in_focus,
                     key_of, labels, line_agents, mark, move_words, needs_effort, progress, quiet_bar, quiet_block,
                     quiet_place, quiet_scale, review_cell, review_marks, review_said, reviewers, tree,
                     waits_words)
from pulls import RepoPulls, parse_pull, stack
from test_pulls import node, review, thread
from theme import AXIS, INK, MUTED, UNITS, blend

NOW = datetime(2026, 11, 14, 16, 0, tzinfo=timezone.utc)
TIP, OLD = "a1" * 20, "c3" * 20
ASK = lambda *who: {"nodes": [{"requestedReviewer": {"__typename": "User", "login": w}} for w in who]}  # noqa: E731
APPROVED = lambda who: {"nodes": [review(who, "APPROVED", "2026-11-12T10:00:00Z")]}                      # noqa: E731


def app_nodes():
    return [
        node(12, "client", author={"login": "djp3"}, baseRefOid=TIP, title="GraphQL client",
             reviewRequests=ASK("bob"), updatedAt="2026-11-13T16:00:00Z"),                    # waits on bob, 1d
        node(13, "state", base="client", author={"login": "djp3"}, title="Review state",
             reviewDecision="APPROVED", latestOpinionatedReviews=APPROVED("bob")),            # queued behind 12
        node(15, "inference", base="state", author={"login": "djp3"}, isDraft=True,
             updatedAt="2026-11-14T15:00:00Z"),                                               # djp3: finish the draft, 1h
        node(9, "jira", author={"login": "mira"}, baseRefOid=OLD, title="Jira client",
             reviewRequests=ASK("djp3", "kenji"), updatedAt="2026-11-11T16:00:00Z"),          # waits on djp3 + kenji, 3d
        node(16, "reader", author={"login": "tobias"}, baseRefOid=TIP, mergeable="CONFLICTING",
             updatedAt="2026-11-10T16:00:00Z"),                                               # tobias: conflict, 4d
        node(24, "token-fix", base="release-0.2", author={"login": "tobias"}, baseRefOid=OLD,
             commits={"nodes": [{"commit": {"statusCheckRollup": {"state": "PENDING"}}}]}),   # nobody: checks running
        node(20, "keychain", author={"login": "djp3"}, baseRefOid=TIP, reviewDecision="APPROVED",
             latestOpinionatedReviews=APPROVED("kenji"), updatedAt="2026-11-14T14:00:00Z"),   # djp3: merge, 2h
    ]


def read(name, nodes, tip=TIP):
    return RepoPulls(name, "main", stack([parse_pull(name, n) for n in nodes], "main"), len(nodes), NOW, tip)


def picture(agents=None, nodes=None, viewer="djp3"):
    return Picture(["o/app", "o/web", "o/slow"],
                   {"o/app": read("o/app", nodes or app_nodes()), "o/web": read("o/web", [])},
                   viewer, agents or {}, {"o/slow": "reading…"})


def pull(pic, number, repo="o/app"):
    return pic.find((repo, number))


def text(rows):
    return [" | ".join(plain(cell).rstrip() for cell in row) for row in rows]


def test_the_tree_runs_repo_base_commit_then_the_pull_requests_in_stack_order():
    rows = tree(picture())
    seen = [(r.kind, r.lead + (f"#{r.pull.number}" if r.pull else r.sha + (" " + r.label if r.label and r.sha else r.label)))
            for r in rows]
    assert seen == [
        ("repo", "o/app"),
        ("base", "└ main"),
        ("commit", "  ┬ " + TIP[:7] + " tip"),          # the default branch's tip comes first
        ("pull", "  ├ #12"), ("pull", "  │ └ #13"), ("pull", "  │   └ #15"),
        ("pull", "  ├ #16"), ("pull", "  ├ #20"),
        ("commit", "  ┼ " + OLD[:7]),                    # an older commit: no distance is claimed
        ("pull", "  └ #9"),
        ("base", "└ release-0.2"),
        ("commit", "  ┬ " + OLD[:7]),
        ("pull", "  └ #24"),
        ("repo", "o/web"),                               # nothing open there: no line beneath it
        ("repo", "o/slow"), ("note", "reading…"),
    ]
    # each repository's row carries how many are open there and how many of them are listed:
    # for the one with none, that count is all the board has to say so
    assert [(r.repo, r.total, r.listed) for r in rows if r.kind == "repo"] == [
        ("o/app", 7, 7), ("o/web", 0, 0), ("o/slow", None, 0)]
    more = Picture(["o/app"], {"o/app": RepoPulls("o/app", "main", read("o/app", app_nodes()).pulls, 240, NOW, TIP)},
                   "djp3", {}, {})
    assert [(r.total, r.listed) for r in tree(more) if r.kind == "repo"] == [(240, 7)]     # more than were read


def test_column_one_for_each_way_a_pull_request_can_stand():
    pic = picture()
    assert text(cells(pic, pull(pic, 12))) == ["  you | waiting |   bob"]
    assert text(cells(pic, pull(pic, 13))) == ["  you | waiting |   #12 →", " |  |   bob"]
    assert text(cells(pic, pull(pic, 15))) == ["● you | to act | "]
    assert text(cells(pic, pull(pic, 9))) == ["  mira | waiting | ● you", " |  |   kenji"]
    assert text(cells(pic, pull(pic, 16))) == ["  tobias | to act | "]
    assert text(cells(pic, pull(pic, 24))) == ["  tobias | checks running | "]
    assert text(cells(pic, pull(pic, 20))) == ["● you | to act | "]


def test_yellow_is_only_on_a_move_that_is_the_viewers():
    pic = picture()

    def yellow(number):
        return ["#ffd75f" in style for row in cells(pic, pull(pic, number)) for cell in row for _, style in cell]

    assert any(yellow(15)) and any(yellow(9)) and any(yellow(20))
    assert not any(yellow(12)) and not any(yellow(16)) and not any(yellow(13)) and not any(yellow(24))
    nobody = picture(viewer="")                                   # no token yet: nobody is "you"
    assert text(cells(nobody, pull(nobody, 15))) == ["  djp3 | to act | "]


def test_the_move_in_words_and_where_it_waits():
    pic = picture()
    assert move_words(pic, pull(pic, 13)) == "merge once #12 lands"
    assert move_words(pic, pull(pic, 12)) == "review"
    assert waits_words(pic, pull(pic, 13)) == "waits on bob to review (behind #12)"
    assert waits_words(pic, pull(pic, 20)) == "waits on you to merge"
    assert waits_words(pic, pull(pic, 24)) == "checks running"


def test_what_needs_effort_is_ordered_by_how_long_it_has_been_quiet():
    found = needs_effort(picture(), NOW)
    assert [(e.pull.number, e.who, e.standing.what, e.mine, e.quiet) for e in found] == [
        (16, "tobias", "resolve the conflict", False, "4d"),
        (9, "you, kenji", "review", True, "3d"),
        (12, "bob", "review", False, "1d"),
        (20, "you", "merge", True, "2h"),
        (15, "you", "finish the draft", True, "1h"),
    ]                                                             # 13 is queued; 24 waits on nobody
    assert [p.number for p in found[2].behind] == [13] and found[2].urgency == "quiet 1d"


def test_an_idle_agent_makes_its_pull_request_more_urgent_and_a_working_one_takes_it_off_the_list():
    idle = Agent("djp3", "01", "o/app", 15, "author", "at its prompt")
    busy = Agent("bob", "01", "o/app", 12, "review", "working", "reading client.py")
    pic = picture({("o/app", 15): [idle], ("o/app", 12): [busy]})
    found = needs_effort(pic, NOW)
    assert [e.pull.number for e in found] == [15, 16, 9, 20]      # 15 first: an agent sits idle on it
    assert found[0].urgency == "1 agent waiting · quiet 1h"
    assert text(cells(pic, pull(pic, 15))) == ["● you |  | ", "  └ ▷ agent 01 | at prompt | "]
    assert text(cells(pic, pull(pic, 12))) == ["  you | waiting |   bob", " |  |   └ ⠋ agent 01"]
    assert progress(pic, pull(pic, 12))[0] == "in progress"       # an agent is at work on it: bob's, reviewing


def test_line_agents_names_the_agents_cells_draws_on_each_line():
    """The board says what each agent is doing beside the line it is drawn on: line_agents
    has to name, line for line, the agents cells draws."""
    pic = picture({("o/app", 15): [Agent("djp3", "01", "o/app", 15, "author", "at its prompt"),
                                   Agent("djp3", "02", "o/app", 15, "author", "working")],
                   ("o/app", 12): [Agent("bob", "01", "o/app", 12, "review", "working"),
                                   Agent("bob", "02", "o/app", 12, "review", "waiting"),
                                   Agent("djp3", "03", "o/app", 12, "author", "waiting")]})
    for p in pic.all():                                           # 12: both sides; 13: queued behind 12
        drawn, named = text(cells(pic, p)), line_agents(pic, p)
        assert len(named) == len(drawn), p.number
        for line, agents in zip(drawn, named):
            assert [a.name for a in agents] == re.findall(r"agent \d\d", line), (p.number, line)
    assert [[a.name for a in agents] for agents in line_agents(pic, pull(pic, 13))] == [
        [], [], ["agent 01"], ["agent 02"]]


def test_a_pull_request_whose_author_has_no_rights_waits_on_the_maintainer():
    nodes = [node(30, "fix", author={"login": "djp3"}, authorAssociation="CONTRIBUTOR", baseRefOid=TIP),
             node(31, "feature", author={"login": "wen"}, authorAssociation="NONE", baseRefOid=TIP,
                  reviewDecision="APPROVED", latestOpinionatedReviews=APPROVED("o"))]
    pic = picture(nodes=nodes)                                    # seen by the author of 30
    assert text(cells(pic, pull(pic, 30))) == ["  you | waiting |   o"]
    assert waits_words(pic, pull(pic, 30)) == "waits on o to review"
    assert waits_words(pic, pull(pic, 31)) == "waits on o to merge"
    assert not pic.mine(pull(pic, 30)) and counts(pic, "o/app") == (0, 1)
    assert [(p.name, [x.number for x in p.to_act]) for p in humans(pic)] == [("you", []), ("o", [30, 31])]
    keeper = picture(nodes=nodes, viewer="o")                     # seen by the maintainer: both are theirs
    assert text(cells(keeper, pull(keeper, 30))) == ["  djp3 | waiting | ● you"]
    assert counts(keeper, "o/app") == (2, 0)


def test_the_humans_are_the_viewer_and_then_whoever_is_waited_on_longest():
    people = humans(picture())
    assert [(p.name, [x.number for x in p.to_act]) for p in people] == [
        ("you", [9, 20, 15]), ("tobias", [16]), ("kenji", [9]), ("bob", [12])]
    assert "mira" not in [p.name for p in people]                 # nothing waits on mira: no card


def test_focus_steps_through_the_humans_and_dims_what_is_not_theirs():
    pic = picture()
    steps = focuses(pic)
    assert steps == [Focus(), Focus("crew", "djp3"), Focus("crew", "tobias"), Focus("crew", "kenji"),
                     Focus("crew", "bob")]

    def lit(focus):
        return [p.number for p in pic.all() if in_focus(pic, p, focus)]

    assert lit(Focus()) == [9, 12, 13, 15, 16, 20, 24]
    assert lit(Focus("crew", "bob")) == [12, 13]                  # what waits on bob, and what queues behind it
    assert lit(Focus("crew", "tobias")) == [16, 24]
    assert lit(Focus("crew", "djp3")) == [9, 12, 13, 15, 20]      # the viewer's own, and the review asked of them

    agent = Agent("djp3", "01", "o/app", 12, "author", "working")
    with_agent = picture({("o/app", 12): [agent]})
    assert Focus("agent", "djp3", agent.key) in focuses(with_agent) and Focus("human", "djp3") in focuses(with_agent)
    assert [p.number for p in with_agent.all() if in_focus(with_agent, p, Focus("agent", "djp3", agent.key))] == [12, 13]


def test_the_counts_for_the_top_line():
    pic = picture()
    assert counts(pic, "o/app") == (3, 3)                         # yours: 9, 20, 15; others: tobias, kenji, bob
    assert counts(pic, "o/web") == (0, 0)


def test_what_needs_effort_is_worked_out_once_per_picture(monkeypatch):
    """The counts, the cards and the focus all ask for it, on every draw."""
    asked = []
    quiet = pictures.quiet                                        # asked once for each pull request found
    monkeypatch.setattr(pictures, "quiet", lambda pull, now=None: asked.append(pull.number) or quiet(pull, now))
    pic = picture()
    found = needs_effort(pic)
    for name in pic.names:
        counts(pic, name)
    humans(pic)
    focuses(pic)
    assert needs_effort(pic) == found
    assert sorted(asked) == sorted(e.pull.number for e in found)  # each one, once
    asked.clear()
    needs_effort(picture())                                       # a new picture works it out again
    assert sorted(asked) == sorted(e.pull.number for e in found)
    assert [e.pull.number for e in needs_effort(pic, NOW)] == [e.pull.number for e in found]   # and a given moment


def test_marks_review_cells_and_progress():
    pic = picture(nodes=app_nodes() + [
        node(30, "x", author={"login": "djp3"}, baseRefOid=TIP, reviewDecision="CHANGES_REQUESTED",
             latestOpinionatedReviews={"nodes": [review("bob", "CHANGES_REQUESTED", "2026-11-12T10:00:00Z")]},
             reviewThreads={"totalCount": 3, "nodes": [{"isResolved": True}, {"isResolved": False},
                                                        {"isResolved": False}]})])
    assert [mark(pic, pull(pic, n))[2] for n in (15, 16, 30, 12, 20, 24)] == [
        "draft", "merge conflict", "changes requested", "in review", "ready to merge", "open, no review asked"]
    assert plain(review_cell(pic, pull(pic, 30), True, 40)) == "✗ bob  ◇2"
    assert plain(review_cell(pic, pull(pic, 9), True, 40)) == "◔ you  ◔ kenji  "
    assert plain(review_cell(pic, pull(pic, 9), False, 40)) == "◔◔"
    assert plain(review_cell(pic, pull(pic, 15), True, 40)) == "·"
    assert progress(pic, pull(pic, 30)) == ("stalled", 1, 3)
    assert progress(pic, pull(pic, 12)) == ("not started", 0, 0)     # waits on a first review
    assert progress(pic, pull(pic, 20)) == ("ready to merge", 0, 0)  # approved: merging is the next move
    assert progress(pic, pull(pic, 13)) == ("queued", 0, 0)          # the same, behind the one it stands on
    done = picture(nodes=[node(31, "y", author={"login": "djp3"}, reviewDecision="CHANGES_REQUESTED",
                               latestOpinionatedReviews={"nodes": [review("bob", "CHANGES_REQUESTED",
                                                                          "2026-11-12T10:00:00Z")]},
                               reviewThreads={"totalCount": 2, "nodes": [{"isResolved": True}] * 2})])
    assert progress(done, pull(done, 31)) == ("resolved", 2, 2)      # all resolved; the request still stands


def test_the_quiet_bar_has_two_cells_to_each_unit_of_time_and_the_color_of_the_unit_it_has_reached():
    minute, hour, day = 60, 3600, 86400
    assert [quiet_place(s) for s in (5, minute, hour, day, 7 * day, 90 * day, 400 * day)] == [0, 0, 2, 4, 6, 8, 8]
    assert 0 < quiet_place(10 * minute) < 2 < quiet_place(3 * hour) < 4 < quiet_place(2 * day) < 6
    assert plain(quiet_scale()) == "m h d w"
    assert [color for text, color in quiet_scale() if text.strip()] == list(UNITS)    # the heading is the key

    def quiet_for(seconds):
        at = datetime.fromtimestamp(NOW.timestamp() - seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return parse_pull("o/app", node(40, "x", updatedAt=at))

    def bar(seconds):
        return quiet_bar(quiet_for(seconds), NOW)

    minutes, hours, days, weeks = UNITS
    assert bar(20) == ("▏", f"bold {INK}")                           # within the minute: news
    assert bar(minute) == ("▏", minutes)                             # never an empty bar
    # minutes, hours and days: the whole bar in the unit's color, at full strength throughout
    assert bar(40 * minute) == ("█▊", minutes) and bar(hour - 1) == ("█▉", minutes)
    assert bar(hour) == ("██", hours) and bar(3 * hour) == ("██▋", hours) and bar(20 * hour) == ("███▉", hours)
    assert bar(day) == ("████", days) and bar(4 * day) == ("█████▍", days) and bar(7 * day - 1) == ("█████▉", days)
    # weeks: the whole bar again, and only this color fades, to grey at three months
    assert bar(7 * day) == ("██████", weeks)
    assert bar(9 * day) == ("██████▏", blend(weeks, AXIS, 2 / 83))
    assert bar(60 * day) == ("███████▋", blend(weeks, AXIS, 53 / 83))
    assert bar(90 * day) == bar(400 * day) == ("████████", AXIS)
    # the same in one cell: two steps to each unit, in the color of the bar
    blocks = [quiet_block(quiet_for(s), NOW) for s in (
        20, 5 * minute, 40 * minute, 3 * hour, 20 * hour, 2 * day + hour, 5 * day, 9 * day, 60 * day, 90 * day)]
    assert [block for block, _ in blocks] == list("▁▁▂▃▄▅▆▇██")
    assert [ink for _, ink in blocks[1:7]] == [minutes, minutes, hours, hours, days, days] and blocks[-1][1] == AXIS
    never = parse_pull("o/app", node(41, "y", updatedAt=None))
    assert quiet_bar(never, NOW) == ("", MUTED) and quiet_block(never, NOW) == ("", MUTED)


def test_the_mark_is_about_the_review_whatever_the_checks_and_the_next_move_are():
    """Approved by rafael after a bot's comments, all resolved, and the checks fail: the move
    is to fix the checks, and the mark still says it is approved."""
    resolved = {"totalCount": 8, "nodes": [{"isResolved": True}] * 8}
    bot = review("copilot-pull-request-reviewer", "COMMENTED", "2026-11-11T10:00:00Z")
    rafael = review("rafael-moreno", "APPROVED", "2026-11-12T10:00:00Z")
    failing = {"nodes": [{"commit": {"statusCheckRollup": {"state": "FAILURE"}}}]}
    running = {"nodes": [{"commit": {"statusCheckRollup": {"state": "PENDING"}}}]}
    one_open = {"totalCount": 2, "nodes": [{"isResolved": True}, {"isResolved": False}]}
    pic = picture(nodes=[
        node(94, "a", author={"login": "wen"}, baseRefOid=OLD, reviewDecision="APPROVED", commits=failing,
             latestReviews={"nodes": [bot]}, latestOpinionatedReviews={"nodes": [rafael]}, reviewThreads=resolved),
        node(95, "b", author={"login": "wen"}, reviewDecision="APPROVED", commits=running,
             latestOpinionatedReviews={"nodes": [rafael]}),
        node(96, "c", author={"login": "wen"}, reviewDecision="APPROVED", reviewThreads=one_open,
             latestOpinionatedReviews={"nodes": [rafael]}),
        node(97, "d", author={"login": "wen"}, latestReviews={"nodes": [bot]}, reviewThreads=one_open),
        node(98, "e", author={"login": "wen"}, reviewDecision="REVIEW_REQUIRED",
             latestOpinionatedReviews={"nodes": [rafael]}),
        node(99, "f", author={"login": "wen"}, latestReviews={"nodes": [bot]})])
    assert [mark(pic, pull(pic, n))[::2] for n in (94, 95, 96, 97, 98, 99)] == [
        ("✓", "approved"), ("✓", "approved"), ("✓", "approved"),      # the checks, or a comment, hold it up
        ("◔", "in review"), ("◔", "in review"),                       # looked at, not settled
        ("▲", "ready to merge")]                                      # looked at, nothing asked, nothing open
    assert waits_words(pic, pull(pic, 94)) == "waits on wen to fix the checks"
    assert plain(review_cell(pic, pull(pic, 94), True, 23)) == "◆ copilot  ✓ rafael-mo…  "    # as the board gives it
    assert plain(review_cell(pic, pull(pic, 94), True, 40)) == "◆ copilot  ✓ rafael-moreno  "
    assert plain(review_cell(pic, pull(pic, 94), True, 10)) == "◆✓"           # no room for three letters each
    assert plain(review_cell(pic, pull(pic, 96), True, 14)) == "✓ rafael-…  ◇1"   # the count keeps its place


def test_a_comment_only_review_shows_whether_its_comments_are_still_to_resolve():
    bot = "copilot-pull-request-reviewer"
    said = {"nodes": [review(bot, "COMMENTED", "2026-11-11T10:00:00Z"),
                      review("carol", "COMMENTED", "2026-11-12T10:00:00Z"),
                      review("dave", "COMMENTED", "2026-11-13T10:00:00Z")]}
    threads = {"totalCount": 4, "nodes": [thread(True, by="Copilot", under=bot), thread(True, by="Copilot", under=bot),
                                          thread(False, by="carol", under="carol"),
                                          thread(True, by="carol", under="carol")]}
    pic = picture(nodes=[node(40, "a", author={"login": "wen"}, latestReviews=said, reviewThreads=threads)])
    here = pull(pic, 40)
    assert plain(review_cell(pic, here, True, 40)) == "◆ copilot  ◇ carol  ◆ dave  ◇1"
    assert plain(review_cell(pic, here, False, 40)) == "◆◇◆ ◇1"
    assert [review_marks(r) for r in here.reviews] == [
        [("◆", "#0ca30c")], [("◇", "#ec835a")], [("◆", "#0ca30c")]]    # filled green: resolved; open orange: not yet
    assert [review_said(r) for r in here.reviews] == [
        "commented, all resolved", "commented, 1 to resolve", "commented"]    # dave began no thread
    after = picture(nodes=[node(40, "a", author={"login": "wen"}, latestReviews=said, reviewThreads={
        "totalCount": 4, "nodes": [thread(True, under=bot), thread(False, under=bot),      # one reopened,
                                   thread(True, under="carol"), thread(True, under="carol")]})])   # one resolved
    assert fingerprint(pic, here) != fingerprint(after, pull(after, 40))      # the same count, and it still shows


def test_a_review_asked_again_after_changes_reads_re_review_in_the_mark_and_in_column_one():
    """GitHub keeps "changes requested" until bob reviews again; once the author has asked
    him to, the icon says so, as column 1 does."""
    changes = {"nodes": [review("bob", "CHANGES_REQUESTED", "2026-11-12T10:00:00Z")]}
    pic = picture(nodes=[
        node(41, "a", author={"login": "wen"}, reviewDecision="CHANGES_REQUESTED",
             latestOpinionatedReviews=changes, reviewRequests=ASK("bob")),
        node(42, "b", author={"login": "wen"}, reviewDecision="CHANGES_REQUESTED",
             latestOpinionatedReviews=changes, reviewRequests=ASK("bob", "carol"))])     # and someone new
    assert mark(pic, pull(pic, 41))[::2] == ("↻", "re-review asked")
    assert waits_words(pic, pull(pic, 41)) == "waits on bob to re-review"
    assert mark(pic, pull(pic, 42))[::2] == ("◔", "in review")
    assert waits_words(pic, pull(pic, 42)).endswith("to review")


def test_a_review_that_asked_for_changes_turns_to_a_green_circle_once_all_it_raised_is_resolved():
    def asked_for_changes(*threads):
        nodes = [node(41, "a", author={"login": "wen"}, reviewDecision="CHANGES_REQUESTED",
                      latestOpinionatedReviews={"nodes": [review("bob", "CHANGES_REQUESTED", "2026-11-12T10:00:00Z")]},
                      reviewThreads={"totalCount": len(threads), "nodes": list(threads)})]
        pic = picture(nodes=nodes)
        here = pull(pic, 41)
        return review_marks(here.reviews[0])[0], review_said(here.reviews[0]), plain(review_cell(pic, here, True, 40))

    assert asked_for_changes(thread(True, under="bob"), thread(True, under="bob")) == (
        ("●", "#0ca30c"), "asked for changes, all resolved", "● bob  ")
    assert asked_for_changes(thread(True, under="bob"), thread(False, under="bob")) == (
        ("✗", "#ec835a"), "asked for changes, 1 to resolve", "✗ bob  ◇1")
    assert asked_for_changes() == (("✗", "#ec835a"), "asked for changes", "✗ bob  ")   # nothing to go by: it stands
    pic = picture(nodes=[node(41, "a", author={"login": "wen"}, reviewDecision="CHANGES_REQUESTED",
                              latestOpinionatedReviews={"nodes": [review("bob", "CHANGES_REQUESTED",
                                                                         "2026-11-12T10:00:00Z")]},
                              reviewThreads={"totalCount": 1, "nodes": [thread(True, under="bob")]})])
    assert mark(pic, pull(pic, 41))[::2] == ("✗", "changes requested")        # the pull request as a whole still is
    assert waits_words(pic, pull(pic, 41)) == "waits on wen to address the requested changes"


def test_unpaid_checks_are_a_red_dollar_and_do_not_hold_an_approved_pull_request_up():
    failing = {"nodes": [{"commit": {"statusCheckRollup": {"state": "FAILURE"}}}]}
    pic = picture(nodes=[node(94, "a", author={"login": "wen"}, reviewDecision="APPROVED", commits=failing,
                              latestOpinionatedReviews=APPROVED("rafael"))])
    assert (mark(pic, pull(pic, 94))[2], waits_words(pic, pull(pic, 94))) == ("approved", "waits on wen to fix the checks")
    pull(pic, 94).checks = "unpaid"
    paid = Picture(pic.names, pic.pulls, pic.viewer)             # the rule, worked out again
    assert (mark(paid, pull(paid, 94))[::2], waits_words(paid, pull(paid, 94))) == (
        ("▲", "ready to merge"), "waits on wen to merge")
    assert CHECK_MARK["unpaid"] == ("$", "#d03b3b") and "not paid" in CHECK_SAID["unpaid"]


def test_the_review_column_shows_each_reviewers_trail_before_their_name():
    """Copilot commented (seven threads, all resolved); rafael asked for changes (one thread,
    resolved) and then approved; bob asked for changes and has been asked to look again."""
    bot = "copilot-pull-request-reviewer"
    said = [review(bot, "COMMENTED", "2026-11-10T10:00:00Z", "R1"),
            review("rafael-moreno", "CHANGES_REQUESTED", "2026-11-11T10:00:00Z", "R2"),
            review("rafael-moreno", "APPROVED", "2026-11-12T10:00:00Z", "R3")]
    threads = [thread(True, by="Copilot", review="R1")] * 7 + [thread(True, by="rafael-moreno", review="R2")]
    facts = dict(author={"login": "wen"}, reviewDecision="APPROVED", reviews={"nodes": said},
                 latestReviews={"nodes": [said[0], said[2]]}, latestOpinionatedReviews={"nodes": [said[2]]},
                 reviewThreads={"totalCount": 8, "nodes": threads})
    again = review("bob", "CHANGES_REQUESTED", "2026-11-11T10:00:00Z", "B1")
    pic = picture(nodes=[
        node(94, "a", **facts),
        node(95, "b", author={"login": "wen"}, reviews={"nodes": [again]}, latestOpinionatedReviews={"nodes": [again]},
             reviewThreads={"totalCount": 1, "nodes": [thread(True, review="B1")]}, reviewDecision="CHANGES_REQUESTED",
             reviewRequests=ASK("bob", "carol"))])
    here = pull(pic, 94)
    assert plain(review_cell(pic, here, True, 40)) == "◆ copilot  ●✓ rafael-moreno  "
    assert plain(review_cell(pic, here, True, 23)) == "◆ copilot  ●✓ rafael-m…  "       # as wide as the board gives it
    assert plain(review_cell(pic, here, True, 10)) == "◆ ●✓"                # no names: a gap keeps the trails apart
    assert plain(review_cell(pic, here, False, 40)) == "◆ ●✓"
    assert [(plain(marks), who, words) for marks, who, words in reviewers(pic, here)] == [
        ("◆", "copilot", "commented, all resolved"), ("●✓", "rafael-moreno", "approved")]
    assert review_marks(here.reviews[1]) == [("●", "#0ca30c"), ("✓", "#0ca30c")]
    assert [(plain(marks), who, words) for marks, who, words in reviewers(pic, pull(pic, 95))] == [
        ("●◔", "bob", "asked again, no answer yet"), ("◔", "carol", "asked, no answer yet")]
    assert plain(review_cell(pic, pull(pic, 95), True, 40)) == "●◔ bob  ◔ carol  "


def test_a_long_trail_keeps_its_latest_marks():
    rounds = [review("bob", "CHANGES_REQUESTED", f"2026-11-0{day}T10:00:00Z", f"R{day}") for day in range(1, 6)]
    done = review("bob", "APPROVED", "2026-11-09T10:00:00Z", "R9")
    pic = picture(nodes=[node(40, "a", author={"login": "wen"}, reviews={"nodes": rounds + [done]},
                              latestOpinionatedReviews={"nodes": [done]}, reviewDecision="APPROVED",
                              reviewThreads={"totalCount": 5, "nodes": [thread(True, review=f"R{d}") for d in range(1, 6)]})])
    assert plain(review_cell(pic, pull(pic, 40), True, 40)) == "…●●✓ bob  "       # six steps: the last three, after …


def test_names_that_do_not_fit_are_cut_the_longest_first():
    assert fit(["bob", "carol"], 8) == ["bob", "carol"]
    assert fit(["copilot-pull-request-reviewer", "bob"], 12) == ["copilot-…", "bob"]
    assert fit(["alexandria", "bartholomew"], 10) == ["alex…", "bart…"]
    assert fit(["alexandria", "bartholomew"], 5) is None and fit([], 0) == []
    pic = picture()
    assert pic.shown("copilot-pull-request-reviewer") == "copilot" and pic.shown("dependabot[bot]") == "dependabot"
    assert pic.shown("DJP3") == "you" and pic.shown("bob") == "bob"


def test_two_repositories_with_the_same_name_keep_their_owners():
    assert labels(["djp3/app", "upstream/app", "o/web"]) == {
        "djp3/app": "djp3/app", "upstream/app": "upstream/app", "o/web": "web"}


def test_what_changed_between_two_readings():
    before = picture()
    nodes = [n for n in app_nodes() if n["number"] != 16]                        # 16 merged or closed
    nodes[0] = dict(nodes[0], reviewRequests={"nodes": []}, reviewDecision="APPROVED",
                    latestOpinionatedReviews=APPROVED("bob"))                     # bob approved 12
    nodes.append(node(31, "new", author={"login": "mira"}, baseRefOid=TIP, reviewRequests=ASK("djp3")))
    after = picture(nodes=nodes)
    assert fingerprint(before, pull(before, 15)) == fingerprint(after, pull(after, 15))
    said = {c.key[1]: c.said for c in changes(before, after, "o/app")}
    assert said == {
        12: "#12 bob approved · waits on you to merge",
        13: "#13 now waits on you to merge (behind #12)",                          # it queues behind 12, which moved
        31: "#31 opened by mira · waits on you to review",
        16: "#16 is no longer open (merged or closed)",
    }
    assert [c.pull for c in changes(before, after, "o/app") if c.key[1] == 16] == [None]
    assert changes(None, after, "o/app") == []                                     # a first reading changes nothing
    first = Picture(["o/app"], {}, "djp3")
    assert changes(first, after, "o/app") == []
    assert key_of(pull(after, 31)) == ("o/app", 31)


# ---- a review asked of a team ----------------------------------------------------------

PLATFORM, ENGINEERING = "team/acme/platform", "team/acme/engineering"
ON_PLATFORM = {PLATFORM: "Platform"}


def ask_teams(*slugs, people=()):
    return {"nodes": [{"requestedReviewer": {"__typename": "Team", "slug": slug}} for slug in slugs]
            + [{"requestedReviewer": {"__typename": "User", "login": who}} for who in people]}


def team_nodes():
    return [
        node(40, "bump-a", author={"login": "dependabot"}, baseRefOid=TIP, title="Bump a",
             reviewRequests=ask_teams("platform"), updatedAt="2026-11-12T16:00:00Z"),      # the viewer's team, 2d
        node(41, "bump-b", author={"login": "dependabot"}, baseRefOid=TIP, title="Bump b",
             reviewRequests=ask_teams("docs"), updatedAt="2026-11-13T16:00:00Z"),          # a team they are not on
        node(42, "own", author={"login": "djp3"}, baseRefOid=TIP, title="My own",
             reviewRequests=ask_teams("platform"), updatedAt="2026-11-14T10:00:00Z"),      # their own, asking it
        node(43, "both", author={"login": "mira"}, baseRefOid=TIP, title="Both",
             reviewRequests=ask_teams("platform", people=("djp3",)), updatedAt="2026-11-14T12:00:00Z"),
    ]


def team_picture(nodes=None, teams=ON_PLATFORM, held=False, agents=None, viewer="djp3"):
    """One repository of the organization Acme, seen by someone on its team Platform."""
    return Picture(["Acme/app"], {"acme/app": read("Acme/app", nodes or team_nodes())}, viewer, agents or {}, {},
                   teams=dict(teams), held=held)


def at(pic, number):
    return pull(pic, number, "acme/app")


def yellow(pic, number):
    return any("#ffd75f" in style for row in cells(pic, at(pic, number)) for cell in row for _, style in cell)


def test_a_review_asked_of_a_team_the_viewer_is_on_is_theirs_under_the_teams_name():
    pic = team_picture()
    assert at(pic, 40).asked == ["team/Acme/platform"]                     # the team in full, as GitHub spells Acme
    assert pic.mine(at(pic, 40)) and yellow(pic, 40)
    assert text(cells(pic, at(pic, 40))) == ["  dependabot | waiting | ● Platform"]      # as "● you" is drawn
    assert waits_words(pic, at(pic, 40)) == "waits on Platform to review"
    assert plain(review_cell(pic, at(pic, 40), True, 40)).strip() == "◔ Platform"
    found = {e.pull.number: e for e in needs_effort(pic, NOW)}
    assert (found[40].mine, found[40].who, found[40].undecided) == (True, "Platform", False)
    assert (found[43].mine, found[43].who) == (True, "Platform, you")
    # 40 and 43 are the viewer's to get moving. The others it waits on: the team they are not
    # on, and their own team's other people for the pull request they wrote themselves.
    assert counts(pic, "Acme/app") == (2, 2)


def test_a_team_the_viewer_is_not_on_reads_as_it_did_and_a_slug_in_another_organization_is_another_team():
    pic = team_picture()
    assert not pic.mine(at(pic, 41)) and not yellow(pic, 41)
    assert text(cells(pic, at(pic, 41))) == ["  dependabot | waiting |   team/docs"]
    assert waits_words(pic, at(pic, 41)) == "waits on team/docs to review"
    elsewhere = team_picture(teams={"team/other/platform": "Platform"})    # on Other's Platform, not Acme's
    assert not elsewhere.mine(at(elsewhere, 40)) and not yellow(elsewhere, 40)
    assert text(cells(elsewhere, at(elsewhere, 40))) == ["  dependabot | waiting |   team/platform"]
    nobody = team_picture(viewer="")                                       # no token: nothing is anyone's
    assert not nobody.mine(at(nobody, 40))


def test_a_teams_request_stays_the_viewers_whoever_has_reviewed():
    """For as long as GitHub lists the team as asked: there is no rule about reviews."""
    nodes = [node(40, "bump-a", author={"login": "dependabot"}, baseRefOid=TIP, reviewRequests=ask_teams("platform"),
                  latestReviews={"nodes": [review("bob", "COMMENTED", "2026-11-13T10:00:00Z")]}),
             node(41, "bump-b", author={"login": "dependabot"}, baseRefOid=TIP, reviewRequests=ask_teams("platform"),
                  reviewDecision="APPROVED", latestOpinionatedReviews=APPROVED("djp3"))]
    pic = team_picture(nodes)
    assert pic.mine(at(pic, 40)) and pic.mine(at(pic, 41))                 # a teammate reviewed; the viewer did
    assert waits_words(pic, at(pic, 41)) == "waits on Platform to review"


def test_the_viewers_own_pull_request_asking_their_team_is_not_theirs():
    """GitHub lets no author review their own: the request is for the others on the team."""
    pic = team_picture()
    assert not pic.mine(at(pic, 42)) and not yellow(pic, 42)
    assert text(cells(pic, at(pic, 42))) == ["  you | waiting |   Platform"]      # still by its name
    assert not {e.pull.number: e for e in needs_effort(pic, NOW)}[42].mine


def test_a_review_asked_of_the_team_the_viewers_team_is_nested_in_is_theirs_too():
    nodes = [node(40, "bump-a", author={"login": "dependabot"}, baseRefOid=TIP,
                  reviewRequests=ask_teams("engineering"))]
    pic = team_picture(nodes, teams={PLATFORM: "Platform", ENGINEERING: "Engineering"})
    assert pic.mine(at(pic, 40))
    assert text(cells(pic, at(pic, 40))) == ["  dependabot | waiting | ● Engineering"]    # by its own name


def test_until_the_viewers_teams_are_known_a_teams_request_is_nobodys():
    pic = team_picture(teams={}, held=True)
    assert not pic.mine(at(pic, 40)) and not yellow(pic, 40)
    assert text(cells(pic, at(pic, 40))) == ["  dependabot | waiting |   team/platform"]   # no dot, no name yet
    found = {e.pull.number: e for e in needs_effort(pic, NOW)}
    assert found[40].undecided and found[41].undecided
    # One that also waits on the viewer by name is theirs already: nothing about it is undecided.
    assert (found[43].mine, found[43].undecided) == (True, False) and yellow(pic, 43)
    assert text(cells(pic, at(pic, 43))) == ["  mira | waiting |   team/platform", " |  | ● you"]
    # Nor is the viewer's own: whichever team it asks, it is the others' on it to review.
    assert (found[42].mine, found[42].undecided) == (False, False)
    assert counts(pic, "Acme/app") == (1, 1)                               # 40 and 41 are in neither count
    assert [(p.name, [x.number for x in p.to_act]) for p in humans(pic)] == [("you", [43])]     # and no card
    assert focuses(pic) == [Focus(), Focus("crew", "djp3")]
    let_go = team_picture(teams={})                                        # the read failed: anyone else's
    assert counts(let_go, "Acme/app") == (1, 2)
    assert [p.name for p in humans(let_go)] == ["you", "team/platform", "team/docs"]


def test_a_teams_name_in_a_wide_script_is_fitted_by_the_cells_it_takes():
    """A login is ASCII; a team's name is whatever was typed. The comments to resolve, at
    the end of the review column, must not be pushed out of it."""
    wide = "プラットフォーム基盤"                                          # ten characters, twenty cells
    assert fit([wide], 8) == ["プラッ…"] and fit([wide, "bob"], 9) == ["プラ…", "bob"]
    assert fit([wide], 20) == [wide] and fit([wide], 2) is None
    open_threads = {"totalCount": 2, "nodes": [thread(False, by="bob", under="bob"),
                                               thread(False, by="bob", under="bob")]}
    nodes = [node(40, "bump-a", author={"login": "dependabot"}, baseRefOid=TIP, reviewRequests=ask_teams("platform"),
                  reviewThreads=open_threads)]
    pic = team_picture(nodes, teams={PLATFORM: wide})
    for room in (14, 18, 30):
        cell = review_cell(pic, at(pic, 40), True, room)
        assert seg_len(cell) <= room and plain(cell).endswith("◇2"), (room, plain(cell))
    assert plain(review_cell(pic, at(pic, 40), True, 14)) == "◔ プラッ…  ◇2"
    assert plain(review_cell(team_picture(nodes), at(pic, 40), True, 14)) == "◔ Platform  ◇2"    # as before


def test_a_request_to_a_team_and_a_person_is_held_whole_until_the_teams_are_known():
    """It must not read as someone else's and then turn into the viewer's."""
    nodes = [node(46, "mixed", author={"login": "mira"}, baseRefOid=TIP,
                  reviewRequests=ask_teams("platform", people=("bob",)))]
    held = team_picture(nodes, teams={}, held=True)
    effort, = needs_effort(held, NOW)
    assert (effort.mine, effort.undecided) == (False, True)                # no mark yet, either way
    assert counts(held, "Acme/app") == (0, 1)                              # bob is one of the others, whatever comes
    assert [p.name for p in humans(held)] == ["you", "bob"]
    known = team_picture(nodes)                                            # the viewer is on Platform
    effort, = needs_effort(known, NOW)
    assert (effort.mine, effort.undecided) == (True, False) and counts(known, "Acme/app") == (1, 1)
    not_on_it = team_picture(nodes, teams={})                              # or is on no such team
    effort, = needs_effort(not_on_it, NOW)
    assert (effort.mine, effort.undecided) == (False, False) and counts(not_on_it, "Acme/app") == (0, 2)


def test_a_working_agent_on_a_teams_request_takes_it_off_the_viewers_hands():
    busy = Agent("bob", "01", "Acme/app", 40, "review", "working", "reading the diff")
    pic = team_picture(agents={("acme/app", 40): [busy]})
    assert not pic.mine(at(pic, 40)) and 40 not in [e.pull.number for e in needs_effort(pic, NOW)]
    assert text(cells(pic, at(pic, 40))) == ["  dependabot | waiting |   Platform", " |  |   └ ⠋ agent 01"]
    assert counts(pic, "Acme/app") == (1, 2)


def test_a_reviewing_agent_nobody_asked_by_name_is_beneath_every_team_asked():
    """Which team its operator is on is not known, so it is not guessed."""
    nodes = [node(44, "wide", author={"login": "mira"}, baseRefOid=TIP,
                  reviewRequests=ask_teams("platform", "engineering", people=("kenji",)))]
    unnamed = Agent("bob", "01", "Acme/app", 44, "review", "at its prompt")
    named = Agent("kenji", "02", "Acme/app", 44, "review", "waiting")
    pic = team_picture(nodes, teams={PLATFORM: "Platform", ENGINEERING: "Engineering"},
                       agents={("acme/app", 44): [unnamed, named]})
    assert text(cells(pic, at(pic, 44))) == [
        "  mira | waiting | ● Platform", " |  |   └ ▷ agent 01",
        " |  | ● Engineering", " |  |   └ ▷ agent 01",
        " |  |   kenji", " |  |   └ · agent 02"]                           # kenji's is beneath kenji alone
    assert [[a.name for a in agents] for agents in line_agents(pic, at(pic, 44))] == [
        [], ["agent 01"], [], ["agent 01"], [], ["agent 02"]]              # in step with the lines drawn


def test_a_stacked_pull_request_that_waits_through_the_one_below_on_the_viewers_team():
    nodes = [node(50, "base", author={"login": "mira"}, baseRefOid=TIP, reviewRequests=ask_teams("platform")),
             node(51, "next", base="base", author={"login": "djp3"}, reviewDecision="APPROVED",
                  latestOpinionatedReviews=APPROVED("bob"))]
    pic = team_picture(nodes)
    assert pic.stand(at(pic, 51)).queued and pic.mine(at(pic, 51))         # the viewer can review 50, mira's
    assert text(cells(pic, at(pic, 51))) == ["  you | waiting |   #50 →", " |  | ● Platform"]
    assert waits_words(pic, at(pic, 51)) == "waits on Platform to review (behind #50)"
    swapped = [dict(nodes[0], author={"login": "djp3"}), dict(nodes[1], author={"login": "mira"})]
    other = team_picture(swapped)                                          # 50 is the viewer's own: not theirs
    assert not other.mine(at(other, 51)) and not other.mine(at(other, 50))


def test_a_team_has_a_card_of_its_own_right_after_the_viewers():
    people = humans(team_picture())
    assert [(p.name, p.kind, p.viewer, p.yours, p.dot, [x.number for x in p.to_act]) for p in people] == [
        ("you", "human", True, True, True, [43]),
        ("Platform", "team", False, True, True, [40, 42, 43]),             # once, here: not on the viewer's card
        ("team/docs", "team", False, False, False, [41])]
    own = humans(team_picture([team_nodes()[2]]))                          # only the viewer's own pull request
    assert [(p.name, p.yours, p.dot) for p in own] == [("you", True, False), ("Platform", True, False)]
    late = [node(60, "old", author={"login": "mira"}, baseRefOid=TIP, reviewRequests=ASK("kenji"),
                 updatedAt="2026-11-01T16:00:00Z")] + team_nodes()
    assert [p.name for p in humans(team_picture(late))] == ["you", "Platform", "kenji", "team/docs"]
    named_you = humans(team_picture(teams={PLATFORM: "you"}))              # a team may be called anything
    assert [(p.name, p.viewer, p.kind) for p in named_you[:2]] == [("you", True, "human"), ("you", False, "team")]


def test_a_teams_card_is_one_step_of_the_focus_and_lights_what_waits_on_the_team():
    pic = team_picture()
    assert focuses(pic) == [Focus(), Focus("crew", "djp3"), Focus("crew", PLATFORM), Focus("crew", "team/acme/docs")]

    def lit(focus):
        return [p.number for p in pic.all() if in_focus(pic, p, focus)]

    assert lit(Focus("crew", PLATFORM)) == [40, 42, 43]
    assert lit(Focus("crew", "djp3")) == [42, 43]                          # theirs by name; 40 is the team's


def test_a_review_asked_of_someone_github_does_not_name_is_someone_elses_to_give():
    nodes = [node(47, "secret", author={"login": "djp3"}, baseRefOid=TIP,
                  reviewRequests={"nodes": [{"requestedReviewer": None}]})]
    pic = team_picture(nodes)
    assert text(cells(pic, at(pic, 47))) == ["  you | waiting |   a hidden reviewer"]      # not "to act"
    assert waits_words(pic, at(pic, 47)) == "waits on a hidden reviewer to review"
    assert not pic.mine(at(pic, 47)) and counts(pic, "Acme/app") == (0, 1)
    assert [(p.name, p.kind, p.yours) for p in humans(pic)] == [("you", "human", True),
                                                                ("a hidden reviewer", "human", False)]
    held = team_picture(nodes, teams={}, held=True)                        # not a team to wait for: settled
    assert not needs_effort(held, NOW)[0].undecided


def test_two_teams_that_would_read_alike_each_say_which_organization_they_are_of():
    def two(teams):
        asks = lambda number: [node(number, "bump", author={"login": "dependabot"}, baseRefOid=TIP,     # noqa: E731
                                    reviewRequests=ask_teams("platform"))]
        return Picture(["Acme/app", "Other/web"], {"acme/app": read("Acme/app", asks(40)),
                                                   "other/web": read("Other/web", asks(70))},
                       "djp3", {}, {}, teams=teams)

    def waits(pic):
        return [text(cells(pic, pull(pic, 40, "acme/app")))[0].split(" | ")[2].strip(),
                text(cells(pic, pull(pic, 70, "other/web")))[0].split(" | ")[2].strip()]

    on_neither = two({})
    assert waits(on_neither) == ["team/Acme/platform", "team/Other/platform"]
    assert [p.name for p in humans(on_neither)] == ["you", "team/Acme/platform", "team/Other/platform"]
    on_both = two({PLATFORM: "Platform", "team/other/platform": "Platform"})
    assert waits(on_both) == ["● Acme/Platform", "● Other/Platform"]
    assert [p.name for p in humans(on_both)] == ["you", "Acme/Platform", "Other/Platform"]
    assert [e.who for e in needs_effort(on_both, NOW)] == ["Acme/Platform", "Other/Platform"]
    on_one = two(ON_PLATFORM)                                              # they read apart already
    assert waits(on_one) == ["● Platform", "team/platform"]
    assert waits(two({PLATFORM: "Platform", "team/other/platform": "Web Platform"})) == [
        "● Platform", "● Web Platform"]
    alone = team_picture()                                                 # one organization: as it was
    assert alone.shown("team/Acme/platform") == "Platform" and alone.shown("team/Acme/docs") == "team/docs"


def test_a_change_names_the_team_as_it_names_people():
    before = team_picture()
    nodes = team_nodes() + [node(45, "bump-c", author={"login": "dependabot"}, baseRefOid=TIP,
                                 reviewRequests=ask_teams("platform"))]
    nodes[1] = dict(nodes[1], reviewRequests=ask_teams("docs", "platform"))
    said = {c.key[1]: c.said for c in changes(before, team_picture(nodes), "Acme/app")}
    assert said == {45: "#45 opened by dependabot · waits on Platform to review",
                    41: "#41 Platform asked to review · waits on team/docs, Platform to review"}
    # Seen while the teams are being read, the same two readings differ in the same places:
    held = {c.key[1] for c in changes(team_picture(teams={}, held=True), team_picture(nodes, teams={}, held=True),
                                      "Acme/app")}
    assert held == {45, 41}
    assert changes(team_picture(teams={}, held=True), team_picture(teams={}, held=True), "Acme/app") == []
