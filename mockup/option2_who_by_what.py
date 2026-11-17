"""Agent Frolic mock-up, option 2: who × what. The stack tree stays down the left, but the
columns are people. A cell says what that person is to that branch (wrote it, approved it,
asked for changes, was asked to review), and the lit cell is whoever holds the next move.
Read a column to see one person's load, a row to see who is on a branch.

    .venv/bin/python mockup/option2_who_by_what.py

Keys: up/down pick a branch, left/right pick a person, enter greys out the rows that person
is not on, tab goes to the next repo, q quits.
"""
from __future__ import annotations

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Static

from frolic_common import (ACCENT, AXIS, CRIT, GOOD, HAIR, INK, INK2, ME, MUTED, PLANE, PR,
                           RAISED, REPOS, STATUS, SURFACE, WARN, WORD, Canvas, age_hours, flat,
                           is_stale, movers, people, shown, verdicts)

NAME_W, CELL_W, CHIP_W, CHECK_W = 46, 9, 6, 16
DIM = "#5c5b56"                  # rows greyed out by the person focus


def tree_rows(prs: list[PR]):
    """(connector, branch) for every branch, parents first."""
    def visit(pr: PR, lead: str, last: bool, top: bool):
        yield ("" if top else lead + ("└ " if last else "├ ")), pr
        below = "" if top else lead + ("  " if last else "│ ")
        for i, child in enumerate(pr.children):
            yield from visit(child, below, i == len(pr.children) - 1, False)
    for pr in prs:
        yield from visit(pr, "", True, True)


def marks(pr: PR, person: str) -> list[tuple[str, str]]:
    """What `person` is to this branch, as (mark, color) pairs."""
    out: list[tuple[str, str]] = []
    if shown(pr.author) == person:
        out.append(("●", INK2))
        if pr.mine and pr.workers:
            live = any(w.live for w in pr.workers)
            out.append(("▶" if live else "▷", ACCENT if live else MUTED))
    for who, event in verdicts(pr).items():
        if shown(who) == person:
            icon, color, _ = WORD[event.verdict]
            out.append((icon, color))
    return out


def role(pr: PR, person: str) -> str:
    """The same thing in words, for the person card."""
    if shown(pr.author) == person:
        return "wrote it" if pr.state != "findings" else "local review"
    for who, event in verdicts(pr).items():
        if shown(who) == person:
            return WORD[event.verdict][2]
    return ""


def check(pr: PR) -> tuple[str, str, str]:
    if pr.state == "conflict":
        return "⚠", CRIT, "conflict"
    return {"fail": ("✗", CRIT, "check failing"), "run": ("…", WARN, "running"),
            "pass": ("✓", GOOD, ""), "none": ("·", AXIS, "")}[pr.ci]


def quiet_bar(age: str) -> int:
    hours = age_hours(age)
    return 1 + sum(hours > limit for limit in (1, 6, 24, 72, 168))


class WhoByWhat(App):
    CSS = f"""
    Screen {{ background: {PLANE}; color: {INK}; }}
    #tabs {{ height: 1; margin: 1 2 0 2; }}
    #legend {{ height: 2; margin: 1 2; text-wrap: nowrap; text-overflow: ellipsis; }}
    #matrix {{ height: 1fr; margin: 0 2; }}
    #cards {{ height: 14; margin: 0 1; }}
    .card {{ background: {SURFACE}; padding: 1 2; margin: 0 1 1 1; width: 1fr; height: 1fr;
             text-wrap: nowrap; text-overflow: ellipsis; }}
    #hints {{ height: 1; margin: 0 2; color: {MUTED}; }}
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
        self.row = self.col = 0
        self.focused_person = False

    def compose(self) -> ComposeResult:
        yield Static(id="tabs")
        yield Static(id="legend")
        yield Static(id="matrix")
        with Horizontal(id="cards"):
            yield Static(id="branch", classes="card")
            yield Static(id="person", classes="card")
        yield Static("↑↓ branch    ←→ person    ↵ only this person's rows    ⇥ next repo    q quit",
                     id="hints")

    def on_mount(self) -> None:
        self.draw()

    def on_resize(self) -> None:
        self.draw()

    # ---- drawing -----------------------------------------------------------
    def draw(self) -> None:
        prs = REPOS[self.repo]
        rows, names = list(tree_rows(prs)), people(prs)
        self.row = min(self.row, max(0, len(rows) - 1))
        self.col = min(self.col, len(names) - 1)
        self.query_one("#tabs", Static).update(self.tabs())
        self.query_one("#legend", Static).update(self.legend())
        self.query_one("#matrix", Static).update(self.matrix(rows, names))
        branch, person = self.query_one("#branch", Static), self.query_one("#person", Static)
        branch.update(self.branch_card(rows[self.row][1]) if rows
                      else Text("no open pull requests or local branches", style=MUTED))
        person.update(self.person_card(names[self.col], prs))

    def tabs(self) -> Text:
        text = Text()
        for name, prs in REPOS.items():
            yours = sum("you" in movers(pr) for pr in flat(prs))
            current = name == self.repo
            text.append(name, style=f"bold underline {INK}" if current else MUTED)
            text.append(f" {yours}" if yours else " ·", style=ACCENT if yours else AXIS)
            text.append("     ")
        return text

    def legend(self) -> Text:
        text = Text()
        for mark, color, meaning in (("●", INK2, "wrote it"), ("✓", GOOD, "approved"),
                                     ("✗", WORD["changes"][1], "asked for changes"),
                                     ("◔", WARN, "review asked"), ("↻", WARN, "re-review asked"),
                                     ("▶", ACCENT, "your session at work"),
                                     ("▷", MUTED, "your session idle")):
            text.append(mark, style=color)
            text.append(f" {meaning}   ", style=MUTED)
        text.append("\n")
        text.append(" ✓ ", style=f"bold #ffffff on {ACCENT}")
        text.append(" a lit cell holds the next move: blue when it is yours, ", style=MUTED)
        text.append(" ✓ ", style=f"{INK} on {AXIS}")
        text.append(" grey when it is theirs", style=MUTED)
        return text

    def matrix(self, rows: list[tuple[str, PR]], names: list[str]) -> Text:
        width = max(40, self.size.width - 4)
        canvas = Canvas(width, len(rows) + 3)
        person = names[self.col]
        x_checks = NAME_W + len(names) * CELL_W + 1
        x_quiet = x_checks + CHECK_W

        # header: names, then how many moves each one holds, as bars in one hue
        owed = {n: sum(n in movers(pr) for _, pr in rows) for n in names}
        most = max(owed.values(), default=0) or 1
        canvas.put(0, 0, f"{len(rows)} branches", MUTED)
        canvas.put(0, 1, "next moves held", MUTED)
        for j, name in enumerate(names):
            x = NAME_W + j * CELL_W
            style = f"bold {INK}" if name == "you" else INK2
            canvas.put(x, 0, name[:CELL_W - 1], style + (" underline" if j == self.col else ""))
            cells = -(-owed[name] * 4 // most) if owed[name] else 0
            canvas.put(x, 1, "━" * cells, ACCENT if name == "you" else MUTED)
            canvas.put(x + cells + (1 if cells else 0), 1, str(owed[name]) if owed[name] else "·",
                       INK2 if owed[name] else AXIS)
        canvas.put(x_checks, 0, "checks", MUTED)
        canvas.put(x_quiet, 0, "quiet for", MUTED)
        canvas.put(0, 2, "─" * min(width, x_quiet + 12), HAIR)

        for i, (lead, pr) in enumerate(rows):
            y = i + 3
            involved = bool(marks(pr, person)) or person in movers(pr)
            dim = self.focused_person and not involved
            icon, color, _, _ = STATUS[pr.state]
            canvas.put(0, y, lead, AXIS)
            x = len(lead)
            canvas.put(x, y, icon, DIM if dim else color)
            canvas.put(x + 2, y, pr.key, DIM if dim else f"bold {INK}")
            canvas.put(x + 2 + len(pr.key), y,
                       ("-" + pr.slug)[:NAME_W - x - 3 - len(pr.key)], DIM if dim else INK2)
            for j, name in enumerate(names):
                x0 = NAME_W + j * CELL_W
                found = marks(pr, name)
                lit = name in movers(pr)
                start = x0 + (CHIP_W - len(found)) // 2
                for k, (mark, mark_color) in enumerate(found):
                    if dim:
                        mark_color = DIM
                    elif lit:
                        mark_color = "bold #ffffff" if name == "you" else INK
                    canvas.put(start + k, y, mark, mark_color)
                if lit and not found:
                    canvas.put(x0 + CHIP_W // 2 - 1, y, "→", "bold #ffffff" if name == "you" else INK)
                if lit and not dim:
                    canvas.paint(x0, y, CHIP_W, ACCENT if name == "you" else AXIS)
            mark, mark_color, words = check(pr)
            canvas.put(x_checks, y, mark, DIM if dim else mark_color)
            canvas.put(x_checks + 2, y, words, DIM if dim else INK2)
            bar = quiet_bar(pr.age)
            canvas.put(x_quiet, y, "━" * bar, DIM if dim else (INK2 if is_stale(pr.age) else AXIS))
            canvas.put(x_quiet + 7, y, pr.age, DIM if dim else (f"bold {INK}" if is_stale(pr.age) else INK2))
            if i == self.row:                       # the crosshair's row
                for x in range(width):
                    if "on " not in canvas.cells[y][x][1]:
                        canvas.paint(x, y, 1, RAISED)
        return canvas.text()

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

        who, what = pr.next
        if who:
            line("next", Text("● " if who == "you" else "○ ", style=ACCENT if who == "you" else MUTED)
                 .append(f"{who} · {what}", style=f"bold {INK}" if who == "you" else INK))
        off = [w for w in pr.workers if w.name]
        if off:
            want = f"{self.repo}-{pr.key}-{off[0].serial}"
            line("names", Text("≠ ", style=CRIT).append(f"session is named {off[0].name}", style=INK))
            line("", Text(f"expected {want}", style=INK2))
        else:
            parts = ["branch"] + (["session"] if pr.mine and pr.workers else []) + \
                    (["PR"] if pr.is_pr else []) + ["Jira"]
            agree = Text()
            for part in parts:
                agree.append("✓ ", style=GOOD).append(part + "  ", style=INK)
            if not pr.is_pr:
                agree.append(f"issue #{pr.number} holds the number", style=INK2)
            line("names", agree)
        line("base", (pr.parent.name if pr.parent else "main") + f" · level {pr.depth}")
        mark, mark_color, words = check(pr)
        line("checks", Text(mark + " ", style=mark_color)
             .append(" · ".join(x for x in (pr.ci_note, pr.merge) if x), style=INK))
        open_threads, total = pr.threads
        if total:
            line("threads", f"{total} · {open_threads} open")
            for part in pr.thread_note.split(" · ") if pr.thread_note else []:
                line("", Text(part, style=INK2))
        if pr.same_ticket:
            line("also", f"{pr.ticket.upper()} is on {pr.same_ticket} too")
        line("quiet", pr.age + ("  stale" if is_stale(pr.age) else ""))
        return text

    def person_card(self, person: str, prs: list[PR]) -> Text:
        everything = flat(prs)
        holds = [pr for pr in everything if person in movers(pr)]
        on = [pr for pr in everything if marks(pr, person) and pr not in holds]
        wrote = sum(shown(pr.author) == person and pr.state != "findings" for pr in everything)
        text = Text()
        text.append(person, style=f"bold {INK}")
        if person == "you":
            live = sum(w.live for pr in everything for w in pr.workers)
            text.append(f"   {live} sessions at work on this Mac", style=INK2)
        text.append(f"\nholds {len(holds)} next moves · wrote {wrote} · "
                    f"on {len(holds) + len(on)} branches\n\n", style=INK2)

        def listing(title: str, items: list[PR], room: int, held: bool) -> None:
            if not items:
                return
            text.append(title + "\n", style=MUTED)
            for pr in items[:room]:
                icon, color, _, _ = STATUS[pr.state]
                text.append(f"  {icon} ", style=color)
                text.append(f"{pr.name:<38}"[:38], style=INK)
                text.append(f" {pr.next[1] if held else role(pr, person)}", style=INK2)
                if held:
                    text.append(f"  {pr.age}", style=MUTED)
                text.append("\n")
            if len(items) > room:
                text.append(f"  and {len(items) - room} more\n", style=MUTED)

        listing("holds the next move on", holds, 5, True)
        listing("also on", on, max(1, 6 - min(len(holds), 5)), False)
        return text

    # ---- keys --------------------------------------------------------------
    def action_move(self, down: int, right: int) -> None:
        prs = REPOS[self.repo]
        self.row = max(0, min(len(flat(prs)) - 1, self.row + down))
        self.col = max(0, min(len(people(prs)) - 1, self.col + right))
        self.draw()

    def action_toggle_focus(self) -> None:
        self.focused_person = not self.focused_person
        self.draw()

    def action_repo(self, step: int) -> None:
        names = list(REPOS)
        self.repo = names[(names.index(self.repo) + step) % len(names)]
        self.row = self.col = 0
        self.draw()


if __name__ == "__main__":
    WhoByWhat().run()
