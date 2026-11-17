"""Agent Frolic mock-up, option 5: minimal. One page, no panels, no icons to learn. Stacks are
shown by indentation alone. Everything is grey except what is yours to do, which is white
with a blue dot; red appears only where something is broken. Details are sentences, and only
when you ask for them.

    .venv/bin/python mockup/option5_minimal.py

Keys: up/down move, enter opens or closes the details of a branch, y shows only your moves,
tab goes to the next repo, q quits.
"""
from __future__ import annotations

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Static

from frolic_common import (ACCENT, AXIS, CRIT, INK, INK2, ME, MUTED, PLANE, PR, RAISED, REPOS,
                           STATUS, WORD, broken, flat, is_stale, movers, verdicts)

NAME_W, STATE_W = 48, 22


def sentences(repo: str, pr: PR) -> list[str]:
    """What the detail pane of the other options says, as plain sentences."""
    out = []
    said = []
    for who, event in verdicts(pr).items():
        name = "you" if who == ME else who
        words = {"approved": "approved", "changes": "asked for changes",
                 "requested": "was asked to review" if name != "you" else "were asked to review",
                 "rerequested": "was asked to look again"}[event.verdict]
        said.append(f"{name} {words} {event.age} ago"
                    + (f", {event.threads} threads still open" if event.threads else ""))
    if pr.state == "findings":
        out.append(f"Findings from a local review of pr-{pr.number:04d}, kept on this Mac.")
    elif said:
        out.append((". ".join(s[0].upper() + s[1:] for s in said)) + ".")
    elif pr.is_pr:
        out.append("Nobody has been asked to review it yet.")
    else:
        out.append(f"No pull request yet. Issue #{pr.number} holds its number. {pr.merge.capitalize()}.")

    if not pr.mine:
        out.append(f"It is {pr.author}'s, on GitHub. None of your sessions is on it.")
    else:
        for worker in pr.workers:
            out.append((f"Session {worker.serial} is at work: {worker.note}." if worker.live
                        else f"Session {worker.serial} is idle: {worker.note}.").replace(" · ", ", "))
    off = [w for w in pr.workers if w.name]
    if off:
        out.append(f"The session is named {off[0].name}. It should be {repo}-{pr.key}-{off[0].serial}.")
    if pr.is_pr:
        checks = {"pass": f"Checks pass, {pr.ci_note}.", "fail": f"Checks fail, {pr.ci_note}.",
                  "run": f"Checks are running, {pr.ci_note}.", "none": "No checks have run."}[pr.ci]
        out.append(f"{checks} {pr.merge.capitalize().replace(' · ', ', ')}.".replace(" · ", ", "))
    if pr.same_ticket:
        out.append(f"{pr.ticket.upper()} is also on {pr.same_ticket}.")
    return out


class Minimal(App):
    CSS = f"""
    Screen {{ background: {PLANE}; color: {INK}; }}
    #page {{ margin: 2 4 0 4; height: 1fr; text-wrap: nowrap; text-overflow: ellipsis; }}
    #hints {{ height: 1; margin: 0 4 1 4; color: {AXIS}; }}
    """
    BINDINGS = [
        Binding("up", "move(-1)", show=False), Binding("down", "move(1)", show=False),
        Binding("enter", "open", show=False), Binding("y", "yours", show=False),
        Binding("tab", "repo(1)", show=False, priority=True),
        Binding("shift+tab", "repo(-1)", show=False, priority=True),
        Binding("q", "quit", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.repo = next(iter(REPOS))
        self.sel = 0
        self.opened: set[int] = set()
        self.only_yours = False

    def compose(self) -> ComposeResult:
        yield Static(id="page")
        yield Static("↑↓ move    ↵ details    y only yours    ⇥ next repo    q quit", id="hints")

    def on_mount(self) -> None:
        self.draw()

    def on_resize(self) -> None:
        self.draw()

    def rows(self) -> list[PR]:
        everything = flat(REPOS[self.repo])
        return [pr for pr in everything if "you" in movers(pr)] if self.only_yours else everything

    def draw(self) -> None:
        everything = flat(REPOS[self.repo])
        rows = self.rows()
        self.sel = max(0, min(len(rows) - 1, self.sel))
        width = max(60, self.size.width - 8)
        page = Text()

        for name in REPOS:
            page.append(name, style=f"bold {INK}" if name == self.repo else AXIS)
            page.append("    ")
        page.append("\n\n")

        yours = sum("you" in movers(pr) for pr in everything)
        theirs = sum(bool(movers(pr)) and "you" not in movers(pr) for pr in everything)
        ready = sum(pr.state == "ready" for pr in everything)
        for count, words in ((yours, "moves are yours"), (theirs, "wait on others"),
                             (ready, "ready to merge")):
            page.append(str(count), style=f"bold {INK}")
            page.append(f" {words}     ", style=MUTED)
        if self.only_yours:
            page.append("showing only yours", style=ACCENT)
        page.append("\n\n\n")

        if not rows:
            page.append("    nothing here", style=MUTED)
        for i, pr in enumerate(rows):
            mine_to_do = "you" in movers(pr)
            tone = INK if mine_to_do else MUTED
            line = Text()
            line.append("› " if i == self.sel else "  ", style=INK)
            line.append("● " if mine_to_do else "  ", style=ACCENT)
            indent = "" if self.only_yours else "  " * (pr.depth - 1)
            line.append(indent)
            line.append(pr.key, style=f"bold {tone}" if mine_to_do else tone)
            line.append("-" + pr.slug, style=tone)
            line.append(" " * max(1, NAME_W - len(indent) - len(pr.name)))
            state = Text(STATUS[pr.state][2], style=INK2 if mine_to_do else MUTED)
            if broken(pr):
                state.append(" ✗", style=CRIT)
                if pr.state != "conflict":
                    state.append(" check failing", style=INK2 if mine_to_do else MUTED)
            line.append_text(state)
            line.append(" " * max(1, STATE_W + 10 - state.cell_len))
            who, what = pr.next
            if mine_to_do:
                line.append(what, style=INK)
            elif who:
                line.append(who, style=MUTED)
            if is_stale(pr.age):
                line.append(f"   quiet for {pr.age}", style=INK2)
            line.append(" " * max(0, width - line.cell_len))
            if i == self.sel:
                line.stylize(f"on {RAISED}")
            page.append_text(line).append("\n")
            if id(pr) in self.opened:
                for sentence in sentences(self.repo, pr):
                    page.append(" " * (6 + len(indent)) + sentence + "\n", style=INK2)
                page.append("\n")
        self.query_one("#page", Static).update(page)

    def action_move(self, step: int) -> None:
        self.sel += step
        self.draw()

    def action_open(self) -> None:
        rows = self.rows()
        if rows:
            self.opened ^= {id(rows[self.sel])}
            self.draw()

    def action_yours(self) -> None:
        self.only_yours = not self.only_yours
        self.sel = 0
        self.draw()

    def action_repo(self, step: int) -> None:
        names = list(REPOS)
        self.repo = names[(names.index(self.repo) + step) % len(names)]
        self.sel = 0
        self.draw()


if __name__ == "__main__":
    Minimal().run()
