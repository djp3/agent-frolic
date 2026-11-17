"""Agent Frolic mock-up, option 4b v4: THE TOWER. The repo is an airfield seen from the
control tower, and you are the controller. A controller works from a board of flight
strips: one strip per flight, the callsign first and heaviest, racked in bays, in order.
Here a strip is a branch, and its callsign is WHO OWNS IT.

- Top line: the one every v4 uses.
- ON FREQUENCY, under it: everyone who owns a strip or is waited on, raised to the top of
  the screen. Your agents by state, each named with its branch; the strips with no crew;
  then the other people, each with the strips that hold for them, or "nothing to do". Each
  group is headed by the tower's word and the plain one beside it, so this block is also
  the key to the words on the strips. Left/right picks someone and lights their strips in
  place; nothing is regrouped.
- THE BOARD: every base and every stack at once, in one connected rack. A base is a bay; a
  commit is a divider carrying its short hash; a stack is strips racked one under another;
  a second base hangs from the commit it was cut from. Each strip reads left to right:
  owner (the callsign block), the owner's state, WAITS ON (the sequence, to where the wait
  ends: "pr-0024 → mira"), then the branch, review, checks, progress and how long quiet.
  The strip in hand opens to three lines.
- CLEARANCES TO GIVE: what needs effort to get moving, in order of urgency (is an agent
  waiting on it, then how long it has been quiet), each line saying why. Beside it the
  strip in hand, in plain words, owner and waits-on first.

The tower's words, each always beside a plain one: cleared = working; hold short = the
agent sits at its prompt and asks your clearance; holding = idle until someone else moves;
no crew = your move, and no agent is on it.

Blue means one thing: idle, and you can fix it. The dots turn where an agent works;
nothing else moves. Every name and number is made up.

    .venv/bin/python mockup/option4_studio_v4b.py

Keys: up/down pick a strip, n jumps to the next clearance to give (most urgent first),
left/right pick someone on frequency and light their strips (esc clears), l swaps the
bottom band for the legend, tab goes to the next repo (shift+tab back), q quits.
"""
from __future__ import annotations

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Static

from frolic_v4 import (ACCENT, AXIS, CRIT, GOOD, HAIR, INK, INK2, ME, MUTED, PLANE, PR, RAISED,
                       REPOS, SPIN, STATUS, SURFACE, WARN, WORD, Canvas, Member, Row, Segments,
                       cast, chain, flat, is_stale, needs_effort, owned_by, owner, pipe, short,
                       top_line, tree_rows, verdicts, waits, waits_segments, work_of, write)

# a strip, left to right: the callsign block, the owner's state, what it waits on, the branch
BLOCK_W, X_STATE, X_WAIT, X_TREE = 13, 14, 25, 48
WAIT_W = X_TREE - X_WAIT - 1
CHIP_X = 38                      # where the names start on the frequency block
FIXED = 4                        # rows that are not the frequency block, the board or the band
BOARD_MAX = 9                    # the frequency block wraps, up to this many rows
BAND_MIN, BAND_MAX = 8, 11       # the bottom band, its top border included
PANE_W = 47                      # the strip in hand, padding included
DIM = "#5c5b56"                  # strips set aside while someone else's are lit
STRIP_A, STRIP_B = SURFACE, "#141413"        # two tints, so each strip reads as its own band
BLOCK, BLOCK_HOT = RAISED, "#3a3a37"         # behind a callsign; behind the one in hand
CI = {"pass": ("✓", GOOD), "fail": ("✗", CRIT), "run": ("…", WARN), "none": ("·", AXIS)}

# the tower's word for each state, and the plain words always shown beside it
WORDS = (
    ("working", "CLEARED", "working"),
    ("at its prompt", "HOLD SHORT", "at its prompt, asks you"),
    ("no agent", "NO CREW", "your move, no agent"),
    ("waiting", "HOLDING", "idle, waits on someone"),
    ("people", "PEOPLE", "strips held for them"),
)
TOWER = {"working": "cleared", "at its prompt": "hold short", "waiting": "holding"}


# ---- one cell at a time ---------------------------------------------------------------------

def callsign(pr: PR, spin: str) -> tuple[str, str, str, str]:
    """The owner as a callsign: (mark, its style, NAME, its style). The mark turns while the
    agent works, is a blue ▷ at its prompt and a blue ● where your move has no crew."""
    who = owner(pr)
    if who.kind == "agent":
        mark, mark_style, ink = {
            "working": (spin, INK, INK), "at its prompt": ("▷", ACCENT, INK),
            "waiting": ("·", MUTED, INK2)}[who.state]
        return mark, mark_style, who.name.upper(), f"bold {ink}"
    if who.kind == "no agent":
        return ("●", ACCENT, "NO CREW", f"bold {INK}") if who.fixable else (" ", "", "NO CREW", MUTED)
    if who.kind == "person":
        return " ", "", who.name.upper(), f"bold {INK2}"
    return " ", "", "NOTES", MUTED


def state_word(pr: PR) -> tuple[str, str]:
    """The owner's state in the tower's word. The plain one is the heading of its group on
    the frequency block, right above the board."""
    who = owner(pr)
    if who.kind == "agent":
        return TOWER[who.state], INK if who.fixable else MUTED
    if who.kind == "no agent":
        return "no agent", INK if who.fixable else MUTED
    wait = waits(pr)
    return ("holding", MUTED) if wait is not None and wait.kind != "working" else ("", MUTED)


def review_segments(pr: PR) -> Segments:
    latest = verdicts(pr)
    if not latest:
        return [("·", AXIS)]
    out: Segments = [(f"r{max(e.round for e in latest.values())} ", MUTED)]
    out += [(WORD[e.verdict][0], WORD[e.verdict][1]) for e in latest.values()]
    if pr.threads[0]:
        out += [(" ◆", MUTED), (str(pr.threads[0]), INK)]
    return out


def progress_segments(pr: PR) -> Segments:
    """ "in progress" only while an agent works; otherwise "stalled, …" or "not started". """
    here = pipe(pr)
    if here is None:
        return [(f"review notes for {short(pr.parent)}" if pr.parent else "review notes", MUTED)]
    if here.state == "working":
        return [(here.progress, INK2)]
    return [(here.progress, INK if here.partly else MUTED)]


def append(text: Text, segments: Segments) -> Text:
    for part, style in segments:
        text.append(part, style=style)
    return text


def shorts(prs: list[PR]) -> str:
    return " ".join(short(p) for p in prs)


# ---- on frequency: everyone, raised to the top ----------------------------------------------

def frequency(prs: list[PR]) -> tuple[Member, list[tuple[str, str, str, list[Member]]], list[Member]]:
    """You, then the cast grouped the way the frequency block shows it, then everyone in the
    order left/right steps through them."""
    members = cast(prs)
    you = members[0]
    agents = [m for m in members if m.kind == "agent"]
    people = [m for m in members if m.kind == "person"]
    groups = []
    for key, word, plain in WORDS:
        found = (people if key == "people" else
                 [m for m in members if m.kind == "no agent"] if key == "no agent" else
                 [m for m in agents if m.state == key])
        groups.append((key, word, plain, found))
    return you, groups, [you] + [m for _, _, _, found in groups for m in found]


def chip(member: Member) -> Segments:
    """One name on the frequency block. An agent is always named with its branch."""
    if member.kind == "you":
        return [("you", INK), (" · the tower", MUTED)]
    if member.kind == "agent":
        out: Segments = [(member.name, INK), (" · ", MUTED), (short(member.pr), INK2)]
        wait = waits(member.pr) if member.state == "waiting" else None
        if wait is not None and wait.kind == "agent":
            out += [(" for ", MUTED), (f"{wait.end} · {short(wait.end_pr)}", INK2)]
        elif wait is not None and wait.end:
            out += [(" for ", MUTED), (wait.end, INK2)]
        return out
    if member.kind == "no agent":
        return [(short(member.pr), f"bold {INK}")]
    if member.state == "nothing to do":
        return [("● ", ACCENT), (member.name, f"bold {INK}"), (": nothing to do", INK)]
    if not member.waited_by:
        return [(member.name, INK), (": has work", MUTED)]
    return [(member.name, INK), (": ", MUTED), (shorts(member.waited_by), INK2)]


def lit_words(member: Member) -> Segments:
    """What is lit, said once on the title row of the frequency block."""
    out: Segments = [("lit  ", MUTED)]
    if member.kind == "you":
        out += [("the strips that wait on you", INK)]
        out += [(": ", MUTED), (shorts(member.waited_by), INK2)] if member.waited_by else [(": none", MUTED)]
    elif member.kind == "agent":
        out += [(f"{member.name} · {short(member.pr)}", INK)]
        if member.waited_by:
            out += [(" · held for it: ", MUTED), (shorts(member.waited_by), INK2)]
    elif member.kind == "no agent":
        out += [(short(member.pr), INK), (", which has no crew", MUTED)]
    else:
        out += [(member.name, INK)]
        if member.owns:
            out += [(" · owns ", MUTED), (shorts(member.owns), INK2)]
        if member.waited_by:
            out += [(f" · held for {member.name}: ", MUTED), (shorts(member.waited_by), INK2)]
        if not member.owns and not member.waited_by:
            out += [(" has no strip and nothing to do", MUTED)]
    return out + [("    esc clears", MUTED)]


def frequency_text(prs: list[PR], width: int, frame: int, pick: int) -> tuple[Text, int]:
    """The block under the top line, and how many rows it took. Names flow along each
    group's row and wrap under themselves when the row is full. `pick` is the place, in the
    stepping order, of whoever is picked; -1 for nobody."""
    you, groups, order = frequency(prs)
    picked = order[pick] if 0 <= pick < len(order) else None
    canvas = Canvas(width, BOARD_MAX)

    def place(member: Member, x: int, y: int) -> int:
        parts = chip(member)
        size = sum(len(part) for part, _ in parts)
        if x + size > width and x > CHIP_X:
            x, y = CHIP_X, y + 1
        write(canvas, x, y, parts, max(0, width - x))
        if picked is not None and member is picked:
            canvas.paint(x - 1, y, size + 2, BLOCK_HOT)
        return x + size + 3, y

    canvas.put(0, 0, "ON FREQUENCY", f"bold {INK}")
    place(you, X_STATE, 0)
    if picked is not None:
        write(canvas, CHIP_X, 0, lit_words(picked), width - CHIP_X)
    else:
        canvas.put(CHIP_X, 0, "everyone who owns a strip or is waited on · ◂ ▸ picks one and "
                              "lights their strips below", MUTED)
    y = 0
    for key, word, plain, found in groups:
        y += 1
        mark, style = {
            "working": (SPIN[frame % len(SPIN)] if found else SPIN[0], INK), "at its prompt": ("▷", ACCENT),
            "no agent": ("●", ACCENT), "waiting": ("·", MUTED), "people": (" ", MUTED)}[key]
        canvas.put(0, y, mark, style if found else AXIS)
        canvas.put(2, y, word, f"bold {INK}" if found else MUTED)
        canvas.put(13, y, plain, MUTED)
        if not found:
            canvas.put(CHIP_X, y, "—", AXIS)
        x = CHIP_X
        for member in found:
            x, y = place(member, x, y)
    used = min(BOARD_MAX, y + 1)
    canvas.height, canvas.cells = used, canvas.cells[:used]
    return canvas.text(), used


# ---- the bottom band --------------------------------------------------------------------------

def clearances_text(prs: list[PR], selected: PR | None, room: int, width: int) -> Text:
    """What needs effort to get moving, most urgent first. Each line leads with why it is
    urgent, then says what to do, and what it holds up."""
    text = Text("CLEARANCES TO GIVE", style=f"bold {INK}")
    text.append(" · needs effort to get moving", style=INK2)
    text.append("   by urgency: agents waiting, then quiet longest\n", style=MUTED)
    found = needs_effort(prs)
    if not found:
        return text.append("nothing: everything that can move is moving" if flat(prs)
                           else "nothing open", style=MUTED)
    key_w = max(len(short(e.pr)) for e in found) + 1
    for rank, effort in enumerate(found[:room], start=1):
        here = effort.pipe
        line = Text(f"{rank:<2}", style=MUTED)
        mark = {"needs you": "▷ ", "no agent": "● "}.get(effort.kind, "○ ")
        line.append(mark, style=ACCENT if effort.yours else MUTED)
        line.append(f"{short(effort.pr):<{key_w}}", style=f"bold {INK}")
        urgency = Text()
        if effort.waiting:
            held, quiet = effort.urgency.split(" · ", 1)
            urgency.append(held, style=f"bold {INK}").append(" · ", style=MUTED)
        else:
            quiet = effort.urgency
        urgency.append(quiet, style=f"bold {INK}" if is_stale(effort.pr.age) else INK2)
        line.append_text(urgency).append(" " * max(2, 28 - urgency.cell_len))
        if effort.kind == "needs you":
            line.append("clear it", style=INK).append(" · at its prompt · ", style=MUTED)
            line.append(here.what, style=INK)
        elif effort.kind == "no agent":
            line.append("crew it", style=INK).append(" · no agent · ", style=MUTED)
            line.append(here.what, style=INK)
        else:
            line.append(f"held for {effort.who}", style=INK).append(" · ", style=MUTED)
            line.append(f"{here.what}, {here.progress}", style=INK2)
        if effort.behind:
            line.append(" · holds up ", style=MUTED)
            line.append(", ".join(short(p) for p in effort.behind), style=INK)
        if selected is not None and (selected is effort.pr or selected in effort.behind):
            line.truncate(width)
            line.append(" " * max(0, width - line.cell_len))
            line.stylize(f"on {RAISED}")
        text.append_text(line).append("\n")
    if len(found) > room:
        text.append(f"  and {len(found) - room} more: press n to step through them\n", style=MUTED)
    return text


def review_words(pr: PR) -> Segments:
    """The last thing that happened in review, as words."""
    if not pr.history:
        return [("nobody has been asked to review yet", MUTED)] if pr.is_pr else [("no PR yet", MUTED)]
    event = pr.history[-1]
    if event.kind == "push":
        return [(f"{event.commits} commits pushed by {event.who} {event.age}", INK2)]
    mark, mark_color, _ = WORD[event.verdict]
    name = "you" if event.who == ME else event.who
    was = "were" if name == "you" else "was"
    words = {"approved": "approved", "changes": "asked for changes",
             "requested": f"{was} asked to review",
             "rerequested": f"{was} asked to look again"}[event.verdict]
    out: Segments = [(f"r{event.round} ", MUTED), (mark, mark_color), (f" {name} {words} {event.age}", INK2)]
    if pr.threads[0]:
        out.append((f" · {pr.threads[0]} open", MUTED))
    return out


def pane_text(repo: str, prs: list[PR], pr: PR) -> Text:
    """The strip in hand, in plain words: who owns it, then what it waits on."""
    text = Text()
    text.append(pr.name + "\n", style=f"bold underline {INK}")

    def kv(key: str, value: Text | str) -> None:
        text.append(f"{key:<9}", style=MUTED)
        text.append_text(value if isinstance(value, Text) else Text(value, style=INK))
        text.append("\n")

    who = owner(pr)
    if who.kind == "agent":
        said = Text(who.name, style=f"bold {INK}")
        said.append(f", {who.state}", style=INK)
        said.append(f" ({TOWER[who.state]})", style=MUTED)
    elif who.kind == "no agent":
        said = Text("no agent", style=f"bold {INK}").append(" (no crew)", style=MUTED)
        if who.fixable:
            said.append(": your move", style=INK)
    elif who.kind == "person":
        said = Text(pr.author, style=f"bold {INK}")
        said.append(", on GitHub", style=INK)
        said.append(" (agents not seen)", style=MUTED)
    else:
        said = Text("review notes; the session has ended", style=INK2)
    kv("owner", said)

    wait = waits(pr)
    if wait is None:
        kv("waits on", Text(f"nothing: these are notes for {short(pr.parent)}" if pr.parent
                            else "nothing: these are notes", style=INK2))
    elif wait.kind == "working":
        kv("waits on", Text("nothing: its agent is at work", style=INK2))
        kv("", Text(f"{wait.what} · {pipe(pr).progress}", style=INK2))
    else:
        said = Text()
        for hop in wait.links:
            said.append(short(hop), style=INK).append(" → ", style=MUTED)
        if wait.kind == "you":
            said.append("● ", style=ACCENT).append("you", style=f"bold {INK}")
        elif wait.kind == "agent":
            said.append(f"{wait.end} · {short(wait.end_pr)}", style=f"bold {INK}")
            said.append(", at work", style=INK2)
        else:
            said.append(wait.end, style=f"bold {INK}")
        kv("waits on", said)
        end = pipe(wait.end_pr)
        kv("", Text(f"to {wait.what}", style=INK2))
        kv("", Text((f"{end.progress} · " if end else "") + f"quiet {wait.end_pr.age}", style=INK2))
    queued = [p for p in flat(prs) if p is not pr and pr in chain(p)]
    if queued:
        kv("holds up", Text(shorts(queued), style=INK))

    names = Text()                                             # the four names that should read alike
    names.append("✓ ", style=GOOD).append("branch ", style=INK)
    session = who.worker if pr.mine else None
    if session is not None and session.name:
        names.append("≠ ", style=f"bold {CRIT}").append("session ", style=INK)
    elif session is not None:
        names.append("✓ ", style=GOOD).append("session ", style=INK)
    if pr.is_pr:
        names.append("✓ ", style=GOOD).append(f"PR #{pr.number} ", style=INK)
    elif pr.state != "findings":
        names.append("· ", style=MUTED).append(f"#{pr.number} reserved ", style=INK2)
    names.append("✓ ", style=GOOD).append("Jira", style=INK)
    kv("names", names)
    if session is not None and session.name:
        kv("", Text(f"is {session.name}, want {pr.key}-{session.serial}", style=INK2))
    if pr.same_ticket:
        kv("also", Text("! ", style=WARN).append(f"{pr.ticket.upper()} is on {pr.same_ticket} too", style=INK))

    if wait is not None and wait.kind == "person":             # what else holds for the same person
        for name in (n.strip() for n in wait.end.split(",")):
            others = [p for p in work_of(prs, name) if p is not wait.end_pr]
            if others:
                kv(name[:8], Text("also has ", style=MUTED).append(shorts(others[:4]), style=INK2))
    return text


def legend_text(width: int, rows: int) -> Text:
    canvas = Canvas(max(60, width), max(1, rows))
    canvas.put(0, 0, "a strip, left to right", f"bold {INK}")
    for y, (mark, style, words) in enumerate((
            ("▌ AGENT 01", f"bold {INK}", "owner: your agent on the branch, or"),
            ("▌ MIRA", f"bold {INK2}", "the person whose branch it is"),
            ("cleared", MUTED, "the owner's state, in the tower's word"),
            ("pr-0024 → mira", INK2, "waits on: read left to right to"),
            ("", "", "where the wait ends; empty while working"),
            ("├ ◔ af-44-…", INK2, "the branch, under its commit and bay"),
            ("r2 ✓✗ ◆3", MUTED, "review round, each word, open threads"),
            ("stalled, 1 of 3", MUTED, "partly done, and nobody is on it")), start=1):
        canvas.put(0, y, mark, style)
        canvas.put(17, y, words, MUTED)

    x = 58
    canvas.put(x, 0, "the tower's words", f"bold {INK}")
    for y, (word, words) in enumerate((
            ("cleared", "working: an agent of yours is on it now"),
            ("hold short", "the agent sits at its prompt, and"),
            ("", "asks for your clearance to go on"),
            ("holding", "idle until someone else moves"),
            ("NO CREW", "your move, and no agent is on it"),
            ("bay", "a base branch: main, a release"),
            ("divider", "a commit, by short hash: where a stack"),
            ("", "hangs, or where another bay was cut")), start=1):
        canvas.put(x, y, word, INK2)
        canvas.put(x + 12, y, words, MUTED)

    x = 112
    canvas.put(x, 0, "marks", f"bold {INK}")
    for y, (mark, color, words) in enumerate((
            ("● ▷", ACCENT, "idle, and you can fix it."),
            ("", "", "Blue means nothing else."),
            (SPIN[2], INK, "turns where an agent works"),
            ("·", MUTED, "an agent that is holding"),
            ("▲✓◔↻", MUTED, "PR state: ready, approved,"),
            ("✗⚠◌○◇", MUTED, "in review … local notes"),
            ("●3 ○2", MUTED, "top line: yours to fix,"),
            ("", "", "people being waited on")), start=1):
        canvas.put(x, y, mark, color)
        canvas.put(x + 7, y, words, MUTED)
    return canvas.text()


class StudioV4B(App):
    CSS = f"""
    Screen {{ background: {PLANE}; color: {INK}; }}
    #top, #gap, #cols, #hints {{ height: 1; padding: 0 1; text-wrap: nowrap; text-overflow: ellipsis; }}
    #freq {{ height: 6; padding: 0 1; }}
    #board {{ height: 1fr; padding: 0 1; }}
    #band {{ height: {BAND_MAX}; border-top: solid {HAIR}; }}
    #clear {{ width: 1fr; height: 1fr; padding: 0 1; border-right: solid {HAIR}; }}
    #pane {{ width: {PANE_W}; height: 1fr; padding: 0 1; }}
    #legend {{ width: 1fr; height: 1fr; padding: 0 1; display: none; }}
    #clear, #pane, #legend {{ text-wrap: nowrap; text-overflow: ellipsis; }}
    #band.legend #clear, #band.legend #pane {{ display: none; }}
    #band.legend #legend {{ display: block; }}
    #hints {{ color: {MUTED}; }}
    """
    BINDINGS = [
        Binding("up", "move(-1)", show=False), Binding("down", "move(1)", show=False),
        Binding("n", "next_clearance", show=False),
        Binding("left", "pick(-1)", show=False), Binding("right", "pick(1)", show=False),
        Binding("escape", "unpick", show=False), Binding("l", "toggle_legend", show=False),
        Binding("tab", "repo(1)", show=False, priority=True),
        Binding("shift+tab", "repo(-1)", show=False, priority=True),
        Binding("q", "quit", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.repo = next(iter(REPOS))
        self.sel = 0                  # index among the strips
        self.top = 0                  # first board line on screen, when the window is too short
        self.pick = -1                # index into the frequency order; -1 when nobody is picked
        self.frame = 0                # turns the dots where an agent works; nothing else moves
        self.freq_h = 6
        self.band_h = BAND_MAX

    def compose(self) -> ComposeResult:
        yield Static(id="top")
        yield Static(id="gap")
        yield Static(id="freq")
        yield Static(id="cols")
        yield Static(id="board")
        with Horizontal(id="band"):
            yield Static(id="clear")
            yield Static(id="pane")
            yield Static(id="legend")
        yield Static(id="hints")

    def on_mount(self) -> None:
        self.draw()
        self.set_interval(1 / 8, self.tick)

    def on_resize(self) -> None:
        self.draw()

    def tick(self) -> None:
        self.frame += 1
        self.query_one("#top", Static).update(top_line(self.repo, frame=self.frame))
        self.query_one("#freq", Static).update(self.freq()[0])
        self.query_one("#board", Static).update(self.board())

    # ---- state ---------------------------------------------------------------------------------
    def width(self) -> int:
        return max(80, self.size.width - 2)

    def branches(self) -> list[PR]:
        return [row.pr for row in tree_rows(self.repo) if row.pr is not None]

    def selected(self) -> PR | None:
        found = self.branches()
        return found[min(self.sel, len(found) - 1)] if found else None

    def picked(self) -> Member | None:
        order = frequency(REPOS[self.repo])[2]
        return order[self.pick] if 0 <= self.pick < len(order) else None

    def freq(self) -> tuple[Text, int]:
        return frequency_text(REPOS[self.repo], self.width(), self.frame, self.pick)

    def fit(self) -> int:
        """The frequency block takes the rows its names need; the board gets enough to show
        every bay and stack at once (the strip in hand is three lines); the band takes what
        is left, within its limits. Returns the rows the board has."""
        used = self.freq()[1]
        if used != self.freq_h:
            self.freq_h = used
            self.query_one("#freq").styles.height = used
        needed = len(tree_rows(self.repo)) + 2
        band = max(BAND_MIN, min(BAND_MAX, self.size.height - FIXED - used - needed))
        if band != self.band_h:
            self.band_h = band
            self.query_one("#band").styles.height = band
        return max(3, self.size.height - FIXED - used - band)

    # ---- drawing -------------------------------------------------------------------------------
    def draw(self) -> None:
        prs = REPOS[self.repo]
        pr = self.selected()
        self.fit()
        self.query_one("#top", Static).update(top_line(self.repo, frame=self.frame))
        self.query_one("#freq", Static).update(self.freq()[0])
        self.query_one("#board", Static).update(self.board())          # sets what #cols reports
        room = self.band_h - 3
        self.query_one("#clear", Static).update(
            clearances_text(prs, pr, room, max(20, self.size.width - PANE_W - 3)))
        self.query_one("#pane", Static).update(
            pane_text(self.repo, prs, pr) if pr is not None
            else Text("no strip in hand: nothing is open", style=MUTED))
        self.query_one("#legend", Static).update(legend_text(self.width(), self.band_h - 1))
        self.query_one("#hints", Static).update(
            "↑↓ strip    n next clearance to give    ◂ ▸ light someone's strips (esc clears)"
            "    l legend    ⇥ next repo    q quit")

    def columns(self, width: int) -> tuple[int, int, int, int]:
        """Where review, checks, progress and quiet start. A narrow window gives up progress
        (0) so the branch names keep their room."""
        quiet = width - 6
        progress = quiet - 28
        ci = progress - 4
        review = ci - 11
        if review - X_TREE < 34:
            progress, ci = 0, quiet - 4
            review = ci - 11
        return review, ci, progress, quiet

    def board(self) -> Text:
        prs = REPOS[self.repo]
        rows = tree_rows(self.repo)
        branches = [row.pr for row in rows if row.pr is not None]
        self.sel = max(0, min(len(branches) - 1, self.sel))
        selected = branches[self.sel] if branches else None
        width = self.width()
        height = self.fit()
        x_review, x_ci, x_progress, x_quiet = self.columns(width)

        lines: list[tuple[int, Row | None, int]] = []          # (row index, row, which line of it)
        for i, row in enumerate(rows):
            lines.append((i, row, 0))
            if row.pr is not None and row.pr is selected:      # the strip in hand opens
                lines += [(i, row, 1), (i, row, 2)]
        if not branches:
            lines.append((-1, None, 0))
        at = next((n for n, (_, row, _) in enumerate(lines) if row is not None and row.pr is selected
                   and selected is not None), 0)
        if len(lines) <= height:
            self.top = 0
        else:                                                  # only a short window ever scrolls
            if at - 1 < self.top:
                self.top = max(0, at - 1)
            if at + 2 >= self.top + height:
                self.top = at + 3 - height
            self.top = max(0, min(self.top, len(lines) - height))
        shown = lines[self.top:self.top + height]

        head = Canvas(width, 1)
        head.put(3, 0, "owner", MUTED)
        head.put(X_WAIT, 0, "waits on", MUTED)
        head.put(X_TREE, 0, "branch", MUTED)
        if len(lines) > height:
            head.put(X_TREE + 8, 0, f"lines {self.top + 1}–{self.top + len(shown)} of {len(lines)}", INK2)
        head.put(x_review, 0, "review", MUTED)
        head.put(x_ci, 0, "ci", MUTED)
        if x_progress:
            head.put(x_progress, 0, "progress", MUTED)
        head.put(x_quiet, 0, "quiet", MUTED)
        self.query_one("#cols", Static).update(head.text())

        member = self.picked()
        lit = None if member is None else {id(pr) for pr in owned_by(prs, member)}
        stripe = {id(pr): n % 2 for n, pr in enumerate(branches)}
        canvas = Canvas(width, max(1, len(shown)))
        for y, (i, row, part) in enumerate(shown):
            if row is None:
                canvas.put(X_TREE, y, "no strips on the board: no open pull requests or local branches", MUTED)
            elif row.kind == "base":
                self.draw_bay(canvas, y, row, width)
            elif row.kind == "commit":
                self.draw_divider(canvas, y, row, width)
            else:
                pr = row.pr
                hot = pr is selected
                dim = lit is not None and id(pr) not in lit and not hot
                if part == 0:
                    self.draw_strip(canvas, y, i, row, dim, hot, (x_review, x_ci, x_progress, x_quiet))
                else:
                    self.draw_open(canvas, y, row, part, x_review, width)
                if not dim:                                    # the strip's own band, callsign block heavier
                    canvas.paint(0, y, BLOCK_W, BLOCK_HOT if hot else BLOCK)
                    canvas.paint(BLOCK_W, y, width - BLOCK_W,
                                 RAISED if hot else (STRIP_B if stripe[id(pr)] else STRIP_A))
        return canvas.text()

    def draw_bay(self, canvas: Canvas, y: int, row: Row, width: int) -> None:
        """A base branch: the head of a bay, a heavy rule with its name on it."""
        canvas.put(0, y, "━" * (X_TREE - 1), AXIS)
        canvas.put(2, y, " BAY ", MUTED)
        canvas.put(X_TREE, y, row.lead, AXIS)
        x = X_TREE + len(row.lead)
        canvas.put(x, y, row.label, f"bold {INK}")
        x += len(row.label) + 2
        if row.sha and row.sha in row.note:                    # "branched from main at 18deb8e, …"
            before, after = row.note.split(row.sha, 1)
            x += write(canvas, x, y, [(before, MUTED), (row.sha, INK2), (after, MUTED)], width - x)
        else:
            note = row.note or "its history runs down from the tip"
            canvas.put(x, y, note, MUTED)
            x += len(note)
        canvas.put(x + 1, y, "━" * max(0, width - x - 1), AXIS)

    def draw_divider(self, canvas: Canvas, y: int, row: Row, width: int) -> None:
        """A commit: a divider between groups of strips, carrying its short hash."""
        canvas.put(0, y, "─" * (X_TREE - 1), HAIR)
        lead = len(row.lead)
        canvas.put(X_TREE, y, row.lead[:-2], AXIS)
        canvas.put(X_TREE + lead - 2, y, row.lead[-2:], MUTED)
        canvas.put(X_TREE + lead, y, row.sha, INK2)
        canvas.put(X_TREE + lead + len(row.sha) + 1, y, row.label, MUTED)
        x = X_TREE + lead + len(row.sha) + len(row.label) + 2
        canvas.put(x, y, "─" * max(0, width - x), HAIR)

    def draw_strip(self, canvas: Canvas, y: int, index: int, row: Row, dim: bool, hot: bool,
                   xs: tuple[int, int, int, int]) -> None:
        """One strip: callsign block (owner), the owner's state, waits on, then the branch."""
        pr = row.pr
        x_review, x_ci, x_progress, x_quiet = xs
        spin = SPIN[(self.frame + index * 3) % len(SPIN)]
        off = DIM if dim else ""
        mark, mark_style, name, name_style = callsign(pr, spin)
        canvas.put(0, y, "▌", off or (INK if hot else MUTED))
        canvas.put(1, y, mark, off or mark_style)
        canvas.put(3, y, name[:BLOCK_W - 4], off or name_style)
        word, word_style = state_word(pr)
        canvas.put(X_STATE, y, word, off or word_style)
        write(canvas, X_WAIT, y, waits_segments(pr, spin), WAIT_W, off)

        canvas.put(X_TREE, y, row.lead, AXIS)
        x = X_TREE + len(row.lead)
        icon, color, _, _ = STATUS[pr.state]
        canvas.put(x, y, icon, off or color)
        write(canvas, x + 2, y, [(pr.key, f"bold {INK}"), ("-" + pr.slug, INK2)], x_review - x - 3, off)
        write(canvas, x_review, y, review_segments(pr), x_ci - x_review - 1, off)
        ci_icon, ci_color = CI[pr.ci]
        canvas.put(x_ci, y, ci_icon, off or ci_color)
        if x_progress:
            write(canvas, x_progress, y, progress_segments(pr), x_quiet - x_progress - 1, off)
        canvas.put(x_quiet, y, pr.age.rjust(4), off or (f"bold {INK}" if is_stale(pr.age) else MUTED))

    def draw_open(self, canvas: Canvas, y: int, row: Row, part: int, x_review: int, width: int) -> None:
        """The second and third lines of the strip in hand: more of its facts, each under
        the cell it belongs to."""
        pr = row.pr
        who, wait = owner(pr), waits(pr)
        canvas.put(0, y, "▌", INK)
        stem = row.lead[:-2] + ("│ " if row.lead.endswith("├ ") else "  ")
        canvas.put(X_TREE, y, stem + ("│" if pr.children else " "), AXIS)
        x = X_TREE + len(stem) + 2
        left_w = X_TREE - X_STATE - 1
        if part == 1:
            if who.kind == "agent":
                said = who.worker.note or who.state
            elif who.kind == "no agent":
                said = "no agent assigned" + (": yours to crew" if who.fixable else "")
            elif who.kind == "person":
                said = f"{pr.author}'s branch, on GitHub"
            else:
                said = "review notes; the session has ended"
            write(canvas, X_STATE, y, [(said, INK2)], left_w)
            write(canvas, x, y, [(pr.title or pr.slug, INK2)], x_review - x - 1)
            ci_icon, ci_color = CI[pr.ci]
            merge: Segments = ([("⚠ ", CRIT), (pr.merge, INK2)] if "conflict" in pr.merge
                               else [(pr.merge, MUTED)])
            write(canvas, x_review, y, [(ci_icon, ci_color), (f" {pr.ci_note or 'no checks yet'}", INK2),
                                        (" · ", MUTED)] + merge, width - x_review)
        else:
            if wait is None:
                said = f"notes for {short(pr.parent)}" if pr.parent else "notes"
            elif wait.kind == "working":
                said = f"now: {wait.what}"
            else:
                said = f"{wait.end}: {wait.what}"
            write(canvas, X_STATE, y, [(said, INK2)], left_w)
            root = pr.root()
            size = sum(p.state != "findings" for p in root.walk())
            where: Segments = [(STATUS[pr.state][2], INK2)]
            if size > 1:
                where.append((f" · level {pr.depth} of {size}", MUTED))
            if pr.is_pr:
                where.append((f" · PR #{pr.number}", MUTED))
            elif pr.state != "findings":
                where.append((f" · #{pr.number} reserved", MUTED))
            write(canvas, x, y, where, x_review - x - 1)
            write(canvas, x_review, y, review_words(pr), width - x_review)

    # ---- keys ----------------------------------------------------------------------------------
    def action_move(self, step: int) -> None:
        self.sel += step
        self.draw()

    def action_next_clearance(self) -> None:
        """Jump to the next clearance to give, in the band's order: most urgent first."""
        branches = self.branches()
        stops = [branches.index(e.pr) for e in needs_effort(REPOS[self.repo]) if e.pr in branches]
        if not stops:
            return
        self.sel = stops[(stops.index(self.sel) + 1) % len(stops)] if self.sel in stops else stops[0]
        self.draw()

    def action_pick(self, step: int) -> None:
        """Pick the next one on frequency; their strips light, in place. Stepping off either
        end lets go."""
        count = len(frequency(REPOS[self.repo])[2])
        self.pick += step
        if self.pick < -1:
            self.pick = count - 1
        elif self.pick >= count:
            self.pick = -1
        self.draw()

    def action_unpick(self) -> None:
        self.pick = -1
        self.draw()

    def action_toggle_legend(self) -> None:
        self.query_one("#band").toggle_class("legend")

    def action_repo(self, step: int) -> None:
        names = list(REPOS)
        self.repo = names[(names.index(self.repo) + step) % len(names)]
        self.sel = self.top = 0
        self.pick = -1
        self.draw()


if __name__ == "__main__":
    StudioV4B().run()
