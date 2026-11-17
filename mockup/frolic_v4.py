"""What the v4 mock-ups share. Everything in frolic_v3 still holds (one connected hierarchy
of commit, branch and stack; urgency order; the top line; the four lines). v4 adds what the
feedback on v3 asked for: WHO comes first.

- OWNER. Every branch has an owner, and it is the first thing on its row. On a branch of
  yours the owner is the agent (the Claude Code session) assigned to it, or "no agent". On
  someone else's branch it is that person; their agents cannot be seen. Use `owner(pr)`.
- WAITS ON. Right beside the owner: nothing while the owner's agent works, otherwise who or
  what the branch waits on, read left to right along the chain to its end
  ("pr-0024 → mira"). This is the sequence the old "pipeline" column carried; the word
  pipeline is gone. Use `waits(pr)`.
- The two cells drawn the same way everywhere: `owner_segments(pr)`, `waits_segments(pr)`.

      owner         waits on          branch
      ⠋ agent 01                      ├ af-32-pr-0013-…        its agent is at work
      ▷ agent 01    ● you             ├ af-44-pr-0018-…        its agent sits at its prompt
      · agent 01    pr-0024 → mira    │ └ af-52-pr-0025-…      its agent is idle, and why
      ● no agent    ● you             ├ af-31-pr-0012-…        your move, and nobody on it
        mira        ● you             ├ af-27-pr-0009-…        hers, and it waits on you
        tobias      mira              └ af-51-pr-0024-…        his, and it waits on her

- THE CAST, for putting people and agents at the top of a screen: `cast(prs)` lists you,
  each agent of yours, each branch that lacks one, and each other person, with what they
  own and what waits on them. No counts are meant to be shown as scores.

Blue still means one thing: idle, and you can fix it. The hierarchy is still commit, then
branch, then stack: use `tree_rows(repo)` from v3 and put these cells in front of it.

Everything here is invented; nothing is read from GitHub, Jira or this machine.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from frolic_v3 import (ACCENT, AXIS, BASES, CRIT, GOOD, GROUPS, HAIR, INK, INK2, ME, MUTED,  # noqa: F401
                       PLANE, PR, RAISED, REPOS, SERIOUS, SPIN, STATUS, SURFACE, WARN, WORD, Base,
                       Canvas, Effort, Event, Pipe, Row, Worker, age_hours, agent, agents_at_work,
                       anchor_note, blockers, broken, chain, effort_lines, fit, flat, free_people,
                       is_stale, link, movers, needs_effort, people, people_line, pipe,
                       point_label, push, repo_status, root_cause, roster, rv, session_state,
                       sessions, sha, short, shown, top_line, tree_rows, verdicts, work_of)

Segments = list[tuple[str, str]]             # (text, style) pairs laid end to end


# ---- who owns a branch ---------------------------------------------------------------------------

@dataclass
class Owner:
    kind: str                    # "agent" | "no agent" | "person" | "notes"
    name: str                    # "agent 01" | "no agent" | "mira" | "notes"
    state: str = ""              # an agent's: "working" | "at its prompt" | "waiting"
    fixable: bool = False        # idle, and you can fix it: this is what blue marks
    worker: Worker | None = None  # the session, for kind "agent"

    @property
    def working(self) -> bool:
        return self.state == "working"


def agent_name(worker: Worker) -> str:
    return f"agent {worker.serial}"


def owner(pr: PR) -> Owner:
    """Who owns a branch: your agent on it (or "no agent") when it is yours, else its author.
    A local review branch belongs to the agent writing the notes, while that agent is alive."""
    here = pipe(pr)
    if pr.state == "findings":
        alive = [w for w in pr.workers if session_state(w) != "ended"]
        if not alive:
            return Owner("notes", "notes")
        return Owner("agent", agent_name(alive[-1]), session_state(alive[-1]), worker=alive[-1])
    if not pr.mine:
        return Owner("person", pr.author)
    session = agent(pr)
    if session is None:
        return Owner("no agent", "no agent", fixable=here is not None and here.state == "no agent")
    state = session_state(session)
    return Owner("agent", agent_name(session), state,
                 fixable=state == "at its prompt", worker=session)


# ---- what a branch waits on ----------------------------------------------------------------------

@dataclass
class Wait:
    kind: str                    # "working" | "you" | "agent" | "person"
    end: str = ""                # where the wait ends: "you", "agent 01", "mira"; "" when working
    links: list[PR] = field(default_factory=list)       # the branches in between, nearest first
    fixable: bool = False        # the wait ends at you
    what: str = ""               # the move that ends the wait, in words
    end_pr: PR | None = None     # the branch where the wait ends

    @property
    def said(self) -> str:
        """The whole cell as plain words: "pr-0024 → mira", "you", "" while working."""
        return " → ".join([short(p) for p in self.links] + ([self.end] if self.end else []))


def waits(pr: PR) -> Wait | None:
    """What a branch waits on, or None for a local review branch (notes wait on nothing).
    kind "working": the owner's agent is at work, so the cell is empty. kind "agent": the
    wait ends at an agent of yours that is at work (your review of someone's branch, or the
    branch below in the stack). kind "you": it ends at you. kind "person": at someone else."""
    here = pipe(pr)
    if here is None:
        return None
    if here.state == "working":
        if pr.mine:
            return Wait("working", what=here.what, end_pr=pr)
        return Wait("agent", agent_name(here.agent), what=here.what, end_pr=pr)
    links = chain(pr)[1:]
    end_pr = links[-1] if links else pr
    end = pipe(end_pr)
    if end is None:
        return Wait("person", here.on, links, what=here.what, end_pr=end_pr)
    if end.state == "working":
        return Wait("agent", agent_name(end.agent), links, what=end.what, end_pr=end_pr)
    if end.state in ("needs you", "no agent"):
        return Wait("you", "you", links, fixable=True, what=end.what, end_pr=end_pr)
    return Wait("person", end.on, links, what=end.what, end_pr=end_pr)


# ---- the two cells, drawn the same way everywhere --------------------------------------------------

def owner_segments(pr: PR, spin: str = SPIN[0]) -> Segments:
    """The owner cell: a two-cell mark, then the name. The mark turns while the agent works,
    is a blue ▷ for an agent at its prompt, a blue ● where your move has no agent, and a
    quiet · for an agent that waits on someone else."""
    who = owner(pr)
    if who.kind == "agent":
        return {
            "working": [(spin + " ", INK), (who.name, INK)],
            "at its prompt": [("▷ ", ACCENT), (who.name, f"bold {INK}")],
            "waiting": [("· ", MUTED), (who.name, INK2)],
        }[who.state]
    if who.kind == "no agent":
        return ([("● ", ACCENT), (who.name, f"bold {INK}")] if who.fixable
                else [("  ", MUTED), (who.name, MUTED)])
    return [("  ", MUTED), (who.name, INK2 if who.kind == "person" else MUTED)]


def waits_segments(pr: PR, spin: str = SPIN[0], keys: bool = False) -> Segments:
    """The waits-on cell: empty while the owner's agent works; otherwise the chain, read
    left to right to where it ends. `keys` spells the branches in between in full."""
    wait = waits(pr)
    if wait is None or wait.kind == "working":
        return []
    out: Segments = []
    for hop in wait.links:
        out += [(hop.key if keys else short(hop), INK2), (" → ", MUTED)]
    if wait.kind == "you":
        return out + [("● ", ACCENT), ("you", f"bold {INK}")]
    if wait.kind == "agent":
        return out + [(spin + " ", INK), (wait.end, INK2)]
    return out + [(wait.end, INK2)]


def write(canvas: Canvas, x: int, y: int, segments: Segments, width: int, style: str = "") -> int:
    """Lay segments end to end from x, cut at `width` cells with an ellipsis. `style`, when
    given, replaces every segment's own (for dimmed rows). Returns the cells used."""
    room = width
    for text, own in segments:
        if room <= 0:
            break
        if len(text) > room:
            text = text[:max(0, room - 1)] + "…"
        canvas.put(x, y, text, style or own)
        x += len(text)
        room -= len(text)
    return width - room


# ---- the cast: everyone who owns something or is waited on ----------------------------------------

@dataclass
class Member:
    kind: str                    # "you" | "agent" | "no agent" | "person"
    name: str                    # "you" | "agent 01" | "no agent" | "mira"
    state: str                   # agent: "working" | "at its prompt" | "waiting";
                                 # person: "has work" | "nothing to do"; otherwise ""
    pr: PR | None = None         # the one branch an agent is on, or the one that lacks an agent
    fixable: bool = False        # idle, and you can fix it
    owns: list[PR] = field(default_factory=list)        # the branches this one owns
    waited_by: list[PR] = field(default_factory=list)   # the branches whose wait ends here


def cast(prs: list[PR]) -> list[Member]:
    """You, then each agent of yours (in map order), then each branch of yours whose move
    has no agent, then every other person. An agent has exactly one branch, so its name is
    only unique together with it: show "agent 01 · pr-0013", never the serial alone."""
    everything = [pr for pr in flat(prs)]
    ends: dict[str, list[PR]] = {}
    for pr in everything:
        wait = waits(pr)
        if wait is None or wait.kind == "working":
            continue
        if wait.kind == "person":
            for name in wait.end.split(","):
                ends.setdefault(name.strip(), []).append(pr)
        else:
            ends.setdefault(wait.kind, []).append(pr)           # "you" or "agent"

    out = [Member("you", "you", "", fixable=bool(ends.get("you")), waited_by=ends.get("you", []))]
    missing: list[Member] = []
    for pr in everything:
        who = owner(pr)
        if who.kind == "agent":
            # an agent writing review notes works for the branch it reviews: waits end there
            target = pr.parent if pr.state == "findings" and pr.parent is not None else pr
            held = [p for p in ends.get("agent", [])
                    if (w := waits(p)) and w.end_pr is target and p is not pr]
            out.append(Member("agent", who.name, who.state, pr, who.fixable, [pr], held))
        elif who.kind == "no agent" and who.fixable:
            missing.append(Member("no agent", who.name, "", pr, True, [pr]))
    out += missing
    for name, has_work in roster(prs):
        if name == "you":
            continue
        out.append(Member("person", name, "has work" if has_work else "nothing to do",
                          fixable=not has_work,
                          owns=[pr for pr in everything if pr.author == name],
                          waited_by=ends.get(name, [])))
    return out


def owned_by(prs: list[PR], member: Member) -> list[PR]:
    """The branches to light when a member of the cast is picked: what it owns, plus what
    waits on it."""
    seen: list[PR] = []
    for pr in [*member.owns, *member.waited_by]:
        if pr not in seen:
            seen.append(pr)
    return seen
