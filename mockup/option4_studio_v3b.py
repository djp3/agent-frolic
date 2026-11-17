"""Agent Frolic mock-up, option 4 v3b: plumbing. "Pipeline" taken literally, drawn as a piping
diagram. The hierarchy is the pipework: a base is a main running down the page, its commits
are flanges named by short hash, a stack is a pipe teed off at its commit, and a base cut
from another base hangs from the flange it was cut at.

Every branch then has a line running in from the right: its pump (your agent) at the far end,
a valve where the line meets the branch. Fluid means an agent. It FLOWS (and only flow moves)
while the agent works. It stands STILL behind a closed valve while the agent waits. The line
is DRY when no agent is on it. A closed valve is filled, and blue when you can open it
yourself (a session at its prompt, a move with no agent); grey when a person's hand is on it.
A branch that waits on another branch has no valve of its own: its line ties into that
branch's line, so everything backed up behind one valve is drawn joined to it.

So the screen is read down the valve bank: where is it flowing, where does it stop, which
valve is blue. The work order at the bottom lists the valves to open, most urgent first.

    .venv/bin/python mockup/option4_studio_v3b.py

Keys: up/down pick a branch, n jumps to the next valve on the work order, space stops the
flow (all motion), tab / shift+tab go to the next repo, q quits.
"""
from __future__ import annotations

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Static

from frolic_v3 import (ACCENT, AXIS, BASES, CRIT, GOOD, INK, INK2, ME, MUTED, PLANE, PR, RAISED,
                       REPOS, SPIN, STATUS, SURFACE, WORD, Canvas, Pipe, Row, agent, broken, chain,
                       free_people, needs_effort, is_stale, pipe, root_cause, session_state, sha,
                       short, top_line, tree_rows, verdicts, work_of)

FPS = 8
X_TREE, X_SAID, SAID_W, LINE_W, TIE_AT = 1, 48, 52, 20, 4      # TIE_AT: where a line ties in
PIPEWORK = str.maketrans({"├": "╠", "└": "╚", "│": "║", "┬": "╥", "┼": "╫"})


def clip(text: str, width: int) -> str:
    return text if len(text) <= width else text[:max(0, width - 1)] + "…"


def pipework(row: Row) -> str:
    """The thin connectors of the shared tree, redrawn as pipe: tees, elbows and flanges."""
    lead = row.lead.translate(PIPEWORK)
    return lead if row.kind == "commit" or not lead else lead[:-1] + "═"


def fitting(pr: PR, held: Pipe) -> tuple[str, str]:
    """(valve, fluid) for a branch. valve: open | yours | theirs | tie. fluid: flow | still | dry."""
    if held.state == "working":
        return "open", "flow"
    if held.state == "needs you":
        return "yours", "still"              # its agent stands at the prompt behind the valve
    if held.state == "no agent":
        return "yours", "dry"                # nothing in the line: no agent to pump it
    if held.on_pr is not None:
        return "tie", "still"                # backed up behind another branch's valve
    return "theirs", "still" if agent(pr) else "dry"


def said(pr: PR, held: Pipe) -> list[tuple[str, str]]:
    """Who has a hand on the valve and what they have to do, as styled pieces."""
    counted = f" {held.done}/{held.total} {held.unit}".rstrip() if held.total else ""
    if held.state == "working":
        return [("in progress", INK), (f" · {held.what}", INK2), (f" ·{counted}" if counted else "", MUTED)]
    if held.on_pr is not None:
        end_held, _ = root_cause(pr)
        end = "flowing" if end_held.state == "working" else valve_owner(end_held)
        return [("behind ", MUTED), (" ▸ ".join(short(p) for p in chain(pr)[1:]), INK2),
                (" ▸ ", MUTED), (end, INK2), (f" · {held.what}", MUTED)]
    yours = held.state in ("needs you", "no agent")
    return [(valve_owner(held), f"bold {INK}" if yours else INK), (f" · {held.what}", INK2),
            (f" · stalled{counted}" if held.partly else " · not started", MUTED)]


def valve_owner(held: Pipe) -> str:
    return {"needs you": "you", "no agent": "an agent"}.get(held.state, held.on)


class StudioV3B(App):
    CSS = f"""
    Screen {{ background: {PLANE}; color: {INK}; }}
    #top {{ height: 1; margin: 1 2 0 2; }}
    #map {{ height: 1fr; margin: 1 1 0 1; }}
    #band {{ height: 13; }}
    .tile {{ background: {SURFACE}; padding: 1 2; margin: 0 1; width: 1fr; height: 13; }}
    #legend {{ height: 3; margin: 1 2 0 2; }}
    #hints {{ height: 1; margin: 0 2; color: {MUTED}; }}
    #top, #map, .tile, #legend, #hints {{ text-wrap: nowrap; text-overflow: ellipsis; }}
    """
    BINDINGS = [
        Binding("up", "move(-1)", show=False), Binding("down", "move(1)", show=False),
        Binding("n", "next_valve", show=False), Binding("space", "pause", show=False),
        Binding("tab", "repo(1)", show=False, priority=True),
        Binding("shift+tab", "repo(-1)", show=False, priority=True),
        Binding("q", "quit", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.repo = next(iter(REPOS))
        self.frame = 0
        self.paused = False
        self.sel = 0                 # index into the branch rows
        self.top = 0                 # first row of the map on screen, when it has to scroll
        self.order_at = -1           # where `n` is on the work order

    def compose(self) -> ComposeResult:
        yield Static(id="top")
        yield Static(id="map")
        with Horizontal(id="band"):
            yield Static(id="order", classes="tile")
            yield Static(id="detail", classes="tile")
        yield Static(id="legend")
        yield Static("↑↓ branch    n next valve on the work order    space stop the flow    "
                     "⇥ next repo    q quit", id="hints")

    def on_mount(self) -> None:
        self.set_interval(1 / FPS, self.tick)
        self.query_one("#legend", Static).update(self.legend())
        self.draw_all()
        self.call_after_refresh(self.draw_all)

    def on_resize(self) -> None:
        self.draw_all()

    def tick(self) -> None:
        if not self.paused:
            self.frame += 1
            self.draw_moving()

    # ---- what is on the page -------------------------------------------------
    def rows(self) -> list[Row]:
        return tree_rows(self.repo)

    def branches(self) -> list[int]:
        return [i for i, row in enumerate(self.rows()) if row.kind == "branch"]

    def selected(self) -> PR | None:
        rows, picks = self.rows(), self.branches()
        return rows[picks[min(self.sel, len(picks) - 1)]].pr if picks else None

    def draw_all(self) -> None:
        self.draw_moving()
        self.query_one("#order", Static).update(self.work_order())
        self.query_one("#detail", Static).update(self.detail())

    def draw_moving(self) -> None:
        self.query_one("#top", Static).update(
            top_line(self.repo, frame=self.frame, paused=self.paused))
        self.query_one("#map", Static).update(self.diagram())

    # ---- the piping diagram --------------------------------------------------
    def fluid(self, kind: str, k: int) -> tuple[str, str]:
        """One cell of a line. Only flow moves, and it moves toward the valve."""
        if kind == "flow":
            phase = 2 if self.paused else (k + self.frame) % 6
            return "≈", INK if phase == 0 else INK2 if phase == 1 else MUTED
        return ("═", INK2) if kind == "still" else ("┄", AXIS)

    def diagram(self) -> Text:
        rows, picks = self.rows(), self.branches()
        width = max(60, self.size.width - 2)
        said_w = max(16, SAID_W - max(0, 146 - width))         # a narrow window squeezes the words
        x_valve = X_SAID + said_w + 1
        x_line = x_valve + 2
        x_tie, x_pump = x_line + TIE_AT, x_line + LINE_W + 2
        x_quiet = x_pump + 17

        room = self.query_one("#map", Static).size.height - 1
        room = len(rows) if room <= 0 else max(3, room)
        chosen = picks[min(self.sel, len(picks) - 1)] if picks else 0
        if chosen < self.top + 1:
            self.top = max(0, chosen - 1)
        if chosen >= self.top + room:
            self.top = chosen - room + 1
        self.top = max(0, min(self.top, len(rows) - room))
        shown = range(self.top, min(len(rows), self.top + room))
        canvas = Canvas(width, 1 + len(shown) + (0 if picks else 2))

        rev = next((row.sha for row in rows if row.kind == "commit"), "")
        title = (f"piping diagram · {self.repo} · rev {rev}" if len(shown) == len(rows) else
                 f"piping diagram · rows {self.top + 1}–{shown[-1] + 1} of {len(rows)}")
        canvas.put(X_TREE, 0, clip(title, X_SAID - X_TREE - 2), MUTED)
        for label, x in ((clip("whose hand is on the valve · what it takes", said_w - 1), X_SAID),
                         ("valve", x_valve - 1), ("line", x_line + 7), ("pump", x_pump),
                         ("quiet", x_quiet - 1)):
            canvas.put(x, 0, label, MUTED)

        where = {id(row.pr): i for i, row in enumerate(rows) if row.pr is not None}
        fits: dict[int, tuple[str, str]] = {}
        ties: list[tuple[int, int]] = []         # (the row that waits, the row it waits on)
        for i, row in enumerate(rows):
            held = pipe(row.pr) if row.pr is not None else None
            if held is not None:
                fits[i] = fitting(row.pr, held)
                if held.on_pr is not None and id(held.on_pr) in where:
                    ties.append((i, where[id(held.on_pr)]))
        tied_into = {target for _, target in ties}

        for i in shown:
            row, y = rows[i], 1 + i - self.top
            lead = pipework(row)
            canvas.put(X_TREE, y, lead, MUTED)
            x = X_TREE + len(lead)
            if row.kind == "base":
                canvas.put(x, y, row.label, f"bold {INK}")
                canvas.put(x + len(row.label) + 2, y, row.note or "the main, newest commit first", MUTED)
                continue
            if row.kind == "commit":
                canvas.put(x, y, row.sha, INK2)
                canvas.put(x + len(row.sha) + 1, y, row.label, MUTED)
                continue
            pr = row.pr
            if i in fits:                                      # the stub into the tree says the same
                canvas.put(x - 1, y, *{"flow": ("≈", INK2), "still": ("═", INK2),
                                       "dry": ("┄", AXIS)}[fits[i][1]])
            icon, color, _, _ = STATUS[pr.state]
            canvas.put(x, y, icon, color)
            canvas.put(x + 2, y, pr.key, f"bold {INK}")
            slug = clip("-" + pr.slug, X_SAID - x - 5 - len(pr.key))
            canvas.put(x + 2 + len(pr.key), y, slug, INK2)
            if broken(pr) and pr.state != "conflict":
                canvas.put(x + 3 + len(pr.key) + len(slug), y, "✗", CRIT)

            held = pipe(pr)
            if held is None:                                   # a local review branch: no line
                canvas.put(X_SAID, y, clip(f"review notes for {short(pr)}", said_w), MUTED)
            else:
                valve, kind = fits[i]
                at = X_SAID
                for words, style in said(pr, held):
                    room_left = x_valve - 1 - at
                    canvas.put(at, y, clip(words, room_left), style)
                    if len(words) > room_left:                 # the words ran out of room: stop here
                        break
                    at += len(words)
                for k in range(LINE_W):
                    cell = x_line + k
                    if valve == "tie" and cell < x_tie:
                        continue
                    if valve == "tie" and cell == x_tie:
                        canvas.put(cell, y, "╚", INK2)
                        continue
                    here = "still" if kind == "dry" and i in tied_into and cell <= x_tie else kind
                    canvas.put(cell, y, *self.fluid(here, k))
                if valve == "open":
                    canvas.put(x_valve, y, "▷◁", MUTED)
                elif valve != "tie":
                    canvas.put(x_valve, y, "▶◀", f"bold {ACCENT}" if valve == "yours" else INK2)
                self.pump(canvas, x_pump, y, pr, held)
            canvas.put(x_quiet, y, pr.age.rjust(4), f"bold {INK}" if is_stale(pr.age) else MUTED)

        for waiting, target in ties:                           # the tie-ins, drawn over the lines
            step = 1 if target > waiting else -1
            for i in range(waiting + step, target, step):
                y = 1 + i - self.top
                if 0 < y < canvas.height and x_tie < width:
                    joined = canvas.cells[y][x_tie][0] in "╚╔╠"
                    canvas.put(x_tie, y, "╠" if joined else "║", INK2)
            y = 1 + target - self.top
            if 0 < y < canvas.height and x_tie < width:
                style = canvas.cells[y][x_tie][1] or INK2
                if fits.get(target, ("", ""))[0] == "tie":
                    canvas.put(x_tie, y, "╠", INK2)
                else:
                    canvas.put(x_tie, y, "╦" if step < 0 else "╩", style)

        if not picks:
            canvas.put(X_TREE + 2, canvas.height - 1, "nothing in the pipes", MUTED)
        y = 1 + chosen - self.top
        if picks and 0 < y < canvas.height:
            for x in range(width):
                if " on " not in canvas.cells[y][x][1]:
                    canvas.paint(x, y, 1, RAISED)
            canvas.put(0, y, "▌", f"{INK} on {RAISED}")
        return canvas.text()

    def pump(self, canvas: Canvas, x: int, y: int, pr: PR, held: Pipe) -> None:
        mine = agent(pr)
        if mine is None:
            canvas.put(x, y, "· no agent" if pr.mine or "you" in held.who else f"{pr.author}'s", MUTED)
            return
        state = session_state(mine)
        if state == "working":
            canvas.put(x, y, "⠿" if self.paused else SPIN[(self.frame + y) % len(SPIN)], INK)
            canvas.put(x + 2, y, f"{mine.serial} working", INK)
        else:
            canvas.put(x, y, "□", MUTED)
            canvas.put(x + 2, y, f"{mine.serial} " + ("at prompt" if state == "at its prompt" else state),
                       INK2 if state == "at its prompt" else MUTED)

    # ---- the work order ------------------------------------------------------
    def work_order(self) -> Text:
        prs = REPOS[self.repo]
        found, free = needs_effort(prs), free_people(prs)
        width = max(20, (self.size.width - 4) // 2 - 4)
        text = Text("work order · valves to open, most urgent first\n", style=MUTED)
        if not found and not free:
            return text.append("every line that can flow is flowing" if self.branches()
                               else "nothing in the pipes", style=INK2)
        chosen = self.selected()
        ends_at = root_cause(chosen)[1] if chosen is not None else None
        room = 9 - len(free)
        for effort in found[:room if len(found) <= room else room - 1]:
            line = Text("▶◀ ", style=f"bold {ACCENT}" if effort.yours else INK2)
            line.append(f"{short(effort.pr):<8}", style=f"bold {INK}")
            what = {"needs you": f"you, at its prompt: {effort.pipe.what}",
                    "no agent": f"needs an agent: {effort.pipe.what}",
                    }.get(effort.kind, f"{effort.who}: {effort.pipe.what}")
            line.append(f"{clip(what, 28):<29}", style=INK if effort.yours else INK2)
            line.append(effort.urgency, style=MUTED)
            if effort.pr is ends_at:                           # the valve the chosen branch is behind
                line.append(" " * max(0, width - line.cell_len))
                line.stylize(f"on {RAISED}")
            text.append_text(line).append("\n")
        if len(found) > room:
            text.append(f"   and {len(found) - room + 1} more\n", style=MUTED)
        for name in free:
            text.append("●  ", style=ACCENT)
            text.append(f"{name} has nothing to do\n", style=INK)
        return text

    # ---- the chosen branch ---------------------------------------------------
    def detail(self) -> Text:
        pr = self.selected()
        if pr is None:
            return Text("nothing selected", style=MUTED)
        prs = REPOS[self.repo]
        icon, color, label, _ = STATUS[pr.state]
        text = Text(f"{self.repo}-", style=MUTED)
        text.append(pr.name + "\n", style=f"bold {INK}")
        text.append(icon, style=color)
        text.append(f" {label} · {pr.title}\n", style=INK2)

        def key(word: str) -> None:
            text.append(f"{word:<9}", style=MUTED)

        held = pipe(pr)
        owner = pr.parent if held is None and pr.parent is not None else pr
        end_held, end = root_cause(owner)
        key("line")                                            # the wait, drawn as its plumbing
        path = chain(owner)
        for i, branch in enumerate(path):
            text.append(short(branch), style=f"bold {INK}" if branch is pr else INK)
            if i < len(path) - 1:
                text.append(" ═╗ ", style=INK2)
        if end_held is None:
            text.append("\n")
        elif end_held.state == "working":
            text.append(" ≈≈ ", style=INK2).append("flowing: an agent is at work\n", style=INK)
        else:
            yours = end_held.state in ("needs you", "no agent")
            text.append(" ▶◀ ", style=f"bold {ACCENT}" if yours else INK2)
            text.append(valve_owner(end_held) + "\n", style=f"bold {INK}" if yours else INK)

        key("opens")
        if end_held is None:
            text.append("it has no line of its own\n", style=INK2)
        elif end_held.state == "working":
            text.append(f"nothing to open · {end_held.progress}: {end_held.what}\n", style=INK2)
        elif end_held.state == "needs you":
            text.append(f"when you answer the session on {short(end)}: {end_held.what}\n", style=INK)
        elif end_held.state == "no agent":
            text.append(f"when you put an agent on {short(end)}: {end_held.what}\n", style=INK)
        else:
            text.append(f"when {end_held.on} gets to {short(end)}: {end_held.what} "
                        f"({end_held.progress})\n", style=INK)
            also = [f"{short(p)} {p.next[1]}" for name in end_held.who
                    for p in work_of(prs, name) if p is not end]
            if also:
                key("")
                text.append(clip(f"the same hand is on {', '.join(also)}", 58) + "\n", style=INK2)
        behind = next((e.behind for e in needs_effort(prs) if e.pr is pr), [])
        if behind:
            key("behind")
            text.append(", ".join(short(p) for p in behind) + " backed up behind this valve\n", style=INK2)

        key("pump")
        mine = agent(pr)
        if mine is not None:
            state = session_state(mine)
            text.append(f"{self.repo}-{pr.key}-{mine.serial}", style=INK)
            text.append(f" · {state} · {mine.note}\n", style=INK2)
        elif pr.mine:
            text.append("no agent is assigned\n", style=INK2)
        else:
            text.append(f"none of yours · {pr.author}'s branch, on GitHub\n", style=INK2)

        key("joined")                                          # its place in the pipework
        bases = {base.name: base for base in BASES.get(self.repo, [])}
        root = pr.root()
        legs = [(root.base, sha(self.repo, root.base, root.behind))]
        while bases.get(legs[0][0]) and bases[legs[0][0]].forks_from:
            base = bases[legs[0][0]]
            legs.insert(0, (base.forks_from, sha(self.repo, base.forks_from, base.fork_behind)))
        for name, commit in legs:
            text.append(name, style=INK).append(" ╫ ", style=MUTED)
            text.append(commit, style=INK2).append(" ═ ", style=MUTED)
        trail, here = [], pr
        while here is not None:
            trail.insert(0, here)
            here = here.parent
        text.append(" ═ ".join(short(p) if p.state != "findings" else "review" for p in trail) + "\n",
                    style=INK2)

        key("names")
        off = [w for w in pr.workers if w.name]
        if off:
            text.append("≠ ", style=CRIT)
            text.append(clip(f"session is named {off[0].name}, expected {self.repo}-{pr.key}-{off[0].serial}",
                             58) + "\n", style=INK)
        else:
            text.append("✓ ", style=GOOD)
            parts = ["branch"] + (["session"] if pr.mine and pr.workers else []) + \
                    (["PR"] if pr.is_pr else []) + ["Jira"]
            text.append(", ".join(parts) + " agree"
                        + ("" if pr.is_pr or pr.state == "findings" else f" · issue #{pr.number} holds the number")
                        + "\n", style=INK2)

        key("review")
        latest = verdicts(pr)
        for who, event in latest.items():
            mark, mark_color, words = WORD[event.verdict]
            text.append(mark, style=mark_color)
            text.append(f" {'you' if who == ME else who} {words} {event.age}   ", style=INK2)
        if not latest:
            text.append("nobody asked yet   ", style=INK2)
        if pr.threads[1]:
            text.append(f"{pr.threads[0]} of {pr.threads[1]} threads open", style=INK2)
        text.append("\n")
        key("checks")
        text.append({"pass": "✓ ", "fail": "✗ ", "run": "… ", "none": "· "}[pr.ci],
                    style={"pass": GOOD, "fail": CRIT}.get(pr.ci, MUTED))
        text.append(" · ".join(part for part in (pr.ci_note, pr.merge) if part) or "none yet", style=INK2)
        return text

    def legend(self) -> Text:
        text = Text()

        def entry(mark: str, style: str, meaning: str) -> None:
            text.append(mark, style=style)
            text.append(f" {meaning}   ", style=MUTED)

        entry("≈≈", INK2, "flowing: an agent at work")
        entry("══", INK2, "still: an agent waits behind the valve")
        entry("┄┄", AXIS, "dry: no agent on it")
        entry("╚", INK2, "ties into the line it waits on")
        text.append("\n")
        entry("▷◁", MUTED, "open valve")
        entry("▶◀", f"bold {ACCENT}", "closed, you can open it")
        entry("▶◀", INK2, "closed, a person holds it")
        entry(SPIN[2], INK, "pump running")
        entry("□", MUTED, "pump stopped")
        entry("╥ ╫", MUTED, "commits on a base")
        text.append("\n")
        for state, (icon, color, label, _) in STATUS.items():
            short_label = {"ready": "ready", "rereview": "re-review", "changes": "changes asked",
                           "local": "local", "findings": "review notes"}.get(state, label)
            entry(icon, color, short_label)
        entry("✗", CRIT, "check failing")
        return text

    # ---- keys ----------------------------------------------------------------
    def action_move(self, step: int) -> None:
        picks = self.branches()
        if picks:
            self.sel = max(0, min(len(picks) - 1, self.sel + step))
            self.draw_all()

    def action_next_valve(self) -> None:
        found = needs_effort(REPOS[self.repo])
        if not found:
            return
        self.order_at = (self.order_at + 1) % len(found)
        rows, picks = self.rows(), self.branches()
        for n, i in enumerate(picks):
            if rows[i].pr is found[self.order_at].pr:
                self.sel = n
        self.draw_all()

    def action_pause(self) -> None:
        self.paused = not self.paused
        self.draw_all()

    def action_repo(self, step: int) -> None:
        names = list(REPOS)
        self.repo = names[(names.index(self.repo) + step) % len(names)]
        self.sel, self.top, self.order_at = 0, 0, -1
        self.draw_all()


if __name__ == "__main__":
    StudioV3B().run()
