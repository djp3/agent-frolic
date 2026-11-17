"""Agent Frolic mock-up, option 1 v2: tree table. Still the dense one, everything on a single
screen with a column per fact, but rebuilt around one question: is every agent working, and
if not, what gets it going again?

- Repos run across the top, each with two small numbers: idle things you can fix (blue) and
  people a branch waits on (grey).
- Two roster lines: who has work and who has nothing to do, then your agents as marks.
- The map: each base branch, its history from the tip backwards, and the stacks hanging off
  it. Per branch: review, checks, pipeline (working, or what it waits on, followed to where
  the wait really ends), progress (in progress only while an agent works; else stalled or
  not started), the agent on it, and how long it has been quiet.
- The band at the bottom: what needs effort to get moving, the selected branch, and its
  wait chain with whatever else waits on the same person.

Blue means one thing: idle, and you can fix it. Every name and number is made up.

    .venv/bin/python mockup/option1_tree_table_v2.py

Keys: up/down pick a branch, n jumps to the next thing that needs effort, f cycles the filter
(everything / not working / needs you), l swaps the bottom band for the legend, tab goes to
the next repo (shift+tab back), q quits.
"""
from __future__ import annotations

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Static

from frolic_v2 import (ACCENT, AXIS, CRIT, GOOD, GROUPS, HAIR, INK, INK2, ME, MUTED, PLANE, PR,
                       REPOS, RAISED, STATUS, WARN, WORD, Canvas, Row, agent, anchor_note,
                       blockers, chain, flat, free_people, is_stale, pipe, repo_status,
                       root_cause, roster, session_state, tree_rows, verdicts, work_of)

X_REVIEW, X_CHECKS, X_PIPE, X_PROGRESS, X_COUNT, X_AGENT, X_QUIET = 46, 58, 62, 94, 106, 120, 137
BAND = 16                        # rows of the bottom band, its top border included
DIM = "#5c5b56"                  # rows the filter has set aside
CI = {"pass": ("✓", GOOD), "fail": ("✗", CRIT), "run": ("…", WARN), "none": ("·", AXIS)}
Segments = list[tuple[str, str]]


def ref(pr: PR) -> str:
    return f"pr-{pr.number:04d}"


def needs_you(pr: PR) -> bool:
    """The wait this branch sits in ends at something you can fix."""
    end, _ = root_cause(pr)
    return end is not None and end.state in ("needs you", "no agent")


FILTERS = [
    ("everything", lambda pr: True),
    ("not working", lambda pr: (here := pipe(pr)) is not None and here.state != "working"),
    ("needs you", needs_you),
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


def end_word(pr: PR) -> tuple[str, bool]:
    """Where a wait ends, in a word, and whether that end is yours to fix."""
    end = pipe(pr)
    if end is None:
        return "", False
    word = {"working": "working", "needs you": "you", "no agent": "an agent"}.get(end.state, end.on)
    return word, end.state in ("needs you", "no agent")


def pipeline_segments(pr: PR) -> Segments:
    """Working, or what the branch waits on, followed along the chain to its end."""
    here = pipe(pr)
    if here is None:
        return [(f"review notes for {ref(pr.parent)}" if pr.parent else "review notes", MUTED)]
    if here.state == "working":
        return [("▶ ", INK), ("working", INK)]
    if here.state == "needs you":
        return [("● ", ACCENT), ("waits on you", f"bold {INK}")]
    if here.state == "no agent":
        return [("● ", ACCENT), ("needs an agent", f"bold {INK}")]
    if here.on_pr is None:
        return [("○ ", MUTED), (f"waits on {here.on}", INK2)]
    links = chain(pr)[1:]
    word, yours = end_word(links[-1])
    return [("○ ", MUTED), ("waits on " + " ▸ ".join(ref(p) for p in links), INK2),
            (" ▸ ", MUTED), (word, f"bold {INK}" if yours else INK2)]


def progress_segments(pr: PR) -> tuple[Segments, Segments]:
    """The word (in progress / stalled / not started) and the count, as two cells."""
    here = pipe(pr)
    if here is None:
        return [], []
    working = here.state == "working"
    word = "in progress" if working else ("stalled" if here.partly else "not started")
    style = INK2 if working else (f"bold {INK}" if here.partly else MUTED)
    count = f"{here.done}/{here.total} {here.unit}" if here.total and (here.partly or working) else ""
    return [(word, style)], [(count, INK2)]


def agent_segments(pr: PR) -> Segments:
    if not pr.mine:
        return [(f"{pr.author}'s", MUTED)]
    session = agent(pr)
    if session is None:
        return [("—", AXIS)]
    state = session_state(session)
    out: Segments = {
        "working": [("▶", INK), (f" {session.serial} working", INK2)],
        "at its prompt": [("▷", ACCENT), (f" {session.serial} at prompt", INK)],
        "waiting": [("▷", MUTED), (f" {session.serial} waiting", INK2)],
    }[state]
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


# ---- the bottom band -------------------------------------------------------------------

def effort_text(prs: list[PR], selected: PR | None) -> Text:
    """What needs effort to get the pipeline moving: yours first, then people to nudge."""
    text = Text("needs effort to get moving\n", style=f"bold {INK}")
    found = blockers(prs)
    free = free_people(prs)
    if not found and not free:
        return text.append("nothing: everything is moving or done", style=MUTED)
    room = BAND - 3 - len(free)
    key_w = max([len(b.pr.key) for b in found] + [len(name) for name in free]) + 1
    for blocker in found[:room]:
        here = blocker.pipe
        line = Text()
        line.append("● " if blocker.yours else "○ ", style=ACCENT if blocker.yours else MUTED)
        line.append(f"{blocker.pr.key:<{key_w}}", style=f"bold {INK}" if blocker.yours else INK)
        if blocker.kind == "needs you":
            line.append(f"at prompt · {here.what}", style=INK)
        elif blocker.kind == "no agent":
            line.append(f"no agent · {here.what}", style=INK)
        else:
            word = "stalled" if here.partly else "not started"
            count = f" {here.done}/{here.total}" if here.partly and here.total else ""
            line.append(f"{blocker.who} · {here.what}", style=INK)
            line.append(f", {word}{count}", style=INK2)
        if blocker.behind:
            line.append(f" · +{len(blocker.behind)} queued", style=INK2)
        if selected is not None and (selected is blocker.pr or selected in blocker.behind):
            line.append(" " * max(0, 54 - line.cell_len))
            line.stylize(f"on {RAISED}")
        text.append_text(line).append("\n")
    if len(found) > room:
        text.append(f"  and {len(found) - room} more\n", style=MUTED)
    for name in free:
        text.append("● ", style=ACCENT)
        text.append(f"{name:<{key_w}}", style=f"bold {INK}")
        text.append("has nothing to do\n", style=INK)
    return text


def branch_text(repo: str, pr: PR) -> Text:
    icon, color, label, _ = STATUS[pr.state]
    text = Text()
    text.append(f"{repo}-", style=MUTED)
    text.append(pr.name + "\n", style=f"bold underline {INK}")
    text.append(icon, style=color)
    text.append(f" {label} · {pr.title}\n", style=INK2)

    def kv(key: str, value: Text | str) -> None:
        text.append(f"{key:<8}", style=MUTED)
        text.append_text(value if isinstance(value, Text) else Text(value, style=INK))
        text.append("\n")

    # The four names that should read alike: one line when they do, two when one is off.
    names = Text()
    names.append("✓ ", style=GOOD).append("branch  ", style=INK)
    session = agent(pr) if pr.mine else None
    if session is not None and session.name:
        names.append("≠ ", style=f"bold {CRIT}").append(f"session {session.name}", style=INK)
    elif session is not None:
        names.append("✓ ", style=GOOD).append(f"session -{session.serial}", style=INK)
    kv("names", names)
    if session is not None and session.name:
        kv("", Text(f"expected {repo}-{pr.key}-{session.serial}", style=INK2))
    more = Text()
    if pr.is_pr:
        more.append("✓ ", style=GOOD).append(f"PR #{pr.number}  ", style=INK)
    elif pr.state == "findings":
        more.append("· ", style=MUTED).append(f"review notes on PR #{pr.number}  ", style=INK2)
    else:
        more.append("· ", style=MUTED).append(f"no PR yet, #{pr.number} reserved  ", style=INK2)
    more.append("✓ ", style=GOOD).append(f"Jira {pr.ticket.upper()}", style=INK)
    kv("", more)
    if pr.same_ticket:
        kv("also", Text("! ", style=WARN).append(f"{pr.ticket.upper()} is on {pr.same_ticket} too", style=INK))
    size = sum(p.state != "findings" for p in pr.root().walk())
    kv("anchor", anchor_note(pr) + ("" if size == 1 else f" · level {pr.depth} of {size}"))
    kv("base", pr.parent.name if pr.parent else pr.root().base)
    ci_icon, ci_color = CI[pr.ci]
    kv("checks", Text(f"{ci_icon} ", style=ci_color).append(pr.ci_note or "none yet", style=INK))
    kv("merge", Text("⚠ ", style=CRIT).append(pr.merge, style=INK) if "conflict" in pr.merge else pr.merge)
    kv("Jira", f"{pr.ticket.upper()} · {pr.jira_status()} · {pr.author}")

    text.append("review\n" if pr.history else "review  ", style=MUTED)
    if not pr.history:
        text.append(("nobody has been asked yet" if pr.is_pr else "not a pull request yet") + "\n", style=INK2)
    for event in pr.history[-3:]:
        if event.kind == "push":
            text.append("  pushed  ", style=MUTED)
            text.append(f"{event.commits} commits from {event.who}".ljust(30), style=INK2)
        else:
            icon, color, _ = WORD[event.verdict]
            words = {"approved": "approved", "changes": "changes requested",
                     "requested": "review requested", "rerequested": "re-review requested"}[event.verdict]
            text.append(f"  r{event.round} ", style=MUTED)
            text.append(f"{'you' if event.who == ME else event.who:<7}", style=INK)
            text.append(icon, style=color)
            text.append(f" {words}".ljust(22), style=INK2)
        text.append(event.age.rjust(4) + "\n", style=MUTED)
    open_threads, total = pr.threads
    if total:
        kv("threads", f"{total} · {total - open_threads} resolved · {open_threads} open")
    return text


def chain_text(repo: str, prs: list[PR], pr: PR) -> Text:
    """The selected branch in the pipeline: its state, the chain it waits in, what else waits
    on the same person, and what is queued behind it."""
    text = Text("pipeline\n", style=f"bold {INK}")
    here = pipe(pr)
    if here is None:
        owner = pr.parent
        text.append(f"local review notes for {ref(owner) if owner else 'a branch'}\n", style=INK2)
        session = agent(pr)
        text.append("▶ " if session and session.live else "▷ ", style=INK if session and session.live else MUTED)
        text.append((f"session {session.serial} {session_state(session)}" if session
                     else "its session has ended") + "\n", style=INK)
        if session:
            text.append(f"  {session.note}\n", style=INK2)
        return text
    for part, style in pipeline_segments(pr):
        text.append(part, style=style)
    text.append("\n")
    text.append(f"  {', '.join(here.who)} · {here.what}\n", style=INK)
    text.append(f"  {here.progress}\n", style=INK2 if here.state == "working" or not here.partly else INK)
    session = here.agent
    if session:
        text.append("  ▶ " if session.live else "  ▷ ",
                    style=INK if session.live else (ACCENT if session_state(session) == "at its prompt" else MUTED))
        text.append(f"session {session.serial} {session_state(session)}\n", style=INK)
        text.append(f"    {session.note}\n", style=INK2)
    elif pr.mine or "you" in here.who:
        text.append("  no session of yours is on it\n", style=INK2)

    links = chain(pr)
    if len(links) > 1:                                     # one agent waiting on another
        text.append("wait chain\n", style=MUTED)
        for depth, link in enumerate(links):
            text.append("  " * depth + ("└ " if depth else "  "), style=AXIS)
            text.append(link.key, style=f"bold {INK}")
            last = depth == len(links) - 1
            end = pipe(link) if last else None
            phrase = "" if end is None else {"working": "working", "needs you": "waits on you",
                                             "no agent": "needs an agent"}.get(end.state, f"waits on {end.on}")
            text.append(f"  {phrase}".rstrip() + "\n",
                        style=f"bold {INK}" if end and end.state in ("needs you", "no agent") else INK2)

    end, end_pr = root_cause(pr)
    if end is not None and end.state == "waiting" and end.on_pr is None:
        for name in end.who:                               # what else waits on the same person
            others = [p for p in work_of(prs, name) if p is not end_pr]
            text.append(f"also waiting on {name}\n" if others else f"nothing else waits on {name}\n",
                        style=MUTED)
            for other in others[:3]:
                icon, color, _, _ = STATUS[other.state]
                text.append(f"  {icon} ", style=color)
                text.append(f"{other.key}  ", style=INK)
                text.append(f"{other.next[1]}\n", style=INK2)
    queued = [p for p in flat(prs) if p is not pr and pr in chain(p)]
    if queued:
        text.append("queued behind this branch\n", style=MUTED)
        for other in queued[:3]:
            icon, color, _, _ = STATUS[other.state]
            text.append(f"  {icon} ", style=color)
            text.append(f"{other.key}-{other.slug}\n", style=INK)
    return text


def legend_text(width: int) -> Text:
    canvas = Canvas(max(60, width), BAND - 1)
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

    x = 48
    canvas.put(x, 0, "pipeline", f"bold {INK}")
    for y, (mark, color, words) in enumerate((
            ("▶", INK, "working: an agent of yours is on it right now."),
            ("", "", 'This is the only thing called "in progress".'),
            ("●", ACCENT, "idle, and you can fix it: a session at its"),
            ("", "", "prompt, a move with no agent, a person with"),
            ("", "", "nothing to do. Blue means nothing else."),
            ("○", MUTED, "waits on someone else: a person, or another"),
            ("", "", "branch (often the one below it in the stack)."),
            ("▸", MUTED, "follows a wait along to where it really ends."),
            ("", "", ""),
            ("", "", "stalled      partly done on GitHub, nobody on it"),
            ("", "", "not started  nothing done yet")), start=1):
        canvas.put(x, y, mark, color)
        canvas.put(x + 2, y, words, MUTED)

    x = 100
    canvas.put(x, 0, "other marks", f"bold {INK}")
    for y, (mark, color, words) in enumerate((
            ("▷", MUTED, "a session of yours that is not working"),
            ("≠", CRIT, "a session name breaks the pattern"),
            ("◆", MUTED, "open review threads"),
            ("r2", MUTED, "review round, then each reviewer's word"),
            ("┬┼", MUTED, "where stacks hang off the base's history"),
            ("", "", ""),
            ("", "", "repos at the top:"),
            ("●3", ACCENT, "idle things you can fix"),
            ("○2", MUTED, "people a branch waits on")), start=1):
        canvas.put(x, y, mark, color)
        canvas.put(x + 3, y, words, MUTED)
    return canvas.text()


class TreeTableV2(App):
    CSS = f"""
    Screen {{ background: {PLANE}; color: {INK}; }}
    #tabs, #people, #agents, #cols, #hints {{ height: 1; padding: 0 1;
        text-wrap: nowrap; text-overflow: ellipsis; }}
    #map {{ height: 1fr; padding: 0 1; }}
    #band {{ height: {BAND}; border-top: solid {HAIR}; }}
    #effort {{ width: 58; height: 1fr; padding: 0 1; border-right: solid {HAIR}; }}
    #left {{ width: 1fr; height: 1fr; padding: 0 1; }}
    #right {{ width: 40; height: 1fr; padding: 0 1; border-left: solid {HAIR}; }}
    #legend {{ width: 1fr; height: 1fr; padding: 0 1; display: none; }}
    #effort, #left, #right, #legend {{ text-wrap: nowrap; text-overflow: ellipsis; }}
    #band.legend #effort, #band.legend #left, #band.legend #right {{ display: none; }}
    #band.legend #legend {{ display: block; }}
    #band.narrow #right {{ display: none; }}
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
        self.top = 0                  # first map row on screen
        self.filter = 0

    def compose(self) -> ComposeResult:
        yield Static(id="tabs")
        yield Static(id="people")
        yield Static(id="agents")
        yield Static(id="cols")
        yield Static(id="map")
        with Horizontal(id="band"):
            yield Static(id="effort")
            yield Static(id="left")
            yield Static(id="right")
            yield Static(id="legend")
        yield Static("↑↓ branch    n next thing needing effort    f filter    l legend    "
                     "⇥ next repo    q quit", id="hints")

    def on_mount(self) -> None:
        self.draw()

    def on_resize(self) -> None:
        self.query_one("#band").set_class(self.size.width < 140, "narrow")
        self.draw()

    # ---- state ---------------------------------------------------------------
    def rows(self) -> list[Row]:
        return tree_rows(REPOS[self.repo])

    def branches(self) -> list[PR]:
        return [row.pr for row in self.rows() if row.pr is not None]

    def selected(self) -> PR | None:
        found = self.branches()
        return found[min(self.sel, len(found) - 1)] if found else None

    # ---- drawing -------------------------------------------------------------
    def draw(self) -> None:
        prs = REPOS[self.repo]
        pr = self.selected()
        self.query_one("#tabs", Static).update(self.tabs())
        self.query_one("#people", Static).update(self.people_line(prs))
        self.query_one("#agents", Static).update(self.agents_line(prs))
        self.query_one("#map", Static).update(self.map())            # sets what #cols reports
        self.query_one("#effort", Static).update(effort_text(prs, pr))
        left, right = self.query_one("#left", Static), self.query_one("#right", Static)
        if pr is None:
            left.update(Text("no open pull requests or local branches", style=MUTED))
            right.update("")
        else:
            left.update(branch_text(self.repo, pr))
            right.update(chain_text(self.repo, prs, pr))
        self.query_one("#legend", Static).update(legend_text(self.size.width - 2))

    def tabs(self) -> Text:
        text = Text()
        for name, prs in REPOS.items():
            yours, waited_on, free = repo_status(prs)
            idle = yours + free                                # everything blue: you can fix it
            text.append(name, style=f"bold underline {INK}" if name == self.repo else MUTED)
            if idle or waited_on:
                text.append(" ●", style=ACCENT if idle else AXIS)
                text.append(str(idle), style=INK if idle else AXIS)
                text.append(" ○", style=MUTED if waited_on else AXIS)
                text.append(str(waited_on), style=INK2 if waited_on else AXIS)
            else:
                text.append(" ·", style=AXIS)
            text.append("      ")
        text.append("refreshed 2m ago", style=MUTED)
        if self.filter:
            text.append("      showing ", style=MUTED)
            text.append(f"{FILTERS[self.filter][0]} only", style=f"bold {INK}")
        return text

    def people_line(self, prs: list[PR]) -> Text:
        """Who has work and who has nothing to do. Names, never numbers."""
        text = Text("people   ", style=MUTED)
        listed = roster(prs)
        busy = [name for name, has_work in listed if has_work]
        free = [name for name, has_work in listed if not has_work and name != "you"]
        if not flat(prs):
            return text.append("nobody is on this repo yet", style=MUTED)
        text.append("have work  ", style=MUTED)
        text.append(" · ".join(busy) if busy else "nobody", style=INK2)
        if free:
            text.append("      ● ", style=ACCENT)
            text.append("nothing to do  ", style=MUTED)
            text.append(" · ".join(free), style=f"bold {INK}")
        else:
            text.append("      everyone has work", style=MUTED)
        return text

    def agents_line(self, prs: list[PR]) -> Text:
        """Your agents as marks, one per session, so an idle one shows without counting."""
        states = [session_state(session) for pr in flat(prs) if pr.mine and pr.workers
                  and (session := agent(pr) if pr.state != "findings" else next(
                      (w for w in pr.workers if session_state(w) != "ended"), None)) is not None]
        unassigned = sum(b.kind == "no agent" for b in blockers(prs))
        text = Text("agents   ", style=MUTED)
        if not states and not unassigned:
            return text.append("none on this repo", style=MUTED)
        for state, mark, color, words in (("working", "▶", INK, "working"),
                                          ("at its prompt", "▷", ACCENT, "at a prompt, waiting for you"),
                                          ("waiting", "▷", MUTED, "waiting on someone else")):
            n = states.count(state)
            if n:
                text.append(mark * n, style=color)
                text.append(f" {words}      ", style=INK2 if state != "at its prompt" else INK)
        if unassigned:
            text.append("●" * unassigned, style=ACCENT)
            text.append(" with no agent on a move that is yours", style=INK)
        return text

    def map(self) -> Text:
        rows = self.rows()
        branches = [row.pr for row in rows if row.pr is not None]
        self.sel = max(0, min(len(branches) - 1, self.sel))
        selected = branches[self.sel] if branches else None
        width = max(60, self.size.width - 2)
        height = max(3, self.size.height - 5 - BAND)
        at = next((i for i, row in enumerate(rows) if row.pr is selected), 0) if selected else 0
        self.top = max(0, min(self.top, at))                   # keep the selection on screen
        if at >= self.top + height:
            self.top = at - height + 1
        if at <= 2:
            self.top = 0                                       # and its anchor, when it is near the top
        shown = rows[self.top:self.top + height]

        head = Text(style=MUTED)
        scrolled = f"   rows {self.top + 1}–{self.top + len(shown)} of {len(rows)}" if len(rows) > height else ""
        head.append("  branch")
        head.append(scrolled.ljust(X_REVIEW - 8), style=INK2)
        for label, x, nxt in (("review", X_REVIEW, X_CHECKS), ("ci", X_CHECKS, X_PIPE),
                              ("pipeline", X_PIPE, X_PROGRESS), ("progress", X_PROGRESS, X_AGENT),
                              ("agent", X_AGENT, X_QUIET)):
            head.append(label.ljust(nxt - x))
        head.append("quiet")
        self.query_one("#cols", Static).update(head)

        canvas = Canvas(width, max(1, len(shown)))
        if not rows:
            canvas.put(0, 0, "no open pull requests or local branches", MUTED)
        keep = FILTERS[self.filter][1]
        for y, row in enumerate(shown):
            if row.kind == "anchor":
                canvas.put(0, y, row.label, f"bold {INK}")
                canvas.put(len(row.label) + 2, y, "base branch, newest commit first", MUTED)
            elif row.kind == "point":
                canvas.put(0, y, row.lead, MUTED)
                canvas.put(len(row.lead), y, row.label, MUTED)
            else:
                self.draw_branch(canvas, y, row, not keep(row.pr))
                if row.pr is selected:
                    for x in range(width):
                        canvas.paint(x, y, 1, RAISED)
        return canvas.text()

    def draw_branch(self, canvas: Canvas, y: int, row: Row, dim: bool) -> None:
        pr = row.pr
        icon, color, _, _ = STATUS[pr.state]
        canvas.put(0, y, row.lead, AXIS)
        x = len(row.lead)
        canvas.put(x, y, icon, DIM if dim else color)
        write(canvas, x + 2, y, [(pr.key, f"bold {INK}"), ("-" + pr.slug, INK2)], X_REVIEW - x - 3, dim)
        write(canvas, X_REVIEW, y, review_segments(pr), X_CHECKS - X_REVIEW - 1, dim)
        ci_icon, ci_color = CI[pr.ci]
        canvas.put(X_CHECKS, y, ci_icon, DIM if dim else ci_color)
        write(canvas, X_PIPE, y, pipeline_segments(pr), X_PROGRESS - X_PIPE - 1, dim)
        word, count = progress_segments(pr)
        write(canvas, X_PROGRESS, y, word, X_COUNT - X_PROGRESS - 1, dim)
        write(canvas, X_COUNT, y, count, X_AGENT - X_COUNT - 1, dim)
        write(canvas, X_AGENT, y, agent_segments(pr), X_QUIET - X_AGENT - 1, dim)
        canvas.put(X_QUIET, y, pr.age.rjust(4),
                   DIM if dim else (f"bold {INK}" if is_stale(pr.age) else MUTED))

    # ---- keys ----------------------------------------------------------------
    def action_move(self, step: int) -> None:
        self.sel += step
        self.draw()

    def action_next_effort(self) -> None:
        """Jump to the next branch where a wait ends: yours first, then people to nudge."""
        branches = self.branches()
        stops = [branches.index(b.pr) for b in blockers(REPOS[self.repo]) if b.pr in branches]
        if not stops:
            return
        here = self.sel if self.sel in stops else None
        self.sel = stops[(stops.index(here) + 1) % len(stops)] if here is not None else stops[0]
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
    TreeTableV2().run()
