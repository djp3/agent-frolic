"""What the v3 mock-ups share. Everything in frolic_v2 still holds (the pipeline model: a
branch is working, or it waits on you, an agent, a person or another branch). v3 adds what
the feedback on v2 asked for:

- ONE HIERARCHY. Every base and every stack is on screen at once, in a single connected
  tree. A base's history runs down the page from its tip, and the commits that matter are
  named by their short hash: the ones a stack hangs from, and the one where another base
  branched off. So release-0.2 is not a second list; it hangs from the commit on main it
  was cut from. Use `tree_rows(repo)`.
- URGENCY. What needs effort is ordered by urgency: first whether anyone is waiting for it
  to continue, then how long it has been quiet. Use `needs_effort(prs)`, not `blockers()`.
- THE TOP LINE every v3 uses, taken from option 3: repos with their two small numbers, then
  how many agents are at work and when the data was refreshed. Use `top_line()`.
- THE FOUR LINES from option 5 ("At a prompt", "No agent", "Waits on", "Nothing to do") and
  the people line from option 1 ("have work" / "nothing to do"), as `effort_lines()` and
  `people_line()`.

Everything here is invented; nothing is read from GitHub, Jira or this machine.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from rich.text import Text

from frolic_v2 import (ACCENT, AXIS, CRIT, GOOD, GROUPS, HAIR, INK, INK2, ME, MUTED, PLANE, PR,  # noqa: F401
                       RAISED, REPOS, SERIOUS, STATUS, SURFACE, WARN, WORD, Canvas, Event, Pipe,
                       Worker, age_hours, agent, anchor_note, blockers, broken, chain, fit, flat,
                       free_people, is_stale, link, movers, people, pipe, point_label, push,
                       repo_status, root_cause, roster, rv, session_state, sessions, shown,
                       verdicts, work_of)

SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"          # the dots that turn where an agent is at work


def short(pr: PR) -> str:
    return f"pr-{pr.number:04d}"


# ---- one hierarchy: bases joined through commits ----------------------------------------------

@dataclass
class Base:
    name: str
    forks_from: str = ""         # the base it was branched from; "" for the trunk
    fork_behind: int = 0         # how many commits behind that base's tip it was branched
    ahead: int = 0               # commits made on it since


BASES: dict[str, list[Base]] = {
    "agent-frolic": [Base("main"), Base("release-0.2", "main", 40, 3)],
    "ledger-api": [Base("main"), Base("release-2.4", "main", 64, 5)],
    "web-console": [Base("main")],
    "infra-scripts": [Base("main")],
}


def sha(repo: str, base: str, behind: int) -> str:
    """A made-up short hash for the commit `behind` commits back from the tip of `base`."""
    return hashlib.sha1(f"{repo}:{base}~{behind}".encode()).hexdigest()[:7]


@dataclass
class Row:
    kind: str                    # "base" | "commit" | "branch"
    lead: str                    # the connector to draw before it
    pr: PR | None = None         # the branch, for kind "branch"
    base: str = ""               # the base this row belongs to (for "base": its own name)
    sha: str = ""                # "commit": its short hash; "base": the commit it branched from
    behind: int = 0              # commits behind the tip of its base
    label: str = ""              # "base": its name; "commit": "tip", "14 commits back"
    note: str = ""               # "base": "branched from main at 5d2ab43, 3 commits on"


def tree_rows(repo: str, prs: list[PR] | None = None) -> list[Row]:
    """The whole map as one tree, top to bottom. `prs` defaults to the repo's data; pass
    your own copy if you change it.

        main
        ┬ 3f9a2c1 tip
        ├ af-31-pr-0012-…                 a stack hanging from that commit
        │ └ af-32-pr-0013-…
        ┼ 8be04d7 14 commits back
        ├ af-27-pr-0009-…
        ┼ 5d2ab43 40 commits back
        ├ release-0.2                     a base cut from that commit
        │ ┬ e91f0b6 tip
        │ └ af-51-pr-0024-…
        ┼ a0c3f58 52 commits back
        └ af-45-pr-0019-…
    """
    prs = REPOS[repo] if prs is None else prs
    bases = BASES.get(repo, [Base("main")])
    rows: list[Row] = []

    def visit(pr: PR, lead: str, last: bool, base: str, behind: int) -> None:
        rows.append(Row("branch", lead + ("└ " if last else "├ "), pr, base, behind=behind))
        for i, child in enumerate(pr.children):
            visit(child, lead + ("  " if last else "│ "), i == len(pr.children) - 1, base, behind)

    def emit(base: Base, lead: str) -> None:
        stacks: dict[int, list[PR]] = {}
        for pr in prs:
            if pr.base == base.name:
                stacks.setdefault(pr.behind, []).append(pr)
        forks: dict[int, list[Base]] = {}
        for other in bases:
            if other.forks_from == base.name:
                forks.setdefault(other.fork_behind, []).append(other)
        points = sorted({0} | set(stacks) | set(forks))
        for i, behind in enumerate(points):
            here = sha(repo, base.name, behind)
            rows.append(Row("commit", lead + ("┬ " if i == 0 else "┼ "), base=base.name, sha=here,
                            behind=behind, label=point_label(behind)))
            hanging: list[PR | Base] = [*stacks.get(behind, []), *forks.get(behind, [])]
            for j, item in enumerate(hanging):
                last = i == len(points) - 1 and j == len(hanging) - 1
                if isinstance(item, PR):
                    visit(item, lead, last, base.name, behind)
                else:
                    rows.append(Row("base", lead + ("└ " if last else "├ "), base=item.name, sha=here,
                                    behind=behind, label=item.name,
                                    note=f"branched from {base.name} at {here}, {item.ahead} commits on"))
                    emit(item, lead + ("  " if last else "│ "))

    for base in bases:
        if not base.forks_from:
            rows.append(Row("base", "", base=base.name, label=base.name))
            emit(base, "")
    return rows


# ---- what needs effort, most urgent first -------------------------------------------------------

@dataclass
class Effort:
    kind: str                    # "needs you" | "no agent" | "person"
    who: str                     # "you", or the person(s) it waits on
    pr: PR                       # the branch where the wait ends
    pipe: Pipe
    behind: list[PR] = field(default_factory=list)      # other branches queued behind this one
    waiting: int = 0             # agents idle because of it: its own, plus the ones queued behind
    sentence: str = ""

    @property
    def yours(self) -> bool:     # something you can fix yourself: these are the blue ones
        return self.kind != "person"

    @property
    def urgency(self) -> str:
        held = (f"{self.waiting} agent{'' if self.waiting == 1 else 's'} waiting · "
                if self.waiting else "")
        return f"{held}quiet {self.pr.age}"


def needs_effort(prs: list[PR]) -> list[Effort]:
    """Every end of a wait that is not working, most urgent first: whether any agent is
    waiting for it to continue, then how long it has been quiet."""
    out: list[Effort] = []
    for b in blockers(prs):
        own = agent(b.pr)
        idle_own = own is not None and session_state(own) in ("at its prompt", "waiting")
        out.append(Effort(b.kind, b.who, b.pr, b.pipe, b.behind, len(b.behind) + idle_own, b.sentence))
    return sorted(out, key=lambda e: (e.waiting == 0, -age_hours(e.pr.age)))


# ---- pieces every v3 shows the same way ----------------------------------------------------------

def agents_at_work(prs: list[PR]) -> int:
    return sum(1 for pr in flat(prs) for worker in pr.workers if pr.mine and worker.live)


def top_line(current: str, data: dict[str, list[PR]] | None = None, frame: int = 0,
             refreshed: str = "2m ago", paused: bool = False, breath: str = ACCENT) -> Text:
    """Option 3's top line, the one every v3 uses: each repo with a blue ●n (what you can fix
    there) and a grey ○n (people being waited on), then the agents at work and the refresh.
    `data` lets a mock-up show its own changing copy of the current repo; `frame` turns the
    dots; `breath` is the blue to use this frame if you make it breathe."""
    data = data or REPOS
    text = Text()
    for name in REPOS:
        here = name == current
        yours, nudge, _ = repo_status(data.get(name, REPOS[name]))
        text.append(name, style=f"bold underline {INK}" if here else MUTED)
        if yours:
            text.append(" ●", style=breath)
            text.append(str(yours), style=INK if here else INK2)
        if nudge:
            text.append(" ○", style=MUTED)
            text.append(str(nudge), style=MUTED)
        if not yours and not nudge:
            text.append(" ·", style=AXIS)
        text.append("      ")
    working = agents_at_work(data.get(current, REPOS[current]))
    if paused:
        text.append("❚❚ paused", style=MUTED)
    elif working:
        text.append(SPIN[frame % len(SPIN)], style=INK)
        text.append(f" {working} agent{'' if working == 1 else 's'} at work", style=INK2)
    else:
        text.append("no agent is at work", style=MUTED)
    return text.append(f" · refreshed {refreshed}", style=MUTED)


def effort_lines(prs: list[PR], label_w: int = 16) -> list[Text]:
    """Option 5's four lines: At a prompt, No agent, Waits on, Nothing to do. A line is left
    out when it has nothing to say."""
    found = needs_effort(prs)

    def item(e: Effort, words: bool) -> str:
        said = short(e.pr) + (f" {e.pipe.what}" if words else "")
        return said + (f" (holds up {', '.join(short(p) for p in e.behind)})" if e.behind else "")

    def line(label: str, items: list[str], yours: bool) -> Text:
        text = Text("● " if yours else "  ", style=ACCENT)
        text.append(f"{label:<{label_w}}", style=INK if yours else MUTED)
        return text.append(" · ".join(items), style=INK if yours else MUTED)

    lines: list[Text] = []
    prompt = [item(e, True) for e in found if e.kind == "needs you"]
    unassigned = [item(e, True) for e in found if e.kind == "no agent"]
    waited: dict[str, list[str]] = {}
    for e in found:
        if e.kind == "person":
            for name in e.who.split(","):
                waited.setdefault(name.strip(), []).append(item(e, False))
    if prompt:
        lines.append(line("At a prompt", prompt, True))
    if unassigned:
        lines.append(line("No agent", unassigned, True))
    if waited:
        lines.append(line("Waits on", [f"{name} for {', '.join(items)}"
                                       for name, items in waited.items()], False))
    if free_people(prs):
        lines.append(line("Nothing to do", free_people(prs), True))
    if not lines:
        lines.append(Text("  Everything that can move is moving." if flat(prs) else "  Nothing open.",
                          style=MUTED))
    return lines


def people_line(prs: list[PR]) -> Text:
    """Option 1's people line: who has work and who has nothing to do. No counts."""
    busy = [name for name, has_work in roster(prs) if has_work]
    free = free_people(prs)
    text = Text("have work  ", style=MUTED)
    text.append(" · ".join(busy) or "nobody", style=INK2)
    if free:
        text.append("      ● ", style=ACCENT)
        text.append("nothing to do  ", style=INK)
        text.append(" · ".join(free), style=f"bold {INK}")
    return text
