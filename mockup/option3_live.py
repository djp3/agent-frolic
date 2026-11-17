"""Agent Frolic mock-up, option 3: live. The stack tree again, but motion carries meaning:
a spinner where one of your sessions is at work, a pulse on every move that is yours, a
current running along the rails toward main, a scrolling activity line per branch, and made-up
events that arrive every few seconds so you can see how a change lands (the row flashes, its
icon turns over, the headline numbers roll, the log scrolls).

    .venv/bin/python mockup/option3_live.py

Keys: up/down pick a branch, e fires the next event now, r sweeps a refresh, d folds the
detail drawer, space pauses all motion, tab goes to the next repo, q quits.
"""
from __future__ import annotations

import copy
import math
import random

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Static

from frolic_common import (ACCENT, AXIS, CRIT, GOOD, INK, INK2, ME, MUTED, PLANE, PR, RAISED,
                           REPOS, STATUS, SURFACE, WARN, WORD, Canvas, age_hours, broken, flat,
                           link, movers, rv, verdicts)

FPS = 12
SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
TURN = "◐◓◑◒"                                    # an icon turning over as its state changes
BREATH = ["#1c5cab", "#256abf", "#2a78d6", "#3987e5", "#5598e7", "#6da7ec"]   # one hue, stepped
FLASH = ["#4a4a46", "#444441", "#3d3d3a", "#363633", "#2f2f2d", "#292927"]
BARS = "▁▂▃▄▅▆▇█"
X_TREE, X_SPARK, X_REVIEW, X_CHECKS, X_NEXT, X_WHO, X_AGE = 2, 50, 64, 73, 88, 119, 135


def tree_rows(prs: list[PR]):
    def visit(pr: PR, lead: str, last: bool):
        yield lead + ("└─ " if last else "├─ "), pr
        for i, child in enumerate(pr.children):
            yield from visit(child, lead + ("   " if last else "│  "), i == len(pr.children) - 1)
    for i, pr in enumerate(prs):
        yield from visit(pr, "", i == len(prs) - 1)


# ---- the made-up events: each changes the data and says what happened -------------------

def ev_checks(app: "Live") -> tuple[PR, str]:
    pr = app.find(17)
    pr.ci, pr.ci_note = "pass", "12 of 12"
    return pr, "checks finished on pr-0017 · 12 of 12 passed"


def ev_approved(app: "Live") -> tuple[PR, str]:
    pr = app.find(15)
    pr.state, pr.next = "approved", ("you", "land pr-0013 first")
    pr.history.append(rv(2, "mira", "approved", "now"))
    return pr, "mira approved pr-0015"


def ev_thread(app: "Live") -> tuple[PR, str]:
    pr = app.find(13)
    pr.threads, pr.next = (2, 7), ("you", "answer 2 threads")
    pr.thread_note = "1 waits for your reply · 1 waits for mira to resolve"
    return pr, "mira resolved a thread on pr-0013 · 2 still open"


def ev_session(app: "Live") -> tuple[PR, str]:
    pr = app.find(20)
    pr.workers[0].live, pr.workers[0].note = True, "reading keychain.py"
    return pr, "session 01 picked up pr-0020"


def ev_rebase(app: "Live") -> tuple[PR, str]:
    pr = app.find(16)
    pr.state, pr.merge, pr.next = "changes", "clean", ("tobias", "answer 2 threads")
    pr.ci, pr.ci_note = "run", "0 of 12 so far"
    return pr, "tobias rebased pr-0016 · the conflict is gone"


def ev_opened(app: "Live") -> tuple[PR, str]:
    pr = PR("af-48", 23, "event-log", "review", author="kenji",
            title="A log of what changed since you last looked",
            history=[rv(1, ME, "requested", "now")], ci="run", ci_note="2 of 12 so far",
            next=("you", "review"), age="now")
    app.prs.append(pr)
    link(app.prs)
    return pr, "kenji opened pr-0023 and asked you to review"


SCRIPT = [ev_checks, ev_approved, ev_thread, ev_session, ev_rebase, ev_opened]
EVERY = 4.5                      # seconds between events


class Live(App):
    CSS = f"""
    Screen {{ background: {PLANE}; color: {INK}; }}
    #tabs {{ height: 1; margin: 1 2 0 2; }}
    #heads {{ height: 2; margin: 1 2; }}
    #map {{ height: 1fr; margin: 0 1; }}
    #drawer {{ height: 9; background: {SURFACE}; padding: 1 2; margin: 0 2;
               text-wrap: nowrap; text-overflow: ellipsis; overflow: hidden; }}
    #events {{ height: 5; margin: 1 2 0 2; text-wrap: nowrap; text-overflow: ellipsis; }}
    #hints {{ height: 1; margin: 0 2; color: {MUTED}; }}
    """
    BINDINGS = [
        Binding("up", "move(-1)", show=False), Binding("down", "move(1)", show=False),
        Binding("e", "fire", show=False), Binding("r", "sweep", show=False),
        Binding("d", "drawer", show=False), Binding("space", "pause", show=False),
        Binding("tab", "repo(1)", show=False, priority=True),
        Binding("shift+tab", "repo(-1)", show=False, priority=True),
        Binding("q", "quit", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.frame = 0
        self.paused = False
        self.drawer_open = True
        self.counts: dict[str, int] = {}
        self.load(next(iter(REPOS)))

    def compose(self) -> ComposeResult:
        yield Static(id="tabs")
        yield Static(id="heads")
        yield Static(id="map")
        yield Static(id="drawer")
        yield Static(id="events")
        yield Static("↑↓ branch    e next event now    r refresh    d drawer    space pause    "
                     "⇥ next repo    q quit", id="hints")

    def on_mount(self) -> None:
        self.set_interval(1 / FPS, self.tick)
        self.draw()

    # ---- state -------------------------------------------------------------
    def load(self, repo: str) -> None:
        """A fresh copy of one repo's data, so events can change it and a replay can reset it."""
        self.repo = repo
        self.prs = link(copy.deepcopy(REPOS[repo]))
        self.sel = 0
        self.born: dict[int, int] = {}            # row -> frame it starts drawing itself in
        self.flash: dict[int, int] = {}           # row -> frame an event hit it
        self.touched: dict[int, int] = {}         # row -> frame of its last event, for "12s"
        self.ci_since: dict[int, int] = {}
        self.spark: dict[int, list[int]] = {}
        self.events: list[tuple[int, str]] = []
        self.step = 0
        self.next_at = self.frame + int(EVERY * FPS)
        self.sweep_at: int | None = None
        self.refreshed_at = self.frame
        self.drawer_at = self.frame
        for i, pr in enumerate(flat(self.prs)):
            self.born[id(pr)] = self.frame + 2 + i * 2          # rows cascade in, top first
            rng = random.Random(pr.number * 7 + len(pr.slug))
            line = [rng.choice((0, 0, 1, 1, 2, 3, 5)) for _ in range(12)]
            quiet = min(12, int(age_hours(pr.age) // 2))        # nothing since it went quiet
            self.spark[id(pr)] = line[:12 - quiet] + [0] * quiet

    def find(self, number: int) -> PR:
        return next(pr for pr in flat(self.prs) if pr.number == number and pr.state != "findings")

    def rows(self) -> list[tuple[str, PR]]:
        return list(tree_rows(self.prs))

    def live(self, pr: PR) -> bool:
        return pr.mine and any(w.live for w in pr.workers)

    def targets(self) -> dict[str, int]:
        everything = flat(self.prs)
        return {
            "yours": sum("you" in movers(pr) for pr in everything),
            "theirs": sum(bool(movers(pr)) and "you" not in movers(pr) for pr in everything),
            "ready": sum(pr.state == "ready" for pr in everything),
            "broken": sum(broken(pr) for pr in everything),
            "sessions": sum(self.live(pr) for pr in everything),
        }

    # ---- the clock ---------------------------------------------------------
    def tick(self) -> None:
        if self.paused:
            return
        self.frame += 1
        if self.repo == "agent-frolic" and self.frame >= self.next_at:
            self.action_fire()
        if self.frame % 9 == 0:                   # the activity lines move on
            for pr in flat(self.prs):
                if self.live(pr):
                    line = self.spark[id(pr)]
                    line.append(random.choice((1, 1, 2, 3, 4, 6, 7)))
                    del line[0]
        for key, target in self.targets().items():      # headline numbers roll, one a frame
            shown = self.counts.get(key, 0)
            if shown != target and self.frame % 2 == 0:
                self.counts[key] = shown + (1 if target > shown else -1)
        self.draw()

    def action_fire(self) -> None:
        if self.repo != "agent-frolic":
            return
        self.next_at = self.frame + int(EVERY * FPS)
        if self.step >= len(SCRIPT):
            self.load(self.repo)
            self.events.append((self.frame, "replaying the made-up events from the start"))
            return
        pr, said = SCRIPT[self.step](self)
        self.step += 1
        pr.age = "now"
        self.flash[id(pr)] = self.touched[id(pr)] = self.frame
        self.born.setdefault(id(pr), self.frame)
        self.spark.setdefault(id(pr), [0] * 12)
        self.spark[id(pr)][-1] = 7
        self.events.append((self.frame, said))
        if self.rows() and self.rows()[self.sel][1] is pr:
            self.drawer_at = self.frame

    # ---- drawing -----------------------------------------------------------
    def draw(self) -> None:
        self.query_one("#tabs", Static).update(self.tabs())
        self.query_one("#heads", Static).update(self.heads())
        self.query_one("#map", Static).update(self.map())
        self.query_one("#drawer", Static).update(self.drawer())
        self.query_one("#events", Static).update(self.log_text())

    def breath(self) -> str:
        wave = (math.sin(self.frame / FPS * math.pi) + 1) / 2          # one breath in two seconds
        return BREATH[round(wave * (len(BREATH) - 1))]

    def tabs(self) -> Text:
        text = Text()
        for name, prs in REPOS.items():
            text.append(name, style=f"bold underline {INK}" if name == self.repo else MUTED)
            text.append("     ")
        if self.paused:
            text.append("❚❚ paused", style=MUTED)
        else:
            text.append("●", style=GOOD if self.frame // 6 % 2 else "#076607")
            text.append(" live", style=INK2)
        seconds = (self.frame - self.refreshed_at) // FPS
        text.append(f" · refreshed {seconds}s ago" if seconds else " · refreshed just now", style=MUTED)
        return text

    def heads(self) -> Text:
        marks = {"yours": ("●", self.breath()), "theirs": ("○", MUTED), "ready": ("▲", GOOD),
                 "broken": ("⚠", CRIT), "sessions": (SPIN[self.frame % len(SPIN)], ACCENT)}
        labels = {"yours": "moves are yours", "theirs": "moves are theirs", "ready": "ready to merge",
                  "broken": "broken", "sessions": "sessions at work"}
        top, bottom = Text(), Text()
        for key in marks:
            top.append(f"{self.counts.get(key, 0):<22}", style=f"bold {INK}")
            mark, color = marks[key]
            bottom.append(mark, style=color)
            bottom.append(f" {labels[key]:<20}", style=MUTED)
        return top.append("\n").append_text(bottom)

    def map(self) -> Text:
        rows = self.rows()
        width = max(60, self.size.width - 2)
        canvas = Canvas(width, len(rows) + 2)
        canvas.put(X_TREE, 0, "main", f"bold {INK}")
        for label, x in (("activity", X_SPARK), ("review", X_REVIEW), ("checks", X_CHECKS),
                         ("next move", X_NEXT), ("who", X_WHO), ("quiet", X_AGE)):
            canvas.put(x, 0, label, MUTED)
        if not rows:
            canvas.put(X_TREE, 1, "└─ no open pull requests or local branches", MUTED)
        sweep_row = None if self.sweep_at is None else self.frame - self.sweep_at
        for i, (lead, pr) in enumerate(rows):
            y = i + 1
            age = self.frame - self.born.get(id(pr), 0)
            if age < 0:
                continue
            self.draw_row(canvas, y, lead, pr)
            reveal = age * 16                                  # the row writes itself in
            for x in range(min(width, max(0, reveal)), width):
                canvas.cells[y][x] = (" ", "")
            hit = self.frame - self.flash.get(id(pr), -999)
            if 0 <= hit // 2 < len(FLASH):
                for x in range(width):
                    canvas.paint(x, y, 1, FLASH[hit // 2])
            elif sweep_row is not None and 0 <= sweep_row - i < 3:
                for x in range(width):
                    canvas.paint(x, y, 1, FLASH[(sweep_row - i) * 2])
            elif i == self.sel:
                for x in range(width):
                    canvas.paint(x, y, 1, RAISED)
            if i == self.sel:
                canvas.put(0, y, "▌", ACCENT)
        if sweep_row is not None and sweep_row > len(rows) + 3:
            self.sweep_at = None
        return canvas.text()

    def draw_row(self, canvas: Canvas, y: int, lead: str, pr: PR) -> None:
        # the rails, with a current running up and left, toward main
        for k, ch in enumerate(lead):
            x = X_TREE + k
            lit = ch != " " and (y * 3 + x + self.frame // 2) % 14 < 2
            canvas.put(x, y, ch, MUTED if lit else AXIS)
        x = X_TREE + len(lead)
        icon, color, _, _ = STATUS[pr.state]
        hit = self.frame - self.flash.get(id(pr), -999)
        if 0 <= hit < 8:
            icon, color = TURN[hit % len(TURN)], INK
        canvas.put(x, y, icon, color)
        canvas.put(x + 2, y, pr.key, f"bold {INK}")
        canvas.put(x + 2 + len(pr.key), y, ("-" + pr.slug)[:X_SPARK - x - 3 - len(pr.key)], INK2)

        line = self.spark[id(pr)]
        for k, level in enumerate(line):                       # the day in twelve two-hour cells
            newest = k == len(line) - 1 and level and (self.live(pr) or pr.age == "now")
            canvas.put(X_SPARK + k, y, BARS[level], ACCENT if newest else (MUTED if level else AXIS))

        x = X_REVIEW
        for event in verdicts(pr).values():
            mark, mark_color, _ = WORD[event.verdict]
            canvas.put(x, y, mark, mark_color)
            x += 1
        if pr.threads[0]:
            canvas.put(x + 1, y, f"◆{pr.threads[0]}", INK2)

        if pr.ci == "run":
            done, total = int(pr.ci_note.split()[0]), int(pr.ci_note.split()[2])
            since = self.frame - self.ci_since.setdefault(id(pr), self.frame)
            done = min(total - 1, done + since // (FPS * 2))
            filled = max(1, round(6 * done / total))
            shimmer = self.frame // 2 % 6
            for k in range(6):
                style = AXIS if k >= filled else (INK if k == shimmer else WARN)
                canvas.put(X_CHECKS + k, y, "▰" if k < filled else "▱", style)
            canvas.put(X_CHECKS + 7, y, f"{done}/{total}", INK2)
        elif pr.state == "conflict":
            canvas.put(X_CHECKS, y, "⚠", CRIT)
            canvas.put(X_CHECKS + 2, y, "conflict", INK2)
        elif pr.ci == "fail":
            canvas.put(X_CHECKS, y, "✗", CRIT)
            canvas.put(X_CHECKS + 2, y, "failing", INK2)
        else:
            canvas.put(X_CHECKS, y, "✓" if pr.ci == "pass" else "·", GOOD if pr.ci == "pass" else AXIS)

        who, what = pr.next
        if who == "you":
            canvas.put(X_NEXT, y, "●", self.breath())
            canvas.put(X_NEXT + 2, y, f"you · {what}"[:X_WHO - X_NEXT - 3], f"bold {INK}")
        elif who:
            canvas.put(X_NEXT, y, "○", MUTED)
            canvas.put(X_NEXT + 2, y, f"{who} · {what}"[:X_WHO - X_NEXT - 3], INK2)

        if not pr.mine:
            canvas.put(X_WHO, y, pr.author, MUTED)
        x = X_WHO
        for worker in pr.workers if pr.mine else []:
            if worker.live:
                canvas.put(x, y, SPIN[(self.frame + y * 3) % len(SPIN)], ACCENT)
                canvas.put(x + 2, y, worker.serial, INK)
            else:
                canvas.put(x, y, "▷", MUTED)
                canvas.put(x + 2, y, worker.serial, MUTED)
            x += 6
        age = pr.age
        if age == "now":
            seconds = (self.frame - self.touched.get(id(pr), self.frame)) // FPS
            age = f"{seconds}s" if seconds else "now"
        canvas.put(X_AGE, y, age.rjust(4), f"bold {INK}" if pr.age == "now" else INK2)

    def drawer(self) -> Text:
        rows = self.rows()
        if not rows:
            return Text("nothing selected", style=MUTED)
        pr = rows[self.sel][1]
        icon, color, label, _ = STATUS[pr.state]
        lines: list[Text] = []
        lines.append(Text(f"{self.repo}-", style=MUTED).append(pr.name, style=f"bold {INK}"))
        lines.append(Text(icon, style=color).append(f" {label} · {pr.title}", style=INK2))
        who, what = pr.next
        if who:
            lines.append(Text("next     ", style=MUTED)
                         .append("● " if who == "you" else "○ ", style=ACCENT if who == "you" else MUTED)
                         .append(f"{who} · {what}", style=f"bold {INK}" if who == "you" else INK))
        said = Text("review   ", style=MUTED)
        for event in pr.history[-3:]:
            if event.kind == "push":
                said.append(f"{event.commits} commits pushed {event.age}", style=INK2)
            else:
                mark, mark_color, words = WORD[event.verdict]
                said.append(mark, style=mark_color)
                said.append(f" {'you' if event.who == ME else event.who} {words} {event.age}", style=INK)
            said.append("   ")
        if not pr.history:
            said.append("nobody asked yet", style=INK2)
        if pr.threads[1]:
            said.append(f"◆ {pr.threads[0]} of {pr.threads[1]} threads open", style=INK2)
        lines.append(said)
        work = Text("sessions ", style=MUTED)
        for worker in pr.workers if pr.mine else []:
            work.append(SPIN[self.frame % len(SPIN)] if worker.live else "▷",
                        style=ACCENT if worker.live else MUTED)
            work.append(f" {self.repo}-{pr.key}-{worker.serial}", style=INK if worker.live else INK2)
            work.append(f"  {worker.note}     ", style=INK2 if worker.live else MUTED)
        if not (pr.mine and pr.workers):
            work.append(f"{pr.author} on GitHub · no session on this Mac", style=INK2)
        lines.append(work)
        off = [w for w in pr.workers if w.name]
        names = Text("names    ", style=MUTED)
        if off:
            names.append("≠ ", style=CRIT)
            names.append(f"session is named {off[0].name}, expected "
                         f"{self.repo}-{pr.key}-{off[0].serial}", style=INK)
        else:
            names.append("✓ ", style=GOOD).append("branch, session, PR and Jira agree", style=INK)
        lines.append(names)
        lines.append(Text("checks   ", style=MUTED)
                     .append(" · ".join(x for x in (pr.ci_note, pr.merge) if x) or "none yet", style=INK))

        out = Text()
        age = self.frame - self.drawer_at                      # the drawer writes itself in too
        for i, line in enumerate(lines):
            out.append_text(line[:max(0, (age - i) * 28)])
            out.append("\n")
        return out

    def log_text(self) -> Text:
        out = Text()
        if self.repo != "agent-frolic":
            out.append("events   ", style=MUTED)
            out.append("the made-up event feed only runs on agent-frolic", style=MUTED)
            return out
        newest = self.events[-4:][::-1]
        if not newest:
            wait = max(0, (self.next_at - self.frame) // FPS)
            out.append("events   ", style=MUTED).append(f"the first one lands in {wait}s", style=MUTED)
        for i, (at, said) in enumerate(newest):
            seconds = (self.frame - at) // FPS
            out.append(f"{'now' if not seconds else str(seconds) + 's ago':<9}",
                       style=ACCENT if i == 0 and seconds < 2 else MUTED)
            out.append(said[:max(0, (self.frame - at) * 6)] if i == 0 else said,
                       style=INK if i == 0 else INK2)
            out.append("\n")
        return out

    # ---- keys --------------------------------------------------------------
    def action_move(self, step: int) -> None:
        count = len(self.rows())
        if count:
            self.sel = max(0, min(count - 1, self.sel + step))
            self.drawer_at = self.frame
            self.draw()

    def action_sweep(self) -> None:
        self.sweep_at = self.refreshed_at = self.frame

    def action_drawer(self) -> None:
        self.drawer_open = not self.drawer_open
        self.query_one("#drawer", Static).styles.animate(
            "height", value=9 if self.drawer_open else 0, duration=0.35)

    def action_pause(self) -> None:
        self.paused = not self.paused
        self.draw()

    def action_repo(self, step: int) -> None:
        names = list(REPOS)
        self.counts = {}
        self.load(names[(names.index(self.repo) + step) % len(names)])
        self.draw()


if __name__ == "__main__":
    Live().run()
