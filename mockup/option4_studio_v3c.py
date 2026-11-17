"""Agent Frolic mock-up, option 4 v3c (the wildcard): keep the lights on.

The repo is a building seen at night, in plan. main is the corridor down the left, with a
door at each commit that something hangs from, named by its short hash. Every branch is a
room. A stack is a suite of rooms opening off the corridor: the room nearest the corridor is
the one nearest main, and the rooms stacked on it lie behind it, so a room is as tall as
everything that rests on it. A base cut from main (release-0.2) is a side corridor with its
own rooms.

A room is LIT while an agent is at work in it, and only then; that is all "in progress"
means. A DARK room says what it waits on: you, an agent being assigned, a person, or the
room to its left (followed to the end of the wait). So the question "is every agent
working?" is "are all the lights on?", and the answer is wherever it is dark.

A night watch walks the dark rooms that need effort, most urgent first (is any agent
waiting on it, then how long it has been quiet). The numbers on the rooms are the order of
its rounds; the list bottom left is the same rounds; the report bottom right is what it
found in the room it is standing in, and what gets that room going again. Leave the keyboard
alone and the watch keeps walking; touch an arrow and you carry the lantern yourself.

Blue marks only what is idle and yours to fix. Color sits on marks; rooms are greys.

    .venv/bin/python mockup/option4_studio_v3c.py

Keys: left/right walk through a suite (toward main / away from it), up/down move between
rooms, n goes to the next stop on the rounds, space lets the watch walk or holds it,
tab/shift+tab switch repo, q quits.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Static

from frolic_v3 import (ACCENT, AXIS, CRIT, GOOD, INK, INK2, ME, MUTED, PLANE, PR, REPOS, SPIN,
                       STATUS, WORD, Canvas, Effort, Pipe, Row, agent, anchor_note, broken, chain,
                       needs_effort, people_line, pipe, sha, short, top_line, tree_rows, verdicts,
                       work_of)

LIT, DARK = "#262624", "#121211"     # a room with an agent at work, and one without
LAMP, EDGE = "#6a6964", "#232322"    # the strip along the top of a lit room, and of a dark one
GUTTER = 11                          # the corridor: a commit's short hash and its place
FPS = 8
STEP = 5                             # seconds the watch spends in a room


def clip(text: str, width: int) -> str:
    return text if len(text) <= width else text[:max(0, width - 1)] + "…"


@dataclass
class Node:
    """A room (a branch) or a side corridor (a base cut from another base)."""
    row: Row
    level: int                               # 0 opens onto main's corridor
    parent: "Node | None" = None
    kids: list["Node"] = field(default_factory=list)
    doors: list[Row] = field(default_factory=list)      # commits it hangs from, in its corridor
    first: int = 0                           # the bands (rows of leaf rooms) it spans
    last: int = 0

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


def chain_words(pr: PR) -> str:
    """ "waits on pr-0024, which waits on mira" """
    links = chain(pr)
    said = [f"waits on {short(nxt)}" for nxt in links[1:]]
    end = pipe(links[-1])
    said.append({"working": "is in progress", "needs you": "waits on you",
                 "no agent": "needs an agent"}.get(end.state, f"waits on {end.on}") if end else "")
    return said[0] + "".join(f", which {s}" for s in said[1:] if s)


class StudioV3C(App):
    CSS = f"""
    Screen {{ background: {PLANE}; color: {INK}; }}
    #top {{ height: 1; margin: 0 2; text-wrap: nowrap; text-overflow: ellipsis; }}
    #people {{ height: 1; margin: 0 2; text-wrap: nowrap; text-overflow: ellipsis; }}
    #plan {{ height: auto; margin: 1 2 0 2; }}
    #band {{ height: 1fr; min-height: 6; margin: 1 2 0 2; }}
    #rounds {{ width: 58; height: 1fr; text-wrap: nowrap; text-overflow: ellipsis; }}
    #report {{ width: 1fr; height: 1fr; padding: 0 0 0 3; text-wrap: nowrap; text-overflow: ellipsis; }}
    #legend {{ height: 1; margin: 0 2; text-wrap: nowrap; text-overflow: ellipsis; }}
    #hints {{ height: 1; margin: 0 2; color: {MUTED}; text-wrap: nowrap; text-overflow: ellipsis; }}
    """
    BINDINGS = [
        Binding("up", "walk('up')", show=False), Binding("down", "walk('down')", show=False),
        Binding("left", "walk('left')", show=False), Binding("right", "walk('right')", show=False),
        Binding("n", "next_stop", show=False), Binding("space", "watch", show=False),
        Binding("tab", "repo(1)", show=False, priority=True),
        Binding("shift+tab", "repo(-1)", show=False, priority=True),
        Binding("q", "quit", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.frame = 0
        self.walking = True                  # the watch does its rounds until you take over
        self.top_band = 0
        self.load(next(iter(REPOS)))

    def compose(self) -> ComposeResult:
        yield Static(id="top")
        yield Static(id="people")
        yield Static(id="plan")
        with Horizontal(id="band"):
            yield Static(id="rounds")
            yield Static(id="report")
        yield Static(id="legend")
        yield Static(id="hints")

    def on_mount(self) -> None:
        self.set_interval(1 / FPS, self.tick)
        self.draw()

    def on_resize(self) -> None:
        self.draw()

    # ---- state -------------------------------------------------------------
    def load(self, repo: str) -> None:
        self.repo = repo
        self.nodes, self.bands = layout(repo)
        self.rooms = [n for n in self.nodes if n.pr is not None]
        self.stops: list[Effort] = needs_effort(REPOS[repo])
        self.stop = 0
        self.top_band = 0
        first = self.room_of(self.stops[0].pr) if self.stops else None
        self.sel: Node | None = first or (self.rooms[0] if self.rooms else None)
        self.since = self.frame              # when the watch arrived in this room

    def room_of(self, pr: PR) -> Node | None:
        return next((n for n in self.rooms if n.pr is pr), None)

    def tick(self) -> None:
        self.frame += 1
        if self.walking and self.stops and self.frame - self.since >= STEP * FPS:
            self.stop = (self.stop + 1) % len(self.stops)
            self.sel = self.room_of(self.stops[self.stop].pr) or self.sel
            self.since = self.frame
            self.draw()
            return
        self.query_one("#top", Static).update(top_line(self.repo, frame=self.frame))
        self.query_one("#plan", Static).update(self.plan())

    # ---- drawing -----------------------------------------------------------
    def draw(self) -> None:
        self.query_one("#top", Static).update(top_line(self.repo, frame=self.frame))
        self.query_one("#people", Static).update(people_line(REPOS[self.repo]) if self.rooms else "")
        self.query_one("#plan", Static).update(self.plan())
        self.query_one("#rounds", Static).update(self.rounds())
        self.query_one("#report", Static).update(self.report())
        self.query_one("#legend", Static).update(self.legend())
        watch = ("space holds the watch" if self.walking else "space lets the watch walk")
        self.query_one("#hints", Static).update(
            f"←→ through a suite    ↑↓ between rooms    n next stop    {watch}    ⇥ next repo    q quit")

    def plan(self) -> Text:
        inner = max(60, self.size.width - 4)
        out = Text(no_wrap=True)
        out.append("main", style=f"bold {INK}")
        out.append("   the corridor · commits run down from the tip · a room is as tall as what is stacked on it",
                   style=MUTED)
        if not self.rooms:
            out.append(f"\n┬ {sha(self.repo, 'main', 0)}", style=INK2)
            out.append("   no rooms: nothing is open in this repo", style=MUTED)
            return out
        levels = max(n.level for n in self.nodes) + 1
        lw = max(14, min(30, (inner - GUTTER) // levels - 1))
        rp, shown = self.grid()
        if self.sel is not None:                               # keep the chosen room in view
            self.top_band = min(self.top_band, self.sel.first)
            self.top_band = max(self.top_band, min(self.sel.first, self.sel.last - shown + 1),
                                self.sel.first - shown + 1)
        self.top_band = max(0, min(self.top_band, self.bands - shown))
        canvas = Canvas(inner, shown * rp)
        numbers = {id(e.pr): (i + 1, e.yours) for i, e in enumerate(self.stops)}

        def y_of(band: int) -> int:
            return (band - self.top_band) * rp

        # the corridor and its doors
        trunk = [n for n in self.nodes if n.level == 0 and n.doors]
        if trunk:
            for y in range(max(0, y_of(trunk[0].first) + 1), canvas.height):
                canvas.put(0, y, "│", AXIS)
        seen_door = False
        for node in self.nodes:
            x = 0 if node.level == 0 else GUTTER + (node.level - 1) * (lw + 1) + 1
            for k, door in enumerate(node.doors):
                y = y_of(node.first) + 1
                first_here = not seen_door if node.level == 0 else door.behind == 0
                room = GUTTER - 3 if node.level == 0 else lw - 4
                if node.level == 0:
                    canvas.put(x, y, "┬" if first_here else "┼", MUTED)
                    canvas.put(x + 2, y, door.sha, INK2)
                    canvas.put(x + 2, y + 1, clip(door.label.replace(" commits", ""), room), MUTED)
                elif rp == 3:
                    canvas.put(x, y + 1, "┬" if first_here else "┼", MUTED)
                    canvas.put(x + 2, y + 1, clip(f"{door.sha} {door.label.replace(' commits', '')}", room),
                               MUTED)
                if node.level == 0:
                    seen_door = True

        for node in self.nodes:
            x = GUTTER + node.level * (lw + 1)
            width = lw if (node.kids or node.pr is None) else inner - x
            gap, first, last = y_of(node.first), y_of(node.first) + 1, y_of(node.last) + rp - 1
            if last < 0 or gap >= canvas.height:
                continue
            if node.pr is None:
                self.draw_wing(canvas, node, x, width, first, rp)
            else:
                self.draw_room(canvas, node, x, width, gap, first, last, numbers)
        out.append("\n")
        out.append_text(canvas.text())
        return out

    def draw_wing(self, canvas: Canvas, node: Node, x: int, width: int, first: int, rp: int) -> None:
        """A base cut from another base: a side corridor, so it is not filled in."""
        canvas.put(x + 1, first, clip(node.row.label, width - 2), f"bold {INK2}")
        if rp == 2 and not node.kids:
            return
        doors = [d for kid in node.kids for d in kid.doors]
        if rp == 2 and doors and len(node.row.label) + 9 < width:
            canvas.put(x + 2 + len(node.row.label), first, doors[0].sha, MUTED)

    def draw_room(self, canvas: Canvas, node: Node, x: int, width: int, gap: int, first: int,
                  last: int, numbers: dict[int, tuple[int, bool]]) -> None:
        pr = node.pr
        held = pipe(pr)
        notes_live = pr.state == "findings" and any(w.live for w in pr.workers)
        lit = notes_live or (held is not None and held.state == "working")
        fixable = held is not None and held.state in ("needs you", "no agent")
        fill = LIT if lit else DARK
        chosen = node is self.sel
        for y in range(max(gap, 0), last + 1):
            if y == gap:
                canvas.put(x, y, "▄" * width, INK if chosen else (LAMP if lit else EDGE))
            else:
                canvas.put(x, y, " " * width, f"on {fill}")

        def put(dx: int, y: int, text: str, style: str) -> None:
            canvas.put(x + dx, y, text, f"{style} on {fill}")

        y = max(first, 0)                                      # the name stays in view when scrolled
        if lit:
            mark, mark_style = SPIN[(self.frame + node.first * 3) % len(SPIN)], INK
        elif held is None:
            mark, mark_style = "", MUTED
        elif fixable:
            mark, mark_style = "●", ACCENT
        elif held.on_pr is not None:
            mark, mark_style = "◂", INK2
        else:
            mark, mark_style = "○", MUTED
        through_wall = (held is not None and not lit and held.on_pr is not None
                        and node.parent is not None and node.parent.pr is held.on_pr)
        number = numbers.get(id(pr))
        chip = f" {number[0]} " if number else ""
        right = 2 + (len(chip) + 1 if chip else 0)
        icon, color, _, _ = STATUS[pr.state]
        put(1, y, icon, color)
        name = clip(pr.name, width - 4 - right)
        strong = lit or fixable
        put(3, y, name[:len(pr.key)], (f"bold {INK}" if strong else INK2) + (" underline" if chosen else ""))
        put(3 + len(pr.key), y, name[len(pr.key):], INK2 if strong else MUTED)
        after = 3 + len(name)
        if broken(pr) and pr.state != "conflict" and after + 18 < width - right:
            put(after + 2, y, "✗", CRIT)
            put(after + 4, y, "check failing", INK2 if strong else MUTED)
            after += 17
        if through_wall:
            put(0, y, mark, mark_style)
        elif mark:
            put(width - 2, y, mark, mark_style)
        if chip:
            canvas.put(x + width - 3 - len(chip), y, chip,
                       f"bold #ffffff on {ACCENT}" if number[1] else f"{INK} on {AXIS}")

        long = width >= 40
        words = self.words(pr, held, long)
        style = INK2 if lit else (f"bold {INK}" if fixable else MUTED)
        spare = last - y                                       # lines below the name
        if spare >= 1:
            put(3, y + 1, clip(words, width - 5), style)
            if spare >= 2 and not long and held is not None:
                put(3, y + 2, clip(f"{', '.join(held.who)} · {held.what}", width - 5), MUTED)
        elif long:
            col = max(after + 3, 50 if width >= 100 else 0)
            if col + 14 < width - right:
                put(col, y, clip(words, width - right - col - 1), style)

    def words(self, pr: PR, held: Pipe | None, long: bool) -> str:
        """What a room says under its name: in progress, or what it waits on."""
        if held is None:
            at_work = any(w.live for w in pr.workers)
            if long:
                return f"review notes for {short(pr)}" + (" · your session is reading" if at_work else "")
            return "review notes" + (" · at work" if at_work else "")
        count = f" {held.done}/{held.total}" if held.total else ""
        if held.state == "working":
            return (f"in progress · {held.what}" + (f" · {held.counted} done" if held.total else "")
                    if long else f"in progress{count}")
        if held.state == "needs you":
            return (f"waits on you · a session is at its prompt · {held.what} · quiet {pr.age}"
                    if long else "waits on you")
        if held.state == "no agent":
            return (f"needs an agent · {held.what} · {held.progress} · quiet {pr.age}"
                    if long else "needs an agent")
        if held.on_pr is None:
            return (f"waits on {held.on} · {held.what} · {held.progress} · quiet {pr.age}"
                    if long else f"{held.on} · " + (f"stalled{count}" if held.partly else "not started"))
        if long:
            return f"{chain_words(pr)} · {held.what}"
        end = pipe(chain(pr)[-1])
        tail = {"working": "working", "needs you": "you", "no agent": "an agent"}.get(end.state, end.on)
        return f"{short(held.on_pr)} → {tail}"

    def rounds(self) -> Text:
        """The same order as the numbers on the rooms: most urgent first."""
        text = Text()
        text.append("the rounds", style=f"bold {INK}")
        text.append("   most urgent first · " + ("the watch is walking\n" if self.walking
                                                  else "you have the lantern\n"), style=MUTED)
        rp, shown = self.grid()
        rows = max(1, self.size.height - 7 - (1 + shown * rp))
        if not self.stops:
            text.append("nothing needs effort: every room that can be lit is lit\n" if self.rooms
                        else "nothing is open\n", style=MUTED)
        for i, stop in enumerate(self.stops[:rows]):
            here = self.sel is not None and stop.pr is self.sel.pr
            text.append("▸" if here else " ", style=INK)
            text.append(f" {i + 1} ", style=f"bold #ffffff on {ACCENT}" if stop.yours else f"{INK} on {AXIS}")
            text.append(f" {stop.pr.key:<16}", style=f"bold {INK}" if here or stop.yours else INK2)
            why = {"needs you": "at its prompt", "no agent": "no agent"}.get(stop.kind, stop.who)
            text.append(f"{clip(why, 14):<15}", style=INK if stop.yours else INK2)
            text.append((f"{stop.waiting} waiting · " if stop.waiting else "") + f"quiet {stop.pr.age}\n",
                        style=MUTED)
        if len(self.stops) > rows:
            text.append(f"      and {len(self.stops) - rows} more\n", style=MUTED)
        return text

    def grid(self) -> tuple[int, int]:
        """(rows per band, bands shown). Two lines a room when the whole building fits that
        way; one line a room when it does not; and past that the plan scrolls."""
        rp = 3 if self.bands * 3 <= self.size.height - 16 else 2
        return rp, max(1, min(self.bands, max(4, self.size.height - 14) // rp))

    def report(self) -> Text:
        """What the watch found in the room it is standing in, and what gets it going."""
        text = Text()
        if self.sel is None or self.sel.pr is None:
            return text.append("the building is empty", style=MUTED)
        pr = self.sel.pr
        held = pipe(pr)
        icon, color, label, _ = STATUS[pr.state]
        text.append(f"{self.repo}-", style=MUTED)
        text.append(pr.name, style=f"bold {INK}")
        text.append("   ")
        text.append(icon, style=color)
        text.append(f" {label}\n", style=INK2)

        mine = agent(pr)
        idle = (f" Session {mine.serial} is assigned and idle until then."
                if mine and not mine.live else "")
        if held is None:
            found = (f"Review notes for {short(pr)}, kept on this Mac."
                     + (" Your session is in here reading." if any(w.live for w in pr.workers) else ""))
            going = ""
        elif held.state == "working":
            found = (f"Lit. Session {mine.serial if mine else '?'} is at work: {held.what}"
                     + (f", {held.counted} done." if held.total else "."))
            going = mine.note if mine else ""
        elif held.state == "needs you":
            found = f"Dark. Session {mine.serial} is at its prompt, waiting for you to {held.what}."
            going = "It gets going when you answer the prompt."
        elif held.state == "no agent":
            found = f"Dark. The next move is yours ({held.what}) and no agent is in here."
            going = "It gets going when you put an agent on it."
        elif held.on_pr is None:
            found = f"Dark. It waits on {held.on} to {held.what} ({held.progress})."
            going = f"It gets going when {held.on} gets to it.{idle}"
        else:
            found = f"Dark. It {chain_words(pr)}."
            going = f"It gets going when {short(held.on_pr)} does.{idle}"
        text.append(found + "\n", style=INK)
        behind = next((s.behind for s in self.stops if s.pr is pr), [])
        if behind:
            going += " Behind it: " + ", ".join(short(p) for p in behind) + "."
        if going:
            text.append("● " if held and held.state in ("needs you", "no agent") else "  ",
                        style=ACCENT)
            text.append(going + "\n", style=INK2)
        if held is not None and held.on_pr is None and held.state == "waiting":
            for name in held.who:
                text.append(f"  {name} has  ", style=MUTED)
                text.append(clip(" · ".join(f"{short(p)} {p.next[1]}" for p in work_of(REPOS[self.repo], name)),
                                 72) + "\n", style=INK2)

        def fact(key: str, value: Text) -> None:
            text.append(f"{key:<8}", style=MUTED)
            text.append_text(value).append("\n")

        off = [w for w in pr.workers if w.name]
        if off:
            fact("names", Text("≠ ", style=CRIT).append(
                f"session is named {off[0].name}, expected {self.repo}-{pr.key}-{off[0].serial}", style=INK2))
        else:
            fact("names", Text("✓ ", style=GOOD).append(
                "branch, session, PR and Jira agree" if pr.is_pr and pr.mine and pr.workers
                else ("branch, PR and Jira agree" if pr.is_pr
                      else f"branch and Jira agree · issue #{pr.number} holds the number"), style=INK2))
        said = Text()
        for who, event in verdicts(pr).items():
            mark, mark_color, did = WORD[event.verdict]
            said.append(mark, style=mark_color)
            said.append(f" {'you' if who == ME else who} {did} {event.age}   ", style=INK2)
        if pr.threads[1]:
            said.append(f"{pr.threads[0]} of {pr.threads[1]} threads open", style=INK2)
        fact("review", said if said.plain else Text("nobody asked yet", style=INK2))
        check = {"pass": ("✓", GOOD), "fail": ("✗", CRIT), "run": ("…", MUTED), "none": ("·", MUTED)}[pr.ci]
        fact("checks", Text(check[0] + " ", style=check[1]).append(
            " · ".join(x for x in (pr.ci_note, pr.merge) if x) or "none yet", style=INK2))
        root = pr.root()
        fact("commit", Text(f"{sha(self.repo, root.base, root.behind)} · {anchor_note(pr)}", style=INK2))
        return text

    def legend(self) -> Text:
        text = Text()
        text.append("▄▄▄▄", style=f"{LAMP} on {LIT}")
        text.append(" lit: agent at work ", style=MUTED)
        text.append(SPIN[0], style=INK)
        text.append("   ")
        text.append("▄▄▄▄", style=f"{EDGE} on {DARK}")
        text.append(" dark: nobody working   ", style=MUTED)
        text.append("●", style=ACCENT).append(" yours to fix   ", style=MUTED)
        text.append("○", style=MUTED).append(" waits on a person   ", style=MUTED)
        text.append("◂", style=MUTED).append(" waits on the room to its left   ", style=MUTED)
        text.append(" 1 ", style=f"bold #ffffff on {ACCENT}")
        text.append(" ").append(" 2 ", style=f"{INK} on {AXIS}")
        text.append(" rounds", style=MUTED)
        return text

    # ---- keys --------------------------------------------------------------
    def action_walk(self, where: str) -> None:
        self.walking = False                 # you have the lantern now
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
        self.walking = False
        if self.stops:
            at = next((i for i, s in enumerate(self.stops) if self.sel and s.pr is self.sel.pr), -1)
            self.stop = (at + 1) % len(self.stops)
            self.sel = self.room_of(self.stops[self.stop].pr) or self.sel
        self.draw()

    def action_watch(self) -> None:
        self.walking = not self.walking
        self.since = self.frame
        at = next((i for i, s in enumerate(self.stops) if self.sel and s.pr is self.sel.pr), None)
        if at is not None:
            self.stop = at
        self.draw()

    def action_repo(self, step: int) -> None:
        names = list(REPOS)
        self.load(names[(names.index(self.repo) + step) % len(names)])
        self.draw()


if __name__ == "__main__":
    StudioV3C().run()
