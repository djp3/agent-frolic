"""Agent Frolic mock-up, option 5 v2: minimal. Still one page, no panels, words over icons.
The page now answers one question: is everything that can move moving, and if not, what gets
it going again? A few short lines at the top name what needs effort (a session at a prompt,
a move with no agent, who a branch waits on, who has nothing to do). Below them the map:
each base branch, the points in its history that stacks hang from, and every branch saying
in a few words whether it is in progress or what it waits on, to the end of the wait.

Everything is grey except what you can fix yourself, which is white with a blue dot. Red
appears only where something is broken. The review state is not a column any more: the next
move implies it, and the sentences behind enter spell it out.

    .venv/bin/python mockup/option5_minimal_v2.py

Keys: up/down move, enter opens or closes the sentences for a branch, y shows only what is
held up, p steps through the people (their work as a list, or that they have none),
tab goes to the next repo, q quits.
"""
from __future__ import annotations

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Static

from frolic_v2 import (ACCENT, AXIS, CRIT, INK, INK2, ME, MUTED, PLANE, PR, RAISED, REPOS, STATUS,
                       Blocker, Row, agent, anchor_note, blockers, broken, chain, flat, pipe,
                       repo_status, root_cause, roster, session_state, tree_rows, verdicts, work_of)

NAME_W, PIPE_W, LABEL_W = 44, 44, 16
QUIET = "#6e6d67"                # anchor points: readable, but behind everything else


def short(pr: PR) -> str:
    return f"pr-{pr.number:04d}"


def fixable(pr: PR) -> bool:
    """The move is yours and nothing is working on it: a prompt to answer or an agent to assign."""
    here = pipe(pr)
    return here is not None and here.state in ("needs you", "no agent")


def held_up(pr: PR) -> bool:
    """Not working, and not merely queued behind something that is."""
    end, _ = root_cause(pr)
    return pipe(pr) is not None and end is not None and end.state != "working"


def waits_phrase(pr: PR) -> str:
    """In progress, or what the branch waits on, followed to the end of the wait."""
    here = pipe(pr)
    if here is None:
        return f"local review of {short(pr)}" + (", session at work" if any(w.live for w in pr.workers) else "")
    if here.state == "working":
        return "in progress"
    if here.state == "needs you":
        return "waits on you, at a prompt"
    if here.state == "no agent":
        return "waits on an agent"
    if here.on_pr is None:
        return f"waits on {here.on}"
    links = chain(pr)[1:]
    end = pipe(links[-1])
    tail = {"working": ", which is in progress", "needs you": ", which waits on you",
            "no agent": ", which has no agent"}.get(end.state, f", which waits on {end.on}")
    return "waits on " + " then ".join(short(link) for link in links) + tail


def move_phrase(pr: PR) -> str:
    """Who holds the next move, what it is, and how far along it is."""
    here = pipe(pr)
    if here is None:
        return ""
    text = f"{', '.join(here.who)} · {here.what}"
    if here.state == "working":
        return text + (f" · {here.done} of {here.total} done" if here.total else "")
    return f"{text} · {here.progress}"


def sentences(repo: str, pr: PR, held: dict[int, Blocker]) -> list[str]:
    """Everything a detail pane would say, as plain sentences."""
    out: list[str] = []
    label = STATUS[pr.state][2]
    said = []
    for who, event in verdicts(pr).items():
        name = "you" if who == ME else who
        words = {"approved": "approved", "changes": "asked for changes",
                 "requested": "were asked to review" if name == "you" else "was asked to review",
                 "rerequested": "was asked to look again"}[event.verdict]
        said.append(f"{name} {words} {event.age} ago"
                    + (f", {event.threads} threads still open" if event.threads else ""))
    if pr.state == "findings":
        out.append(f"Findings from a local review of {short(pr)}, kept on this Mac.")
    elif said:
        out.append(f"{label.capitalize()}. " + ". ".join(s[0].upper() + s[1:] for s in said) + ".")
    elif pr.is_pr:
        out.append(f"{label.capitalize()}. Nobody has been asked to review it yet.")
    else:
        out.append(f"No pull request yet. Issue #{pr.number} holds its number. {pr.merge.capitalize()}.")

    here, assigned = pipe(pr), agent(pr)
    note = assigned.note.replace(" · ", ", ") if assigned else ""
    if here is None:
        for worker in pr.workers:
            state = session_state(worker)
            out.append(f"Session {worker.serial} is working on it now: {worker.note.replace(' · ', ', ')}."
                       if state == "working" else f"Session {worker.serial} has ended.")
    elif here.state == "working":
        out.append(f"In progress: session {assigned.serial} is working on it now, {note}."
                   + (f" {here.counted.capitalize()} done." if here.total else ""))
    elif here.state == "needs you":
        out.append(f"Session {assigned.serial} sits at its prompt, waiting for you to {here.what}.")
    elif here.state == "no agent":
        out.append(f"The next move is yours, to {here.what}, and no agent is assigned."
                   + (f" It is stalled{': ' + here.counted if here.counted else ', partly done'}."
                      if here.partly else ""))
    else:
        if here.on_pr is None:
            wait = f"It waits on {here.on} to {here.what}. {here.progress.capitalize()}."
        else:
            links = chain(pr)[1:]
            end = pipe(links[-1])
            ending = {"working": "which is in progress",
                      "needs you": "which waits on you, at a prompt",
                      "no agent": "which waits on an agent being assigned",
                      }.get(end.state, f"which waits on {end.on} to {end.what} ({end.progress})")
            wait = "It waits on " + ", which waits on ".join(short(link) for link in links) + f", {ending}."
        if assigned:
            wait += f" Session {assigned.serial} is assigned and idle until then."
        out.append(wait)
    if id(pr) in held and held[id(pr)].behind:
        out.append("It holds up " + ", ".join(short(p) for p in held[id(pr)].behind) + ".")

    if not pr.mine:
        out.append(f"It is {pr.author}'s, on GitHub. Their agents cannot be seen from here.")
    off = [w for w in pr.workers if w.name]
    if off:
        out.append(f"The session is named {off[0].name}. It should be {repo}-{pr.key}-{off[0].serial}.")
    if pr.is_pr:
        checks = {"pass": f"Checks pass, {pr.ci_note}.", "fail": f"Checks fail, {pr.ci_note}.",
                  "run": f"Checks are running, {pr.ci_note}.", "none": "No checks have run."}[pr.ci]
        out.append(f"{checks} {pr.merge.capitalize()}.".replace(" · ", ", "))
    if pr.state != "findings":
        out.append(f"Its stack sits {anchor_note(pr)}.")
    if pr.same_ticket:
        out.append(f"{pr.ticket.upper()} is also on {pr.same_ticket}.")
    return out


class MinimalV2(App):
    CSS = f"""
    Screen {{ background: {PLANE}; color: {INK}; }}
    #page {{ margin: 2 3 0 3; height: 1fr; text-wrap: nowrap; text-overflow: ellipsis; }}
    #hints {{ height: 1; margin: 0 3 1 3; color: {QUIET}; text-wrap: nowrap; text-overflow: ellipsis; }}
    """
    BINDINGS = [
        Binding("up", "move(-1)", show=False), Binding("down", "move(1)", show=False),
        Binding("enter", "open", show=False), Binding("y", "held", show=False),
        Binding("p", "person", show=False),
        Binding("tab", "repo(1)", show=False, priority=True),
        Binding("shift+tab", "repo(-1)", show=False, priority=True),
        Binding("q", "quit", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.repo = next(iter(REPOS))
        self.sel = 0
        self.opened: set[int] = set()
        self.only_held = False
        self.person: str | None = None

    def compose(self) -> ComposeResult:
        yield Static(id="page")
        yield Static("↑↓ move    ↵ details    y only what is held up    p by person    "
                     "⇥ next repo    q quit", id="hints")

    def on_mount(self) -> None:
        self.draw()

    def on_resize(self) -> None:
        self.draw()

    # ---- what is on the page -------------------------------------------------
    def rows(self) -> list[Row]:
        prs = REPOS[self.repo]
        if self.person:
            return [Row("branch", "", pr, pr.root().base) for pr in work_of(prs, self.person)]
        if self.only_held:
            return [Row("branch", "", pr, pr.root().base) for pr in flat(prs) if held_up(pr)]
        return tree_rows(prs)

    def tabs(self) -> Text:
        """The repos, each with the least that says how it is doing."""
        text = Text()
        for name, prs in REPOS.items():
            current = name == self.repo
            text.append(name, style=f"bold {INK}" if current else MUTED)
            fix, waited, _ = repo_status(prs)
            if not flat(prs):
                text.append("  empty", style=QUIET)
            elif not fix and not waited:
                text.append("  all moving", style=QUIET)
            else:
                text.append("  ")
                if fix:
                    text.append("● ", style=ACCENT)
                    text.append(f"{fix} to fix", style=INK2 if current else MUTED)
                if waited:
                    text.append((", " if fix else "") + f"{waited} waited on", style=MUTED)
            text.append("    ")
        return text

    def effort(self) -> list[Text]:
        """What needs effort to get the pipeline going, one short line per kind."""
        prs = REPOS[self.repo]
        found = blockers(prs)

        def item(b: Blocker, words: bool) -> str:
            text = short(b.pr) + (f" {b.pipe.what}" if words else "")
            return text + (f" (holds up {', '.join(short(p) for p in b.behind)})" if b.behind else "")

        def line(label: str, items: list[str], yours: bool) -> Text:
            text = Text("● " if yours else "  ", style=ACCENT)
            text.append(f"{label:<{LABEL_W}}", style=INK if yours else MUTED)
            return text.append(" · ".join(items), style=INK if yours else MUTED)

        lines: list[Text] = []
        prompt = [item(b, True) for b in found if b.kind == "needs you"]
        unassigned = [item(b, True) for b in found if b.kind == "no agent"]
        waited: dict[str, list[str]] = {}
        for b in found:
            if b.kind == "person":
                for name in b.who.split(","):
                    waited.setdefault(name.strip(), []).append(item(b, False))
        free = [name for name, has_work in roster(prs) if not has_work and name != "you"]
        if prompt:
            lines.append(line("At a prompt", prompt, True))
        if unassigned:
            lines.append(line("No agent", unassigned, True))
        if waited:
            lines.append(line("Waits on", [f"{name} for {', '.join(items)}"
                                           for name, items in waited.items()], False))
        if free:
            lines.append(line("Nothing to do", free, True))
        if not lines:
            lines.append(Text("  Everything that can move is moving." if flat(prs)
                              else "  Nothing open.", style=MUTED))
        return lines

    def person_line(self) -> Text:
        """One person: whether they have any work. Their work is the list below."""
        name = self.person or ""
        has_work = bool(work_of(REPOS[self.repo], name))
        verb = "have" if name == "you" else "has"
        if has_work:
            return Text("  ").append(f"{name} {verb} work", style=INK)
        text = Text("● " if name != "you" else "  ", style=ACCENT)
        return text.append(f"{name} {verb} nothing to do", style=INK)

    def draw(self) -> None:
        prs = REPOS[self.repo]
        rows = self.rows()
        branches = [row for row in rows if row.kind == "branch"]
        self.sel = max(0, min(len(branches) - 1, self.sel))
        chosen = branches[self.sel].pr if branches else None
        held = {id(b.pr): b for b in blockers(prs)}
        width = max(60, self.size.width - 6)
        page = self.tabs().append("\n\n")

        if self.person:
            page.append_text(self.person_line()).append("\n")
        else:
            for line in self.effort():
                page.append_text(line).append("\n")
        if self.only_held and not self.person:
            page.append("\n  showing only what is held up\n", style=INK2)
        page.append("\n")

        body: list[Text] = []                 # the map, one line each
        chosen_at = 0
        for row in rows:
            if row.kind == "anchor":
                if body:
                    body.append(Text())
                body.append(Text(f"    {row.label}", style=f"bold {MUTED}"))
                continue
            if row.kind == "point":
                body.append(Text("    " + row.lead, style=AXIS).append(row.label, style=QUIET))
                continue
            pr = row.pr
            bright = fixable(pr)
            tone = INK if bright else MUTED
            line = Text()
            line.append("› " if pr is chosen else "  ", style=INK)
            line.append("● " if bright else "  ", style=ACCENT)
            line.append(row.lead, style=AXIS)
            line.append(pr.key, style=f"bold {tone}" if bright else tone)
            line.append("-" + pr.slug, style=tone)
            line.append(" " * max(1, NAME_W - len(row.lead) - len(pr.name)))
            waits = Text(waits_phrase(pr), style=tone)
            if broken(pr):
                waits.append("  ✗", style=CRIT)
                waits.append(" conflict" if pr.state == "conflict" else " check failing", style=tone)
            line.append_text(waits)
            line.append(" " * max(1, PIPE_W - waits.cell_len))
            line.append(move_phrase(pr), style=tone)
            line.truncate(width, overflow="ellipsis")
            line.append(" " * max(0, width - line.cell_len))
            if pr is chosen:
                line.stylize(f"on {RAISED}")
                chosen_at = len(body)
            body.append(line)
            if id(pr) in self.opened:
                for sentence in sentences(self.repo, pr, held):
                    body.append(Text(" " * (6 + len(row.lead)) + sentence, style=INK2))
                body.append(Text())

        room = max(4, self.size.height - 4 - page.plain.count("\n"))     # lines left for the map
        if len(body) > room:                  # keep the chosen row in view, no scrollbar
            start = min(max(0, chosen_at - room // 2), len(body) - room)
            body = body[start:start + room]
        for line in body:
            page.append_text(line).append("\n")
        self.query_one("#page", Static).update(page)

    # ---- keys ----------------------------------------------------------------
    def action_move(self, step: int) -> None:
        self.sel += step
        self.draw()

    def action_open(self) -> None:
        branches = [row for row in self.rows() if row.kind == "branch"]
        if branches:
            self.opened ^= {id(branches[self.sel].pr)}
            self.draw()

    def action_held(self) -> None:
        self.only_held, self.person, self.sel = not self.only_held, None, 0
        self.draw()

    def action_person(self) -> None:
        names = [None] + [name for name, _ in roster(REPOS[self.repo])]
        self.person = names[(names.index(self.person) + 1) % len(names)]
        self.sel = 0
        self.draw()

    def action_repo(self, step: int) -> None:
        names = list(REPOS)
        self.repo = names[(names.index(self.repo) + step) % len(names)]
        self.sel, self.person = 0, None
        self.draw()


if __name__ == "__main__":
    MinimalV2().run()
