"""Agent Frolic mock-up, option 4 v2: studio. The stack tree is gone. The page is arranged by
the only question that matters here, "what is each branch waiting on?", and every branch
carries a ribbon of the last three days so idle time is something you can see.

- Rows are grouped by what holds them up: you, a missing agent, a person, or nothing (an agent
  is at work). A branch that waits on another branch hangs under it with a ↳, so a chain of
  agents waiting on each other reads top to bottom, with the real cause at the top.
- The ribbon is time, oldest on the left, now on the right. A heavy line is an agent's time:
  light when the agent worked, blue when it could have been working and was not (a session
  at its prompt, a move with no agent). A thin line is time spent waiting on someone else.
- Blue appears nowhere else, so a calm page with no blue means every agent that can work is
  working. People are never counted: a person either heads a "waits on" group or is listed
  under "nothing to do".

    .venv/bin/python mockup/option4_studio_v2.py

Keys: up/down pick a branch or a person, tab and shift+tab switch repo, q quits.
"""
from __future__ import annotations

import math
import random
import re
from dataclasses import dataclass

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Static

from frolic_v2 import (ACCENT, AXIS, CRIT, GOOD, INK, INK2, ME, MUTED, PLANE, PR, RAISED, REPOS,
                       STATUS, SURFACE, WORD, Canvas, Pipe, age_hours, agent, anchor_note,
                       blockers, chain, flat, free_people, is_stale, pipe, repo_status,
                       session_state, sessions, verdicts, work_of)

QUIET = "#5c5b56"                # the thin line: time spent waiting on someone else
GUTTER = 23                      # the column the group names sit in
HOURS_PER_CELL = 2


@dataclass
class Line:
    """One row of the board: a branch, or a person with nothing to do."""
    group: str                   # what this part of the board waits on
    blue: bool                   # idle, could be working, and you can fix it
    pr: PR | None = None
    person: str = ""
    depth: int = 0               # 0 holds the group up; 1 waits on it; 2 waits on that
    first: bool = False          # the first row of its group carries the group's name


def clip(text: str, width: int) -> str:
    return text if len(text) <= width else text[:max(0, width - 1)] + "…"


def idle_for(pr: PR) -> str:
    """How long a branch has had nobody on it: from its session if it has one, else from
    the last thing that happened on GitHub."""
    mine = sessions(pr)
    if mine:
        found = re.search(r"(\d+[mhd])(?: ago)?$", (agent(pr) or mine[-1]).note)
        if found:
            return found.group(1)
    return pr.age


def waiting_on(prs: list[PR], end: PR, depth: int = 1) -> list[tuple[PR, int]]:
    """Every branch that waits on `end`, each followed by what waits on it in turn."""
    out: list[tuple[PR, int]] = []
    for pr in flat(prs):
        held = pipe(pr)
        if held and held.on_pr is end:
            out.append((pr, depth))
            out += waiting_on(prs, pr, depth + 1)
    return out


def board(prs: list[PR]) -> list[Line]:
    """The page, top to bottom: what you can fix, who has nothing to do, who is being waited
    on (longest wait first), and last the branches in progress: an agent is at work on them."""
    lines: list[Line] = []

    def group(name: str, blue: bool, ends: list[PR]) -> None:
        for i, end in enumerate(ends):
            lines.append(Line(name, blue, end, first=i == 0))
            lines.extend(Line(name, blue, pr, depth=depth) for pr, depth in waiting_on(prs, end))

    found = blockers(prs)
    group("waits on you", True, [b.pr for b in found if b.kind == "needs you"])
    group("waits on an agent", True, [b.pr for b in found if b.kind == "no agent"])
    for i, name in enumerate(free_people(prs)):
        lines.append(Line("nothing to do", True, person=name, first=i == 0))
    people: dict[str, list[PR]] = {}
    for b in found:
        if b.kind == "person":
            people.setdefault(b.who, []).append(b.pr)
    for who, ends in sorted(people.items(), key=lambda item: -max(age_hours(idle_for(pr)) for pr in item[1])):
        group(f"waits on {who}", False, sorted(ends, key=lambda pr: -age_hours(idle_for(pr))))
    group("in progress", False, [pr for pr in flat(prs) if (held := pipe(pr)) and held.state == "working"])
    return lines


def ribbon(pr: PR, held: Pipe, blue: bool, cells: int) -> str:
    """The branch's last days, one letter per cell: w an agent worked, b an agent could have
    been working, i it waited on someone else, e somebody did something on GitHub. The right
    end is real (it follows the data); what came before is made up, the same every run."""
    rng = random.Random(pr.number * 31 + len(pr.slug))
    kinds = [" "] * cells
    if held.state == "working":
        span = rng.randint(1, 4)
        kinds[cells - span:] = "w" * span
    else:
        span = max(1, min(cells, math.ceil(age_hours(idle_for(pr)) / HOURS_PER_CELL)))
        kinds[cells - span:] = ("b" if blue else "i") * span
    at = cells - span
    if pr.mine:                                   # your agents' time is known: work, then waits
        born = max(0, at - rng.randint(8, 30))
        worked = held.state != "working"
        while at > born:
            start = max(born, at - (rng.randint(1, 4) if worked else rng.randint(2, 7)))
            kinds[start:at] = ("w" if worked else "i") * (at - start)
            at, worked = start, not worked
    else:                                         # someone else's branch: only GitHub is visible
        at -= rng.randint(1, 3)
        stop = at - 22
        while at >= 0 and at > stop:
            kinds[at] = "e"
            at -= rng.randint(3, 9)
    return "".join(kinds)


RIBBON = {"w": ("━", INK2), "b": ("━", ACCENT), "i": ("─", QUIET), "e": ("•", MUTED), " ": (" ", "")}


class StudioV2(App):
    CSS = f"""
    Screen {{ background: {PLANE}; color: {INK}; }}
    #tabs {{ height: 1; margin: 1 2 0 2; }}
    #key {{ height: 1; margin: 1 2 0 2; }}
    #board {{ height: 1fr; margin: 1 2 0 2; }}
    #detail {{ height: 10; margin: 0 1; }}
    .tile {{ background: {SURFACE}; padding: 1 2; margin: 0 1; height: 10; }}
    #facts {{ width: 1fr; }}
    #wait {{ width: 74; }}
    #legend {{ height: 1; margin: 1 2 0 2; }}
    #hints {{ height: 1; margin: 0 2; color: {MUTED}; }}
    #tabs, #key, #board, #legend, .tile {{ text-wrap: nowrap; text-overflow: ellipsis; }}
    """
    BINDINGS = [
        Binding("up", "move(-1)", show=False), Binding("down", "move(1)", show=False),
        Binding("tab", "repo(1)", show=False, priority=True),
        Binding("shift+tab", "repo(-1)", show=False, priority=True),
        Binding("q", "quit", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.repo = next(iter(REPOS))
        self.sel = 0

    def compose(self) -> ComposeResult:
        yield Static(id="tabs")
        yield Static(id="key")
        yield Static(id="board")
        with Horizontal(id="detail"):
            yield Static(id="facts", classes="tile")
            yield Static(id="wait", classes="tile")
        yield Static(id="legend")
        yield Static("↑↓ branch or person    ⇥ next repo    q quit", id="hints")

    def on_mount(self) -> None:
        self.draw()

    def on_resize(self) -> None:
        self.call_after_refresh(self.draw)

    # ---- drawing -----------------------------------------------------------
    def draw(self) -> None:
        prs = REPOS[self.repo]
        lines = board(prs)
        self.sel = max(0, min(len(lines) - 1, self.sel))
        self.query_one("#tabs", Static).update(self.tabs())
        self.query_one("#key", Static).update(self.key())
        self.query_one("#board", Static).update(self.page(prs, lines))
        facts, wait = self.query_one("#facts", Static), self.query_one("#wait", Static)
        if not lines:
            facts.update(Text("nothing is open here, so nothing waits", style=MUTED))
            wait.update("")
        elif lines[self.sel].pr is None:
            facts.update(self.person_facts(prs, lines[self.sel].person))
            wait.update(self.person_wait(prs, lines[self.sel].person))
        else:
            facts.update(self.facts(lines[self.sel].pr))
            wait.update(self.wait(prs, lines[self.sel]))
        self.query_one("#legend", Static).update(self.legend())

    def tabs(self) -> Text:
        """Repos across the top. Beside each: how many things you can fix (blue) and how many
        people a branch waits on (grey)."""
        text = Text()
        for name, prs in REPOS.items():
            yours, nudge, free = repo_status(prs)
            text.append(name, style=f"bold underline {INK}" if name == self.repo else MUTED)
            text.append(f" {yours + free}" if yours + free else " ·", style=ACCENT if yours + free else AXIS)
            text.append(f" {nudge}" if nudge else " ·", style=MUTED if nudge else AXIS)
            text.append("     ")
        text.append("idle and yours to fix", style=ACCENT).append(" · ", style=AXIS)
        text.append("people being waited on", style=MUTED)
        return text

    def key(self) -> Text:
        text = Text()
        for mark, color, meaning in (("━━", INK2, "an agent at work"),
                                     ("━━", ACCENT, "an agent could be working: yours to fix"),
                                     ("──", QUIET, "waiting on someone else"),
                                     ("•", MUTED, "something happened on GitHub")):
            text.append(mark, style=color)
            text.append(f" {meaning}     ", style=MUTED)
        return text

    def page(self, prs: list[PR], lines: list[Line]) -> Text:
        width = max(60, self.size.width - 4)
        cells = 36 if width >= 140 else 24 if width >= 110 else 12
        name_w = 38 if width >= 140 else 32
        x_name = GUTTER
        x_agent = x_name + name_w
        x_move = x_agent + 6
        x_idle = width - 5
        x_ribbon = x_idle - 1 - cells
        move_w = max(8, x_ribbon - x_move - 2)

        ys: list[int] = []                         # one blank row between groups
        y = 0
        for i, line in enumerate(lines):
            y += 1 if line.first and i else 0
            ys.append(y)
            y += 1
        body = Canvas(width, max(1, y))
        if not lines:
            body.put(x_name, 0, "no open pull requests or local branches", MUTED)

        for i, (line, y) in enumerate(zip(lines, ys)):
            if line.first:
                if line.blue:
                    body.put(0, y, "●", ACCENT)
                body.put(2, y, clip(line.group, GUTTER - 3), f"bold {INK}" if line.blue else MUTED)
            if line.pr is None:                    # a person with nothing to do
                body.put(x_name, y, line.person, f"bold {INK}")
                body.put(x_move, y, clip("nothing waits on them", move_w), INK)
                trail, seen = ribbon_of_person(prs, line.person, cells)
                for k, kind in enumerate(trail):
                    body.put(x_ribbon + k, y, *RIBBON[kind])
                body.put(x_idle, y, seen.rjust(4), f"bold {INK}")
            else:
                pr, held = line.pr, pipe(line.pr)
                x = x_name
                if line.depth:
                    body.put(x, y, "  " * (line.depth - 1) + "↳", MUTED)
                    x += line.depth * 2
                icon, color, _, _ = STATUS[pr.state]
                body.put(x, y, icon, color)
                room = x_agent - x - 3
                body.put(x + 2, y, clip(pr.key, room), f"bold {INK}" if line.blue else INK)
                body.put(x + 2 + len(pr.key), y, clip("-" + pr.slug, room - len(pr.key)),
                         INK2 if line.blue else MUTED)
                assigned = agent(pr)
                if assigned:
                    state = session_state(assigned)
                    body.put(x_agent, y, "▶" if state == "working" else "▷",
                             ACCENT if state == "at its prompt" else (INK if state == "working" else MUTED))
                    body.put(x_agent + 2, y, assigned.serial, INK if state != "waiting" else MUTED)
                elif held.state == "no agent":
                    body.put(x_agent, y, "none", INK)
                body.put(x_move, y, clip(self.move_words(line, held), move_w),
                         INK if line.blue and not line.depth else INK2)
                for k, kind in enumerate(ribbon(pr, held, line.blue, cells)):
                    body.put(x_ribbon + k, y, *RIBBON[kind])
                if held.state != "working":
                    idle = idle_for(pr)
                    body.put(x_idle, y, idle.rjust(4),
                             f"bold {INK}" if line.blue or is_stale(idle) else INK2)
            if i == self.sel:
                body.paint(x_name - 1, y, width - x_name + 1, RAISED)

        # the head of the grid, then as much of the body as fits, keeping the selection in view
        head = Canvas(width, 2)
        for label, x in (("waits on", 2), ("branch", x_name), ("agent", x_agent),
                         ("next move", x_move), ("idle", x_idle)):
            head.put(x, 0, label, MUTED)
        for k in range(0, cells, 12):
            head.put(x_ribbon + k, 0, f"{(cells - k) // 12}d ago", AXIS)
        head.put(x_ribbon + cells - 3, 0, "now", MUTED)
        room = max(1, (self.query_one("#board", Static).size.height or 99) - 2)
        top = 0 if not lines else max(0, min(ys[self.sel] - room + 2, body.height - room))
        view = Canvas(width, min(room, body.height - top))
        view.cells = body.cells[top:top + view.height]
        return head.text().append("\n").append_text(view.text())

    def move_words(self, line: Line, held: Pipe) -> str:
        done = f"{held.done} of {held.total} done" if held.total and (held.partly or held.state == "working") else ""
        if line.depth:                             # hangs under what it waits on
            who = "" if held.who == ["you"] else ", ".join(held.who) + " · "
            return who + held.what
        if held.state == "working":
            return held.what + (f" · {done}" if done else "")
        said = held.progress                       # "stalled, 1 of 2 threads" | "not started"
        if held.unit and held.unit in held.what:   # the move already names what is counted
            said = said.replace(f" {held.unit}", "")
        return f"{held.what} · {said}"

    # ---- the two tiles under the board ----------------------------------------
    def facts(self, pr: PR) -> Text:
        icon, color, label, _ = STATUS[pr.state]
        text = Text()
        text.append(f"{self.repo}-", style=MUTED)
        text.append(pr.name + "\n", style=f"bold {INK}")
        text.append(icon, style=color)
        text.append(f" {label}", style=INK2)
        if pr.ci == "fail":
            text.append("   ✗", style=CRIT).append(" a check is failing", style=INK2)
        text.append(f"   {pr.title}\n", style=MUTED)

        def row(key: str, value: Text) -> None:
            text.append(f"{key:<9}", style=MUTED)
            text.append_text(value).append("\n")

        off = [w for w in pr.workers if w.name]
        if off:
            row("names", Text("≠ ", style=CRIT).append(f"the session is named {off[0].name}", style=INK))
            row("", Text(f"  it should be {self.repo}-{pr.key}-{off[0].serial}", style=INK2))
        else:
            parts = ["branch"] + (["session"] if pr.mine and pr.workers else []) \
                + (["PR"] if pr.is_pr else []) + ["Jira"]
            row("names", Text("✓ ", style=GOOD).append(", ".join(parts) + " agree", style=INK)
                .append("" if pr.is_pr else f" · issue #{pr.number} holds the number", style=INK2))
        row("anchor", Text(anchor_note(pr), style=INK)
            .append(f" · sits on {pr.parent.key}" if pr.parent else "", style=INK2))
        if pr.same_ticket:
            row("also", Text(f"{pr.ticket.upper()} is on {pr.same_ticket} too", style=INK))
        said = Text()
        for who, event in verdicts(pr).items():
            mark, mark_color, words = WORD[event.verdict]
            said.append(mark, style=mark_color)
            said.append(f" {'you' if who == ME else who} {words} {event.age}   ", style=INK)
        if pr.threads[0]:
            said.append(f"{pr.threads[0]} of {pr.threads[1]} threads open", style=INK2)
        row("review", said if said.plain else Text("nobody has been asked", style=INK2))
        mark, mark_color = {"pass": ("✓ ", GOOD), "fail": ("✗ ", CRIT)}.get(pr.ci, ("· ", MUTED))
        row("checks", Text(mark, style=mark_color)
            .append(" · ".join(x for x in (pr.ci_note or "none yet", pr.merge) if x), style=INK))
        mine = sessions(pr)
        if mine:
            worker = agent(pr) or mine[-1]
            state = session_state(worker)
            row("session", Text("▶ " if state == "working" else "▷ ", style=INK if state == "working" else MUTED)
                .append(f"{worker.serial} · {worker.note}", style=INK if state != "ended" else INK2))
        else:
            row("session", Text("none of yours" + ("" if pr.mine else f" · {pr.author}'s branch, on GitHub"),
                                style=INK2))
        return text

    def wait(self, prs: list[PR], line: Line) -> Text:
        """Why the selected branch is not moving, link by link, and what gets it going."""
        links = chain(line.pr)
        end, end_pipe = links[-1], pipe(links[-1])
        moving = end_pipe.state == "working"
        text = Text("it is moving\n" if moving else "why it waits\n", style=MUTED)
        for i, pr in enumerate(links):
            held = pipe(pr)
            text.append("  " * i + ("↳ " if i else ""), style=MUTED)
            text.append(pr.key, style=f"bold {INK}" if pr is line.pr else INK)
            words = {"working": f"  an agent is at work: {held.what}",
                     "needs you": f"  session at its prompt: {held.what}",
                     "no agent": f"  no agent assigned: {held.what}",
                     }.get(held.state, f"  waits on {held.on}" + ("" if held.on_pr else f": {held.what}"))
            text.append(words + "\n", style=INK2)
        behind = [pr for pr, _ in waiting_on(prs, end) if pr not in links]
        if behind:
            text.append("also behind it  ", style=MUTED)
            text.append(clip(", ".join(pr.key for pr in behind), 46) + "\n", style=INK2)
        text.append("gets going when  ", style=MUTED)
        if moving:
            text.append("nothing is needed, the agent is on it\n", style=INK2)
        elif end_pipe.state == "needs you":
            text.append("● ", style=ACCENT).append(f"you answer session {end_pipe.agent.serial} at its prompt\n", style=INK)
        elif end_pipe.state == "no agent":
            text.append("● ", style=ACCENT).append(f"you put an agent on {end.key}\n", style=INK)
        else:
            text.append("○ ", style=MUTED).append(f"{end_pipe.on} gets to it", style=INK)
            text.append(f"  ({end_pipe.progress})\n", style=INK2)
            room = 7 - text.plain.count("\n")          # a person's work as a list, never a count
            names = [n.strip() for n in end_pipe.on.split(",")]
            work = [(name, pr) for name in names for pr in work_of(prs, name)]
            work.sort(key=lambda item: item[1] is not end)
            for i, (name, pr) in enumerate(work[:max(1, room)]):
                text.append(f"{name + ' has' if i == 0 or work[i - 1][0] != name else '':<17}", style=MUTED)
                text.append(clip(f"{pr.key}  {pipe(pr).what}", 44) + "\n", style=INK2)
        return text

    def person_facts(self, prs: list[PR], name: str) -> Text:
        text = Text()
        text.append(name + "\n", style=f"bold {INK}")
        text.append("●", style=ACCENT).append(" has nothing to do on this repo\n\n", style=INK2)
        text.append("No branch waits on them. What they did last:\n", style=MUTED)
        seen = [(pr, event) for pr in flat(prs) for who, event in verdicts(pr).items() if who == name]
        for pr, event in sorted(seen, key=lambda item: age_hours(item[1].age))[:4]:
            mark, mark_color, words = WORD[event.verdict]
            text.append(f"  {mark} ", style=mark_color)
            text.append(f"{words} {pr.name}", style=INK).append(f"  {event.age} ago\n", style=MUTED)
        return text

    def person_wait(self, prs: list[PR], name: str) -> Text:
        text = Text("gets going when\n", style=MUTED)
        text.append("● ", style=ACCENT).append(f"you give {name} something: a review, or a branch\n\n", style=INK)
        unreviewed = [pr for pr in flat(prs) if pr.is_pr and not pr.history and pr.author != name]
        text.append("open with nobody asked to review\n", style=MUTED)
        for pr in unreviewed[:4]:
            icon, color, label, _ = STATUS[pr.state]
            text.append(f"  {icon} ", style=color)
            text.append(clip(pr.name, 40), style=INK).append(f"  {label}\n", style=MUTED)
        if not unreviewed:
            text.append("  none: every open pull request has a reviewer\n", style=INK2)
        return text

    def legend(self) -> Text:
        text = Text()
        short = {"ready": "ready", "rereview": "re-review", "changes": "changes asked",
                 "local": "local", "findings": ""}
        for state, (icon, color, label, _) in STATUS.items():
            if short.get(state, label):
                text.append(icon, style=color)
                text.append(f" {short.get(state, label)}  ", style=MUTED)
        text.append("  ▶", style=INK).append(" session at work  ", style=MUTED)
        text.append("▷", style=ACCENT).append(" at its prompt  ", style=MUTED)
        text.append("▷", style=MUTED).append(" waiting", style=MUTED)
        return text

    # ---- keys --------------------------------------------------------------
    def action_move(self, step: int) -> None:
        self.sel += step
        self.draw()

    def action_repo(self, step: int) -> None:
        names = list(REPOS)
        self.repo = names[(names.index(self.repo) + step) % len(names)]
        self.sel = 0
        self.draw()


def ribbon_of_person(prs: list[PR], name: str, cells: int) -> tuple[str, str]:
    """A person's trail is only what GitHub shows: a dot for each thing they did, then blue
    from the last of them to now. Also returns how long ago that last thing was."""
    kinds = [" "] * cells
    ages = [event.age for pr in flat(prs) for who, event in verdicts(pr).items() if who == name]
    for age in ages:
        at = cells - 1 - int(age_hours(age) // HOURS_PER_CELL)
        if 0 <= at < cells:
            kinds[at] = "e"
    last = max((k for k, kind in enumerate(kinds) if kind == "e"), default=-1)
    for k in range(last + 1, cells):
        kinds[k] = "b"
    return "".join(kinds), min(ages, key=age_hours, default="")


if __name__ == "__main__":
    StudioV2().run()
