"""What the v2 mock-ups share. It takes the made-up data of frolic_common and reshapes it
around the feedback on v1. The question the v2s answer is "is every agent working, and if
not, what gets it going again?".

The model, in the words the screens should use:

- A branch is WORKING when one of your sessions is at work on it right now. That is the only
  thing "in progress" means. Other people's agents cannot be seen, so their moves are never
  shown as in progress.
- A branch that is not being worked on WAITS ON something: on you (its session sits at a
  prompt), on an agent being assigned (the move is yours and no session is on it), on a
  person (a review, their own PR), or on another branch (usually the one below it in the
  stack). Waiting on a branch chains: follow it to the end to find what really holds it up.
- A move that is partly done according to GitHub (1 of 3 threads replied to) with nobody
  working on it is STALLED. One with nothing done is NOT STARTED.
- A BLOCKER is the end of a chain that is not working: you, a missing agent, or a person.
  Blockers are what need effort to get the pipeline going. The ones you can fix yourself
  (kinds "needs you" and "no agent") and people with nothing to do are what blue marks.
- People are never tallied. A person either has work or has nothing to do, and you can list
  what waits on them.
- Stacks are anchored on a branch (main, a release branch) at a point in its history. Under
  each anchor they are ordered from the tip backwards.

Everything here is invented; nothing is read from GitHub, Jira or this machine.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field

from frolic_common import (ACCENT, AXIS, CRIT, GOOD, GROUPS, HAIR, INK, INK2, ME, MUTED, PLANE,  # noqa: F401
                           PR, RAISED, SERIOUS, STATUS, SURFACE, WARN, WORD, Canvas, Event, Worker,
                           age_hours, broken, fit, flat, is_stale, link, movers, people, push, rv,
                           shown, verdicts)
from frolic_common import REPOS as _V1


# ---- the made-up data, v2 ------------------------------------------------------------------

def _find(prs: list[PR], number: int) -> PR:
    return next(pr for pr in flat(prs) if pr.number == number and pr.state != "findings")


def _review_branch(prs: list[PR], number: int) -> PR:
    return next(pr for pr in flat(prs) if pr.number == number and pr.state == "findings")


def _build() -> dict[str, list[PR]]:
    repos = copy.deepcopy(_V1)

    af = repos["agent-frolic"]
    pr = _find(af, 12)                       # ready to merge, and its session is gone
    pr.workers[0].mode = "ended"
    pr = _find(af, 13)                       # an agent is replying to threads right now
    pr.next, pr.progress, pr.unit = ("you", "reply to 3 threads"), (1, 3), "threads"
    pr = _find(af, 15)                       # mira began her second look, then stopped
    pr.progress, pr.unit = (2, 6), "files"
    pr.workers[0].mode, pr.workers[0].note = "waiting", "idle until mira looks again · 1d"
    pr = _find(af, 17)
    pr.workers[0].mode = "ended"
    pr = _find(af, 21)                       # its agent waits on the agent one branch down
    pr.waits_on, pr.next = 17, ("you", "open the PR once pr-0017 settles")
    pr.workers[0].live, pr.workers[0].mode = False, "waiting"
    pr.workers[0].note = "idle until pr-0017 settles · 40m"
    _review_branch(af, 13).workers[0].mode = "ended"
    pr = _find(af, 9)                        # your review of mira's PR, under way on a local branch
    pr.progress, pr.unit, pr.behind = (4, 9), "files", 14
    pr = _find(af, 14)
    pr.waits_on = 9
    pr = _find(af, 16)                       # tobias answered one thread and went quiet
    pr.behind, pr.progress, pr.unit = 31, (1, 2), "threads"
    pr = _find(af, 18)                       # the move is yours and the session sits at its prompt
    pr.workers[0].live, pr.workers[0].mode = False, "prompt"
    pr.workers[0].note = "waiting at its prompt · 35m"
    _find(af, 19).behind = 52
    pr = _find(af, 22)
    pr.next = ("you", "settle the overlap with pr-0019")
    _find(af, 20).workers[0].mode = "ended"
    af.append(PR("af-51", 24, "token-refresh-fix", "review", author="tobias", base="release-0.2",
                 title="Refresh the GitHub token before it expires",
                 history=[rv(1, "mira", "requested", "3h")], ci_note="12 of 12",
                 next=("mira", "review"), age="3h",
                 children=[
                     PR("af-52", 25, "release-notes", "local", title="Release notes for 0.2.1",
                        ci="none", merge="1 commit, not pushed", waits_on=24,
                        next=("you", "open the PR once pr-0024 lands"), age="20m",
                        workers=[Worker("01", False, "idle until pr-0024 lands · 20m", mode="waiting")]),
                 ]))

    led = repos["ledger-api"]
    pr = _find(led, 329)
    pr.progress, pr.unit = (2, 5), "threads"
    pr = _find(led, 355)
    pr.workers[0].mode, pr.workers[0].note = "waiting", "idle until lucia looks again · 2d"
    _find(led, 338).behind = 22
    _find(led, 312).behind = 87
    led.append(PR("led-240", 365, "backport-rounding-fix", "approved", author="ana",
                  base="release-2.4", title="Backport the rounding fix to 2.4",
                  history=[rv(1, "kenji", "approved", "6h")], threads=(0, 1), ci_note="48 of 48",
                  next=("ana", "merge"), age="6h"))

    web = repos["web-console"]
    pr = _find(web, 143)                     # findings written down, the review never posted
    pr.next, pr.started = ("you", "post the review"), True
    _review_branch(web, 143).workers[0].mode = "ended"
    pr = _find(web, 146)
    pr.next, pr.progress, pr.unit = ("you", "reply to 1 thread"), (0, 1), "threads"

    for prs in repos.values():
        link(prs)
    return repos


REPOS: dict[str, list[PR]] = _build()


# ---- where a stack is anchored ---------------------------------------------------------------

def anchors(prs: list[PR]) -> list[tuple[str, list[PR]]]:
    """The stacks grouped by the branch they are anchored on, main first; within an anchor,
    from the tip of that branch backwards through its history."""
    grouped: dict[str, list[PR]] = {}
    for pr in prs:
        grouped.setdefault(pr.base, []).append(pr)
    return [(name, sorted(roots, key=lambda pr: pr.behind))
            for name, roots in sorted(grouped.items(), key=lambda item: (item[0] != "main", item[0]))]


def point_label(behind: int) -> str:
    return "tip" if not behind else f"{behind} commits back"


def anchor_note(pr: PR) -> str:
    """Where a branch's stack sits on the branch it is anchored on, as a phrase."""
    root = pr.root()
    return (f"at the tip of {root.base}" if not root.behind
            else f"{root.behind} commits behind the tip of {root.base}")


@dataclass
class Row:
    kind: str                    # "anchor" | "point" | "branch"
    lead: str                    # the connector to draw before it
    pr: PR | None                # the branch, for kind "branch"
    anchor: str                  # the branch this part of the map is anchored on
    behind: int = 0              # for "point" and "branch": commits behind the anchor's tip
    label: str = ""              # for "anchor": its name; for "point": "tip", "14 commits back"


def tree_rows(prs: list[PR], points: bool = True) -> list[Row]:
    """The map as rows. An anchor row names a base branch. Its history runs down the page
    from the tip; a point row marks a commit that stacks hang from (pass points=False to
    leave those out). Branch rows use the thin two-column connectors of option 2:

        main
        ┬ tip
        ├ af-31-pr-0012-…
        │ └ af-32-pr-0013-…
        ├ af-46-pr-0020-…
        ┼ 14 commits back
        └ af-27-pr-0009-…
    """
    rows: list[Row] = []

    def visit(pr: PR, lead: str, last: bool, anchor: str, behind: int) -> None:
        rows.append(Row("branch", lead + ("└ " if last else "├ "), pr, anchor, behind))
        for i, child in enumerate(pr.children):
            visit(child, lead + ("  " if last else "│ "), i == len(pr.children) - 1, anchor, behind)

    for anchor, roots in anchors(prs):
        rows.append(Row("anchor", "", None, anchor, label=anchor))
        seen: set[int] = set()
        for i, pr in enumerate(roots):
            if points and pr.behind not in seen:
                seen.add(pr.behind)
                rows.append(Row("point", "┬ " if len(seen) == 1 else "┼ ", None, anchor,
                                pr.behind, point_label(pr.behind)))
            visit(pr, "", i == len(roots) - 1, anchor, pr.behind)
    return rows


# ---- is it moving, and if not, what does it wait on ------------------------------------------

def sessions(pr: PR) -> list[Worker]:
    """Your sessions on a branch, counting the ones on a local review branch made off it."""
    found = list(pr.workers) if pr.mine else []
    for child in pr.children:
        if child.state == "findings":
            found += child.workers
    return found


def session_state(worker: Worker) -> str:
    """ "working" | "at its prompt" | "waiting" | "ended" """
    if worker.live:
        return "working"
    return {"prompt": "at its prompt", "waiting": "waiting"}.get(worker.mode, "ended")


def agent(pr: PR) -> Worker | None:
    """The session assigned to a branch: the one at work, else the newest that has not ended."""
    found = sessions(pr)
    alive = [w for w in found if session_state(w) != "ended"]
    return next((w for w in alive if w.live), alive[-1] if alive else None)


@dataclass
class Pipe:
    state: str                   # "working" | "needs you" | "no agent" | "waiting"
    what: str                    # the next move, in words
    who: list[str]               # who holds it: "you" and/or other people's names
    on: str = ""                 # what it waits on: "you", "an agent", a person, or a branch key
    on_pr: PR | None = None      # the branch it waits on, when it waits on a branch
    partly: bool = False         # some of the move is done according to GitHub
    done: int = 0
    total: int = 0
    unit: str = ""
    agent: Worker | None = None  # your session assigned to it, if one is alive

    @property
    def counted(self) -> str:
        return f"{self.done} of {self.total} {self.unit}".strip() if self.total else ""

    @property
    def progress(self) -> str:
        """ "in progress" only while an agent works on it; otherwise "stalled" or "not started". """
        word = "in progress" if self.state == "working" else ("stalled" if self.partly else "not started")
        return f"{word}, {self.counted}" if self.counted and (self.partly or self.state == "working") else word

    @property
    def waits(self) -> str:
        """What it waits on, as a phrase: "" when working."""
        return {"working": "", "needs you": "waits on you", "no agent": "waits on an agent"
                }.get(self.state, f"waits on {self.on}")


def pipe(pr: PR) -> Pipe | None:
    """Where a branch stands in the pipeline, or None for a local review branch (its work
    belongs to the branch it reviews)."""
    who = movers(pr)
    if not who or pr.state == "findings":
        return None
    assigned = agent(pr)
    partly = pr.started or pr.progress[0] > 0
    base = dict(what=pr.next[1], who=who, partly=partly, done=pr.progress[0], total=pr.progress[1],
                unit=pr.unit, agent=assigned)
    if assigned and assigned.live:
        return Pipe("working", **base)
    if pr.waits_on:
        blocker = next(p for p in flat([pr.root()]) if p.number == pr.waits_on and p.state != "findings")
        return Pipe("waiting", on=blocker.key, on_pr=blocker, **base)
    if "you" in who:
        if assigned and session_state(assigned) == "at its prompt":
            return Pipe("needs you", on="you", **base)
        return Pipe("no agent", on="an agent", **base)
    return Pipe("waiting", on=", ".join(who), **base)


def chain(pr: PR) -> list[PR]:
    """The branch, then the branch it waits on, and so on to the end of the wait."""
    out = [pr]
    while (here := pipe(out[-1])) and here.on_pr is not None and here.on_pr not in out:
        out.append(here.on_pr)
    return out


def root_cause(pr: PR) -> tuple[Pipe | None, PR]:
    """What really holds a branch up: the pipe at the end of its chain, and that branch."""
    end = chain(pr)[-1]
    return pipe(end), end


@dataclass
class Blocker:
    kind: str                    # "needs you" | "no agent" | "person"
    who: str                     # "you", or the person(s) it waits on
    pr: PR                       # the branch where the wait ends
    pipe: Pipe
    behind: list[PR] = field(default_factory=list)      # other branches waiting on this one
    sentence: str = ""

    @property
    def yours(self) -> bool:     # something you can fix yourself: these are the blue ones
        return self.kind != "person"


def blockers(prs: list[PR]) -> list[Blocker]:
    """What needs effort to get the pipeline going: every end of a wait that is not working.
    Yours first (a session at its prompt, a move with no agent), then people to nudge."""
    found: dict[int, Blocker] = {}
    for pr in flat(prs):
        end_pipe, end = root_cause(pr)
        if end_pipe is None or end_pipe.state == "working":
            continue
        if id(end) not in found:
            kind = end_pipe.state if end_pipe.state in ("needs you", "no agent") else "person"
            who = "you" if kind != "person" else end_pipe.on
            sentence = {
                "needs you": f"{end.key}: a session is at its prompt, waiting for you to {end_pipe.what}",
                "no agent": f"{end.key}: {end_pipe.what}, and no agent is assigned",
                "person": f"{end.key}: waits on {end_pipe.on} to {end_pipe.what} ({end_pipe.progress})",
            }[kind]
            found[id(end)] = Blocker(kind, who, end, end_pipe, sentence=sentence)
        if pr is not end:
            found[id(end)].behind.append(pr)
    return sorted(found.values(), key=lambda b: (not b.yours, b.kind, -age_hours(b.pr.age)))


# ---- does everyone have work ------------------------------------------------------------------

def roster(prs: list[PR]) -> list[tuple[str, bool]]:
    """(person, has any work) for you and everyone else on the repo. No counts, on purpose."""
    everything = flat(prs)
    return [(name, any(name in movers(pr) for pr in everything)) for name in people(prs)]


def free_people(prs: list[PR]) -> list[str]:
    """People on the repo with nothing to do: they could be given work."""
    return [name for name, has_work in roster(prs) if not has_work and name != "you"]


def work_of(prs: list[PR], name: str) -> list[PR]:
    """The branches whose next move this person holds: what they have to do, as a list."""
    return [pr for pr in flat(prs) if name in movers(pr) and pr.state != "findings"]


def repo_status(prs: list[PR]) -> tuple[int, int, int]:
    """The little bit of status for a repo tab: (blockers you can fix yourself, people a
    branch waits on, people with nothing to do)."""
    found = blockers(prs)
    nudge = {name.strip() for b in found if not b.yours for name in b.who.split(",")}
    return sum(b.yours for b in found), len(nudge), len(free_people(prs))
