"""What the v5 mock-ups share. The palette, the tree of commit, branch and stack, and the
urgency rule come from the earlier rounds. v5 changes the model underneath, after the
feedback on v4:

- TWO KINDS OF ACTOR. Humans, and agents. Every agent runs under one human, its operator.
  Other people's agents are visible too (assume a connection that reports what every agent
  is working on). On screen an agent always sits beneath its human; a human doing the work
  alone is one line.
- STATUS COMES FROM GITHUB. A PR has reviews asked of zero, one or more people, each
  reviewer's latest word, comments still to resolve, and fixes a reviewer has to approve.
  Who must act next follows from that. Local review-notes branches are no longer rows: an
  agent reviewing a PR shows beneath the reviewer, on that PR.
- WHERE A PR STANDS, `stand(world, pr)`: its owner side (the author and the author's agents
  on it), the other parties it waits on (reviewers and their agents, or another branch and
  whoever that one waits on), whether any agent is at work on it, and the humans who must
  act before it moves again.
- THE BAY, `bay_rows(world, pr)`: three cells per line, owner | state | waits on, and as
  many lines as the PR needs.

      owner          state      waits on
        you                       mira                a reviewer was asked;
        └ · agent 01 waiting      └ ⠋ agent 01        her agent is reviewing
                                  kenji               a second reviewer, nobody on it
      ● you          to act                           your move, nobody on it: one line
        tobias                                        his agent sits at its prompt:
        └ ▷ agent 01 at prompt                        it waits on him, so it is grey

  The waits-on cell names OTHER parties only. When the owner side holds the move, the state
  cell says so ("to act", "at prompt") and waits on stays empty.
- BLUE means one thing: idle, and YOU can fix it. Someone else's idle agent waits on its
  operator and is grey.
- SEVERAL REPOS AT ONCE. `tree5(world, names)` is one tree: repo, then its base (main),
  then commit, branch, stack. Which repos are shown is the app's own choice (a menu);
  `top_line5` lists all of them and marks the shown ones.
- FOCUS, the keeper from option 1: `focuses()` lists everyone, each human with all their
  agents, each human alone, and each agent; `in_focus()` says whether a PR belongs.
- THINGS HAPPEN. `happenings()` is a short script both mock-ups can loop over; each step
  changes a World and returns the PR numbers whose rows changed (flash those).

The data lives in a `World` so a mock-up can change its own copy: `fresh_world()`.
Everything here is invented; nothing is read from GitHub, Jira or this machine.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Callable

from rich.text import Text

from frolic_v4 import (ACCENT, AXIS, BASES, CRIT, GOOD, HAIR, INK, INK2, ME, MUTED, PLANE, PR,  # noqa: F401
                       RAISED, SERIOUS, SPIN, STATUS, SURFACE, WARN, WORD, Canvas, Segments,
                       age_hours, flat, is_stale, movers, rv, session_state, sha, short, shown,
                       tree_rows, verdicts, write)
from frolic_v4 import REPOS as _V4

SHOWN_AT_START = ["agent-frolic", "web-console"]     # the repos a mock-up shows when it opens


def use_accent(color: str) -> None:
    """Let a mock-up choose the color that marks "idle, and you can fix it". The shared
    cells and the top line draw with it from then on; the default is the palette's blue."""
    global ACCENT
    ACCENT = color


# ---- the two kinds of actor ----------------------------------------------------------------------

@dataclass
class Agent:
    operator: str                # the human it runs under: "you", "mira"
    serial: str                  # "01": unique only together with its operator and its PR
    number: int                  # the PR it is on
    role: str                    # "author": works on the PR for its author;
                                 # "review": reviews the PR for a reviewer
    state: str                   # "working" | "at its prompt" | "waiting"
    note: str = ""               # what it is doing, or why it is idle
    odd_name: str = ""           # set when the session's name breaks the naming pattern

    @property
    def name(self) -> str:
        return f"agent {self.serial}"

    @property
    def working(self) -> bool:
        return self.state == "working"

    @property
    def key(self) -> tuple[str, int, str]:
        """What identifies it across copies of the world."""
        return (self.operator, self.number, self.serial)


@dataclass
class World:
    repos: dict[str, list[PR]]
    agents: dict[int, list[Agent]] = field(default_factory=dict)      # by PR number

    def on(self, pr: PR) -> list[Agent]:
        """Every agent on a PR, whoever operates it."""
        return self.agents.get(pr.number, [])

    def all(self, names: list[str] | None = None) -> list[PR]:
        """Every PR and local branch of the named repos (all repos when None), in map order."""
        return [pr for name in (names or list(self.repos)) for pr in flat(self.repos[name])]

    def find(self, number: int) -> PR:
        return next(pr for pr in self.all() if pr.number == number)

    def repo_of(self, pr: PR) -> str:
        return next(name for name, prs in self.repos.items() if any(p is pr for p in flat(prs)))


_OTHERS = [  # other people's agents: (PR number, operator, serial, role, state, note)
    (13, "mira", "01", "review", "waiting", "idle until the comments are resolved · 5h"),
    (16, "tobias", "01", "author", "at its prompt", "stopped on a conflict in reader.py · 4d"),
    (24, "mira", "01", "review", "working", "reading token_refresh.py · just now"),
    (352, "kenji", "01", "author", "working", "writing the retry tests · 3m ago"),
    (360, "mira", "01", "review", "at its prompt", "review notes ready, not posted · 2h"),
    (338, "ana", "01", "author", "at its prompt", "stopped on a conflict in rounding.py · 5d"),
    (149, "lucia", "01", "author", "working", "wiring the download button · 8m ago"),
]


def _build() -> World:
    """The v4 data, reshaped: your sessions become agents under "you" (a session on a local
    review-notes branch becomes a reviewing agent on the PR it reviews, and that branch
    leaves the tree), and other people get agents of their own."""
    repos = copy.deepcopy(_V4)
    agents: dict[int, list[Agent]] = {}
    for prs in repos.values():
        for pr in flat(prs):
            if pr.state == "findings":
                continue
            for worker in (pr.workers if pr.mine else []):
                if session_state(worker) != "ended":
                    agents.setdefault(pr.number, []).append(Agent(
                        "you", worker.serial, pr.number, "author", session_state(worker),
                        worker.note, worker.name or ""))
            for child in pr.children:
                if child.state != "findings":
                    continue
                for worker in child.workers:
                    if session_state(worker) != "ended":
                        agents.setdefault(pr.number, []).append(Agent(
                            "you", worker.serial, pr.number, "review", session_state(worker),
                            worker.note))
            pr.children = [child for child in pr.children if child.state != "findings"]
            pr.next = (pr.next[0], said(pr.next[1]))
            if pr.unit == "threads":
                pr.unit = "comments"
    for number, operator, serial, role, state, note in _OTHERS:
        agents.setdefault(number, []).append(Agent(operator, serial, number, role, state, note))
    return World(repos, agents)


def said(words: str) -> str:
    """GitHub calls them comments to resolve; the older data said threads."""
    return words.replace("threads", "comments").replace("thread", "comment")


# ---- where a PR stands -----------------------------------------------------------------------------

@dataclass
class Party:
    human: str = ""              # "you", "mira"; "" when this is a link to another branch
    agents: list[Agent] = field(default_factory=list)    # that human's agents on the PR
    branch: PR | None = None     # the branch waited on, when this is a link
    blue: bool = False           # idle, and you can fix it


@dataclass
class Stand:
    pr: PR
    owner: Party                 # the author, and the author's agents on it
    waits: list[Party]           # the OTHER parties it waits on; a link to another branch is
                                 # followed by whoever that branch waits on
    moving: bool                 # an agent is at work on it, here or where its wait ends
    working: list[Agent]         # the agents whose work keeps it moving
    ends: list[str]              # the humans who must act before it moves again
    what: str                    # the move that gets it going, in words
    end_pr: PR                   # the PR where its wait ends (itself, unless it is queued)

    @property
    def blue(self) -> bool:      # you are one of the humans who must act
        return "you" in self.ends

    @property
    def holds(self) -> bool:     # the owner side holds the move, and nobody is working
        return not self.moving and not self.waits and bool(self.ends)

    @property
    def queued(self) -> bool:    # it waits on another branch
        return self.end_pr is not self.pr


def stand(world: World, pr: PR) -> Stand:
    author = shown(pr.author)
    own = [a for a in world.on(pr) if a.operator == author and a.role == "author"]
    owner = Party(author, own)
    what = said(pr.next[1])
    at_work = [a for a in own if a.working]
    if at_work:
        return Stand(pr, owner, [], True, at_work, [], what, pr)
    if pr.waits_on:
        blocker = next(p for p in flat([pr.root()]) if p.number == pr.waits_on)
        sub = stand(world, blocker)
        tail = sub.waits if sub.waits else [sub.owner]
        return Stand(pr, owner, [Party(branch=blocker), *tail], sub.moving, sub.working,
                     sub.ends, sub.what, sub.end_pr)
    holders = movers(pr)
    others = [name for name in holders if name != author]
    if not others:                                       # the move is the owner's
        owner.blue = author == "you" and bool(holders)
        return Stand(pr, owner, [], False, [], [author] if holders else [], what, pr)
    waits = [Party(name, [a for a in world.on(pr) if a.operator == name and a.role == "review"])
             for name in others]
    at_work = [a for party in waits for a in party.agents if a.working]
    for party in waits:
        party.blue = party.human == "you" and not at_work
    return Stand(pr, owner, waits, bool(at_work), at_work, [] if at_work else others, what, pr)


def progress_words(world: World, pr: PR) -> str:
    """ "in progress" only while an agent is at work on this PR; otherwise "stalled" when
    GitHub shows the move partly done, "not started" when it shows nothing."""
    here = stand(world, pr)
    done, total = pr.progress
    counted = f"{done} of {total} {pr.unit}".strip() if total else ""
    partly = pr.started or done > 0
    if here.moving and not here.queued:
        return f"in progress, {counted}" if counted else "in progress"
    if partly and not here.queued:
        return f"stalled, {counted}" if counted else "stalled"
    return "not started"


def reviews(pr: PR) -> list[tuple[str, str]]:
    """Each reviewer GitHub knows about and their latest word: (who, "approved" | "changes"
    | "requested" | "rerequested"). WORD[verdict] gives (mark, color, words)."""
    return [(shown(event.who), event.verdict) for event in verdicts(pr).values()]


def review_segments(pr: PR, names: bool = True) -> Segments:
    """The review cell: each reviewer's mark (and name), then the comments still open."""
    found = reviews(pr)
    if not found and not pr.threads[0]:
        return [("·", AXIS)] if pr.is_pr else []
    out: Segments = []
    for who, verdict in found:
        mark, color, _ = WORD[verdict]
        out += [(mark, color), (f" {who}  " if names else "", INK2)]
    if pr.threads[0]:
        out += [("" if names or not found else " ", MUTED), ("◆", MUTED),
                (f"{pr.threads[0]} to resolve" if names else str(pr.threads[0]), INK)]
    return out


# ---- the bay: owner | state | waits on -------------------------------------------------------------

def human_cell(party: Party) -> Segments:
    if party.blue:
        return [("● ", ACCENT), (party.human, f"bold {INK}")]
    return [("  ", MUTED), (party.human, INK if party.human == "you" else INK2)]


def agent_cell(agent: Agent, spin: str = SPIN[0]) -> Segments:
    """An agent, always drawn beneath its human. The mark turns while it works; ▷ is an
    agent at its prompt, · one that waits. None of it is blue: the blue mark, when there is
    one, is on the human above."""
    yours = agent.operator == "you"
    mark, style = {"working": (spin, INK), "at its prompt": ("▷", INK if yours else MUTED),
                   "waiting": ("·", MUTED)}[agent.state]
    name = INK if agent.working or (yours and agent.state == "at its prompt") else INK2
    out: Segments = [("  └ ", AXIS), (mark + " ", style), (agent.name, name)]
    if agent.odd_name:
        out.append((" ≠", f"bold {CRIT}"))
    return out


def agent_state(agent: Agent) -> Segments:
    yours = agent.operator == "you"
    return {"working": [("working", INK2)],
            "at its prompt": [("at prompt", INK if yours else INK2)],
            "waiting": [("waiting", MUTED)]}[agent.state]


def bay_rows(world: World, pr: PR, spin: str = SPIN[0]) -> list[tuple[Segments, Segments, Segments]]:
    """The bay for one PR: a list of (owner cell, state cell, waits-on cell), one per line,
    as many lines as it needs. The owner side and the waits-on side stack independently."""
    here = stand(world, pr)
    left: list[tuple[Segments, Segments]] = []
    if here.owner.agents:
        state: Segments = []
    elif here.holds:
        state = [("to act", f"bold {INK}" if here.owner.blue else INK2)]
    else:
        state = [("waiting", MUTED)] if here.waits else []
    left.append((human_cell(here.owner), state))
    for agent in here.owner.agents:
        left.append((agent_cell(agent, spin), agent_state(agent)))
    right: list[Segments] = []
    for party in here.waits:
        if party.branch is not None:
            right.append([("  ", MUTED), (short(party.branch), INK2), (" →", MUTED)])
            continue
        right.append(human_cell(party))
        right += [agent_cell(agent, spin) for agent in party.agents]
    lines = max(len(left), len(right))
    left += [([], [])] * (lines - len(left))
    right += [[]] * (lines - len(right))
    return [(owner, state, waits) for (owner, state), waits in zip(left, right)]


# ---- one tree for several repos ----------------------------------------------------------------------

@dataclass
class Row5:
    kind: str                    # "repo" | "base" | "commit" | "branch"
    lead: str                    # the connector to draw before it
    repo: str
    pr: PR | None = None         # the branch, for kind "branch"
    label: str = ""              # repo: its name; base: its name; commit: "tip", "14 commits back"
    sha: str = ""                # commit: its short hash; base: the commit it was cut from
    note: str = ""               # base: "branched from main at 5d2ab43, 3 commits on"


def tree5(world: World, names: list[str]) -> list[Row5]:
    """The shown repos as one tree. A repo is the root; its trunk (main) is its child; the
    rest is v3's tree: commits by short hash, stacks hanging from them, another base hanging
    from the commit it was cut from.

        agent-frolic
        └ main
          ┬ 2a69e7e tip
          ├ af-31-pr-0012-…
          │ └ af-32-pr-0013-…
          ┼ 18deb8e 40 commits back
          ├ release-0.2
          │ ┬ 6935c77 tip
          │ └ af-51-pr-0024-…
        web-console
        └ main
          …
    """
    rows: list[Row5] = []
    for repo in names:
        rows.append(Row5("repo", "", repo, label=repo))
        for row in tree_rows(repo, world.repos[repo]):
            trunk = row.kind == "base" and not row.lead
            rows.append(Row5(row.kind, "└ " if trunk else "  " + row.lead, repo, row.pr,
                             row.label, row.sha, row.note))
    return rows


# ---- what needs effort, most urgent first ----------------------------------------------------------------

@dataclass
class Effort5:
    repo: str
    pr: PR                       # the PR where the wait ends
    stand: Stand
    behind: list[PR] = field(default_factory=list)      # the PRs queued behind it
    waiting: int = 0             # idle agents: the ones on it, and the ones queued behind

    @property
    def blue(self) -> bool:
        return self.stand.blue

    @property
    def who(self) -> str:        # the humans who must act: "you", "mira, kenji"
        return ", ".join(self.stand.ends)

    @property
    def urgency(self) -> str:
        held = (f"{self.waiting} agent{'' if self.waiting == 1 else 's'} waiting · "
                if self.waiting else "")
        return f"{held}quiet {self.pr.age}"


def needs_effort5(world: World, names: list[str]) -> list[Effort5]:
    """Every PR in the named repos that no agent is working on and that waits on a human,
    most urgent first: whether any agent sits idle because of it, then how long it has been
    quiet. PRs queued behind another are counted with the one they wait on."""
    found: dict[int, Effort5] = {}
    queued: list[tuple[PR, Stand]] = []
    for repo in names:
        for pr in flat(world.repos[repo]):
            here = stand(world, pr)
            if here.moving:
                continue
            if here.queued:
                queued.append((pr, here))
            elif here.ends:
                idle = sum(not a.working for a in world.on(pr))
                found[id(pr)] = Effort5(repo, pr, here, waiting=idle)
    for pr, here in queued:
        if id(here.end_pr) in found:
            found[id(here.end_pr)].behind.append(pr)
            found[id(here.end_pr)].waiting += sum(not a.working for a in world.on(pr))
    return sorted(found.values(), key=lambda e: (e.waiting == 0, -age_hours(e.pr.age)))


# ---- humans, each with their agents ---------------------------------------------------------------------

@dataclass
class Person:
    name: str                    # "you", "mira"
    agents: list[Agent] = field(default_factory=list)   # every agent they operate, in map order
    to_act: list[PR] = field(default_factory=list)      # the PRs that wait on this human
    owns: list[PR] = field(default_factory=list)        # the PRs they authored

    @property
    def has_work(self) -> bool:
        """Something waits on them, or one of their agents is at work. Never a count."""
        return bool(self.to_act) or any(a.working for a in self.agents)


def humans(world: World, names: list[str]) -> list[Person]:
    """You first, then everyone else who wrote, reviewed or operates something in the named
    repos, each with the agents they operate beneath them."""
    found: dict[str, Person] = {"you": Person("you")}
    for pr in world.all(names):
        here = stand(world, pr)
        found.setdefault(here.owner.human, Person(here.owner.human)).owns.append(pr)
        for who, _ in reviews(pr):
            found.setdefault(who, Person(who))
        for agent in world.on(pr):
            found.setdefault(agent.operator, Person(agent.operator)).agents.append(agent)
        if not here.queued:
            for name in here.ends:
                found.setdefault(name, Person(name)).to_act.append(pr)
    return list(found.values())


@dataclass
class Focus:
    kind: str = "all"            # "all" | "team" (a human and all their agents) | "human" | "agent"
    human: str = ""
    agent: tuple[str, int, str] | None = None            # Agent.key, for kind "agent"

    @property
    def label(self) -> str:
        if self.kind == "all":
            return "everyone"
        if self.kind == "team":
            return f"{self.human} and {'your' if self.human == 'you' else 'their'} agents"
        if self.kind == "human":
            return f"{self.human} alone"
        return f"{self.human} › agent {self.agent[2]} · pr-{self.agent[1]:04d}"


def focuses(world: World, names: list[str]) -> list[Focus]:
    """The steps of the focus key: everyone; then, per human, the human with all their
    agents, the human alone, and each of their agents."""
    out = [Focus()]
    for person in humans(world, names):
        out.append(Focus("team", person.name))
        if person.agents:
            out.append(Focus("human", person.name))
            out += [Focus("agent", person.name, agent.key) for agent in person.agents]
    return out


def in_focus(world: World, pr: PR, focus: Focus) -> bool:
    """Whether a PR belongs to what is in focus. A human's PRs are the ones they authored
    and the ones that wait on them; a team adds every PR one of their agents is on; an
    agent's are the PR it is on and the PRs kept waiting or moving by it."""
    if focus.kind == "all":
        return True
    here = stand(world, pr)
    if focus.kind == "agent":
        return (any(a.key == focus.agent for a in world.on(pr))
                or any(a.key == focus.agent for a in here.working)
                or here.end_pr.number == focus.agent[1])
    theirs = (here.owner.human == focus.human or focus.human in here.ends
              or any(party.human == focus.human for party in here.waits))
    if focus.kind == "human":
        return theirs
    return theirs or any(a.operator == focus.human for a in world.on(pr))


# ---- the top line, for several repos ----------------------------------------------------------------------

def repo_status5(world: World, repo: str) -> tuple[int, int]:
    """(things there that you can fix, other people something there waits on)."""
    found = needs_effort5(world, [repo])
    people = {name for e in found for name in e.stand.ends if name != "you"}
    return sum(e.blue for e in found), len(people)


def agents_working(world: World, names: list[str]) -> int:
    return sum(a.working for pr in world.all(names) for a in world.on(pr))


def top_line5(world: World, shown_repos: list[str], frame: int = 0,
              refreshed: str | None = "2m ago", paused: bool = False,
              at_work: bool = True) -> Text:
    """Option 3's top line, for several repos at once: every repo with a blue ●n (what you
    can fix there) and a grey ○n (people being waited on); the repos on screen are bold and
    underlined, the others muted. Then the agents at work in the shown repos and the refresh
    (pass refreshed=None to leave the refresh out and show freshness your own way, and
    at_work=False to leave out the count of agents at work; "paused" still shows)."""
    text = Text()
    for name in world.repos:
        here = name in shown_repos
        yours, nudge = repo_status5(world, name)
        text.append(name, style=f"bold underline {INK}" if here else MUTED)
        if yours:
            text.append(" ●", style=ACCENT)
            text.append(str(yours), style=INK if here else INK2)
        if nudge:
            text.append(" ○", style=MUTED)
            text.append(str(nudge), style=MUTED)
        if not yours and not nudge:
            text.append(" ·", style=AXIS)
        text.append("      ")
    working = agents_working(world, shown_repos)
    if paused:
        text.append("❚❚ paused", style=MUTED)
    elif not at_work:
        pass
    elif working:
        text.append(SPIN[frame % len(SPIN)], style=INK)
        text.append(f" {working} agent{'' if working == 1 else 's'} at work", style=INK2)
    else:
        text.append("no agent is at work", style=MUTED)
    if refreshed is None:
        return text
    return text.append(f" · refreshed {refreshed}", style=MUTED)


# ---- things that happen ---------------------------------------------------------------------------------------

@dataclass
class Happening:
    repo: str
    say: str                                   # one line, for a ticker
    apply: Callable[[World], list[int]]        # changes the world; returns the PR numbers to flash


def _one(world: World, number: int, operator: str, role: str = "author") -> Agent:
    return next(a for a in world.agents[number] if a.operator == operator and a.role == role)


def _resolved_one(world: World) -> list[int]:
    pr = world.find(13)
    pr.progress, pr.threads, pr.age = (2, 3), (2, 7), "now"
    pr.next = ("you", "reply to the last comment")
    _one(world, 13, "you").note = "replied on review_state.py · just now"
    return [13]


def _review_lands(world: World) -> list[int]:
    pr = world.find(24)
    pr.history.append(rv(1, "mira", "changes", "now", 2))
    pr.state, pr.threads, pr.age = "changes", (2, 2), "now"
    pr.next = ("tobias", "resolve 2 comments")
    agent = _one(world, 24, "mira", "review")
    agent.state, agent.note = "waiting", "idle until the comments are resolved"
    return [24, 25]


def _agent_assigned(world: World) -> list[int]:
    world.agents.setdefault(20, []).append(
        Agent("you", "02", 20, "author", "working", "reading keychain.py · just now"))
    world.find(20).age = "now"
    return [20]


def _draft_done(world: World) -> list[int]:
    pr = world.find(17)
    pr.next, pr.ci, pr.age = ("you", "open the PR"), "pass", "now"
    agent = _one(world, 17, "you")
    agent.state, agent.note = "at its prompt", "draft finished, waiting at its prompt"
    return [17, 21]


def _approval(world: World) -> list[int]:
    pr = world.find(18)
    pr.history.append(rv(1, "ana", "approved", "now"))
    return [18]


def _notes_ready(world: World) -> list[int]:
    pr = world.find(9)
    pr.progress, pr.started, pr.next = (9, 9), True, ("you", "post the review")
    agent = _one(world, 9, "you", "review")
    agent.state, agent.note = "at its prompt", "review notes ready, waiting to post"
    return [9, 14]


def _back_to_work(world: World) -> list[int]:
    agent = _one(world, 16, "tobias")
    agent.state, agent.note = "working", "rebasing onto main · just now"
    world.find(16).age = "now"
    return [16]


def _reviewer_agent(world: World) -> list[int]:
    world.agents.setdefault(15, []).append(
        Agent("mira", "01", 15, "review", "working", "reading stack_inference.py · just now"))
    world.find(15).age = "now"
    return [15]


def _fix_pushed(world: World) -> list[int]:
    pr = world.find(146)
    pr.history.append(rv(2, "ana", "rerequested", "now"))
    pr.state, pr.progress, pr.threads, pr.age = "rereview", (1, 1), (0, 3), "now"
    pr.next = ("ana", "approve the fix")
    agent = _one(world, 146, "you")
    agent.state, agent.note = "waiting", "idle until ana approves the fix"
    return [146]


def _approved_fix(world: World) -> list[int]:
    pr = world.find(355)
    pr.history.append(rv(2, "lucia", "approved", "now"))
    pr.state, pr.next, pr.age = "ready", ("you", "merge"), "now"
    agent = _one(world, 355, "you")
    agent.state, agent.note = "at its prompt", "approved, waiting at its prompt"
    return [355]


def happenings() -> list[Happening]:
    """A loop of things that happen, in order. After the last one, start again from
    `fresh_world()`."""
    return [
        Happening("agent-frolic", "your agent on pr-0013 resolved a comment: 2 of 3", _resolved_one),
        Happening("agent-frolic", "mira's agent finished its review of pr-0024: mira asked for changes, 2 comments", _review_lands),
        Happening("agent-frolic", "you put an agent on pr-0020", _agent_assigned),
        Happening("agent-frolic", "your agent on pr-0017 finished the draft and sits at its prompt", _draft_done),
        Happening("agent-frolic", "ana approved pr-0018", _approval),
        Happening("web-console", "your agent on pr-0146 resolved the last comment: ana to approve the fix", _fix_pushed),
        Happening("agent-frolic", "your agent on pr-0009 has its review notes ready and sits at its prompt", _notes_ready),
        Happening("agent-frolic", "tobias's agent on pr-0016 is back at work", _back_to_work),
        Happening("agent-frolic", "mira put an agent on the re-review of pr-0015", _reviewer_agent),
        Happening("ledger-api", "lucia approved the fix on pr-0355: yours to merge", _approved_fix),
    ]


WORLD: World = _build()


def fresh_world() -> World:
    """A copy of the made-up world as it is when a mock-up opens."""
    return copy.deepcopy(WORLD)
