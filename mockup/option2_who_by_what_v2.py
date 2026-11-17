"""Agent Frolic mock-up, option 2, v2: who × what, reworked around "is everything moving?".

The stack tree still runs down the left and people are still the columns, but the lit cell
no longer means "holds the next move". It now means "this branch waits on that person":
blue when the wait is yours to end (a session sits at its prompt, or no agent is assigned),
grey when it waits on someone else, and a faint ↑ where a branch is only queued behind
another branch, placed in the column where that chain ends. A branch with an agent at work
has no lit cell at all: a quiet row is a moving row.

Nobody is tallied. A column header says only "has work" or "idle" (nothing to do), and the
person card lists what waits on them instead of counting it.

    .venv/bin/python mockup/option2_who_by_what_v2.py

Keys: up/down pick a branch, left/right pick a person, enter greys out the rows that person
is not on, tab / shift+tab switch repo, q quits.
"""
from __future__ import annotations

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Static

from frolic_v2 import (ACCENT, AXIS, CRIT, GOOD, INK, INK2, MUTED, PLANE, PR, RAISED, REPOS,
                       STATUS, SURFACE, WARN, WORD, Canvas, Pipe, Row, agent, anchor_note, blockers,
                       chain, flat, free_people, is_stale, people, pipe, repo_status, root_cause,
                       roster, session_state, shown, tree_rows, verdicts, work_of)

NAME_W, CELL_W, CHIP_W, AGENT_W, WAIT_W = 44, 9, 6, 13, 31
DIM = "#5c5b56"                  # rows greyed out by the person focus
WHITE = "#ffffff"                # the label inside a blue fill


def short(pr: PR) -> str:
    return f"pr-{pr.number:04d}"


def clip(text: str, width: int) -> str:
    return text if len(text) <= width else text[:max(0, width - 1)] + "…"


def role_marks(pr: PR, person: str) -> list[tuple[str, str]]:
    """What `person` is to this branch, as (mark, color) pairs."""
    out: list[tuple[str, str]] = []
    if shown(pr.author) == person:
        out.append(("●", INK2))
    for who, event in verdicts(pr).items():
        if shown(who) == person:
            icon, color, _ = WORD[event.verdict]
            out.append((icon, color))
    return out


def role_words(pr: PR, person: str) -> str:
    if shown(pr.author) == person:
        return "local review" if pr.state == "findings" else "wrote it"
    for who, event in verdicts(pr).items():
        if shown(who) == person:
            return WORD[event.verdict][2]
    return ""


def waits_on(pr: PR) -> dict[str, str]:
    """Whose column lights up for this branch, and how: "blue" (it waits on you and you can
    end the wait), "grey" (it waits on that person), "behind" (it is queued behind another
    branch, and this is where that chain ends)."""
    here = pipe(pr)
    if here is None or here.state == "working":
        return {}
    if here.state in ("needs you", "no agent"):
        return {"you": "blue"}
    if here.on_pr is None:
        return {name: "grey" for name in here.who}
    end, _ = root_cause(pr)
    if end is None:
        return {}
    if end.state in ("working", "needs you", "no agent"):
        return {"you": "behind"}
    return {name: "behind" for name in end.who}


def count_of(here: Pipe) -> str:
    """ " 1/3 threads" when GitHub can count the move and some of it is done or under way."""
    return (f" {here.done}/{here.total} {here.unit}"
            if here.total and (here.partly or here.state == "working") else "")


def chain_end(pr: PR) -> str:
    """Where a wait on another branch ends, in a word."""
    end, _ = root_cause(pr)
    if end is None:
        return "nothing"
    return {"working": "working", "needs you": "you", "no agent": "an agent"}.get(end.state, end.on)


def check(pr: PR) -> tuple[str, str, str]:
    if pr.state == "conflict":
        return "⚠", CRIT, "conflict"
    return {"fail": ("✗", CRIT, "a check fails"), "run": ("…", WARN, "checks running"),
            "pass": ("✓", GOOD, ""), "none": ("·", AXIS, "")}[pr.ci]


class WhoByWhatV2(App):
    CSS = f"""
    Screen {{ background: {PLANE}; color: {INK}; }}
    #tabs {{ height: 1; margin: 1 2 0 2; }}
    #strip {{ height: 1; margin: 0 2; }}
    #legend {{ height: 2; margin: 1 2 0 2; }}
    #matrix {{ height: 1fr; margin: 1 2 0 2; }}
    #cards {{ height: 13; margin: 0 1; }}
    .card {{ background: {SURFACE}; padding: 1 2; margin: 0 1 1 1; width: 1fr; height: 1fr; }}
    #hints {{ height: 1; margin: 0 2; color: {MUTED}; }}
    #tabs, #strip, #legend, #matrix, .card, #hints {{ text-wrap: nowrap; text-overflow: ellipsis; }}
    """
    BINDINGS = [
        Binding("up", "move(-1, 0)", show=False), Binding("down", "move(1, 0)", show=False),
        Binding("left", "move(0, -1)", show=False), Binding("right", "move(0, 1)", show=False),
        Binding("enter", "toggle_focus", show=False),
        Binding("tab", "repo(1)", show=False, priority=True),
        Binding("shift+tab", "repo(-1)", show=False, priority=True),
        Binding("q", "quit", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.repo = next(iter(REPOS))
        self.row = self.col = 0          # the row counts branches only: anchors are not stops
        self.top = 0                     # first map row on screen, when the map is taller
        self.focused_person = False

    def compose(self) -> ComposeResult:
        yield Static(id="tabs")
        yield Static(id="strip")
        yield Static(id="legend")
        yield Static(id="matrix")
        with Horizontal(id="cards"):
            yield Static(id="branch", classes="card")
            yield Static(id="person", classes="card")
        yield Static("↑↓ branch    ←→ person    ↵ only this person's rows    ⇥ next repo    q quit",
                     id="hints")

    def on_mount(self) -> None:
        self.draw()
        self.call_after_refresh(self.draw)       # again once the map knows its height

    def on_resize(self) -> None:
        self.draw()

    # ---- drawing -----------------------------------------------------------
    def draw(self) -> None:
        prs = REPOS[self.repo]
        rows, names = tree_rows(prs), people(prs)
        stops = [i for i, row in enumerate(rows) if row.kind == "branch"]
        self.row = max(0, min(len(stops) - 1, self.row))
        self.col = max(0, min(len(names) - 1, self.col))
        selected = rows[stops[self.row]].pr if stops else None
        self.query_one("#tabs", Static).update(self.tabs())
        self.query_one("#strip", Static).update(self.strip(prs))
        self.query_one("#legend", Static).update(self.legend())
        self.query_one("#matrix", Static).update(self.matrix(rows, stops, names))
        self.query_one("#branch", Static).update(
            self.branch_card(selected) if selected is not None
            else Text("no open pull requests or local branches", style=MUTED))
        self.query_one("#person", Static).update(self.person_card(names[self.col], prs))

    def tabs(self) -> Text:
        """Each repo with how much of it is not working: what you can fix, who is waited on."""
        text = Text()
        for name, prs in REPOS.items():
            fix, waited, _ = repo_status(prs)
            current = name == self.repo
            text.append(name, style=f"bold underline {INK}" if current else MUTED)
            if fix:
                text.append("  ●", style=ACCENT)
                text.append(str(fix), style=INK if current else INK2)
            if waited:
                text.append("  ○", style=MUTED)
                text.append(str(waited), style=MUTED)
            if not fix and not waited:
                text.append("  ·", style=AXIS)
            text.append("       ")
        return text

    def strip(self, prs: list[PR]) -> Text:
        """What needs effort to get this repo going, in one line. Names, never tallies."""
        found = blockers(prs)
        text = Text("to get it moving    ", style=MUTED)
        yours = [b for b in found if b.yours]
        if yours:
            text.append("● ", style=ACCENT)
            prompt = [short(b.pr) for b in yours if b.kind == "needs you"]
            unmanned = [short(b.pr) for b in yours if b.kind == "no agent"]
            text.append(" · ".join(x for x in (
                "at its prompt: " + ", ".join(prompt) if prompt else "",
                "no agent: " + ", ".join(unmanned) if unmanned else "") if x), style=f"bold {INK}")
            text.append("      ")
        nudge: list[str] = []
        for b in found:
            for name in ([] if b.yours else [n.strip() for n in b.who.split(",")]):
                if name not in nudge:
                    nudge.append(name)
        if nudge:
            text.append("○ ", style=MUTED)
            text.append("waiting on " + ", ".join(nudge), style=INK2)
            text.append("      ")
        free = free_people(prs)
        if free:
            text.append("● ", style=ACCENT)
            text.append(" and ".join(free) + (" has" if len(free) == 1 else " have") + " nothing to do",
                        style=f"bold {INK}")
        if not (found or free):
            text.append("nothing is waiting" if flat(prs) else "nothing is open", style=INK2)
        return text

    def legend(self) -> Text:
        text = Text()
        for mark, color, meaning in (("●", INK2, "wrote it"), ("✓", GOOD, "approved"),
                                     ("✗", WORD["changes"][1], "asked for changes"),
                                     ("◔", WARN, "review asked"), ("↻", WARN, "re-review asked"),
                                     ("▶", INK, "agent working"), ("▷", MUTED, "agent idle"),
                                     ("↑", MUTED, "waits on a branch")):
            text.append(mark, style=color)
            text.append(f" {meaning}   ", style=MUTED)
        text.append("\n")
        text.append("  ●   ", style=f"bold {WHITE} on {ACCENT}")
        text.append(" waits on you: yours to end   ", style=MUTED)
        text.append("  ◔   ", style=f"{INK} on {AXIS}")
        text.append(" waits on that person   ", style=MUTED)
        text.append("  ↑   ", style=f"{INK2} on {RAISED}")
        text.append(" queued behind a branch, ending there   ", style=MUTED)
        text.append("quiet row: an agent is at work", style=MUTED)
        return text

    def matrix(self, rows: list[Row], stops: list[int], names: list[str]) -> Text:
        width = max(40, self.size.width - 4)
        room = max(1, (self.query_one("#matrix", Static).size.height or len(rows) + 2) - 2)
        at = stops[self.row] if stops else 0
        if self.row == 0 or at < self.top:
            self.top = 0 if self.row == 0 else at
        if at >= self.top + room:
            self.top = at - room + 1
        self.top = max(0, min(self.top, max(0, len(rows) - room)))
        visible = rows[self.top:self.top + room]
        canvas = Canvas(width, max(3, len(visible) + 2))

        person = names[self.col]
        x_agent = NAME_W + len(names) * CELL_W
        x_wait = x_agent + AGENT_W
        x_quiet = x_wait + WAIT_W

        # header: a name, then only whether that person has any work at all
        has_work = dict(roster(REPOS[self.repo]))
        canvas.put(0, 0, "branch", MUTED)
        for j, name in enumerate(names):
            x = NAME_W + j * CELL_W
            style = f"bold {INK}" if name == "you" else INK2
            canvas.put(x, 0, clip(name, CELL_W - 1), style + (" underline" if j == self.col else ""))
            if has_work.get(name):
                canvas.put(x, 1, "has work", MUTED)
            elif name == "you":
                canvas.put(x, 1, "no work", MUTED)
            else:
                canvas.put(x, 1, "●", ACCENT)
                canvas.put(x + 2, 1, "idle", f"bold {INK}")
        canvas.put(x_agent, 0, "agent", MUTED)
        canvas.put(x_wait, 0, "waits on", MUTED)
        canvas.put(x_quiet - 1, 0, "quiet", MUTED)
        if not rows:
            canvas.put(0, 2, "no open pull requests or local branches", MUTED)

        for i, row in enumerate(visible):
            y = i + 2
            if row.kind == "anchor":
                canvas.put(0, y, row.label, f"bold {INK}")
                continue
            if row.kind == "point":
                canvas.put(0, y, row.lead, AXIS)
                canvas.put(len(row.lead), y, row.label, MUTED)
                continue
            pr = row.pr
            lit = waits_on(pr)
            dim = self.focused_person and not (role_marks(pr, person) or person in lit)
            icon, color, _, _ = STATUS[pr.state]
            canvas.put(0, y, row.lead, AXIS)
            x = len(row.lead)
            canvas.put(x, y, icon, DIM if dim else color)
            canvas.put(x + 2, y, pr.key, DIM if dim else f"bold {INK}")
            canvas.put(x + 2 + len(pr.key), y,
                       ("-" + pr.slug)[:max(0, NAME_W - x - 3 - len(pr.key))], DIM if dim else INK2)

            for j, name in enumerate(names):
                x0 = NAME_W + j * CELL_W
                kind = lit.get(name, "")
                found = list(role_marks(pr, name))
                if kind == "behind":
                    found.append(("↑", INK2))
                elif kind and not found:
                    found.append(("→", INK))
                start = x0 + (CHIP_W - len(found)) // 2
                for k, (mark, mark_color) in enumerate(found):
                    if dim:
                        mark_color = DIM
                    elif kind == "blue":
                        mark_color = f"bold {WHITE}"
                    elif kind in ("grey", "behind"):
                        mark_color = INK if kind == "grey" else INK2
                    canvas.put(start + k, y, mark, mark_color)
                if kind and not dim:
                    canvas.paint(x0, y, CHIP_W, {"blue": ACCENT, "grey": AXIS, "behind": RAISED}[kind])

            for k, (piece, style) in enumerate(self.agent_cell(pr)):
                canvas.put(x_agent + (0 if k == 0 else 2), y, piece, DIM if dim else style)
            x = x_wait
            for piece, style in self.wait_cell(pr):
                canvas.put(x, y, clip(piece, max(0, x_quiet - 1 - x)), DIM if dim else style)
                x += len(piece)
            canvas.put(x_quiet, y, pr.age.rjust(4),
                       DIM if dim else (f"bold {INK}" if is_stale(pr.age) else MUTED))
            if self.top + i == at:                          # the crosshair's row
                for xx in range(width):
                    if "on " not in canvas.cells[y][xx][1]:
                        canvas.paint(xx, y, 1, RAISED)
        return canvas.text()

    def agent_cell(self, pr: PR) -> list[tuple[str, str]]:
        """Your session on a branch: (mark, style), (words, style)."""
        here, on_it = pipe(pr), agent(pr)
        if on_it is not None:
            state = session_state(on_it)
            if state == "working":
                return [("▶", INK), (f"{on_it.serial} working", INK2)]
            if state == "at its prompt":
                return [("▷", ACCENT), (f"{on_it.serial} prompt", f"bold {INK}")]
            return [("▷", MUTED), (f"{on_it.serial} waiting", MUTED)]
        if here is not None and here.state == "no agent":
            return [("○", ACCENT), ("none", f"bold {INK}")]
        return [("·", AXIS)]

    def wait_cell(self, pr: PR) -> list[tuple[str, str]]:
        """What a branch waits on and how far the move has got; "in progress" only when an
        agent is at work on it."""
        here = pipe(pr)
        if here is None:
            return [("◇ ", MUTED), (f"findings for {short(pr)}", MUTED)]
        word = "stalled" if here.partly else "not started"
        if here.state == "working":
            return [("▶ ", INK), ("in progress" + count_of(here), INK)]
        if here.state in ("needs you", "no agent"):
            return [("● ", ACCENT), (f"{here.on} · {word}{count_of(here)}", f"bold {INK}")]
        if here.on_pr is not None:
            return [("↑ ", MUTED), (f"{short(here.on_pr)} → {chain_end(pr)}", INK2)]
        return [("○ ", MUTED), (f"{here.on} · {word}{count_of(here)}", INK2)]

    def pipe_phrase(self, pr: PR, here: Pipe | None) -> Text:
        """One link of a wait chain, in words."""
        if here is None:
            return Text("◇ ", style=MUTED).append(
                f"findings of your local review of {short(pr)}; nothing waits on it", style=INK2)
        word = "stalled" if here.partly else "not started"
        if here.state == "working":
            return Text("▶ ", style=INK).append(
                f"in progress{count_of(here)} · {here.what}", style=INK)
        if here.state == "needs you":
            return Text("● ", style=ACCENT).append(
                f"waits on you at its prompt · {here.what}", style=f"bold {INK}")
        if here.state == "no agent":
            return Text("● ", style=ACCENT).append(
                f"no agent assigned · {here.what} · {word}{count_of(here)}", style=f"bold {INK}")
        if here.on_pr is not None:
            return Text("↑ ", style=MUTED).append(f"waits on {short(here.on_pr)} · {here.what}", style=INK)
        return Text("○ ", style=MUTED).append(
            f"waits on {here.on} · {here.what} · {word}{count_of(here)}", style=INK)

    def branch_card(self, pr: PR) -> Text:
        icon, color, label, _ = STATUS[pr.state]
        text = Text()
        text.append(f"{self.repo}-", style=MUTED)
        text.append(pr.name + "\n", style=f"bold {INK}")
        text.append(icon, style=color)
        text.append(f" {label} · {pr.title}\n\n", style=INK2)

        def line(key: str, value: str | Text) -> None:
            text.append(f"{key:<9}", style=MUTED)
            text.append_text(value if isinstance(value, Text) else Text(value, style=INK))
            text.append("\n")

        # the wait, followed to its end: one line per branch in the chain
        for depth, link_pr in enumerate(chain(pr)):
            phrase = self.pipe_phrase(link_pr, pipe(link_pr))
            if depth:
                phrase = Text("└ ", style=AXIS).append(f"{short(link_pr)} ", style=INK2).append_text(phrase)
            line("pipeline" if depth == 0 else "", phrase)

        here, on_it = pipe(pr), agent(pr)
        gone = [w for w in pr.workers if pr.mine and session_state(w) == "ended"]
        if on_it is not None:
            state = session_state(on_it)
            tail = f"-{on_it.serial}" if on_it in pr.workers else f"-review-{on_it.serial}"
            if pr.state == "findings":
                tail = ""
            line("agent", Text({"working": "▶ ", "at its prompt": "▷ "}.get(state, "▷ "),
                               style={"working": INK, "at its prompt": ACCENT}.get(state, MUTED))
                 .append(f"{self.repo}-{pr.name if pr.state == 'findings' else pr.key}{tail}", style=INK)
                 .append(f" · {on_it.note}", style=INK2))
        elif here is not None and here.state == "no agent":
            line("agent", Text("○ ", style=ACCENT).append("none assigned", style=f"bold {INK}")
                 .append(f" · session {gone[-1].serial} {gone[-1].note}" if gone else "", style=INK2))
        elif not pr.mine:
            line("agent", Text(f"{pr.author}'s branch · their agents cannot be seen from here", style=INK2))
        else:
            line("agent", Text("none" + (f" · session {gone[-1].serial} {gone[-1].note}" if gone else ""),
                               style=INK2))

        off = [w for w in pr.workers if w.name]
        if off:
            line("names", Text("≠ ", style=CRIT).append(
                f"session is named {off[0].name}, expected {self.repo}-{pr.key}-{off[0].serial}", style=INK))
        else:
            parts = ["branch"] + (["session"] if pr.mine and pr.workers else []) + \
                    (["PR"] if pr.is_pr else []) + ["Jira"]
            agree = Text()
            for part in parts:
                agree.append("✓ ", style=GOOD).append(part + "  ", style=INK)
            if not pr.is_pr and pr.state != "findings":
                agree.append(f"issue #{pr.number} holds the number", style=INK2)
            line("names", agree)
        line("anchor", anchor_note(pr) + (f" · on {pr.parent.key}" if pr.parent else ""))
        mark, mark_color, words = check(pr)
        line("checks", Text(mark + " ", style=mark_color)
             .append(" · ".join(x for x in (pr.ci_note or words, pr.merge) if x) or "none yet", style=INK))
        return text

    def person_card(self, person: str, prs: list[PR]) -> Text:
        """What waits on a person, as a list to read, never a number to compare."""
        everything = flat(prs)
        text = Text()
        text.append(person, style=f"bold {INK}")
        has_work = dict(roster(prs)).get(person, False)
        if person != "you" and not has_work:
            text.append("   ●", style=ACCENT)
            text.append(" has nothing to do", style=f"bold {INK}")
        elif has_work:
            text.append("   has work", style=INK2)
        text.append("\n")

        def item(mark: str, color: str, pr: PR, words: str, bold: bool = False) -> None:
            text.append(f"  {mark} ", style=color)
            text.append(f"{clip(pr.name, 30):<30}", style=f"bold {INK}" if bold else INK)
            text.append(f"  {words}\n", style=INK2)

        room = 9
        found = blockers(prs)
        if person == "you":
            mine = [b for b in found if b.yours]
            if mine:
                text.append("waits on you\n", style=MUTED)
                room -= 1
            for b in mine[:4]:
                item("●", ACCENT, b.pr, f"{b.pipe.what} · "
                     + ("at its prompt" if b.kind == "needs you" else "no agent"), bold=True)
                room -= 1
            if len(mine) > 4:
                text.append("  and more\n", style=MUTED)
                room -= 1
            working = [pr for pr in everything if (p := pipe(pr)) and p.state == "working"]
            waiting = [pr for pr in everything if pr.mine and (p := pipe(pr)) and p.state == "waiting"
                       and agent(pr) is not None]
            if working and room >= 2:
                text.append("your agents at work\n", style=MUTED)
                text.append("  ▶ ", style=INK)
                text.append(" · ".join(short(pr) for pr in working) + "\n", style=INK)
                room -= 2
            if waiting and room >= 2:
                text.append("your agents waiting, each on its own branch\n", style=MUTED)
                text.append("  ▷ ", style=MUTED)
                text.append(" · ".join(f"{short(pr)} on {short(pipe(pr).on_pr) if pipe(pr).on_pr else pipe(pr).on}"
                                       for pr in waiting) + "\n", style=INK2)
            if not (mine or working or waiting):
                text.append("nothing waits on you here\n", style=INK2)
            return text

        theirs = [b for b in found if not b.yours and person in [n.strip() for n in b.who.split(",")]]
        if theirs:
            text.append(f"waits on {person}\n", style=MUTED)
            room -= 1
        for b in theirs:
            if room < 1:
                break
            word = "stalled" if b.pipe.partly else "not started"
            item("○", MUTED, b.pr, f"{b.pipe.what} · {word}{count_of(b.pipe)}")
            room -= 1
            if b.behind and room >= 1:
                text.append("      ↑ ", style=MUTED)
                text.append("queued behind it: " + ", ".join(short(pr) for pr in b.behind) + "\n", style=INK2)
                room -= 1
        direct = {id(b.pr) for b in theirs}
        later = [pr for pr in work_of(prs, person) if id(pr) not in direct]
        if later and room >= 2:
            text.append("theirs next, once another branch lands\n", style=MUTED)
            room -= 1
            for pr in later[:room]:
                item("↑", MUTED, pr, pr.next[1])
                room -= 1
        held = direct | {id(pr) for pr in later}
        also = [pr for pr in everything if role_marks(pr, person) and id(pr) not in held]
        if not theirs and not later:
            text.append(f"nothing in this repo waits on {person}\n", style=INK2)
            room -= 1
        if also and room >= 2:
            text.append("also on\n", style=MUTED)
            room -= 1
            for pr in also[:room]:
                icon, color, _, _ = STATUS[pr.state]
                item(icon, color, pr, role_words(pr, person))
        return text

    # ---- keys --------------------------------------------------------------
    def action_move(self, down: int, right: int) -> None:
        self.row += down
        self.col += right
        self.draw()

    def action_toggle_focus(self) -> None:
        self.focused_person = not self.focused_person
        self.draw()

    def action_repo(self, step: int) -> None:
        names = list(REPOS)
        self.repo = names[(names.index(self.repo) + step) % len(names)]
        self.row = self.col = self.top = 0
        self.draw()


if __name__ == "__main__":
    WhoByWhatV2().run()
