# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""The team's agents, from what the agent-grange reports to where they are drawn: which
pull request an agent is on, what it is doing there, and how the app keeps asking."""
import time
from dataclasses import replace
from datetime import datetime, timezone

import pytest

import board
import grange
import keychain
import main
from canvas import plain
from grange import GrangeError, Reported, out_of_date
import picture as pictures
from picture import (LOOKS, Agent, Focus, Person, Picture, agent_cell, agent_name, agent_state, astray, cells, doing,
                     early, focuses, line_agents, located, located_in, look, needs_effort, placed, progress, words)
from pulls import RepoPulls, parse_pull, stack
from repos import RepoStore
from settings import Settings
from standins import (TOKEN, FakeClipboard, FakeGrange, FakePulls, age, drive, screen, settle, team, to_setup,
                      until)
from test_pulls import node, review
from theme import MUTED

NOW = datetime(2026, 11, 14, 16, 0, tzinfo=timezone.utc)
TIP = "a1" * 20
ASK = lambda *who: {"nodes": [{"requestedReviewer": {"__typename": "User", "login": w}} for w in who]}  # noqa: E731
APPROVED = lambda who: {"nodes": [review(who, "APPROVED", "2026-11-12T10:00:00Z")]}                      # noqa: E731
ASK_TEAM = lambda slug: {"nodes": [{"requestedReviewer": {"__typename": "Team", "slug": slug}}]}        # noqa: E731


def app_nodes():
    return [
        node(98, "proj-86-fix-tests", author={"login": "djp3"}, baseRefOid=TIP,
             reviewRequests=ASK("bob")),                                         # waits on bob
        node(102, "proj-91-more-tests", base="proj-86-fix-tests", author={"login": "djp3"},
             reviewDecision="APPROVED", latestOpinionatedReviews=APPROVED("bob")),   # to merge, once 98 lands
        node(99, "limits", author={"login": "djp3"}, baseRefOid=TIP, title="PROJ-87: rate limits",
             isDraft=True),                                                      # djp3: finish the draft
        node(7, "proj-12-docs", author={"login": "mira"}, baseRefOid=TIP,
             reviewRequests=ASK("djp3")),                                        # waits on djp3
        node(40, "sec-4-bump-a", author={"login": "dependabot"}, baseRefOid=TIP,
             reviewRequests=ASK_TEAM("platform")),                               # waits on a team
    ]


def web_nodes():
    return [node(98, "ops-3-deploy", author={"login": "kenji"}, baseRefOid=TIP)]


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

@pytest.mark.parametrize("name, read", [
    ("o-app-pr-98 fix tests", [("o-app", 98)]), ("O-App-PR-98", [("o-app", 98)]),
    ("acme-web-pr-0017-01", [("acme-web", 17)]), ("review of o-app-pr-3", [("o-app", 3)]),
    ("o-app-pr-98 then o-web-pr-98", [("o-app", 98)]),               # the first word that says one is the one
    ("fix tests", []), ("pr-98", []), ("-pr-98", []), ("o-app-pr-", []), ("o-app-pr-x9", []),
    ("o-app pr-98", []), ("o-app-pr 98", []), ("", []),
    ("app-pr-98", [("app", 98)]),                                    # it reads: whether it fits is another matter
    ("see:o-app-pr-98", [("see:o-app", 98)]),                        # a word runs from one space to the next
    ("acme-notes-pr-2024-pr-7 tidy", [("acme-notes", 2024), ("acme-notes-pr-2024", 7)])])
def test_a_name_says_a_repository_and_a_pull_requests_number(name, read):
    assert [(one.repo, one.number) for one in pictures.said(name)] == read


def test_the_repository_in_the_name_says_whose_pull_request_98_it_is():
    """Both repositories have a pull request 98."""
    assert where(Reported("djp3", "o-app-pr-98 fix tests", "busy")) == [("o/app", 98)]
    assert where(Reported("kenji", "O-WEB-PR-98", "busy")) == [("o/web", 98)]


def test_a_ticket_in_a_branch_or_a_title_plays_no_part():
    """#98's branch has "proj-86" in it and #99's title "PROJ-87". A name that says a ticket
    and a number is on neither, and one that says the repository needs no ticket."""
    assert where(Reported("djp3", "proj-86-pr-98", "busy"), Reported("djp3", "proj-87-pr-99", "busy")) == []
    assert where(Reported("djp3", "o-app-pr-99", "busy")) == [("o/app", 99)]


def test_a_name_that_does_not_say_whose_repository_is_on_nothing():
    """A teammate's board may show another repository of that name: the owner is never guessed."""
    assert where(Reported("djp3", "app-pr-98", "busy"), Reported("djp3", "web-pr-98", "busy")) == []


def test_the_repository_is_read_from_where_its_word_begins():
    """"my-o-app" is another owner's repository for all the name says, not o/app."""
    assert where(Reported("djp3", "my-o-app-pr-98", "busy"), Reported("djp3", "see:o-app-pr-98", "busy")) == []
    assert where(Reported("djp3", "see: o-app-pr-98", "busy")) == [("o/app", 98)]


def test_an_agent_that_names_no_pull_request_here_is_on_none():
    assert where(Reported("djp3", "scratch", "busy"), Reported("djp3", "o-app-pr-5", "busy"),
                 Reported("djp3", "o-docs-pr-98", "busy")) == []


def test_an_agent_whose_session_has_ended_is_on_nothing():
    assert where(Reported("djp3", "o-app-pr-98", "dead")) == []


def alike(first=5, second=5):
    """Two repositories a name writes the same way ("acme-web-api"), each with one pull request."""
    return {"acme/web-api": read("acme/web-api", [node(first, "a", author={"login": "djp3"}, baseRefOid=TIP)]),
            "acme-web/api": read("acme-web/api", [node(second, "b", author={"login": "kenji"}, baseRefOid=TIP)])}


def test_two_pull_requests_a_name_fits_are_not_guessed_between():
    both = alike()
    pic = Picture(list(both), both, "djp3")
    assert located([Reported("djp3", "acme-web-api-pr-5", "busy")], pic.all()) == []


def test_two_repositories_written_alike_are_told_apart_when_only_one_has_the_number():
    both = alike(5, 6)
    reported = (Reported("djp3", "acme-web-api-pr-5", "busy"), Reported("djp3", "acme-web-api-pr-6", "busy"))
    assert sorted(Picture(list(both), both, "djp3", reported=reported).agents) == [
        ("acme-web/api", 6), ("acme/web-api", 5)]


def test_hiding_one_of_two_a_name_fits_does_not_say_which_was_meant():
    both = alike()
    busy = (Reported("djp3", "acme-web-api-pr-5", "busy"),)
    for shown in (list(both), ["acme/web-api"], ["acme-web/api"]):
        pic = Picture(shown, both, "djp3", reported=busy)
        assert pic.agents == {}, shown
        assert all(pic.mine(pull) == Picture(shown, both, "djp3").mine(pull) for pull in pic.all())


def test_a_repository_that_is_not_shown_has_no_agents():
    pic = Picture(["o/web"], {"o/app": read("o/app", app_nodes()), "o/web": read("o/web", web_nodes())}, "djp3",
                  reported=(Reported("djp3", "o-app-pr-98", "busy"),))
    assert pic.agents == {}


def test_a_repository_with_pr_and_a_number_in_its_own_name_has_its_agents():
    """Its word reads two ways, and only one of them is a pull request that is there."""
    pulls = {"acme/notes-pr-2024": read("acme/notes-pr-2024", [node(7, "x", author={"login": "djp3"})])}
    pic = Picture(list(pulls), pulls, "djp3", reported=(Reported("djp3", "acme-notes-pr-2024-pr-7 tidy", "busy"),))
    assert [(a.number, a.note) for a in pic.agents[("acme/notes-pr-2024", 7)]] == [(7, "tidy")]


def test_the_name_to_use_puts_an_agent_on_its_pull_request():
    pic = picture()
    assert agent_name(pic.find(("o/app", 98))) == "o-app-pr-98"
    assert agent_name(parse_pull("Acme/Web.Console", node(3, "x"))) == "Acme-Web.Console-pr-3"    # as GitHub spells it
    for pull in pic.all():
        one = Reported("bob", agent_name(pull), "busy")
        assert located([one], pic.all()) == [(one, pull)]


# ---- why an agent is on none --------------------------------------------------------------

def lost(*names, shown=("o/app", "o/web"), pulls=None, state="busy"):
    """(name, why) for each of djp3's agents so named that tried to say a pull request and
    is on none. o/docs is on the list too, and has never been read."""
    if pulls is None:
        pulls = {"o/app": read("o/app", app_nodes()), "o/web": read("o/web", web_nodes())}
    reported = [Reported("djp3", name, state) for name in names]
    return [(one.one.name, one.why) for one in astray(reported, shown, pulls, ["o/app", "o/web", "o/docs"])]


def test_a_name_that_tried_and_is_on_nothing_is_given_with_why():
    assert lost("o-app-pr-5 fix", "app-pr-98", "o-tools-pr-3", "proj-86-pr-98") == [
        ("o-app-pr-5 fix", "o/app has no open pull request 5"),
        ("app-pr-98", "say whose repository: o-app-pr-98"),
        ("o-tools-pr-3", "names no repository on your list"),
        ("proj-86-pr-98", "names no repository on your list")]


def test_an_agent_that_is_where_it_says_or_says_nothing_is_not_astray():
    assert lost("o-app-pr-98", "O-Web-PR-98 deploy", "scratch", "pr-98") == []
    assert lost("o-app-pr-5", state="dead") == []                                 # its session has ended


def test_an_agent_of_a_hidden_repository_is_hidden_with_it_and_not_astray():
    assert lost("o-app-pr-98", "o-app-pr-5", shown=("o/web",)) == []


def test_a_repository_not_read_yet_may_have_the_pull_request_for_all_that_is_known():
    assert lost("o-docs-pr-3") == []                                              # on the list, never read
    only = {"o/web": read("o/web", web_nodes())}
    assert lost("o-app-pr-98", pulls=only) == []                                  # shown, and its read still to come


def test_a_name_that_fits_two_pull_requests_is_a_tie_and_says_which():
    both, one = alike(), [Reported("djp3", "acme-web-api-pr-5", "busy")]
    assert [(got.why, got.tie) for got in astray(one, list(both), both, list(both))] == [
        ("fits acme/web-api #5 and acme-web/api #5", "pull requests")]
    hidden = astray(one, ["acme/web-api"], both, list(both))                      # hiding one settles nothing
    assert [got.tie for got in hidden] == ["pull requests"]
    assert astray(one, [], both, list(both)) == []                                # both hidden: it is hidden with them


def test_a_name_without_its_owner_is_told_every_repository_it_could_mean():
    both, one = alike(), [Reported("djp3", "api-pr-5", "busy")]
    assert [got.why for got in astray(one, list(both), both, ["acme-web/api", "bob/api"])] == [
        "say whose repository: acme-web-api-pr-5 or bob-api-pr-5"]


def test_every_agent_whose_name_tries_is_placed_or_astray_and_never_both():
    reported = [Reported("djp3", name, "busy") for name in (
        "o-app-pr-98", "o-app-pr-5", "app-pr-98", "o-tools-pr-3", "scratch", "o-web-pr-98 deploy")]
    pulls = {"o/app": read("o/app", app_nodes()), "o/web": read("o/web", web_nodes())}
    on = [one.name for one, _ in located_in(reported, ["o/app", "o/web"], pulls)]
    off = [one.one.name for one in astray(reported, ["o/app", "o/web"], pulls)]
    assert sorted(on + off) == sorted(one.name for one in reported if pictures.said(one.name))
    assert not set(on) & set(off)


# ---- an agent that has no pull request yet -------------------------------------------------

def early_in(*reported, shown=("o/app", "o/web"), listed=("o/app", "o/web", "o/docs")):
    """(name, repository, what the name goes on to say) for each agent at work with no pull
    request yet. o/docs is on the list too, and hidden."""
    return [(one.name, repo, one.name[end:]) for one, repo, end in early(reported, shown, listed)]


def test_an_agent_with_no_pull_request_yet_says_its_repository_as_a_word_of_its_own():
    names = ("o-app reflow fix", "O-App", "notes on o-web deploy", "o-app o-web")
    assert early_in(*(Reported("djp3", name, "busy") for name in names)) == [
        ("o-app reflow fix", "o/app", " reflow fix"), ("O-App", "o/app", ""),
        ("notes on o-web deploy", "o/web", " deploy"), ("o-app o-web", "o/app", " o-web")]    # the first such word


def test_a_repository_run_on_with_other_words_is_not_read_as_one():
    """Where the repository's name ends in "o-app-reflow-fix" cannot be told without a guess."""
    names = ("o-app-reflow-fix", "my-o-app", "app", "o-app:", "o/app", "scratch", "")
    assert early_in(*(Reported("djp3", name, "busy") for name in names)) == []


def test_a_name_that_says_a_pull_request_is_read_that_way_alone():
    """"o-app o-app-pr-5" says pull request 5, which is not open: the agent is not at work in
    o/app with none, it is astray."""
    said = (Reported("djp3", "o-app o-app-pr-5", "busy"), Reported("djp3", "o-app-pr-98", "busy"))
    assert early_in(*said) == []
    assert [(a.number, a.early) for agents in picture(*said).agents.values() for a in agents] == [(98, False)]
    assert lost("o-app o-app-pr-5") == [("o-app o-app-pr-5", "o/app has no open pull request 5")]


def test_an_agent_with_none_yet_is_put_only_in_a_repository_that_is_shown():
    assert early_in(Reported("djp3", "o-app", "busy"), shown=("o/web",)) == []            # hidden with it
    assert early_in(Reported("djp3", "o-docs", "busy")) == []                             # on the list, and hidden
    assert early_in(Reported("djp3", "o-tools", "busy")) == []                            # not on the list at all
    assert early_in(Reported("djp3", "o-app", "dead")) == []                              # its session has ended
    assert lost("o-app", "o-docs", "o-tools", shown=("o/web",)) == []                     # and none of them is astray


def test_an_agent_is_not_put_in_one_of_two_repositories_written_alike_and_is_said_to_fit_both():
    both, one = alike(), [Reported("djp3", "acme-web-api reflow", "busy")]
    for shown in (list(both), ["acme/web-api"], ["acme-web/api"]):
        assert early(one, shown, list(both)) == [], shown                                 # hiding one does not say which
        assert [(got.why, got.tie) for got in astray(one, shown, both, list(both))] == [
            ("fits acme/web-api and acme-web/api", "repositories")], shown
    assert astray(one, [], both, list(both)) == []                                        # both hidden: hidden with them
    assert Picture(list(both), both, "djp3", reported=tuple(one)).agents == {}
    # The other may be hidden and never read: the picture knows of it from the list.
    only = {"acme/web-api": both["acme/web-api"]}
    assert Picture(["acme/web-api"], only, "djp3", reported=tuple(one), listed=tuple(both)).agents == {}
    assert list(Picture(["acme/web-api"], only, "djp3", reported=tuple(one)).agents) == [("acme/web-api", 0)]


def test_an_agent_with_none_yet_is_kept_under_its_repository_and_the_number_nought():
    pic = picture(Reported("djp3", "o-app reflow fix", "idle"), Reported("djp3", "o-app", "busy"),
                  Reported("bob", "o-web deploy", "waiting"), Reported("bob", "o-web", "idle", True),
                  Reported("djp3", "o-app-pr-98", "busy"))
    assert [(a.operator, a.serial, a.number, a.role, a.state, a.note, a.early) for a in pic.early("O/App")] == [
        ("djp3", "01", 0, "author", "working", "", True),
        ("djp3", "02", 0, "author", "at its prompt", "reflow fix", True)]
    assert [(a.serial, a.state, a.note) for a in pic.early("o/web")] == [
        ("01", "unknown", ""), ("02", "at its prompt", "deploy")]
    assert [(a.serial, a.early) for a in pic.agents[("o/app", 98)]] == [("01", False)]    # numbered on its own there
    assert pic.on(pic.find(("o/app", 98))) == pic.agents[("o/app", 98)]                   # and no pull request's agents


@pytest.mark.parametrize("word, stale, state", [
    ("busy", False, "working"), ("waiting", False, "at its prompt"), ("idle", False, "at its prompt"),
    ("shell", False, "at its prompt"), ("dozing", False, "unknown"), ("busy", True, "unknown"), ("idle", True, "unknown")])
def test_an_idle_agent_with_none_yet_is_at_its_prompt_since_it_waits_on_nobody_else(word, stale, state):
    assert [a.state for a in picture(Reported("bob", "o-app", word, stale)).early("o/app")] == [state]


def test_an_agent_with_none_yet_keeps_its_number_as_the_others_do():
    numbers = {}

    def numbered_in(*names):
        pic = Picture(["o/app"], {"o/app": read("o/app", app_nodes())}, "djp3", numbers=numbers,
                      reported=tuple(Reported("djp3", f"o-app {name}", "busy") for name in names))
        return {agent.note: agent.serial for agent in pic.early("o/app")}

    assert numbered_in("a", "b") == {"a": "01", "b": "02"}
    assert numbered_in("b") == {"b": "02"} and list(numbers) == [(("o/app", 0), "djp3")]  # a has ended: b keeps its own
    assert numbered_in() == {} and numbers == {}


def test_an_agent_with_none_yet_has_a_row_under_its_repository_before_its_branches():
    pic = picture(Reported("djp3", "o-app reflow", "busy"), Reported("bob", "o-web", "busy"))
    kinds = [(row.kind, row.repo) for row in pictures.tree(pic)]
    assert kinds[:3] == [("repo", "o/app"), ("agent", "o/app"), ("base", "o/app")]
    assert kinds[kinds.index(("repo", "o/web")) + 1] == ("agent", "o/web")
    assert [row.agent.note for row in pictures.tree(pic) if row.kind == "agent"] == ["reflow", ""]
    unread = Picture(["o/docs"], {}, "djp3", reported=(Reported("djp3", "o-docs", "busy"),))
    assert [row.kind for row in pictures.tree(unread)] == ["repo", "note", "agent"]       # a repository not read yet
    assert [row.kind for row in pictures.tree(picture())].count("agent") == 0


def test_the_viewers_own_agent_with_none_yet_at_its_prompt_is_theirs_to_get_moving():
    reported = (Reported("djp3", "o-web hotfix", "idle"), Reported("bob", "o-web deploy", "idle"),
                Reported("djp3", "o-web", "busy"))
    before, pic = picture(), picture(*reported)
    assert [(a.operator, a.note) for a in pictures.asking(pic)] == [("djp3", "hotfix")]   # not bob's, nor one at work
    yours, others = pictures.counts(before, "o/web")
    assert pictures.counts(pic, "o/web") == (yours + 1, others)                           # counted as theirs there,
    assert pictures.counts(pic, "o/app") == pictures.counts(before, "o/app")              # and nowhere else
    assert [key for key in map(lambda e: (e.pull.repo, e.pull.number), needs_effort(pic))] == [
        (e.pull.repo, e.pull.number) for e in needs_effort(before)]                       # which are pull requests
    only = {"o/web": read("o/web", web_nodes())}                                          # nothing there waits on them
    assert not pictures.humans(Picture(["o/web"], only, "djp3"))[0].dot
    assert pictures.humans(Picture(["o/web"], only, "djp3", reported=reported))[0].dot    # but their agent does


def test_an_agent_with_none_yet_is_a_stop_of_the_focus_and_in_focus_with_its_human():
    pic = picture(Reported("bob", "o-web deploy", "busy"))
    agent, = pic.early("o/web")
    stop = Focus("agent", "bob", agent.key)
    assert stop in focuses(pic)
    assert all(pictures.agent_in_focus(agent, focus) for focus in (Focus(), Focus("crew", "bob"), stop))
    assert not any(pictures.agent_in_focus(agent, focus) for focus in (
        Focus("human", "bob"), Focus("crew", "djp3"), Focus("agent", "bob", ("bob", "o/web", 0, "another"))))
    assert not any(pictures.in_focus(pic, pull, stop) for pull in pic.all())              # it is on no pull request


def test_a_pull_request_is_newly_opened_only_when_it_was_created_since_the_reading_before():
    """One that was not in the reading before may have been open all along: a repository with
    many open is read only as far as its most recent, and an older one comes into that when
    another closes."""
    before = Picture(["o/web"], {"o/web": read("o/web", web_nodes())}, "djp3")       # taken at NOW: the 14th, 16:00

    def opened(created):
        nodes = web_nodes() + [node(31, "hotfix", author={"login": "djp3"}, createdAt=created)]
        change, = pictures.changes(before, Picture(["o/web"], {"o/web": read("o/web", nodes)}, "djp3"), "o/web")
        return change.opened

    assert opened("2026-11-14T16:05:00Z")                    # since that reading was taken
    assert opened("2026-11-14T15:58:30Z")                    # a moment before it, by a clock that disagrees a little:
    assert not opened("2026-11-14T15:57:00Z")                # it was not in that reading, so it is new all the same
    assert not opened("2026-01-05T09:00:00Z")                # open all along
    assert not opened(None)                                  # GitHub did not say when: not taken for new
    assert pictures.CLOCKS_APART.total_seconds() == 120


# ---- who it works for, and its number ---------------------------------------------------

def test_the_authors_agent_works_for_them_and_anyone_elses_reviews():
    pic = picture(Reported("DJP3", "o-app-pr-98 fix tests", "busy"), Reported("bob", "o-app-pr-98 review", "busy"))
    assert [(a.operator, a.role, a.serial) for a in pic.on(pic.find(("o/app", 98)))] == [
        ("DJP3", "author", "01"), ("bob", "review", "01")]


def test_agents_keep_their_numbers_whatever_order_they_are_reported_in():
    one, two = Reported("djp3", "o-app-pr-98 a", "busy"), Reported("djp3", "o-app-pr-98 b", "idle")
    for order in ((one, two), (two, one)):
        pic = picture(*order, Reported("bob", "o-app-pr-98", "busy"))
        assert [(a.operator, a.serial, a.note) for a in pic.agents[("o/app", 98)]] == [
            ("djp3", "01", "a"), ("djp3", "02", "b"), ("bob", "01", "")]


@pytest.mark.parametrize("name, said", [
    ("o-app-pr-98 fix tests", "fix tests"), ("O-APP-PR-98: Fix tests ", "Fix tests"),
    ("o-app-pr-98-fix-tests", "fix-tests"), ("o-app-pr-98", ""), ("acme-web-pr-0017-01", ""),
    ("review of o-app-pr-98", ""), ("scratch", "")])
def test_what_a_name_goes_on_to_say_is_what_the_agent_is_at(name, said):
    assert words(name) == said


def test_what_an_agent_is_at_is_said_on_its_line_of_the_list():
    pic = picture(Reported("djp3", "o-app-pr-98 fix tests", "busy"))
    assert [a.note for a in pic.agents[("o/app", 98)]] == ["fix tests"]


def test_two_sessions_with_one_name_are_two_agents():
    twice = Reported("djp3", "o-app-pr-98", "busy")
    assert [(a.serial, a.key) for a in picture(twice, twice).agents[("o/app", 98)]] == [
        ("01", ("djp3", "o/app", 98, "o-app-pr-98")), ("02", ("djp3", "o/app", 98, "o-app-pr-98\x002"))]


def test_no_two_agents_are_known_by_the_same_thing_whatever_they_are_named():
    """The second of two sessions with one name was known as "name ·2", and a third session
    may be named exactly that: two agents with one key, and tab could not step past them."""
    named = ("o-app-pr-98", "o-app-pr-98", "o-app-pr-98 ·2", "o-app-pr-98 2", "o-app-pr-98 ·2")
    pic = picture(*(Reported("djp3", name, "busy") for name in named))
    agents = pic.agents[("o/app", 98)]
    assert len(agents) == 5 and len({a.key for a in agents}) == 5
    steps = [f for f in focuses(pic) if f.kind == "agent"]
    assert len(steps) == len(set(steps)) == 5                                     # each a stop of its own


def test_an_agent_is_known_by_its_name_when_one_before_it_ends_so_a_focus_on_it_stays():
    a, b, c = (Reported("djp3", f"o-app-pr-98 {letter}", "busy") for letter in "abc")
    before, after = picture(a, b, c), picture(b, c)                               # a ends
    was = {agent.note: agent for agent in before.agents[("o/app", 98)]}
    now = {agent.note: agent for agent in after.agents[("o/app", 98)]}
    assert (was["b"].serial, now["b"].serial) == ("02", "01")                     # its place moves up
    assert was["b"].key == now["b"].key and was["c"].key == now["c"].key          # what it is known by does not
    held = Focus("agent", "djp3", was["b"].key)
    assert held in focuses(before) and held in focuses(after)
    assert Focus("agent", "djp3", was["a"].key) not in focuses(after)             # the one that ended is let go


def numbered(numbers, *names):
    """{what its name goes on to say: its number} for djp3's sessions on #98, built with `numbers`."""
    pic = Picture(["o/app"], {"o/app": read("o/app", app_nodes())}, "djp3", numbers=numbers,
                  reported=tuple(Reported("djp3", f"o-app-pr-98 {name}", "busy") for name in names))
    return {agent.note: agent.serial for agent in pic.agents.get(("o/app", 98), [])}


def test_an_agent_keeps_its_number_for_as_long_as_its_session_is_reported():
    """Numbered by place alone, agent 02 became agent 01 the moment the one before it ended."""
    numbers = {}
    assert numbered(numbers, "a", "b", "c") == {"a": "01", "b": "02", "c": "03"}
    assert numbered(numbers, "b", "c") == {"b": "02", "c": "03"}                  # a has ended: nothing moves
    assert numbered(numbers, "c", "b") == {"b": "02", "c": "03"}                  # in whatever order they come
    assert numbered(numbers, "aa", "b", "c") == {"aa": "01", "b": "02", "c": "03"}    # a new one takes what is free
    assert numbered(numbers, "aa", "c", "d") == {"aa": "01", "c": "03", "d": "02"}
    assert numbered(numbers, "c") == {"c": "03"}
    assert numbered(numbers, "a", "c") == {"a": "01", "c": "03"}                  # a is back: as a new one is


def test_numbers_are_forgotten_with_the_agents_that_had_them():
    numbers = {}
    numbered(numbers, "a", "b")
    assert list(numbers) == [(("o/app", 98), "djp3")]
    assert numbered(numbers, "b") == {"b": "02"} and numbers[(("o/app", 98), "djp3")] == {"o-app-pr-98 b": 2}
    Picture(["o/app"], {"o/app": read("o/app", app_nodes())}, "djp3", numbers=numbers,
            reported=(Reported("mira", "o-app-pr-7", "busy"),))              # djp3 has none on #98 any more
    assert list(numbers) == [(("o/app", 7), "mira")]
    assert numbered(numbers, "b") == {"b": "01"}                                  # so b is numbered afresh


def test_when_nobody_is_reported_every_number_is_free_again():
    """The numbers were brought up to date only when there was someone to place: with
    nobody, they stayed as they were, and b came back alone as agent 02."""
    numbers = {}
    assert numbered(numbers, "a", "b") == {"a": "01", "b": "02"}
    assert numbered(numbers) == {} and numbers == {}                              # both have ended
    assert numbered(numbers, "b") == {"b": "01"}


def test_without_numbers_kept_agents_are_numbered_by_name_each_time():
    assert numbered(None, "b", "c") == {"b": "01", "c": "02"}


def test_two_sessions_with_one_name_do_not_move_on_screen_when_they_trade_what_they_do():
    busy, idle = Reported("djp3", "o-app-pr-98", "busy"), Reported("djp3", "o-app-pr-98", "idle")
    one, other = picture(busy, idle).agents[("o/app", 98)], picture(idle, busy).agents[("o/app", 98)]
    assert [(a.serial, a.state, a.key) for a in one] == [(a.serial, a.state, a.key) for a in other]


# ---- what it is doing --------------------------------------------------------------------

def state(owner, name, word, stale=False):
    pic = picture(Reported(owner, name, word, stale))
    return [a.state for agents in pic.agents.values() for a in agents]


def test_an_agent_the_farm_calls_busy_is_working():
    assert state("djp3", "o-app-pr-98", "busy") == ["working"]


def test_an_agent_that_asked_its_human_something_is_at_its_prompt_wherever_the_pull_request_stands():
    assert state("djp3", "o-app-pr-98", "waiting") == ["at its prompt"]         # though #98 waits on bob


@pytest.mark.parametrize("word", ["idle", "shell"])
def test_an_idle_agent_is_at_its_prompt_only_when_the_move_is_its_humans(word):
    assert state("djp3", "o-app-pr-99", word) == ["at its prompt"]              # djp3: finish the draft
    assert state("djp3", "o-app-pr-98", word) == ["waiting"]                    # it waits on bob
    assert state("bob", "o-app-pr-98", word) == ["at its prompt"]               # and bob's own is at its prompt
    assert state("djp3", "o-app-pr-102", word) == ["waiting"]                   # queued behind #98
    assert state("kenji", "o-app-pr-7", word) == ["waiting"]                    # nobody asked kenji


def test_an_idle_agent_that_reviews_for_a_team_that_was_asked_is_at_its_prompt():
    """Which team its human is on is not known: a team was asked, and it is not the author's."""
    assert state("bob", "o-app-pr-40", "idle") == ["at its prompt"]
    assert state("dependabot", "o-app-pr-40", "idle") == ["waiting"]


def test_the_viewers_own_idle_agent_is_at_its_prompt_for_a_team_only_when_they_are_on_it():
    """The yellow on their card says a move is theirs: for a team they are not on, it is not."""
    mine = Reported("djp3", "o-app-pr-40 review", "idle")                        # #40 is asked of team platform
    off, on = picture(mine), picture(mine, teams={"team/o/platform": "Platform"})
    assert [a.state for a in off.agents[("o/app", 40)]] == ["waiting"] and not off.mine(off.find(("o/app", 40)))
    assert [a.state for a in on.agents[("o/app", 40)]] == ["at its prompt"] and on.mine(on.find(("o/app", 40)))


def test_the_viewers_idle_agent_is_at_its_prompt_where_they_may_act_as_maintainer():
    """An outsider's approved pull request waits on the organization to merge it."""
    def state(access):
        nodes = [node(41, "ops-9-hotfix", author={"login": "wen"}, authorAssociation="CONTRIBUTOR", baseRefOid=TIP,
                      reviewDecision="APPROVED", latestOpinionatedReviews=APPROVED("kenji"))]
        got = RepoPulls("acme/app", "main", stack([parse_pull("acme/app", n) for n in nodes], "main"), 1, NOW, TIP,
                        access=access, org=True)
        pic = Picture(["acme/app"], {"acme/app": got}, "djp3", reported=(Reported("djp3", "acme-app-pr-41", "idle"),))
        return [a.state for a in pic.agents[("acme/app", 41)]]

    assert state("admin") == ["at its prompt"] and state("read") == ["waiting"]


@pytest.mark.parametrize("word", ["unknown", "pondering", "", "BUSY"])
def test_a_word_this_app_does_not_know_is_unknown_and_never_work(word):
    assert state("djp3", "o-app-pr-98", word) == ["unknown"]


@pytest.mark.parametrize("word", ["busy", "waiting", "idle"])
def test_what_a_farm_gone_quiet_last_said_is_not_what_its_agents_do_now(word):
    assert state("mira", "o-app-pr-7", word, stale=True) == ["unknown"]


def test_doing_is_asked_of_the_farms_word_and_the_standing_alone():
    pic = picture()
    assert doing(pic, Reported("djp3", "anything", "busy"), pic.find(("o/app", 98)), "author") == "working"


# ---- what it changes on the board -------------------------------------------------------

def test_a_pull_request_an_agent_is_at_work_on_is_moving_and_off_the_list_of_what_needs_effort():
    quiet, busy = picture(), picture(Reported("mira", "o-app-pr-7 review fixes", "busy"))
    seven = quiet.find(("o/app", 7))
    assert quiet.mine(seven) and ("o/app", 7) in {(e.pull.repo, e.pull.number) for e in needs_effort(quiet, NOW)}
    assert not busy.mine(seven) and ("o/app", 7) not in {(e.pull.repo, e.pull.number) for e in needs_effort(busy, NOW)}
    assert progress(busy, seven)[0] == "in progress"


@pytest.mark.parametrize("word, stale, idle", [("idle", False, 1), ("waiting", False, 1), ("pondering", False, 0),
                                               ("busy", True, 0)])
def test_an_agent_that_is_not_known_to_be_working_takes_nothing_off_the_list(word, stale, idle):
    """And only one known to be idle is counted as waiting for it: of the rest nothing is known."""
    pic = picture(Reported("mira", "o-app-pr-7", word, stale))
    effort = {(e.pull.repo, e.pull.number): e for e in needs_effort(pic, NOW)}
    assert pic.mine(pic.find(("o/app", 7))) and effort[("o/app", 7)].waiting == idle


def test_each_agent_is_drawn_beneath_its_human_with_what_it_is_doing():
    pic = picture(Reported("djp3", "o-app-pr-98 a", "busy"), Reported("djp3", "o-app-pr-98 b", "idle"),
                  Reported("bob", "o-app-pr-98 review", "waiting"), Reported("mira", "o-app-pr-7", "busy", True))
    assert said(pic, "o/app", 98) == ["  you |  |   bob", "  └ ⠋ agent 01 | working |   └ ▷ agent 01",
                                      "  └ · agent 02 | waiting |"]
    assert said(pic, "o/app", 7) == ["  mira |  | ● you", "  └ ? agent 01 | unknown |"]


def test_an_agent_whose_human_is_not_waited_on_is_on_the_row_all_the_same():
    """It takes the pull request off the list of what needs effort: the row must say why."""
    pic = picture(Reported("kenji", "o-app-pr-7 poke", "busy"))                 # #7 waits on djp3; nobody asked kenji
    seven = pic.find(("o/app", 7))
    assert not pic.mine(seven) and said(pic, "o/app", 7) == ["  mira | waiting |   you", " |  |   kenji",
                                                             " |  |   └ ⠋ agent 01"]
    assert [[a.operator for a in line] for line in line_agents(pic, seven)] == [[], [], ["kenji"]]
    assert MUTED in {style for _, style in cells(pic, seven)[1][2]}               # kenji: the faintest ink


def test_a_reviewers_agent_stays_on_the_row_when_the_move_goes_back_to_the_author():
    pic = picture(Reported("bob", "o-app-pr-99 review", "idle"))                # #99 is djp3's draft to finish
    assert said(pic, "o/app", 99) == ["● you | to act |   bob", " |  |   └ · agent 01"]


def test_an_agent_on_a_pull_request_that_waits_on_the_one_below_is_on_its_own_row():
    pic = picture(Reported("kenji", "o-app-pr-102", "busy"), Reported("bob", "o-app-pr-98", "busy"))
    assert said(pic, "o/app", 102) == ["  you | waiting |   #98 →", " |  |   bob", " |  |   └ ⠋ agent 01",
                                       " |  |   kenji", " |  |   └ ⠋ agent 01"]


def test_no_agent_is_drawn_twice_on_a_row():
    pic = picture(Reported("bob", "o-app-pr-98 review", "busy"), Reported("djp3", "o-app-pr-98", "busy"))
    drawn = [a.key for line in line_agents(pic, pic.find(("o/app", 98))) for a in line]
    assert sorted(drawn) == sorted(a.key for a in pic.agents[("o/app", 98)]) and len(drawn) == 2


def test_an_agent_whose_state_is_unknown_has_its_place_on_a_card_and_in_the_order():
    unknown = picture(Reported("mira", "o-app-pr-7", "busy", True)).agents[("o/app", 7)][0]
    assert plain(board.agent_line(unknown, yours=False)) == "? #7"
    assert look(unknown).order > LOOKS["waiting"].order


def test_every_state_is_drawn_from_the_one_table_wherever_it_is_drawn():
    """Its mark, its word and its place on a card were written out in five places."""
    pic = picture()
    for state, its in LOOKS.items():
        agent = Agent("djp3", "01", "o/app", 98, "author", state)
        mark = its.mark or "⠋"
        assert plain(agent_cell(pic, agent)) == f"  └ {mark} agent 01" and plain(agent_state(pic, agent)) == its.word
        assert plain(board.agent_line(agent, yours=True)) == f"{mark} #98"
    card = board.make_card(Person("djp3", "you", agents=[Agent("djp3", f"{n:02d}", "o/app", n, "author", state)
                                                         for n, state in enumerate(reversed(list(LOOKS)), 1)]), 1)
    assert [plain(line).split()[0] for line, _ in card.cols[0]] == ["▷", "⠋", "·", "?"]   # in the table's order


def test_a_state_with_no_look_of_its_own_is_drawn_as_unknown_and_never_fails():
    """A drawing that fails is swallowed where the screen is drawn: the board would stand
    still, with nothing to say why."""
    pic, odd = picture(), Agent("djp3", "01", "o/app", 98, "author", "dozing")
    assert plain(agent_cell(pic, odd)) == "  └ ? agent 01" and plain(agent_state(pic, odd)) == "unknown"
    assert plain(board.agent_line(odd, yours=False)) == "? #98" and look(odd) is LOOKS["unknown"]
    board.make_card(Person("djp3", "you", agents=[odd]), 1)


@drive
async def test_a_state_with_no_look_of_its_own_does_not_stop_the_board(isolated, monkeypatch):
    monkeypatch.setattr(pictures, "doing", lambda picture, one, pull, role: "dozing")
    a_board()
    app = main.Frolic()
    async with app.run_test(size=(170, 46)) as pilot:
        await settle(app, pilot)
        FakeGrange.answers.put(team(("djp3", "o-app-pr-98 fix tests", "busy")))
        await until(pilot, lambda: app.reported)
        for _ in range(3):
            await pilot.press("down")                            # to #98, so that the drawer has it
            await pilot.pause(0.1)
            if app.board.sel == ("o/app", 98):
                break
        await pilot.pause(0.3)
        drawn = screen(app)
        assert "└ ? agent 01" in drawn and "? #98" in drawn and "you › ? agent 01 dozing" in drawn


def test_a_picture_is_given_its_agents_or_the_reports_they_come_from_not_both():
    """Given both it kept the second and dropped the first, without a word."""
    reported = (Reported("djp3", "o-app-pr-98", "busy"),)
    given = picture(*reported).agents
    with pytest.raises(ValueError, match="not both"):
        Picture(["o/app"], {"o/app": read("o/app", app_nodes())}, "djp3", given, reported=reported)
    assert Picture(["o/app"], {"o/app": read("o/app", app_nodes())}, "djp3", given).agents == given


def test_the_setups_tally_and_the_picture_go_by_the_same_rule():
    reported = (Reported("djp3", "o-app-pr-98", "busy"), Reported("kenji", "o-web-pr-98", "busy"),
                Reported("djp3", "scratch", "idle"))
    pulls = {"o/app": read("o/app", app_nodes()), "o/web": read("o/web", web_nodes())}
    for names in (["o/app", "o/web"], ["o/app"], ["O/Web"], []):
        pic = Picture(names, pulls, "djp3", reported=reported)
        assert len(located_in(reported, names, pulls)) == sum(len(agents) for agents in pic.agents.values()), names


def test_placed_is_what_the_picture_holds():
    reported = (Reported("djp3", "o-app-pr-98", "busy"),)
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


def watch_clock(monkeypatch) -> list:
    """The clock the watch counts a silent grange's minute on, in the test's hands: `[0]` is
    the time by it. (The watch still waits between its tries by the real one.)"""
    clock = [1000.0]
    monkeypatch.setattr(grange, "now", lambda: clock[0])
    return clock


def failing(kind: str, said: str, **more) -> None:
    """From now on every question to the grange fails so, and is asked again at once."""
    FakeGrange.failing = GrangeError(kind, said, retry=0.01, **more)


def ticker(app) -> str:
    """The board's ticker line, as drawn (the line above the hints)."""
    return screen(app).splitlines()[-2].rstrip()


THE_TEAM = (("djp3", "o-app-pr-98 fix tests", "busy"), ("djp3", "scratch", "idle"),
            ("mira", "o-app-pr-7 docs", "busy"), ("kenji", "o-billing-pr-7", "busy"))


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
        FakeGrange.answers.put(team(("mira", "o-app-pr-7 docs", "idle"), cursor=5))
        await until(pilot, lambda: len(app.reported) == 1)
        await pilot.pause(0.3)
        assert {key: [a.state for a in agents] for key, agents in app.board.pic.agents.items()} == {
            ("o/app", 7): ["waiting"]}


@drive
async def test_on_the_board_an_agent_keeps_its_number_when_the_one_before_it_ends(isolated):
    a_board()
    app = main.Frolic()
    mine = [("djp3", "o-app-pr-98 a", "busy"), ("djp3", "o-app-pr-98 b", "busy")]
    async with app.run_test(size=(150, 40)) as pilot:
        await settle(app, pilot)
        FakeGrange.answers.put(team(*mine, cursor=4))
        await until(pilot, lambda: len(app.reported) == 2)
        await pilot.pause(0.3)
        assert [(a.note, a.serial) for a in app.board.pic.agents[("o/app", 98)]] == [("a", "01"), ("b", "02")]
        FakeGrange.answers.put(team(mine[1], cursor=5))          # a has ended
        await until(pilot, lambda: len(app.reported) == 1)
        await pilot.pause(0.3)
        assert [(a.note, a.serial) for a in app.board.pic.agents[("o/app", 98)]] == [("b", "02")]
        assert "agent 02" in screen(app) and "agent 01" not in screen(app)
        app.forget_agents()                                      # the grange gone for good: nothing is kept
        assert app.agent_numbers == {}


def a_board_of_two_alike():
    """A board of two repositories a name writes the same way, each with a pull request 5."""
    keychain.put(keychain.GITHUB_TOKEN, TOKEN)
    store = RepoStore()
    store.add("acme/web-api", admin=True)
    store.add("acme-web/api", admin=True)
    FakePulls.tips = {"acme/web-api": TIP, "acme-web/api": TIP}
    FakePulls.open_ = {"acme/web-api": [node(5, "a", author={"login": "djp3"}, baseRefOid=TIP)],
                       "acme-web/api": [node(5, "b", author={"login": "kenji"}, baseRefOid=TIP)]}


@drive
async def test_a_repository_that_is_removed_takes_its_reading_with_it_and_settles_the_tie(isolated):
    """Hidden, a repository's reading is kept, and still counts when a name fits a pull
    request of each of two: hiding does not say which was meant. Removed, it is gone for good,
    its side of the tie with it, and a read of it that ends afterwards is not kept either."""
    a_board_of_two_alike()
    app = main.Frolic()
    async with app.run_test(size=(170, 46)) as pilot:
        await settle(app, pilot)
        FakeGrange.answers.put(team(("djp3", "acme-web-api-pr-5", "busy")))
        await until(pilot, lambda: app.reported)
        await pilot.pause(0.3)
        assert app.board.pic.agents == {}                        # it fits two: it is on neither
        app.show_repo("acme-web/api", False)
        await pilot.pause(0.3)
        assert app.board.pic.agents == {} and "acme-web/api" in app.pulls     # hidden: still a tie
        late = app.pulls["acme-web/api"]
        await to_setup(app, pilot)
        await pilot.press("down")
        assert app.selected().name == "acme-web/api"
        await pilot.press("x")
        await pilot.pause()
        await pilot.press("y")
        await settle(app, pilot)
        assert app.store.get("acme-web/api") is None and "acme-web/api" not in app.pulls
        app.pulls_known("acme-web/api", late, app.token_used)    # a read of it that ends only now
        assert "acme-web/api" not in app.pulls
        await pilot.press("s")
        await pilot.pause(0.3)
        assert list(app.board.pic.agents) == [("acme/web-api", 5)]     # removed: the tie is settled
        assert app.agents_tied() == ""


@drive
async def test_the_board_draws_an_agent_with_no_pull_request_yet_under_its_repository(isolated):
    a_board()
    app = main.Frolic()
    async with app.run_test(size=(170, 46)) as pilot:
        await settle(app, pilot)
        yours, others = pictures.counts(app.board.pic, "o/app")
        FakeGrange.answers.put(team(("djp3", "o-app reflow fix", "idle"), ("bob", "O-WEB deploy", "busy"),
                                    ("bob", "o-app-reflow", "busy")))
        await until(pilot, lambda: app.reported)
        await pilot.pause(0.3)
        rows = screen(app).splitlines()
        mine = rows[next(i for i, row in enumerate(rows) if "╪ o/app" in row) + 1]       # the line under the repository's
        assert mine.split("│")[0].split() == ["●", "you", "at", "prompt"]                # theirs to answer: the dot
        assert "▷ agent 01  reflow fix" in mine and mine.rstrip().endswith("no pull request yet")
        bobs = rows[next(i for i, row in enumerate(rows) if "╪ o/web" in row) + 1]
        assert bobs.split("│")[0].split() == ["bob", "working"] and "agent 01  deploy" in bobs
        assert "o-app-reflow" not in screen(app) and len(app.board.pic.agents) == 2      # run on: it is nowhere
        assert pictures.counts(app.board.pic, "o/app") == (yours + 1, others)
        assert "▷ app" in screen(app) and "web" in rows[3]                               # on the cards: where each is at work
        asked = next(row for row in rows if "you · answer agent 01 · reflow fix" in row)  # among what needs effort,
        assert asked.split()[:2] == ["●", "app"] and "no pull request yet" in asked       # first, and theirs
        assert app.board.effort_rows[0] is None                                           # a click on it goes nowhere
        # It is not a pull request: the arrows and n pass it by, and the drawer is the selected pull request's.
        assert all(number for _, number in app.board.keys())
        for key in ("down", "up", "n", "n", "n", "n"):
            await pilot.press(key)
            assert app.board.sel in app.board.keys(), key
        await to_setup(app, pilot)
        assert "3 reported · 0 on the pull requests shown here · 2 with no pull request yet" in screen(app)
        assert "not shown" not in screen(app)


@drive
async def test_the_ticker_names_the_pull_request_the_viewer_opened_where_their_agent_had_none(isolated):
    """The agent shows on it only once its session is named for it: the ticker says the name,
    and the board selects the pull request, so that the drawer shows that name and c copies it."""
    a_board()
    app = main.Frolic()
    async with app.run_test(size=(170, 46)) as pilot:
        await settle(app, pilot)
        FakeGrange.answers.put(team(("djp3", "o-web hotfix", "busy"), ("bob", "o-app", "busy")))
        await until(pilot, lambda: app.reported)
        was = app.board.sel
        just_now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        FakePulls.open_["o/app"] = app_nodes() + [node(32, "more", author={"login": "djp3"}, baseRefOid=TIP,
                                                       createdAt=just_now)]
        age(app, only=["o/app"])
        app.tick()
        await settle(app, pilot)
        await pilot.pause(0.3)
        assert "app · #32 opened by you" in screen(app) and app.board.sel == was          # no agent of theirs there:
        # One of theirs that was open all along and has only now come into what is read  # said as any other is
        # (created on the 10th): it is not the one their agent has just opened.
        FakePulls.open_["o/web"] = web_nodes() + [node(30, "old-work", author={"login": "djp3"}, baseRefOid=TIP)]
        age(app, only=["o/web"])
        app.tick()
        await settle(app, pilot)
        await pilot.pause(0.3)
        assert "web · #30 opened by you" in screen(app) and app.board.sel == was
        assert not any("rename" in text for _, text, _ in app.lines)
        FakePulls.open_["o/web"] = FakePulls.open_["o/web"] + [
            node(31, "hotfix", author={"login": "djp3"}, baseRefOid=TIP,
                 createdAt=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))]
        age(app, only=["o/web"])
        app.tick()
        await settle(app, pilot)
        await pilot.pause(0.3)
        said = "you opened #31 · rename your agent there to o-web-pr-31 · c copies it"
        assert f"web · {said}" in screen(app) and app.board.sel == ("o/web", 31) != was
        assert "name     o-web-pr-31 · c copies it" in screen(app)                        # the drawer: the same name
        await pilot.press("c")
        await settle(app, pilot)
        assert FakeClipboard.copied == ["o-web-pr-31"]
        assert [text for _, text, _ in app.lines].count(f"o/web · {said}") == 1           # and the log keeps it
        FakePulls.open_["o/web"] = FakePulls.open_["o/web"] + [node(33, "deploy", author={"login": "bob"}, baseRefOid=TIP)]
        age(app, only=["o/web"])
        app.tick()
        await settle(app, pilot)
        await pilot.pause(0.3)
        assert "web · #33 opened by bob" in screen(app) and app.board.sel == ("o/web", 31)    # someone else's: as ever


@drive
async def test_a_name_that_fits_two_repositories_is_said_quietly_too(isolated):
    a_board_of_two_alike()
    app = main.Frolic()
    async with app.run_test(size=(170, 46)) as pilot:
        await settle(app, pilot)
        FakeGrange.answers.put(team(("djp3", "acme-web-api reflow", "busy")))
        await until(pilot, lambda: app.reported)
        await pilot.pause(0.3)
        assert app.board.pic.agents == {}
        assert app.agents_tied() == "1 agent not shown: its name fits two repositories · s says which"
        assert app.board.ticker().plain.endswith(app.agents_tied())
        FakeGrange.answers.put(team(("djp3", "acme-web-api reflow", "busy"), ("djp3", "acme-web-api-pr-5", "busy"),
                                    cursor=2))
        await until(pilot, lambda: len(app.reported) == 2)
        await pilot.pause(0.3)
        assert app.agents_tied() == ("2 agents not shown: each name fits two pull requests or repositories"
                                     " · s says which")
        await to_setup(app, pilot)
        assert "djp3 › acme-web-api reflow   fits acme/web-api and acme-web/api" in screen(app)


TIED = "1 agent not shown: its name fits two pull requests · s says which"


@drive
async def test_a_name_that_fits_two_pull_requests_is_said_quietly_for_as_long_as_it_does(isolated):
    """The agent is on neither, and without a word it would simply be missing."""
    a_board_of_two_alike()
    app = main.Frolic()
    async with app.run_test(size=(170, 46)) as pilot:
        await settle(app, pilot)
        assert app.agents_tied() == "" and "not shown" not in screen(app)
        FakeGrange.answers.put(team(("djp3", "acme-web-api-pr-5 fix", "busy"), ("bob", "scratch", "idle")))
        await until(pilot, lambda: app.reported)
        await pilot.pause(0.5)                                   # drawn more than once
        ticker = app.board.ticker()
        assert ticker.plain.endswith("   " + TIED) and "✗" not in ticker.plain
        assert str(ticker.spans[-1].style) == MUTED              # fainter than what is amiss, and with no mark
        assert TIED in screen(app)
        said = "djp3's agent acme-web-api-pr-5 fix is not shown: its name fits acme/web-api #5 and acme-web/api #5"
        assert [(text, style) for _, text, style in app.lines if "not shown" in text] == [(said, main.INK)]   # once
        await to_setup(app, pilot)
        drawn = screen(app)
        assert "2 reported · 0 on the pull requests shown here" in drawn
        assert "not shown djp3 › acme-web-api-pr-5 fix   fits acme/web-api #5 and acme-web/api #5" in drawn
        await pilot.press("s")
        FakeGrange.answers.put(team(("bob", "scratch", "idle"), cursor=2))     # its session has ended
        await until(pilot, lambda: len(app.reported) == 1)
        await pilot.pause(0.3)
        assert app.agents_tied() == "" and TIED not in screen(app)


@drive
async def test_what_is_amiss_with_the_grange_is_said_before_a_name_that_fits_two(isolated):
    a_board_of_two_alike()
    app = main.Frolic()
    async with app.run_test(size=(170, 46)) as pilot:
        await settle(app, pilot)
        FakeGrange.answers.put(team(("djp3", "acme-web-api-pr-5", "busy"), ("bob", "acme-web-api-pr-5", "idle")))
        await until(pilot, lambda: app.reported)
        await pilot.pause(0.3)
        assert app.agents_tied() == "2 agents not shown: each name fits two pull requests · s says which"
        FakeGrange.answers.put(out_of_date({"error": "image out of date", "fix": "make update"}, retry=0.01))
        await until(pilot, lambda: app.grange_state == "stale")
        await pilot.pause(0.3)
        ticker = app.board.ticker().plain
        assert "✗ agent-grange is out of date" in ticker and "fits two" not in ticker


@drive
async def test_in_a_window_too_narrow_for_both_the_news_stays_and_the_quiet_notice_gives_way(isolated):
    a_board_of_two_alike()
    app = main.Frolic()
    async with app.run_test(size=(70, 40)) as pilot:
        await settle(app, pilot)
        FakeGrange.answers.put(team(("djp3", "acme-web-api-pr-5", "busy")))
        await until(pilot, lambda: app.reported)
        await pilot.pause(0.3)
        assert app.agents_tied() == TIED
        assert app.board.ticker().plain == app.board.happened().plain          # the line as it would be without it


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
        assert "not shown kenji › o-billing-pr-7   names no repository on your list" in drawn
        assert "scratch" not in drawn                            # it says no pull request: nothing is amiss with it
        counted = app.told_for
        app.setup.draw()
        assert app.told_for is counted                           # drawn again: not counted again
        assert all(kept is app.pulls[key] for kept, key in zip(counted[2], app.pulls))   # the readings themselves
        app.pulls["o/web"] = replace(app.pulls["o/web"])         # read again: the same within, another object
        app.setup.draw()
        assert app.told_for is not counted and app.told_for[2][1] is app.pulls["o/web"]
        app.show_repo("o/app", False)                            # a repository hidden: counted afresh
        await pilot.pause(0.1)
        assert "4 reported · 0 on the pull requests shown here" in screen(app)
        assert [said for _, said, _ in app.lines].count("agent-grange answers: 3 granges, 4 agents") == 1


@drive
async def test_the_agents_not_shown_have_a_few_rows_of_the_setup_and_one_row_each(isolated):
    """Their names are typed on a teammate's Mac and may be long, and there may be many: the
    part keeps its height, so the pull requests and the log below keep theirs."""
    a_board()
    app = main.Frolic()
    async with app.run_test(size=(100, 40)) as pilot:
        await settle(app, pilot)
        await to_setup(app, pilot)
        long = "o-app-pr-5 " + "x" * 200
        FakeGrange.answers.put(team(("djp3", long, "busy"), *(("djp3", f"o-app-pr-{n}", "idle") for n in range(1, 6))))
        await until(pilot, lambda: app.grange_state == "ok")
        await pilot.pause(0.2)
        rows = screen(app).splitlines()
        top = next(i for i, row in enumerate(rows) if "4  Agents" in row)
        part = rows[top:next(i for i in range(top, len(rows)) if set(rows[i].strip()) == {"─"})]
        assert len(part) == 4 + app.ASTRAY_SHOWN, part           # the four it had, and no more than these
        assert "6 reported · 0 on the pull requests shown here" in part[3]
        assert "not shown djp3 › o-app-pr-5 xxxx" in part[4]                 # the name is cut, and why it is
        assert part[4].rstrip().endswith("…   o/app has no open pull request 5")      # not shown is said whole
        assert part[5].strip() == "djp3 › o-app-pr-1   o/app has no open pull request 1"
        assert part[-1].strip() == "and 3 more"                  # six of them: three, and a count of the rest


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
async def test_something_else_on_the_granges_port_is_said_plainly_and_is_no_alarm(isolated):
    """A Mac with no grange, and another program on port 7788: as little trouble as none at all."""
    a_board()
    app = main.Frolic()
    refused = "The agent-grange on this Mac refused the question (HTTP 404)."
    async with app.run_test(size=(170, 46)) as pilot:
        await settle(app, pilot)
        for _ in range(2):
            FakeGrange.answers.put(GrangeError("trouble", refused, retry=0.01))
        await until(pilot, lambda: len(FakeGrange.asked) == 3)
        assert app.grange_state == "trouble" and not app.grange_ever and app.notice_now() == ""
        assert [(said, style) for _, said, style in app.lines if said == refused] == [(refused, main.INK2)]
        await to_setup(app, pilot)
        drawn = screen(app)
        assert "nothing on port 7788 of this Mac answers as an agent-grange" in drawn and "✗" not in drawn
        FakeGrange.answers.put(team(*THE_TEAM))                  # a grange after all
        await until(pilot, lambda: app.grange_state == "ok")
        FakeGrange.answers.put(GrangeError("trouble", refused, retry=30))
        await until(pilot, lambda: app.grange_state == "trouble")
        await pilot.pause(0.1)
        assert app.grange_ever and f"✗ {refused}" in screen(app)  # one that was answering is amiss


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
        assert app.agents_amiss() == "agent-grange is out of date: no agents shown · s to fix"
        assert ticker(app).endswith("✗ " + app.GRANGE_STALE)
        app.happened(time.time(), "app · #98 was approved by bob")   # something on GitHub: it keeps its place
        await pilot.pause(0.3)
        assert ticker(app).endswith("✗ " + app.GRANGE_STALE) and "#98 was approved by bob" in ticker(app)
        await to_setup(app, pilot)
        drawn = screen(app)
        assert "grange    ✗ out of date: image is 3 commit(s) behind tag 'agent-grange-latest'" in drawn
        assert "to fix    make update (git pull --ff-only, then make up)   in the grange's folder" in drawn
        FakeGrange.answers.put(team(*THE_TEAM))                  # updated: it answers
        await until(pilot, lambda: app.grange_state == "ok")
        assert app.agents_amiss() == "" and "to fix" not in screen(app)
        await pilot.press("s")
        await pilot.pause(0.3)
        assert "✗" not in ticker(app) and "#98 was approved by bob" in ticker(app)     # gone with its cause


@drive
async def test_the_board_does_not_say_no_agents_are_shown_while_they_still_are(isolated, monkeypatch):
    """A grange that was answering and is found out of date: what it last reported stays its
    minute, and the board said "no agents shown" from the first second of it."""
    clock = watch_clock(monkeypatch)
    a_board()
    app = main.Frolic()
    async with app.run_test(size=(170, 46)) as pilot:
        await settle(app, pilot)
        FakeGrange.answers.put(team(*THE_TEAM))
        await until(pilot, lambda: app.reported)
        FakeGrange.failing = out_of_date({"error": "image out of date", "fix": "make update"}, retry=0.01)
        await until(pilot, lambda: app.grange_state == "stale")
        await pilot.pause(0.3)
        assert len(app.reported) == 4 and app.agents_amiss() == "agent-grange is out of date · s to fix"
        assert "no agents shown" not in ticker(app) and "agent 01" in screen(app)
        clock[0] += grange.GRACE + 1                             # its minute is over
        await until(pilot, lambda: app.reported == ())
        await pilot.pause(0.3)
        assert app.agents_amiss() == "agent-grange is out of date: no agents shown · s to fix"
        assert ticker(app).endswith("✗ " + app.GRANGE_STALE) and "agent 01" not in screen(app)


@drive
async def test_what_the_grange_says_of_itself_takes_one_row_a_line_however_long_it_is(isolated):
    """It is from outside, and may be 256 characters a field. Left to wrap, the part took a
    dozen rows of a small window and the log beneath it was left one."""
    a_board()
    app = main.Frolic()
    long = out_of_date({"error": "image out of date", "summary": "behind " * 36, "fix": "make update " * 21},
                       retry=30)
    async with app.run_test(size=(100, 30)) as pilot:
        await settle(app, pilot)
        await to_setup(app, pilot)
        FakeGrange.answers.put(long)
        await until(pilot, lambda: app.grange_state == "stale")
        await pilot.pause(0.2)
        rows = screen(app).splitlines()
        top = next(i for i, row in enumerate(rows) if "4  Agents" in row)
        part = rows[top:next(i for i in range(top, len(rows)) if set(rows[i].strip()) == {"─"})]
        assert len(part) == 3, part                                # its heading, why, and the fix
        assert part[1].rstrip().endswith("…") and part[2].rstrip().endswith("…")
        assert all(len(row.rstrip()) <= 100 for row in part)
        assert app.setup.query_one("#lower").size.height >= 4      # the log keeps its room
        assert long.said in [said for _, said, _ in app.lines]     # and has it whole


@drive
async def test_a_granges_own_name_takes_one_row_too_however_long_it_is(isolated):
    a_board()
    app = main.Frolic()
    async with app.run_test(size=(100, 30)) as pilot:
        await settle(app, pilot)
        await to_setup(app, pilot)
        named = team(*THE_TEAM)
        FakeGrange.answers.put(replace(named, grange="g" * 256))
        await until(pilot, lambda: app.grange_state == "ok")
        await pilot.pause(0.2)
        rows = screen(app).splitlines()
        top = next(i for i, row in enumerate(rows) if "4  Agents" in row)
        part = rows[top:next(i for i in range(top, len(rows)) if set(rows[i].strip()) == {"─"})]
        assert len(part) == 5, part                # its heading, the grange, the team, the agents, the one not shown
        assert "✓ answers on 127.0.0.1:7788 as ggg" in part[1] and part[1].rstrip().endswith("…")
        assert all(len(row.rstrip()) <= 100 for row in part)


@drive
async def test_what_was_last_reported_stays_for_a_minute_once_the_grange_stops_answering(isolated, monkeypatch):
    clock = watch_clock(monkeypatch)
    a_board()
    app = main.Frolic()
    async with app.run_test(size=(170, 46)) as pilot:
        await settle(app, pilot)
        FakeGrange.answers.put(team(*THE_TEAM))
        await until(pilot, lambda: app.reported)
        failing("trouble", "The agent-grange on this Mac did not answer.")
        await until(pilot, lambda: app.grange_state == "trouble")
        clock[0] += grange.GRACE - 1                             # nearly its minute, and still asked
        asked = len(FakeGrange.asked)
        await until(pilot, lambda: len(FakeGrange.asked) > asked + 2)
        assert len(app.reported) == 4                            # a grange being restarted is back by then
        await to_setup(app, pilot)
        drawn = screen(app)
        assert "✗ The agent-grange on this Mac did not answer." in drawn and "4 reported" in drawn
        assert "as of" in drawn
        clock[0] += 2                                            # the minute is over: the watch says so
        await until(pilot, lambda: app.reported == ())
        await pilot.pause(0.1)
        assert app.grange_reading is None and "4 reported" not in screen(app)
        assert "agent-grange has not answered for a minute: the 4 agents it reported are no longer shown" in [
            said for _, said, _ in app.lines]
        assert app.agents_amiss() == "agent-grange stopped answering: no agents shown"


@drive
async def test_the_minute_is_counted_from_the_first_failure_not_from_the_last_answer(isolated, monkeypatch):
    """After the Mac has slept, the last answer is hours old when the first failure comes."""
    clock = watch_clock(monkeypatch)
    a_board()
    app = main.Frolic()
    async with app.run_test(size=(150, 40)) as pilot:
        await settle(app, pilot)
        FakeGrange.answers.put(team(*THE_TEAM))
        await until(pilot, lambda: app.reported)
        app.grange_read_at -= 2 * 3600                           # two hours asleep
        clock[0] += 2 * 3600
        for kind in ("trouble", "down"):                         # the held question drops, then nothing listens
            failing(kind, f"The agent-grange on this Mac: {kind}.")
            await until(pilot, lambda: app.grange_state == kind)
            clock[0] += 20                                       # not moved on by the second kind of failure
            app.tick()
            assert len(app.reported) == 4 and app.agents_amiss() == ""
        clock[0] += 25                                           # 65 seconds since the first failure
        await until(pilot, lambda: app.reported == ())
        assert app.agents_amiss() == app.GRANGE_LOST


@drive
async def test_a_failure_just_before_the_mac_sleeps_does_not_cost_the_agents_on_waking(isolated, monkeypatch):
    """The wall clock moves on while the Mac sleeps, and the clock the watch counts on does
    not. Counted on the first, the minute was long over on waking, before the watch, still
    inside its own, had asked once more. Only the watch counts it now."""
    clock = watch_clock(monkeypatch)
    a_board()
    app = main.Frolic()
    async with app.run_test(size=(150, 40)) as pilot:
        await settle(app, pilot)
        FakeGrange.answers.put(team(*THE_TEAM))
        await until(pilot, lambda: app.reported)
        failing("trouble", "The agent-grange on this Mac did not answer.")
        await until(pilot, lambda: app.grange_state == "trouble")
        asleep = time.time() + 2 * 3600
        monkeypatch.setattr(main.time, "time", lambda: asleep)   # two hours on, by the wall clock alone
        app.tick()
        asked = len(FakeGrange.asked)
        await until(pilot, lambda: len(FakeGrange.asked) > asked + 2)
        assert len(app.reported) == 4 and app.agents_amiss() == ""
        clock[0] += grange.GRACE + 1                             # and a minute awake does cost them
        await until(pilot, lambda: app.reported == ())


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
        app.grange_read_at -= 2 * 3600
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


def test_the_builds_self_check_asks_no_grange(isolated, capsys):
    """It starts the app to see that it starts, on the Mac that builds: whatever listens on
    the grange's port there is none of its business."""
    assert main.selfcheck() == 0 and "self-check passed" in capsys.readouterr().out
    assert FakeGrange.made == [] and FakeGrange.asked == []


@drive
async def test_an_app_told_not_to_ask_the_grange_starts_no_watch_and_says_so(isolated):
    a_board()
    app = main.Frolic(ask_grange=False)
    async with app.run_test(size=(150, 40)) as pilot:
        await settle(app, pilot)
        await pilot.pause(0.1)
        assert app.watch is None and FakeGrange.made == [] and app.grange_state == "off"
        await to_setup(app, pilot)
        assert "grange    not asked in this run" in screen(app)


def test_the_granges_port_is_7788_unless_the_settings_say_otherwise(tmp_path):
    import json
    assert Settings.load().grange_port == 7788
    for written, kept in [(7790, 7790), (0, 7788), (65536, 7788), (-1, 7788), ("7790", 7788), (True, 7788)]:
        (tmp_path / "settings.json").write_text(json.dumps({"grange_port": written}))
        assert Settings.load().grange_port == kept, written
