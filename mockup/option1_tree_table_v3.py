"""Agent Frolic mock-up, option 1 v3: the tree table, consolidated from options 1, 2 and 5.
One dense screen that answers "is every agent working, and if not, what gets it going?".

- Top line (option 3's): repos with their two small numbers, agents at work, the refresh.
- Four lines (option 5's): At a prompt, No agent, Waits on, Nothing to do. The fourth also
  names who has work (option 1's people line). These say WHAT KIND of effort is needed, and
  from whom.
- The map (option 1's table on option 2's thin tree): every base and every stack at once,
  in one connected tree. A base's history runs down from its tip, and the commits that
  matter carry their short hash: where a stack hangs, and where another base was cut. Per
  branch: review, checks, pipeline (working, or what it waits on, read left to right along
  the chain: "waits on pr-0024 → mira"), progress ("in progress" only while an agent works,
  else "stalled, 1 of 3 threads" or "not started"), the agent, and how long it is quiet.
  There are no columns per person: the sequence is in the pipeline column.
- The band at the bottom: "needs effort to get moving", in ORDER of urgency (is an agent
  waiting for it, then how long it has been quiet), with what it holds up; beside it the
  selected branch, its wait chain first.

Blue means one thing: idle, and you can fix it. The dots before "working" turn; nothing
else moves. Every name and number is made up.

    .venv/bin/python mockup/option1_tree_table_v3.py

Keys: up/down pick a branch, n jumps to the next thing that needs effort (most urgent
first), f cycles the filter (everything / not working / needs you), l swaps the bottom band
for the legend, tab goes to the next repo (shift+tab back), q quits.
"""
from __future__ import annotations

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Static

from frolic_v3 import (ACCENT, AXIS, CRIT, GOOD, GROUPS, HAIR, INK, INK2, ME, MUTED, PLANE, PR,
                       RAISED, REPOS, SPIN, STATUS, WARN, WORD, Canvas, Effort, Row, agent,
                       chain, effort_lines, flat, free_people, is_stale, needs_effort, pipe,
                       root_cause, roster, session_state, sha, short, top_line, tree_rows,
                       verdicts, work_of)

X_REVIEW, X_CHECKS, X_PIPE, X_PROGRESS, X_AGENT, X_QUIET = 46, 58, 62, 94, 123, 141
FIXED = 8                        # rows that are not the map or the band: top, gap, four, header, hints
BAND_MIN, BAND_MAX = 9, 16       # the bottom band, its top border included
EFFORT_W = 99                    # the needs-effort pane, padding and border included
DIM = "#5c5b56"                  # rows the filter has set aside
CI = {"pass": ("✓", GOOD), "fail": ("✗", CRIT), "run": ("…", WARN), "none": ("·", AXIS)}
Segments = list[tuple[str, str]]


def fixable(pr: PR) -> bool:
    """The wait this branch sits in ends at something you can fix."""
    end, _ = root_cause(pr)
    return end is not None and end.state in ("needs you", "no agent")


FILTERS = [
    ("everything", lambda pr: True),
    ("not working", lambda pr: (here := pipe(pr)) is not None and here.state != "working"),
    ("needs you", fixable),
]


# ---- one cell at a time ---------------------------------------------------------------

def review_segments(pr: PR) -> Segments:
    latest = verdicts(pr)
    if not latest:
        return [("·", AXIS)]
    out: Segments = [(f"r{max(e.round for e in latest.values())} ", MUTED)]
    out += [(WORD[e.verdict][0], WORD[e.verdict][1]) for e in latest.values()]
    if pr.threads[0]:
        out += [(" ◆", MUTED), (str(pr.threads[0]), INK)]
    return out


def end_segments(pr: PR, spin: str) -> Segments:
    """Where a wait ends: an agent at work, you, an agent to be assigned, or a person."""
    end = pipe(pr)
    if end is None:
        return []
    if end.state == "working":
        return [(spin + " ", INK), ("working", INK)]
    if end.state == "needs you":
        return [("● ", ACCENT), ("you", f"bold {INK}")]
    if end.state == "no agent":
        return [("● ", ACCENT), ("an agent", f"bold {INK}")]
    return [(end.on, INK2)]


def pipeline_segments(pr: PR, spin: str = SPIN[0], keys: bool = False) -> Segments:
    """Working, or what the branch waits on, read left to right along the chain to its end.
    This is the sequence the per-person columns of option 2 hid."""
    here = pipe(pr)
    if here is None:
        return [(f"review notes for {short(pr.parent)}" if pr.parent else "review notes", MUTED)]
    if here.state == "working":
        return [(spin + " ", INK), ("working", INK)]
    if here.state == "needs you":
        return [("● ", ACCENT), ("waits on you", f"bold {INK}")]
    if here.state == "no agent":
        return [("● ", ACCENT), ("waits on an agent", f"bold {INK}")]
    if here.on_pr is None:
        return [("○ ", MUTED), (f"waits on {here.on}", INK2)]
    links = chain(pr)[1:]
    out: Segments = [("○ ", MUTED), ("waits on ", INK2)]
    for link in links:
        out += [(link.key if keys else short(link), INK2), (" → ", MUTED)]
    return out + end_segments(links[-1], spin)


def progress_segments(pr: PR) -> Segments:
    """ "in progress" only while an agent works; otherwise "stalled, …" or "not started". """
    here = pipe(pr)
    if here is None:
        return []
    if here.state == "working":
        return [(here.progress, INK2)]
    return [(here.progress, INK if here.partly else MUTED)]


def agent_segments(pr: PR, spin: str = SPIN[0]) -> Segments:
    """Your session on the branch and what it is doing. On someone else's branch, whose it
    is, with your session in front when the move there is yours (a review)."""
    session = agent(pr)
    here = pipe(pr)
    theirs: Segments = [] if pr.mine else [(f"{pr.author}'s", MUTED)]
    if session is None:
        if pr.mine or (here is not None and "you" in here.who):
            return [("—", AXIS)] + ([(" · ", AXIS)] + theirs if theirs else [])
        return theirs
    if theirs:                                                 # your review session, their branch
        mark = (spin, INK) if session.live else ("▷", MUTED)
        return [mark, (f" {session.serial} · ", INK2)] + theirs
    out: Segments = {
        "working": [(spin, INK), (f" {session.serial} working", INK2)],
        "at its prompt": [("▷", ACCENT), (f" {session.serial} at prompt", INK)],
        "waiting": [("▷", MUTED), (f" {session.serial} waiting", INK2)],
    }[session_state(session)]
    if session.name:
        out.append((" ≠", f"bold {CRIT}"))
    return out


def write(canvas: Canvas, x: int, y: int, segments: Segments, width: int, dim: bool = False) -> None:
    """Segments laid end to end from x, cut at `width` cells with an ellipsis."""
    room = width
    for text, style in segments:
        if room <= 0:
            return
        if len(text) > room:
            text = text[:max(0, room - 1)] + "…"
        canvas.put(x, y, text, DIM if dim else style)
        x += len(text)
        room -= len(text)


def append(text: Text, segments: Segments) -> Text:
    for part, style in segments:
        text.append(part, style=style)
    return text


# ---- the lines under the top line ------------------------------------------------------

def four_lines(prs: list[PR]) -> Text:
    """Option 5's four lines, with option 1's "have work" names riding on the fourth, so
    people are named once: here, and not again in the band below."""
    lines = effort_lines(prs)
    busy = [name for name, has_work in roster(prs) if has_work]
    free = free_people(prs)
    out = Text()
    for i, line in enumerate(lines):
        if free and busy and i == len(lines) - 1:              # the "Nothing to do" line
            line = line.copy()
            line.append("        have work  ", style=MUTED)
            line.append(" · ".join(busy), style=INK2)
        out.append_text(line).append("\n")
    if not free and busy:
        out.append(f"  {'Have work':<16}", style=MUTED)
        out.append(" · ".join(busy), style=INK2)
        out.append("   everyone has something to do", style=MUTED)
    return out


# ---- the bottom band -------------------------------------------------------------------

def effort_text(prs: list[PR], selected: PR | None, room: int, width: int) -> Text:
    """What needs effort to get the pipeline moving, most urgent first. Each line leads with
    why it is urgent, then says what it waits on and what it holds up."""
    text = Text("needs effort to get moving", style=f"bold {INK}")
    text.append("   most urgent first: is an agent waiting on it, then how long quiet\n",
                style=MUTED)
    found = needs_effort(prs)
    if not found:
        return text.append("nothing: everything that can move is moving" if flat(prs)
                           else "nothing open", style=MUTED)
    key_w = max(len(e.pr.key) for e in found) + 1
    for rank, effort in enumerate(found[:room], start=1):
        here = effort.pipe
        line = Text(f"{rank:<2}", style=MUTED)
        line.append("● " if effort.yours else "○ ", style=ACCENT if effort.yours else MUTED)
        line.append(f"{effort.pr.key:<{key_w}}", style=f"bold {INK}")
        urgency = Text()
        if effort.waiting:
            held, quiet = effort.urgency.split(" · ", 1)
            urgency.append(held, style=f"bold {INK}").append(" · ", style=MUTED)
        else:
            quiet = effort.urgency
        urgency.append(quiet, style=f"bold {INK}" if is_stale(effort.pr.age) else INK2)
        line.append_text(urgency).append(" " * max(2, 27 - urgency.cell_len))
        if effort.kind == "needs you":
            line.append(f"at its prompt · {here.what}", style=INK)
        elif effort.kind == "no agent":
            line.append(f"no agent · {here.what}", style=INK)
        else:
            line.append(f"{effort.who} · {here.what}", style=INK)
            line.append(f", {here.progress}", style=INK2)
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


def detail_text(repo: str, prs: list[PR], pr: PR) -> Text:
    """The selected branch: its wait chain first, then the facts behind its row."""
    icon, color, label, _ = STATUS[pr.state]
    text = Text()
    text.append(f"{repo}-", style=MUTED)
    text.append(pr.name + "\n", style=f"bold underline {INK}")
    text.append(icon, style=color)
    text.append(f" {label} · {pr.title}\n", style=INK2)

    def kv(key: str, value: Text | str) -> None:
        text.append(f"{key:<9}", style=MUTED)
        text.append_text(value if isinstance(value, Text) else Text(value, style=INK))
        text.append("\n")

    here = pipe(pr)
    session = agent(pr)
    if here is None:                                           # a local review branch
        kv("chain", Text(f"review notes for {short(pr.parent)}" if pr.parent else "review notes", style=INK2))
    else:
        links = chain(pr)
        sequence = Text(short(pr), style=f"bold {INK}")        # the sequence, in order
        for link in links[1:]:
            sequence.append(" → ", style=MUTED).append(short(link), style=INK)
        end = pipe(links[-1])
        if end is not None and end.state != "working" or len(links) > 1:
            sequence.append(" → ", style=MUTED)
            append(sequence, end_segments(links[-1], SPIN[0]))
        else:
            sequence.append("  ", style=MUTED)
            append(sequence, end_segments(pr, SPIN[0]))
        kv("chain", sequence)
        last = links[-1]
        ends = pipe(last)
        if ends is not None:
            who = ", ".join(ends.who)
            kv("", Text(f"{who} · {ends.what}" if last is pr else f"{short(last)}: {who} · {ends.what}",
                        style=INK2))
            kv("", Text(ends.progress, style=INK if ends.partly and ends.state != "working" else INK2))
        queued = [p for p in flat(prs) if p is not pr and pr in chain(p)]
        if queued:
            kv("holds up", Text(", ".join(short(p) for p in queued), style=INK))
    if session is not None:
        state = session_state(session)
        mark = Text("▶ " if session.live else "▷ ",
                    style=INK if session.live else (ACCENT if state == "at its prompt" else MUTED))
        kv("agent", mark.append(f"{session.serial} {state}", style=INK))
        kv("", Text(session.note, style=INK2))
    elif pr.mine or (here is not None and "you" in here.who):
        kv("agent", Text("none assigned", style=INK if here and here.state == "no agent" else INK2))
    else:
        kv("agent", Text(f"{pr.author}'s branch, on GitHub", style=INK2))

    names = Text()                                             # the four names that should read alike
    names.append("✓ ", style=GOOD).append("branch ", style=INK)
    own = agent(pr) if pr.mine else None
    if own is not None and own.name:
        names.append("≠ ", style=f"bold {CRIT}").append("session ", style=INK)
    elif own is not None:
        names.append("✓ ", style=GOOD).append("session ", style=INK)
    if pr.is_pr:
        names.append("✓ ", style=GOOD).append(f"PR #{pr.number} ", style=INK)
    elif pr.state != "findings":
        names.append("· ", style=MUTED).append(f"#{pr.number} reserved ", style=INK2)
    names.append("✓ ", style=GOOD).append("Jira", style=INK)
    kv("names", names)
    if own is not None and own.name:
        kv("", Text(f"is {own.name}, want {pr.key}-{own.serial}", style=INK2))

    root = pr.root()
    size = sum(p.state != "findings" for p in root.walk())
    where = Text(sha(repo, root.base, root.behind), style=INK)
    where.append(f" · {root.base} " + ("tip" if not root.behind else f"{root.behind} back"), style=INK2)
    if size > 1:
        where.append(f" · level {pr.depth} of {size}", style=INK2)
    kv("base", where)

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
        kv("review", Text("nobody has been asked yet", style=INK2))
    ci_icon, ci_color = CI[pr.ci]
    kv("checks", Text(f"{ci_icon} ", style=ci_color).append(pr.ci_note or "none yet", style=INK))
    kv("merge", Text("⚠ ", style=CRIT).append(pr.merge, style=INK) if "conflict" in pr.merge
       else Text(pr.merge, style=INK2))
    if pr.same_ticket:
        kv("also", Text("! ", style=WARN).append(f"{pr.ticket.upper()} is on {pr.same_ticket} too", style=INK))

    end, end_pr = root_cause(pr)                               # what else waits on the same person
    if end is not None and end.state == "waiting" and end.on_pr is None:
        for name in end.who:
            others = [p for p in work_of(prs, name) if p is not end_pr]
            if others:
                kv(f"{name}"[:7], Text("also holds ", style=MUTED).append(
                    ", ".join(short(p) for p in others[:4]), style=INK2))
    return text


def legend_text(width: int, rows: int) -> Text:
    canvas = Canvas(max(60, width), max(1, rows))
    canvas.put(0, 0, "pull request state", f"bold {INK}")
    y = 1
    for group, color in GROUPS:
        first = True
        for icon, _, label, in_group in STATUS.values():
            if in_group != group:
                continue
            if first:
                canvas.put(0, y, "━", color)
                canvas.put(2, y, group, INK2)
                first = False
            canvas.put(19, y, icon, color)
            canvas.put(21, y, label, MUTED)
            y += 1
        if group == "broken":
            canvas.put(19, y, "✗", CRIT)
            canvas.put(21, y, "a check is failing", MUTED)
            y += 1

    x = 46
    canvas.put(x, 0, "pipeline", f"bold {INK}")
    for y, (mark, color, words) in enumerate((
            (SPIN[2], INK, "working: an agent of yours is on it right now."),
            ("", "", 'The only thing called "in progress".'),
            ("●", ACCENT, "idle, and you can fix it: a session at its"),
            ("", "", "prompt, a move with no agent, a person with"),
            ("", "", "nothing to do. Blue means nothing else."),
            ("○", MUTED, "waits on someone else: a person, or another"),
            ("", "", "branch. Read the arrows left to right:"),
            ("→", MUTED, "pr-0025 waits on pr-0024, which waits on mira."),
            ("", "", ""),
            ("", "", "stalled      partly done on GitHub, nobody on it"),
            ("", "", "not started  nothing done yet")), start=1):
        canvas.put(x, y, mark, color)
        canvas.put(x + 2, y, words, MUTED)

    x = 98
    canvas.put(x, 0, "the map, and other marks", f"bold {INK}")
    for y, (mark, color, words) in enumerate((
            ("┬ ┼", MUTED, "a commit, by short hash: where a stack"),
            ("", "", "hangs, or where another base was cut"),
            ("▷", MUTED, "a session of yours that is not working"),
            ("≠", CRIT, "a session name breaks the pattern"),
            ("◆", MUTED, "open review threads"),
            ("r2", MUTED, "review round, then each reviewer's word"),
            ("", "", ""),
            ("", "", "repos on the top line:"),
            ("●3", ACCENT, "idle things you can fix there"),
            ("○2", MUTED, "people a branch waits on")), start=1):
        canvas.put(x, y, mark, color)
        canvas.put(x + 4, y, words, MUTED)
    return canvas.text()


class TreeTableV3(App):
    CSS = f"""
    Screen {{ background: {PLANE}; color: {INK}; }}
    #top, #gap, #cols, #hints {{ height: 1; padding: 0 1; text-wrap: nowrap; text-overflow: ellipsis; }}
    #four {{ height: 4; padding: 0 1; text-wrap: nowrap; text-overflow: ellipsis; }}
    #map {{ height: 1fr; padding: 0 1; }}
    #band {{ height: {BAND_MAX}; border-top: solid {HAIR}; }}
    #effort {{ width: {EFFORT_W}; height: 1fr; padding: 0 1; border-right: solid {HAIR}; }}
    #detail {{ width: 1fr; height: 1fr; padding: 0 1; }}
    #legend {{ width: 1fr; height: 1fr; padding: 0 1; display: none; }}
    #effort, #detail, #legend {{ text-wrap: nowrap; text-overflow: ellipsis; }}
    #band.legend #effort, #band.legend #detail {{ display: none; }}
    #band.legend #legend {{ display: block; }}
    #band.narrow #effort {{ width: 66; }}
    #hints {{ color: {MUTED}; }}
    """
    BINDINGS = [
        Binding("up", "move(-1)", show=False), Binding("down", "move(1)", show=False),
        Binding("n", "next_effort", show=False), Binding("f", "cycle_filter", show=False),
        Binding("l", "toggle_legend", show=False),
        Binding("tab", "repo(1)", show=False, priority=True),
        Binding("shift+tab", "repo(-1)", show=False, priority=True),
        Binding("q", "quit", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.repo = next(iter(REPOS))
        self.sel = 0                  # index among the branch rows
        self.top = 0                  # first map row on screen, when the window is too short
        self.filter = 0
        self.frame = 0                # turns the dots before "working"; nothing else moves
        self.band_h = BAND_MAX

    def compose(self) -> ComposeResult:
        yield Static(id="top")
        yield Static(id="gap")
        yield Static(id="four")
        yield Static(id="cols")
        yield Static(id="map")
        with Horizontal(id="band"):
            yield Static(id="effort")
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
        self.query_one("#map", Static).update(self.map())

    # ---- state ---------------------------------------------------------------
    def rows(self) -> list[Row]:
        return tree_rows(self.repo)

    def branches(self) -> list[PR]:
        return [row.pr for row in self.rows() if row.pr is not None]

    def selected(self) -> PR | None:
        found = self.branches()
        return found[min(self.sel, len(found) - 1)] if found else None

    def fit(self) -> int:
        """The map gets the rows it needs so every base and stack shows at once; the band
        takes what is left, within its limits. Returns the rows the map has."""
        spare = self.size.height - FIXED - max(2, len(self.rows()))
        band = max(BAND_MIN, min(BAND_MAX, spare))
        if band != self.band_h:
            self.band_h = band
            self.query_one("#band").styles.height = band
        return max(3, self.size.height - FIXED - band)

    # ---- drawing -------------------------------------------------------------
    def draw(self) -> None:
        prs = REPOS[self.repo]
        pr = self.selected()
        self.fit()
        self.query_one("#top", Static).update(top_line(self.repo, frame=self.frame))
        self.query_one("#four", Static).update(four_lines(prs))
        self.query_one("#map", Static).update(self.map())            # sets what #cols reports
        narrow = self.size.width < 140
        pane = (66 if narrow else EFFORT_W) - 3
        self.query_one("#effort", Static).update(effort_text(prs, pr, self.band_h - 3, pane))
        detail = self.query_one("#detail", Static)
        detail.update(detail_text(self.repo, prs, pr) if pr is not None
                      else Text("no open pull requests or local branches", style=MUTED))
        self.query_one("#legend", Static).update(legend_text(self.size.width - 2, self.band_h - 1))
        hints = Text("↑↓ branch    n next thing needing effort    f filter: ")
        hints.append(FILTERS[self.filter][0], style=f"bold {INK}" if self.filter else MUTED)
        hints.append("    l legend    ⇥ next repo    q quit")
        self.query_one("#hints", Static).update(hints)

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

        head = Text(style=MUTED)
        head.append("  branch")
        note = f"   rows {self.top + 1}–{self.top + len(shown)} of {len(rows)}" if len(rows) > height else ""
        head.append(note.ljust(X_REVIEW - 8), style=INK2)
        for label, x, nxt in (("review", X_REVIEW, X_CHECKS), ("ci", X_CHECKS, X_PIPE),
                              ("pipeline", X_PIPE, X_PROGRESS), ("progress", X_PROGRESS, X_AGENT),
                              ("agent", X_AGENT, X_QUIET)):
            head.append(label.ljust(nxt - x))
        head.append("quiet")
        self.query_one("#cols", Static).update(head)

        canvas = Canvas(width, max(1, len(shown)) + (0 if branches else 1))
        if not branches:
            canvas.put(0, len(shown), "  no open pull requests or local branches", MUTED)
        keep = FILTERS[self.filter][1]
        for y, row in enumerate(shown):
            lead = len(row.lead)
            if row.kind == "base":
                canvas.put(0, y, row.lead, AXIS)
                canvas.put(lead, y, row.label, f"bold {INK}")
                x = lead + len(row.label) + 2
                if row.sha and row.sha in row.note:            # "branched from main at 18deb8e, …"
                    before, after = row.note.split(row.sha, 1)
                    write(canvas, x, y, [(before, MUTED), (row.sha, INK2), (after, MUTED)], width - x)
                else:
                    canvas.put(x, y, row.note or "its history runs down from the tip", MUTED)
            elif row.kind == "commit":
                canvas.put(0, y, row.lead[:-2], AXIS)
                canvas.put(lead - 2, y, row.lead[-2:], MUTED)
                canvas.put(lead, y, row.sha, INK2)
                canvas.put(lead + len(row.sha) + 1, y, row.label, MUTED)
            else:
                self.draw_branch(canvas, y, row, not keep(row.pr))
                if row.pr is selected:
                    for x in range(width):
                        canvas.paint(x, y, 1, RAISED)
        return canvas.text()

    def draw_branch(self, canvas: Canvas, y: int, row: Row, dim: bool) -> None:
        pr = row.pr
        spin = SPIN[(self.frame + y * 3) % len(SPIN)]
        icon, color, _, _ = STATUS[pr.state]
        canvas.put(0, y, row.lead, AXIS)
        x = len(row.lead)
        canvas.put(x, y, icon, DIM if dim else color)
        write(canvas, x + 2, y, [(pr.key, f"bold {INK}"), ("-" + pr.slug, INK2)], X_REVIEW - x - 3, dim)
        write(canvas, X_REVIEW, y, review_segments(pr), X_CHECKS - X_REVIEW - 1, dim)
        ci_icon, ci_color = CI[pr.ci]
        canvas.put(X_CHECKS, y, ci_icon, DIM if dim else ci_color)
        write(canvas, X_PIPE, y, pipeline_segments(pr, spin), X_PROGRESS - X_PIPE - 1, dim)
        write(canvas, X_PROGRESS, y, progress_segments(pr), X_AGENT - X_PROGRESS - 1, dim)
        write(canvas, X_AGENT, y, agent_segments(pr, spin), X_QUIET - X_AGENT - 1, dim)
        canvas.put(X_QUIET, y, pr.age.rjust(4),
                   DIM if dim else (f"bold {INK}" if is_stale(pr.age) else MUTED))

    # ---- keys ----------------------------------------------------------------
    def action_move(self, step: int) -> None:
        self.sel += step
        self.draw()

    def action_next_effort(self) -> None:
        """Jump to the next thing that needs effort, in the band's order: most urgent first."""
        branches = self.branches()
        found: list[Effort] = needs_effort(REPOS[self.repo])
        stops = [branches.index(e.pr) for e in found if e.pr in branches]
        if not stops:
            return
        self.sel = stops[(stops.index(self.sel) + 1) % len(stops)] if self.sel in stops else stops[0]
        self.draw()

    def action_cycle_filter(self) -> None:
        self.filter = (self.filter + 1) % len(FILTERS)
        self.draw()

    def action_toggle_legend(self) -> None:
        self.query_one("#band").toggle_class("legend")

    def action_repo(self, step: int) -> None:
        names = list(REPOS)
        self.repo = names[(names.index(self.repo) + step) % len(names)]
        self.sel = self.top = 0
        self.draw()


if __name__ == "__main__":
    TreeTableV3().run()
