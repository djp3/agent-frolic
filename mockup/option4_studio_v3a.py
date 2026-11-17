"""Agent Frolic mock-up, option 4 v3a: relay. The hierarchy stays down the left, every base,
commit and stack at once. The ribbon on the right no longer looks back; it looks forward.
Each branch is a relay: the ordered legs that still have to be run before it lands (reply to
threads, mira looks again, merge), one tile per handoff, read left to right from "now".

- The first tile is whoever holds the baton now. Dots turn where one of your agents is
  carrying it. A blue dot means the baton is on the ground and you can pick it up: a session
  at its prompt ("you"), or a move with no agent on it ("nobody"). A hollow dot is someone
  else's hands. The lit part of a tile is how far that leg has got (2 of 6 files).
- A branch cannot land before the one below it, so its merge is pushed right until that one
  has merged, and a thin line hands the baton down the stack. The merges fall into a
  staircase: the order things will land in is the picture.
- A dotted run is a branch waiting for a handoff from another branch.
- What needs effort is numbered by urgency, in the band at the top and again in the gutter
  beside the branch, so the list and the map are the same thing.

Every name and number is made up. The legs are worked out from each branch's state; the
schedule is counted in handoffs, not hours.

    .venv/bin/python mockup/option4_studio_v3a.py

Keys: up/down pick a branch, n jumps to the next thing needing effort (most urgent first),
tab / shift+tab switch repo, q quits.
"""
from __future__ import annotations

from dataclasses import dataclass

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Static

from frolic_v3 import (ACCENT, AXIS, BASES, CRIT, GOOD, INK, INK2, ME, MUTED, PLANE, PR, RAISED,
                       REPOS, SPIN, STATUS, SURFACE, WORD, Canvas, Pipe, Row, agent, broken,
                       chain, flat, needs_effort, people_line, pipe, root_cause, session_state,
                       sessions, short, shown, top_line, tree_rows, verdicts)

X_TREE, TREE_W = 4, 48           # the gutter holds the selection bar and the urgency number
X_RELAY = X_TREE + TREE_W + 2    # where "now" begins
CW = 15                          # one handoff: a 14-cell tile and a 1-cell gap for the hand-down
RAIL = "#5c5b56"                 # connectors, in the tree and in the relay
NOW, LATER, FILL = RAISED, SURFACE, "#474742"
ORD = ["now", "next", "3rd", "4th", "5th", "6th", "7th", "8th", "9th", "10th", "11th", "12th"]
VERBS = (("merge", "merge"), ("post the review", "post"), ("review", "review"), ("reply", "reply"),
         ("answer", "reply"), ("rebase", "rebase"), ("finish the draft", "draft"),
         ("open the pr", "open"), ("fix", "fix"), ("settle", "settle"))
JOIN = {("│", "╰"): "├", ("╰", "│"): "├", ("├", "│"): "├", ("├", "╰"): "├",
        ("╮", "│"): "┤", ("│", "╮"): "┤", ("┤", "│"): "┤"}


def clip(text: str, width: int) -> str:
    return text if len(text) <= width else text[:max(0, width - 1)] + "…"


def verb_of(what: str) -> str:
    low = what.lower()
    return next((verb for needle, verb in VERBS if needle in low), low.split()[0][:6])


# ---- the relay: the legs a branch still has to run, and when each can start -------------------

@dataclass
class Leg:
    who: str                     # "you", a name, several names, or "" when not known yet
    verb: str                    # one short word for the tile
    words: str                   # the move in full, for the detail pane
    col: int = 0                 # handoffs from now
    after: tuple[PR, int] | None = None      # the branch, and its leg, that must finish first


def plan(pr: PR) -> list[Leg]:
    """The legs left before a branch lands, in order, from its state."""
    held = pipe(pr)
    if held is None:
        return []
    author = shown(pr.author)
    legs = [Leg(", ".join(held.who), verb_of(held.what), held.what)]
    if legs[0].verb == "merge":
        return legs
    pending = ", ".join(shown(who) for who, event in verdicts(pr).items() if event.verdict != "approved")
    if author in held.who:                       # the author moves first, then it is looked at
        if pr.state == "local" and legs[0].verb != "open":
            legs.append(Leg(author, "open", "open the PR"))
        if pending or pr.state != "conflict":
            legs.append(Leg(pending, "review", "review it"))
    legs.append(Leg(author, "merge", "merge"))
    return legs


def schedule(prs: list[PR]) -> dict[int, list[Leg]]:
    """Every branch's legs with a column each. A branch that waits on another starts after
    that one's leg; a merge never comes before the merge of the branch below it."""
    plans = {id(pr): plan(pr) for pr in flat(prs)}
    placed: set[int] = set()

    def place(pr: PR) -> None:
        if id(pr) in placed:
            return
        placed.add(id(pr))
        legs = plans[id(pr)]
        if not legs:
            return
        start, after = 0, None
        held = pipe(pr)
        if held and held.on_pr is not None:      # it waits on another branch
            place(held.on_pr)
            theirs = plans.get(id(held.on_pr), [])
            if theirs:
                lands = "land" in held.what or "merge after" in held.what
                ref = theirs[-1] if lands else theirs[0]
                start, after = ref.col + 1, (held.on_pr, ref.col)
        parent_merge = None
        if pr.parent is not None:
            place(pr.parent)
            parent_merge = (plans.get(id(pr.parent)) or [None])[-1]
        for i, leg in enumerate(legs):
            leg.col = start if i == 0 else legs[i - 1].col + 1
            if i == 0:
                leg.after = after
            if leg.verb == "merge" and parent_merge is not None and parent_merge.col + 1 >= leg.col:
                leg.col, leg.after = parent_merge.col + 1, (pr.parent, parent_merge.col)

    for pr in flat(prs):
        place(pr)
    return plans


class StudioV3A(App):
    CSS = f"""
    Screen {{ background: {PLANE}; color: {INK}; }}
    #top {{ height: 1; margin: 0 2; }}
    #band {{ height: 3; margin: 1 2 0 2; }}
    #board {{ height: 1fr; margin: 1 2 0 2; }}
    #details {{ height: 9; margin: 1 1 0 1; }}
    .pane {{ background: {SURFACE}; padding: 1 2; margin: 0 1; height: 9; width: 1fr; }}
    #legend {{ height: 3; margin: 1 2 0 2; }}
    #hints {{ height: 1; margin: 0 2; color: {MUTED}; }}
    #top, #band, #board, .pane, #legend, #hints {{ text-wrap: nowrap; text-overflow: ellipsis; }}
    """
    BINDINGS = [
        Binding("up", "move(-1)", show=False), Binding("down", "move(1)", show=False),
        Binding("n", "next_effort", show=False),
        Binding("tab", "repo(1)", show=False, priority=True),
        Binding("shift+tab", "repo(-1)", show=False, priority=True),
        Binding("q", "quit", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.frame = 0
        self.offset = 0
        self.load(next(iter(REPOS)))

    def compose(self) -> ComposeResult:
        yield Static(id="top")
        yield Static(id="band")
        yield Static(id="board")
        with Horizontal(id="details"):
            yield Static(id="identity", classes="pane")
            yield Static(id="relay", classes="pane")
        yield Static(id="legend")
        yield Static("↑↓ branch    n next thing needing effort    ⇥ next repo    q quit", id="hints")

    def on_mount(self) -> None:
        self.set_interval(1 / 8, self.tick)
        self.call_after_refresh(self.draw)

    def on_resize(self) -> None:
        self.call_after_refresh(self.draw)

    # ---- state -------------------------------------------------------------
    def load(self, repo: str) -> None:
        self.repo = repo
        self.prs = REPOS[repo]
        self.rows: list[Row] = tree_rows(repo)
        self.legs = schedule(self.prs)
        self.row_of = {id(row.pr): i for i, row in enumerate(self.rows) if row.pr is not None}
        self.effort = needs_effort(self.prs)
        self.rank = {id(e.pr): i + 1 for i, e in enumerate(self.effort)}
        branches = [row.pr for row in self.rows if row.pr is not None]
        self.sel: PR | None = self.effort[0].pr if self.effort else (branches[0] if branches else None)
        self.offset = 0

    def tick(self) -> None:
        self.frame += 1
        self.query_one("#top", Static).update(top_line(self.repo, frame=self.frame))
        self.query_one("#board", Static).update(self.board())
        self.query_one("#relay", Static).update(self.relay_pane())

    def draw(self) -> None:
        self.query_one("#top", Static).update(top_line(self.repo, frame=self.frame))
        self.query_one("#band", Static).update(self.band())
        self.query_one("#board", Static).update(self.board())
        self.query_one("#identity", Static).update(self.identity_pane())
        self.query_one("#relay", Static).update(self.relay_pane())
        self.query_one("#legend", Static).update(self.legend())

    # ---- the band: what needs effort, most urgent first ----------------------
    def band(self) -> Text:
        width = max(40, self.size.width - 4)
        label = 14
        text = Text("needs effort".ljust(label), style=MUTED)
        if not self.effort:
            text.append("everything that can move is moving" if flat(self.prs) else "nothing open here",
                        style=MUTED)
        used, lines = label, 1
        for i, e in enumerate(self.effort):
            legs = self.legs.get(id(e.pr)) or []
            who = {"needs you": "you", "no agent": "nobody"}.get(e.kind, e.who)
            said = f"{short(e.pr)} {who} {legs[0].verb if legs else ''}".rstrip()
            if e.behind:
                said += f", holds up {', '.join(short(p) for p in e.behind)}"
            need = len(str(i + 1)) + 2 + len(said) + 3
            if used + need > width:
                if lines == 2:
                    text.append("…", style=MUTED)
                    break
                text.append("\n" + " " * label)
                used, lines = label, 2
            text.append(f"{i + 1} ", style=f"bold {INK}" if e.yours else MUTED)
            text.append("●" if e.yours else "○", style=ACCENT if e.yours else MUTED)
            text.append(said + "   ", style=INK if e.yours else INK2)
            used += need
        text.append("\n")
        text.append("people".ljust(label), style=MUTED)
        return text.append_text(people_line(self.prs))

    # ---- the board: the hierarchy, and each branch's relay ---------------------
    def board(self) -> Text:
        widget = self.query_one("#board", Static)
        width = max(60, self.size.width - 4)
        height = max(2, widget.size.height or len(self.rows) + 1)
        room = height - 1
        sel_row = self.row_of.get(id(self.sel), 0)
        if sel_row < self.offset + 1:                         # keep a line of context above
            self.offset = max(0, sel_row - 1)
        if sel_row >= self.offset + room:
            self.offset = sel_row - room + 1
        self.offset = max(0, min(self.offset, max(0, len(self.rows) - room)))

        canvas = Canvas(width, height)
        furthest = max((leg.col for legs in self.legs.values() for leg in legs), default=0)
        cols = max(1, min(furthest + 1, (width - X_RELAY) // CW))
        canvas.put(X_TREE, 0, "bases, commits and stacks", MUTED)
        if self.offset or len(self.rows) > room:
            canvas.put(X_TREE + 27, 0, f"rows {self.offset + 1}–{min(len(self.rows), self.offset + room)}"
                                       f" of {len(self.rows)}", RAIL)
        for c in range(cols):
            canvas.put(X_RELAY + c * CW + 1, 0, ORD[c], f"bold {INK2}" if c == 0 else MUTED)

        def y_of(index: int) -> int | None:
            y = 1 + index - self.offset
            return y if 1 <= y < height else None

        for i, row in enumerate(self.rows):
            y = y_of(i)
            if y is None:
                continue
            self.draw_tree(canvas, y, row)
            if row.pr is not None:
                self.draw_relay(canvas, y, row.pr, cols, width)

        for i, row in enumerate(self.rows):                   # the baton handed down the stack
            for leg in self.legs.get(id(row.pr), []) if row.pr is not None else []:
                if leg.after is None or leg.col >= cols:
                    continue
                source, source_col = leg.after
                start = self.row_of.get(id(source))
                if start is None or start >= i:
                    continue
                x = X_RELAY + (source_col + 1) * CW - 1
                lit = row.pr is self.sel or source is self.sel
                for index in range(start, i + 1):
                    y = y_of(index)
                    if y is None:
                        continue
                    glyph = "╮" if index == start else ("╰" if index == i else "│")
                    glyph = JOIN.get((canvas.cells[y][x][0], glyph), glyph)
                    canvas.put(x, y, glyph, INK2 if lit else RAIL)
        return canvas.text()

    def draw_tree(self, canvas: Canvas, y: int, row: Row) -> None:
        canvas.put(X_TREE, y, row.lead, RAIL)
        x = X_TREE + len(row.lead)
        room = TREE_W - len(row.lead)
        if row.kind == "base":
            canvas.put(x, y, row.label, f"bold {INK}")
            base = next((b for b in BASES.get(self.repo, []) if b.name == row.label), None)
            if base is not None and base.forks_from:
                canvas.put(x + len(row.label) + 2, y,
                           clip(f"cut from {base.forks_from} here, {base.ahead} commits on",
                                room - len(row.label) - 2), MUTED)
            return
        if row.kind == "commit":
            canvas.put(x, y, row.sha, MUTED)
            canvas.put(x + len(row.sha) + 1, y, row.label, RAIL)
            return
        pr = row.pr
        icon, color, _, _ = STATUS[pr.state]
        canvas.put(x, y, icon, color)
        name_room = room - 2 - (2 if broken(pr) and pr.state != "conflict" else 0)
        key = clip(pr.key, name_room)
        canvas.put(x + 2, y, key, f"bold {INK}" if pr.state != "findings" else INK2)
        slug = clip("-" + pr.slug, name_room - len(key)) if name_room > len(key) + 1 else ""
        canvas.put(x + 2 + len(key), y, slug, INK2 if pr.state != "findings" else MUTED)
        if broken(pr) and pr.state != "conflict":
            canvas.put(x + 3 + len(key) + len(slug), y, "✗", CRIT)
        rank = self.rank.get(id(pr))
        if rank:
            mine = self.effort[rank - 1].yours
            canvas.put(1, y, str(rank).rjust(2), f"bold {INK}" if mine else MUTED)
        if pr is self.sel:
            canvas.paint(3, y, X_RELAY - 4, RAISED)
            canvas.put(0, y, "▌", INK)

    def draw_relay(self, canvas: Canvas, y: int, pr: PR, cols: int, width: int) -> None:
        legs = self.legs.get(id(pr), [])
        selected = pr is self.sel
        if not legs:
            canvas.put(X_RELAY + 1, y, "review notes", RAIL)
            return
        held = pipe(pr)
        reach = -1                                            # the last column drawn so far
        for i, leg in enumerate(legs):
            if leg.col >= cols:
                canvas.put(width - 1, y, "›", MUTED)
                break
            if leg.col > reach + 1:                           # waiting for a handoff
                canvas.put(X_RELAY + (reach + 1) * CW, y,
                           "┄" * ((leg.col - reach - 1) * CW - 1), INK2 if selected else AXIS)
            self.draw_tile(canvas, X_RELAY + leg.col * CW, y, leg, i, held, selected)
            reach = leg.col
            if leg is legs[-1] and leg.col + 1 < cols:        # where it ends: the base it lands on
                canvas.put(X_RELAY + (leg.col + 1) * CW + 1, y, f"▸ {pr.root().base}", RAIL)

    def draw_tile(self, canvas: Canvas, x: int, y: int, leg: Leg, index: int, held: Pipe | None,
                  selected: bool) -> None:
        width = CW - 1
        names = [name.strip() for name in leg.who.split(",") if name.strip()]
        holder = names[0] + (f"+{len(names) - 1}" if len(names) > 1 else "") if names else ""
        now = index == 0 and leg.col == 0 and held is not None
        if not now:                                           # a leg still to come
            background, mark, mark_color = LATER, " ", MUTED
            ink = INK2 if selected else MUTED
        elif held.state == "working":                         # an agent is carrying it
            background, mark, mark_color = NOW, SPIN[self.frame % len(SPIN)], INK
            holder, ink = (held.agent.serial if held.agent else "you"), f"bold {INK}"
        elif held.state in ("needs you", "no agent"):         # on the ground: yours to pick up
            background, mark, mark_color = NOW, "●", ACCENT
            holder, ink = ("you" if held.state == "needs you" else "nobody"), f"bold {INK}"
        else:                                                 # in someone else's hands
            background, mark, mark_color, ink = NOW, "○", MUTED, INK2
        body = f"{holder} {leg.verb}".strip()
        if len(body) > width - 1 and leg.verb == "review":
            body = f"{holder} look"
        body = clip(body, width - 1).ljust(width - 1)
        canvas.put(x, y, mark, f"{mark_color} on {background}")
        done = 0
        if now and held.total and held.done and (held.partly or held.state == "working"):
            done = max(1, round((width - 1) * held.done / held.total))
        canvas.put(x + 1, y, body[:done], f"{ink} on {FILL}")
        canvas.put(x + 1 + done, y, body[done:], f"{ink} on {background}")

    # ---- the two panes under the board ---------------------------------------
    def identity_pane(self) -> Text:
        pr = self.sel
        text = Text()
        if pr is None:
            return text.append("nothing open here", style=MUTED)
        icon, color, label, _ = STATUS[pr.state]
        text.append(f"{self.repo}-", style=MUTED)
        text.append(pr.name + "\n", style=f"bold {INK}")
        text.append(icon, style=color)
        text.append(f" {label}", style=INK2)
        if broken(pr) and pr.state != "conflict":
            text.append("   ✗", style=CRIT).append(" a check is failing", style=INK2)
        text.append(f" · {pr.title}\n", style=INK2)

        def line(key: str, value: Text | str) -> None:
            text.append(f"{key:<12}", style=MUTED)
            text.append_text(value if isinstance(value, Text) else Text(value, style=INK))
            text.append("\n")

        row = self.rows[self.row_of[id(pr)]]
        commit = next((r for r in self.rows if r.kind == "commit" and r.base == row.base
                       and r.behind == row.behind), None)
        where = "the tip of" if not row.behind else f"{row.behind} commits back on"
        line("hangs from", f"{commit.sha if commit else '?'}, {where} {row.base}"
             + (f" · on top of {short(pr.parent)}" if pr.parent else ""))

        off = [w for w in pr.workers if w.name]
        if off:
            line("names", Text("≠ ", style=CRIT).append(
                f"the session is named {off[0].name}, not …-{pr.key}-{off[0].serial}", style=INK))
        else:
            parts = ["branch"] + (["session"] if pr.mine and pr.workers else []) \
                + (["PR"] if pr.is_pr else []) + ["Jira"]
            said = Text("✓ ", style=GOOD).append(", ".join(parts) + " agree", style=INK)
            if not pr.is_pr and pr.state != "findings":
                said.append(f" · issue #{pr.number} holds the number", style=INK2)
            line("names", said)

        said = Text()
        for who, event in verdicts(pr).items():
            mark, mark_color, words = WORD[event.verdict]
            said.append(mark, style=mark_color)
            said.append(f" {'you' if who == ME else who} {words} {event.age}   ", style=INK)
        if pr.threads[1]:
            said.append(f"{pr.threads[0]} of {pr.threads[1]} threads open", style=INK2)
        line("review", said if said.plain else Text("nobody has been asked yet", style=INK2))

        mark, mark_color = {"pass": ("✓ ", GOOD), "fail": ("✗ ", CRIT)}.get(pr.ci, ("· ", MUTED))
        line("checks", Text(mark, style=mark_color)
             .append(" · ".join(x for x in (pr.ci_note, pr.merge) if x), style=INK))

        on_it = agent(pr)
        if on_it is not None:
            state = session_state(on_it)
            said = Text(SPIN[self.frame % len(SPIN)] if state == "working" else "▷ ",
                        style=INK if state == "working" else (ACCENT if state == "at its prompt" else MUTED))
            if state == "working":
                said.append(" ")
            said.append(f"session {on_it.serial}", style=INK)
            said.append(f" · {on_it.note}", style=INK2)
            line("session", said)
        elif pr.mine or sessions(pr):
            ended = sessions(pr)
            line("session", Text("none", style=INK).append(
                f" · the last one: {ended[-1].note}" if ended else "", style=INK2))
        else:
            line("session", Text(f"none of yours · {pr.author}'s branch, on GitHub", style=INK2))
        return text

    def relay_pane(self) -> Text:
        pr = self.sel
        text = Text()
        if pr is None:
            return text
        legs = self.legs.get(id(pr), [])
        held = pipe(pr)
        text.append("the relay", style=f"bold {INK}")
        text.append("   what still has to happen before it lands, in order\n", style=MUTED)
        if not legs:
            text.append(f"none of its own: these are notes from your review of {short(pr)}\n", style=INK2)
        for i, leg in enumerate(legs[:4]):
            text.append(f"{ORD[min(leg.col, len(ORD) - 1)]:<6}", style=MUTED)
            now = i == 0 and leg.col == 0 and held is not None
            if not now:
                text.append("  ")
                text.append(f"{leg.who or 'a reviewer'} · {leg.words if i == 0 else leg.verb}", style=INK2)
                if leg.after is not None and "once" not in leg.words:
                    source, source_col = leg.after
                    theirs = self.legs.get(id(source), [])
                    ended = theirs and theirs[-1].col == source_col
                    text.append(f", once {short(source)} " + ("has merged" if ended else "settles"),
                                style=MUTED)
            elif held.state == "working":
                text.append(SPIN[self.frame % len(SPIN)] + " ", style=INK)
                text.append(f"session {held.agent.serial if held.agent else '?'} is carrying it: "
                            f"{leg.words}", style=f"bold {INK}")
                text.append(f" · {held.counted}" if held.counted else "", style=INK2)
            elif held.state == "needs you":
                text.append("● ", style=ACCENT)
                text.append(f"at session {held.agent.serial if held.agent else '?'}'s prompt, "
                            f"waiting for you: {leg.words}", style=f"bold {INK}")
            elif held.state == "no agent":
                text.append("● ", style=ACCENT)
                text.append(f"nobody has it: {leg.words}", style=f"bold {INK}")
                text.append(" · no agent is assigned", style=INK2)
            else:
                text.append("○ ", style=MUTED)
                text.append(f"{leg.who} {'have' if ',' in leg.who else 'has'} it: {leg.words}", style=INK)
                text.append(f" · {held.progress}", style=INK2)
            text.append("\n")
        if len(legs) > 4:
            text.append(f"      and {len(legs) - 4} more, ending with {legs[-1].who} · merge\n", style=MUTED)

        end_pipe, end = root_cause(pr)
        if end_pipe is not None:
            text.append("gets going  ", style=MUTED)
            if end_pipe.state == "working":
                text.append("it is moving" + ("" if end is pr else f": {short(end)} is being worked on"),
                            style=INK)
            elif end_pipe.state == "needs you":
                text.append("● ", style=ACCENT).append(f"when you answer the session on {short(end)}", style=INK)
            elif end_pipe.state == "no agent":
                text.append("● ", style=ACCENT).append(f"when you put an agent on {short(end)}", style=INK)
            else:
                text.append("○ ", style=MUTED)
                text.append(f"when {end_pipe.on} gets to {short(end)}", style=INK)
                text.append(f" ({end_pipe.progress})", style=INK2)
            text.append("\n")
        queued = [p for p in flat(self.prs) if p is not pr and pr in chain(p)]
        above = [p for p in flat([pr]) if p is not pr and p.state != "findings"]
        if queued or above:
            text.append("holds up    ", style=MUTED)
            if queued:
                text.append(", ".join(short(p) for p in queued) + " wait on it", style=INK)
            rest = [p for p in above if p not in queued]
            if rest:
                text.append((" · " if queued else "") + ", ".join(short(p) for p in rest)
                            + " cannot land before it", style=INK2)
        return text

    def legend(self) -> Text:
        text = Text()
        short_label = {"ready": "ready", "rereview": "re-review", "changes": "changes asked",
                       "local": "local", "findings": "review notes"}
        for state, (icon, color, label, _) in STATUS.items():
            text.append(icon, style=color)
            text.append(f" {short_label.get(state, label)}   ", style=MUTED)
        text.append("✗", style=CRIT).append(" check failing\n", style=MUTED)
        text.append(SPIN[0], style=INK).append(" an agent is carrying it   ", style=MUTED)
        text.append("●", style=ACCENT).append(" on the ground, yours to pick up   ", style=MUTED)
        text.append("○", style=MUTED).append(" in someone else's hands   ", style=MUTED)
        text.append("  ", style=f"on {FILL}").append("  ", style=f"on {NOW}")
        text.append(" how far that leg has got\n", style=MUTED)
        text.append("┄", style=MUTED).append(" waits for a handoff   ", style=MUTED)
        text.append("╮╰", style=MUTED)
        text.append(" handed down the stack: it cannot land before the one below   ", style=MUTED)
        text.append("▸ main", style=RAIL).append(" where it lands   ", style=MUTED)
        text.append("1 2", style=f"bold {INK}").append(" most urgent first", style=MUTED)
        return text

    # ---- keys --------------------------------------------------------------
    def action_move(self, step: int) -> None:
        branches = [row.pr for row in self.rows if row.pr is not None]
        if not branches:
            return
        here = branches.index(self.sel) if self.sel in branches else 0
        self.sel = branches[max(0, min(len(branches) - 1, here + step))]
        self.draw()

    def action_next_effort(self) -> None:
        if not self.effort:
            return
        order = [e.pr for e in self.effort]
        self.sel = order[(order.index(self.sel) + 1) % len(order)] if self.sel in order else order[0]
        self.draw()

    def action_repo(self, step: int) -> None:
        names = list(REPOS)
        self.load(names[(names.index(self.repo) + step) % len(names)])
        self.draw()


if __name__ == "__main__":
    StudioV3A().run()
