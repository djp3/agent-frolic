"""Agent Frolic mock-up, option 4 v4a: THE SCORE.

The repo is a conductor's score and you are the conductor. A score already has the shape
round 4 asks for: the player's name is the first thing on every staff, and the brackets down
the left margin group the staves into sections.

- Top line: the one every mock-up uses.
- THE ORCHESTRA, SEATED. Everyone who owns a branch or is waited on, by what they are doing
  right now: your agents that are playing (working), the ones waiting for your cue (at
  their prompt), the ones resting until someone else acts, the empty chairs (your move, no
  agent), then the people who are waited on and the people with nothing to do. An agent is
  always named with its branch. Left/right pick a player; that player's staves stay lit in
  the score below and the rest go dim. Nothing is regrouped.
- THE SCORE. One staff per branch, in the order commit, then branch, then stack: the
  brackets are the hierarchy, and the commits that matter carry their short hash. Each staff
  reads OWNER, then WAITS ON (the sequence, left to right: "pr-0024 → mira"), then the part
  (the branch), then the staff itself, which shows the present and no history:
    ♩ ♩ ⠋ ♩      a part being played: notes are the countable progress, the turning dots
                 stand where the agent is (2 of 3 done here)
    ┝━━━━━┥      a rest: the longer the bar, the longer the branch has been quiet (a log
                 scale, so 20 minutes and 9 days both fit)
    blue rest    a rest that ends when you act; dashed when the chair is empty (no agent)
- CUES TO GIVE. What needs effort to get moving, most urgent first (is an agent waiting on
  it, then how long quiet), each line led by who has to act. Beside it, the selected staff
  in plain words, owner and waits-on first.

Blue means one thing: idle, and you can fix it. The dots turn where an agent works; nothing
else moves. Every name and number is made up.

    .venv/bin/python mockup/option4_studio_v4a.py

Keys: up/down pick a staff, left/right pick a player (esc lets go), n jumps to the next cue
to give (most urgent first), l swaps the bottom band for the legend, tab goes to the next
repo (shift+tab back), q quits.
"""
from __future__ import annotations

import math

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Static

from frolic_v4 import (ACCENT, AXIS, CRIT, GOOD, HAIR, INK, INK2, ME, MUTED, PLANE, PR, RAISED,
                       REPOS, SPIN, STATUS, SURFACE, WARN, WORD, Canvas, Member, Row, Segments,
                       age_hours, cast, chain, flat, is_stale, needs_effort, owned_by, owner,
                       owner_segments, pipe, sha, short, top_line, tree_rows, verdicts, waits,
                       waits_segments, write)

X_OWNER, X_WAIT, X_PART = 0, 12, 34      # the first two things on a staff, then the part
LABEL_W = 27                             # the seating block's labels
BAND_MIN, BAND_MAX = 9, 14               # the bottom band, its top border included
EFFORT_W = 99                            # the cues pane, padding and border included
DIM = "#5c5b56"                          # notes still to come, and staves that are not lit
NOTE = "♩"
CI = {"pass": ("✓", GOOD), "fail": ("✗", CRIT), "run": ("…", WARN), "none": ("·", AXIS)}

# The seating block: (key, the musical word, the plain one, what to say when nobody sits there)
SECTIONS = (
    ("playing", "playing", "working", "nobody: no agent is at work"),
    ("cue", "your cue", "at its prompt", "nobody"),
    ("resting", "resting", "waits on others", "nobody"),
    ("chair", "empty chair", "no agent", "none"),
    ("waited", "people", "waited on", "nobody"),
    ("free", "people", "nothing to do", "nobody: everyone has work"),
)


# ---- the staff: what is happening on a branch right now ----------------------------------

def rest_bars(age: str, longest: int) -> int:
    """How long a rest is drawn: a log scale from a minute to about three weeks."""
    minutes = max(1.0, age_hours(age) * 60)
    return 2 + round(min(1.0, math.log10(minutes) / 4.5) * (longest - 2))


def staff(canvas: Canvas, x: int, y: int, pr: PR, width: int, spin: str, dim: bool) -> None:
    """One branch's staff, `width` cells from x. Notes while an agent plays; a rest whose
    length is how long it has been quiet otherwise; then the staff line runs on."""
    here = pipe(pr)
    off = DIM if dim else ""
    if here is None:                                           # a local review branch: notes
        if not owner(pr).working:
            return
        used = write(canvas, x, y, [(spin, INK), (" ♫", INK2)], width, off)
    elif here.state == "working":
        done, total = here.done, here.total
        if total > 12:                                         # keep long counts inside the staff
            done, total = round(done * 12 / total), 12
        notes: Segments = [(NOTE + " ", INK)] * done + [(spin, INK)]
        notes += [(" " + NOTE, DIM)] * (total - done) if total else [(" ♫", INK2)]
        used = write(canvas, x, y, notes, width, off)
    else:
        blue = here.state in ("needs you", "no agent")         # the rest ends when you act
        fill = "╍" if here.state == "no agent" else "━"        # dashed: the chair is empty
        bar = "┝" + fill * rest_bars(pr.age, max(4, min(24, width - 12))) + "┥"
        used = write(canvas, x, y, [(bar, ACCENT if blue else MUTED), (" quiet ", MUTED),
                                    (pr.age, f"bold {INK}" if is_stale(pr.age) else INK2)], width, off)
    if width - used > 2:
        canvas.put(x + used + 1, y, "─" * (width - used - 1), HAIR if dim else AXIS)


def now_segments(pr: PR) -> Segments:
    """ "in progress" only while an agent plays; otherwise "stalled, …" or "not started". """
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


def bare(segments: Segments) -> Segments:
    """A cell without the blank mark in front of it, for use inside a sentence."""
    return segments[1:] if segments and not segments[0][0].strip() else segments


# ---- the orchestra, seated ----------------------------------------------------------------

def seats(prs: list[PR]) -> list[tuple[str, Member]]:
    """Everyone, in the order the seating block shows them: you, your agents by what they
    are doing, the empty chairs, then the other people. Left/right walk this list."""
    members = cast(prs)
    agents = [m for m in members if m.kind == "agent"]
    people = [m for m in members if m.kind == "person"]
    out = [("you", members[0])]
    out += [("playing", m) for m in agents if m.state == "working"]
    out += [("cue", m) for m in agents if m.state == "at its prompt"]
    out += [("resting", m) for m in agents if m.state == "waiting"]
    # an empty chair is any move of yours with nobody on it, a review of someone's branch too
    out += [("chair", Member("no agent", "no agent", "", pr, True, [pr]))
            for pr in flat(prs) if (here := pipe(pr)) is not None and here.state == "no agent"]
    out += [("waited", m) for m in people if m.state == "has work"]
    out += [("free", m) for m in people if m.state != "has work"]
    return out


def seat_segments(member: Member, spin: str) -> Segments:
    """One seat. An agent is named with its branch: its serial alone says nothing."""
    pr = member.pr
    if member.kind == "you":
        return [("you", f"bold {INK}")]
    if member.kind == "agent":
        tail: Segments = [(" · ", MUTED), (short(pr), INK2)]
        if pr.state == "findings":
            tail.append((" review", MUTED))
        if member.state == "working":
            return [(spin + " ", INK), (member.name, INK)] + tail
        if member.state == "at its prompt":
            return [("▷ ", ACCENT), (member.name, f"bold {INK}")] + tail
        wait = waits(pr)
        if wait is not None and wait.said:
            tail += [(" → ", MUTED), (wait.said, INK2)]
        return [("· ", MUTED), (member.name, INK2)] + tail
    if member.kind == "no agent":
        here = pipe(pr)
        return [("● ", ACCENT), (short(pr), f"bold {INK}"), (f" {here.what}" if here else "", MUTED)]
    if member.state == "has work":
        if not member.waited_by:
            return [(member.name, INK), (" has work", MUTED)]
        return [(member.name, INK), (" for ", MUTED),
                (", ".join(short(p) for p in member.waited_by), INK2)]
    return [("● ", ACCENT), (member.name, f"bold {INK}")]


def seating(prs: list[PR], picked: int | None, width: int, frame: int) -> tuple[Text, int]:
    """The block under the top line, and how many lines it took."""
    found = seats(prs)
    placed: list[tuple[int, int, Segments, bool]] = []         # x, y, segments, picked
    labels: list[tuple[int, str, str, str]] = []               # y, musical, plain, when empty
    y = 0
    for key, musical, plain, nobody in SECTIONS:
        y += 1
        x = LABEL_W
        here = [(i, m) for i, (section, m) in enumerate(found) if section == key]
        labels.append((y, musical, plain, "" if here else nobody))
        for i, member in here:
            segments = seat_segments(member, SPIN[(frame + i * 3) % len(SPIN)])
            size = sum(len(part) for part, _ in segments)
            if x > LABEL_W and x + size > width:               # no room: carry on a line below
                y, x = y + 1, LABEL_W
            placed.append((x, y, segments, i == picked))
            x += size + 3

    canvas = Canvas(width, y + 1)
    canvas.put(0, 0, "the orchestra, seated", f"bold {INK}")
    canvas.put(LABEL_W, 0, "conductor", MUTED)
    you = seat_segments(found[0][1], SPIN[0])
    placed.append((LABEL_W + 10, 0, you, picked == 0))
    hint = "← → pick a player: their staves stay lit below"
    if width - len(hint) > LABEL_W + 20:
        canvas.put(width - len(hint), 0, hint, MUTED)
    for row, musical, plain, nobody in labels:
        canvas.put(0, row, musical, INK)
        canvas.put(len(musical), row, f" · {plain}", MUTED)
        if nobody:
            canvas.put(LABEL_W, row, nobody, DIM)
    for x, row, segments, on in placed:
        size = write(canvas, x, row, segments, max(0, width - x))
        if on:
            canvas.paint(x - 1, row, size + 2, RAISED)
            for i in range(x, min(width, x + size)):           # underline the one that is picked
                ch, style = canvas.cells[row][i]
                canvas.cells[row][i] = (ch, f"underline {style}")
    return canvas.text(), y + 1


# ---- the bottom band -------------------------------------------------------------------------

def cues_text(prs: list[PR], selected: PR | None, room: int, width: int) -> Text:
    """What needs effort to get moving, most urgent first. Each line starts with who has to
    act, then the branch, why it is urgent, and what the move is."""
    text = Text("cues to give", style=f"bold {INK}")
    text.append("   needs effort · most urgent first: is an agent waiting on it, then how long quiet\n",
                style=MUTED)
    found = needs_effort(prs)
    if not found:
        return text.append("none: everything that can move is moving" if flat(prs)
                           else "none: nothing is open", style=MUTED)
    who_w = max(len(e.who) for e in found) + 1
    key_w = max(len(e.pr.key) for e in found) + 2
    shown = found[:room] if len(found) <= room else found[:max(1, room - 1)]
    for rank, effort in enumerate(shown, start=1):
        here = effort.pipe
        line = Text(f"{rank:<2}", style=MUTED)
        line.append("● " if effort.yours else "○ ", style=ACCENT if effort.yours else MUTED)
        line.append(f"{effort.who:<{who_w}}", style=f"bold {INK}" if effort.yours else INK)
        line.append(f"{effort.pr.key:<{key_w}}", style=INK2)
        urgency = Text()
        if effort.waiting:
            held, quiet = effort.urgency.split(" · ", 1)
            urgency.append(held, style=f"bold {INK}").append(" · ", style=MUTED)
        else:
            quiet = effort.urgency
        urgency.append(quiet, style=f"bold {INK}" if is_stale(effort.pr.age) else INK2)
        line.append_text(urgency).append(" " * max(2, 28 - urgency.cell_len))
        if effort.kind == "needs you":
            line.append(f"at its prompt · {here.what}", style=INK)
        elif effort.kind == "no agent":
            line.append(f"no agent · {here.what}", style=INK)
        else:
            line.append(here.what, style=INK)
            line.append(f", {here.progress}", style=INK2)
        if effort.behind:
            line.append(" · holds up ", style=MUTED)
            line.append(", ".join(short(p) for p in effort.behind), style=INK)
        if selected is not None and (selected is effort.pr or selected in effort.behind):
            line.truncate(width)
            line.append(" " * max(0, width - line.cell_len))
            line.stylize(f"on {RAISED}")
        text.append_text(line).append("\n")
    if len(found) > len(shown):
        text.append(f"  and {len(found) - len(shown)} more: press n to step through them\n", style=MUTED)
    return text


def detail_text(repo: str, prs: list[PR], pr: PR) -> Text:
    """The selected staff in plain words: who owns it and what it waits on come first."""
    icon, color, label, _ = STATUS[pr.state]
    text = Text()
    text.append(f"{repo}-", style=MUTED)
    text.append(pr.name + "\n", style=f"bold underline {INK}")
    text.append(icon, style=color)
    text.append(f" {label}", style=INK2).append(" · quiet ", style=MUTED)
    text.append(pr.age, style=f"bold {INK}" if is_stale(pr.age) else INK2)
    text.append(f" · {pr.title}\n", style=MUTED)

    def kv(key: str, value: Text | str) -> None:
        text.append(f"{key:<9}", style=MUTED)
        text.append_text(value if isinstance(value, Text) else Text(value, style=INK2))
        text.append("\n")

    who = owner(pr)
    line = append(Text(), bare(owner_segments(pr)))
    if who.kind == "agent":
        line.append(f", {who.state}", style=INK)
    elif who.kind == "no agent":
        line.append(": your move, nobody is on it" if who.fixable else ": none needed while it waits",
                    style=INK2)
    elif who.kind == "person":
        line.append(" · their agents cannot be seen", style=MUTED)
    else:
        line.append(" · the agent that wrote them has ended", style=MUTED)
    kv("owner", line)
    if who.worker is not None and who.worker.note:
        kv("", who.worker.note)

    here, wait = pipe(pr), waits(pr)
    if wait is None or here is None:
        kv("waits on", Text("nothing · ", style=INK2).append(
            f"review notes for {short(pr.parent)}" if pr.parent else "review notes", style=INK2))
    elif wait.kind == "working":
        kv("waits on", Text("nothing: its agent is at work", style=INK2))
        kv("", Text(f"{here.what} · {here.progress}", style=INK2))
    else:
        kv("waits on", append(Text(), waits_segments(pr)))
        end = pipe(wait.end_pr) if wait.end_pr is not None else None
        move = f"{short(wait.end_pr)}: {wait.what}" if wait.links else f"to {wait.what}"
        said = Text(move, style=INK)
        if end is not None:
            said.append(f" · {end.progress}", style=INK if end.partly and end.state != "working" else INK2)
        kv("", said)
    queued = [p for p in flat(prs) if p is not pr and pr in chain(p)]
    if queued:
        kv("holds up", Text(", ".join(short(p) for p in queued), style=INK))

    root = pr.root()
    size = sum(p.state != "findings" for p in root.walk())
    where = Text(sha(repo, root.base, root.behind), style=INK)
    where.append(f" · {root.base} " + ("tip" if not root.behind else f"{root.behind} back"), style=INK2)
    if size > 1:
        where.append(f" · level {pr.depth} of {size}", style=INK2)
    kv("hangs at", where)

    if pr.history:
        event = pr.history[-1]
        said = Text()
        if event.kind == "push":
            said.append(f"{event.commits} commits pushed by {event.who} {event.age}", style=INK2)
        else:
            mark, mark_color, _ = WORD[event.verdict]
            name = "you" if event.who == ME else event.who
            was = "were" if name == "you" else "was"
            words = {"approved": "approved", "changes": "asked for changes",
                     "requested": f"{was} asked to review",
                     "rerequested": f"{was} asked to look again"}[event.verdict]
            said.append(f"r{event.round} ", style=MUTED).append(mark, style=mark_color)
            said.append(f" {name} {words} {event.age}", style=INK)
        if pr.threads[0]:
            said.append(f" · {pr.threads[0]} open", style=INK2)
        kv("review", said)
    elif pr.is_pr:
        kv("review", "nobody has been asked yet")
    ci_icon, ci_color = CI[pr.ci]
    kv("checks", Text(f"{ci_icon} ", style=ci_color).append(pr.ci_note or "none yet", style=INK))
    if "conflict" in pr.merge:
        kv("merge", Text("⚠ ", style=CRIT).append(pr.merge, style=INK))
    elif pr.merge != "clean":                                  # "1 commit, not pushed"
        kv("merge", pr.merge)
    return text


def legend_text(width: int, rows: int) -> Text:
    canvas = Canvas(max(60, width), max(1, rows))
    canvas.put(0, 0, "owner: the player, first on every staff", f"bold {INK}")
    for y, (mark, color, name, words) in enumerate((
            (SPIN[2], INK, "agent 01", "an agent of yours, at work"),
            ("▷", ACCENT, "agent 01", "at its prompt: it waits for you"),
            ("·", MUTED, "agent 01", "idle until someone else acts"),
            ("●", ACCENT, "no agent", "your move, and nobody is on it"),
            ("", "", "mira", "someone else's branch; their"),
            ("", "", "", "agents cannot be seen"),
            ("", "", "notes", "a local review, its agent gone")), start=1):
        canvas.put(0, y, mark, color)
        canvas.put(2, y, name, INK2)
        canvas.put(12, y, words, MUTED)

    x = 46
    canvas.put(x, 0, "waits on: read left to right", f"bold {INK}")
    for y, (mark, color, name, words) in enumerate((
            ("", "", "(empty)", "the owner's agent is at work"),
            ("●", ACCENT, "you", "idle until you act"),
            ("", "", "mira", "idle until that person acts"),
            (SPIN[2], INK, "agent 02", "an agent of yours is on it now"),
            ("", "", "pr-0024 → mira", "it waits on pr-0024,"),
            ("", "", "", "which waits on mira")), start=1):
        canvas.put(x, y, mark, color)
        canvas.put(x + 2, y, name, INK2)
        canvas.put(x + (18 if y >= 5 else 12), y, words, MUTED)
    canvas.put(x, 7, "┬ ┼", MUTED)
    canvas.put(x + 4, 7, "a commit by short hash: where a stack", MUTED)
    canvas.put(x + 4, 8, "hangs, or where another base was cut", MUTED)

    x = 92
    canvas.put(x, 0, "the staff: what happens now", f"bold {INK}")
    canvas.put(x, 1, f"{NOTE} {NOTE} ", INK)
    canvas.put(x + 4, 1, SPIN[2], INK)
    canvas.put(x + 5, 1, f" {NOTE}", DIM)
    canvas.put(x + 12, 1, "playing: 2 of 3 done, the dots", MUTED)
    canvas.put(x + 12, 2, "turn where the agent is", MUTED)
    canvas.put(x, 3, "┝━━━━━━━┥", MUTED)
    canvas.put(x + 12, 3, "a rest: nobody is on it. The longer", MUTED)
    canvas.put(x + 12, 4, "the bar, the longer it has been quiet", MUTED)
    canvas.put(x, 5, "┝━━━━━━━┥", ACCENT)
    canvas.put(x + 12, 5, "blue: the rest ends when you act", MUTED)
    canvas.put(x, 6, "┝╍╍╍╍╍╍╍┥", ACCENT)
    canvas.put(x + 12, 6, "dashed: an empty chair, no agent", MUTED)
    canvas.put(x, 7, "stalled", INK)
    canvas.put(x + 12, 7, "partly done on GitHub, nobody on it", MUTED)
    canvas.put(x, 8, "✗", CRIT)
    canvas.put(x + 12, 8, "before the staff: a check is failing", MUTED)

    y, x = 9, 0                                                # the state mark before each part
    canvas.put(x, y, "part", f"bold {INK}")
    x += 6
    for icon, color, label, _ in STATUS.values():
        canvas.put(x, y, icon, color)
        canvas.put(x + 2, y, label, MUTED)
        x += len(label) + 4
    return canvas.text()


class StudioV4A(App):
    CSS = f"""
    Screen {{ background: {PLANE}; color: {INK}; }}
    #top, #cols, #hints {{ height: 1; padding: 0 1; text-wrap: nowrap; text-overflow: ellipsis; }}
    #seat {{ height: 7; padding: 0 1; margin-top: 1; }}
    #map {{ height: 1fr; padding: 0 1; }}
    #band {{ height: {BAND_MAX}; border-top: solid {HAIR}; }}
    #cues {{ width: {EFFORT_W}; height: 1fr; padding: 0 1; border-right: solid {HAIR}; }}
    #detail {{ width: 1fr; height: 1fr; padding: 0 1; }}
    #legend {{ width: 1fr; height: 1fr; padding: 0 1; display: none; }}
    #cues, #detail, #legend {{ text-wrap: nowrap; text-overflow: ellipsis; }}
    #band.legend #cues, #band.legend #detail {{ display: none; }}
    #band.legend #legend {{ display: block; }}
    #band.narrow #cues {{ width: 68; }}
    #hints {{ color: {MUTED}; }}
    """
    BINDINGS = [
        Binding("up", "move(-1)", show=False), Binding("down", "move(1)", show=False),
        Binding("left", "player(-1)", show=False), Binding("right", "player(1)", show=False),
        Binding("escape", "let_go", show=False),
        Binding("n", "next_cue", show=False), Binding("l", "toggle_legend", show=False),
        Binding("tab", "repo(1)", show=False, priority=True),
        Binding("shift+tab", "repo(-1)", show=False, priority=True),
        Binding("q", "quit", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.repo = next(iter(REPOS))
        self.sel = 0                  # index among the branch rows
        self.top = 0                  # first staff on screen, when the window is too short
        self.pick: int | None = None  # index among the seats: the player whose staves are lit
        self.frame = 0                # turns the dots where an agent works; nothing else moves
        self.seat_h = 7
        self.band_h = BAND_MAX

    def compose(self) -> ComposeResult:
        yield Static(id="top")
        yield Static(id="seat")
        yield Static(id="cols")
        yield Static(id="map")
        with Horizontal(id="band"):
            yield Static(id="cues")
            yield Static(id="detail")
            yield Static(id="legend")
        yield Static(id="hints")

    def on_mount(self) -> None:
        self.draw()
        self.set_interval(1 / 8, self.tick)

    def on_resize(self) -> None:
        self.query_one("#band").set_class(self.size.width < 140, "narrow")
        self.draw()

    def tick(self) -> None:
        self.frame += 1
        self.query_one("#top", Static).update(top_line(self.repo, frame=self.frame))
        self.query_one("#seat", Static).update(self.seating())
        self.query_one("#map", Static).update(self.map())

    # ---- state ---------------------------------------------------------------
    def rows(self) -> list[Row]:
        return tree_rows(self.repo)

    def branches(self) -> list[PR]:
        return [row.pr for row in self.rows() if row.pr is not None]

    def selected(self) -> PR | None:
        found = self.branches()
        return found[min(self.sel, len(found) - 1)] if found else None

    def lit(self) -> list[PR] | None:
        """The staves of the picked player: what it owns, and what waits on it."""
        found = seats(REPOS[self.repo])
        if self.pick is None or self.pick >= len(found):
            return None
        return owned_by(REPOS[self.repo], found[self.pick][1])

    def columns(self, width: int) -> tuple[int, int, int, int]:
        """(width of the part, x of the staff's bar line, width of the staff, x of progress)."""
        wide = width >= 140
        part_w = 42 if wide else 30
        x_staff = X_PART + part_w + 1
        staff_w = max(14, min(37, width - x_staff - 3 - (27 if wide else 21)))
        return part_w, x_staff, staff_w, x_staff + 2 + staff_w + 1

    def seating(self) -> Text:
        text, lines = seating(REPOS[self.repo], self.pick, max(60, self.size.width - 2), self.frame)
        if lines != self.seat_h:
            self.seat_h = lines
            self.query_one("#seat").styles.height = lines
        return text

    def fit(self) -> int:
        """The score gets the rows it needs so every base and stack shows at once; the band
        takes what is left, within its limits. Returns the rows the score has."""
        fixed = 4 + self.seat_h                                # top line, a gap, column heads, hints
        spare = self.size.height - fixed - max(2, len(self.rows()))
        band = max(BAND_MIN, min(BAND_MAX, spare))
        if band != self.band_h:
            self.band_h = band
            self.query_one("#band").styles.height = band
        return max(3, self.size.height - fixed - band)

    # ---- drawing -------------------------------------------------------------
    def draw(self) -> None:
        prs = REPOS[self.repo]
        self.query_one("#top", Static).update(top_line(self.repo, frame=self.frame))
        self.query_one("#seat", Static).update(self.seating())       # sets the block's height
        self.fit()
        self.query_one("#map", Static).update(self.map())            # sets what #cols reports
        pr = self.selected()
        pane = (68 if self.size.width < 140 else EFFORT_W) - 3
        self.query_one("#cues", Static).update(cues_text(prs, pr, self.band_h - 2, pane))
        self.query_one("#detail", Static).update(
            detail_text(self.repo, prs, pr) if pr is not None
            else Text("no open pull requests or local branches", style=MUTED))
        self.query_one("#legend", Static).update(legend_text(self.size.width - 2, self.band_h - 1))
        hints = Text("↑↓ staff    ← → player")
        if self.pick is not None:
            hints.append("    esc let go", style=INK)
        hints.append("    n next cue to give    l legend    ⇥ next repo    q quit")
        self.query_one("#hints", Static).update(hints)

    def heads(self, width: int, note: str) -> Text:
        _, x_staff, staff_w, x_now = self.columns(width)
        head = Canvas(width, 1)
        head.put(X_OWNER, 0, "owner", MUTED)
        head.put(X_WAIT, 0, "waits on", MUTED)
        head.put(X_PART, 0, "part", MUTED)
        head.put(X_PART + 5, 0, note or "commit, branch, stack", INK2 if note else DIM)
        key: Segments = [("staff  ", MUTED), (NOTE, INK), (" done ", MUTED), (NOTE, DIM),
                         (" to come  ", MUTED), ("┝━┥", MUTED), (" quiet", MUTED)]
        write(head, x_staff + 2, 0, key, staff_w)
        head.put(x_now, 0, "progress", MUTED)
        head.paint(0, 0, width, SURFACE)                       # a band, so the heads part the two blocks
        return head.text()

    def map(self) -> Text:
        rows = self.rows()
        branches = [row.pr for row in rows if row.pr is not None]
        self.sel = max(0, min(len(branches) - 1, self.sel))
        selected = branches[self.sel] if branches else None
        width = max(60, self.size.width - 2)
        height = self.fit()
        at = next((i for i, row in enumerate(rows) if row.pr is selected), 0) if selected else 0
        self.top = max(0, min(self.top, at))                   # only a short window ever scrolls
        if at >= self.top + height:
            self.top = at - height + 1
        if at <= 2 or len(rows) <= height:
            self.top = 0
        shown = rows[self.top:self.top + height]
        note = f"rows {self.top + 1}–{self.top + len(shown)} of {len(rows)}" if len(rows) > height else ""
        self.query_one("#cols", Static).update(self.heads(width, note))

        part_w, x_staff, staff_w, x_now = self.columns(width)
        lit = self.lit()
        canvas = Canvas(width, max(1, len(shown)) + (0 if branches else 1))
        if not branches:
            canvas.put(X_PART, len(shown), "no open pull requests or local branches", MUTED)
        for y, row in enumerate(shown):
            lead = X_PART + len(row.lead)
            canvas.put(x_staff, y, "│", AXIS)                  # the line that joins the staves
            if row.kind == "base":
                canvas.put(X_PART, y, row.lead, AXIS)
                canvas.put(lead, y, row.label, f"bold {INK}")
                x, room = x_staff + 2, width - x_staff - 2     # its note goes where a staff would be
                if row.sha and row.sha in row.note:            # "branched from main at 18deb8e, …"
                    before, after = row.note.split(row.sha, 1)
                    write(canvas, x, y, [(before, MUTED), (row.sha, INK2), (after, MUTED)], room)
                else:
                    write(canvas, x, y, [(row.note or "its history runs down from the tip", MUTED)], room)
            elif row.kind == "commit":
                canvas.put(X_PART, y, row.lead[:-2], AXIS)
                canvas.put(lead - 2, y, row.lead[-2:], MUTED)
                canvas.put(lead, y, row.sha, f"{INK2} on {SURFACE}")
                canvas.put(lead + len(row.sha) + 1, y, row.label, MUTED)
            else:
                dim = lit is not None and row.pr not in lit
                self.draw_staff_row(canvas, y, row, part_w, x_staff, staff_w, x_now, dim)
                if row.pr is selected:
                    canvas.paint(0, y, width, RAISED)
        return canvas.text()

    def draw_staff_row(self, canvas: Canvas, y: int, row: Row, part_w: int, x_staff: int,
                       staff_w: int, x_now: int, dim: bool) -> None:
        pr = row.pr
        spin = SPIN[(self.frame + y * 3) % len(SPIN)]
        off = DIM if dim else ""
        write(canvas, X_OWNER, y, owner_segments(pr, spin), X_WAIT - X_OWNER - 1, off)   # who, first
        write(canvas, X_WAIT, y, waits_segments(pr, spin), X_PART - X_WAIT - 1, off)     # then the cue
        canvas.put(X_PART, y, row.lead, HAIR if dim else AXIS)
        x = X_PART + len(row.lead)
        icon, color, _, _ = STATUS[pr.state]
        canvas.put(x, y, icon, DIM if dim else color)
        write(canvas, x + 2, y, [(pr.key, f"bold {INK}"), ("-" + pr.slug, INK2)],
              X_PART + part_w - x - 4, off)
        if pr.ci == "fail":
            canvas.put(x_staff - 2, y, "✗", DIM if dim else CRIT)
        staff(canvas, x_staff + 2, y, pr, staff_w, spin, dim)
        write(canvas, x_now, y, now_segments(pr), canvas.width - x_now, off)

    # ---- keys ----------------------------------------------------------------
    def action_move(self, step: int) -> None:
        self.sel += step
        self.draw()

    def action_player(self, step: int) -> None:
        """Pick the next player in the seating block; that player's staves stay lit."""
        found = seats(REPOS[self.repo])
        if self.pick is None:
            self.pick = 0 if step > 0 else len(found) - 1
        else:
            self.pick = (self.pick + step) % len(found)
        staves = self.lit() or []
        branches = self.branches()
        first = next((i for i, pr in enumerate(branches) if pr in staves), None)
        if first is not None:                                  # bring the first of them into view
            self.sel = first
        self.draw()

    def action_let_go(self) -> None:
        self.pick = None
        self.draw()

    def action_next_cue(self) -> None:
        """Jump to the next cue to give, in the band's order: most urgent first."""
        branches = self.branches()
        stops = [branches.index(e.pr) for e in needs_effort(REPOS[self.repo]) if e.pr in branches]
        if not stops:
            return
        self.sel = stops[(stops.index(self.sel) + 1) % len(stops)] if self.sel in stops else stops[0]
        self.draw()

    def action_toggle_legend(self) -> None:
        self.query_one("#band").toggle_class("legend")

    def action_repo(self, step: int) -> None:
        names = list(REPOS)
        self.repo = names[(names.index(self.repo) + step) % len(names)]
        self.sel = self.top = 0
        self.pick = None
        self.draw()


if __name__ == "__main__":
    StudioV4A().run()
