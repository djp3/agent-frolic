# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""The team's agents, from what the agent-grange reports to where they are drawn: which
pull request an agent is on, what it is doing there, and how the app keeps asking."""
from datetime import datetime, timezone

import pytest

import board
import keychain
import main
from canvas import plain
from grange import GrangeError, Reported, out_of_date
from picture import Picture, cells, doing, line_agents, located, named, needs_effort, placed, progress, words
from pulls import RepoPulls, parse_pull, stack
from repos import RepoStore
from settings import Settings
from standins import TOKEN, FakeGrange, FakePulls, drive, screen, settle, team, to_setup, until
from test_pulls import node, review
from theme import MUTED

NOW = datetime(2026, 11, 14, 16, 0, tzinfo=timezone.utc)
TIP = "a1" * 20
ASK = lambda *who: {"nodes": [{"requestedReviewer": {"__typename": "User", "login": w}} for w in who]}  # noqa: E731
APPROVED = lambda who: {"nodes": [review(who, "APPROVED", "2026-11-12T10:00:00Z")]}                      # noqa: E731
ASK_TEAM = lambda slug: {"nodes": [{"requestedReviewer": {"__typename": "Team", "slug": slug}}]}        # noqa: E731


def app_nodes():
    return [
        node(98, "proj-86-pr-98-fix-tests", author={"login": "djp3"}, baseRefOid=TIP,
             reviewRequests=ASK("bob")),                                         # waits on bob
        node(102, "proj-91-pr-102", base="proj-86-pr-98-fix-tests", author={"login": "djp3"},
             reviewDecision="APPROVED", latestOpinionatedReviews=APPROVED("bob")),   # to merge, once 98 lands
        node(99, "limits", author={"login": "djp3"}, baseRefOid=TIP, title="PROJ-87: rate limits",
             isDraft=True),                                                      # djp3: finish the draft
        node(7, "proj-12-pr-7-docs", author={"login": "mira"}, baseRefOid=TIP,
             reviewRequests=ASK("djp3")),                                        # waits on djp3
        node(40, "sec-4-bump-a", author={"login": "dependabot"}, baseRefOid=TIP,
             reviewRequests=ASK_TEAM("platform")),                               # waits on a team
    ]


def web_nodes():
    return [node(98, "ops-3-pr-98-deploy", author={"login": "kenji"}, baseRefOid=TIP)]


def read(name, nodes):
    return RepoPulls(name, "main", stack([parse_pull(name, n) for n in nodes], "main"), len(nodes), NOW, TIP)


def picture(*reported, viewer="djp3", teams=None):
    return Picture(["o/app", "o/web"], {"o/app": read("o/app", app_nodes()), "o/web": read("o/web", web_nodes())},
                   viewer, reported=tuple(reported), teams=teams or {})


def where(*reported):
    """(repository, number) for each reported agent that is on a pull request."""
    pic = picture()
    return [(pull.repo, pull.number) for _, pull in located(reported, pic.all())]


def said(pic, repo, number):
    """Column 1 of a pull request, a line at a time: owner | state | waits on."""
    return [" | ".join(plain(cell).rstrip() for cell in row).rstrip()
            for row in cells(pic, pic.find((repo, number)))]


# ---- which pull request an agent is on -------------------------------------------------

@pytest.mark.parametrize("name, found", [
    ("proj-86-pr-98 fix tests", ("proj-86", 98)), ("PROJ-86-PR-98", ("PROJ-86", 98)),
    ("agent-frolic-af-42-pr-0017-01", ("af-42", 17)), ("review of proj_x-9-pr-3", ("proj_x-9", 3)),
    ("proj-86-pr-98 then proj-91-pr-102", ("proj-86", 98)),
    ("fix tests", None), ("pr-98", None), ("proj-pr-98", None), ("proj-86-pr-", None), ("86-pr-98", None),
    ("proj-86 pr-98", None), ("", None)])
def test_a_name_says_its_ticket_and_its_pull_requests_number(name, found):
    assert named(name) == found


def test_the_number_alone_does_not_say_which_repository():
    """Both repositories have a pull request 98: the ticket says which one."""
    assert where(Reported("djp3", "proj-86-pr-98 fix tests", "busy")) == [("o/app", 98)]
    assert where(Reported("kenji", "OPS-3-PR-98", "busy")) == [("o/web", 98)]


def test_a_pull_request_carries_its_ticket_in_its_branch_or_its_title():
    assert where(Reported("djp3", "proj-87-pr-99", "busy")) == [("o/app", 99)]        # "PROJ-87: rate limits"


def test_a_pull_request_that_does_not_carry_the_ticket_is_not_it():
    """Another repository's pull request 7, most likely: one this board does not show."""
    assert where(Reported("bob", "billing-3-pr-7", "busy"), Reported("bob", "proj-1-pr-7", "busy"),
                 Reported("bob", "proj-120-pr-7", "busy"), Reported("bob", "xproj-12-pr-7", "busy")) == []


def test_an_agent_that_names_no_pull_request_here_is_on_none():
    assert where(Reported("djp3", "scratch", "busy"), Reported("djp3", "proj-86-pr-5", "busy")) == []


def test_an_agent_whose_session_has_ended_is_on_nothing():
    assert where(Reported("djp3", "proj-86-pr-98", "dead")) == []


def test_two_pull_requests_that_both_carry_it_are_not_guessed_between():
    both = {"o/app": read("o/app", app_nodes()), "o/web": read("o/web", [node(98, "proj-86-pr-98-too")])}
    pic = Picture(["o/app", "o/web"], both, "djp3")
    assert located([Reported("djp3", "proj-86-pr-98", "busy")], pic.all()) == []


def test_a_repository_that_is_not_shown_has_no_agents():
    pic = Picture(["o/web"], {"o/app": read("o/app", app_nodes()), "o/web": read("o/web", web_nodes())}, "djp3",
                  reported=(Reported("djp3", "proj-86-pr-98", "busy"),))
    assert pic.agents == {}


# ---- who it works for, and its number ---------------------------------------------------

def test_the_authors_agent_works_for_them_and_anyone_elses_reviews():
    pic = picture(Reported("DJP3", "proj-86-pr-98 fix tests", "busy"), Reported("bob", "proj-86-pr-98 review", "busy"))
    assert [(a.operator, a.role, a.serial) for a in pic.on(pic.find(("o/app", 98)))] == [
        ("DJP3", "author", "01"), ("bob", "review", "01")]


def test_agents_keep_their_numbers_whatever_order_they_are_reported_in():
    one, two = Reported("djp3", "proj-86-pr-98 a", "busy"), Reported("djp3", "proj-86-pr-98 b", "idle")
    for order in ((one, two), (two, one)):
        pic = picture(*order, Reported("bob", "proj-86-pr-98", "busy"))
        assert [(a.operator, a.serial, a.note) for a in pic.agents[("o/app", 98)]] == [
            ("djp3", "01", "a"), ("djp3", "02", "b"), ("bob", "01", "")]


@pytest.mark.parametrize("name, said", [
    ("proj-86-pr-98 fix tests", "fix tests"), ("PROJ-86-PR-98: Fix tests ", "Fix tests"),
    ("proj-86-pr-98-fix-tests", "fix-tests"), ("proj-86-pr-98", ""), ("agent-frolic-af-42-pr-0017-01", ""),
    ("review of proj-86-pr-98", ""), ("scratch", "")])
def test_what_a_name_goes_on_to_say_is_what_the_agent_is_at(name, said):
    assert words(name) == said


def test_what_an_agent_is_at_is_said_on_its_line_of_the_list():
    pic = picture(Reported("djp3", "proj-86-pr-98 fix tests", "busy"))
    assert [a.note for a in pic.agents[("o/app", 98)]] == ["fix tests"]


def test_two_sessions_with_one_name_are_two_agents():
    twice = Reported("djp3", "proj-86-pr-98", "busy")
    assert [a.key for a in picture(twice, twice).agents[("o/app", 98)]] == [
        ("djp3", "o/app", 98, "01"), ("djp3", "o/app", 98, "02")]


# ---- what it is doing --------------------------------------------------------------------

def state(owner, name, word, stale=False):
    pic = picture(Reported(owner, name, word, stale))
    return [a.state for agents in pic.agents.values() for a in agents]


def test_an_agent_the_farm_calls_busy_is_working():
    assert state("djp3", "proj-86-pr-98", "busy") == ["working"]


def test_an_agent_that_asked_its_human_something_is_at_its_prompt_wherever_the_pull_request_stands():
    assert state("djp3", "proj-86-pr-98", "waiting") == ["at its prompt"]         # though #98 waits on bob


@pytest.mark.parametrize("word", ["idle", "shell"])
def test_an_idle_agent_is_at_its_prompt_only_when_the_move_is_its_humans(word):
    assert state("djp3", "proj-87-pr-99", word) == ["at its prompt"]              # djp3: finish the draft
    assert state("djp3", "proj-86-pr-98", word) == ["waiting"]                    # it waits on bob
    assert state("bob", "proj-86-pr-98", word) == ["at its prompt"]               # and bob's own is at its prompt
    assert state("djp3", "proj-91-pr-102", word) == ["waiting"]                   # queued behind #98
    assert state("kenji", "proj-12-pr-7", word) == ["waiting"]                    # nobody asked kenji


def test_an_idle_agent_that_reviews_for_a_team_that_was_asked_is_at_its_prompt():
    """Which team its human is on is not known: a team was asked, and it is not the author's."""
    assert state("bob", "sec-4-pr-40", "idle") == ["at its prompt"]
    assert state("dependabot", "sec-4-pr-40", "idle") == ["waiting"]


def test_the_viewers_own_idle_agent_is_at_its_prompt_for_a_team_only_when_they_are_on_it():
    """The yellow on their card says a move is theirs: for a team they are not on, it is not."""
    mine = Reported("djp3", "sec-4-pr-40 review", "idle")                        # #40 is asked of team platform
    off, on = picture(mine), picture(mine, teams={"team/o/platform": "Platform"})
    assert [a.state for a in off.agents[("o/app", 40)]] == ["waiting"] and not off.mine(off.find(("o/app", 40)))
    assert [a.state for a in on.agents[("o/app", 40)]] == ["at its prompt"] and on.mine(on.find(("o/app", 40)))


def test_the_viewers_idle_agent_is_at_its_prompt_where_they_may_act_as_maintainer():
    """An outsider's approved pull request waits on the organization to merge it."""
    def state(access):
        nodes = [node(41, "ops-9-pr-41", author={"login": "wen"}, authorAssociation="CONTRIBUTOR", baseRefOid=TIP,
                      reviewDecision="APPROVED", latestOpinionatedReviews=APPROVED("kenji"))]
        got = RepoPulls("acme/app", "main", stack([parse_pull("acme/app", n) for n in nodes], "main"), 1, NOW, TIP,
                        access=access, org=True)
        pic = Picture(["acme/app"], {"acme/app": got}, "djp3", reported=(Reported("djp3", "ops-9-pr-41", "idle"),))
        return [a.state for a in pic.agents[("acme/app", 41)]]

    assert state("admin") == ["at its prompt"] and state("read") == ["waiting"]


@pytest.mark.parametrize("word", ["unknown", "pondering", "", "BUSY"])
def test_a_word_this_app_does_not_know_is_unknown_and_never_work(word):
    assert state("djp3", "proj-86-pr-98", word) == ["unknown"]


@pytest.mark.parametrize("word", ["busy", "waiting", "idle"])
def test_what_a_farm_gone_quiet_last_said_is_not_what_its_agents_do_now(word):
    assert state("mira", "proj-12-pr-7", word, stale=True) == ["unknown"]


def test_doing_is_asked_of_the_farms_word_and_the_standing_alone():
    pic = picture()
    assert doing(pic, Reported("djp3", "anything", "busy"), pic.find(("o/app", 98)), "author") == "working"


# ---- what it changes on the board -------------------------------------------------------

def test_a_pull_request_an_agent_is_at_work_on_is_moving_and_off_the_list_of_what_needs_effort():
    quiet, busy = picture(), picture(Reported("mira", "proj-12-pr-7 review fixes", "busy"))
    seven = quiet.find(("o/app", 7))
    assert quiet.mine(seven) and ("o/app", 7) in {(e.pull.repo, e.pull.number) for e in needs_effort(quiet, NOW)}
    assert not busy.mine(seven) and ("o/app", 7) not in {(e.pull.repo, e.pull.number) for e in needs_effort(busy, NOW)}
    assert progress(busy, seven)[0] == "in progress"


@pytest.mark.parametrize("word, stale, idle", [("idle", False, 1), ("waiting", False, 1), ("pondering", False, 0),
                                               ("busy", True, 0)])
def test_an_agent_that_is_not_known_to_be_working_takes_nothing_off_the_list(word, stale, idle):
    """And only one known to be idle is counted as waiting for it: of the rest nothing is known."""
    pic = picture(Reported("mira", "proj-12-pr-7", word, stale))
    effort = {(e.pull.repo, e.pull.number): e for e in needs_effort(pic, NOW)}
    assert pic.mine(pic.find(("o/app", 7))) and effort[("o/app", 7)].waiting == idle


def test_each_agent_is_drawn_beneath_its_human_with_what_it_is_doing():
    pic = picture(Reported("djp3", "proj-86-pr-98 a", "busy"), Reported("djp3", "proj-86-pr-98 b", "idle"),
                  Reported("bob", "proj-86-pr-98 review", "waiting"), Reported("mira", "proj-12-pr-7", "busy", True))
    assert said(pic, "o/app", 98) == ["  you |  |   bob", "  └ ⠋ agent 01 | working |   └ ▷ agent 01",
                                      "  └ · agent 02 | waiting |"]
    assert said(pic, "o/app", 7) == ["  mira |  | ● you", "  └ ? agent 01 | unknown |"]


def test_an_agent_whose_human_is_not_waited_on_is_on_the_row_all_the_same():
    """It takes the pull request off the list of what needs effort: the row must say why."""
    pic = picture(Reported("kenji", "proj-12-pr-7 poke", "busy"))                 # #7 waits on djp3; nobody asked kenji
    seven = pic.find(("o/app", 7))
    assert not pic.mine(seven) and said(pic, "o/app", 7) == ["  mira | waiting |   you", " |  |   kenji",
                                                             " |  |   └ ⠋ agent 01"]
    assert [[a.operator for a in line] for line in line_agents(pic, seven)] == [[], [], ["kenji"]]
    assert MUTED in {style for _, style in cells(pic, seven)[1][2]}               # kenji: the faintest ink


def test_a_reviewers_agent_stays_on_the_row_when_the_move_goes_back_to_the_author():
    pic = picture(Reported("bob", "proj-87-pr-99 review", "idle"))                # #99 is djp3's draft to finish
    assert said(pic, "o/app", 99) == ["● you | to act |   bob", " |  |   └ · agent 01"]


def test_an_agent_on_a_pull_request_that_waits_on_the_one_below_is_on_its_own_row():
    pic = picture(Reported("kenji", "proj-91-pr-102", "busy"), Reported("bob", "proj-86-pr-98", "busy"))
    assert said(pic, "o/app", 102) == ["  you | waiting |   #98 →", " |  |   bob", " |  |   └ ⠋ agent 01",
                                       " |  |   kenji", " |  |   └ ⠋ agent 01"]


def test_no_agent_is_drawn_twice_on_a_row():
    pic = picture(Reported("bob", "proj-86-pr-98 review", "busy"), Reported("djp3", "proj-86-pr-98", "busy"))
    drawn = [a.key for line in line_agents(pic, pic.find(("o/app", 98))) for a in line]
    assert sorted(drawn) == sorted(a.key for a in pic.agents[("o/app", 98)]) and len(drawn) == 2


def test_an_agent_whose_state_is_unknown_has_its_place_on_a_card_and_in_the_order():
    unknown = picture(Reported("mira", "proj-12-pr-7", "busy", True)).agents[("o/app", 7)][0]
    assert plain(board.agent_line(unknown, yours=False)) == "? #7"
    assert board.ORDER["unknown"] > board.ORDER["waiting"]


def test_placed_is_what_the_picture_holds():
    reported = (Reported("djp3", "proj-86-pr-98", "busy"),)
    assert picture(*reported).agents == placed(picture(), reported)


# ---- the app: asking, and what it does with the answers --------------------------------

def a_board(port=None):
    keychain.put(keychain.GITHUB_TOKEN, TOKEN)
    store = RepoStore()
    store.add("o/app", admin=True)
    store.add("o/web", admin=True)
    FakePulls.tips = {"o/app": TIP, "o/web": TIP}
    FakePulls.open_ = {"o/app": app_nodes(), "o/web": web_nodes()}
    if port is not None:
        Settings(grange_port=port).save()


THE_TEAM = (("djp3", "proj-86-pr-98 fix tests", "busy"), ("djp3", "scratch", "idle"),
            ("mira", "proj-12-pr-7 docs", "busy"), ("kenji", "billing-3-pr-7", "busy"))


@drive
async def test_the_board_draws_the_agents_the_grange_reports(isolated):
    a_board()
    app = main.Frolic()
    async with app.run_test(size=(170, 46)) as pilot:
        await settle(app, pilot)
        assert app.grange_state == "asking" and "agent 01" not in screen(app)
        FakeGrange.answers.put(team(*THE_TEAM, cursor=4))
        await until(pilot, lambda: app.reported)
        await pilot.pause(0.3)                                   # the board draws five times a second
        assert app.grange_state == "ok" and len(app.reported) == 4
        assert {key: [(a.operator, a.state) for a in agents] for key, agents in app.board.pic.agents.items()} == {
            ("o/app", 98): [("djp3", "working")], ("o/app", 7): [("mira", "working")]}
        drawn = screen(app)
        assert drawn.count("agent 01") >= 2 and "working" in drawn
        assert any(line.split("│")[0].split()[:4] == ["└", line.split("│")[0].split()[1], "agent", "01"]
                   for line in drawn.splitlines() if "agent 01" in line and "│" in line)


@drive
async def test_each_question_carries_the_cursor_of_the_last_answer(isolated):
    a_board()
    app = main.Frolic()
    async with app.run_test(size=(150, 40)) as pilot:
        await settle(app, pilot)
        FakeGrange.answers.put(team(*THE_TEAM, cursor=4))
        await until(pilot, lambda: len(FakeGrange.asked) == 2)
        FakeGrange.answers.put(team(cursor=9))
        await until(pilot, lambda: len(FakeGrange.asked) == 3)
        assert FakeGrange.asked == [(7788, None), (7788, 4), (7788, 9)]


@drive
async def test_a_new_answer_replaces_the_last_whole(isolated):
    a_board()
    app = main.Frolic()
    async with app.run_test(size=(150, 40)) as pilot:
        await settle(app, pilot)
        FakeGrange.answers.put(team(*THE_TEAM, cursor=4))
        await until(pilot, lambda: len(app.reported) == 4)
        FakeGrange.answers.put(team(("mira", "proj-12-pr-7 docs", "idle"), cursor=5))
        await until(pilot, lambda: len(app.reported) == 1)
        await pilot.pause(0.3)
        assert {key: [a.state for a in agents] for key, agents in app.board.pic.agents.items()} == {
            ("o/app", 7): ["waiting"]}


@drive
async def test_the_grange_is_asked_on_the_port_in_the_settings(isolated):
    a_board(port=7790)
    app = main.Frolic()
    async with app.run_test(size=(150, 40)) as pilot:
        await settle(app, pilot)
        await until(pilot, lambda: FakeGrange.asked)
        assert FakeGrange.asked == [(7790, None)]
        await to_setup(app, pilot)
        assert "asking the agent-grange on this Mac (port 7790)…" in screen(app)


@drive
async def test_the_setup_says_what_the_grange_reports(isolated):
    a_board()
    app = main.Frolic()
    async with app.run_test(size=(170, 46)) as pilot:
        await settle(app, pilot)
        FakeGrange.answers.put(team(*THE_TEAM, quiet=("mira",)))
        await until(pilot, lambda: app.reported)
        await to_setup(app, pilot)
        drawn = screen(app)
        assert "4  Agents" in drawn and "✓ answers on 127.0.0.1:7788 as djp3" in drawn
        assert "3 granges · 1 with no word from its agent-farm" in drawn
        assert "4 reported · 2 on the pull requests shown here" in drawn
        assert [said for _, said, _ in app.lines].count("agent-grange answers: 3 granges, 4 agents") == 1


@drive
async def test_a_mac_with_no_grange_says_so_once_and_plainly(isolated):
    a_board()
    app = main.Frolic()
    down = "No agent-grange is running on this Mac (port 7788)."
    async with app.run_test(size=(150, 40)) as pilot:
        await settle(app, pilot)
        for _ in range(3):                                       # it keeps trying: it says so once
            FakeGrange.answers.put(GrangeError("down", down, retry=0.01))
        await until(pilot, lambda: len(FakeGrange.asked) == 4)
        assert (app.grange_state, app.grange_wrong.said) == ("down", down)
        assert [said for _, said, _ in app.lines].count(down) == 1
        assert app.notice_now() == ""                            # nothing is amiss: the board says nothing
        await to_setup(app, pilot)
        assert "none is running on this Mac (port 7788)" in screen(app) and "✗" not in screen(app)


@drive
async def test_a_grange_out_of_date_is_said_on_the_board_and_the_setup_says_how_to_fix_it(isolated):
    a_board()
    app = main.Frolic()
    body = {"summary": "image is 3 commit(s) behind tag 'agent-grange-latest'",
            "fix": "make update   (git pull --ff-only, then make up)"}
    stale = out_of_date(body, retry=0.01).said
    async with app.run_test(size=(170, 46)) as pilot:
        await settle(app, pilot)
        FakeGrange.answers.put(out_of_date(body, retry=0.01))
        FakeGrange.answers.put(out_of_date(body, retry=0.01))
        await until(pilot, lambda: len(FakeGrange.asked) == 3)
        assert app.grange_state == "stale" and [said for _, said, _ in app.lines].count(stale) == 1
        assert app.notice_now() == "agent-grange on this Mac is out of date: s shows how to update it"
        await to_setup(app, pilot)
        drawn = screen(app)
        assert "grange    ✗ out of date: image is 3 commit(s) behind tag 'agent-grange-latest'" in drawn
        assert "to fix    make update (git pull --ff-only, then make up)   in the grange's folder" in drawn
        FakeGrange.answers.put(team(*THE_TEAM))                  # updated: it answers
        await until(pilot, lambda: app.grange_state == "ok")
        assert app.notice_now() == "" and "to fix" not in screen(app)


@drive
async def test_what_was_last_reported_stays_for_a_minute_once_the_grange_stops_answering(isolated):
    a_board()
    app = main.Frolic()
    async with app.run_test(size=(170, 46)) as pilot:
        await settle(app, pilot)
        FakeGrange.answers.put(team(*THE_TEAM))
        await until(pilot, lambda: app.reported)
        FakeGrange.answers.put(GrangeError("trouble", "The agent-grange on this Mac did not answer.", retry=30))
        await until(pilot, lambda: app.grange_state == "trouble")
        app.tick()
        assert len(app.reported) == 4                            # a grange being restarted is back by then
        await to_setup(app, pilot)
        drawn = screen(app)
        assert "✗ The agent-grange on this Mac did not answer." in drawn and "4 reported" in drawn
        assert "as of" in drawn
        app.grange_read_at -= app.GRANGE_GRACE_S + 1             # a minute on
        app.tick()
        await pilot.pause()
        assert app.reported == () and app.grange_reading is None and "4 reported" not in screen(app)
        assert "agent-grange has not answered for a minute: the 4 agents it reported are no longer shown" in [
            said for _, said, _ in app.lines]
        assert app.notice_now() == "agent-grange stopped answering: no agents are shown"


@drive
async def test_a_grange_that_answers_again_within_the_minute_loses_nothing(isolated):
    a_board()
    app = main.Frolic()
    async with app.run_test(size=(150, 40)) as pilot:
        await settle(app, pilot)
        FakeGrange.answers.put(team(*THE_TEAM, cursor=812))
        await until(pilot, lambda: app.reported)
        FakeGrange.answers.put(GrangeError("down", "No agent-grange is running on this Mac (port 7788).", retry=0.01))
        await until(pilot, lambda: app.grange_state == "down")
        await to_setup(app, pilot)                               # one that was answering and is gone is amiss
        assert "✗ No agent-grange is running on this Mac (port 7788)." in screen(app) and "4 reported" in screen(app)
        FakeGrange.answers.put(team(*THE_TEAM, cursor=1))        # restarted: its cursor starts again
        await until(pilot, lambda: app.grange_state == "ok")
        app.grange_read_at -= app.GRANGE_GRACE_S + 1
        app.tick()                                               # however long ago: it is answering
        assert len(app.reported) == 4 and FakeGrange.asked[-2:] == [(7788, None), (7788, 1)]
        assert [said for _, said, _ in app.lines].count("agent-grange answers: 3 granges, 4 agents") == 2


@drive
async def test_leaving_the_app_gives_up_the_question_the_grange_is_holding(isolated):
    a_board()
    app = main.Frolic()
    async with app.run_test(size=(150, 40)) as pilot:
        await settle(app, pilot)
        await until(pilot, lambda: FakeGrange.asked)
        watch = app.watch
        assert watch.is_alive()
    watch.join(5)
    assert not watch.is_alive() and FakeGrange.made[0].closed.is_set()


def test_the_granges_port_is_7788_unless_the_settings_say_otherwise(tmp_path):
    import json
    assert Settings.load().grange_port == 7788
    for written, kept in [(7790, 7790), (0, 7788), (65536, 7788), (-1, 7788), ("7790", 7788), (True, 7788)]:
        (tmp_path / "settings.json").write_text(json.dumps({"grange_port": written}))
        assert Settings.load().grange_port == kept, written
