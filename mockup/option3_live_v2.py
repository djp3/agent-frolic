"""Agent Frolic mock-up, option 3, v2: live. Motion now has one meaning: work is moving.

A working agent spins. A progress meter blinks at the step being worked. When a branch waits
on another branch and the end of that wait is at work, a current runs up the rail from the
waiting branch to the one it waits on. Everything else holds still. What is idle and could
be working, and that you can fix yourself (a session at its prompt, a move with no agent, a
person with nothing to do), is the only blue on the screen, and it breathes slowly, so
stillness is what draws the eye.

The scoreboard of counters is gone. In its place are three lines that say what needs effort
to get the pipeline going: what waits on you, which people branches wait on (with what is
queued behind them), and who has work or has nothing to do. Made-up events land every few
seconds on agent-frolic and those lines change with them: an agent stops at its prompt, you
answer and it resumes, a reviewer gets further, an agent is assigned, someone with nothing
to do is asked to review, a wait is released, a branch lands.

    .venv/bin/python mockup/option3_live_v2.py

Keys: up/down pick a branch, p steps through the people (their work as a list, the rest of
the map greyed), e fires the next event now, r sweeps a refresh, d folds the drawer, space
pauses all motion, tab goes to the next repo, q quits.
"""
from __future__ import annotations

import copy
import math

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Static

from frolic_v2 import (AXIS, CRIT, GOOD, INK, INK2, MUTED, PLANE, PR, RAISED, REPOS, STATUS,
                       SURFACE, Canvas, Worker, anchor_note, blockers, broken, chain, flat, link,
                       movers, pipe, repo_status, root_cause, roster, rv, session_state, tree_rows,
                       work_of)

FPS = 12
EVERY = 4.5                      # seconds between events
SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
TURN = "◐◓◑◒"                    # an icon turning over as its state changes
BREATH = ["#256abf", "#2a78d6", "#3987e5", "#5598e7", "#6da7ec"]      # one hue, stepped
FLASH = ["#4a4a46", "#444441", "#3d3d3a", "#363633", "#2f2f2d", "#292927"]
DIM = "#5c5b56"                  # rows set aside while you look at one person
X_TREE, X_PIPE, X_MOVE, X_PROG, X_AGENT, X_AGE = 2, 50, 76, 111, 133, 144
LANDING = 14                     # frames a merged branch stays on screen before it goes


def short(pr: PR) -> str:
    return f"pr-{pr.number:04d}"


def cut(text: str, width: int) -> str:
    return text if len(text) <= width else text[:max(0, width - 1)] + "…"


# ---- the made-up events: each changes the data and says what happened --------------------

def ev_prompt(app: "LiveV2") -> tuple[list[PR], str]:
    pr = app.find(13)
    worker = pr.workers[0]
    worker.live, worker.mode, worker.note = False, "prompt", "waiting at its prompt"
    pr.progress, pr.next = (2, 3), ("you", "decide on the last thread")
    return [pr], "session 01 on pr-0013 replied to 2 of 3 threads and stopped at its prompt"


def ev_resume(app: "LiveV2") -> tuple[list[PR], str]:
    pr = app.find(13)
    worker = pr.workers[0]
    worker.live, worker.mode, worker.note = True, "", "replying to the last thread"
    pr.next = ("you", "reply to the last thread")
    return [pr], "you answered · session 01 on pr-0013 is working again"


def ev_reviewer(app: "LiveV2") -> tuple[list[PR], str]:
    pr = app.find(15)
    pr.progress = (4, 6)
    return [pr], "mira read two more files on pr-0015 · 4 of 6, still hers to finish"


def ev_assign(app: "LiveV2") -> tuple[list[PR], str]:
    pr = app.find(20)
    pr.workers.append(Worker("02", True, "reading keychain.py"))
    return [pr], "an agent was assigned to pr-0020 · session 02 is at work"


def ev_ask(app: "LiveV2") -> tuple[list[PR], str]:
    pr = app.find(24)
    pr.next = ("mira, kenji", "review")
    pr.history.append(rv(1, "kenji", "requested", "now"))
    return [pr], "kenji was asked to review pr-0024 · kenji now has work"


def ev_release(app: "LiveV2") -> tuple[list[PR], str]:
    below, above = app.find(17), app.find(21)
    below.state, below.next, below.started = "review", ("mira", "review"), False
    below.history.append(rv(1, "mira", "requested", "now"))
    for worker in below.workers:
        if worker.live:
            worker.live, worker.mode, worker.note = False, "waiting", "idle until mira reviews"
    above.waits_on, above.next = 0, ("you", "open the PR")
    worker = above.workers[0]
    worker.live, worker.mode, worker.note = True, "", "opening the PR"
    return [below, above], "pr-0017 is up for review · session 01 on pr-0021 stops waiting and starts"


def ev_land(app: "LiveV2") -> tuple[list[PR], str]:
    pr = app.find(12)
    app.leaving[id(pr)] = app.frame
    pr.next = ("", "")                            # merged: it holds no move any more
    return [pr], "you merged pr-0012 · pr-0013 now sits on main"


SCRIPT = [ev_prompt, ev_resume, ev_reviewer, ev_assign, ev_ask, ev_release, ev_land]


class LiveV2(App):
    CSS = f"""
    Screen {{ background: {PLANE}; color: {INK}; }}
    #tabs {{ height: 1; margin: 1 2 0 2; text-wrap: nowrap; text-overflow: ellipsis; }}
    #effort {{ height: 3; margin: 1 2 0 2; text-wrap: nowrap; text-overflow: ellipsis; }}
    #map {{ height: 1fr; margin: 1 1 0 1; }}
    #drawer {{ height: 8; background: {SURFACE}; padding: 1 2; margin: 0 2;
               text-wrap: nowrap; text-overflow: ellipsis; overflow: hidden; }}
    #events {{ height: 3; margin: 1 2 0 2; text-wrap: nowrap; text-overflow: ellipsis; }}
    #hints {{ height: 1; margin: 0 2; color: {MUTED}; text-wrap: nowrap; text-overflow: ellipsis; }}
    """
    BINDINGS = [
        Binding("up", "move(-1)", show=False), Binding("down", "move(1)", show=False),
        Binding("p", "person", show=False), Binding("e", "fire", show=False),
        Binding("r", "sweep", show=False), Binding("d", "drawer", show=False),
        Binding("space", "pause", show=False),
        Binding("tab", "repo(1)", show=False, priority=True),
        Binding("shift+tab", "repo(-1)", show=False, priority=True),
        Binding("q", "quit", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.frame = 0
        self.paused = False
        self.drawer_open = True
        self.load(next(iter(REPOS)))

    def compose(self) -> ComposeResult:
        yield Static(id="tabs")
        yield Static(id="effort")
        yield Static(id="map")
        yield Static(id="drawer")
        yield Static(id="events")
        yield Static("↑↓ branch    p person    e next event now    r refresh    d drawer    "
                     "space pause    ⇥ next repo    q quit", id="hints")

    def on_mount(self) -> None:
        self.set_interval(1 / FPS, self.tick)
        self.draw()

    def on_resize(self) -> None:
        self.draw()

    # ---- state -------------------------------------------------------------
    def load(self, repo: str) -> None:
        """A fresh copy of one repo's data, so events can change it and a replay can reset it."""
        self.repo = repo
        self.prs = link(copy.deepcopy(REPOS[repo]))
        branches = self.branches()
        self.sel: PR | None = branches[0] if branches else None
        self.person: str | None = None
        self.flash: dict[int, int] = {}           # row -> frame an event hit it
        self.touched: dict[int, int] = {}         # row -> frame of its last event, for "12s"
        self.leaving: dict[int, int] = {}         # row -> frame it was merged
        self.events: list[tuple[int, str]] = []
        self.step = 0
        self.next_at = self.frame + int(EVERY * FPS)
        self.sweep_at: int | None = None
        self.refreshed_at = self.frame

    def find(self, number: int) -> PR:
        return next(pr for pr in flat(self.prs) if pr.number == number and pr.state != "findings")

    def branches(self) -> list[PR]:
        return [row.pr for row in tree_rows(self.prs) if row.pr is not None]

    def at_work(self) -> int:
        return sum(worker.live for pr in flat(self.prs) for worker in pr.workers)

    def behind(self, pr: PR) -> list[PR]:
        """The branches that wait on this one, directly or through a chain."""
        return [other for other in flat(self.prs) if other is not pr and pr in chain(other)]

    # ---- the clock ---------------------------------------------------------
    def tick(self) -> None:
        if self.paused:
            return
        self.frame += 1
        if self.repo == "agent-frolic" and self.frame >= self.next_at:
            self.action_fire()
        for pr in flat(self.prs):                 # a merged branch leaves once it has been seen
            if self.frame - self.leaving.get(id(pr), self.frame) > LANDING:
                self.remove(pr)
        self.draw()

    def remove(self, pr: PR) -> None:
        """Take a merged branch out of the map; what sat on it now sits where it did."""
        for kid in pr.children:
            kid.base, kid.behind = pr.base, pr.behind
        siblings = self.prs if pr.parent is None else pr.parent.children
        at = siblings.index(pr)
        siblings[at:at + 1] = pr.children
        link(self.prs)
        self.leaving.pop(id(pr), None)
        if self.sel is pr:
            branches = self.branches()
            self.sel = branches[0] if branches else None

    def action_fire(self) -> None:
        if self.repo != "agent-frolic":
            return
        self.next_at = self.frame + int(EVERY * FPS)
        if self.step >= len(SCRIPT):
            sel_number = self.sel.number if self.sel else None
            self.load(self.repo)
            if sel_number is not None:
                self.sel = next((pr for pr in self.branches() if pr.number == sel_number), self.sel)
            self.events.append((self.frame, "replaying the made-up events from the start"))
            return
        hit, said = SCRIPT[self.step](self)
        self.step += 1
        for pr in hit:
            pr.age = "0m"                         # kept parseable: the shared helpers sort by age
            self.flash[id(pr)] = self.touched[id(pr)] = self.frame
        self.events.append((self.frame, said))

    # ---- drawing -----------------------------------------------------------
    def draw(self) -> None:
        self.query_one("#tabs", Static).update(self.tabs())
        self.query_one("#effort", Static).update(self.effort())
        self.query_one("#map", Static).update(self.map())
        self.query_one("#drawer", Static).update(self.drawer())
        self.query_one("#events", Static).update(self.log_text())

    def breath(self) -> str:
        """Blue, breathing once in four seconds: idle, and you can get it going."""
        wave = (math.sin(self.frame / FPS * math.pi / 2) + 1) / 2
        return BREATH[round(wave * (len(BREATH) - 1))]

    def spin(self, offset: int = 0) -> str:
        return SPIN[(self.frame + offset) % len(SPIN)]

    def tabs(self) -> Text:
        text = Text()
        for name in REPOS:
            current = name == self.repo
            yours, nudge, _ = repo_status(self.prs if current else REPOS[name])
            text.append(name, style=f"bold underline {INK}" if current else MUTED)
            if yours:
                text.append(" ●", style=self.breath())
                text.append(str(yours), style=INK if current else INK2)
            if nudge:
                text.append(" ○", style=MUTED)
                text.append(str(nudge), style=MUTED)
            if not yours and not nudge:
                text.append(" ·", style=AXIS)
            text.append("      ")
        working = self.at_work()
        if self.paused:
            text.append("❚❚ paused", style=MUTED)
        elif working:
            text.append(self.spin(), style=INK)
            text.append(f" {working} agent{'' if working == 1 else 's'} at work", style=INK2)
        elif any(b.yours for b in blockers(self.prs)):
            text.append("●", style=self.breath())
            text.append(" no agent is at work", style=f"bold {INK}")
        else:
            text.append("no agent is at work", style=MUTED)
        seconds = (self.frame - self.refreshed_at) // FPS
        text.append(f" · refreshed {seconds}s ago" if seconds else " · refreshed just now", style=MUTED)
        return text

    def effort(self) -> Text:
        """What needs effort to get the pipeline going, in three lines."""
        found = blockers(self.prs)
        text = Text()

        text.append(f"{'needs you':<12}", style=MUTED)
        yours = [b for b in found if b.yours]
        for b in yours:
            text.append("● ", style=self.breath())
            text.append(b.pr.key, style=f"bold {INK}")
            text.append(" at its prompt" if b.kind == "needs you" else " no agent", style=INK2)
            if b.behind:
                text.append(f", {', '.join(short(pr) for pr in b.behind)} behind it", style=MUTED)
            text.append("    ")
        if not yours:
            text.append("nothing waits on you", style=MUTED)
        text.append("\n")

        text.append(f"{'waits on':<12}", style=MUTED)
        by_person: dict[str, list] = {}
        for b in found:
            if not b.yours:
                by_person.setdefault(b.who, []).append(b)
        for who, held in by_person.items():
            text.append("○ ", style=MUTED)
            text.append(who, style=INK)
            text.append("  " + " · ".join(short(b.pr) for b in held), style=INK2)
            queued = [pr for b in held for pr in b.behind]
            if queued:
                text.append(f", {', '.join(short(pr) for pr in queued)} behind", style=MUTED)
            text.append("    ")
        if not by_person:
            text.append("nobody", style=MUTED)
        text.append("\n")

        text.append(f"{'people':<12}", style=MUTED)
        for name, has_work in roster(self.prs):
            if has_work or name == "you":
                text.append("▪ ", style=MUTED)
                text.append(name, style=f"underline {INK}" if name == self.person else INK2)
                text.append(" has work    " if has_work else " nothing here    ", style=MUTED)
            else:
                text.append("● ", style=self.breath())
                text.append(name, style=f"bold underline {INK}" if name == self.person else f"bold {INK}")
                text.append(" has nothing to do    ", style=INK2)
        return text

    def map(self) -> Text:
        rows = tree_rows(self.prs)
        box = self.query_one("#map", Static).size
        width = max(60, box.width or self.size.width - 2)
        height = max(3, box.height or len(rows) + 1)
        canvas = Canvas(width, height)
        for label, x in (("pipeline", X_PIPE), ("next move", X_MOVE), ("progress", X_PROG),
                         ("agent", X_AGENT), ("quiet", X_AGE - 1)):
            canvas.put(x, 0, label, MUTED)
        if not rows:
            canvas.put(X_TREE, 1, "no open pull requests or local branches", MUTED)
            return canvas.text()

        # keep the selected branch on screen when the map is taller than its box
        at = next((i for i, row in enumerate(rows) if row.pr is self.sel), 0)
        top = max(0, min(len(rows) - (height - 1), at - (height - 1) // 2))
        where = {id(row.pr): i for i, row in enumerate(rows) if row.pr is not None}
        sweep_row = None if self.sweep_at is None else self.frame - self.sweep_at

        for i, row in enumerate(rows):
            y = 1 + i - top
            if not 1 <= y < height:
                continue
            if row.kind == "anchor":
                canvas.put(X_TREE, y, row.label, f"bold {INK}")
            elif row.kind == "point":
                canvas.put(X_TREE, y, row.lead, AXIS)
                canvas.put(X_TREE + len(row.lead), y, row.label, MUTED)
            else:
                self.draw_branch(canvas, y, row.lead, row.pr)

        # the rail between a waiting branch and the branch it waits on
        for i, row in enumerate(rows):
            pr = row.pr
            here = pipe(pr) if pr is not None else None
            if here is None or here.on_pr is None or here.on_pr is not pr.parent:
                continue
            end, _ = root_cause(pr)
            moving = end is not None and end.state == "working"
            x = X_TREE + len(row.lead) - 2
            for step, row_at in enumerate(range(i, where[id(pr.parent)], -1)):
                y = 1 + row_at - top
                if not 1 <= y < height:
                    continue
                lit = moving and (step * 2 - self.frame // 2) % 8 < 2     # a current, toward the parent
                canvas.put(x, y, canvas.cells[y][x][0], INK if lit else MUTED)

        for i, row in enumerate(rows):
            y = 1 + i - top
            if not 1 <= y < height:
                continue
            pr = row.pr
            if pr is not None and self.person and self.person not in movers(pr):
                for x in range(width):
                    canvas.cells[y][x] = (canvas.cells[y][x][0], DIM)
            hit = self.frame - self.flash.get(id(pr), -999) if pr is not None else 999
            if 0 <= hit // 2 < len(FLASH):
                for x in range(width):
                    canvas.paint(x, y, 1, FLASH[hit // 2])
            elif sweep_row is not None and 0 <= sweep_row - i < 3:
                for x in range(width):
                    canvas.paint(x, y, 1, FLASH[(sweep_row - i) * 2])
            elif pr is not None and pr is self.sel:
                for x in range(width):
                    canvas.paint(x, y, 1, RAISED)
            if pr is not None and pr is self.sel:
                canvas.put(0, y, "▌", INK)
        if sweep_row is not None and sweep_row > len(rows) + 3:
            self.sweep_at = None
        return canvas.text()

    def draw_branch(self, canvas: Canvas, y: int, lead: str, pr: PR) -> None:
        canvas.put(X_TREE, y, lead, AXIS)
        x = X_TREE + len(lead)
        icon, color, _, _ = STATUS[pr.state]
        landing = id(pr) in self.leaving
        hit = self.frame - self.flash.get(id(pr), -999)
        if landing:
            icon, color = "✓", GOOD
        elif 0 <= hit < 8:
            icon, color = TURN[hit % len(TURN)], INK
        canvas.put(x, y, icon, color)
        canvas.put(x + 2, y, pr.key, f"bold {INK}")
        room = X_PIPE - 1 - (x + 2 + len(pr.key))
        slug = cut("-" + pr.slug, room)
        canvas.put(x + 2 + len(pr.key), y, slug, INK2)
        if broken(pr) and pr.state != "conflict" and len(slug) + 2 <= room:
            canvas.put(x + 2 + len(pr.key) + len(slug) + 1, y, "✗", CRIT)

        if landing:
            canvas.put(X_PIPE, y, "✓", GOOD)
            canvas.put(X_PIPE + 2, y, "landed on " + pr.root().base, INK)
            return
        here = pipe(pr)
        if here is None:                                       # a local review branch
            canvas.put(X_PIPE, y, f"review notes for {short(pr)}", MUTED)
        elif here.state == "working":
            canvas.put(X_PIPE, y, self.spin(y * 3), INK)
            canvas.put(X_PIPE + 2, y, "working", INK2)
        elif here.state in ("needs you", "no agent"):
            canvas.put(X_PIPE, y, "●", self.breath())
            canvas.put(X_PIPE + 2, y, here.waits, f"bold {INK}")
        elif here.on_pr is not None:                           # waits on a branch: show where it ends
            canvas.put(X_PIPE, y, "→", MUTED)
            canvas.put(X_PIPE + 2, y, f"waits on {short(here.on_pr)}", INK2)
            end, end_pr = root_cause(pr)
            x_end = X_PIPE + 19
            if end is None or end.state == "working":
                canvas.put(x_end, y, self.spin(y * 3), INK)
            elif end.state in ("needs you", "no agent"):
                canvas.put(x_end, y, "●", self.breath())
            else:
                canvas.put(x_end, y, "○", MUTED)
                canvas.put(x_end + 2, y, cut(end.on, X_MOVE - x_end - 3), MUTED)
        else:
            canvas.put(X_PIPE, y, "○", MUTED)
            canvas.put(X_PIPE + 2, y, cut(here.waits, X_MOVE - X_PIPE - 3), INK2)

        if here is not None:
            who = ", ".join(here.who)
            canvas.put(X_MOVE, y, who, INK)
            canvas.put(X_MOVE + len(who), y, cut(f" · {here.what}", X_PROG - X_MOVE - len(who) - 1), INK2)
            working = here.state == "working"
            word = "in progress" if working else ("stalled" if here.partly else "not started")
            canvas.put(X_PROG, y, word, INK2 if working or here.partly else MUTED)
            if here.total and (working or here.partly):
                cells = min(here.total, 5)
                filled = min(cells, round(cells * here.done / here.total))
                x_bar = X_PROG + len(word) + 1
                for k in range(cells):
                    if k < filled:
                        canvas.put(x_bar + k, y, "▰", INK2 if working else MUTED)
                    elif working and k == filled and self.frame // 5 % 2:      # the step being worked
                        canvas.put(x_bar + k, y, "▰", INK)
                    else:
                        canvas.put(x_bar + k, y, "▱", AXIS)
                canvas.put(x_bar + cells + 1, y, f"{here.done}/{here.total}", INK2)

        if not pr.mine:
            canvas.put(X_AGENT, y, cut(pr.author, X_AGE - X_AGENT - 1), MUTED)
        else:
            alive = [w for w in pr.workers if session_state(w) != "ended"]
            worker = next((w for w in alive if w.live), alive[-1] if alive else None)
            off = any(w.name for w in pr.workers)              # a session name breaks the pattern
            if worker is None:
                if pr.state != "findings":
                    canvas.put(X_AGENT, y, "no agent", MUTED)
            elif worker.live:
                canvas.put(X_AGENT, y, "≠" if off else self.spin(y * 3), CRIT if off else INK)
                canvas.put(X_AGENT + 2, y, worker.serial, INK)
            else:
                state = {"at its prompt": "prompt", "waiting": "waits"}[session_state(worker)]
                canvas.put(X_AGENT, y, "≠" if off else "▷", CRIT if off else MUTED)
                canvas.put(X_AGENT + 2, y, f"{worker.serial} {state}", INK2 if state == "prompt" else MUTED)
        age, fresh = pr.age, id(pr) in self.touched
        if fresh:                                              # just changed: count the seconds
            seconds = (self.frame - self.touched[id(pr)]) // FPS
            age = f"{seconds}s" if seconds else "now"
        canvas.put(X_AGE, y, age.rjust(4), f"bold {INK}" if fresh else MUTED)

    def drawer(self) -> Text:
        if self.person:
            return self.person_card(self.person)
        pr = self.sel
        if pr is None:
            return Text("nothing selected", style=MUTED)
        icon, color, label, _ = STATUS[pr.state]
        out = Text()
        out.append(f"{self.repo}-", style=MUTED).append(pr.name, style=f"bold {INK}")
        out.append(f"     {anchor_note(pr)}\n", style=MUTED)
        out.append(icon, style=color).append(f" {label} · {pr.title}\n", style=INK2)

        here = pipe(pr)
        out.append("pipeline ", style=MUTED)
        if here is None:
            out.append(f"review notes for {short(pr)}, kept on this Mac", style=INK2)
        elif here.state == "working":
            out.append(self.spin(), style=INK)
            out.append(f" working · {here.progress} · {', '.join(here.who)} · {here.what}", style=INK)
        else:
            mine = here.state in ("needs you", "no agent")
            out.append("● " if mine else "○ ", style=self.breath() if mine else MUTED)
            out.append(f"{here.waits} · {here.progress} · {', '.join(here.who)} · {here.what}",
                       style=f"bold {INK}" if mine else INK)
        out.append("\n")

        out.append("chain    ", style=MUTED)                   # the wait, followed to its end
        links = chain(pr)
        for k, link_pr in enumerate(links):
            out.append(link_pr.key, style=f"bold {INK}" if link_pr is pr else INK)
            if k < len(links) - 1:
                out.append(" → ", style=MUTED)
        end, end_pr = root_cause(pr)
        if end is None:
            out.append("  nothing waits here", style=MUTED)
        elif end.state == "working":
            out.append(" ", style=INK).append(self.spin(), style=INK).append(" at work", style=INK2)
        elif end.state in ("needs you", "no agent"):
            out.append(" → ", style=MUTED).append("● ", style=self.breath())
            out.append("you" if end.state == "needs you" else "an agent to be assigned", style=f"bold {INK}")
        else:
            out.append(" → ", style=MUTED).append("○ ", style=MUTED)
            out.append(f"{end.on} · {end.what} · {end.progress}", style=INK2)
        queued = self.behind(pr)
        if queued:
            out.append(f"     behind it: {', '.join(short(q) for q in queued)}", style=MUTED)
        out.append("\n")

        out.append("sessions ", style=MUTED)
        listed = [(f"{self.repo}-{pr.key}-{w.serial}", w) for w in (pr.workers if pr.mine else [])]
        if pr.state == "findings":
            listed = [(f"{self.repo}-{pr.name}", w) for w in pr.workers]
        for child in pr.children:
            if child.state == "findings":
                listed += [(f"{self.repo}-{child.name}", w) for w in child.workers]
        for name, worker in listed:
            state = session_state(worker)
            out.append(self.spin() if worker.live else "▷", style=INK if worker.live else MUTED)
            out.append(f" {name}", style=INK if state != "ended" else MUTED)
            out.append(f" working, {worker.note}    " if worker.live else f" {worker.note}    ",
                       style=INK2 if state != "ended" else MUTED)
        if not listed:
            out.append(f"{pr.author} on GitHub · no session on this Mac" if not pr.mine
                       else "no session on this Mac", style=INK2)
        out.append("\n")

        off = [w for w in pr.workers if w.name]
        out.append("names    ", style=MUTED)
        if off:
            out.append("≠ ", style=CRIT)
            out.append(f"session is named {off[0].name}, expected "
                       f"{self.repo}-{pr.key}-{off[0].serial}", style=INK)
        else:
            out.append("✓ ", style=GOOD).append("branch, session, PR and Jira agree", style=INK2)
        out.append("     checks ", style=MUTED)
        out.append(" · ".join(x for x in (pr.ci_note, pr.merge) if x) or "none yet",
                   style=INK if broken(pr) else INK2)
        return out

    def person_card(self, person: str) -> Text:
        """One person: whether they have work, and the work itself as a list. No counts."""
        has = work_of(self.prs, person)
        out = Text()
        out.append(person, style=f"bold {INK}")
        if has:
            out.append("   has work", style=INK2)
        elif person == "you":
            out.append("   nothing waits on you here", style=INK2)
        else:
            out.append("   ").append("●", style=self.breath())
            out.append(" has nothing to do · could be asked to review", style=f"bold {INK}")
        out.append("          p next person, past the last one back to the branch\n", style=MUTED)
        for pr in has[:5]:
            icon, color, _, _ = STATUS[pr.state]
            here = pipe(pr)
            out.append(f"  {icon} ", style=color)
            out.append(f"{pr.name:<42}"[:42], style=INK)
            out.append(f" {here.what:<34}"[:35] if here else " " * 35, style=INK2)
            if here and here.state == "working":
                out.append(self.spin(), style=INK).append(f" {here.progress}", style=INK2)
            elif here:
                mine = here.state in ("needs you", "no agent")
                out.append("● " if mine else "○ ", style=self.breath() if mine else MUTED)
                out.append(f"{here.waits if mine else here.progress}", style=INK if mine else INK2)
            queued = self.behind(pr)
            if queued:
                out.append(f"   {', '.join(short(q) for q in queued)} behind it", style=MUTED)
            out.append("\n")
        if len(has) > 5:
            out.append("  and more further down the map\n", style=MUTED)
        return out

    def log_text(self) -> Text:
        out = Text()
        if self.repo != "agent-frolic":
            out.append("events   ", style=MUTED)
            out.append("the made-up event feed only runs on agent-frolic", style=MUTED)
            return out
        newest = self.events[-3:][::-1]
        if not newest:
            wait = max(0, (self.next_at - self.frame) // FPS)
            out.append("events   ", style=MUTED).append(f"the first one lands in {wait}s", style=MUTED)
        for i, (at, said) in enumerate(newest):
            seconds = (self.frame - at) // FPS
            out.append(f"{'now' if not seconds else str(seconds) + 's ago':<9}",
                       style=INK if i == 0 and seconds < 2 else MUTED)
            out.append(said, style=INK if i == 0 else INK2)
            out.append("\n")
        return out

    # ---- keys --------------------------------------------------------------
    def action_move(self, step: int) -> None:
        branches = self.branches()
        if not branches:
            return
        at = branches.index(self.sel) if self.sel in branches else 0
        self.sel = branches[max(0, min(len(branches) - 1, at + step))]
        self.person = None
        self.draw()

    def action_person(self) -> None:
        names = [name for name, _ in roster(self.prs)]
        if self.person is None:
            self.person = names[0] if names else None
        else:
            at = names.index(self.person) if self.person in names else len(names) - 1
            self.person = names[at + 1] if at + 1 < len(names) else None
        self.draw()

    def action_sweep(self) -> None:
        self.sweep_at = self.refreshed_at = self.frame
        self.draw()

    def action_drawer(self) -> None:
        self.drawer_open = not self.drawer_open
        self.query_one("#drawer", Static).styles.animate(
            "height", value=8 if self.drawer_open else 0, duration=0.35)

    def action_pause(self) -> None:
        self.paused = not self.paused
        self.draw()

    def action_repo(self, step: int) -> None:
        names = list(REPOS)
        self.load(names[(names.index(self.repo) + step) % len(names)])
        self.draw()


if __name__ == "__main__":
    LiveV2().run()
