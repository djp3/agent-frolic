"""Agent Frolic mock-up, option 1 v4: the tree table, with WHO first.
One dense screen that answers "is every agent working, and if not, who gets it going?".

- Top line (the one every layout uses): repos with their two small numbers, agents at
  work, the refresh.
- Who lines: a short block in which every line starts with a who. First your agents, by
  what they are doing (working, at a prompt, waiting on someone, or missing: a move of
  yours with no agent), each named with its branch. Then people: who is waited on and for
  what, who has nothing to do, who has work. No counts.
- The map: every base and every stack at once, in one connected tree of commit, branch and
  stack. The first two columns of every branch row say who:
      owner     your agent on the branch, "no agent", or the person whose branch it is
      waits on  empty while the owner's agent works; otherwise who or what the branch waits
                on, read left to right to where the wait ends ("pr-0024 → mira")
  then the branch in the tree, review, checks, progress ("in progress" only while an agent
  works, else "stalled, 1 of 3 threads" or "not started") and how long it has been quiet.
- The band at the bottom: "needs effort to get moving", in ORDER of urgency (is an agent
  waiting for it, then how long it has been quiet), each line led by who has to act;
  beside it the selected branch, owner and waits-on first.

Blue means one thing: idle, and you can fix it. The dots where an agent works turn; nothing
else moves. Every name and number is made up.

    .venv/bin/python mockup/option1_tree_table_v4.py

Keys: up/down pick a branch, n jumps to the next thing that needs effort (most urgent
first), o steps through who is on the repo (you, each agent, each person) and lights the
rows they own and the rows that wait on them, O steps back, escape clears it, f cycles the
filter (everything / not working / needs you), l swaps the bottom band for the legend, tab
goes to the next repo (shift+tab back), q quits.
"""
from __future__ import annotations

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Static

from frolic_v4 import (ACCENT, AXIS, CRIT, GOOD, GROUPS, HAIR, INK, INK2, ME, MUTED, PLANE, PR,
                       RAISED, REPOS, SPIN, STATUS, WARN, WORD, Canvas, Effort, Member, Row,
                       Segments, agent, cast, flat, free_people, is_stale, needs_effort,
                       owned_by, owner, owner_segments, root_cause, roster, session_state, sha,
                       short, top_line, tree_rows, verdicts, waits, waits_segments, work_of,
                       write)
from frolic_v4 import pipe as standing       # where a branch stands: at work, or what it waits on

X_OWNER, X_WAITS, X_TREE = 0, 14, 38         # owner, waits on, then the tree
BAND_MIN, BAND_MAX = 9, 16       # the bottom band, its top border included
EFFORT_W = 100                   # the needs-effort pane, padding and border included
GROUP_W, LABEL_W = 9, 15         # who lines: "agents   " then "▷ at a prompt    "
GAP = "   "                      # between two names on a who line
DIM = "#5c5b56"                  # rows set aside by the filter, or by picking someone else
CI = {"pass": ("✓", GOOD), "fail": ("✗", CRIT), "run": ("…", WARN), "none": ("·", AXIS)}


def columns(width: int) -> tuple[int, int, int, int]:
    """Where review, ci, progress and quiet start in a map `width` cells wide. The branch
    column takes what the others leave, within limits."""
    tree_w = max(34, min(52, width - X_TREE - 12 - 4 - 30 - 5))
    review = X_TREE + tree_w
    checks = review + 12
    progress = checks + 4
    quiet = max(progress + 8, min(progress + 30, width - 5))
    return review, checks, progress, quiet


def fixable(pr: PR) -> bool:
    """The wait this branch sits in ends at something you can fix."""
    end, _ = root_cause(pr)
    return end is not None and end.state in ("needs you", "no agent")


FILTERS = [
    ("everything", lambda pr: True),
    ("not working", lambda pr: (here := standing(pr)) is not None and here.state != "working"),
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


def progress_segments(pr: PR) -> Segments:
    """ "in progress" only while an agent works; otherwise "stalled, …" or "not started". """
    here = standing(pr)
    if here is None:                                           # a local review branch
        return [(f"review notes for {short(pr.parent)}" if pr.parent else "review notes", MUTED)]
    if here.state == "working":
        return [(here.progress, INK2)]
    return [(here.progress, INK if here.partly else MUTED)]


def append(text: Text, segments: Segments) -> Text:
    for part, style in segments:
        text.append(part, style=style)
    return text


def marked(segments: Segments) -> Segments:
    """An owner cell without the blank that stands in for a mark."""
    return segments[1:] if segments and not segments[0][0].strip() else segments


# ---- who: the cast, and the lines about it under the top line ---------------------------

def member_label(member: Member) -> str:
    """An agent's serial is not unique by itself, so away from its row it carries its branch."""
    if member.kind in ("agent", "no agent") and member.pr is not None:
        return f"{member.name} · {short(member.pr)}"
    return member.name


def same(picked: Member | None, member: Member) -> bool:
    return (picked is not None and picked.kind == member.kind and picked.name == member.name
            and picked.pr is member.pr)


def who_lines(prs: list[PR], picked: Member | None = None, spin: str = SPIN[0]) -> list[Text]:
    """Every line starts with a who. Your agents first, by what they are doing; then the
    branches where a move of yours has no agent; then people. The member picked with `o`
    is raised where it is named."""
    members = cast(prs)
    found = needs_effort(prs)
    effort_at = {id(e.pr): e for e in found}
    said: set[str] = set()
    lines: list[Text] = []

    def start(group: str, mark: str, mark_style: str, label: str, label_style: str) -> Text:
        text = Text(f"{'' if group in said else group:<{GROUP_W}}", style=f"bold {INK2}")
        said.add(group)
        text.append(f"{mark} ", style=mark_style)
        return text.append(f"{label:<{LABEL_W}}", style=label_style)

    def held(text: Text, effort: Effort | None, style: str) -> None:
        if effort is not None and effort.behind:
            text.append(f" (holds up {', '.join(short(p) for p in effort.behind)})", style=style)

    def chip(text: Text, member: Member, name_style: str, branch_style: str) -> None:
        on = f" on {RAISED}" if same(picked, member) else ""
        if on:
            name_style = branch_style = f"bold {INK}"
        text.append(member.name, style=name_style + on)
        text.append(" · ", style=MUTED + on)
        text.append(short(member.pr), style=branch_style + on)

    def name(text: Text, who: str, style: str) -> None:
        lit = picked is not None and picked.kind == "person" and picked.name == who
        text.append(who, style=f"bold {INK} on {RAISED}" if lit else style)

    agents = [m for m in members if m.kind == "agent"]
    working = [m for m in agents if m.state == "working"]
    prompt = [m for m in agents if m.state == "at its prompt"]
    waiting = [m for m in agents if m.state == "waiting"]
    unassigned = [e for e in found if e.kind == "no agent"]

    if working:
        text = start("agents", spin, INK, "working", INK2)
        for i, member in enumerate(working):
            text.append(GAP if i else "")
            chip(text, member, INK2, INK)
            if member.pr.state == "findings":
                text.append(" review notes", style=MUTED)
        lines.append(text)
    if prompt:
        text = start("agents", "▷", ACCENT, "at a prompt", INK)
        for i, member in enumerate(prompt):
            text.append(GAP if i else "")
            chip(text, member, INK, f"bold {INK}")
            effort = effort_at.get(id(member.pr)) or effort_at.get(id(member.pr.parent))
            if effort is not None:
                text.append(f" {effort.pipe.what}", style=INK)
            held(text, effort, INK)
        lines.append(text)
    if waiting:
        text = start("agents", "·", MUTED, "waiting", INK2)
        for i, member in enumerate(waiting):
            text.append(GAP if i else "")
            chip(text, member, INK2, INK)
            wait = waits(member.pr)
            if wait is not None and wait.said:
                text.append(" on ", style=MUTED).append(wait.said, style=INK2)
        lines.append(text)
    if unassigned:
        text = start("agents", "●", ACCENT, "no agent", INK)
        for i, effort in enumerate(unassigned):
            lit = picked is not None and picked.kind == "no agent" and picked.pr is effort.pr
            text.append(GAP if i else "")
            text.append(short(effort.pr), style=f"bold {INK}" + (f" on {RAISED}" if lit else ""))
            text.append(f" {effort.pipe.what}", style=INK)
            held(text, effort, INK)
        lines.append(text)
    if not lines and flat(prs):
        lines.append(start("agents", " ", MUTED, "none of yours", MUTED).append(
            "on this repo", style=MUTED))

    waited: dict[str, list[Effort]] = {}
    for effort in found:
        if effort.kind == "person":
            for who in effort.who.split(","):
                waited.setdefault(who.strip(), []).append(effort)
    if waited:
        text = start("people", "○", MUTED, "waited on", INK2)
        for i, (who, efforts) in enumerate(waited.items()):
            text.append(GAP if i else "")
            name(text, who, INK2)
            text.append(" for ", style=MUTED)
            for j, effort in enumerate(efforts):
                text.append(", " if j else "", style=MUTED)
                text.append(short(effort.pr), style=INK2)
                held(text, effort, MUTED)
        lines.append(text)
    busy = [who for who, has_work in roster(prs) if has_work and who != "you"]
    free = free_people(prs)
    if free:
        text = start("people", "●", ACCENT, "nothing to do", INK)
        for i, who in enumerate(free):
            text.append(GAP if i else "")
            name(text, who, f"bold {INK}")
        if busy:
            text.append("        have work  ", style=MUTED)
    elif busy:
        text = start("people", " ", MUTED, "have work", MUTED)
    if free or busy:
        for i, who in enumerate(busy):
            text.append(GAP if i else "")
            name(text, who, INK2)
        if not free:
            text.append("        everyone has something to do", style=MUTED)
        lines.append(text)
    if not lines:
        lines.append(Text("  Nothing open.", style=MUTED))
    return lines


# ---- the bottom band -------------------------------------------------------------------

def effort_text(prs: list[PR], selected: PR | None, room: int, width: int) -> Text:
    """What needs effort to get things moving, most urgent first. Each line leads with who
    has to act and why it is urgent, then the branch, what is to be done, what it holds up."""
    text = Text("needs effort to get moving", style=f"bold {INK}")
    text.append("   most urgent first: is an agent waiting on it, then how long quiet\n",
                style=MUTED)
    found = needs_effort(prs)
    if not found:
        return text.append("nothing: everything that can move is moving" if flat(prs)
                           else "nothing open", style=MUTED)
    who_w = max(len(e.who) for e in found) + 2
    why_w = max(len(e.urgency) for e in found) + 2
    key_w = max(len(e.pr.key) for e in found) + 2
    for rank, effort in enumerate(found[:room], start=1):
        here = effort.pipe
        line = Text(f"{rank:<2}", style=MUTED)
        line.append("● " if effort.yours else "○ ", style=ACCENT if effort.yours else MUTED)
        line.append(f"{effort.who:<{who_w}}", style=f"bold {INK}" if effort.yours else INK)
        urgency = Text()
        if effort.waiting:
            held, quiet = effort.urgency.split(" · ", 1)
            urgency.append(held, style=f"bold {INK}").append(" · ", style=MUTED)
        else:
            quiet = effort.urgency
        urgency.append(quiet, style=f"bold {INK}" if is_stale(effort.pr.age) else INK2)
        line.append_text(urgency).append(" " * max(2, why_w - urgency.cell_len))
        line.append(f"{effort.pr.key:<{key_w}}", style=f"bold {INK}")
        if effort.kind == "needs you":
            line.append("at its prompt · ", style=INK2).append(here.what, style=INK)
        elif effort.kind == "no agent":
            line.append("no agent · ", style=INK2).append(here.what, style=INK)
        else:
            line.append(here.what, style=INK).append(f", {here.progress}", style=INK2)
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
    """The selected branch: its owner and what it waits on first, then the facts behind its row."""
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

    who = owner(pr)
    wait = waits(pr)
    here = standing(pr)
    owned = append(Text(), marked(owner_segments(pr)))
    if who.kind == "agent":
        owned.append(f" {who.state}", style=INK if who.fixable else INK2)
        if who.worker is not None and who.worker.name:
            owned.append(" ≠", style=f"bold {CRIT}")
    elif who.kind == "no agent":
        owned.append(" · your move, nobody on it" if who.fixable else " · none needed yet",
                     style=INK2 if who.fixable else MUTED)
    elif who.kind == "person":
        owned.append(" · on GitHub, agents not seen", style=MUTED)
    else:
        owned.append(" · the session has ended", style=MUTED)
    kv("owner", owned)
    if who.worker is not None:
        kv("", Text(who.worker.note, style=INK2))
        if who.worker.name:
            kv("", Text(f"is {who.worker.name}, want {pr.key}-{who.worker.serial}", style=INK2))
    session = agent(pr)
    if who.kind == "person" and session is not None:           # your review session, their branch
        state = session_state(session)
        mine = Text(SPIN[0] + " " if session.live else "▷ ",
                    style=INK if session.live else (ACCENT if state == "at its prompt" else MUTED))
        kv("yours", mine.append(f"agent {session.serial} {state}, review notes", style=INK2))

    if wait is None:
        kv("waits on", Text(f"nothing: review notes for {short(pr.parent)}" if pr.parent
                            else "nothing: review notes", style=INK2))
    elif wait.kind == "working":
        kv("waits on", Text("nothing: its agent is at work", style=INK2))
        kv("", Text(wait.what, style=INK2))
        kv("", Text(here.progress, style=INK2))
    else:
        kv("waits on", append(Text(), waits_segments(pr)))
        end = standing(wait.end_pr) if wait.end_pr is not None else None
        kv("", Text(wait.what if wait.end_pr is pr else f"{short(wait.end_pr)}: {wait.what}",
                    style=INK2))
        if end is not None:
            kv("", Text(end.progress, style=INK if end.partly and end.state != "working" else INK2))
    queued = [p for p in flat(prs) if p is not pr and (w := waits(p)) is not None
              and (pr in w.links or (w.end_pr is pr and w.kind != "working"))]
    if queued:
        kv("holds up", Text(", ".join(short(p) for p in queued), style=INK))

    if pr.history:
        event = pr.history[-1]
        told = Text()
        if event.kind == "push":
            told.append(f"{event.commits} commits pushed by {event.who} {event.age}", style=INK2)
        else:
            mark, mark_color, _ = WORD[event.verdict]
            name = "you" if event.who == ME else event.who
            was = "were" if name == "you" else "was"
            words = {"approved": "approved", "changes": "asked for changes",
                     "requested": f"{was} asked to review",
                     "rerequested": f"{was} asked to look again"}[event.verdict]
            told.append(f"r{event.round} ", style=MUTED).append(mark, style=mark_color)
            told.append(f" {name} {words} {event.age}", style=INK)
        if pr.threads[0]:
            told.append(f" · {pr.threads[0]} open", style=INK2)
        kv("review", told)
    elif pr.is_pr:
        kv("review", Text("nobody has been asked yet", style=INK2))
    ci_icon, ci_color = CI[pr.ci]
    kv("checks", Text(f"{ci_icon} ", style=ci_color).append(pr.ci_note or "none yet", style=INK))
    kv("merge", Text("⚠ ", style=CRIT).append(pr.merge, style=INK) if "conflict" in pr.merge
       else Text(pr.merge, style=INK2))

    root = pr.root()
    size = sum(p.state != "findings" for p in root.walk())
    where = Text(sha(repo, root.base, root.behind), style=INK)
    where.append(f" · {root.base} " + ("tip" if not root.behind else f"{root.behind} back"), style=INK2)
    if size > 1:
        where.append(f" · level {pr.depth} of {size}", style=INK2)
    kv("base", where)
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

    x = 44
    canvas.put(x, 0, "owner, and what the branch waits on", f"bold {INK}")
    for y, (mark, color, name, words) in enumerate((
            (SPIN[2], INK, "agent 01", "your agent, at work right now: the only"),
            ("", "", "", 'thing called "in progress"'),
            ("▷", ACCENT, "agent 01", "your agent sits at its prompt, for you"),
            ("●", ACCENT, "no agent", "your move, and no agent is on it"),
            ("", "", "", "blue: idle, and you can fix it. Nothing else."),
            ("·", MUTED, "agent 01", "your agent, idle until someone else moves"),
            ("", "", "mira", "someone else's; their agents can't be seen"),
            ("", "", "waits on", "empty while the owner's agent works. Else"),
            ("", "", "", "read left to right: pr-0024 → mira is"),
            ("", "", "", '"waits on pr-0024, which waits on mira"')), start=1):
        canvas.put(x, y, mark, color)
        canvas.put(x + 2, y, name, INK2)
        canvas.put(x + 12, y, words, MUTED)

    x = 103
    canvas.put(x, 0, "the map, and other marks", f"bold {INK}")
    for y, (mark, color, words) in enumerate((
            ("┬ ┼", MUTED, "a commit, by short hash: where a stack"),
            ("", "", "hangs, or where another base was cut"),
            ("≠", CRIT, "a session name breaks the pattern"),
            ("◆", MUTED, "open review threads"),
            ("r2", MUTED, "review round, then each reviewer's word"),
            ("", "", "stalled: partly done on GitHub, nobody"),
            ("", "", "on it. not started: nothing done yet"),
            ("o", INK2, "light one owner's rows; the rest dim"),
            ("●3", ACCENT, "top line: idle things you can fix there"),
            ("○2", MUTED, "top line: people a branch waits on")), start=1):
        canvas.put(x, y, mark, color)
        canvas.put(x + 4, y, words, MUTED)
    return canvas.text()


class TreeTableV4(App):
    CSS = f"""
    Screen {{ background: {PLANE}; color: {INK}; }}
    #top, #gap, #cols, #hints {{ height: 1; padding: 0 1; text-wrap: nowrap; text-overflow: ellipsis; }}
    #who {{ height: 6; padding: 0 1; text-wrap: nowrap; text-overflow: ellipsis; }}
    #map {{ height: 1fr; padding: 0 1; }}
    #band {{ height: {BAND_MAX}; border-top: solid {HAIR}; }}
    #effort {{ width: {EFFORT_W}; height: 1fr; padding: 0 1; border-right: solid {HAIR}; }}
    #detail {{ width: 1fr; height: 1fr; padding: 0 1; }}
    #legend {{ width: 1fr; height: 1fr; padding: 0 1; display: none; }}
    #effort, #detail, #legend {{ text-wrap: nowrap; text-overflow: ellipsis; }}
    #band.legend #effort, #band.legend #detail {{ display: none; }}
    #band.legend #legend {{ display: block; }}
    #band.narrow #effort {{ width: 70; }}
    #hints {{ color: {MUTED}; }}
    """
    BINDINGS = [
        Binding("up", "move(-1)", show=False), Binding("down", "move(1)", show=False),
        Binding("n", "next_effort", show=False), Binding("f", "cycle_filter", show=False),
        Binding("o", "who(1)", show=False), Binding("O", "who(-1)", show=False),
        Binding("escape", "clear_who", show=False),
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
        self.who: int | None = None   # index into the cast: whose rows are lit, if anyone's
        self.frame = 0                # turns the dots where an agent works; nothing else moves
        self.band_h = BAND_MAX
        self.who_h = 6

    def compose(self) -> ComposeResult:
        yield Static(id="top")
        yield Static(id="gap")
        yield Static(id="who")
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
        self.draw_who()
        self.query_one("#map", Static).update(self.map())

    # ---- state ---------------------------------------------------------------
    def rows(self) -> list[Row]:
        return tree_rows(self.repo)

    def branches(self) -> list[PR]:
        return [row.pr for row in self.rows() if row.pr is not None]

    def selected(self) -> PR | None:
        found = self.branches()
        return found[min(self.sel, len(found) - 1)] if found else None

    def picked(self) -> Member | None:
        members = cast(REPOS[self.repo])
        return members[self.who] if self.who is not None and self.who < len(members) else None

    def lit(self) -> list[PR] | None:
        """The rows the picked member owns, plus the rows that wait on them; None when
        nobody is picked."""
        member = self.picked()
        return owned_by(REPOS[self.repo], member) if member is not None else None

    def fit(self) -> int:
        """The map gets the rows it needs so every base and stack shows at once; the band
        takes what is left, within its limits. Returns the rows the map has."""
        fixed = 4 + self.who_h           # top, gap, who lines, column heads, hints
        spare = self.size.height - fixed - max(2, len(self.rows()))
        band = max(BAND_MIN, min(BAND_MAX, spare))
        if band != self.band_h:
            self.band_h = band
            self.query_one("#band").styles.height = band
        return max(3, self.size.height - fixed - band)

    # ---- drawing -------------------------------------------------------------
    def draw_who(self) -> None:
        lines = who_lines(REPOS[self.repo], self.picked(), SPIN[self.frame % len(SPIN)])
        block = self.query_one("#who", Static)
        if len(lines) != self.who_h:
            self.who_h = len(lines)
            block.styles.height = self.who_h
        block.update(Text("\n").join(lines))

    def draw(self) -> None:
        prs = REPOS[self.repo]
        pr = self.selected()
        self.draw_who()                                               # sets the height fit() uses
        self.fit()
        self.query_one("#top", Static).update(top_line(self.repo, frame=self.frame))
        self.query_one("#map", Static).update(self.map())            # sets what #cols reports
        narrow = self.size.width < 140
        pane = (70 if narrow else EFFORT_W) - 3
        self.query_one("#effort", Static).update(effort_text(prs, pr, self.band_h - 3, pane))
        detail = self.query_one("#detail", Static)
        detail.update(detail_text(self.repo, prs, pr) if pr is not None
                      else Text("no open pull requests or local branches", style=MUTED))
        self.query_one("#legend", Static).update(legend_text(self.size.width - 2, self.band_h - 1))
        member = self.picked()
        hints = Text("↑↓ branch   n next thing needing effort   o/O who: ")
        if member is None:
            hints.append("everyone", style=MUTED)
        else:
            hints.append(member_label(member), style=f"bold {INK}")
            hints.append("   esc everyone")
        hints.append("   f filter: ")
        hints.append(FILTERS[self.filter][0], style=f"bold {INK}" if self.filter else MUTED)
        hints.append("   l legend   ⇥ next repo   q quit")
        self.query_one("#hints", Static).update(hints)

    def head(self, width: int, cols: tuple[int, int, int, int], note: str) -> Text:
        review, checks, progress, quiet = cols
        canvas = Canvas(width, 1)
        canvas.put(X_TREE, 0, "branch", MUTED)
        canvas.put(X_TREE + 9, 0, note[:max(0, review - X_TREE - 10)], INK2)
        for label, x in (("owner", X_OWNER), ("waits on", X_WAITS), ("review", review),
                         ("ci", checks), ("progress", progress), ("quiet", quiet)):
            canvas.put(x, 0, label, MUTED)
        return canvas.text()

    def map(self) -> Text:
        rows = self.rows()
        branches = [row.pr for row in rows if row.pr is not None]
        self.sel = max(0, min(len(branches) - 1, self.sel))
        selected = branches[self.sel] if branches else None
        width = max(60, self.size.width - 2)
        cols = columns(width)
        height = self.fit()
        at = next((i for i, row in enumerate(rows) if row.pr is selected), 0) if selected else 0
        self.top = max(0, min(self.top, at))                   # only a short window ever scrolls
        if at >= self.top + height:
            self.top = at - height + 1
        if at <= 2 or len(rows) <= height:
            self.top = 0
        shown = rows[self.top:self.top + height]

        member = self.picked()
        lit = self.lit()
        notes = []
        if member is not None:
            notes.append(f"lit: {member_label(member)}" + ("" if lit else ", nothing here"))
        if len(rows) > height:
            notes.append(f"rows {self.top + 1}–{self.top + len(shown)} of {len(rows)}")
        self.query_one("#cols", Static).update(self.head(width, cols, " · ".join(notes)))

        canvas = Canvas(width, max(1, len(shown)) + (0 if branches else 1))
        if not branches:
            canvas.put(0, len(shown), "  no open pull requests or local branches", MUTED)
        keep = FILTERS[self.filter][1]
        for y, row in enumerate(shown):
            x = X_TREE + len(row.lead)
            if row.kind == "base":
                canvas.put(X_TREE, y, row.lead, AXIS)
                canvas.put(x, y, row.label, f"bold {INK}")
                x += len(row.label) + 2
                if row.sha and row.sha in row.note:            # "branched from main at 18deb8e, …"
                    before, after = row.note.split(row.sha, 1)
                    write(canvas, x, y, [(before, MUTED), (row.sha, INK2), (after, MUTED)], width - x)
                else:
                    canvas.put(x, y, row.note or "its history runs down from the tip", MUTED)
            elif row.kind == "commit":
                canvas.put(X_TREE, y, row.lead[:-2], AXIS)
                canvas.put(x - 2, y, row.lead[-2:], MUTED)
                canvas.put(x, y, row.sha, INK2)
                canvas.put(x + len(row.sha) + 1, y, row.label, MUTED)
            else:
                aside = not keep(row.pr) or (lit is not None and row.pr not in lit)
                self.draw_branch(canvas, y, row, aside, cols)
                if row.pr is selected:
                    for cell in range(width):
                        canvas.paint(cell, y, 1, RAISED)
        return canvas.text()

    def draw_branch(self, canvas: Canvas, y: int, row: Row, dim: bool,
                    cols: tuple[int, int, int, int]) -> None:
        pr = row.pr
        review, checks, progress, quiet = cols
        spin = SPIN[(self.frame + y * 3) % len(SPIN)]
        off = DIM if dim else ""
        used = write(canvas, X_OWNER, y, owner_segments(pr, spin), X_WAITS - X_OWNER - 1, off)
        who = owner(pr)
        if who.worker is not None and who.worker.name and used + 2 < X_WAITS - X_OWNER:
            canvas.put(X_OWNER + used + 1, y, "≠", off or f"bold {CRIT}")
        write(canvas, X_WAITS, y, waits_segments(pr, spin), X_TREE - X_WAITS - 1, off)
        canvas.put(X_TREE, y, row.lead, AXIS)
        x = X_TREE + len(row.lead)
        icon, color, _, _ = STATUS[pr.state]
        canvas.put(x, y, icon, off or color)
        write(canvas, x + 2, y, [(pr.key, f"bold {INK}"), ("-" + pr.slug, INK2)], review - x - 3, off)
        write(canvas, review, y, review_segments(pr), checks - review - 1, off)
        ci_icon, ci_color = CI[pr.ci]
        canvas.put(checks, y, ci_icon, off or ci_color)
        write(canvas, progress, y, progress_segments(pr), quiet - progress - 1, off)
        canvas.put(quiet, y, pr.age.rjust(5),
                   off or (f"bold {INK}" if is_stale(pr.age) else MUTED))

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

    def action_who(self, step: int) -> None:
        """Step through the cast: you, each agent, each branch that lacks one, each person.
        Stepping past either end lights everyone again. The tree stays in its order; the
        selection goes to the first lit row when it is not on one already."""
        count = len(cast(REPOS[self.repo]))
        slots = [None, *range(count)]
        self.who = slots[(slots.index(self.who if self.who in slots else None) + step) % len(slots)]
        lit = self.lit()
        branches = self.branches()
        if lit and self.selected() not in lit:
            self.sel = next(i for i, pr in enumerate(branches) if pr in lit)
        self.draw()

    def action_clear_who(self) -> None:
        self.who = None
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
        self.who = None
        self.draw()


if __name__ == "__main__":
    TreeTableV4().run()
