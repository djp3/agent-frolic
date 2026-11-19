# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""
The picture, as plain data: everything the board draws, worked out from GitHub's pull
requests (pulls.py), the rule for who must act (standing.py), and the agents on each pull
request. Nothing here touches the screen or the network, so all of it is tested directly.

  • TWO KINDS OF ACTOR. Humans (GitHub logins; the viewer reads as "you") and agents. An
    agent runs under one human, its operator, and is always drawn beneath that human.
    Nothing reports agents yet, so the mapping is empty; the shapes are here for when it
    is not.
  • TEAMS. A review can be asked of a GitHub team ("team/org/slug", see pulls.py). A team
    the viewer is on is the viewer's to act for (`acts_as`): its request is drawn as
    "● you" is, under the team's name, and the team has a card of its own. One request is
    one job, so it shows once, on the team, and on no member's card. While the viewer's
    teams are still being read (`held`), a team's request is nobody's yet (`undecided`).
  • ORGANIZATIONS. When an author may not merge or ask for a review, the move is the
    maintainer's, named by the repository's owner (standing.py). If that owner is an
    organization and the viewer's own rights in the repository allow the move, the viewer
    acts for the organization as for a team they are on (`maintains`): drawn as
    "you @ Acme" with the yellow dot, with a card of its own among the viewer's teams.
  • THE TREE, `tree()`: repo, then base branch (the default branch first), then the commit
    of that branch a pull request stands on, then the pull requests in stack order.
  • COLUMN 1, `cells()`: owner | state | waits on, as many lines as a pull request needs.
    The owner is the author. When the author must act the state says "to act" and waits on
    is empty; when reviewers must act each is a line under waits on; a queued pull request
    shows "#12 →" and then whoever that one waits on; with checks running nobody must act.
  • YELLOW (theme.WAITING) marks one thing: a move that is the viewer's, their own or
    through a team or an organization they act for.
  • WHAT NEEDS EFFORT, `needs_effort()`: every pull request that waits on a human, most
    urgent first: idle agents because of it, then how long it has been quiet.
  • THE HUMANS for the cards, `humans()`: the viewer, the viewer's teams, then everyone
    something waits on.
  • FOCUS, `focuses()` / `in_focus()`: everyone, a human with their agents (a crew), the
    human alone, one agent.
  • WHAT CHANGED, `changes()`: between two readings of a repository, which pull requests
    changed and one line to say about each.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Mapping

from canvas import Segments, cell_len, cut, seg_len
from pulls import PullRequest, RepoPulls, Review, Step, is_team, known_as, team_slug
from standing import Standing, settle
from theme import AXIS, CRIT, GOOD, INK, INK2, MUTED, SERIOUS, WAITING, WARN

SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"            # the dots that turn where an agent is at work
Key = tuple[str, int]             # a pull request: (repository in lower case, number)

REVIEW_MARK = {"approved": ("✓", GOOD), "changes": ("✗", SERIOUS), "commented": ("◆", GOOD),
               "dismissed": ("–", MUTED)}
REVIEW_SAID = {"approved": "approved", "changes": "asked for changes", "commented": "commented",
               "dismissed": "had their review dismissed"}
CHECK_MARK = {"pass": ("✓", GOOD), "fail": ("✗", CRIT), "running": ("…", WARN), "none": ("·", AXIS),
              "unpaid": ("$", CRIT)}
CHECK_SAID = {"pass": "checks pass", "fail": "checks fail", "running": "checks running", "none": "no checks",
              "unpaid": "checks not run: GitHub is not paid"}
TRAIL = 4                         # the most marks drawn for one reviewer; a longer trail starts with …


def key_of(pull: PullRequest) -> Key:
    return (pull.repo.lower(), pull.number)


# ---- agents ----------------------------------------------------------------------------

@dataclass
class Agent:
    operator: str                 # the login of the human it runs under
    serial: str                   # "01": counts sessions on one pull request
    repo: str
    number: int                   # the pull request it is on
    role: str                     # "author": works on it for its author; "review": reviews it
    state: str                    # "working" | "at its prompt" | "waiting"
    note: str = ""                # what it is doing, or why it is idle

    @property
    def name(self) -> str:
        return f"agent {self.serial}"

    @property
    def working(self) -> bool:
        return self.state == "working"

    @property
    def key(self) -> tuple[str, str, int, str]:
        return (self.operator.lower(), self.repo.lower(), self.number, self.serial)


# ---- the picture -----------------------------------------------------------------------

@dataclass
class Picture:
    names: list[str]                               # the shown repositories, in the user's order
    pulls: dict[str, RepoPulls]                    # repository (lower case) -> what was read from it
    viewer: str = ""                               # the viewer's GitHub login, "" when unknown
    agents: dict[Key, list[Agent]] = field(default_factory=dict)
    notes: dict[str, str] = field(default_factory=dict)    # repository (lower case) -> a line to
                                                           # show under it: reading…, or why it failed
    teams: Mapping[str, str] = field(default_factory=dict)     # the teams the viewer is on: "team/org/
                                                           # slug" in lower case -> the name people read
    held: bool = False                             # the viewer's teams are still being read: until
                                                   # they are known, a team's request is nobody's
    standings: dict[Key, Standing] = field(default_factory=dict, repr=False)
    efforts: list[Effort] | None = field(default=None, repr=False, compare=False)   # needs_effort's
                                                   # answer, once something has asked for it
    twins: set[str] | None = field(default=None, repr=False, compare=False)         # the labels two teams
                                                   # asked here would share (lower case), once asked for
    orgs: dict[str, bool] = field(default_factory=dict, repr=False, compare=False)  # the organizations that
                                                   # own a shown repository (lower case) -> whether the
                                                   # viewer has rights in one of them to act for it

    def __post_init__(self) -> None:
        for got in self.read():
            for number, standing in settle(got.pulls).items():
                self.standings[(got.repo.lower(), number)] = standing
            if got.org:
                owner = got.repo.split("/", 1)[0].lower()
                self.orgs[owner] = self.orgs.get(owner, False) or any(got.rights)

    def read(self) -> list[RepoPulls]:
        """What was read, for the shown repositories that have been read, in order."""
        return [self.pulls[name.lower()] for name in self.names if name.lower() in self.pulls]

    def all(self) -> list[PullRequest]:
        return [pull for got in self.read() for pull in got.pulls]

    def find(self, key: Key | None) -> PullRequest | None:
        if key is None or key not in self.standings:
            return None
        return self.standings[key].pull

    def stand(self, pull: PullRequest) -> Standing:
        return self.standings[key_of(pull)]

    def on(self, pull: PullRequest) -> list[Agent]:
        return self.agents.get(key_of(pull), [])

    def is_viewer(self, login: str) -> bool:
        return bool(self.viewer) and login.lower() == self.viewer.lower()

    def shown(self, login: str, pull: PullRequest | None = None) -> str:
        """A login as it reads on screen: the viewer is "you", a bot goes by the name GitHub
        shows for it, and a team by its name when the viewer is on it. No other team's
        name is known: it reads "team/slug". Two teams asked here that would read alike,
        one in each of two organizations, read "Acme/Platform" or "team/Acme/platform".
        An organization the viewer acts for reads "you @ Acme": on `pull`, where they may
        make its move there (`maintains`); with no pull request named, where they have
        rights in it (`yours`)."""
        if self.is_viewer(login):
            return "you"
        if self.is_org(login) and (self.maintains(login, pull) if pull is not None else self.yours(login)):
            return f"you @ {login}"
        if is_team(login):
            label = self._team_label(login)
            if label.lower() not in self._twins():
                return label
            # Two teams asked here would read alike: each says whose it is, as two repositories
            # of one name keep their owners (`labels`).
            return f"{login.split('/')[1]}/{label}" if self.on_team(login) else login
        return known_as(login)

    def _team_label(self, team: str) -> str:
        return self.teams[team.lower()] if self.on_team(team) else team_slug(team)

    def _twins(self) -> set[str]:
        """The labels that more than one of the teams asked in this picture would have."""
        if self.twins is None:
            keys: dict[str, set[str]] = {}
            for pull in self.all():
                for who in pull.asked:
                    if is_team(who):
                        keys.setdefault(self._team_label(who).lower(), set()).add(who.lower())
            self.twins = {label for label, teams in keys.items() if len(teams) > 1}
        return self.twins

    def on_team(self, who: str) -> bool:
        """`who` is a team the viewer is on. With no viewer there is no such team."""
        return bool(self.viewer) and who.lower() in self.teams

    def is_org(self, who: str) -> bool:
        """`who` is the organization that owns one of the shown repositories."""
        return who.lower() in self.orgs

    def yours(self, who: str) -> bool:
        """The viewer, a team the viewer is on, or an organization they have rights in: in
        one of its shown repositories they may at least ask for a review."""
        return self.is_viewer(who) or self.on_team(who) or (bool(self.viewer) and self.orgs.get(who.lower(), False))

    def maintains(self, who: str, pull: PullRequest) -> bool:
        """The move on `pull` is the maintainer's (its author may not make it, standing.py),
        `who` is the organization that owns the repository and so stands as its maintainer,
        and the viewer's own rights there allow that move: merging takes write access or
        more, asking for a review triage or more. A person's repository is not so: its
        maintainer is that person, and is the viewer or is not."""
        here = self.standings.get(key_of(pull))
        got = self.pulls.get(pull.repo.lower())
        if not self.viewer or here is None or here.by != "maintainer" or got is None or not got.org:
            return False
        if who.lower() != pull.repo.split("/", 1)[0].lower():
            return False
        may_ask, may_merge = got.rights
        return may_merge if here.what == "merge" else may_ask

    def undecided(self, who: str, pull: PullRequest | None = None) -> bool:
        """A team, while the viewer's teams are not known yet: whether its requests are the
        viewer's cannot be said, so for now they are neither theirs nor anyone else's.
        On a pull request the viewer wrote it can be said already: not theirs, whichever
        team it is (`acts_as`). With no pull request named, the team as such is meant."""
        if not (self.held and is_team(who)):
            return False
        return pull is None or not self.is_viewer(pull.author)

    def acts_as(self, who: str, pull: PullRequest) -> bool:
        """Whether a move on `pull` that is `who`'s is the viewer's to make: `who` is the
        viewer, a team they are on, or the organization they may act for as its maintainer
        (`maintains`). A team's is not theirs on a pull request the viewer wrote: GitHub
        lets no author review their own, so a request to their team is for the others on it."""
        if self.is_viewer(who) or self.maintains(who, pull):
            return True
        return self.on_team(who) and not self.is_viewer(pull.author)

    def working(self, pull: PullRequest) -> list[Agent]:
        """The agents at work where this pull request's wait ends."""
        standing = self.stand(pull)
        end = (standing.end or standing).pull
        return [a for a in self.on(end) if a.working]

    def mine(self, pull: PullRequest) -> bool:
        """The viewer is one of those who must act for it to move, themselves or through a
        team or an organization they act for, and no agent is on it."""
        here = self.stand(pull)
        end = (here.end or here).pull                    # where the wait ends: whose move it is
        return not self.working(pull) and any(self.acts_as(who, end) for who in here.movers)


# ---- the tree --------------------------------------------------------------------------

@dataclass
class Row:
    kind: str                     # "repo" | "note" | "base" | "commit" | "pull"
    lead: str                     # the connector to draw before it
    repo: str
    pull: PullRequest | None = None
    label: str = ""               # repo: its name; base: the branch; commit: "tip" or ""; note: its words
    sha: str = ""                 # commit: the short hash
    total: int | None = None      # repo: how many pull requests are open there, None while it is unread
    listed: int = 0               # repo: how many of them the list holds (the most recent, past the most read)


def tree(picture: Picture) -> list[Row]:
    """The shown repositories as one tree, top to bottom:

        owner/name
        └ main
          ┬ 2a69e7e tip
          ├ #12 …                    a pull request standing on that commit
          │ └ #13 …                  one stacked on it
          ┼ 18deb8e
          └ #9 …
        └ release-0.2
          ┬ 6935c77
          └ #24 …

    A repository that has not been read, or could not be read, gets a note row saying so.
    One with nothing open gets none: its own row carries how many are open (`total`, 0
    here), which the board writes on it, and a line more would only make the list longer."""
    rows: list[Row] = []
    for name in picture.names:
        low = name.lower()
        got = picture.pulls.get(low)
        rows.append(Row("repo", "", got.repo if got else name, label=got.repo if got else name,
                        total=got.total if got else None, listed=len(got.pulls) if got else 0))
        if low in picture.notes:
            rows.append(Row("note", "", name, label=picture.notes[low]))
        if got is None:
            if low not in picture.notes:
                rows.append(Row("note", "", name, label="not read yet"))
            continue
        by_number = {p.number: p for p in got.pulls}             # (none open: nothing more is added)
        roots = [p for p in got.pulls if not p.parent]
        bases = sorted({p.base for p in roots}, key=lambda b: (b != got.default_branch, b))
        for base in bases:
            rows.append(Row("base", "└ ", got.repo, label=base))
            commits: dict[str, list[PullRequest]] = {}
            for root in (p for p in roots if p.base == base):
                commits.setdefault(root.base_oid, []).append(root)

            def newest(oid: str) -> float:
                return max((p.updated.timestamp() if p.updated else 0.0) for p in commits[oid])

            is_tip = {oid: bool(got.tip) and base == got.default_branch and oid == got.tip for oid in commits}
            order = sorted(commits, key=lambda oid: (not is_tip[oid], -newest(oid)))
            for i, oid in enumerate(order):
                rows.append(Row("commit", "  ┬ " if i == 0 else "  ┼ ", got.repo,
                                label="tip" if is_tip[oid] else "", sha=oid[:7]))
                hanging = commits[oid]
                for j, root in enumerate(hanging):
                    last = i == len(order) - 1 and j == len(hanging) - 1
                    _visit(rows, by_number, root, "  ", last, got.repo)
    return rows


def _visit(rows: list[Row], by_number: dict[int, PullRequest], pull: PullRequest, lead: str,
           last: bool, repo: str) -> None:
    rows.append(Row("pull", lead + ("└ " if last else "├ "), repo, pull))
    for i, number in enumerate(pull.children):
        _visit(rows, by_number, by_number[number], lead + ("  " if last else "│ "),
               i == len(pull.children) - 1, repo)


# ---- column 1: owner | state | waits on ------------------------------------------------

def human_cell(picture: Picture, login: str, yours_to_act: bool, pull: PullRequest | None = None) -> Segments:
    """A human, a team, or an organization standing as maintainer: a two-cell mark, then
    the name. The mark is the yellow dot when the move is the viewer's: this is the viewer,
    or a team or an organization they act for. `pull` is the one whose move it is."""
    name = picture.shown(login, pull)
    if yours_to_act:
        return [("● ", WAITING), (name, f"bold {INK}")]
    return [("  ", MUTED), (name, INK if picture.yours(login) else INK2)]


def agent_cell(picture: Picture, agent: Agent, spin: str = SPIN[0]) -> Segments:
    """An agent, beneath its human. The mark turns while it works; ▷ is an agent at its
    prompt, · one that waits. It is never yellow: the yellow dot is on the human above."""
    yours = picture.is_viewer(agent.operator)
    mark, style = {"working": (spin, INK), "at its prompt": ("▷", INK if yours else MUTED),
                   "waiting": ("·", MUTED)}[agent.state]
    name = INK if agent.working or (yours and agent.state == "at its prompt") else INK2
    return [("  └ ", AXIS), (mark + " ", style), (agent.name, name)]


def agent_state(picture: Picture, agent: Agent) -> Segments:
    yours = picture.is_viewer(agent.operator)
    return {"working": [("working", INK2)],
            "at its prompt": [("at prompt", INK if yours else INK2)],
            "waiting": [("waiting", MUTED)]}[agent.state]


def cells(picture: Picture, pull: PullRequest, spin: str = SPIN[0]) -> list[tuple[Segments, Segments, Segments]]:
    """Column 1 for one pull request: a list of (owner, state, waits on), one per line. The
    owner side (the author, then the author's agents) and the waits-on side (each party,
    then that party's agents) stack independently."""
    return [(owner, state, waits) for owner, state, waits, _ in _lines(picture, pull, spin)]


def line_agents(picture: Picture, pull: PullRequest) -> list[list[Agent]]:
    """The agents drawn on each line of `cells`, in step with it."""
    return [agents for *_, agents in _lines(picture, pull)]


def _lines(picture: Picture, pull: PullRequest,
           spin: str = SPIN[0]) -> list[tuple[Segments, Segments, Segments, list[Agent]]]:
    """Each line of column 1: (owner, state, waits on, the agents drawn on it). The one
    layout `cells` and `line_agents` both read, so they cannot fall out of step."""
    here = picture.stand(pull)
    end = here.end or here
    mine = picture.mine(pull)
    own_agents = [a for a in picture.on(pull) if a.role == "author"]
    left: list[tuple[Segments, Segments, list[Agent]]] = []
    by_author = here.by == "author" and not here.queued
    owner = human_cell(picture, pull.author, mine and by_author)
    if own_agents:
        state: Segments = []
    elif here.queued or here.others:
        state = [("waiting", MUTED)]
    elif here.by == "nobody":
        state = [("checks running", INK2)]
    else:
        state = [("to act", f"bold {INK}" if mine else INK2)]
    left.append((owner, state, []))
    for agent in own_agents:
        left.append((agent_cell(picture, agent, spin), agent_state(picture, agent), [agent]))

    right: list[tuple[Segments, list[Agent]]] = []
    if here.queued:
        more = " … →" if len(here.chain) > 1 else " →"
        right.append(([("  ", MUTED), (f"#{here.chain[0]}", INK2), (more, MUTED)], []))
    if here.queued or here.others:
        if end.by == "nobody":
            right.append(([("  ", MUTED), ("checks running", MUTED)], []))
        for who in end.who:
            yours = mine and picture.acts_as(who, end.pull)
            right.append((human_cell(picture, who, yours, end.pull), []))
            right += [(agent_cell(picture, a, spin), [a]) for a in reviewing(picture, end.pull, who)]
    lines = max(len(left), len(right))
    left += [([], [], [])] * (lines - len(left))
    right += [([], [])] * (lines - len(right))
    return [(owner, state, waits, own + theirs)
            for (owner, state, own), (waits, theirs) in zip(left, right)]


def reviewing(picture: Picture, pull: PullRequest, who: str) -> list[Agent]:
    """The reviewing agents on `pull` that belong beneath `who`, one of those it waits on.
    Beneath a reviewer: the agents they operate. Beneath a team: the ones whose operator
    was not asked by name. Which team such an operator is on is not known, so when several
    teams are asked the agent is beneath each of them."""
    named = {w.lower() for w in picture.stand(pull).who if not is_team(w)}
    return [a for a in picture.on(pull) if a.role == "review"
            and (a.operator.lower() not in named if is_team(who) else a.operator.lower() == who.lower())]


def move_words(picture: Picture, pull: PullRequest) -> str:
    """The pull request's next move in words, for the line under it and the drawer."""
    here = picture.stand(pull)
    if here.queued:
        return f"{here.what} once #{here.chain[0]} lands"
    return here.what


# ---- what GitHub shows, as marks and words ---------------------------------------------

def mark(picture: Picture, pull: PullRequest) -> tuple[str, str, str]:
    """(icon, color, label) for where a pull request is in its review. Color is the icon's.
    This is about the review alone: an approved pull request stays "approved" while its
    checks fail or run, though the next move (standing.py) is then about the checks.
    A review asked for comes before changes requested, as in standing.py: GitHub keeps
    "changes requested" until the reviewer reviews again, and once the author has asked
    them to, the pull request waits on that re-review."""
    if pull.draft:
        return "◌", MUTED, "draft"
    if pull.mergeable == "conflict":
        return "⚠", CRIT, "merge conflict"
    if pull.asked:
        reviewed = {r.who for r in pull.reviews}
        if all(who in reviewed for who in pull.asked):
            return "↻", WARN, "re-review asked"
        return "◔", WARN, "in review"
    if pull.decision == "changes":
        return "✗", SERIOUS, "changes requested"
    if pull.decision == "approved" or (pull.decision == "" and pull.reviews and not pull.comments_open):
        if pull.checks in ("pass", "none", "unpaid") and pull.mergeable == "yes" and not pull.comments_open:
            return "▲", GOOD, "ready to merge"
        said = pull.decision == "approved" or any(r.state == "approved" for r in pull.reviews)
        return "✓", GOOD, "approved" if said else "reviewed"
    if pull.reviews:                                             # somebody has looked; it is not settled
        return "◔", WARN, "in review"
    return "○", MUTED, "open, no review asked"


def step_mark(step: Step) -> tuple[str, str]:
    """(icon, color) for one step of a reviewer's trail. A step that only commented shows
    whether anything it raised is still to resolve: an open orange ◇ while something is
    (the same ◇ that counts the comments to resolve), a filled green ◆ once nothing is.
    A step that asked for changes is the orange ✗ until every thread begun in it is
    resolved, and then a green ●: that is not yet an approval, so it is not the ✓."""
    if step.state == "commented" and step.open:
        return "◇", SERIOUS
    if step.state == "changes" and step.threads and not step.open:
        return "●", GOOD
    return REVIEW_MARK[step.state]


def _capped(marks: list[tuple[str, str]]) -> list[tuple[str, str]]:
    return marks if len(marks) <= TRAIL else [("…", MUTED)] + marks[1 - TRAIL:]


def review_marks(review: Review) -> list[tuple[str, str]]:
    """A reviewer's trail as marks, oldest first: ●✓ is "asked for changes, all of it
    resolved, then approved". The last mark is where they stand now."""
    steps = review.trail or [Step(review.state, review.threads, review.open)]
    return _capped([step_mark(step) for step in steps])


def reviewers(picture: Picture, pull: PullRequest) -> list[tuple[list[tuple[str, str]], str, str]]:
    """Everyone with a say in the review, as drawn: (marks, name, words). Those who have
    reviewed come first, oldest word first; then those asked and not yet answered, whose
    trail (when they reviewed before) ends in ◔."""
    out = [(review_marks(r), picture.shown(r.who), review_said(r)) for r in pull.reviews if r.who not in pull.asked]
    for who in pull.asked:
        before = next((r for r in pull.reviews if r.who == who), None)
        marks = (review_marks(before) if before is not None else []) + [("◔", WARN)]
        out.append((_capped(marks), picture.shown(who),
                    "asked again, no answer yet" if before is not None else "asked, no answer yet"))
    return out


def review_said(review: Review) -> str:
    """A reviewer's latest word: "commented, 2 to resolve", "asked for changes, all
    resolved", "approved". """
    said = REVIEW_SAID[review.state]
    if review.open:
        return f"{said}, {review.open} to resolve"
    if review.state in ("commented", "changes") and review.threads:
        return f"{said}, all resolved"
    return said


def fit(names: list[str], letters: int) -> list[str] | None:
    """The names cut to share `letters` cells: the widest gives way first, and a cut name
    ends in …. None when they could not keep three cells each. Cells, not characters: a
    login is plain ASCII, but a team's name is whatever its people typed, and a character
    of a wide script takes two cells."""
    widths = [cell_len(name) for name in names]
    if sum(min(n, 3) for n in widths) > letters:
        return None
    while sum(widths) > letters:
        widths[widths.index(max(widths))] -= 1
    return [name if cell_len(name) == n else cut(name, n) for name, n in zip(names, widths)]


def review_cell(picture: Picture, pull: PullRequest, names: bool, room: int) -> Segments:
    """Each reviewer's trail of marks and their name, then who is still asked, then ◇ the
    comments to resolve. Names that do not fit are cut to fit; only when even that fails
    are the marks left alone."""
    tail: Segments = [("◇", MUTED), (str(pull.comments_open), INK)] if pull.comments_open else []
    found = reviewers(picture, pull)
    if not found and not tail:
        return [("·", AXIS)]
    if names:                                                    # the marks, a space, the name, two spaces
        letters = room - sum(len(marks) + 3 for marks, _, _ in found) - seg_len(tail) + (0 if tail else 2)
        fitted = fit([who for _, who, _ in found], letters)
        if fitted is not None:
            long: Segments = []
            for (marks, _, _), who in zip(found, fitted):
                long += marks + [(f" {who}  ", INK2)]
            return long + tail
    apart = any(len(marks) > 1 for marks, _, _ in found)         # trails need a gap to tell them apart
    brief: Segments = []
    for marks, _, _ in found:
        brief += ([(" ", MUTED)] if apart and brief else []) + marks
    return brief + ([(" ", MUTED)] if brief and tail else []) + tail


def progress(picture: Picture, pull: PullRequest) -> tuple[str, int, int]:
    """(word, done, total): how far along it is. Tried in this order:

        in progress      an agent is at work on it (with the comments resolved, of the total)
        stalled          some review comments are resolved and some are still open
        ready to merge   merging is the next move
        queued           merging is the next move, once the one it is stacked on lands
        resolved         every review comment is resolved, and something else holds it up
        not started      none of the above

    done and total are 0 when there is no count to show."""
    done = pull.comments_total - pull.comments_open
    if any(a.working for a in picture.on(pull)):
        return "in progress", done, pull.comments_total
    if pull.comments_open and done > 0:
        return "stalled", done, pull.comments_total
    here = picture.stand(pull)
    if here.what == "merge":
        return ("queued" if here.queued else "ready to merge"), 0, 0
    if pull.comments_total and not pull.comments_open:
        return "resolved", done, pull.comments_total
    return "not started", 0, 0


def quiet(pull: PullRequest, now: datetime | None = None) -> str:
    """How long the pull request has been quiet, as the app says it wherever it does: the
    figure of its age, in days and hours ("128d 11h", "21h", "0h" within the hour). Nothing
    when GitHub did not say when it last changed."""
    seconds = silence(pull, now)
    return "" if seconds is None else age_said(seconds)


AGE_BAR = 12                      # cells in the bar of an age, and in the bar of a repository's timer
AGE_FIGURE = 8                    # cells the figure after a bar takes at the most: "128d 11h"
QUIET = AGE_BAR + 1 + AGE_FIGURE + 1             # the age column: the bar, the figure, a cell before the edge
_SPANS = ((60, 3600), (3600, 86400), (86400, 7 * 86400), (7 * 86400, 90 * 86400))   # minutes, hours, days, weeks


def age_said(seconds: float) -> str:
    """An age in days and hours, and nothing finer: "128d 11h". Under a day it is the hours
    alone ("21h"), and under an hour that is "0h". Past 999 days it is the days alone
    ("1000d"): the hours would not fit in AGE_FIGURE cells, and say nothing by then."""
    hours = max(0, int(seconds // 3600))
    if hours < 24:
        return f"{hours}h"
    days = hours // 24
    return f"{days}d" if days > 999 else f"{days}d {hours % 24:02d}h"


def age_reach(seconds: float) -> float:
    """How much of its bar an age fills, from 0 to 1: a quarter each for minutes, hours, days
    and weeks, and within each by the logarithm, so that its first doubling counts for as
    much as its last. A minute or less is 0, three months or more is 1.

    The figure says nothing finer than the hour, so within the hour the bar is all there is
    to tell two ages apart, and it does so roughly: drawn in AGE_BAR cells it has three
    steps to each unit, so none of it is white for some four minutes, a cell until a
    quarter of an hour, two until the hour."""
    for unit, (low, high) in enumerate(_SPANS):
        if seconds < high:
            return (unit + math.log(max(seconds, low) / low) / math.log(high / low)) / len(_SPANS)
    return 1.0


def silence(pull: PullRequest, now: datetime | None = None) -> float | None:
    """How many seconds the pull request has been quiet. None when GitHub did not say when
    it last changed."""
    if pull.updated is None:
        return None
    return max(0.0, ((now or datetime.now(timezone.utc)) - pull.updated).total_seconds())


# ---- what needs effort -----------------------------------------------------------------

@dataclass
class Effort:
    pull: PullRequest             # the pull request where the wait ends
    standing: Standing
    mine: bool                    # the viewer is one of those who must act
    who: str                      # who must act, as shown: "you", "bob, carol", "Platform"
    undecided: bool = False       # a team is among those it waits on, the viewer's teams are not
                                  # known yet, and it is not the viewer's already: whether it will
                                  # be cannot be said, so for now it is nobody's
    behind: list[PullRequest] = field(default_factory=list)     # the ones queued behind it
    waiting: int = 0              # idle agents: the ones on it, and the ones queued behind
    quiet: str = ""

    @property
    def urgency(self) -> str:
        held = f"{self.waiting} agent{'' if self.waiting == 1 else 's'} waiting · " if self.waiting else ""
        return f"{held}quiet {self.quiet}"


def needs_effort(picture: Picture, now: datetime | None = None) -> list[Effort]:
    """Every pull request that waits on a human and that no agent is working on, most
    urgent first: whether any agent sits idle because of it, then how long it has been
    quiet. Pull requests queued behind another are counted with the one they wait on.

    Worked out once per picture: the counts, the cards and the focus all ask for it on
    every draw. Only an answer for a given `now` is worked out afresh (and not kept)."""
    if now is not None:
        return _needs_effort(picture, now)
    if picture.efforts is None:
        picture.efforts = _needs_effort(picture, datetime.now(timezone.utc))
    return list(picture.efforts)


def _needs_effort(picture: Picture, now: datetime) -> list[Effort]:
    found: dict[Key, Effort] = {}
    queued: list[PullRequest] = []
    for pull in picture.all():
        here = picture.stand(pull)
        if here.queued:
            queued.append(pull)
        elif here.who and not picture.working(pull):
            idle = sum(not a.working for a in picture.on(pull))
            mine = picture.mine(pull)
            found[key_of(pull)] = Effort(pull, here, mine, ", ".join(picture.shown(w, pull) for w in here.who),
                                         undecided=not mine and any(picture.undecided(w, pull) for w in here.who),
                                         waiting=idle, quiet=quiet(pull, now))
    for pull in queued:
        end = picture.stand(pull).end
        effort = found.get(key_of(end.pull)) if end is not None else None
        if effort is not None:
            effort.behind.append(pull)
            effort.waiting += sum(not a.working for a in picture.on(pull))
    far = datetime.max.replace(tzinfo=timezone.utc)
    return sorted(found.values(), key=lambda e: (e.waiting == 0, e.pull.updated or far))


# ---- the humans ------------------------------------------------------------------------

@dataclass
class Person:
    """Who a card is for: a human, or a team a review was asked of."""
    login: str                    # a team's is "team/org/slug"
    name: str                     # as shown: "you" for the viewer, a team's name
    to_act: list[PullRequest] = field(default_factory=list)     # what waits on them
    agents: list[Agent] = field(default_factory=list)           # the agents they operate
    kind: str = "human"           # "human" | "team" | "org" (an organization, as maintainer)
    viewer: bool = False          # the viewer's own card. Told by this, never by the name: a
                                  # team may be called anything, "you" included
    yours: bool = False           # the viewer, a team the viewer is on, or an organization they
                                  # have rights in
    dot: bool = False             # something here is the viewer's to act on


def humans(picture: Picture) -> list[Person]:
    """The viewer first, then the viewer's teams that something waits on (with the
    organizations they have rights in), then everyone else something waits on; within each,
    the one waited on longest first. Someone nothing waits on has no card: on a real
    repository that is every stranger who ever left a review. A team has a card as a human
    has; what waits on it is on its card alone, not on its members'. A team that is still
    undecided has none."""
    found: dict[str, Person] = {}
    if picture.viewer:
        found[picture.viewer.lower()] = Person(picture.viewer, "you", viewer=True, yours=True)

    def card(who: str) -> Person:
        kind = "team" if is_team(who) else "org" if picture.is_org(who) else "human"
        return found.setdefault(who.lower(), Person(who, picture.shown(who), yours=picture.yours(who), kind=kind))

    for effort in needs_effort(picture):
        for who in effort.standing.who:
            if picture.undecided(who):
                continue
            person = card(who)
            person.to_act.append(effort.pull)
            person.dot = person.dot or picture.acts_as(who, effort.pull)
    for agents in picture.agents.values():
        for agent in agents:
            card(agent.operator).agents.append(agent)
    far = datetime.max.replace(tzinfo=timezone.utc)

    def longest(person: Person) -> datetime:
        return min((p.updated or far for p in person.to_act), default=far)

    def own_team(person: Person) -> bool:
        return person.kind in ("team", "org") and person.yours

    viewer = [p for p in found.values() if p.viewer]
    teams = sorted((p for p in found.values() if own_team(p) and p.to_act), key=longest)
    others = sorted((p for p in found.values() if not p.viewer and not own_team(p) and (p.to_act or p.agents)),
                    key=longest)
    return viewer + teams + others


# ---- focus -----------------------------------------------------------------------------

@dataclass(frozen=True)
class Focus:
    kind: str = "all"             # "all" | "crew" (a human and their agents) | "human" | "agent"
    human: str = ""               # the login, lower case; for a team's card, its "team/org/slug"
    agent: tuple | None = None    # Agent.key, for kind "agent"


def focuses(picture: Picture) -> list[Focus]:
    """The steps of the focus key: everyone; then, per card, the human with their agents
    (a crew), and when they have agents, the human alone and each agent. A team's card is
    one step: what waits on that team."""
    out = [Focus()]
    for person in humans(picture):
        out.append(Focus("crew", person.login.lower()))
        if person.agents:
            out.append(Focus("human", person.login.lower()))
            out += [Focus("agent", person.login.lower(), agent.key) for agent in person.agents]
    return out


def in_focus(picture: Picture, pull: PullRequest, focus: Focus) -> bool:
    """Whether a pull request belongs to what is in focus. A human's are the ones they
    wrote and the ones that wait on them; a crew adds the ones their agents are on; an
    agent's are the one it is on and the ones queued behind that one. It is the card in
    focus that the movers are compared with, never the viewer: what waits on a team is in
    that team's focus, and in no member's."""
    if focus.kind == "all":
        return True
    here = picture.stand(pull)
    if focus.kind == "agent":
        end = (here.end or here).pull
        return any(a.key == focus.agent for a in picture.on(pull) + picture.on(end))
    theirs = (pull.author.lower() == focus.human
              or any(w.lower() == focus.human for w in here.movers + here.who))
    if focus.kind == "human":
        return theirs
    return theirs or any(a.operator.lower() == focus.human for a in picture.on(pull))


# ---- the top line's counts -------------------------------------------------------------

def counts(picture: Picture, repo: str) -> tuple[int, int]:
    """For one repository: (pull requests the viewer can get moving, other people and
    teams that something there waits on). A team the viewer acts for is not one of the
    others, and an undecided one is not counted either way."""
    low = repo.lower()
    found = [e for e in needs_effort(picture) if e.pull.repo.lower() == low]
    people = {w.lower() for e in found for w in e.standing.who
              if not picture.acts_as(w, e.pull) and not picture.undecided(w, e.pull)}
    return sum(e.mine for e in found), len(people)


def labels(names: list[str]) -> dict[str, str]:
    """A short name for each repository: the part after the slash, unless two repositories
    share it (a fork and the repository it came from), which keep their owners."""
    short = [name.split("/", 1)[-1].lower() for name in names]
    return {name: (name.split("/", 1)[-1] if short.count(name.split("/", 1)[-1].lower()) == 1 else name)
            for name in names}


# ---- what changed between two readings -------------------------------------------------

def fingerprint(picture: Picture, pull: PullRequest) -> tuple:
    """The facts about a pull request that show on screen. Two readings with the same
    fingerprint look the same."""
    here = picture.stand(pull)
    return (pull.title, pull.draft, pull.mergeable, pull.checks, pull.decision, tuple(pull.asked),
            tuple((r.who, r.state, r.open, tuple((s.state, s.threads, s.open) for s in r.trail))
                  for r in pull.reviews), pull.comments_open, pull.comments_total,
            pull.base, pull.parent, tuple(here.movers), here.move, tuple(here.chain))


def waits_words(picture: Picture, pull: PullRequest) -> str:
    """ "waits on bob to review", "waits on you to merge", "checks running". """
    here = picture.stand(pull)
    if not here.movers:
        return here.move
    end = (here.end or here).pull                       # whose move it is: where the wait ends
    who = ", ".join(picture.shown(w, end) for w in here.movers)
    through = f" (behind #{here.chain[0]})" if here.queued else ""
    return f"waits on {who} to {here.move}{through}"


@dataclass
class Change:
    key: Key
    said: str                     # one line: "#12 bob approved · waits on you to merge"
    pull: PullRequest | None      # None when it is no longer open


def changes(before: Picture | None, after: Picture, repo: str) -> list[Change]:
    """What changed in one repository between two readings: new pull requests, ones no
    longer open, and ones whose facts on screen changed, each with a line to say."""
    if before is None:
        return []
    low = repo.lower()
    old = {key_of(p): p for p in before.all() if p.repo.lower() == low}
    new = {key_of(p): p for p in after.all() if p.repo.lower() == low}
    if low not in before.pulls:                   # the first reading: everything is new, nothing changed
        return []
    out: list[Change] = []
    for key, pull in new.items():
        if key not in old:
            out.append(Change(key, f"#{pull.number} opened by {after.shown(pull.author)} · "
                                   f"{waits_words(after, pull)}", pull))
        elif fingerprint(before, old[key]) != fingerprint(after, pull):
            out.append(Change(key, f"#{pull.number} {_difference(before, old[key], after, pull)}", pull))
    for key, pull in old.items():
        if key not in new:
            out.append(Change(key, f"#{pull.number} is no longer open (merged or closed)", None))
    return out


def _difference(before: Picture, old: PullRequest, after: Picture, new: PullRequest) -> str:
    """The first thing that changed, in words, then where the pull request stands now."""
    said = ""
    was = {r.who: r.state for r in old.reviews}
    for review in new.reviews:
        if was.get(review.who) != review.state:
            said = f"{after.shown(review.who)} {REVIEW_SAID[review.state]}"
            break
    if not said:
        asked = [w for w in new.asked if w not in old.asked]
        if asked:
            said = f"{', '.join(after.shown(w) for w in asked)} asked to review"
        elif new.comments_open != old.comments_open:
            n = new.comments_open
            said = f"{n} comment{'' if n == 1 else 's'} to resolve" if n else "all comments resolved"
        elif new.checks != old.checks:
            said = CHECK_SAID[new.checks]
        elif new.draft != old.draft:
            said = "is a draft again" if new.draft else "is ready for review"
        elif new.mergeable != old.mergeable:
            said = "has a merge conflict" if new.mergeable == "conflict" else "no longer conflicts"
        elif new.title != old.title:
            said = "was renamed"
    now = waits_words(after, new)
    return f"{said} · {now}" if said else f"now {now}"
