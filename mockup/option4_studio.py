"""Agent Frolic mock-up, option 4: studio. What a designer might draw: one big number that
says how many moves are yours, a row of tiles for the state of the repo, and the stacks as
transit lines running left to right off main, each pull request a station. No borders: tiles
are blocks of a slightly lighter surface with gaps between them. Color sits on marks only.

    .venv/bin/python mockup/option4_studio.py

Keys: arrows travel along and between the lines, tab goes to the next repo, q quits.
"""
from __future__ import annotations

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Static

from frolic_common import (ACCENT, AXIS, CRIT, GOOD, GROUPS, INK, INK2, ME, MUTED, PLANE, PR,
                           RAISED, REPOS, STATUS, SURFACE, WORD, Canvas, broken, flat, is_stale,
                           movers, people)

LEVEL_W, X0, LANE_H, CAPTION_W = 18, 5, 5, 14
DIGITS = {                       # big numerals drawn with thin rounded lines, three rows each
    "0": ("╭──╮", "│  │", "╰──╯"), "1": (" ╶╮ ", "  │ ", "  ╵ "), "2": ("╶──╮", "╭──╯", "╰──╴"),
    "3": ("╶──╮", " ──┤", "╶──╯"), "4": ("╷  ╷", "╰──┤", "   ╵"), "5": ("╭──╴", "╰──╮", "╶──╯"),
    "6": ("╭──╴", "├──╮", "╰──╯"), "7": ("╶──╮", "   │", "   ╵"), "8": ("╭──╮", "├──┤", "╰──╯"),
    "9": ("╭──╮", "╰──┤", "╶──╯"),
}


def clip(text: str, width: int) -> str:
    return text if len(text) <= width else text[:width - 1] + "…"


class Studio(App):
    CSS = f"""
    Screen {{ background: {PLANE}; color: {INK}; }}
    #bar {{ height: 1; margin: 1 2 0 2; }}
    #tiles {{ height: 7; margin: 1 1 0 1; }}
    .tile {{ background: {SURFACE}; padding: 1 2; margin: 0 1; height: 7;
             text-wrap: nowrap; text-overflow: ellipsis; }}
    #hero {{ width: 44; }}
    #split {{ width: 1fr; }}
    #load {{ width: 42; }}
    #body {{ height: 1fr; margin: 1 1 0 1; }}
    #lines {{ width: 1fr; height: 1fr; margin: 0 1; }}
    #inspector {{ width: 48; height: 1fr; background: {SURFACE}; padding: 1 2; margin: 0 1;
                  text-wrap: nowrap; text-overflow: ellipsis; }}
    #legend {{ height: 2; margin: 1 2 0 2; text-wrap: nowrap; text-overflow: ellipsis; }}
    #hints {{ height: 1; margin: 0 2; color: {MUTED}; }}
    """
    BINDINGS = [
        Binding("up", "go('up')", show=False), Binding("down", "go('down')", show=False),
        Binding("left", "go('left')", show=False), Binding("right", "go('right')", show=False),
        Binding("tab", "repo(1)", show=False, priority=True),
        Binding("shift+tab", "repo(-1)", show=False, priority=True),
        Binding("q", "quit", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.repo = next(iter(REPOS))
        self.selected: PR | None = None
        self.pos: dict[int, tuple[int, int]] = {}

    def compose(self) -> ComposeResult:
        yield Static(id="bar")
        with Horizontal(id="tiles"):
            yield Static(id="hero", classes="tile")
            yield Static(id="split", classes="tile")
            yield Static(id="load", classes="tile")
        with Horizontal(id="body"):
            yield Static(id="lines")
            yield Static(id="inspector")
        yield Static(id="legend")
        yield Static("←→ along a line    ↑↓ between lines    ⇥ next repo    q quit", id="hints")

    def on_mount(self) -> None:
        self.draw()

    def on_resize(self) -> None:
        self.draw()

    # ---- drawing -----------------------------------------------------------
    def draw(self) -> None:
        prs = REPOS[self.repo]
        everything = flat(prs)
        if self.selected not in everything:
            self.selected = everything[0] if everything else None
        self.query_one("#bar", Static).update(self.bar())
        self.query_one("#hero", Static).update(self.hero(everything))
        self.query_one("#split", Static).update(self.split(everything))
        self.query_one("#load", Static).update(self.load(prs))
        self.query_one("#lines", Static).update(self.lines(prs))
        self.query_one("#inspector", Static).update(self.inspector())
        self.query_one("#legend", Static).update(self.legend())

    def bar(self) -> Text:
        text = Text()
        for name in REPOS:
            if name == self.repo:
                text.append(f" {name} ", style=f"bold {INK} on {RAISED}")
            else:
                text.append(f" {name} ", style=MUTED)
            text.append("  ")
        text.append("    refreshed 2m ago", style=AXIS)
        return text

    def hero(self, everything: list[PR]) -> Text:
        yours = sum("you" in movers(pr) for pr in everything)
        live = sum(w.live for pr in everything for w in pr.workers)
        beside = [Text("●", style=ACCENT).append(" moves are yours", style=f"bold {INK}"),
                  Text(f"  of {len(everything)} branches in {self.repo}", style=MUTED),
                  Text("▶", style=ACCENT if live else AXIS)
                  .append(f" {live} session{'' if live == 1 else 's'} at work on this Mac", style=MUTED)]
        text = Text()
        for row in range(3):
            text.append(" ".join(DIGITS[d][row] for d in str(yours)), style=f"bold {INK}")
            text.append("   ").append_text(beside[row]).append("\n")
        return text

    def split(self, everything: list[PR]) -> Text:
        """One whole split into at most five parts: a thin segmented bar with gaps, then a legend."""
        counted = [pr for pr in everything if pr.state != "findings"]
        text = Text(f"{len(counted)} branches by where they stand\n", style=MUTED)
        if not counted:
            return text.append("nothing open", style=AXIS)
        width = max(20, self.size.width - 44 - 42 - 14)
        totals = [(name, color, sum(STATUS[pr.state][3] == name for pr in counted))
                  for name, color in GROUPS]
        for name, color, n in totals:
            if n:
                text.append("━" * max(1, round((width - 4) * n / len(counted))), style=color)
                text.append(" ")
        text.append("\n\n")
        present = [item for item in totals if item[2]]
        for i, (name, color, n) in enumerate(present):
            text.append("━ ", style=color)
            text.append(f"{name} ", style=INK2)
            text.append(f"{n}", style=f"bold {INK}")
            text.append("\n" if i == 2 else "   ")
        return text

    def load(self, prs: list[PR]) -> Text:
        """Who holds the next move: ranked bars in one hue, yours in the accent."""
        held = {name: sum(name in movers(pr) for pr in flat(prs)) for name in people(prs)}
        ranked = sorted(held.items(), key=lambda item: -item[1])[:4]
        most = max((n for _, n in ranked), default=0) or 1
        text = Text("who holds the next move\n", style=MUTED)
        for name, n in ranked:
            text.append(f"{name:<8}", style=f"bold {INK}" if name == "you" else INK2)
            text.append("━" * round(22 * n / most), style=ACCENT if name == "you" else MUTED)
            text.append(f" {n}\n" if n else "·\n", style=INK if n else AXIS)
        return text

    def lines(self, prs: list[PR]) -> Text:
        width = max(40, self.size.width - 48 - 6)
        stacks = [pr for pr in prs if pr.children]
        singles = [pr for pr in prs if not pr.children]
        self.pos = {}

        def place(pr: PR, level: int, y: int) -> int:
            self.pos[id(pr)] = (X0 + level * LEVEL_W, y)
            below = y
            for i, kid in enumerate(sorted(pr.children, key=lambda k: k.state == "findings")):
                below = place(kid, level + 1, y if i == 0 else below)
            return max(below, y + LANE_H)

        y = 2
        roots: list[int] = []
        for stack in stacks:
            roots.append(y)
            y = place(stack, 0, y)
        singles_y = y + (1 if stacks else -1)
        per_row = max(1, (width - X0) // LEVEL_W)
        for i, pr in enumerate(singles):
            self.pos[id(pr)] = (X0 + (i % per_row) * LEVEL_W, singles_y + 1 + (i // per_row) * LANE_H)
        height = max([py + LANE_H for _, py in self.pos.values()], default=4)
        canvas = Canvas(width, height)

        if stacks:
            canvas.put(1, 0, f"STACKS  {len(stacks)}", MUTED)
            canvas.put(1, 1, "main", f"bold {INK}")
            for yy in range(2, roots[-1] + 1):                # the trunk every line leaves from
                canvas.put(2, yy, "│", MUTED)
            for k, root_y in enumerate(roots):
                canvas.put(2, root_y, ("└" if k == len(roots) - 1 else "├") + "──", MUTED)
        if singles:
            canvas.put(1, singles_y, f"ON MAIN, NOT STACKED  {len(singles)}", MUTED)
        if not prs:
            canvas.put(1, 1, "no open pull requests or local branches", MUTED)

        for pr in flat(prs):                                   # the lines between stations
            if pr.parent is None:
                continue
            (px, py), (x, y) = self.pos[id(pr.parent)], self.pos[id(pr)]
            if y == py:
                canvas.put(px + 1, y, "─" * (x - px - 1), MUTED)
                continue
            fork = px + LEVEL_W - 3
            canvas.put(fork, py, "┬", MUTED)
            for yy in range(py + 1, y):
                canvas.put(fork, yy, "├" if canvas.cells[yy][fork][0] == "└" else "│", MUTED)
            canvas.put(fork, y, "└" + "─" * (x - fork - 1), MUTED)

        for pr in flat(prs):                                   # the stations and their captions
            x, y = self.pos[id(pr)]
            icon, color, _, _ = STATUS[pr.state]
            canvas.put(x, y, icon, color)
            if broken(pr) and pr.state != "conflict":
                canvas.put(x + 1, y, "✗", CRIT)
            canvas.put(x, y + 1, pr.key, f"bold {INK}")
            canvas.put(x, y + 2, clip(pr.slug, CAPTION_W), INK2)
            who = movers(pr)
            if who:
                yours = "you" in who
                canvas.put(x, y + 3, "●" if yours else "○", ACCENT if yours else MUTED)
                canvas.put(x + 2, y + 3, clip(who[0], 7), f"bold {INK}" if yours else INK2)
                x_age = x + 3 + len(clip(who[0], 7))
            else:
                x_age = x
            canvas.put(x_age, y + 3, ("· " if who else "") + pr.age, f"bold {INK}" if is_stale(pr.age) else MUTED)
            if pr.mine and any(w.live for w in pr.workers):
                canvas.put(x_age + len(pr.age) + (3 if who else 1), y + 3, "▶", ACCENT)
            if pr is self.selected:
                for yy in range(y, y + 4):
                    canvas.paint(x - 1, yy, CAPTION_W + 1, RAISED)
        return canvas.text()

    def inspector(self) -> Text:
        pr = self.selected
        text = Text()
        if pr is None:
            return text.append("nothing selected", style=MUTED)
        icon, color, label, _ = STATUS[pr.state]
        what = {"local": f"no PR yet, #{pr.number} is reserved",
                "findings": f"local review of PR {pr.number}"}.get(pr.state, f"PR {pr.number}")
        text.append(f"{pr.ticket.upper()} · {what}\n", style=MUTED)
        text.append(pr.slug + "\n", style=f"bold {INK}")
        text.append(pr.title + "\n\n", style=INK2)
        text.append(icon, style=color)
        text.append(f" {label[0].upper()}{label[1:]}\n\n", style=INK)

        who, what = pr.next
        if who:
            text.append("NEXT MOVE\n", style=MUTED)
            text.append("● " if who == "you" else "○ ", style=ACCENT if who == "you" else MUTED)
            text.append(f"{who} · {what}\n\n", style=f"bold {INK}" if who == "you" else INK)

        text.append("REVIEW\n", style=MUTED)                   # a timeline, oldest first
        if not pr.history:
            text.append("○ ", style=AXIS).append("nobody has been asked yet\n", style=INK2)
        for event in pr.history:
            if event.kind == "push":
                text.append("│ ", style=AXIS)
                text.append(f"{event.commits} commits pushed by {event.who}".ljust(34), style=INK2)
            else:
                mark, mark_color, words = WORD[event.verdict]
                text.append(mark + " ", style=mark_color)
                name = "you" if event.who == ME else event.who
                text.append(f"round {event.round} · {name} {words}".ljust(34), style=INK)
            text.append(event.age.rjust(4) + "\n", style=MUTED)
            if event.threads:
                text.append("│ ", style=AXIS).append(f"{event.threads} threads still open\n", style=INK2)
        text.append("\n")

        off = [w for w in pr.workers if w.name]
        text.append("NAMES  ", style=MUTED)
        text.append("≠ one is off\n" if off else "✓ all agree\n", style=CRIT if off else GOOD)
        text.append(f"{self.repo}-{pr.key}\n", style=f"bold {INK}")      # the stem they share
        text.append("  branch   ", style=MUTED).append(f"…-{pr.slug}\n", style=INK2)
        for worker in pr.workers if pr.mine else []:
            text.append("  session  ", style=MUTED)
            if worker.name:
                text.append(worker.name, style=INK).append("  ≠\n", style=CRIT)
            else:
                tail = "…" if pr.state == "findings" else f"…-{worker.serial}"
                text.append(tail + "\n", style=INK2)
        text.append("  PR       ", style=MUTED)
        text.append(clip(f"#{pr.number} · {pr.ticket.upper()} {pr.title}", 31) + "\n"
                    if pr.is_pr else f"none yet · issue #{pr.number}\n", style=INK2)
        text.append("  Jira     ", style=MUTED)
        text.append(f"{pr.ticket.upper()} · {pr.jira_status()}\n", style=INK2)
        if pr.same_ticket:
            text.append("  also     ", style=MUTED)
            text.append(clip(f"{pr.ticket.upper()} is on {pr.same_ticket}", 31) + "\n", style=INK)
        text.append("\n")

        text.append("ON IT\n", style=MUTED)
        if not pr.mine:
            text.append(f"{pr.author} on GitHub · no session here\n", style=INK2)
        for worker in pr.workers if pr.mine else []:
            text.append("▶ " if worker.live else "▷ ", style=ACCENT if worker.live else MUTED)
            text.append(f"{worker.serial}  ", style=INK)
            text.append(clip(worker.note, 36) + "\n", style=INK2 if worker.live else MUTED)
        if pr.mine and not pr.workers:
            text.append("no session here\n", style=INK2)
        text.append("\n")

        text.append("CHECKS  ", style=MUTED)
        text.append({"pass": "✓ ", "fail": "✗ ", "run": "… ", "none": "· "}[pr.ci],
                    style={"pass": GOOD, "fail": CRIT}.get(pr.ci, MUTED))
        text.append((pr.ci_note or "none yet") + "\n", style=INK2)
        text.append("MERGE   ", style=MUTED)
        text.append(pr.merge + "\n", style=INK if "conflict" in pr.merge else INK2)
        return text

    def legend(self) -> Text:
        text = Text()
        short = {"ready": "ready", "rereview": "re-review", "changes": "changes asked",
                 "local": "local", "findings": "local review"}
        for state, (icon, color, label, _) in STATUS.items():
            text.append(icon, style=color)
            text.append(f" {short.get(state, label)}   ", style=MUTED)
        text.append("\n")
        text.append("✗", style=CRIT).append(" check failing   ", style=MUTED)
        text.append("●", style=ACCENT).append(" your move   ", style=MUTED)
        text.append("○", style=MUTED).append(" their move   ", style=MUTED)
        text.append("▶", style=ACCENT).append(" session at work", style=MUTED)
        return text

    # ---- keys --------------------------------------------------------------
    def action_go(self, where: str) -> None:
        if self.selected is None:
            return
        everything = flat(REPOS[self.repo])
        x, y = self.pos[id(self.selected)]
        spots = [(self.pos[id(pr)], pr) for pr in everything if pr is not self.selected]
        if where in ("left", "right"):
            ahead = [(px, pr) for (px, py), pr in spots if py == y and (px > x) == (where == "right")]
            if ahead:
                target = (min if where == "right" else max)(ahead, key=lambda item: item[0])[1]
            elif where == "right":
                target = self.selected.children[0] if self.selected.children else None
            else:
                target = self.selected.parent
        else:
            beyond = [((px, py), pr) for (px, py), pr in spots if (py > y) == (where == "down") and py != y]
            if not beyond:
                return
            row = (min if where == "down" else max)(py for (_, py), _ in beyond)
            target = min((item for item in beyond if item[0][1] == row),
                         key=lambda item: abs(item[0][0] - x))[1]
        if target is not None:
            self.selected = target
            self.draw()

    def action_repo(self, step: int) -> None:
        names = list(REPOS)
        self.repo = names[(names.index(self.repo) + step) % len(names)]
        self.selected = None
        self.draw()


if __name__ == "__main__":
    Studio().run()
