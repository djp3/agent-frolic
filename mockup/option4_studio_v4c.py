"""Agent Frolic mock-up, option 4 v4c: the building, with the occupant's name on every door.

The repo is one floor of a building, drawn as a plan with walls and doors.

- THE CORRIDOR is main. It runs down the left, and each commit that something hangs from is
  a junction in it, named by its short hash. Another base (release-0.2) is a WING: a passage
  that opens off the corridor at the commit it was cut from, with its own junction and rooms.
- A ROOM is a branch, numbered by its PR number. A stack is a SUITE: the room nearest the
  corridor is the one nearest main, and each room stacked on it lies behind it, reached only
  through the door (╎) of the room before. So the plan still reads commit, then branch, then
  stack, and every room of every base is on the page at once.
- THE NAMEPLATE is the first thing read on a door, in this order: the OCCUPANT (your agent on
  the branch, "no agent", or the person whose branch it is), then who they WAIT ON (nothing
  while the agent works; otherwise the chain read left to right, "pr-0024 → mira"), and only
  then the room's name.
- A room is LIT while an agent is at work in it, and only then. A dark room is idle. Blue
  marks only what is idle and yours to fix: a VACANT room (the move is yours and no agent is
  in it), an agent standing AT THE DOOR (its session sits at its prompt, waiting for you),
  and a room of someone else's that waits on you.
- THE LOBBY DIRECTORY, the board by the entrance, raises who to the top of the screen: each
  agent of yours with its room and what it is doing, the vacant rooms, then you and the
  other people (has work or nothing to do, and the rooms that wait on them, by name). Pick
  an occupant there and their rooms are outlined in place on the plan; nothing is regrouped.
- THE CARETAKER'S ROUNDS list what needs effort, most urgent first (is an agent waiting on
  it, then how long it has been quiet). The same numbers hang over the doors.
- Fittings: dust (░) gathers in a room the longer it is quiet; a failing check is an
  out-of-order sign (✗); review and checks sit on the room's line where there is width.

Nothing moves by itself except the turning dots where an agent works. Every name and number
is made up.

    .venv/bin/python mockup/option4_studio_v4c.py

Keys: left/right walk through a suite (toward main / away from it), up/down move between
rooms, n goes to the next stop on the rounds, o / O pick the next / previous occupant in the
lobby directory (esc clears the pick), tab/shift+tab switch repo, q quits.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.css.query import NoMatches
from textual.widgets import Static

from frolic_v4 import (ACCENT, AXIS, CRIT, GOOD, INK, INK2, ME, MUTED, PLANE, PR, RAISED, REPOS,
                       SPIN, STATUS, WARN, WORD, Canvas, Effort, Member, Row, Segments, age_hours,
                       cast, is_stale, needs_effort, owned_by, owner, owner_segments,
                       pipe, sha, short, top_line, tree_rows, verdicts, waits, waits_segments,
                       write)

LIT, DARK = "#2e2e2b", "#141413"     # a room with an agent at work, and one without
LAMP = "#8f8e86"                     # the ceiling line of a lit room
WALL = "#4d4c47"                     # every other wall
DIM = "#5c5b56"                      # rooms set aside while an occupant is picked
GUT = 10                             # the corridor: a junction mark and a short hash
LEAF_MIN = 33                        # the innermost room of the deepest suite keeps this width
WIDE = 70                            # a room this wide says everything on one line
BAND_MIN = 8                         # rows the rounds and the room pane always keep
FPS = 8
CI = {"pass": ("✓", GOOD), "fail": ("✗", CRIT), "run": ("…", WARN), "none": ("·", AXIS)}

# walls: each cell remembers which ways a wall leaves it, so corners and junctions draw themselves
UP, DOWN, LEFT, RIGHT = 1, 2, 4, 8
GLYPH = {0: " ", UP: "│", DOWN: "│", LEFT: "─", RIGHT: "─", UP | DOWN: "│", LEFT | RIGHT: "─",
         DOWN | RIGHT: "┌", DOWN | LEFT: "┐", UP | RIGHT: "└", UP | LEFT: "┘",
         UP | DOWN | RIGHT: "├", UP | DOWN | LEFT: "┤", LEFT | RIGHT | DOWN: "┬",
         LEFT | RIGHT | UP: "┴", UP | DOWN | LEFT | RIGHT: "┼"}


class Walls:
    def __init__(self) -> None:
        self.at: dict[tuple[int, int], int] = {}

    def across(self, x0: int, x1: int, y: int) -> None:
        for x in range(x0, x1 + 1):
            self.at[(x, y)] = self.at.get((x, y), 0) | (LEFT if x > x0 else 0) | (RIGHT if x < x1 else 0)

    def down(self, x: int, y0: int, y1: int) -> None:
        for y in range(y0, y1 + 1):
            self.at[(x, y)] = self.at.get((x, y), 0) | (UP if y > y0 else 0) | (DOWN if y < y1 else 0)

    def box(self, x0: int, y0: int, x1: int, y1: int) -> None:
        self.across(x0, x1, y0)
        self.across(x0, x1, y1)
        self.down(x0, y0, y1)
        self.down(x1, y0, y1)


def clip(text: str, width: int) -> str:
    return text if len(text) <= width else text[:max(0, width - 1)] + "…"


def cells(segments: Segments) -> int:
    return sum(len(text) for text, _ in segments)


def lay(canvas: Canvas, x: int, y: int, segments: Segments, width: int, fill: str,
        dim: bool = False) -> int:
    """Segments end to end from x on a room's floor, cut at `width` with an ellipsis.
    Returns the cells used."""
    used = 0
    for text, style in segments:
        room = width - used
        if room <= 0:
            break
        if len(text) > room:
            text = text[:max(0, room - 1)] + "…"
        canvas.put(x + used, y, text, f"{DIM if dim else style} on {fill}".strip())
        used += len(text)
    return used


def dust(pr: PR) -> str:
    """Dust gathers the longer a room is quiet: none inside six hours, four specks past a week."""
    hours = age_hours(pr.age)
    return "░" * (0 if hours < 6 else 1 if hours < 24 else 2 if hours < 72 else 3 if hours < 168 else 4)


def review_segments(pr: PR) -> Segments:
    latest = verdicts(pr)
    if not latest:
        return [("·", AXIS)]
    out: Segments = [(f"r{max(e.round for e in latest.values())} ", MUTED)]
    out += [(WORD[e.verdict][0], WORD[e.verdict][1]) for e in latest.values()]
    if pr.threads[0]:
        out += [(" ◆", MUTED), (str(pr.threads[0]), INK2)]
    return out


def checks_segments(pr: PR) -> Segments:
    if pr.ci == "fail":
        return [("✗ ", CRIT), ("out of order", INK2)]
    return [CI[pr.ci]]


# ---- the plan: who is behind whom, and where the walls go ------------------------------------

@dataclass
class Node:
    """A room (a branch) or a wing's passage (a base cut from another base)."""
    row: Row
    level: int                               # 0 opens onto main's corridor
    parent: "Node | None" = None
    kids: list["Node"] = field(default_factory=list)
    doors: list[Row] = field(default_factory=list)      # commits it hangs from, in its corridor
    first: int = 0                           # the bands (rows of innermost rooms) it spans
    last: int = 0
    x0: int = 0                              # its walls on the plan: left, right, top, bottom
    x1: int = 0
    y0: int = 0
    y1: int = 0

    @property
    def pr(self) -> PR | None:
        return self.row.pr


def layout(repo: str) -> tuple[list[Node], int]:
    """tree_rows() turned into rooms: who is behind whom, and which bands each one spans."""
    nodes: list[Node] = []
    open_: list[Node] = []
    waiting_doors: dict[int, list[Row]] = {}
    bands = 0

    def close(level: int) -> None:
        nonlocal bands
        while open_ and open_[-1].level >= level:
            node = open_.pop()
            if node.kids:
                node.first, node.last = node.kids[0].first, node.kids[-1].last
            else:
                node.first = node.last = bands
                bands += 1

    for row in tree_rows(repo):
        if row.kind == "base" and not row.lead:
            continue                         # the trunk is the corridor itself
        level = len(row.lead) // 2 - 1
        close(level)
        if row.kind == "commit":
            waiting_doors.setdefault(level - 1, []).append(row)
            continue
        node = Node(row, level, open_[-1] if open_ else None)
        node.doors = waiting_doors.pop(level - 1, [])
        if node.parent:
            node.parent.kids.append(node)
        nodes.append(node)
        open_.append(node)
    close(0)
    return nodes, bands


class StudioV4C(App):
    CSS = f"""
    Screen {{ background: {PLANE}; color: {INK}; }}
    #top, #hints {{ height: 1; margin: 0 1; text-wrap: nowrap; text-overflow: ellipsis; }}
    #lobby {{ height: auto; margin: 0 1; }}
    #plan {{ height: auto; margin: 1 1 0 1; }}
    #band {{ height: 1fr; margin: 0 1; }}
    #rounds {{ width: 80; height: 1fr; text-wrap: nowrap; text-overflow: ellipsis; }}
    #room {{ width: 1fr; height: 1fr; padding: 0 0 0 2; text-wrap: nowrap; text-overflow: ellipsis; }}
    #key {{ height: 2; margin: 0 1; text-wrap: nowrap; text-overflow: ellipsis; }}
    #hints {{ color: {MUTED}; }}
    """
    BINDINGS = [
        Binding("up", "walk('up')", show=False), Binding("down", "walk('down')", show=False),
        Binding("left", "walk('left')", show=False), Binding("right", "walk('right')", show=False),
        Binding("n", "next_stop", show=False),
        Binding("o", "pick(1)", show=False), Binding("O", "pick(-1)", show=False),
        Binding("escape", "unpick", show=False),
        Binding("tab", "repo(1)", show=False, priority=True),
        Binding("shift+tab", "repo(-1)", show=False, priority=True),
        Binding("q", "quit", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.frame = 0                       # turns the dots where an agent works; nothing else moves
        self.scroll = 0                      # first plan row on screen, when the window is too short
        self.total = 2                       # rows the whole plan takes
        self.load(next(iter(REPOS)))

    def compose(self) -> ComposeResult:
        yield Static(id="top")
        yield Static(id="lobby")
        yield Static(id="plan")
        with Horizontal(id="band"):
            yield Static(id="rounds")
            yield Static(id="room")
        yield Static(id="key")
        yield Static(id="hints")

    def on_mount(self) -> None:
        self.set_interval(1 / FPS, self.tick)
        self.draw()

    def on_resize(self) -> None:
        self.draw()

    # ---- state -------------------------------------------------------------
    def load(self, repo: str) -> None:
        self.repo = repo
        self.prs = REPOS[repo]
        self.nodes, self.bands = layout(repo)
        self.rooms = [n for n in self.nodes if n.pr is not None]
        self.stops: list[Effort] = needs_effort(self.prs)
        self.numbers = {id(e.pr): (i + 1, e) for i, e in enumerate(self.stops)}
        order = {id(n.pr): i for i, n in enumerate(self.rooms)}
        members = cast(self.prs)
        self.tenants: list[Member] = sorted((m for m in members if m.kind in ("agent", "no agent")),
                                            key=lambda m: order.get(id(m.pr), 0))
        self.folk: list[Member] = [m for m in members if m.kind in ("you", "person")]
        self.picked: Member | None = None
        self.scroll = 0
        first = self.room_of(self.stops[0].pr) if self.stops else None
        self.sel: Node | None = first or (self.rooms[0] if self.rooms else None)

    def room_of(self, pr: PR) -> Node | None:
        return next((n for n in self.rooms if n.pr is pr), None)

    def member_of(self, pr: PR) -> Member | None:
        """The directory entry for a room's occupant, if the directory lists one."""
        who = owner(pr)
        if who.kind == "person":
            return next((m for m in self.folk if m.name == who.name), None)
        return next((m for m in self.tenants if m.pr is pr), None)

    def tick(self) -> None:
        """Turn the dots. Nothing here moves the selection or changes what is shown."""
        self.frame += 1
        try:
            self.query_one("#top", Static).update(top_line(self.repo, frame=self.frame))
            self.query_one("#lobby", Static).update(self.lobby())
            self.query_one("#plan", Static).update(self.plan())
        except NoMatches:                    # the app is closing
            pass

    # ---- sizes -------------------------------------------------------------
    def inner(self) -> int:
        return max(60, self.size.width - 2)

    def lobby_rows(self) -> int:
        return max(1, min(7, max((len(self.tenants) + 1) // 2, len(self.folk))))

    def wide(self, node: Node) -> bool:
        return node.pr is not None and not node.kids and node.x1 - node.x0 - 1 >= WIDE

    def place(self) -> None:
        """Give every room its walls. A suite's rooms share walls; the innermost room takes
        the rest of the width. A band is two rows (a wall and one line) when its innermost
        room is wide enough to say everything on one line, and four when a nameplate there
        needs its three lines."""
        if not self.nodes:
            self.total = 2
            return
        levels = max(n.level for n in self.nodes) + 1
        right = self.inner() - 1
        span = right - GUT
        lw = span if levels == 1 else max(14, min(30, (span - LEAF_MIN) // (levels - 1)))
        for node in self.nodes:
            node.x0 = min(right - 2, GUT + node.level * lw)
            node.x1 = right if (node.pr is not None and not node.kids) else min(right, node.x0 + lw)
        heights = [2] * self.bands
        for node in self.nodes:
            if node.pr is not None and node.first == node.last and not self.wide(node):
                heights[node.first] = 4
        tops = [0]
        for height in heights:
            tops.append(tops[-1] + height)
        for node in self.nodes:
            node.y0, node.y1 = tops[node.first], tops[node.last + 1]
        self.total = tops[-1] + 1

    def shown(self) -> int:
        """Rows of the plan on screen: all of it when the window is tall enough."""
        fixed = 1 + (1 + self.lobby_rows()) + 1 + 2 + 1          # top, lobby, gap, key, hints
        return max(4, min(self.total, self.size.height - fixed - BAND_MIN))

    def band_rows(self) -> int:
        fixed = 1 + (1 + self.lobby_rows()) + 1 + 2 + 1
        return max(1, self.size.height - fixed - self.shown())

    # ---- drawing -----------------------------------------------------------
    def draw(self) -> None:
        wide = self.inner() >= 140
        self.query_one("#rounds").styles.width = 80 if wide else 52
        self.query_one("#top", Static).update(top_line(self.repo, frame=self.frame))
        self.query_one("#lobby", Static).update(self.lobby())
        self.query_one("#plan", Static).update(self.plan())
        self.query_one("#rounds", Static).update(self.rounds(wide))
        self.query_one("#room", Static).update(self.pane())
        self.query_one("#key", Static).update(self.key())
        hints = ""
        if self.rooms and self.shown() < self.total:           # only a short window scrolls the plan
            hints = f"plan rows {self.scroll + 1}–{self.scroll + self.shown()} of {self.total}    "
        hints += "←→ through a suite    ↑↓ between rooms    n next on the rounds    o O pick an occupant"
        hints += "    esc clear the pick" if self.picked is not None else ""
        hints += "    ⇥ next repo    q quit"
        self.query_one("#hints", Static).update(hints)

    # ---- the lobby directory -------------------------------------------------
    def lobby(self) -> Text:
        """The board by the entrance: occupant, room, what they are doing. Your agents and the
        vacant rooms in the order of the plan, then you and the other people."""
        inner = self.inner()
        rows = self.lobby_rows()
        canvas = Canvas(inner, rows + 1)
        cw = max(28, min(50, (inner - 48) // 2))
        px = 2 * cw
        canvas.put(0, 0, "lobby directory", f"bold {INK}")
        if self.picked is not None:
            who = self.label(self.picked)
            kind = self.picked.kind
            if not owned_by(self.prs, self.picked):
                note = f"{who}: no room, and no room waits on {'you' if kind == 'you' else who}"
            elif kind == "you":
                note = "outlined below: the rooms that wait on you"
            elif kind == "person":
                note = f"outlined below: the rooms of {who}, and the rooms that wait on {who}"
            elif kind == "no agent":
                note = f"outlined below: the vacant room, {short(self.picked.pr)}"
            else:
                note = f"outlined below: the room of {who}, and the rooms that wait on it"
            canvas.put(17, 0, clip(note, px - 18), INK2)
        else:
            canvas.put(17, 0, clip("your agents · their room · what they are doing", px - 18), MUTED)
        canvas.put(px + 1, 0, clip("people · the rooms that wait on them", inner - px - 1), MUTED)
        at = self.member_of(self.sel.pr) if self.sel is not None and self.sel.pr is not None else None
        if not self.tenants:
            canvas.put(1, 1, "no agent of yours has a room here", MUTED)
        hidden = len(self.tenants) - rows * 2
        for i, member in enumerate(self.tenants[:rows * 2]):
            col, row = divmod(i, rows)
            if hidden > 0 and i == rows * 2 - 1:
                canvas.put(col * cw + 1, row + 1, f"and {hidden + 1} more: o steps through them", MUTED)
                break
            self.entry(canvas, col * cw, row + 1, cw, member, member is at)
        for i, member in enumerate(self.folk[:rows]):
            self.entry(canvas, px, i + 1, inner - px, member, member is at)
        return canvas.text()

    def label(self, member: Member) -> str:
        return f"{member.name} · {short(member.pr)}" if member.pr is not None else member.name

    def entry(self, canvas: Canvas, x: int, y: int, width: int, member: Member, here: bool) -> None:
        if here:
            canvas.put(x, y, "▸", INK2)
        write(canvas, x + 1, y, self.entry_segments(member), width - 1)
        if member is self.picked:
            canvas.paint(x, y, width, RAISED)

    def entry_segments(self, member: Member) -> Segments:
        spin = SPIN[self.frame % len(SPIN)]
        strong = f"bold {INK}"
        if member.kind in ("agent", "no agent"):
            pr = member.pr
            held = pipe(pr)
            mark = {"working": (spin + " ", INK), "at its prompt": ("▷ ", ACCENT),
                    "waiting": ("· ", MUTED)}.get(member.state, ("● ", ACCENT))
            name = strong if member.fixable else (INK if member.state == "working" else INK2)
            out: Segments = [mark, (member.name, name), (" · ", MUTED), (short(pr), INK2), ("  ", "")]
            if member.kind == "no agent":
                return out + [("vacant: ", INK), (held.what if held else "", INK2)]
            if member.state == "working":
                doing = "review notes" if pr.state == "findings" else (held.what if held else "")
                return out + [("at work: ", INK2), (doing, MUTED)]
            if member.state == "at its prompt":
                return out + [("at the door: ", INK), ("waits on you", strong)]
            wait = waits(pr)
            return out + [("waits on ", MUTED), (wait.said if wait else "", INK2)]
        waiting = " ".join(short(pr) for pr in member.waited_by)
        if member.kind == "you":
            if member.waited_by:
                return [("● ", ACCENT), (f"{'you':<8}", strong), (f"{'your move':<14}", INK), (waiting, INK2)]
            return [("  ", ""), (f"{'you':<8}", INK2), ("no room waits on you", MUTED)]
        if member.state == "nothing to do":
            return [("● ", ACCENT), (f"{member.name:<8}", strong), ("nothing to do", INK),
                    ("" if member.owns else " · in the lobby", MUTED)]
        return [("  ", ""), (f"{member.name:<8}", INK2), (f"{'has work':<14}", MUTED), (waiting, INK2)]

    # ---- the plan ------------------------------------------------------------
    def plan(self) -> Text:
        self.place()
        if not self.rooms:
            out = Text(no_wrap=True)
            out.append("main", style=f"bold {INK}")
            out.append("\n┬ ", style=MUTED)
            out.append(sha(self.repo, "main", 0), style=INK2)
            out.append(" tip", style=MUTED)
            out.append("     no rooms: nothing is open in this repo", style=MUTED)
            return out
        inner = self.inner()
        canvas = Canvas(inner, self.total)
        lit_up = ({id(pr) for pr in owned_by(self.prs, self.picked)}
                  if self.picked is not None else None)

        walls = Walls()
        for node in self.nodes:
            if node.pr is None:                                # a wing's passage is open to its corridor
                walls.across(node.x0, node.x1, node.y0)
                walls.across(node.x0, node.x1, node.y1)
            else:
                walls.box(node.x0, node.y0, node.x1, node.y1)
                fill = LIT if owner(node.pr).working else DARK
                for y in range(node.y0 + 1, node.y1):
                    canvas.put(node.x0 + 1, y, " " * (node.x1 - node.x0 - 1), f"on {fill}")
        for (x, y), ways in walls.at.items():
            canvas.put(x, y, GLYPH[ways], WALL)
        for node in self.rooms:                                # the ceiling line of a lit room
            if owner(node.pr).working:
                self.recolor(canvas, range(node.x0 + 1, node.x1), [node.y0], LAMP)
        if lit_up is not None:
            for node in self.rooms:
                if id(node.pr) in lit_up:
                    self.outline(canvas, node, MUTED)
        if self.sel is not None:
            self.outline(canvas, self.sel, f"bold {INK}")
        for node in self.rooms:                                # every room is entered by its door
            canvas.put(node.x0, node.y0 + 1, "╎", INK2 if owner(node.pr).working else MUTED)

        self.corridor(canvas)
        for node in self.nodes:
            if node.pr is None:
                self.draw_wing(canvas, node)
        for node in self.rooms:
            aside = lit_up is not None and id(node.pr) not in lit_up
            self.sign(canvas, node, aside)
            self.draw_room(canvas, node, aside)

        rows = self.shown()
        if rows < self.total:                                  # only a short window ever scrolls
            if self.sel is not None:
                if self.sel.y1 >= self.scroll + rows:
                    self.scroll = self.sel.y1 - rows + 1
                self.scroll = min(self.scroll, self.sel.y0)
            self.scroll = max(0, min(self.scroll, self.total - rows))
            canvas.cells = canvas.cells[self.scroll:self.scroll + rows]
            canvas.height = rows
        else:
            self.scroll = 0
        return canvas.text()

    def recolor(self, canvas: Canvas, xs, ys, style: str) -> None:
        for y in ys:
            for x in xs:
                if 0 <= y < canvas.height and 0 <= x < canvas.width:
                    canvas.put(x, y, canvas.cells[y][x][0], style)

    def outline(self, canvas: Canvas, node: Node, style: str) -> None:
        self.recolor(canvas, range(node.x0, node.x1 + 1), [node.y0, node.y1], style)
        self.recolor(canvas, [node.x0, node.x1], range(node.y0, node.y1 + 1), style)

    def corridor(self, canvas: Canvas) -> None:
        """main, down the left: a junction at each commit something hangs from."""
        canvas.put(0, 0, "main", f"bold {INK}")
        trunk = [n for n in self.nodes if n.level == 0 and n.doors]
        if not trunk:
            return
        for y in range(trunk[0].y0 + 1, canvas.height):
            canvas.put(0, y, "│", AXIS)
        for i, node in enumerate(trunk):
            door = node.doors[0]
            y = node.y0 + 1
            canvas.put(0, y, "┬" if i == 0 else "┼", MUTED)
            canvas.put(2, y, door.sha, INK2)
            canvas.put(2, y + 1, clip(door.label.replace(" commits", ""), GUT - 3), MUTED)

    def draw_wing(self, canvas: Canvas, node: Node) -> None:
        """A base cut from another base: a passage off the corridor, with no wall between
        them, its name over the opening and its own junctions inside."""
        room = node.x1 - node.x0 - 3
        canvas.put(node.x0 + 2, node.y0, clip(f" {node.row.label} wing ", room), f"bold {INK2}")
        for kid in node.kids:
            for door in kid.doors:
                y = kid.y0 + 1
                canvas.put(node.x0 + 1, y, "┬" if door.behind == 0 else "┼", MUTED)
                canvas.put(node.x0 + 3, y, clip(door.sha, room), INK2)
                canvas.put(node.x0 + 4 + len(door.sha), y,
                           clip(door.label.replace(" commits", ""), room - len(door.sha) - 2), MUTED)

    def sign(self, canvas: Canvas, node: Node, aside: bool = False) -> None:
        """The caretaker's number over the door, with the word for what was found when it is
        yours to fix: vacant, at the door, your move."""
        found = self.numbers.get(id(node.pr))
        if found is None:
            return
        rank, effort = found
        who = owner(node.pr)
        word = ""
        if effort.yours:
            word = ("at the door " if who.state == "at its prompt"
                    else "vacant " if who.kind == "no agent" else "your move ")
        text = f" {rank} {word}"
        if len(text) > node.x1 - node.x0 - 3:
            text = f" {rank} "
        canvas.put(node.x0 + 2, node.y0, text,
                   f"{DIM} on {RAISED}" if aside
                   else f"bold #ffffff on {ACCENT}" if effort.yours else f"{INK} on {AXIS}")

    # ---- one room --------------------------------------------------------------
    def waits_cell(self, pr: PR, spin: str, long: bool) -> Segments:
        """The second thing on a nameplate: who the occupant waits on. While the agent works
        it waits on nothing, and the line says what it is at instead."""
        wait = waits(pr)
        if wait is None:
            return [(f"review notes for {short(pr.parent)}" if pr.parent else "review notes", MUTED)]
        if wait.kind == "working":
            held = pipe(pr)
            if long:
                said = held.what + (f" {held.done}/{held.total}" if held.total else "")
            else:
                said = f"{held.done}/{held.total} {held.unit}" if held.total else held.what
            return [("at work: ", INK2), (said, MUTED)]
        return [("waits on ", MUTED)] + waits_segments(pr, spin)

    def room_cell(self, pr: PR, chosen: bool) -> Segments:
        icon, color, _, _ = STATUS[pr.state]
        number = f"bold {INK}" + (" underline" if chosen else "")
        digits = f"{pr.number:04d}"
        before, after = pr.name.split(digits, 1)
        return [(icon + " ", color), (before, INK2), (digits, number), (after, INK2)]

    def quiet_cell(self, pr: PR, lit: bool, long: bool) -> Segments:
        """Dust and how long it has been quiet. A lit room is in use: it says neither."""
        if lit or not pr.age:
            return []
        specks = dust(pr)
        out: Segments = [(specks + " ", MUTED)] if specks else []
        if long:
            out.append(("quiet ", MUTED))
        return out + [(pr.age, f"bold {INK}" if is_stale(pr.age) else MUTED)]

    def more_cell(self, pr: PR) -> Segments:
        """A taller room has a line to spare: what its agent is doing, or the next move."""
        who = owner(pr)
        held = pipe(pr)
        if who.working and who.worker is not None:
            return [(who.worker.note, MUTED)]
        if held is None:
            return [(pr.merge, MUTED)]
        return [("next: ", MUTED), (f"{', '.join(held.who)} · {held.what}", MUTED)]

    def draw_room(self, canvas: Canvas, node: Node, dim: bool) -> None:
        pr = node.pr
        lit = owner(pr).working
        fill = LIT if lit else DARK
        width = node.x1 - node.x0 - 1
        lines = node.y1 - node.y0 - 1
        if width < 6 or lines < 1:
            return
        usable = width - (2 if width >= 40 else 1)             # a cell of floor inside the door
        left, top = node.x0 + 2, node.y0 + 1
        spin = SPIN[(self.frame + node.first * 3) % len(SPIN)]
        long = width >= 60
        who = owner_segments(pr, spin)
        wait = self.waits_cell(pr, spin, long)
        name = self.room_cell(pr, node is self.sel)
        quiet = self.quiet_cell(pr, lit, width >= WIDE)
        failing = pr.ci == "fail"

        def put(dx: int, dy: int, segments: Segments, room: int) -> int:
            return lay(canvas, left + dx, top + dy, segments, max(0, room), fill, dim)

        if lines == 1:                                          # a wide room: one line says it all
            put(0, 0, who, 12)
            waits_w = 31 if usable >= 82 else max(12, usable - 13 - 26)
            put(13, 0, wait, waits_w)
            x = 13 + waits_w + 1
            edge = usable
            if quiet and usable >= 96:
                put(edge - cells(quiet), 0, quiet, cells(quiet))
                edge -= cells(quiet) + 2
            if edge - x >= 42 + 1 + 11 + 1 + 14:
                put(x, 0, name, 42)
                put(x + 43, 0, review_segments(pr), 11)
                put(x + 55, 0, checks_segments(pr), 14)
            elif edge - x >= 42 + 1 + 14:
                put(x, 0, name, 42)
                put(x + 43, 0, checks_segments(pr), 14)
            else:
                used = put(x, 0, name, edge - x - (2 if failing else 0))
                if failing:
                    put(x + used + 1, 0, [("✗", CRIT)], 1)
            return

        if lines == 2:
            put(0, 0, who, 12)
            put(13, 0, wait, usable - 13)
            put(0, 1, name, usable)
            return

        put(0, 0, who, usable)                                  # the nameplate: occupant,
        used = put(2, 1, wait, usable - 2)                      # who they wait on,
        if quiet and usable - 2 - used >= cells(quiet) + 1:
            put(usable - cells(quiet), 1, quiet, cells(quiet))
        if long and usable >= 42 + 1 + 11 + 1 + 14:            # and then the room
            put(0, 2, name, 42)
            put(43, 2, review_segments(pr), 11)
            put(55, 2, checks_segments(pr), 14)
        else:
            put(0, 2, name, usable - (2 if failing else 0))
            if failing:
                put(usable - 1, 2, [("✗", CRIT)], 1)
        if lines >= 4:
            put(2, 3, self.more_cell(pr), usable - 2)
        if lines >= 5:
            put(2, 4, review_segments(pr) + [("  ", "")] + checks_segments(pr), usable - 2)

    # ---- the caretaker's rounds ------------------------------------------------
    def rounds(self, wide: bool) -> Text:
        """What needs effort, most urgent first, with the numbers that hang over the doors.
        Each line: who is in the room, what it waits on, and why it is urgent."""
        text = Text()
        text.append("the caretaker's rounds", style=f"bold {INK}")
        text.append("   most urgent first: an agent waiting, then longest quiet\n" if wide
                    else "   most urgent first\n", style=MUTED)
        rows = max(1, self.band_rows() - 1)
        if not self.stops:
            text.append("nothing needs effort: every room that can be lit is lit\n" if self.rooms
                        else "nothing needs effort: nothing is open\n", style=MUTED)
        fits = rows if len(self.stops) <= rows else rows - 1
        for i, stop in enumerate(self.stops[:fits]):
            here = self.sel is not None and stop.pr is self.sel.pr
            who = owner(stop.pr)
            text.append("▸" if here else " ", style=INK)
            text.append(f" {i + 1} ", style=f"bold #ffffff on {ACCENT}" if stop.yours else f"{INK} on {AXIS}")
            text.append(f" {who.name}", style=f"bold {INK}" if here or stop.yours else INK2)
            text.append(" · ", style=MUTED)
            text.append(short(stop.pr), style=INK2)
            text.append(" " * max(1, 19 - len(who.name) - 3 - len(short(stop.pr))))
            if wide:
                if not stop.yours:
                    said = f"waits on {stop.who} · {stop.pipe.what}"
                elif who.state == "at its prompt":
                    said = f"at the door · {stop.pipe.what}"
                elif who.kind == "no agent":
                    said = f"vacant · {stop.pipe.what}"
                else:
                    said = f"waits on you · {stop.pipe.what}"
                text.append(f"{clip(said, 28):<29}", style=INK if stop.yours else INK2)
            if stop.waiting:
                held, quiet = stop.urgency.split(" · ", 1)
                text.append(held, style=f"bold {INK}").append(" · ", style=MUTED)
            else:
                quiet = stop.urgency
            text.append(quiet + "\n", style=f"bold {INK}" if is_stale(stop.pr.age) else INK2)
        if len(self.stops) > fits:
            text.append(f"      and {len(self.stops) - fits} more: n steps through them\n", style=MUTED)
        return text

    # ---- the room you are standing in -------------------------------------------
    def pane(self) -> Text:
        """The selected room in plain words: the occupant first, then what it waits on."""
        text = Text()
        if self.sel is None or self.sel.pr is None:
            return text.append("the building is empty: nothing is open in this repo", style=MUTED)
        pr = self.sel.pr
        who, wait, held = owner(pr), waits(pr), pipe(pr)
        icon, color, label, _ = STATUS[pr.state]
        text.append("room ", style=MUTED)
        for part, style in self.room_cell(pr, False)[1:]:
            text.append(part, style=style)
        text.append("   ")
        text.append(icon, style=color)
        text.append(f" {label}\n", style=INK2)

        def fact(key: str, segments: Segments) -> None:
            text.append(f"{key:<10}", style=MUTED)
            for part, style in segments:
                text.append(part, style=style)
            text.append("\n")

        strong = f"bold {INK}"
        if who.kind == "agent":
            note = who.worker.note if who.worker is not None else ""
            fact("occupant", {
                "working": [("⠋ ", INK), (who.name, strong), (f", at work: {note}", INK2)],
                "at its prompt": [("▷ ", ACCENT), (who.name, strong),
                                  (", at the door: its session is at its prompt", INK)],
                "waiting": [("· ", MUTED), (who.name, strong), (f", {note}", INK2)],
            }[who.state])
        elif who.kind == "no agent":
            fact("occupant", [("● ", ACCENT), ("no agent", strong), (": the room is vacant and the move is yours", INK)]
                 if who.fixable else [("no agent", INK), (": its session ended, and nothing here is yours to do yet", INK2)])
        elif who.kind == "person":
            fact("occupant", [(who.name, strong), (" · other people's agents cannot be seen", MUTED)])
        else:
            fact("occupant", [("nobody", INK), (": your review notes, kept on this Mac · " + pr.merge, MUTED)])

        quiet: Segments = [(" · quiet ", MUTED), (pr.age, strong if is_stale(pr.age) else INK2)]
        hint = ""
        if wait is None:
            fact("waits on", [("nothing", INK), (f": these notes belong to {short(pr.parent)}" if pr.parent else "", MUTED)])
        elif wait.kind == "working":
            fact("waits on", [("nothing", INK), (": the room is lit", INK2)])
            fact("doing", [(held.what + (f" · {held.counted} done" if held.total else ""), INK2)])
        else:
            said: Segments = []
            for hop in wait.links:
                said += [(short(hop), INK), (", which waits on ", MUTED)]
            end = pipe(wait.end_pr) if wait.end_pr is not None else None
            if wait.kind == "you":
                said += [("● ", ACCENT), ("you", strong), (f", to {wait.what}", INK)]
                if who.state == "at its prompt":
                    hint = "It gets going when you answer the prompt."
                elif who.kind == "no agent":
                    hint = "It gets going when you put an agent in the room."
                elif end is not None and end.state == "needs you":
                    hint = "It gets going when you answer your agent's prompt."
                else:
                    hint = "It gets going when you put an agent on it."
            elif wait.kind == "agent":                         # someone is at work on it: not quiet
                said += [("⠋ ", INK), (wait.end, INK)]
                if wait.end_pr is pr:
                    said.append((", reviewing in the notes room"
                                 + (f" · {end.counted}" if end is not None and end.total else ""), INK2))
                else:
                    said.append((", at work", INK2))
                quiet = []
            else:
                said += [(wait.end, INK), (f", to {wait.what}", INK2)]
                if end is not None and not wait.links:
                    said.append((f" ({end.progress})", MUTED))
            fact("waits on", said + quiet)
        if hint:
            fact("", [("● ", ACCENT), (hint, INK2)])
        behind = next((stop.behind for stop in self.stops if stop.pr is pr), [])
        if behind:
            fact("behind it", [(" ".join(short(p) for p in behind), INK), ("  wait on this room", MUTED)])

        off = [w for w in pr.workers if w.name]
        if off:
            fact("names", [("≠ ", CRIT), (f"session is {off[0].name}, want {pr.key}-{off[0].serial}", INK2)])
        else:
            fact("names", [("✓ ", GOOD), (
                "branch, session, PR and Jira agree" if pr.is_pr and pr.mine and pr.workers
                else "branch, PR and Jira agree" if pr.is_pr
                else f"branch and Jira agree · issue #{pr.number} holds the number", INK2)])
        seen: Segments = []
        for name, event in verdicts(pr).items():
            mark, mark_color, did = WORD[event.verdict]
            seen += [(mark, mark_color), (f" {'you' if name == ME else name} {did} {event.age}  ", INK2)]
        fact("review", seen or [("nobody asked yet", INK2)])
        if pr.threads[1]:
            fact("threads", [("◆ ", MUTED), (f"{pr.threads[0]} of {pr.threads[1]} open", INK2)])
        check_icon, check_color = CI[pr.ci]
        fact("checks", [(check_icon + " ", check_color),
                        (("out of order: " if pr.ci == "fail" else "")
                         + (" · ".join(x for x in (pr.ci_note, "" if pr.merge == "clean" else pr.merge) if x)
                            or "none yet"), INK2)])
        root = pr.root()
        suite = [p for p in root.walk() if p.state != "findings"]
        where = (f"{root.base} at {sha(self.repo, root.base, root.behind)}, "
                 + ("the tip" if not root.behind else f"{root.behind} commits back"))
        if len(suite) > 1:
            where += f" · a suite of {len(suite)} rooms"
        fact("where", [(where, INK2)])
        return text

    # ---- the plan's key ------------------------------------------------------------
    def key(self) -> Text:
        text = Text()
        text.append("key   ", style=f"bold {INK}")
        text.append("on a door: occupant · waits on · room   ", style=MUTED)
        text.append(f" {SPIN[0]} ", style=f"{INK} on {LIT}")
        text.append(" lit: agent at work   ", style=MUTED)
        text.append(" · ", style=f"{MUTED} on {DARK}")
        text.append(" dark: idle   ", style=MUTED)
        text.append("╎", style=INK2)
        text.append(" door to the room behind   ", style=MUTED)
        text.append("░░", style=MUTED).append(" dust: quiet a day or more\n", style=MUTED)
        text.append("      ")
        text.append("●", style=ACCENT).append(" ").append("▷", style=ACCENT)
        text.append(" idle, you can fix it: ", style=MUTED)
        text.append("vacant", style=INK2).append(" = no agent · ", style=MUTED)
        text.append("at the door", style=INK2).append(" = agent at its prompt   ", style=MUTED)
        text.append(" 1 ", style=f"bold #ffffff on {ACCENT}")
        text.append(" ").append(" 2 ", style=f"{INK} on {AXIS}")
        text.append(" the rounds   ", style=MUTED)
        text.append("✗", style=CRIT).append(" out of order: a check is failing", style=MUTED)
        return text

    # ---- keys ------------------------------------------------------------------------
    def action_walk(self, where: str) -> None:
        node = self.sel
        if node is None:
            return
        target: Node | None = None
        if where == "left":
            target = node.parent if node.parent is not None and node.parent.pr is not None else None
        elif where == "right":
            target = next((kid for kid in node.kids if kid.pr is not None), None)
        else:
            band = node.first - 1 if where == "up" else node.last + 1
            there = [n for n in self.rooms if n.first <= band <= n.last]
            if there:
                target = min(there, key=lambda n: (abs(n.level - node.level), -n.level))
        if target is not None:
            self.sel = target
        self.draw()

    def action_next_stop(self) -> None:
        if self.stops:
            at = next((i for i, s in enumerate(self.stops) if self.sel and s.pr is self.sel.pr), -1)
            self.sel = self.room_of(self.stops[(at + 1) % len(self.stops)].pr) or self.sel
        self.draw()

    def action_pick(self, step: int) -> None:
        """Step through the directory. Past either end the pick clears. Picking moves you to
        the occupant's first room, and outlines the rest."""
        listing: list[Member | None] = [None, *self.tenants, *self.folk]
        at = next(i for i, m in enumerate(listing) if m is self.picked)
        self.picked = listing[(at + step) % len(listing)]
        if self.picked is not None:
            rooms = [self.room_of(pr) for pr in owned_by(self.prs, self.picked)]
            self.sel = next((room for room in rooms if room is not None), self.sel)
        self.draw()

    def action_unpick(self) -> None:
        self.picked = None
        self.draw()

    def action_repo(self, step: int) -> None:
        names = list(REPOS)
        self.load(names[(names.index(self.repo) + step) % len(names)])
        self.draw()


if __name__ == "__main__":
    StudioV4C().run()
