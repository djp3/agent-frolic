"""Agent Frolic mock-up, option 6 v5c: the round radar, with bearing as WHO.

The round scope of option 6 v5b with one thing changed: bearing no longer follows the tree.
Distance still says how close a PR is to needing a human; direction now says whose it is.
The rim shows the people, and beneath each of them the agents they operate.

- Top line: every repo with its two small numbers; the repos on screen are underlined.
- Whose radar it is, in a block, with the agents that human operates beneath (every agent
  runs under one human). o / O re-centre on the next or previous human; a / A narrow to
  that human alone or to one of their agents; escape returns to you. PRs that do not
  involve the centre are dimmed in place. Nobody moves round the dial when you re-centre.
- The scope, on the left.
    centre    the human it is centred on.
    rings     four, read outwards from the centre, divided by fine dotted circles and
              numbered at 12 o'clock: 1 that human must act · 2 an agent of theirs is at
              work · 3 someone else's agent is at work · 4 nobody is at work and it waits
              on another human.
    bearing   who owns it. Every human has a sector of the dial, you first from 12
              o'clock, then the others clockwise. A PR's blip sits in its owner's sector,
              so direction says whose it is and distance how close it is to the centre.
    rim       two tiers of arcs outside the rings. The outer, heavier arc is a human: it
              spans their sector, and their name is outside it. Beneath it is a smaller
              arc for each agent they operate, with the agent's mark under its middle: the
              turning dots while it works, ▷ at its prompt, · while it waits. An agent on
              one of its human's own PRs sits over that PR's blip; a blip with no small
              arc over it has no agent, only its human. An agent reviewing someone else's
              PR has a dashed arc with nothing beneath it (that PR's blip is in its
              owner's sector) and its number reads r0009. Outside the arcs: PR numbers.
    blips     exactly one per PR, in its ring: blue ● idle and you can fix it; the turning
              dots on a small lit patch where an agent is at work; ○, then ◎ after a day,
              then ◉ after four, as an idle PR sits longer.
    selection one thin line from the centre through the selected PR's blip. Its number is
              lit, and so is the arc of every agent on it, wherever that is on the dial.
- The sweep runs only when data arrives (something happens, or you press r). A beam turns
  once round the scope in about two seconds. A blip keeps the old picture until the beam
  reaches its bearing; then a blip that changed flashes, and one that changed ring leaves a
  short trail of dots between where it was and where it is, for a few seconds. Between
  sweeps only the turning dots move.
- Under the scope: the key to the rings, and the selected PR: its owner side, what it
  waits on, then what GitHub says.
- The panel on the right: the hierarchy (repo, main, commit, branch, stack) as a tree, one
  line per PR, with its ring, who (an agent always after its operator's name) and what, and
  how long it has been quiet. Under it, what needs effort, most urgent first.

Every name and number is made up; the things that happen are a short loop.

    .venv/bin/python mockup/option6_radar_v5c.py

Keys: up/down pick a PR in the order of the tree, left/right step round the dial, n next
thing needing effort, o / O re-centre, a / A narrow, escape back to you, e next event now,
r refresh, space pause, m repos on screen (up/down, space toggles, enter closes), l legend,
q quit.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.widgets import Static

from frolic_v5 import (ACCENT, AXIS, CRIT, GOOD, HAIR, INK, INK2, MUTED, PLANE, PR, RAISED,
                       SERIOUS, SHOWN_AT_START, SPIN, STATUS, WARN, Agent, Canvas, Focus, Person, Row5,
                       Segments, Stand, World, age_hours, focuses, fresh_world, happenings, humans,
                       in_focus, needs_effort5, progress_words, repo_status5, reviews, short,
                       stand, top_line5, tree5, write)
from frolic_v5 import shown as shown_as            # "you" for the account running this

FPS = 15                         # ticks a second; the dots turn on every other tick
EVERY = 5.0                      # seconds between the things that happen
SWEEP_S = 2.0                    # how long the beam takes to go round once
FLASH_S = 1.4                    # how long a changed blip and its row stay washed
TRAIL_S = 4.0                    # how long a blip's trail stays
FLASH = ["#5a5a55", "#51514d", "#474744", "#3d3d3a", "#333331", "#2a2a28"]     # a wash, fading
DIM = "#5c5b56"                  # PRs that do not involve the centre
RING = "#66655f"                 # the dotted circles between rings
LIT = "#2f2f2c"                  # behind a blip an agent is working on
CI = {"pass": ("✓", GOOD), "fail": ("✗", CRIT), "run": ("…", WARN), "none": ("·", AXIS)}
BURN = (("○", MUTED), ("◎", INK2), ("◉", INK))        # an idle blip, heavier the longer it sits
WEDGE, GAP = 1.6, 0.8            # slots kept clear at 12 o'clock, and between two humans
MARGIN, VM = 18, 5               # columns and rows outside the rings, for the arcs and labels
MARK_R, AGENT_R = 1.0, 1.6       # rows outside the outer ring: an agent's mark, and its arc over it
HUMAN_R = 2.75                   # ...then the humans' arcs
NUMBER_R, NAME_R = 3.9, 4.9      # ...then the PR numbers, then the humans' names
CHROME = 6                       # rows that are not the scope or the panel


# ---- range: how close a PR is to needing the centre --------------------------------------------

def band_of(here: Stand, centre: str) -> int:
    """0 the centre must act · 1 an agent of theirs is at work on it · 2 someone else's agent
    is · 3 nobody is at work and it waits on another human. On screen these are rings 1–4."""
    if centre in here.ends:
        return 0
    if here.moving:
        return 1 if any(a.operator == centre for a in here.working) else 2
    return 3


def burn(age: str) -> int:
    """How long a blip has sat quiet, as a weight: under a day, under four days, longer."""
    hours = age_hours(age)
    return 0 if hours < 24 else (1 if hours < 96 else 2)


def ring_names(centre: str, tight: int = 0) -> list[str]:
    """What the four rings mean, as seen from the centre, innermost first. `tight` 1 and 2
    are shorter wordings for narrow scopes."""
    whose = "your" if centre == "you" else f"{centre}'s"
    people = "people" if centre == "you" else "others"
    if tight >= 3:
        return [f"needs {centre}", "yours" if centre == "you" else whose, "others'", f"waits on {people}"]
    if tight == 2:
        return [f"needs {centre}", f"{whose} agents", "others'", f"waits on {people}"]
    if tight == 1:
        return [f"needs {centre}", f"{whose} agents", "other agents", f"waits on {people}"]
    return [f"needs {centre}", f"{whose} agents at work", "others at work", f"waits on {people}"]


def length(segments: Segments) -> int:
    return sum(len(text) for text, _ in segments)


def labels(world: World, here: Stand, weight: int) -> tuple[list[Segments], Segments]:
    """What the panel says beside a PR, from the fullest wording to the shortest, and the
    wording to cut short when none fits. It leads with who: an agent after its operator
    while one is at work, else the humans who must act. Then what."""
    dot: tuple[str, str] = (" · ", MUTED)
    prefix: Segments = []
    if here.queued:
        prefix = [("behind ", MUTED), (short(here.waits[0].branch), INK2), dot]
    if here.moving:
        agent = here.working[0]
        who: Segments = [(agent.operator, INK), (" › ", MUTED), (agent.name, INK)]
        if here.queued:
            return [prefix + who], prefix + who
        done, total = here.end_pr.progress
        doing = "reviewing" if agent.role == "review" else here.what
        counted = "" if not total else (f"{done} of {total} {here.end_pr.unit}".strip()
                                        if agent.role == "review" else f"{done} of {total} done")
        short_form = who + [dot, (doing, INK2)]
        return ([short_form + [dot, (counted, INK2)]] if counted else []) + [short_form, who], short_form
    if not here.ends:
        nothing: Segments = [("nothing to do on it", MUTED)]
        return [nothing], nothing
    name = f"bold {INK}" if here.blue else (INK, INK2, INK)[weight] if weight else INK2
    who = [(", ".join(here.ends), name)]
    idle = [a for a in world.on(here.end_pr)
            if a.operator in here.ends and a.state == "at its prompt"]
    at: Segments = [(" › ", MUTED), (f"{idle[0].name} at prompt", INK2)] if idle else []
    if here.queued:
        return [prefix + who + at, prefix + who], prefix + who
    what: Segments = [dot, (here.what, INK2)]
    found = [who + at + what, who + what, who + at, who] if at else [who + what, who]
    return found, who + what


@dataclass
class View:
    """One PR as the scope and the panel draw it."""
    band: int                    # 0–3: the ring it is in, counted from the centre
    moving: bool                 # an agent is at work: the blip is the turning dots
    blue: bool                   # idle, and you can fix it
    burn: int                    # 0, 1, 2: how heavy an idle blip is
    labels: list[Segments]
    cut: Segments                # the wording to cut short when no label fits
    quiet: str
    lit: bool                    # it involves whoever is in focus
    state: str = "local"         # the PR's state on GitHub, for the mark before its name

    @property
    def signature(self) -> tuple:
        return (self.band, self.moving, self.blue, "".join(t for t, _ in self.labels[0]), self.quiet,
                self.state)

    def blip(self, spin: str) -> tuple[str, str]:
        if self.moving:
            return spin, f"bold {INK}"
        if self.blue:
            return "●", ACCENT
        return BURN[self.burn]


def same(a: Focus, b: Focus) -> bool:
    return (a.kind, a.human, a.agent) == (b.kind, b.human, b.agent)


def agent_words(agent: Agent) -> str:
    return {"working": "working", "at its prompt": "at its prompt", "waiting": "waiting"}[agent.state]


# ---- drawing fine lines with braille dots -------------------------------------------------------

BITS = ((0x01, 0x08), (0x02, 0x10), (0x04, 0x20), (0x40, 0x80))     # [dot row][dot column] of a cell


class Dots:
    """Dots finer than a cell: each cell holds two columns of four. Because a cell is about
    twice as tall as it is wide, the dots sit on a square grid, so a circle comes out round."""

    def __init__(self) -> None:
        self.cells: dict[tuple[int, int], list] = {}       # (x, y) -> [bits, style, rank]

    def dot(self, x: float, y: float, style: str, rank: int = 0) -> None:
        i, j = math.floor(x + 0.5), math.floor(y + 0.5)
        column = 1 if x + 0.5 - i >= 0.5 else 0
        row = min(3, int((y + 0.5 - j) * 4))
        cell = self.cells.get((i, j))
        if cell is None:
            self.cells[(i, j)] = [BITS[row][column], style, rank]
            return
        cell[0] |= BITS[row][column]
        if rank >= cell[2]:
            cell[1], cell[2] = style, rank

    def copy(self) -> "Dots":
        out = Dots()
        out.cells = {key: list(value) for key, value in self.cells.items()}
        return out

    def onto(self, canvas: Canvas) -> None:
        for (x, y), (bits, style, _) in self.cells.items():
            canvas.put(x, y, chr(0x2800 + bits), style)


@dataclass
class Scope:
    """The round scope's geometry, in cells. A radius of R rows is 2R columns."""
    R: int                       # the outer ring's radius, in rows
    margin: int                  # columns outside the outer ring, each side
    vm: int                      # rows outside the outer ring, above and below

    @property
    def width(self) -> int:
        return 4 * self.R + 1 + 2 * self.margin

    @property
    def height(self) -> int:
        return 2 * self.R + 1 + 2 * self.vm

    @property
    def cx(self) -> int:
        return 2 * self.R + self.margin

    @property
    def cy(self) -> int:
        return self.R + self.vm

    def at(self, r: float, bearing: float) -> tuple[float, float]:
        """The point r rows from the centre on a bearing (degrees clockwise from 12 o'clock)."""
        angle = math.radians(bearing)
        return self.cx + 2 * r * math.sin(angle), self.cy - r * math.cos(angle)

    def bounds(self) -> list[float]:
        """The outer edge of each ring; the last is the outer ring's, where the arcs begin."""
        return [0.31 * self.R, 0.54 * self.R, 0.77 * self.R, float(self.R)]

    def radii(self, ring: int) -> tuple[float, float]:
        """The two radii blips sit at within a ring; neighbours alternate between them."""
        edges = self.bounds()
        inner = 1.2 if ring == 0 else edges[ring - 1]
        span = edges[ring] - inner
        return inner + 0.38 * span, inner + 0.74 * span

    def arc(self, dots: Dots, r: float, start: float, end: float, step: float, style: str,
            rank: int = 0) -> None:
        """Dots along the circle of radius r between two bearings, one every `step` columns."""
        steps = max(1, round(math.radians(end - start) * 2 * r / step))
        for k in range(steps + (0 if end - start >= 360 else 1)):
            dots.dot(*self.at(r, start + (end - start) * k / steps), style, rank)

    def ray(self, dots: Dots, bearing: float, r0: float, r1: float, step: float, style: str,
            rank: int = 0) -> None:
        """Dots along a bearing between two radii, one every `step` columns."""
        steps = max(1, round(abs(r1 - r0) * 2 / step))
        for k in range(steps + 1):
            dots.dot(*self.at(r0 + (r1 - r0) * k / steps, bearing), style, rank)

    def label_at(self, bearing: float, beyond: float) -> tuple[int, int, str]:
        """Where a label goes `beyond` rows outside the outer ring: (x, row, how it is
        aligned to x). Near 12 and 6 o'clock it is centred; elsewhere it runs outwards."""
        angle = math.radians(bearing)
        s, c = math.sin(angle), math.cos(angle)
        x = round(self.cx + 2 * (self.R + beyond) * s)
        row = round(self.cy - (self.R + beyond) * c)
        if abs(s) < 0.3:
            return x, row, "centre"
        return (x, row, "left") if s > 0 else (x, row, "right")


def size_scope(width: int, main_h: int) -> Scope:
    """The biggest scope the window allows, leaving four rows under it for the selected PR.
    The rim needs room outside the rings: two tiers of arcs, then numbers, then names."""
    detail = 4
    panel = 60 if width >= 140 else 44
    by_height = (main_h - 1 - detail - (2 * VM + 1)) // 2
    by_width = (width - 4 - panel - 1 - 2 * MARGIN) // 4
    radius = min(17, by_height, by_width)
    if radius >= 7:
        return Scope(radius, MARGIN, VM)
    radius = max(4, min((main_h - 1 - detail - 7) // 2, (width - 4 - panel - 1 - 14) // 4))
    return Scope(radius, 7, 3)                             # the arcs, but no room for labels


@dataclass
class Slot:
    """One bearing in a human's sector."""
    kind: str                    # "pr": a PR that human owns; its blip is beneath
                                 # "review": an agent of theirs on someone else's PR; nothing beneath
    human: str
    number: int                  # the PR
    agents: list[Agent] = field(default_factory=list)      # that human's agents on that PR
    bearing: float = 0.0

    @property
    def label(self) -> str:
        return f"r{self.number:04d}" if self.kind == "review" else f"{self.number:04d}"

    def spans(self, delta: float) -> list[tuple[float, float]]:
        """Where each agent's arc starts and ends: the slot shared out, with clear gaps."""
        share = delta / max(1, len(self.agents))
        left = self.bearing - delta / 2
        return [(left + (i + 0.125) * share, left + (i + 0.875) * share) for i in range(len(self.agents))]


@dataclass
class Sector:
    """One human's part of the dial."""
    human: str
    edge: float                  # the boundary before it
    start: float                 # where its outer arc starts
    end: float                   # ...and ends
    idle: bool                   # this human has nothing to do
    slots: list[Slot] = field(default_factory=list)

    @property
    def middle(self) -> float:
        return (self.start + self.end) / 2


@dataclass
class Plan:
    """Who is where round the rim, for the repos on screen."""
    delta: float                                           # one slot, in degrees
    sectors: list[Sector] = field(default_factory=list)
    bearing: dict[int, float] = field(default_factory=dict)       # PR number -> its blip's bearing
    order: list[int] = field(default_factory=list)                # PR numbers, clockwise round the dial
    edges: list[float] = field(default_factory=list)              # the boundaries between humans

    @property
    def signature(self) -> tuple:
        return tuple((s.human, s.idle, tuple((slot.kind, slot.number,
                                              tuple((a.key, a.state) for a in slot.agents))
                                             for slot in s.slots)) for s in self.sectors)


def build_plan(world: World, names: list[str], rows: list[Row5]) -> Plan:
    """Give every human a sector, you first from 12 o'clock and then clockwise in the order
    `humans()` lists them, so nobody moves when the radar is re-centred. Inside a sector: one
    slot per PR that human owns, in the order of the tree (stacks stay together), then one
    slot per agent of theirs that is on someone else's PR. Sectors are as wide as their
    slots; a human with none still gets one slot's width. A wedge at the top stays clear
    for the ring numbers."""
    in_tree = [row.pr for row in rows if row.pr is not None]
    owner = {pr.number: shown_as(pr.author) for pr in in_tree}
    found: list[tuple[Person, list[Slot]]] = []
    for person in humans(world, names):
        slots = [Slot("pr", person.name, pr.number,
                      [a for a in world.on(pr) if a.operator == person.name])
                 for pr in in_tree if owner[pr.number] == person.name]
        slots += [Slot("review", person.name, agent.number, [agent])
                  for agent in person.agents if owner.get(agent.number) != person.name]
        found.append((person, slots))
    plan = Plan(360 / (WEDGE + sum(GAP + max(1, len(slots)) for _, slots in found)))
    at = WEDGE / 2 * plan.delta
    for person, slots in found:
        width = max(1, len(slots)) * plan.delta
        start = at + GAP / 2 * plan.delta
        for i, slot in enumerate(slots):
            slot.bearing = start + (i + 0.5) * plan.delta
            if slot.kind == "pr":
                plan.bearing[slot.number] = slot.bearing
                plan.order.append(slot.number)
        plan.sectors.append(Sector(person.name, at, start, start + width,
                                   not person.has_work and person.name != "you", slots))
        plan.edges.append(at)
        at = start + width + GAP / 2 * plan.delta
    plan.edges.append(at)
    return plan


class RadarV5C(App):
    CSS = f"""
    Screen {{ background: {PLANE}; color: {INK}; layers: base overlay; }}
    Static {{ text-wrap: nowrap; text-overflow: ellipsis; }}
    #top, #ticker, #hints {{ height: 1; padding: 0 1; }}
    #centre {{ height: 3; padding: 0 1; }}
    #main {{ height: 1fr; }}
    #left {{ width: 78; height: 1fr; padding: 0 0 0 1; }}
    #scope {{ height: 33; }}
    #key {{ height: 1; }}
    #detail {{ height: 1fr; }}
    #right {{ width: 1fr; height: 1fr; padding: 0 1; border-left: solid {HAIR}; }}
    #thead {{ height: 1; }}
    #tree {{ height: 1fr; }}
    #effort {{ height: 9; }}
    #ticker, #hints {{ color: {MUTED}; }}
    #menu {{ layer: overlay; display: none; width: 46; height: auto; offset: 3 2;
             background: {RAISED}; border: round {MUTED}; padding: 0 1; }}
    #legend {{ layer: overlay; display: none; width: 76; height: auto; offset: 2 5;
               background: {RAISED}; border: round {MUTED}; padding: 0 1; }}
    """
    BINDINGS = [
        Binding("up", "move(-1)", show=False), Binding("down", "move(1)", show=False),
        Binding("left", "dial(-1)", show=False), Binding("right", "dial(1)", show=False),
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
        self.top = 0                          # first tree row on screen
        self.frame = 0                        # turns the dots
        self.ticks = 0
        self.paused = False
        self.next_at = time.monotonic() + EVERY
        self.refreshed = "2m ago"
        self.said = ""                        # the last thing that happened
        self.said_repo = ""
        self.sweep: float | None = None       # the beam's bearing, while it turns
        self.old_views: dict[int, View] = {}  # the picture before, for blips the beam has not reached
        self.pending: set[int] = set()        # changed PRs the beam has not reached yet
        self.moved: dict[int, int] = {}       # PR -> the ring it was in before
        self.flash: dict[int, float] = {}     # PR -> when the beam passed its changed blip
        self.trails: dict[int, tuple[int, float]] = {}    # PR -> (the ring it left, when)
        self.menu_open = False
        self.legend_open = False
        self.menu_at = 0
        self.scope = Scope(13, MARGIN, VM)
        self.tree_h = 30
        self.effort_h = 9
        self._views: dict[int, View] | None = None
        self._rows: list[Row5] | None = None
        self._plan: Plan | None = None
        self._base: tuple[tuple, Dots] | None = None

    def compose(self) -> ComposeResult:
        yield Static(id="top")
        yield Static(id="centre")
        with Horizontal(id="main"):
            with Vertical(id="left"):
                yield Static(id="scope")
                yield Static(id="key")
                yield Static(id="detail")
            with Vertical(id="right"):
                yield Static(id="thead")
                yield Static(id="tree")
                yield Static(id="effort")
        yield Static(id="ticker")
        yield Static(id="hints")
        yield Static(id="menu")
        yield Static(id="legend")

    def on_mount(self) -> None:
        self.draw()
        self.set_interval(1 / FPS, self.tick)

    def on_resize(self) -> None:
        self.draw()

    # ---- the picture ---------------------------------------------------------------
    @property
    def centre(self) -> str:
        return self.focus.human or "you"

    def touch(self) -> None:
        """The world, the repos on screen or the focus changed: work the picture out again."""
        self._views = None
        self._rows = None
        self._plan = None

    def rows(self) -> list[Row5]:
        if self._rows is None:
            self._rows = tree5(self.world, self.shown)
        return self._rows

    def plan(self) -> Plan:
        if self._plan is None:
            self._plan = build_plan(self.world, self.shown, self.rows())
        return self._plan

    def views(self) -> dict[int, View]:
        if self._views is None:
            found: dict[int, View] = {}
            for pr in self.world.all(self.shown):
                here = stand(self.world, pr)
                weight = 0 if here.moving else burn(pr.age)
                said, cut = labels(self.world, here, weight)
                found[pr.number] = View(band_of(here, self.centre), here.moving, here.blue, weight,
                                        said, cut, pr.age, in_focus(self.world, pr, self.focus), pr.state)
            self._views = found
        return self._views

    def view(self, number: int) -> View:
        """A PR as it shows right now: the old picture until the beam has reached it."""
        if number in self.pending and number in self.old_views:
            return self.old_views[number]
        return self.views()[number]

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

    def layout(self) -> None:
        """Size the scope from the window, then give the tree the rows it needs and what
        needs effort the rest."""
        width, main_h = self.size.width, max(12, self.size.height - CHROME)
        scope = size_scope(width, main_h)
        need = len(self.rows())
        spare = main_h - 1 - need
        effort = min(13, spare) if spare >= 5 else (8 if main_h >= 36 else 5)
        effort = max(3, min(effort, main_h - 1 - 4))
        if (scope, effort) != (self.scope, self.effort_h) or self._base is None:
            self.scope, self.effort_h = scope, effort
            self._base = None
            self.query_one("#left").styles.width = scope.width + 1
            self.query_one("#scope").styles.height = scope.height
            self.query_one("#effort").styles.height = effort
        self.tree_h = max(3, main_h - 1 - effort)

    def panel_w(self) -> int:
        return max(30, self.size.width - (self.scope.width + 1) - 3)

    def window(self) -> tuple[list[Row5], int]:
        """The tree rows on screen, scrolled so the selected PR is among them."""
        rows, height = self.rows(), self.tree_h
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
            self.sweep += 360 / (SWEEP_S * FPS)
            self.reveal(now)
            if self.sweep >= 372:                         # the beam is back at 12 o'clock
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
                self.query_one("#scope", Static).update(self.scope_text())
                self.query_one("#tree", Static).update(self.tree_text())
        except NoMatches:                                 # a last tick while the app is closing
            pass

    def reveal(self, now: float) -> None:
        """The beam shows the new picture bearing by bearing: a changed blip starts its
        flash, and one that changed ring starts its trail, as the beam passes."""
        bearings = self.plan().bearing
        for number in list(self.pending):
            if number in bearings and bearings[number] > (self.sweep or 0.0):
                continue
            self.pending.discard(number)
            self.flash[number] = now
            if number in self.moved:
                self.trails[number] = (self.moved.pop(number), now)

    def start_sweep(self, old: dict[int, View], changed: set[int]) -> None:
        new = self.views()
        self.old_views = {**old, **{n: v for n, v in self.old_views.items() if n in self.pending}}
        self.pending = {n for n in self.pending | changed if n in new}     # a sweep may still be in flight
        self.moved.update({n: old[n].band for n in changed if n in old and n not in self.moved})
        self.moved = {n: was for n, was in self.moved.items() if n in new and new[n].band != was}
        self.sweep = 0.0
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
    def draw(self) -> None:
        try:
            self.layout()
            self.query_one("#top", Static).update(self.top_text())
            self.query_one("#centre", Static).update(self.centre_text())
            self.query_one("#scope", Static).update(self.scope_text())
            self.query_one("#key", Static).update(self.key_text())
            pr = self.selected()
            rows = max(1, self.size.height - CHROME - self.scope.height - 1)
            self.query_one("#detail", Static).update(
                self.detail_text(pr, rows) if pr is not None
                else Text("no open pull requests or local branches in the repos on screen", style=MUTED))
            self.query_one("#thead", Static).update(self.thead_text())
            self.query_one("#tree", Static).update(self.tree_text())
            self.query_one("#effort", Static).update(self.effort_text())
            self.query_one("#ticker", Static).update(self.ticker_text())
            self.query_one("#hints", Static).update(Text(
                "↑↓ PR  ←→ round the dial  n next needing effort  o O re-centre  a A narrow  esc you  "
                "e next event  r refresh  space pause  m repos  l legend  q quit"))
            menu = self.query_one("#menu", Static)
            menu.display = self.menu_open
            if self.menu_open:
                menu.update(self.menu_text())
            legend = self.query_one("#legend", Static)
            legend.display = self.legend_open
            if self.legend_open:
                legend.update(self.legend_text())
        except NoMatches:                                 # drawn while the app is closing
            pass

    def top_text(self) -> Text:
        return top_line5(self.world, self.shown, frame=self.frame, refreshed=self.refreshed,
                         paused=self.paused)

    def centre_text(self) -> Text:
        """Whose radar this is, in a block; the humans to re-centre on; and beneath, the
        agents the centre operates."""
        width = max(60, self.size.width - 2)
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

    # ---- the scope -----------------------------------------------------------------
    def base_dots(self) -> Dots:
        """What does not change from frame to frame: the dotted circles between the rings,
        and outside them the rim: a tick where one human's sector ends and the next starts,
        an arc two dots thick per human, and beneath it an arc one dot thick per agent."""
        sc, plan = self.scope, self.plan()
        key = (sc.R, sc.margin, self.centre, plan.signature)
        if self._base is not None and self._base[0] == key:
            return self._base[1]
        dots = Dots()
        edges = sc.bounds()
        for r in edges[:3]:
            sc.arc(dots, r, 0, 360, 1.25, RING)
        sc.arc(dots, edges[3], 0, 360, 1.25, RING, 1)
        for edge in plan.edges:                            # between two humans
            sc.ray(dots, edge, sc.R + 0.6, sc.R + HUMAN_R + 0.3, 0.4, MUTED, 2)
        for sector in plan.sectors:
            style = INK if sector.human == self.centre else INK2
            for lift in (-0.125, 0.125):                   # two lines of dots: the heavier arc
                sc.arc(dots, sc.R + HUMAN_R + lift, sector.start, sector.end, 0.35, style, 4)
            for slot in sector.slots:
                for agent, (start, end) in zip(slot.agents, slot.spans(plan.delta)):
                    self.agent_arc(dots, slot, start, end, INK if agent.working else INK2, 3)
        self._base = (key, dots)
        return dots

    def agent_arc(self, dots: Dots, slot: Slot, start: float, end: float, style: str, rank: int,
                  heavy: bool = False) -> None:
        """One agent's arc, beneath its human's: a line one dot thick, dashed for an agent
        that reviews someone else's PR. `heavy` doubles it: an agent on the selected PR."""
        sc = self.scope
        for lift in ((-0.125, 0.125) if heavy else (0.0,)):
            r = sc.R + AGENT_R + lift
            steps = max(2, round(math.radians(end - start) * 2 * r / 0.45))
            for k in range(steps + 1):
                if slot.kind == "review" and k % 4 >= 2:   # two dots on, two off
                    continue
                dots.dot(*sc.at(r, start + (end - start) * k / steps), style, rank)

    def blip_cells(self) -> dict[int, tuple[int, int]]:
        """The cell of every PR's blip: on its bearing, in its ring, alternate bearings on
        the ring's two radii, and never two in one cell."""
        sc, plan = self.scope, self.plan()
        name = self.centre
        taken = {(sc.cx - len(name) // 2 + i, sc.cy) for i in range(-1, len(name) + 1)}
        taken |= {cell for cell, _ in self.numerals()}
        found: dict[int, tuple[int, int]] = {}
        for i, number in enumerate(plan.order):
            ring = self.view(number).band
            near, far = sc.radii(ring)
            first, second = (near, far) if i % 2 == 0 else (far, near)
            mid = (near + far) / 2
            for r in (first, second, mid, first - 0.5, second + 0.5):
                x, y = sc.at(r, plan.bearing[number])
                cell = (round(x), round(y))
                if cell not in taken:
                    break
            taken.add(cell)
            found[number] = cell
        return found

    def numerals(self) -> list[tuple[tuple[int, int], str]]:
        """The ring numbers, at 12 o'clock, each in the middle of its ring."""
        sc = self.scope
        return [((sc.cx, sc.cy - round(sum(sc.radii(ring)) / 2)), str(ring + 1)) for ring in range(4)]

    def blip_radius(self, number: int, ring: int) -> float:
        near, far = self.scope.radii(ring)
        return near if self.plan().order.index(number) % 2 == 0 else far

    def scope_text(self) -> Text:
        sc, plan = self.scope, self.plan()
        canvas = Canvas(sc.width, sc.height)
        now = time.monotonic()
        selected = self.selected()
        chosen = selected.number if selected is not None else None
        dots = self.base_dots().copy()
        for sector in plan.sectors:                        # every agent on the selected PR: its arc lit
            for slot in sector.slots:
                if slot.number == chosen:
                    for start, end in slot.spans(plan.delta):
                        self.agent_arc(dots, slot, start, end, INK, 6, heavy=True)
        for number, (was, _) in self.trails.items():       # where a blip came from
            if number in plan.bearing and self.view(number).band != was:
                sc.ray(dots, plan.bearing[number], self.blip_radius(number, was),
                       self.blip_radius(number, self.view(number).band), 1.0, INK, 4)
        if chosen in plan.bearing:                         # the selected PR's bearing
            sc.ray(dots, plan.bearing[chosen], 1.5, sc.R - 0.25, 0.5, MUTED, 5)
        if self.sweep is not None and self.sweep < 360:    # the beam, and its afterglow
            sc.ray(dots, self.sweep - 9, 1.5, sc.R - 0.25, 2.0, RING, 6)
            sc.ray(dots, self.sweep, 1.5, sc.R - 0.25, 0.4, INK, 7)
        dots.onto(canvas)

        for (x, y), numeral in self.numerals():
            canvas.put(x, y, numeral, MUTED)
        name = self.centre
        canvas.put(sc.cx - len(name) // 2 - 1, sc.cy, f" {name} ", f"bold {INK}")

        cells = self.blip_cells()
        for i, number in enumerate(plan.order):
            view = self.view(number)
            x, y = cells[number]
            mark, color = view.blip(SPIN[(self.frame + i * 3) % len(SPIN)])
            canvas.put(x, y, mark, color if view.lit else DIM)
            if view.moving and view.lit:                   # a small lit patch: an agent is at work
                canvas.paint(x - 1, y, 3, LIT)
            since = now - self.flash.get(number, -99.0)
            if 0 <= since < FLASH_S:
                canvas.put(x, y, mark, color if color.startswith("bold") else f"bold {color}")
                canvas.paint(x - 1, y, 3, FLASH[min(len(FLASH) - 1, int(since / FLASH_S * len(FLASH)))])

        turn = 0
        for sector in plan.sectors:                        # each agent's mark, under the middle of its arc
            for slot in sector.slots:
                for agent, (start, end) in zip(slot.agents, slot.spans(plan.delta)):
                    x, y = sc.at(sc.R + MARK_R, (start + end) / 2)
                    turn += 1
                    mark, style = self.agent_mark(agent, SPIN[(self.frame + turn * 3) % len(SPIN)],
                                                  slot.number == chosen)
                    canvas.put(round(x), round(y), mark, style)
                    if agent.working:
                        canvas.paint(round(x), round(y), 1, LIT)
        if sc.vm >= VM:
            self.rim_labels(canvas, now, set(dots.cells))
        return canvas.text()

    def agent_mark(self, agent: Agent, spin: str, lit: bool) -> tuple[str, str]:
        """An agent's own mark: the turning dots while it works, ▷ at its prompt, · while it
        waits. None of it is blue: where you can fix something, the blip beneath says so."""
        if agent.working:
            return spin, f"bold {INK}"
        if agent.state == "at its prompt":
            return "▷", f"bold {INK}" if lit or agent.operator == "you" else INK2
        return "·", f"bold {INK}" if lit else INK2

    def rim_labels(self, canvas: Canvas, now: float, drawn: set[tuple[int, int]]) -> None:
        """Outside the arcs: each slot's PR number at its bearing, and each human's name by
        the middle of their arc. The selected PR's number goes first and always shows; then
        the other numbers, and one that would run into another is left out; then the names,
        which always show, a little further out and on a row clear of the numbers."""
        sc, plan = self.scope, self.plan()
        taken: set[tuple[int, int]] = set()

        def place(segments: Segments, bearings: list[float], beyond: float, force: bool = False,
                  roam: bool = False) -> bool:
            size = length(segments)
            gap = 2 if roam else 1                         # a name keeps clear of the numbers
            for anyhow in ((False, True) if force else (False,)):
                for bearing in bearings:
                    x, row, align = sc.label_at(bearing, beyond)
                    angle = math.radians(bearing)
                    out_x = 0 if align == "centre" else (1 if math.sin(angle) > 0 else -1)
                    out_y = 0 if abs(math.cos(angle)) < 0.3 else (-1 if math.cos(angle) > 0 else 1)
                    tries = [(0, 0)] + ([(0, out_y)] if out_y else []) + ([(out_x, 0)] if out_x else [])
                    if roam:
                        tries += [(0, -1), (0, 1), (out_x, -1), (out_x, 1), (2 * out_x, 0),
                                  (-2, 0), (2, 0), (0, -2), (0, 2), (-4, 0), (4, 0)]
                    for dx, dy in tries:
                        start = (x if align == "left" else x - size + 1 if align == "right"
                                 else x - size // 2) + dx
                        y = row + dy
                        if not (0 <= y < sc.height and 0 <= start and start + size <= sc.width):
                            continue
                        inner = {(start + i, y) for i in range(size)}
                        around = {(start + i, y) for i in range(-gap, size + gap)}
                        if not anyhow and (around & taken or inner & drawn):
                            continue
                        write(canvas, start, y, segments, size)
                        taken.update(inner)
                        return True
            return False

        selected = self.selected()
        chosen = selected.number if selected is not None else None
        slots = [slot for sector in plan.sectors for slot in sector.slots]
        for slot in slots:                                 # the selected PR, wherever it is named
            if slot.number == chosen:
                place([(slot.label, f"bold {INK} on {RAISED}")], [slot.bearing], NUMBER_R, force=True)
        for slot in slots:
            if slot.number == chosen:
                continue
            view = self.view(slot.number)
            flashing = slot.kind == "pr" and 0 <= now - self.flash.get(slot.number, -99.0) < FLASH_S
            style = f"bold {INK}" if flashing else (MUTED if view.lit else DIM)
            if not place([(slot.label, style)], [slot.bearing], NUMBER_R) and slot.kind == "review":
                place([(f"r{slot.number}", style)], [slot.bearing], NUMBER_R)      # r13: shorter, still marked
        for sector in plan.sectors:
            centre = sector.human == self.centre
            said: Segments = [("● ", ACCENT)] if sector.idle else []
            said.append((sector.human, f"bold {INK}" if centre or sector.idle else f"bold {INK2}"))
            beside = abs(math.sin(math.radians(sector.middle))) >= 0.3
            if beside and len(sector.slots) % 2:           # between two numbers' rows, not on one
                bearings = [sector.middle + plan.delta / 2, sector.middle - plan.delta / 2, sector.middle]
            else:
                bearings = [sector.middle]
            place(said, bearings, NAME_R, force=True, roam=True)

    def key_text(self) -> Text:
        """The rings, spelled out: the one line of words the scope needs."""
        width = self.scope.width
        for tight in (0, 1, 2, 3):
            names = ring_names(self.centre, tight)
            text = Text("" if tight == 3 else "rings ", style=MUTED, no_wrap=True)
            for i, name in enumerate(names):
                text.append((" " if tight == 3 else "  ") if i else "", style=MUTED)
                text.append(f"{i + 1} ", style=f"bold {INK}")
                text.append(name, style=INK2)
            if text.cell_len <= width:
                break
        return text

    def detail_text(self, pr: PR, rows: int) -> Text:
        """The selected PR, under the scope: its owner side and what it waits on, in plain
        words, then what GitHub says, then every agent on it."""
        world = self.world
        here = stand(world, pr)
        icon, color, label, _ = STATUS[pr.state]
        lines: list[Text] = []
        head = Text(no_wrap=True)
        head.append(icon + " ", style=color)
        head.append(pr.name, style=f"bold underline {INK}")
        head.append(f"  {label} · {world.repo_of(pr)}", style=INK2)
        lines.append(head)

        def kv(key: str, value: Text) -> None:
            lines.append(Text(f"{key:<9}", style=MUTED, no_wrap=True).append_text(value))

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
        waits.append("  ·  to do: ", style=MUTED).append(here.what or "nothing", style=INK)
        waits.append(f", {progress_words(world, pr)}", style=INK2)
        kv("waits on", waits)

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
        if pr.threads[1]:
            opened, total = pr.threads
            said.append("  ·  ", style=MUTED)
            said.append(f"{opened} of {total} comments to resolve" if opened
                        else f"all {total} comments resolved", style=INK if opened else INK2)
        kv("reviews", said)
        ci_icon, ci_color = CI[pr.ci]
        checks = Text(ci_icon + " ", style=ci_color).append(pr.ci_note or "no checks yet", style=INK)
        checks.append("   merge ", style=MUTED)
        if "conflict" in pr.merge:
            checks.append("⚠ ", style=CRIT)
        checks.append(pr.merge, style=INK if "conflict" in pr.merge else INK2)
        kv("checks", checks)
        on_it = world.on(pr)
        if not on_it:
            kv("agents", Text("none on it", style=INK2))
        for i, agent in enumerate(on_it):
            line = Text(f"{agent.operator} › {agent.name}", style=INK)
            line.append(f" · {'reviewing' if agent.role == 'review' else 'on it'} · {agent_words(agent)}",
                        style=INK2)
            if agent.odd_name:
                line.append(" ≠ ", style=f"bold {CRIT}").append(f"session is {agent.odd_name}", style=INK2)
            if agent.note:
                line.append(f" · {agent.note}", style=MUTED)
            kv("agents" if i == 0 else "", line)
        if pr.title:
            kv("title", Text(pr.title, style=INK2))
        out = Text(no_wrap=True)
        for i, line in enumerate(lines[:rows]):
            out.append("\n" if i else "").append_text(line)
        return out

    # ---- the panel -----------------------------------------------------------------
    def columns(self) -> tuple[int, int, bool]:
        """(where the ring number goes, where quiet goes, whether the full branch key fits)."""
        width = self.panel_w()
        prs = [row for row in self.rows() if row.pr is not None]
        full = max((len(row.lead) + 2 + len(row.pr.key) for row in prs), default=20) + 2
        brief = max((len(row.lead) + 2 + len(short(row.pr)) for row in prs), default=14) + 2
        quiet = width - 5
        if quiet - 1 - (full + 4) >= 38:
            return full, quiet, True
        return min(brief, max(12, width - 30)), quiet, False

    def thead_text(self) -> Text:
        width = self.panel_w()
        ring_x, quiet_x, _ = self.columns()
        canvas = Canvas(width, 1)
        rows = self.rows()
        said = "repo → main → commit → branch → stack"
        if len(said) > ring_x - 2:
            said = "the tree"
        if len(rows) > self.tree_h:
            said = f"rows {self.top + 1}–{min(len(rows), self.top + self.tree_h)} of {len(rows)}"
        canvas.put(0, 0, said[:max(0, ring_x - 1)], MUTED)
        canvas.put(ring_x, 0, "ring  who · what", MUTED)
        canvas.put(quiet_x, 0, "quiet", MUTED)
        return canvas.text()

    def tree_text(self) -> Text:
        """The hierarchy as a tree, one line per PR: the tree, the ring its blip is in, who
        and what, and how long it has been quiet."""
        width = self.panel_w()
        ring_x, quiet_x, full = self.columns()
        shown, height = self.window()
        selected = self.selected()
        now = time.monotonic()
        canvas = Canvas(width, max(1, height))
        if not self.branches() and len(shown) < height:
            canvas.put(2, len(shown), "no open pull requests or local branches", MUTED)
        for y, row in enumerate(shown):
            if row.kind == "repo":
                canvas.put(0, y, "━" * width, AXIS)
                canvas.put(2, y, f" {row.label} ", f"bold {INK}")
            elif row.kind == "base":
                canvas.put(0, y, row.lead, AXIS)
                x = len(row.lead)
                canvas.put(x, y, row.label, f"bold {INK2}")
                x += len(row.label) + 1
                if row.note and width - x > len(row.note) + 6:
                    x += write(canvas, x, y, [(row.note, MUTED)], width - x - 4) + 1
                canvas.put(x, y, "─" * max(0, width - x), HAIR)
            elif row.kind == "commit":
                canvas.put(0, y, row.lead[:-2], AXIS)
                canvas.put(len(row.lead) - 2, y, row.lead[-2:], MUTED)
                write(canvas, len(row.lead), y, [(row.sha, INK2), (" " + row.label, MUTED)],
                      width - len(row.lead))
            else:
                pr = row.pr
                view = self.view(pr.number)
                aside = "" if view.lit else DIM
                icon, color, _, _ = STATUS[view.state]
                canvas.put(0, y, row.lead, AXIS)
                x = len(row.lead)
                canvas.put(x, y, icon, aside or color)
                write(canvas, x + 2, y, [(pr.key if full else short(pr), f"bold {INK}")], ring_x - x - 3, aside)
                canvas.put(ring_x, y, str(view.band + 1), aside or MUTED)
                mark, mark_color = view.blip(SPIN[(self.frame + y * 3) % len(SPIN)])
                canvas.put(ring_x + 2, y, mark, aside or mark_color)
                room = quiet_x - 1 - (ring_x + 4)
                write(canvas, ring_x + 4, y, self.wording(view, room), room, aside)
                heavy = not view.moving and view.burn == 2
                canvas.put(quiet_x, y, view.quiet.rjust(5),
                           aside or (f"bold {INK}" if heavy else INK2 if view.burn and not view.moving else MUTED))
                wash = RAISED if pr is selected else ""
                since = now - self.flash.get(pr.number, -99.0)
                if 0 <= since < FLASH_S:
                    wash = FLASH[min(len(FLASH) - 1, int(since / FLASH_S * len(FLASH)))]
                if wash:
                    canvas.paint(0, y, width, wash)
        return canvas.text()

    def wording(self, view: View, room: int) -> Segments:
        """The fullest wording that fits; else who and what, cut short; else who alone."""
        for said in view.labels[:-1] or view.labels:
            if length(said) <= room:
                return said
        who = view.labels[-1]
        return view.cut if length(who) + 11 <= room else who

    def effort_text(self) -> Text:
        """What needs effort to get moving, most urgent first. Each line leads with who must
        act, then the PR, then why it is urgent, then the move."""
        width, room = self.panel_w(), self.effort_h - 1
        text = Text(no_wrap=True)
        title = " needs effort to get moving · most urgent first "
        text.append("──", style=HAIR).append(title, style=f"bold {INK2}")
        text.append("─" * max(0, width - 2 - len(title)) + "\n", style=HAIR)
        found = needs_effort5(self.world, self.shown)
        if not found:
            return text.append("nothing: everything that can move is moving"
                               if self.branches() else "nothing open", style=MUTED)
        fits = room if len(found) <= room else room - 1
        who_w = min(11, max(len(e.who) for e in found))
        for rank, effort in enumerate(found[:fits], start=1):
            line = Text(f"{rank:<2}", style=MUTED, no_wrap=True)
            line.append("● " if effort.blue else "○ ", style=ACCENT if effort.blue else MUTED)
            who = effort.who if len(effort.who) <= who_w else effort.who[:who_w - 1] + "…"
            line.append(f"{who:<{who_w}} ", style=f"bold {INK}" if effort.blue else INK2)
            line.append(f"{short(effort.pr)}  ", style=INK)
            urgency = Text()
            if effort.waiting:
                held, quiet = effort.urgency.split(" · ", 1)
                urgency.append(held, style=f"bold {INK}").append(" · ", style=MUTED)
            else:
                quiet = effort.urgency
            urgency.append("just now" if quiet == "quiet now" else quiet,
                           style=f"bold {INK}" if burn(effort.pr.age) == 2 else INK2)
            line.append_text(urgency).append(" " * max(2, 27 - urgency.cell_len))
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
            text.append(f"  and {len(found) - fits} more: n steps through them", style=MUTED)
        return text

    def legend_text(self) -> Text:
        m = MUTED
        lines: list[Segments] = [
            [("the scope", f"bold {INK}")],
            [("centre   ", INK2), ("a human: you, unless you re-centre with o / O", m)],
            [("rings    ", INK2), ("how close a PR is to needing that human, 1 closest:", m)],
            [("         ", m), ("1 they must act · 2 an agent of theirs is at work", m)],
            [("         ", m), ("3 someone else's agent is · 4 nobody is, it waits on others", m)],
            [("bearing  ", INK2), ("whose it is. Every human has a sector of the dial, and a", m)],
            [("         ", m), ("PR's blip sits in its owner's sector. One PR, one blip.", m)],
            [("", m)],
            [("the rim: two tiers of arcs", f"bold {INK}")],
            [("outer    ", INK2), ("the heavier arc is a human, with their name outside it", m)],
            [("beneath  ", INK2), ("a smaller arc for each agent they operate, marked:", m)],
            [("         ", m), (SPIN[2] + " ", INK), ("at work   ", m), ("▷ ", INK), ("at its prompt   ", m),
             ("· ", INK2), ("waiting", m)],
            [("         ", m), ("An arc over a blip: that agent is on that PR. No small", m)],
            [("         ", m), ("arc over a blip: no agent is on it, only its human.", m)],
            [("r0009    ", INK2), ("a dashed arc: an agent reviewing someone else's PR.", m)],
            [("         ", m), ("That PR's blip is in its owner's sector, not beneath.", m)],
            [("", m)],
            [("the blips", f"bold {INK}")],
            [(SPIN[2] + " ", INK), ("an agent is at work on it right now", m)],
            [("● ", ACCENT), ("idle, and you can fix it. Blue is only this.", m)],
            [("○ ", MUTED), ("idle under a day   ", m), ("◎ ", INK2), ("a day or more   ", m),
             ("◉ ", INK), ("four days or more", m)],
            [("", m)],
            [("when data arrives", f"bold {INK}")],
            [("A beam turns once round the scope. A blip that changed flashes as", m)],
            [("the beam passes; one that changed ring leaves a trail of dots", m)],
            [("for a few seconds. Nothing else moves but the turning dots.", m)],
            [("", m)],
            [("l closes this", m)],
        ]
        text = Text(no_wrap=True)
        for i, segments in enumerate(lines):
            text.append("\n" if i else "")
            for part, style in segments:
                text.append(part, style=style)
        return text

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

    def action_dial(self, step: int) -> None:
        """Step round the dial, blip by blip: right is clockwise. Slots with no blip (an
        agent reviewing someone else's PR) are passed over."""
        order = self.plan().order
        if self.menu_open or not order:
            return
        at = order.index(self.sel) if self.sel in order else (-1 if step > 0 else 0)
        self.sel = order[(at + step) % len(order)]
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
        if self.menu_open or self.legend_open:
            self.menu_open = self.legend_open = False
            self.draw()
        elif self.focus.kind != "all":
            self.refocus(Focus())

    def action_event(self) -> None:
        self.happen()

    def action_refresh(self) -> None:
        """A manual refresh: the beam goes round, and nothing has changed."""
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
        self.legend_open = not self.legend_open
        self.draw()


if __name__ == "__main__":
    RadarV5C().run()
