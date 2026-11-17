"""Agent Frolic mock-up, option 3, v3: live. If it moves, work is moving. If it is blue, it
is still, and it is yours to get going.

Only one thing moves all the time: the dots that turn before "working", wherever an agent
is at work (on its row, at the end of a wait that is moving, and in the top line). The tree
itself is drawn once and stays put: bases, the commits that join them (by short hash), and
the connectors never animate. Blue no longer breathes; it marks what is idle and yours to
fix, and holds still.

Everything else moves only when a made-up event lands, and then settles:
- the row it touched washes pale for a second (the data columns only, never the tree);
- a progress meter steps to its new count, the new cells bright for a moment;
- the needs-effort list re-orders itself. It is kept most urgent first (is an agent waiting
  for this to continue, then how long has it been quiet), so a row slides up or down when
  an event changes that. A thing that gets going again says so and leaves the list.

The list sits at the bottom left, beside the drawer for the selected branch. The line under
the repos says who has work and who has nothing to do, with no counts.

    .venv/bin/python mockup/option3_live_v3.py

Keys: up/down pick a branch, p steps through the people (their work as a list, the rest of
the map greyed), e fires the next event now, r sweeps a refresh, d folds the drawer, space
pauses all motion, tab goes to the next repo, q quits.
"""
from __future__ import annotations

import copy

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Static

from frolic_v2 import push, rv                 # the two event builders frolic_v3 does not pass on
from frolic_v3 import (ACCENT, AXIS, CRIT, GOOD, INK, INK2, MUTED, PLANE, PR, RAISED, REPOS, SPIN,
                       STATUS, SURFACE, Canvas, Effort, Worker, anchor_note, broken, chain, flat,
                       free_people, link, movers, needs_effort, pipe, root_cause, roster,
                       session_state, sha, short, top_line, tree_rows, work_of)

FPS = 12
EVERY = 4.0                      # seconds between events
FLASH = ["#4a4a46", "#444441", "#3d3d3a", "#363633", "#2f2f2d", "#292927"]   # a wash, fading
DIM = "#5c5b56"                  # rows set aside while you look at one person
X_TREE, X_PIPE, X_MOVE, X_PROG, X_AGENT, X_AGE = 2, 50, 76, 111, 133, 144
LANDING = 14                     # frames a merged branch stays on screen before it goes
GLOW = 16                        # frames a stepped meter stays bright
LEAVE = 16                       # frames a resolved item stays in the list, saying why
SLIDE = 0.17                     # rows a list item moves per frame on its way to its place
BAND = 11                        # rows of the bottom band: the list and the drawer


def cut(text: str, width: int) -> str:
    return text if len(text) <= width else text[:max(0, width - 1)] + "…"


def wash(canvas: Canvas, y: int, x0: int, x1: int, color: str) -> None:
    """A background behind part of a row, replacing any background already there."""
    if not 0 <= y < canvas.height:
        return
    for x in range(max(0, x0), min(canvas.width, x1)):
        ch, style = canvas.cells[y][x]
        canvas.cells[y][x] = (ch, f"{style.split(' on ')[0]} on {color}".strip())


# ---- the made-up events: each changes the data and says what happened --------------------

def ev_prompt(app: "LiveV3") -> tuple[list[PR], str]:
    pr = app.find(13)
    worker = pr.workers[0]
    worker.live, worker.mode, worker.note = False, "prompt", "waiting at its prompt"
    app.step_meter(pr, (2, 3))
    pr.next = ("you", "decide on the last thread")
    return [pr], "session 01 on pr-0013 replied to 2 of 3 threads, then stopped at its prompt for you"


def ev_resume(app: "LiveV3") -> tuple[list[PR], str]:
    pr = app.find(13)
    worker = pr.workers[0]
    worker.live, worker.mode, worker.note = True, "", "replying to the last thread"
    pr.next = ("you", "reply to the last thread")
    return [pr], "you answered · session 01 on pr-0013 is working again"


def ev_reviewer(app: "LiveV3") -> tuple[list[PR], str]:
    pr = app.find(15)
    app.step_meter(pr, (4, 6))
    return [pr], "mira read two more files on pr-0015 · 4 of 6, still hers to finish"


def ev_assign(app: "LiveV3") -> tuple[list[PR], str]:
    pr = app.find(20)
    pr.workers.append(Worker("02", True, "reading keychain.py"))
    return [pr], "an agent was assigned to pr-0020 · session 02 is at work"


def ev_ask(app: "LiveV3") -> tuple[list[PR], str]:
    pr = app.find(24)
    pr.next = ("mira, kenji", "review")
    pr.history.append(rv(1, "kenji", "requested", "now"))
    return [pr], "kenji was asked to review pr-0024 · kenji now has work"


def ev_handoff(app: "LiveV3") -> tuple[list[PR], str]:
    pr = app.find(13)
    worker = pr.workers[0]
    worker.live, worker.mode, worker.note = False, "waiting", "idle until mira looks again"
    pr.state, pr.next = "rereview", ("mira", "re-review")
    pr.progress, pr.unit, pr.started, pr.threads = (0, 0), "", False, (0, 7)
    pr.history += [push("session 01", 2, "now"), rv(2, "mira", "rerequested", "now")]
    return [pr], "session 01 answered the last thread on pr-0013 · it now waits on mira to look again"


def ev_release(app: "LiveV3") -> tuple[list[PR], str]:
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


def ev_approved(app: "LiveV3") -> tuple[list[PR], str]:
    pr = app.find(13)
    pr.state, pr.next, pr.waits_on = "approved", ("you", "merge after pr-0012"), 12
    pr.history.append(rv(2, "mira", "approved", "now"))
    pr.workers[0].note = "idle until pr-0012 lands"
    return [pr], "mira approved pr-0013 · it cannot merge before pr-0012, so an agent now waits on that"


def ev_land(app: "LiveV3") -> tuple[list[PR], str]:
    pr, above = app.find(12), app.find(13)
    app.leaving[id(pr)] = app.frame
    pr.next = ("", "")                            # merged: it holds no move any more
    above.state, above.next, above.waits_on = "ready", ("you", "merge"), 0
    worker = above.workers[0]
    worker.live, worker.mode, worker.note = True, "", "merging"
    return [pr, above], "you merged pr-0012 · pr-0013 hangs from main now, and session 01 is merging it"


SCRIPT = [ev_prompt, ev_resume, ev_reviewer, ev_assign, ev_ask, ev_release, ev_handoff, ev_approved,
          ev_land]


class LiveV3(App):
    CSS = f"""
    Screen {{ background: {PLANE}; color: {INK}; }}
    #tabs {{ height: 1; margin: 1 2 0 2; text-wrap: nowrap; text-overflow: ellipsis; }}
    #people {{ height: 1; margin: 1 2 0 2; text-wrap: nowrap; text-overflow: ellipsis; }}
    #map {{ height: 1fr; margin: 1 1 0 1; }}
    #band {{ height: {BAND}; margin: 1 1 0 1; }}
    .pane {{ height: {BAND}; background: {SURFACE}; padding: 1 2; margin: 0 1;
             text-wrap: nowrap; text-overflow: ellipsis; overflow: hidden; }}
    #effort {{ width: 1fr; }}
    #drawer {{ width: 64; }}
    #events {{ height: 2; margin: 0 2; text-wrap: nowrap; text-overflow: ellipsis; }}
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
        self.drawer_w = 64
        self.load(next(iter(REPOS)))

    def compose(self) -> ComposeResult:
        yield Static(id="tabs")
        yield Static(id="people")
        yield Static(id="map")
        with Horizontal(id="band"):
            yield Static(id="effort", classes="pane")
            yield Static(id="drawer", classes="pane")
        yield Static(id="events")
        yield Static("↑↓ branch    p person    e next event now    r refresh    d drawer    "
                     "space pause    ⇥ next repo    q quit", id="hints")

    def on_mount(self) -> None:
        self.fit()
        self.set_interval(1 / FPS, self.tick)
        self.draw()

    def on_resize(self) -> None:
        self.fit()
        self.draw()

    def fit(self) -> None:
        """The drawer gives way to the list in a narrow window."""
        self.drawer_w = 64 if self.size.width >= 140 else max(30, self.size.width * 2 // 5)
        if self.drawer_open:
            self.query_one("#drawer", Static).styles.width = self.drawer_w

    # ---- state -------------------------------------------------------------
    def load(self, repo: str) -> None:
        """A fresh copy of one repo's data, so events can change it and a replay can reset it."""
        self.repo = repo
        self.prs = link(copy.deepcopy(REPOS[repo]))
        branches = self.branches()
        self.sel: PR | None = branches[0] if branches else None
        self.person: str | None = None
        self.flash: dict[int, int] = {}           # branch -> frame an event hit it
        self.touched: set[int] = set()            # branches an event has changed: quiet "now"
        self.stepped: dict[int, tuple[int, int]] = {}     # branch -> (frame, count before the step)
        self.leaving: dict[int, int] = {}         # branch -> frame it was merged
        self.events: list[tuple[int, str]] = []
        self.step = 0
        self.next_at = self.frame + int(EVERY * FPS)
        self.sweep_at: int | None = None
        self.refreshed_at = self.frame
        # the needs-effort list: where each item is drawn, what it said, what has just left
        self.efforts: dict[int, Effort] = {}
        self.order: list[int] = []
        self.slots: dict[int, float] = {}
        self.said: dict[int, str] = {}
        self.gone: dict[int, tuple[int, int, str]] = {}   # item -> (frame, row it held, why)
        self.entered: dict[int, int] = {}
        self.sync_effort(settle=True)

    def find(self, number: int) -> PR:
        return next(pr for pr in flat(self.prs) if pr.number == number and pr.state != "findings")

    def branches(self) -> list[PR]:
        return [row.pr for row in tree_rows(self.repo, self.prs) if row.pr is not None]

    def behind(self, pr: PR) -> list[PR]:
        """The branches that wait on this one, directly or through a chain."""
        return [other for other in flat(self.prs) if other is not pr and pr in chain(other)]

    def step_meter(self, pr: PR, progress: tuple[int, int]) -> None:
        """Move a count on, remembering where it was so the meter can be seen to step."""
        self.stepped[id(pr)] = (self.frame, pr.progress[0])
        pr.progress = progress

    def sync_effort(self, settle: bool = False) -> None:
        """Bring the list up to date with the data and move each item one step toward its
        place. With settle, everything jumps to its place (a fresh load)."""
        found = needs_effort(self.prs)
        keys = [id(e.pr) for e in found]
        before = set(self.efforts)
        self.efforts = dict(zip(keys, found))
        for key in before - set(keys):                         # it stopped holding things up
            if key in self.gone:
                continue
            pr = next((p for p in flat(self.prs) if id(p) == key), None)
            here = pipe(pr) if pr is not None else None
            why = ("landed" if pr is None or key in self.leaving or here is None
                   else "moving again" if here.state == "working"
                   else f"now waits on {short(here.on_pr)}" if here.on_pr is not None
                   else "no longer holds anything up")
            self.gone[key] = (self.frame, round(self.slots.get(key, 0)), why)
        for key in keys:
            self.gone.pop(key, None)
            self.said[key] = short(self.efforts[key].pr)
            if key not in before and not settle:
                self.entered[key] = self.frame
        for key, (at, _, _) in list(self.gone.items()):
            if self.frame - at > LEAVE or settle:
                del self.gone[key]
                self.slots.pop(key, None)
        order = list(keys)
        for key, (_, row, _) in sorted(self.gone.items(), key=lambda item: item[1][1]):
            order.insert(min(row, len(order)), key)            # it keeps its row until it leaves
        self.order = order
        for i, key in enumerate(order):
            at = self.slots.get(key, float(i))
            self.slots[key] = float(i) if settle else at + max(-SLIDE, min(SLIDE, i - at))

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
        self.sync_effort()
        self.draw()

    def remove(self, pr: PR) -> None:
        """Take a merged branch out of the map; what sat on it now hangs where it did."""
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
            pr.age = "now"
            self.flash[id(pr)] = self.frame
            self.touched.add(id(pr))
        self.events.append((self.frame, said))

    # ---- drawing -----------------------------------------------------------
    def draw(self) -> None:
        seconds = (self.frame - self.refreshed_at) // FPS
        self.query_one("#tabs", Static).update(top_line(
            self.repo, data={self.repo: self.prs}, frame=self.frame, paused=self.paused,
            refreshed=f"{seconds}s ago" if seconds else "just now"))
        self.query_one("#people", Static).update(self.people())
        self.query_one("#map", Static).update(self.map())
        self.query_one("#effort", Static).update(self.effort())
        self.query_one("#drawer", Static).update(self.drawer())
        self.query_one("#events", Static).update(self.log_text())

    def spin(self, offset: int = 0) -> str:
        """The turning dots: an agent is at work."""
        return SPIN[(self.frame + offset) % len(SPIN)]

    def people(self) -> Text:
        """Who has work and who has nothing to do. No counts."""
        def name_style(name: str, base: str) -> str:
            return f"underline {base}" if name == self.person else base

        text = Text("have work  ", style=MUTED)
        busy = [name for name, has_work in roster(self.prs) if has_work]
        for k, name in enumerate(busy):
            text.append(name, style=name_style(name, INK2))
            text.append(" · " if k < len(busy) - 1 else "", style=MUTED)
        if not busy:
            text.append("nobody", style=MUTED)
        free = free_people(self.prs)
        if free:
            text.append("      ● ", style=ACCENT)
            text.append("nothing to do  ", style=INK)
            for k, name in enumerate(free):
                text.append(name, style=name_style(name, f"bold {INK}"))
                text.append(" · " if k < len(free) - 1 else "", style=MUTED)
        return text

    def map(self) -> Text:
        rows = tree_rows(self.repo, self.prs)
        box = self.query_one("#map", Static).size
        width = max(60, box.width or self.size.width - 2)
        height = max(3, box.height or len(rows) + 1)
        canvas = Canvas(width, height)
        for label, x in (("pipeline", X_PIPE), ("next move", X_MOVE), ("progress", X_PROG),
                         ("agent", X_AGENT), ("quiet", X_AGE - 1)):
            canvas.put(x, 0, label, MUTED)

        # keep the selected branch on screen when the map is taller than its box
        at = next((i for i, row in enumerate(rows) if row.pr is not None and row.pr is self.sel), 0)
        top = max(0, min(len(rows) - (height - 1), at - (height - 1) // 2))
        sweep_row = None if self.sweep_at is None else self.frame - self.sweep_at

        for i, row in enumerate(rows):
            y = 1 + i - top
            if not 1 <= y < height:
                continue
            pr = row.pr
            canvas.put(X_TREE, y, row.lead, AXIS)              # the tree: drawn, never animated
            x = X_TREE + len(row.lead)
            if row.kind == "base":
                canvas.put(x, y, row.label, f"bold {INK}")
                canvas.put(x + len(row.label) + 2, y, row.note, MUTED)
            elif row.kind == "commit":
                canvas.put(x, y, row.sha, INK2)
                canvas.put(x + len(row.sha) + 1, y, row.label, MUTED)
            else:
                self.draw_branch(canvas, y, x, pr)
                if self.person and self.person not in movers(pr):
                    for cx in range(width):
                        canvas.cells[y][cx] = (canvas.cells[y][cx][0], DIM)

            chosen = pr is not None and pr is self.sel
            if chosen:
                wash(canvas, y, 0, width, RAISED)
                canvas.put(0, y, "▌", f"{INK} on {RAISED}")
            hit = self.frame - self.flash.get(id(pr), -999) if pr is not None else 999
            if 0 <= hit // 2 < len(FLASH):                     # an event landed here: it settles
                wash(canvas, y, X_PIPE - 1, width, FLASH[hit // 2])
            elif sweep_row is not None and 0 <= sweep_row - i < 3 and pr is not None:
                wash(canvas, y, X_PIPE - 1, width, FLASH[(sweep_row - i) * 2])
        if not any(row.pr is not None for row in rows):
            canvas.put(X_TREE, min(height - 1, len(rows) + 2), "no open pull requests or local branches", MUTED)
        if sweep_row is not None and sweep_row > len(rows) + 3:
            self.sweep_at = None
        return canvas.text()

    def draw_branch(self, canvas: Canvas, y: int, x: int, pr: PR) -> None:
        icon, color, _, _ = STATUS[pr.state]
        landing = id(pr) in self.leaving
        if landing:
            icon, color = "✓", GOOD
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
            canvas.put(X_PIPE, y, self.spin(y * 3), INK)       # the dots: an agent is at work
            canvas.put(X_PIPE + 2, y, "working", INK2)
        elif here.state in ("needs you", "no agent"):
            canvas.put(X_PIPE, y, "●", ACCENT)                 # still, and yours to get going
            canvas.put(X_PIPE + 2, y, here.waits, f"bold {INK}")
        elif here.on_pr is not None:                           # waits on a branch: show where it ends
            canvas.put(X_PIPE, y, "→", MUTED)
            canvas.put(X_PIPE + 2, y, f"waits on {short(here.on_pr)}", INK2)
            end, _ = root_cause(pr)
            x_end = X_PIPE + 19
            if end is None or end.state == "working":
                canvas.put(x_end, y, self.spin(y * 3), INK)
            elif end.state in ("needs you", "no agent"):
                canvas.put(x_end, y, "●", ACCENT)
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
                self.draw_meter(canvas, X_PROG + len(word) + 1, y, pr, here.done, here.total, working)

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
                canvas.put(X_AGENT, y, "≠" if off else "▶", CRIT if off else INK)
                canvas.put(X_AGENT + 1, y, worker.serial, INK)
            else:
                state = {"at its prompt": "prompt", "waiting": "waits"}[session_state(worker)]
                canvas.put(X_AGENT, y, "≠" if off else "▷", CRIT if off else MUTED)
                canvas.put(X_AGENT + 1, y, f"{worker.serial} {state}", INK2 if state == "prompt" else MUTED)
        fresh = id(pr) in self.touched
        canvas.put(X_AGE, y, pr.age.rjust(4), f"bold {INK}" if fresh else MUTED)

    def draw_meter(self, canvas: Canvas, x: int, y: int, pr: PR, done: int, total: int,
                   working: bool) -> None:
        """A small meter and its count. When an event moves the count on, the new cells light
        one after another, bright, and then settle."""
        cells = min(total, 5)
        filled = min(cells, round(cells * done / total))
        was_at, was = self.stepped.get(id(pr), (-999, done))
        since = self.frame - was_at
        before = min(cells, round(cells * was / total))
        for k in range(cells):
            new = k >= before and since < GLOW
            if k >= filled or (new and since < 3 * (k - before)):
                canvas.put(x + k, y, "▱", AXIS)
            elif new:
                canvas.put(x + k, y, "▰", f"bold {INK}")
            else:
                canvas.put(x + k, y, "▰", INK2 if working else MUTED)
        canvas.put(x + cells + 1, y, f"{done}/{total}", f"bold {INK}" if since < GLOW else INK2)

    def effort(self) -> Text:
        """What needs effort to get the pipeline going, most urgent first."""
        box = self.query_one("#effort", Static).content_size
        width = min(104, max(30, box.width or 76))             # with the drawer folded, stay readable
        height = max(3, box.height or BAND - 2)
        canvas = Canvas(width, height)
        canvas.put(0, 0, "needs effort, most urgent first", MUTED)
        key_text = "● yours to fix   ○ waits on a person"
        if width >= 34 + len(key_text) + 2:
            canvas.put(width - len(key_text), 0, "●", ACCENT)
            canvas.put(width - len(key_text) + 2, 0, "yours to fix", MUTED)
            canvas.put(width - 19, 0, "○ waits on a person", MUTED)
        if not self.order:
            canvas.put(0, 1, "Everything that can move is moving." if flat(self.prs) else "Nothing open.", MUTED)
            return canvas.text()

        room = height - 1
        shown = room if len(self.order) <= room else room - 1
        _, cause = root_cause(self.sel) if self.sel is not None else (None, None)
        ranked = sorted(self.order, key=lambda k: self.slots.get(k, 0))     # stable: ties keep order
        for y, key in enumerate(ranked[:shown], start=1):
            if key in self.gone:                               # it got going: say so, then leave
                _, _, why = self.gone[key]
                if why == "landed":
                    canvas.put(0, y, "✓", GOOD)
                elif why == "moving again":
                    canvas.put(0, y, self.spin(), INK)         # the dots: it is at work again
                else:
                    canvas.put(0, y, "→", MUTED)
                canvas.put(2, y, self.said.get(key, ""), INK2)
                canvas.put(11, y, why, INK2)
                continue
            e = self.efforts[key]
            canvas.put(0, y, "●" if e.yours else "○", ACCENT if e.yours else MUTED)
            canvas.put(2, y, short(e.pr), f"bold {INK}")
            urgency = e.urgency
            x_urgency = width - len(urgency)
            if e.kind == "needs you":
                what = f"at its prompt · {e.pipe.what}"
            elif e.kind == "no agent":
                what = f"no agent · {e.pipe.what}"
            else:
                state = (f"stalled {e.pipe.done}/{e.pipe.total}" if e.pipe.partly and e.pipe.total
                         else "stalled" if e.pipe.partly else "not started")
                what = f"{e.who} · {e.pipe.what} · {state}"
            if e.behind:
                what += f" · {', '.join(short(pr) for pr in e.behind)} behind it"
            canvas.put(11, y, cut(what, x_urgency - 13), INK if e.yours else INK2)
            if e.waiting:
                held, rest = urgency.split(" · ")
                canvas.put(x_urgency, y, held, INK)
                canvas.put(x_urgency + len(held), y, " · " + rest, MUTED)
            else:
                canvas.put(x_urgency, y, urgency, MUTED)
            since = self.frame - self.entered.get(key, -999)
            if 0 <= since // 2 < len(FLASH):                   # new on the list: it settles in
                wash(canvas, y, 0, width, FLASH[since // 2])
            elif cause is not None and cause is e.pr:          # what holds the chosen branch up
                wash(canvas, y, 0, width, RAISED)
        if len(self.order) > shown:
            canvas.put(2, height - 1, f"and {len(self.order) - shown} more", MUTED)
        return canvas.text()

    def drawer(self) -> Text:
        if self.person:
            return self.person_card(self.person)
        pr = self.sel
        if pr is None:
            return Text("nothing selected", style=MUTED)
        icon, color, label, _ = STATUS[pr.state]
        root = pr.root()
        out = Text()
        out.append(f"{self.repo}-", style=MUTED).append(pr.name + "\n", style=f"bold {INK}")
        out.append(icon, style=color).append(f" {label} · {pr.title}\n", style=INK2)
        out.append("hangs    ", style=MUTED)
        if pr.parent is not None:
            out.append(f"on {short(pr.parent)}, its stack ", style=INK2)
        out.append(f"{anchor_note(pr)} · ", style=INK2)
        out.append(sha(self.repo, root.base, root.behind) + "\n", style=INK)

        here = pipe(pr)
        out.append("pipeline ", style=MUTED)
        if here is None:
            out.append(f"review notes for {short(pr)}, kept on this Mac\n", style=INK2)
        elif here.state == "working":
            out.append(self.spin(), style=INK)
            out.append(f" working · {here.progress}\n", style=INK)
        else:
            mine = here.state in ("needs you", "no agent")
            out.append("● " if mine else "○ ", style=ACCENT if mine else MUTED)
            out.append(f"{here.waits} · {here.progress}\n", style=f"bold {INK}" if mine else INK)
        if here is not None:
            out.append("move     ", style=MUTED)
            out.append(f"{', '.join(here.who)} · {here.what}\n", style=INK2)

        out.append("chain    ", style=MUTED)                   # the wait, followed to its end
        links = chain(pr)
        for k, link_pr in enumerate(links):
            out.append(short(link_pr), style=f"bold {INK}" if link_pr is pr else INK)
            if k < len(links) - 1:
                out.append(" → ", style=MUTED)
        end, _ = root_cause(pr)
        if end is None:
            out.append("  nothing waits here", style=MUTED)
        elif end.state == "working":
            out.append(" ").append(self.spin(), style=INK).append(" at work", style=INK2)
        elif end.state in ("needs you", "no agent"):
            out.append(" → ", style=MUTED).append("● ", style=ACCENT)
            out.append("you" if end.state == "needs you" else "an agent to be assigned", style=f"bold {INK}")
        else:
            out.append(" → ", style=MUTED).append("○ ", style=MUTED)
            out.append(f"{end.on} · {end.what} · {end.progress}", style=INK2)
        out.append("\n")
        queued = self.behind(pr)
        if queued:
            out.append("behind   ", style=MUTED)
            out.append(f"{', '.join(short(q) for q in queued)} wait{'s' if len(queued) == 1 else ''} "
                       "behind it\n", style=INK2)

        out.append("session  ", style=MUTED)
        listed = list(pr.workers) if pr.mine else []
        for child in pr.children:
            if child.state == "findings":
                listed += child.workers
        alive = [w for w in listed if session_state(w) != "ended"] or listed[-1:]
        for worker in alive[-2:]:
            out.append("▶" if worker.live else "▷", style=INK if worker.live else MUTED)
            out.append(f" {worker.serial} {worker.note}   ",
                       style=INK2 if session_state(worker) != "ended" else MUTED)
        if not listed:
            out.append(f"{pr.author} on GitHub · no session on this Mac" if not pr.mine
                       else "no session on this Mac", style=INK2)
        out.append("\n")

        off = [w for w in pr.workers if w.name]
        out.append("names    ", style=MUTED)
        if off:
            out.append("≠ ", style=CRIT).append(f"session is named {off[0].name}", style=INK)
        else:
            out.append("✓ ", style=GOOD).append("agree", style=INK2)
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
            out.append("   ").append("●", style=ACCENT)
            out.append(" has nothing to do · could be asked to review", style=f"bold {INK}")
        out.append("\n")
        out.append("p next person, past the last one back to the branch\n", style=MUTED)
        for pr in has[:6]:
            icon, color, _, _ = STATUS[pr.state]
            here = pipe(pr)
            out.append(f"{icon} ", style=color)
            out.append(f"{short(pr)} ", style=f"bold {INK}")
            out.append(f"{cut(here.what, 24):<25}" if here else " " * 25, style=INK2)
            if here and here.state == "working":
                out.append(self.spin(), style=INK).append(f" {here.progress}", style=INK2)
            elif here:
                mine = here.state in ("needs you", "no agent")
                out.append("● " if mine else "○ ", style=ACCENT if mine else MUTED)
                out.append(here.waits if mine else here.progress, style=INK if mine else INK2)
            out.append("\n")
        if len(has) > 6:
            out.append("and more further down the map\n", style=MUTED)
        return out

    def log_text(self) -> Text:
        out = Text()
        if self.repo != "agent-frolic":
            out.append("events   ", style=MUTED)
            out.append("the made-up event feed only runs on agent-frolic", style=MUTED)
            return out
        newest = self.events[-2:][::-1]
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
        """Fold the drawer away; the list takes its room."""
        self.drawer_open = not self.drawer_open
        drawer = self.query_one("#drawer", Static)
        if self.drawer_open:
            drawer.styles.display = "block"
            drawer.styles.animate("width", value=float(self.drawer_w), duration=0.3)
        else:
            drawer.styles.animate("width", value=0.0, duration=0.3,
                                  on_complete=lambda: setattr(drawer.styles, "display", "none"))

    def action_pause(self) -> None:
        self.paused = not self.paused
        self.draw()

    def action_repo(self, step: int) -> None:
        names = list(REPOS)
        self.load(names[(names.index(self.repo) + step) % len(names)])
        self.draw()


if __name__ == "__main__":
    LiveV3().run()
