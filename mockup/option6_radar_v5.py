"""Agent Frolic mock-up, option 6 v5: radar, taken as an idea and not as a round scope.

A radar answers three things: what is around me, how close is it, and what just changed.
Here "me" is a human, you unless you re-centre, and "close" means how soon a PR needs that
human.

- Top line: every repo with its two small numbers; the repos on screen are underlined.
- The centre: whose radar this is, in a block you cannot miss, with the agents that human
  operates listed beneath (every agent runs under one human). o / O re-centre on the next
  or previous human; a / A narrow to that human alone or to one of their agents; escape
  returns to you. PRs that do not involve the centre are dimmed in place.
- The plot. Down the left, one tree for all repos on screen: repo, main, commit, branch,
  stack, one row per PR. To its right, the range plot. Its left edge is the centre. Each PR
  is one blip on its own row, in one of four range bands:
      needs you        the centre human must act
      your agents      an agent the centre operates is at work on it
      other agents     someone else's agent is at work on it
      waits on people  nobody is at work; it waits on another human
  The bands are divided by thin dotted lines. The label to the right of a blip leads with
  who ("mira › agent 01" for an agent, always after its operator), then what. A PR queued
  behind another says so and sits in the band of the PR it waits on.
- Burn-in: an idle blip gets heavier the longer it has sat quiet (○, then ◎ after a day,
  then ◉ after four), and its quiet time at the right turns bold. A blip that is being
  worked on is the turning dots. Blue ● is the one thing blue ever means: idle, and you can
  fix it.
- The sweep runs only when data arrives (something happens, or you press r). One scan line
  travels down the plot once, in about a second. Rows it has not reached still show the
  old picture; as it passes, a row that changed washes pale for a moment, and a blip that
  changed band leaves a short trail (‹‹‹ closing in on the centre, ››› moving away) that
  clears after a few seconds. Between sweeps only the turning dots move.
- The bottom band: what needs effort, most urgent first (an agent idle because of it, then
  longest quiet), and the selected contact: its owner side, what it waits on, then what
  GitHub says (each reviewer and their word, comments to resolve, checks, merge state) and
  every agent on it. Under it, one line with the last thing that happened.

Every name and number is made up; the things that happen are a short loop.

    .venv/bin/python mockup/option6_radar_v5.py

Keys: up/down pick a PR, n next thing needing effort, o / O re-centre, a / A narrow,
escape back to you, e next event now, r refresh, space pause, m repos on screen (up/down,
space toggles, enter closes), l legend, q quit.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.css.query import NoMatches
from textual.widgets import Static

from frolic_v5 import (ACCENT, AXIS, CRIT, GOOD, HAIR, INK, INK2, MUTED, PLANE, PR, RAISED,
                       SERIOUS, SHOWN_AT_START, SPIN, STATUS, WARN, Agent, Canvas, Focus, Person, Row5,
                       Segments, Stand, World, age_hours, focuses, fresh_world, happenings, humans,
                       in_focus, needs_effort5, progress_words, repo_status5, reviews, short,
                       stand, top_line5, tree5, write)

FPS = 15                         # ticks a second; the dots turn on every other tick
EVERY = 5.0                      # seconds between the things that happen
SWEEP_S = 1.0                    # how long the scan line takes to cross the plot
FLASH_S = 1.2                    # how long a changed row stays washed
TRAIL_S = 3.5                    # how long a blip's trail stays
FLASH = ["#4a4a46", "#444441", "#3d3d3a", "#363633", "#2f2f2d", "#292927"]     # a wash, fading
SCAN, AFTER = "#3d3d3a", "#2a2a28"       # the scan line, and the row it has just left
DIM = "#5c5b56"                  # PRs that do not involve the centre
FIXED = 8                        # rows that are not the plot or the bottom band
BAND_MIN, BAND_MAX = 8, 13       # the bottom band, its top border included
BAND_SCROLL = 11                 # its height once the plot has to scroll anyway
CI = {"pass": ("✓", GOOD), "fail": ("✗", CRIT), "run": ("…", WARN), "none": ("·", AXIS)}
BURN = (("○", MUTED), ("◎", INK2), ("◉", INK))        # an idle blip, heavier the longer it sits


# ---- range: how close a PR is to needing the centre --------------------------------------------

def band_of(here: Stand, centre: str) -> int:
    """0 the centre must act · 1 an agent of theirs is at work on it · 2 someone else's agent
    is · 3 nobody is at work and it waits on another human."""
    if centre in here.ends:
        return 0
    if here.moving:
        return 1 if any(a.operator == centre for a in here.working) else 2
    return 3


def burn(age: str) -> int:
    """How long a blip has sat quiet, as a weight: under a day, under four days, longer."""
    hours = age_hours(age)
    return 0 if hours < 24 else (1 if hours < 96 else 2)


def band_names(centre: str, tight: bool = False) -> list[tuple[str, str]]:
    """(heading, the plain word under it) for the four bands, as seen from the centre. The
    two read together: "your agents" over "at work". `tight` is for narrow bands."""
    agents = "" if tight else " agents"
    if centre == "you":
        return [("needs you", "to act"), ("yours" if tight else "your agents", "at work"),
                ("others" if tight else "other agents", "at work"),
                ("waits on people", "nobody at work")]
    return [(f"needs {centre}", "to act"), (f"{centre}'s{agents}", "at work"),
            ("others" if tight else "other agents", "at work"), ("waits on others", "nobody at work")]


def length(segments: Segments) -> int:
    return sum(len(text) for text, _ in segments)


def pick(variants: list[Segments], room: int) -> Segments:
    """The fullest wording that fits; the shortest, to be cut, when none does."""
    return next((v for v in variants if length(v) <= room), variants[-1])


def labels(world: World, here: Stand, weight: int) -> list[Segments]:
    """What stands to the right of a blip, from the fullest wording to the shortest. It
    leads with who: an agent after its operator while one is at work, else the humans who
    must act. Then what."""
    dot: tuple[str, str] = (" · ", MUTED)
    prefix: Segments = []
    if here.queued:
        prefix = [("behind ", MUTED), (short(here.waits[0].branch), INK2), dot]
    if here.moving:
        agent = here.working[0]
        who: Segments = [(agent.operator, INK), (" › ", MUTED), (agent.name, INK)]
        if here.queued:
            return [prefix + who]
        done, total = here.end_pr.progress
        doing = "reviewing" if agent.role == "review" else here.what
        counted = "" if not total else (f"{done} of {total} {here.end_pr.unit}".strip()
                                        if agent.role == "review" else f"{done} of {total} done")
        short_form = who + [dot, (doing, INK2)]
        return ([short_form + [dot, (counted, INK2)]] if counted else []) + [short_form, who]
    if not here.ends:
        return [[("nothing to do on it", MUTED)]]
    name = f"bold {INK}" if here.blue else (INK, INK2, INK)[weight] if weight else INK2
    who = [(", ".join(here.ends), name)]
    idle = [a for a in world.on(here.end_pr)
            if a.operator in here.ends and a.state == "at its prompt"]
    at: Segments = [(" › ", MUTED), (f"{idle[0].name} at prompt", INK2)] if idle else []
    if here.queued:
        return [prefix + who + at, prefix + who]
    what: Segments = [dot, (here.what, INK2)]
    return [who + at + what, who + what, who + at, who] if at else [who + what, who]


@dataclass
class View:
    """One PR as the plot draws it: where its blip is, and what stands beside it."""
    band: int
    moving: bool                 # an agent is at work: the blip is the turning dots
    blue: bool                   # idle, and you can fix it
    burn: int                    # 0, 1, 2: how heavy an idle blip is
    labels: list[Segments]
    quiet: str
    lit: bool                    # it involves whoever is in focus
    state: str = "local"         # the PR's state on GitHub, for the mark before its name

    @property
    def signature(self) -> tuple:
        return (self.band, self.moving, self.blue, "".join(t for t, _ in self.labels[0]), self.quiet,
                self.state)

    def blip(self, spin: str) -> tuple[str, str]:
        if self.moving:
            return spin, INK
        if self.blue:
            return "●", ACCENT
        return BURN[self.burn]


@dataclass
class Geo:
    """Where things are across the plot, for one window width."""
    width: int                   # the cells there are
    tree: int                    # x of the centre line; the tree is to its left
    band: int                    # how wide the first three bands are
    rings: list[int]             # x of the centre line and of the three dotted lines
    quiet: int                   # x of the quiet column, five cells wide


def geo(width: int) -> Geo:
    tree = max(30, min(46, int(width * 0.31)))
    quiet = width - 5
    plot = quiet - 1 - tree
    band = max(12, min(18, round(plot * 0.19)))
    return Geo(width, tree, band, [tree + i * band for i in range(4)], quiet)


def same(a: Focus, b: Focus) -> bool:
    return (a.kind, a.human, a.agent) == (b.kind, b.human, b.agent)


def agent_words(agent: Agent) -> str:
    return {"working": "working", "at its prompt": "at its prompt", "waiting": "waiting"}[agent.state]


class RadarV5(App):
    CSS = f"""
    Screen {{ background: {PLANE}; color: {INK}; layers: base overlay; }}
    Static {{ text-wrap: nowrap; text-overflow: ellipsis; }}
    #top, #ticker, #hints {{ height: 1; padding: 0 1; }}
    #centre {{ height: 3; padding: 0 1; }}
    #head {{ height: 2; padding: 0 1; }}
    #plot {{ height: 1fr; padding: 0 1; }}
    #band {{ height: {BAND_MAX}; border-top: solid {HAIR}; }}
    #effort {{ width: 88; height: 1fr; padding: 0 1; border-right: solid {HAIR}; }}
    #detail {{ width: 1fr; height: 1fr; padding: 0 1; }}
    #legend {{ width: 1fr; height: 1fr; padding: 0 1; display: none; }}
    #band.legend #effort, #band.legend #detail {{ display: none; }}
    #band.legend #legend {{ display: block; }}
    #band.narrow #effort {{ width: 68; }}
    #ticker, #hints {{ color: {MUTED}; }}
    #menu {{ layer: overlay; display: none; width: 46; height: auto; offset: 3 2;
             background: {RAISED}; border: round {MUTED}; padding: 0 1; }}
    """
    BINDINGS = [
        Binding("up", "move(-1)", show=False), Binding("down", "move(1)", show=False),
        Binding("n", "next_effort", show=False),
        Binding("o", "recentre(1)", show=False), Binding("O", "recentre(-1)", show=False),
        Binding("a", "narrow(1)", show=False), Binding("A", "narrow(-1)", show=False),
        Binding("escape", "back", show=False),
        Binding("e", "event", show=False), Binding("r", "refresh", show=False),
        Binding("space", "space", show=False), Binding("enter", "close_menu", show=False),
        Binding("m", "menu", show=False), Binding("l", "toggle_legend", show=False),
        Binding("q", "quit", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.world = fresh_world()
        self.events = happenings()
        self.step = 0                         # the next thing to happen
        self.shown = [name for name in self.world.repos if name in SHOWN_AT_START]
        self.focus = Focus()                  # everyone, centred on you
        self.sel: int | None = None           # the selected PR, by number
        self.top = 0                          # first plot row on screen
        self.frame = 0                        # turns the dots
        self.ticks = 0
        self.paused = False
        self.next_at = time.monotonic() + EVERY
        self.refreshed = "2m ago"
        self.said = ""                        # the last thing that happened
        self.said_repo = ""
        self.sweep: float | None = None       # the scan line's row, while it travels
        self.sweep_rate = 2.0                 # rows a tick
        self.old_views: dict[int, View] = {}  # the picture before, for rows the line has not reached
        self.pending: set[int] = set()        # changed PRs the line has not reached yet
        self.moved: dict[int, int] = {}       # PR -> the band it was in before
        self.flash: dict[int, float] = {}     # PR -> when the line passed its changed row
        self.trails: dict[int, tuple[int, float]] = {}    # PR -> (the band it left, when)
        self.menu_open = False
        self.menu_at = 0
        self.band_h = BAND_MAX
        self._views: dict[int, View] | None = None
        self._rows: list[Row5] | None = None

    def compose(self) -> ComposeResult:
        yield Static(id="top")
        yield Static(id="centre")
        yield Static(id="head")
        yield Static(id="plot")
        with Horizontal(id="band"):
            yield Static(id="effort")
            yield Static(id="detail")
            yield Static(id="legend")
        yield Static(id="ticker")
        yield Static(id="hints")
        yield Static(id="menu")

    def on_mount(self) -> None:
        self.draw()
        self.set_interval(1 / FPS, self.tick)

    def on_resize(self) -> None:
        try:
            self.query_one("#band").set_class(self.size.width < 140, "narrow")
            self.draw()
        except NoMatches:                     # resized while the app is closing
            pass

    # ---- the picture ---------------------------------------------------------------
    @property
    def centre(self) -> str:
        return self.focus.human or "you"

    def touch(self) -> None:
        """The world, the repos on screen or the focus changed: work the picture out again."""
        self._views = None
        self._rows = None

    def rows(self) -> list[Row5]:
        if self._rows is None:
            self._rows = tree5(self.world, self.shown)
        return self._rows

    def views(self) -> dict[int, View]:
        if self._views is None:
            found: dict[int, View] = {}
            for pr in self.world.all(self.shown):
                here = stand(self.world, pr)
                weight = 0 if here.moving else burn(pr.age)
                found[pr.number] = View(band_of(here, self.centre), here.moving, here.blue, weight,
                                        labels(self.world, here, weight), pr.age,
                                        in_focus(self.world, pr, self.focus), pr.state)
            self._views = found
        return self._views

    def branches(self) -> list[PR]:
        return [row.pr for row in self.rows() if row.pr is not None]

    def selected(self) -> PR | None:
        found = self.branches()
        if not found:
            self.sel = None
            return None
        chosen = next((pr for pr in found if pr.number == self.sel), found[0])
        self.sel = chosen.number
        return chosen

    def plot_rows(self) -> int:
        """The plot takes the rows it needs; the bottom band takes what is left, within its
        limits. Returns the rows the plot has."""
        spare = self.size.height - FIXED - len(self.rows())
        if spare >= BAND_MIN:
            band = min(BAND_MAX, spare)
        else:                                  # the plot scrolls whatever the band takes
            band = BAND_SCROLL if self.size.height >= 40 else BAND_MIN
        band = max(4, min(band, self.size.height - FIXED - 3))
        if band != self.band_h:
            self.band_h = band
            self.query_one("#band").styles.height = band
        return max(3, self.size.height - FIXED - band)

    def window(self) -> tuple[list[Row5], int]:
        """The plot rows on screen, scrolled so the selected PR is among them."""
        rows, height = self.rows(), self.plot_rows()
        selected = self.selected()
        at = next((i for i, row in enumerate(rows) if row.pr is selected), 0) if selected else 0
        self.top = max(0, min(self.top, at))
        if at >= self.top + height:
            self.top = at - height + 1
        if at <= 3 or len(rows) <= height:
            self.top = 0
        elif selected is not None and selected is self.branches()[-1]:
            self.top = len(rows) - height
        self.top = max(0, min(self.top, max(0, len(rows) - height)))
        return rows[self.top:self.top + height], height

    # ---- time ----------------------------------------------------------------------
    def tick(self) -> None:
        now = time.monotonic()
        self.ticks += 1
        turned = self.ticks % 2 == 0
        if turned:
            self.frame += 1
        if not self.paused and now >= self.next_at:
            self.happen()
        busy = self.sweep is not None or bool(self.flash) or bool(self.trails)
        if self.sweep is not None:
            self.sweep += self.sweep_rate
            shown, _ = self.window()
            self.reveal(shown, now)
            if self.sweep > len(shown) + 1:           # the line has left the plot
                self.sweep, self.old_views = None, {}
                self.pending.clear()
                self.moved.clear()
        self.flash = {n: t for n, t in self.flash.items() if now - t < FLASH_S}
        self.trails = {n: v for n, v in self.trails.items() if now - v[1] < TRAIL_S}
        try:
            if turned:
                self.query_one("#top", Static).update(self.top_text())
                self.query_one("#centre", Static).update(self.centre_text())
            if turned or busy:
                self.query_one("#plot", Static).update(self.plot())
        except NoMatches:                             # a last tick while the app is closing
            pass

    def reveal(self, shown: list[Row5], now: float) -> None:
        """The scan line shows the new picture row by row: a changed row starts its wash,
        and a blip that changed band starts its trail, as the line passes."""
        line = int(self.sweep) if self.sweep is not None else len(shown)
        for y, row in enumerate(shown):
            if row.pr is None or y > line or row.pr.number not in self.pending:
                continue
            number = row.pr.number
            self.pending.discard(number)
            self.flash[number] = now
            if number in self.moved:
                self.trails[number] = (self.moved.pop(number), now)

    def start_sweep(self, old: dict[int, View], changed: set[int]) -> None:
        new = self.views()
        self.old_views = old
        self.pending = {n for n in self.pending | changed if n in new}     # a sweep may still be in flight
        self.moved.update({n: old[n].band for n in changed if n in old})
        self.moved = {n: was for n, was in self.moved.items() if n in new and new[n].band != was}
        shown, _ = self.window()
        self.sweep = -1.0
        self.sweep_rate = max(1.0, len(shown) / (SWEEP_S * FPS))
        self.refreshed = "just now"

    def happen(self) -> None:
        """The next thing in the loop happens, and the sweep shows it."""
        old = dict(self.views())
        if self.step >= len(self.events):
            self.world, self.step = fresh_world(), 0
            self.said, self.said_repo, touched = "the loop starts again from how things were", "", []
        else:
            event = self.events[self.step]
            touched = event.apply(self.world)
            self.step += 1
            self.said, self.said_repo = event.say, event.repo
        self.touch()
        new = self.views()
        changed = {n for n in new if n in old and new[n].signature != old[n].signature}
        self.start_sweep(old, changed | set(touched))
        self.next_at = time.monotonic() + EVERY
        self.draw()

    def still(self) -> None:
        """Whatever was in flight is dropped: used when the centre or the repos change."""
        self.sweep, self.old_views = None, {}
        self.pending.clear()
        self.moved.clear()
        self.flash.clear()
        self.trails.clear()

    # ---- drawing -------------------------------------------------------------------
    def inner(self) -> int:
        return max(60, self.size.width - 2)

    def draw(self) -> None:
        try:
            self.plot_rows()
            self.query_one("#top", Static).update(self.top_text())
            self.query_one("#centre", Static).update(self.centre_text())
            self.query_one("#head", Static).update(self.head())
            self.query_one("#plot", Static).update(self.plot())
            narrow = self.size.width < 140
            pane = (68 if narrow else 88) - 3
            self.query_one("#effort", Static).update(self.effort_text(self.band_h - 2, pane))
            pr = self.selected()
            self.query_one("#detail", Static).update(
                self.detail_text(pr) if pr is not None
                else Text("no open pull requests or local branches in the repos on screen", style=MUTED))
            self.query_one("#legend", Static).update(self.legend_text())
            self.query_one("#ticker", Static).update(self.ticker_text())
            self.query_one("#hints", Static).update(Text(
                "↑↓ PR   n next needing effort   o O re-centre   a A narrow   esc back to you   "
                "e next event   r refresh   space pause   m repos   l legend   q quit"))
            menu = self.query_one("#menu", Static)
            menu.display = self.menu_open
            if self.menu_open:
                menu.update(self.menu_text())
        except NoMatches:                             # drawn while the app is closing
            pass

    def top_text(self) -> Text:
        return top_line5(self.world, self.shown, frame=self.frame, refreshed=self.refreshed,
                         paused=self.paused)

    def centre_text(self) -> Text:
        """Whose radar this is, in a block; the humans to re-centre on; and beneath, the
        agents the centre operates."""
        width = self.inner()
        centre, focus = self.centre, self.focus
        people = humans(self.world, self.shown)
        person = next((p for p in people if p.name == centre), Person(centre))
        their = "your" if centre == "you" else "their"
        about = {
            "all": "everything on screen, by how close it is to needing you",
            "team": f"{centre} and {their} agents · the rest is dimmed",
            "human": f"{centre} alone, without {their} agents · the rest is dimmed",
            "agent": f"one agent of {'yours' if centre == 'you' else centre + chr(39) + 's'} · the rest is dimmed",
        }[focus.kind]
        left = Text(no_wrap=True)
        left.append("radar centred on ", style=MUTED)
        left.append(f" {' '.join(centre.upper())} ", style=f"bold {PLANE} on {INK}")
        left.append("  ")
        right = Text(no_wrap=True)
        right.append("re-centre on  ", style=MUTED)
        for i, other in enumerate(people):
            right.append("  " if i else "")
            here = other.name == centre
            right.append(other.name, style=f"bold underline {INK}" if here else INK2)
            if not other.has_work and other.name != "you":
                right.append(" ●", style=ACCENT)
                right.append(" nothing to do", style=INK)
        room = width - left.cell_len - right.cell_len - 3
        if room >= 12:
            left.append(about if len(about) <= room else about[:room - 1] + "…", style=INK2)
        out = Text(no_wrap=True)
        out.append_text(left)
        out.append(" " * max(2, width - left.cell_len - right.cell_len))
        out.append_text(right)
        out.append("\n")

        spin = SPIN[self.frame % len(SPIN)]
        entries: list[Text] = []
        for agent in person.agents:
            yours = agent.operator == "you"
            picked = focus.kind == "agent" and focus.agent == agent.key
            aside = focus.kind == "human" or (focus.kind == "agent" and not picked)
            mark, color = {"working": (spin, INK), "waiting": ("·", MUTED),
                           "at its prompt": ("▷", ACCENT if yours else MUTED)}[agent.state]
            entry = Text(no_wrap=True)
            entry.append(mark, style=DIM if aside else color)
            said = f" {agent.name} · pr-{agent.number:04d}" + (" review" if agent.role == "review" else "")
            entry.append(said, style=DIM if aside else (f"bold {INK}" if picked else
                                                        INK if agent.working else INK2))
            if agent.odd_name:
                entry.append(" ≠", style=DIM if aside else f"bold {CRIT}")
            if picked:
                entry.stylize(f"on {RAISED}")
            entries.append(entry)
        lines = [Text("  └ ", style=AXIS, no_wrap=True), Text("    ", no_wrap=True)]
        if not entries:
            lines[0].append(f"no agents of {'yours' if centre == 'you' else centre + chr(39) + 's'} "
                            "in the repos on screen", style=MUTED)
        placed: list[list[Text]] = [[], []]
        used, at, left_over = [4, 4], 0, 0
        for i, entry in enumerate(entries):
            if used[at] + entry.cell_len + 3 > width:
                if at == 1:
                    left_over = len(entries) - i
                    break
                at = 1
            placed[at].append(entry)
            used[at] += entry.cell_len + 3
        while left_over and placed[1] and used[1] + len(f"and {left_over} more") > width:
            used[1] -= placed[1].pop().cell_len + 3
            left_over += 1
        for line, found in zip(lines, placed):
            for entry in found:
                line.append_text(entry).append("   ")
        if left_over:
            lines[1].append(f"and {left_over} more", style=MUTED)
        return out.append_text(lines[0]).append("\n").append_text(lines[1])

    def head(self) -> Text:
        """The range scale: the band names over the dotted lines, and under them a ruler
        that starts at the centre."""
        g = geo(self.inner())
        canvas = Canvas(g.width, 2)
        canvas.put(0, 0, "repo → main → commit → branch → stack"[:g.tree - 1], MUTED)
        rows = self.rows()
        _, height = self.window()
        if len(rows) > height:
            canvas.put(0, 1, f"rows {self.top + 1}–{min(len(rows), self.top + height)} of {len(rows)}", INK2)
        said = f"centre: {self.centre} "
        if len(rows) <= height or g.tree - len(said) > 20:
            canvas.put(g.tree - len(said), 1, "centre: ", MUTED)
            canvas.put(g.tree - len(said) + 8, 1, self.centre, f"bold {INK}")
        for x in range(g.rings[0], g.quiet - 1):
            canvas.put(x, 1, "━", AXIS)
        canvas.put(g.quiet - 2, 1, "▸", MUTED)
        for i, (name, plain) in enumerate(band_names(self.centre, tight=g.band < 16)):
            x = g.rings[i]
            room = (g.band if i < 3 else g.quiet - 1 - x) - 3
            canvas.put(x, 0, "┃" if i == 0 else "┊", MUTED if i == 0 else AXIS)
            canvas.put(x, 1, "┣" if i == 0 else "┿", MUTED)
            write(canvas, x + 2, 0, [(name, f"bold {INK}" if i == 0 else INK2)], room)
            write(canvas, x + 2, 1, [(f" {plain} ", MUTED)], room)
        canvas.put(g.quiet, 0, "quiet", MUTED)
        return canvas.text()

    def plot(self) -> Text:
        g = geo(self.inner())
        shown, height = self.window()
        views = self.views()
        selected = self.selected()
        now = time.monotonic()
        line = int(self.sweep) if self.sweep is not None else None
        canvas = Canvas(g.width, max(1, height))
        if not self.branches() and len(shown) < height:
            canvas.put(g.rings[0] + 2, len(shown), "no open pull requests or local branches", MUTED)
        for y, row in enumerate(shown):
            wash = ""
            if row.kind == "repo":
                canvas.put(0, y, "━" * g.width, AXIS)
                canvas.put(2, y, f" {row.label} ", f"bold {INK}")
            elif row.kind == "base":
                canvas.put(0, y, row.lead, AXIS)
                x = len(row.lead)
                canvas.put(x, y, row.label, f"bold {INK2}")
                x += len(row.label) + 1
                if row.note:
                    x += write(canvas, x, y, [(row.note, MUTED)], g.width - x - 4) + 1
                canvas.put(x, y, "─" * max(0, g.width - x), HAIR)
            else:
                self.rings(canvas, g, y)
                if row.kind == "commit":
                    canvas.put(0, y, row.lead[:-2], AXIS)
                    canvas.put(len(row.lead) - 2, y, row.lead[-2:], MUTED)
                    write(canvas, len(row.lead), y, [(row.sha, INK2), (" " + row.label, MUTED)],
                          g.tree - 1 - len(row.lead))
                else:
                    number = row.pr.number
                    before = line is not None and y > line and number in self.old_views
                    view = self.old_views[number] if before else views[number]
                    trail = None if before else self.trails.get(number)
                    self.contact(canvas, g, y, row, view, trail)
                    if row.pr is selected:
                        wash = RAISED
                    since = now - self.flash.get(number, -99.0)
                    if 0 <= since < FLASH_S:
                        wash = FLASH[min(len(FLASH) - 1, int(since / FLASH_S * len(FLASH)))]
            if line is not None and y == line:
                wash = SCAN
            elif line is not None and y == line - 1 and not wash:
                wash = AFTER
            if wash:
                canvas.paint(0, y, g.width, wash)
        return canvas.text()

    def rings(self, canvas: Canvas, g: Geo, y: int) -> None:
        for i, x in enumerate(g.rings):
            canvas.put(x, y, "┃" if i == 0 else "┊", MUTED if i == 0 else AXIS)

    def contact(self, canvas: Canvas, g: Geo, y: int, row: Row5, view: View,
                trail: tuple[int, float] | None) -> None:
        """One PR: its place in the tree, then its one blip in its band, the label to the
        right of the blip, and how long it has been quiet."""
        pr = row.pr
        aside = "" if view.lit else DIM
        icon, color, _, _ = STATUS[view.state]
        canvas.put(0, y, row.lead, AXIS)
        x = len(row.lead)
        canvas.put(x, y, icon, aside or color)
        write(canvas, x + 2, y, [(pr.key, f"bold {INK}"), ("-" + pr.slug, INK2)], g.tree - x - 3, aside)

        spin = SPIN[(self.frame + y * 3) % len(SPIN)]
        at = g.rings[view.band] + 2
        text_at = at + 2
        if trail is not None and trail[0] != view.band:
            was = g.rings[trail[0]] + 2
            if was > at:                                   # it came closer: the label steps aside
                canvas.put(at + 2, y, "‹" * (was - at - 1), aside or INK2)
                text_at = was + 2
            else:                                          # it moved away
                canvas.put(was, y, "›" * (at - was - 1), aside or INK2)
        mark, mark_color = view.blip(spin)
        canvas.put(at, y, mark, aside or mark_color)
        room = g.quiet - 1 - text_at
        end = text_at + write(canvas, text_at, y, pick(view.labels, room), room, aside)
        if end in g.rings:                                 # a label never touches a range line
            canvas.put(end, y, " ")
        heavy = not view.moving and view.burn == 2
        canvas.put(g.quiet, y, view.quiet.rjust(5),
                   aside or (f"bold {INK}" if heavy else INK2 if view.burn and not view.moving else MUTED))

    def effort_text(self, room: int, width: int) -> Text:
        """What needs effort to get moving, most urgent first. Each line leads with who must
        act, then why it is urgent, then what the move is and what it holds up."""
        text = Text(no_wrap=True)
        text.append("needs effort to get moving", style=f"bold {INK}")
        text.append("   most urgent first: agents idle on it, then longest quiet\n", style=MUTED)
        found = needs_effort5(self.world, self.shown)
        if not found:
            return text.append("nothing: everything that can move is moving"
                               if self.branches() else "nothing open", style=MUTED)
        fits = room if len(found) <= room else room - 1
        tight = width < 80
        who_w = min(13, max(len(e.who) for e in found))
        key_w = 7 if tight else max(len(e.pr.key) for e in found)
        for rank, effort in enumerate(found[:fits], start=1):
            line = Text(f"{rank:<3}", style=MUTED, no_wrap=True)
            line.append("● " if effort.blue else "○ ", style=ACCENT if effort.blue else MUTED)
            who = effort.who if len(effort.who) <= who_w else effort.who[:who_w - 1] + "…"
            line.append(f"{who:<{who_w}} ", style=f"bold {INK}" if effort.blue else INK2)
            line.append(f"{short(effort.pr) if tight else effort.pr.key:<{key_w}}  ", style=INK)
            urgency = Text()
            if effort.waiting:
                held, quiet = effort.urgency.split(" · ", 1)
                urgency.append(held, style=f"bold {INK}").append(" · ", style=MUTED)
            else:
                quiet = effort.urgency
            urgency.append("just now" if quiet == "quiet now" else quiet,
                           style=f"bold {INK}" if burn(effort.pr.age) == 2 else INK2)
            line.append_text(urgency).append(" " * max(2, 29 - urgency.cell_len))
            line.append(effort.stand.what, style=INK2)
            if effort.behind:
                line.append(" · holds up ", style=MUTED)
                line.append(", ".join(short(p) for p in effort.behind), style=INK)
            if effort.pr.number == self.sel or any(p.number == self.sel for p in effort.behind):
                line.truncate(width)
                line.append(" " * max(0, width - line.cell_len))
                line.stylize(f"on {RAISED}")
            text.append_text(line).append("\n")
        if len(found) > fits:
            text.append(f"   and {len(found) - fits} more: n steps through them", style=MUTED)
        return text

    def detail_text(self, pr: PR) -> Text:
        """The selected contact: its owner side and what it waits on, in plain words, then
        what GitHub says, then every agent on it."""
        world = self.world
        here = stand(world, pr)
        icon, color, label, _ = STATUS[pr.state]
        text = Text(no_wrap=True)
        text.append(icon + " ", style=color)
        text.append(pr.name, style=f"bold underline {INK}")
        text.append(f"  {label}\n", style=INK2)

        def kv(key: str, value: Text) -> None:
            text.append(f"{key:<9}", style=MUTED)
            text.append_text(value).append("\n")

        def who(name: str, blue: bool) -> Text:
            out = Text("● " if blue else "", style=ACCENT)
            return out.append(name, style=f"bold {INK}" if blue else INK)

        def with_agents(agents: list[Agent]) -> str:
            if not agents:
                return ", no agent on it"
            return ", with " + " and ".join(f"{a.name} {agent_words(a)}" for a in agents)

        owner = who(here.owner.human, here.owner.blue)
        owner.append(with_agents(here.owner.agents), style=INK2)
        kv("owner", owner)

        waits = Text()
        if here.waits:
            for i, party in enumerate(here.waits):
                if party.branch is not None:
                    waits.append(short(party.branch), style=INK).append(", which waits on ", style=INK2)
                    continue
                if i and here.waits[i - 1].branch is None:
                    waits.append(" and ", style=MUTED)
                waits.append_text(who(party.human, party.blue))
                waits.append(with_agents(party.agents), style=INK2)
        elif here.moving:
            waits.append("nothing: an agent is at work on it", style=INK2)
        elif here.holds:
            waits.append("nobody else: ", style=INK2).append_text(who(here.owner.human, here.owner.blue))
            waits.append(" to act", style=INK2)
        else:
            waits.append("nothing", style=INK2)
        kv("waits on", waits)
        kv("to do", Text(here.what or "nothing", style=INK).append(
            f" · {progress_words(world, pr)}", style=INK2))

        said = Text()
        words = {"approved": "approved", "changes": "asked for changes",
                 "requested": "asked to review", "rerequested": "asked to approve the fix"}
        marks = {"approved": ("✓", GOOD), "changes": ("✗", SERIOUS), "requested": ("◔", WARN),
                 "rerequested": ("↻", WARN)}
        for i, (name, verdict) in enumerate(reviews(pr)):
            said.append(" · " if i else "", style=MUTED)
            said.append(marks[verdict][0] + " ", style=marks[verdict][1])
            said.append(f"{name} {words[verdict]}", style=INK)
        if not said.plain:
            said.append("nobody has been asked yet" if pr.is_pr else "no PR yet", style=INK2)
        kv("reviews", said)
        if pr.threads[1]:
            opened, total = pr.threads
            kv("comments", Text(f"{opened} of {total} still to resolve" if opened
                                else f"all {total} resolved", style=INK if opened else INK2))
        ci_icon, ci_color = CI[pr.ci]
        checks = Text(ci_icon + " ", style=ci_color).append(pr.ci_note or "no checks yet", style=INK)
        checks.append("   merge ", style=MUTED)
        if "conflict" in pr.merge:
            checks.append("⚠ ", style=CRIT)
        checks.append(pr.merge, style=INK if "conflict" in pr.merge else INK2)
        kv("checks", checks)
        for i, agent in enumerate(world.on(pr)):
            line = Text(f"{agent.operator} › {agent.name}", style=INK)
            line.append(f" · {'reviewing it' if agent.role == 'review' else 'on it'} for {agent.operator}"
                        f" · {agent_words(agent)}", style=INK2)
            if agent.odd_name:
                line.append(" ≠ ", style=f"bold {CRIT}").append(f"session is {agent.odd_name}", style=INK2)
            kv("agents" if i == 0 else "", line)
            if agent.note:
                kv("", Text(agent.note, style=MUTED))
        if pr.title:
            kv("title", Text(pr.title, style=INK2))
        return text

    def legend_text(self) -> Text:
        g = geo(self.inner())
        canvas = Canvas(g.width, max(1, self.band_h - 1))
        m = MUTED
        columns: list[tuple[int, str, list[Segments]]] = [
            (0, "the range plot", [
                [("┃ ", m), ("the centre: a human, you unless you re-centre", m)],
                [("┊ ", m), ("range lines. From the centre outwards: that", m)],
                [("  human must act · an agent of theirs is at work", m)],
                [("  · someone else's agent is · nobody is at work", m)],
                [("› ", m), ("an agent always follows its operator's name", m)],
                [("  \"behind pr-0024\": queued, in that PR's band", m)],
            ]),
            (52, "the blips", [
                [(SPIN[2] + " ", INK), ("an agent is at work on it right now", m)],
                [("● ", ACCENT), ("idle, and you can fix it. Blue is only this.", m)],
                [("○ ", MUTED), ("idle, waits on someone else, under a day", m)],
                [("◎ ", INK2), ("the same, a day or more   ", m), ("◉ ", INK), ("four days or more", m)],
                [("▷ ", m), ("an agent at its prompt waits on its operator", m)],
                [("≠ ", f"bold {CRIT}"), ("a session name that breaks the pattern", m)],
            ]),
            (104, "when data arrives", [
                [("━ ", m), ("one scan line travels down, once.", m)],
                [("  Rows not yet reached show the old", m)],
                [("  picture; a changed row washes pale.", m)],
                [("‹ ", INK2), ("a blip that came closer", m)],
                [("› ", INK2), ("a blip that moved away", m)],
                [("  Nothing else moves but the dots.", m)],
            ]),
        ]
        for x, title, lines in columns:
            canvas.put(x, 0, title, f"bold {INK}")
            for y, segments in enumerate(lines, start=1):
                write(canvas, x, y, segments, g.width - x)
        return canvas.text()

    def ticker_text(self) -> Text:
        text = Text(no_wrap=True)
        text.append("last  ", style=MUTED)
        if not self.said:
            text.append("nothing has happened yet" if self.paused
                        else "nothing yet: the first thing happens in a few seconds", style=MUTED)
        else:
            text.append(self.said, style=INK2)
            if self.said_repo:
                off = self.said_repo not in self.shown
                text.append(f"   {self.said_repo}" + (", not on screen" if off else ""), style=MUTED)
        text.append("      ❚❚ paused: e lets the next thing happen" if self.paused else "", style=MUTED)
        return text

    def menu_text(self) -> Text:
        text = Text(no_wrap=True)
        text.append("repos on screen\n", style=f"bold {INK}")
        for i, name in enumerate(self.world.repos):
            here = i == self.menu_at
            yours, nudge = repo_status5(self.world, name)
            line = Text("▸ " if here else "  ", style=INK, no_wrap=True)
            line.append("✓ " if name in self.shown else "· ", style=INK if name in self.shown else MUTED)
            line.append(f"{name:<16}", style=f"bold {INK}" if here else INK2)
            if yours:
                line.append(" ●", style=ACCENT).append(str(yours), style=INK2)
            if nudge:
                line.append(" ○", style=MUTED).append(str(nudge), style=MUTED)
            text.append_text(line).append("\n")
        text.append("space shows or hides · enter closes", style=MUTED)
        return text

    # ---- keys ----------------------------------------------------------------------
    def action_move(self, step: int) -> None:
        if self.menu_open:
            self.menu_at = (self.menu_at + step) % len(self.world.repos)
            self.draw()
            return
        found = self.branches()
        if not found:
            return
        at = next((i for i, pr in enumerate(found) if pr.number == self.sel), 0)
        self.sel = found[max(0, min(len(found) - 1, at + step))].number
        self.draw()

    def action_next_effort(self) -> None:
        """Jump to the next thing that needs effort, most urgent first."""
        stops = [e.pr.number for e in needs_effort5(self.world, self.shown)]
        if not stops or self.menu_open:
            return
        self.sel = stops[(stops.index(self.sel) + 1) % len(stops)] if self.sel in stops else stops[0]
        self.draw()

    def refocus(self, focus: Focus) -> None:
        self.focus = focus
        self.still()
        self.touch()
        self.draw()

    def action_recentre(self, step: int) -> None:
        """The next or previous human becomes the centre, with all their agents."""
        if self.menu_open:
            return
        stops = [Focus()] + [f for f in focuses(self.world, self.shown) if f.kind == "team"]
        at = next((i for i, f in enumerate(stops) if f.human == self.focus.human
                   and (f.kind == "all") == (self.focus.kind == "all")), 0)
        self.refocus(stops[(at + step) % len(stops)])

    def action_narrow(self, step: int) -> None:
        """Within the centre human: with all their agents, alone, then each agent in turn."""
        if self.menu_open:
            return
        stops = [f for f in focuses(self.world, self.shown) if f.human == self.centre]
        if not stops:
            return
        at = next((i for i, f in enumerate(stops) if same(f, self.focus)), -1 if step > 0 else 0)
        if self.focus.kind == "all":
            at = 0
        self.refocus(stops[(at + step) % len(stops)])

    def action_back(self) -> None:
        if self.menu_open:
            self.menu_open = False
            self.draw()
        elif self.focus.kind != "all":
            self.refocus(Focus())

    def action_event(self) -> None:
        self.happen()

    def action_refresh(self) -> None:
        """A manual refresh: the line sweeps, and nothing has changed."""
        self.start_sweep({}, set())
        self.draw()

    def action_space(self) -> None:
        if self.menu_open:
            name = list(self.world.repos)[self.menu_at]
            if name in self.shown and len(self.shown) > 1:
                self.shown.remove(name)
            elif name not in self.shown:
                self.shown = [n for n in self.world.repos if n in self.shown or n == name]
            people = {p.name for p in humans(self.world, self.shown)}
            if self.focus.human and self.focus.human not in people:
                self.focus = Focus()
            if self.focus.kind == "agent" and not any(
                    a.key == self.focus.agent for pr in self.world.all(self.shown) for a in self.world.on(pr)):
                self.focus = Focus("team", self.focus.human)
            self.still()
            self.touch()
            self.top = 0
        else:
            self.paused = not self.paused
            self.next_at = time.monotonic() + EVERY
        self.draw()

    def action_menu(self) -> None:
        self.menu_open = not self.menu_open
        self.draw()

    def action_close_menu(self) -> None:
        if self.menu_open:
            self.menu_open = False
            self.draw()

    def action_toggle_legend(self) -> None:
        self.query_one("#band").toggle_class("legend")


if __name__ == "__main__":
    RadarV5().run()
