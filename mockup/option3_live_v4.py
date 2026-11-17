"""Agent Frolic mock-up, option 3, v4: live, and who comes first. If it moves, work is
moving. If it is blue, it is still, and it is yours to get going.

WHO, at the front of every row. The first two cells of a branch are its owner (your agent on
it, "no agent", or the person whose branch it is) and what it waits on (nothing while the
agent works, otherwise the chain read left to right to where it ends: "pr-0024 → mira").
Then comes the map itself, unchanged in shape: one connected tree of commit, branch and
stack, every base at once, joined by short hashes.

WHO, at the top of the screen. Under the repos sits the cast strip: a row of small cards.
One per agent of yours (named with its branch, because a serial is only unique together
with it), one per branch whose move is yours and nobody is on it, one per other person.
The cards stand in four groups: needs you, at work, waiting on someone, have work. When
they do not all fit, the quiet groups fold to one line per member (the heading says they
are folded, and how many where there is room); the ones that need you always keep their
full card, and agents at work keep theirs until nothing else can give. Left and right pick a
card: the rows that member owns and the rows that wait on them stay lit, in place, and the
rest of the map is set aside. Escape clears.

Only one thing moves all the time: the dots that turn where an agent is at work (on its
row, on its card, at the end of a wait that is moving, in the top line). The tree is drawn
once and stays put. Blue holds still.

Everything else moves only when a made-up event lands, and then settles:
- the row it touched washes pale for a second (the cells, never the tree);
- any row whose owner or waits-on cell changed because of it washes those two cells, and
  a card whose member changed state washes too, and may move to another group;
- a progress meter steps to its new count;
- the needs-effort list re-orders itself. It is kept most urgent first (is an agent waiting
  for this to continue, then how long has it been quiet). A thing that gets going again
  says so and leaves the list.

    .venv/bin/python mockup/option3_live_v4.py

Keys: up/down pick a branch, left/right pick a card in the cast, escape clears the card,
n jumps to the next thing needing effort (most urgent first), e fires the next event now,
r sweeps a refresh, d folds the drawer, space pauses all motion, tab goes to the next repo
(shift+tab back), q quits. Every name and number is made up.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.css.query import NoMatches
from textual.widgets import Static

from frolic_v4 import (ACCENT, AXIS, CRIT, GOOD, INK, INK2, MUTED, PLANE, PR, RAISED, REPOS, SPIN,
                       STATUS, SURFACE, Canvas, Effort, Member, Segments, Worker, anchor_note,
                       broken, cast, chain, flat, link, needs_effort, owned_by, owner,
                       owner_segments, pipe, push, root_cause, rv, sha, short,
                       top_line, tree_rows, waits, waits_segments, write)

FPS = 12
EVERY = 4.0                      # seconds between events
FLASH = ["#4a4a46", "#444441", "#3d3d3a", "#363633", "#2f2f2d", "#292927"]   # a wash, fading
DIM = "#5c5b56"                  # rows set aside while you look at one member of the cast
X_OWNER, X_WAITS, X_TREE = 2, 16, 38
LANDING = 14                     # frames a merged branch stays on screen before it goes
GLOW = 16                        # frames a stepped meter stays bright
LEAVE = 16                       # frames a resolved item stays in the list, saying why
SLIDE = 0.17                     # rows a list item moves per frame on its way to its place
BAND = 11                        # rows of the bottom band: the list and the drawer
STRIP = 4                        # rows of the cast strip: the group headings, then three lines
X_CAST = 2                       # where the first card starts: under the owner column's marks
Key = tuple[str, object]         # a member of the cast: ("branch", id) or ("person", name)
FOLDED = {"B": "{n} at work, folded", "C": "{n} waiting, folded"}     # headings of folded groups


def cut(text: str, width: int) -> str:
    return text if len(text) <= width else text[:max(0, width - 1)] + "…"


def words(text: str, width: int) -> str:
    """As many whole words as fit in `width`, then … when some are left out."""
    if len(text) <= width:
        return text
    out = ""
    for word in text.split():
        if len(out) + len(word) + (1 if out else 0) + 2 > width:
            break
        out += (" " if out else "") + word
    return out + " …" if out else cut(text, width)


def seg_len(segments: Segments) -> int:
    return sum(len(text) for text, _ in segments)


def plain(segments: Segments) -> str:
    return "".join(text for text, _ in segments)


def wash(canvas: Canvas, y: int, x0: int, x1: int, color: str) -> None:
    """A background behind part of a row, replacing any background already there."""
    if not 0 <= y < canvas.height:
        return
    for x in range(max(0, x0), min(canvas.width, x1)):
        ch, style = canvas.cells[y][x]
        canvas.cells[y][x] = (ch, f"{style.split(' on ')[0]} on {color}".strip())


def columns(width: int) -> tuple[int, int, int]:
    """Where the next move, the progress and the quiet time start, for a map this wide. The
    owner, waits-on and tree columns never move."""
    x_age = width - 5
    x_prog = x_age - 23
    tree = max(30, min(46, round((x_prog - X_TREE) * 0.56)))
    return X_TREE + tree, x_prog, x_age


# ---- the cast: who gets a card, and what it says ---------------------------------------------

def members(prs: list[PR]) -> list[Member]:
    """Everyone with a card: each agent of yours, each branch whose move is yours and nobody
    is on it (someone else's branch too, when the move there is yours), each other person.
    You get no card: everything blue on the screen is already yours."""
    found = [m for m in cast(prs) if m.kind != "you"]
    carded = {id(m.pr) for m in found if m.pr is not None}
    people = [m for m in found if m.kind == "person"]
    rest = [m for m in found if m.kind != "person"]
    for pr in flat(prs):
        here = pipe(pr)
        if here is not None and here.state == "no agent" and not pr.mine and id(pr) not in carded:
            rest.append(Member("no agent", "no agent", "", pr, True, [pr]))
    return rest + people


def key_of(m: Member) -> Key:
    return ("person", m.name) if m.kind == "person" else ("branch", id(m.pr))


def group_of(m: Member) -> str:
    """ "A" needs you · "B" at work · "C" waiting on someone · "D" has work """
    if m.kind == "agent":
        return {"working": "B", "at its prompt": "A", "waiting": "C"}[m.state]
    if m.kind == "no agent":
        return "A"
    return "A" if m.state == "nothing to do" else "D"


def waits_first(pr: PR) -> str:
    """The first thing a branch waits on, for a card: the branch below, or the person."""
    wait = waits(pr)
    if wait is None or wait.kind == "working":
        return ""
    return short(wait.links[0]) if wait.links else wait.end


def waited_names(m: Member) -> list[str]:
    seen: list[str] = []
    for pr in m.waited_by:
        if short(pr) not in seen:
            seen.append(short(pr))
    return seen


def branches_fit(names: list[str], room: int) -> tuple[str, int]:
    """As many branch names as fit in `room`, then … when some are left out."""
    out, shown = "", 0
    for i, name in enumerate(names):
        add = (" " if out else "") + name
        if len(out) + len(add) + (2 if i < len(names) - 1 else 0) > room:
            break
        out += add
        shown += 1
    if shown < len(names):
        out += " …" if out else "…"
    return out, shown


def full_card(m: Member, spin: str) -> list[Segments]:
    """A card of three lines: who, where, and what state they are in."""
    if m.kind == "agent":
        where = short(m.pr)
        if m.state == "working":
            doing = "reviewing" if m.pr.state == "findings" else "working"
            return [[(spin + " ", INK), (m.name, INK)], [("  " + where, INK2)], [("  " + doing, MUTED)]]
        if m.state == "at its prompt":
            return [[("▷ ", ACCENT), (m.name, f"bold {INK}")], [("  " + where, INK)],
                    [("  at prompt", INK2)]]
        return [[("· ", MUTED), (m.name, INK2)], [("  " + where, INK2)],
                [("  on " + cut(waits_first(m.pr), 9), MUTED)]]
    if m.kind == "no agent":
        here = pipe(m.pr)
        return [[("● ", ACCENT), (m.name, f"bold {INK}")], [("  " + short(m.pr), INK)],
                [("  " + words(here.what if here else "", 11), INK2)]]
    if m.state == "nothing to do":
        return [[("● ", ACCENT), (m.name, f"bold {INK}")], [("  nothing", INK2)], [("  to do", INK2)]]
    names = waited_names(m)
    more = " …" if len(names) > 4 else ""
    return [[("  " + m.name, INK2)],
            [("  ← ", MUTED), (" ".join(names[:2]), MUTED)],
            [("    " + " ".join(names[2:4]) + more, MUTED)] if len(names) > 2 else []]


def agent_line(m: Member, spin: str, brief: bool) -> Segments:
    """An agent on one line, for a folded group: "· agent 01 · pr-0015", or "· 01 · pr-0015"."""
    said = f"{m.name.split()[-1] if brief else m.name} · {short(m.pr)}"
    if m.state == "working":
        return [(spin + " ", INK), (said, INK)]
    if m.state == "at its prompt":
        return [("▷ ", ACCENT), (said, f"bold {INK}")]
    return [("· ", MUTED), (said, INK2)]


def person_line(m: Member, room: int) -> tuple[Segments, int]:
    """A person with work on one line: "mira ← pr-0015 pr-0024 …". Returns how many of the
    branches that wait on them it names."""
    names = waited_names(m)
    said, shown = branches_fit(names, room - len(m.name) - 3)
    if not shown and room < len(m.name) + 4:
        return [(m.name, INK2)], 0
    return [(m.name, INK2), (" ← ", MUTED), (said, MUTED)], shown      # "kenji ← …" at the least


@dataclass
class Block:
    """One card, or one column of a folded group (a member per line)."""
    group: str
    lines: list[Segments]
    keys: list[Key]              # the member each line belongs to
    width: int
    x: int = 0


def card_blocks(group: str, found: list[Member], spin: str) -> list[Block]:
    out = []
    for m in found:
        lines = full_card(m, spin)
        lines += [[] for _ in range(3 - len(lines))]
        out.append(Block(group, lines, [key_of(m)] * 3, max(seg_len(line) for line in lines)))
    return out


def folded_blocks(group: str, found: list[Member], lines: list[Segments], roomy: bool = True) -> list[Block]:
    """Three members to a column, one per line. With `roomy`, the first column is at least
    wide enough for the heading that says how many are folded."""
    out = []
    for at in range(0, len(found), 3):
        part = lines[at:at + 3]
        out.append(Block(group, part, [key_of(m) for m in found[at:at + 3]],
                         max(seg_len(line) for line in part)))
    if out and roomy and group in FOLDED:
        out[0].width = max(out[0].width, len(FOLDED[group].format(n=len(found))) - 1)
    return out


def span(blocks: list[Block]) -> int:
    """The cells a run of blocks takes: two between cards, one more between groups."""
    if not blocks:
        return 0
    groups = len({block.group for block in blocks})
    return sum(block.width for block in blocks) + 2 * (len(blocks) - 1) + (groups - 1)


def people_blocks(found: list[Member], form: str, room: int, spin: str) -> tuple[list[Block] | None, int]:
    """The people who have work, as cards ("full"), one line each with what waits on them
    cut to the room there is ("fit"), or names alone ("names"). None when it cannot fit."""
    if not found:
        return [], 0
    if form == "full":
        blocks = card_blocks("D", found, spin)
        return (blocks, 99) if span(blocks) <= room else (None, 0)
    if form == "names":
        blocks = folded_blocks("D", found, [[(m.name, INK2)] for m in found])
        return (blocks, 0) if span(blocks) <= room else (None, 0)
    # three people to a column; a column that needs little takes little, the rest share what is left
    stacks = [found[at:at + 3] for at in range(0, len(found), 3)]
    wants = [max(len(m.name) + 3 + len(" ".join(waited_names(m))) for m in stack) for stack in stacks]
    caps, left = [0] * len(stacks), room - 2 * (len(stacks) - 1)
    for k, i in enumerate(sorted(range(len(stacks)), key=lambda i: wants[i])):
        caps[i] = min(wants[i], left // (len(stacks) - k))
        left -= caps[i]
    lines, shown, named = [], 0, 0
    for stack, cap in zip(stacks, caps):
        for m in stack:
            if cap < len(m.name):
                return None, 0
            line, n = person_line(m, cap)
            lines.append(line)
            shown += n
            named += n > 0
    if named * 2 < len(found):                                 # too few say what waits: names will do
        return None, 0
    return folded_blocks("D", found, lines), shown


def lay_out(found: list[Member], room: int, rank: dict[int, int], spin: str) -> tuple[list[Block], dict[str, str]]:
    """Choose how each group is shown so the strip fits `room`. The ones that need you keep
    full cards. Agents at work keep theirs as long as anything else can give; then what
    waits on people is kept in preference to full cards for agents that wait."""
    by_group: dict[str, list[Member]] = {"A": [], "B": [], "C": [], "D": []}
    for m in found:
        by_group[group_of(m)].append(m)

    def urgency(m: Member) -> tuple[int, int]:
        if m.kind == "person":
            return (2, 0)
        target = m.pr.parent if m.pr.state == "findings" and m.pr.parent is not None else m.pr
        return (0 if m.kind == "agent" else 1, rank.get(id(target), 99))
    by_group["A"].sort(key=urgency)

    def agents(group: str, form: str) -> list[Block]:
        here = by_group[group]
        if form == "full":
            return card_blocks(group, here, spin)
        return folded_blocks(group, here, [agent_line(m, spin, form != "long") for m in here],
                             roomy=form != "tight")

    head_a = card_blocks("A", by_group["A"], spin)
    forms = (("full", 3), ("long", 2), ("brief", 1), ("tight", 0))
    best: tuple[tuple[int, int, int, int], list[Block], dict[str, str]] | None = None
    for b_form, b_rank in forms:
        for c_form, c_rank in forms:
            if c_rank > b_rank:                                # the quiet fold before the busy do
                continue
            head = head_a + agents("B", b_form) + agents("C", c_form)
            left = room - span(head) - (3 if head and by_group["D"] else 0)
            for d_form, d_rank in (("full", 2), ("fit", 1), ("names", 0)):
                tail, shown = people_blocks(by_group["D"], d_form, left, spin)
                if tail is None or span(head) > room:
                    continue
                score = (b_rank, d_rank, shown, c_rank)
                if best is None or score > best[0]:
                    best = (score, head + tail, {"B": b_form, "C": c_form, "D": d_form})
                break
    if best is None:                                           # too narrow for anything: clip
        tail, _ = people_blocks(by_group["D"], "names", 10 ** 6, spin)
        blocks = head_a + agents("B", "tight") + agents("C", "tight") + (tail or [])
        chosen = {"B": "tight", "C": "tight", "D": "names"}
    else:
        _, blocks, chosen = best
    x, group = X_CAST, None
    for block in blocks:
        if group is not None:
            x += 2 + (block.group != group)
        block.x, group = x, block.group
        x += block.width
    return blocks, chosen


def heading(group: str, form: str, n: int, room: int) -> Segments:
    """The words over a group of cards; a folded group says how many are folded."""
    if group == "A":
        return [("● ", ACCENT), ("needs you", INK2)] if room >= 11 else [("●", ACCENT)]
    if group == "B":
        said = ["at work"] if form == "full" else [FOLDED["B"].format(n=n), "at work, folded",
                                                    f"{n} folded"]
    elif group == "C":
        said = (["waiting on someone", "waiting"] if form == "full" else
                [f"waiting on someone, {n} folded", FOLDED["C"].format(n=n), "waiting, folded",
                 f"{n} folded"])
    elif form == "names":
        said = ["have work, folded", "have work", "work"]
    else:
        said = ["have work  ← what waits on them", "have work  ← waits on them", "have work"]
    return [(next((s for s in said if len(s) <= room), cut(said[-1], room)), MUTED)]


# ---- the made-up events: each changes the data and says what happened --------------------

def ev_prompt(app: "LiveV4") -> tuple[list[PR], str]:
    pr = app.find(13)
    worker = pr.workers[0]
    worker.live, worker.mode, worker.note = False, "prompt", "waiting at its prompt"
    app.step_meter(pr, (2, 3))
    pr.next = ("you", "decide on the last thread")
    return [pr], "agent 01 on pr-0013 replied to 2 of 3 threads, then stopped at its prompt · it waits on you"


def ev_resume(app: "LiveV4") -> tuple[list[PR], str]:
    pr = app.find(13)
    worker = pr.workers[0]
    worker.live, worker.mode, worker.note = True, "", "replying to the last thread"
    pr.next = ("you", "reply to the last thread")
    return [pr], "you answered · agent 01 on pr-0013 is working again"


def ev_assign(app: "LiveV4") -> tuple[list[PR], str]:
    pr = app.find(20)
    pr.workers.append(Worker("02", True, "reading keychain.py"))
    return [pr], "you put an agent on pr-0020 · agent 02 owns it now and is at work"


def ev_reviewer(app: "LiveV4") -> tuple[list[PR], str]:
    pr = app.find(15)
    app.step_meter(pr, (4, 6))
    return [pr], "mira read two more files on pr-0015 · 4 of 6, and the rest still waits on mira"


def ev_notes(app: "LiveV4") -> tuple[list[PR], str]:
    pr = app.find(9)
    notes = next(child for child in pr.children if child.state == "findings")
    worker = notes.workers[0]
    worker.live, worker.mode, worker.note = False, "prompt", "review notes written · at its prompt"
    pr.next, pr.started, pr.progress, pr.unit = ("you", "post the review"), True, (0, 0), ""
    return [pr, notes], ("agent 01 finished its review notes for pr-0009 and stopped at its prompt · "
                         "the review is yours to post")


def ev_ask(app: "LiveV4") -> tuple[list[PR], str]:
    pr = app.find(24)
    pr.next = ("mira, kenji", "review")
    pr.history.append(rv(1, "kenji", "requested", "now"))
    return [pr], "kenji was asked to review pr-0024 · kenji now has work"


def ev_reviewed(app: "LiveV4") -> tuple[list[PR], str]:
    pr = app.find(15)
    pr.state, pr.next = "changes", ("you", "answer 2 threads")
    pr.history.append(rv(2, "mira", "changes", "now", 2))
    pr.threads, pr.progress, pr.unit, pr.started = (2, 7), (0, 2), "threads", False
    app.stepped.pop(id(pr), None)
    worker = pr.workers[0]
    worker.live, worker.mode, worker.note = True, "", "reading the 2 new threads"
    return [pr], "mira's second look at pr-0015 landed: changes on 2 threads · agent 01 stops waiting and starts"


def ev_release(app: "LiveV4") -> tuple[list[PR], str]:
    below, above = app.find(17), app.find(21)
    below.state, below.next, below.started = "review", ("mira", "review"), False
    below.history.append(rv(1, "mira", "requested", "now"))
    for worker in below.workers:
        if worker.live:
            worker.live, worker.mode, worker.note = False, "waiting", "idle until mira reviews"
    above.waits_on, above.next = 0, ("you", "open the PR")
    worker = above.workers[0]
    worker.live, worker.mode, worker.note = True, "", "opening the PR"
    return [below, above], ("pr-0017 is up for review and its agent 02 waits on mira · "
                            "agent 01 on pr-0021 stops waiting and starts")


def ev_answered(app: "LiveV4") -> tuple[list[PR], str]:
    pr = app.find(13)
    worker = pr.workers[0]
    worker.live, worker.mode, worker.note = False, "waiting", "idle until mira looks again"
    pr.state, pr.next = "rereview", ("mira", "re-review")
    pr.progress, pr.unit, pr.started, pr.threads = (0, 0), "", False, (0, 7)
    pr.history += [push("agent 01", 2, "now"), rv(2, "mira", "rerequested", "now")]
    return [pr], "agent 01 answered the last thread on pr-0013 · it now waits on mira to look again"


def ev_approved(app: "LiveV4") -> tuple[list[PR], str]:
    pr = app.find(13)
    pr.state, pr.next, pr.waits_on = "approved", ("you", "merge after pr-0012"), 12
    pr.history.append(rv(2, "mira", "approved", "now"))
    pr.workers[0].note = "idle until pr-0012 lands"
    return [pr], "mira approved pr-0013 · it cannot merge before pr-0012, so it now waits on that, and that on you"


def ev_land(app: "LiveV4") -> tuple[list[PR], str]:
    pr, above = app.find(12), app.find(13)
    app.leaving[id(pr)] = app.frame
    pr.next = ("", "")                            # merged: it holds no move any more
    above.state, above.next, above.waits_on = "ready", ("you", "merge"), 0
    worker = above.workers[0]
    worker.live, worker.mode, worker.note = True, "", "merging"
    return [pr, above], "you merged pr-0012 · pr-0013 hangs from main now, and agent 01 is merging it"


SCRIPT = [ev_prompt, ev_resume, ev_assign, ev_reviewer, ev_notes, ev_ask, ev_reviewed, ev_release,
          ev_answered, ev_approved, ev_land]


class LiveV4(App):
    CSS = f"""
    Screen {{ background: {PLANE}; color: {INK}; }}
    #tabs {{ height: 1; margin: 0 3; text-wrap: nowrap; text-overflow: ellipsis; }}
    #strip {{ height: {STRIP}; margin: 0 1; }}
    #map {{ height: 1fr; margin: 1 1 0 1; }}
    #band {{ height: {BAND}; margin: 1 1 0 1; }}
    .pane {{ height: {BAND}; background: {SURFACE}; padding: 1 2; margin: 0 1;
             text-wrap: nowrap; text-overflow: ellipsis; overflow: hidden; }}
    #effort {{ width: 1fr; }}
    #drawer {{ width: 64; }}
    #events {{ height: 2; margin: 0 3; text-wrap: nowrap; text-overflow: ellipsis; }}
    #hints {{ height: 1; margin: 0 3; color: {MUTED}; text-wrap: nowrap; text-overflow: ellipsis; }}
    """
    BINDINGS = [
        Binding("up", "move(-1)", show=False), Binding("down", "move(1)", show=False),
        Binding("left", "card(-1)", show=False), Binding("right", "card(1)", show=False),
        Binding("escape", "clear", show=False), Binding("n", "next_effort", show=False),
        Binding("e", "fire", show=False), Binding("r", "sweep", show=False),
        Binding("d", "drawer", show=False), Binding("space", "pause", show=False),
        Binding("tab", "repo(1)", show=False, priority=True),
        Binding("shift+tab", "repo(-1)", show=False, priority=True),
        Binding("q", "quit", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.frame = 0
        self.paused = False
        self.drawer_open = True
        self.drawer_w = 64
        self.blocks: list[Block] = []
        self.load(next(iter(REPOS)))

    def compose(self) -> ComposeResult:
        yield Static(id="tabs")
        yield Static(id="strip")
        yield Static(id="map")
        with Horizontal(id="band"):
            yield Static(id="effort", classes="pane")
            yield Static(id="drawer", classes="pane")
        yield Static(id="events")
        yield Static("↑↓ branch    ←→ cast    esc clear    n next needing effort    e next event    "
                     "r refresh    d drawer    space pause    ⇥ repo    q quit", id="hints")

    def on_mount(self) -> None:
        self.fit()
        self.set_interval(1 / FPS, self.tick)
        self.draw()

    def on_resize(self) -> None:
        self.fit()
        self.draw()

    def fit(self) -> None:
        """The drawer gives way to the list in a narrow window."""
        self.drawer_w = 64 if self.size.width >= 140 else max(30, self.size.width * 2 // 5)
        if self.drawer_open:
            try:
                self.query_one("#drawer", Static).styles.width = self.drawer_w
            except NoMatches:                     # resized while the app is quitting
                return

    # ---- state -------------------------------------------------------------
    def load(self, repo: str) -> None:
        """A fresh copy of one repo's data, so events can change it and a replay can reset it."""
        self.repo = repo
        self.prs = link(copy.deepcopy(REPOS[repo]))
        branches = self.branches()
        self.sel: PR | None = branches[0] if branches else None
        self.pick: Key | None = None              # the card picked in the cast strip
        self.flash: dict[int, int] = {}           # branch -> frame an event hit it
        self.cell_flash: dict[int, int] = {}      # branch -> frame its owner or waits-on changed
        self.card_flash: dict[Key, int] = {}      # member -> frame its card changed
        self.cell_sig: dict[int, tuple[str, str]] = {}
        self.card_sig: dict[Key, tuple] = {}
        self.touched: set[int] = set()            # branches an event has changed: quiet "now"
        self.stepped: dict[int, tuple[int, int]] = {}     # branch -> (frame, count before the step)
        self.leaving: dict[int, int] = {}         # branch -> frame it was merged
        self.events: list[tuple[int, str]] = []
        self.step = 0
        self.next_at = self.frame + int(EVERY * FPS)
        self.sweep_at: int | None = None
        self.refreshed_at = self.frame
        # the needs-effort list: where each item is drawn, what it said, what has just left
        self.efforts: dict[int, Effort] = {}
        self.order: list[int] = []
        self.slots: dict[int, float] = {}
        self.said: dict[int, str] = {}
        self.gone: dict[int, tuple[int, int, str]] = {}   # item -> (frame, row it held, why)
        self.entered: dict[int, int] = {}
        self.sync_effort(settle=True)
        self.sync_who(settle=True)

    def find(self, number: int) -> PR:
        return next(pr for pr in flat(self.prs) if pr.number == number and pr.state != "findings")

    def branches(self) -> list[PR]:
        return [row.pr for row in tree_rows(self.repo, self.prs) if row.pr is not None]

    def behind(self, pr: PR) -> list[PR]:
        """The branches that wait on this one, directly or through a chain."""
        return [other for other in flat(self.prs) if other is not pr and pr in chain(other)]

    def step_meter(self, pr: PR, progress: tuple[int, int]) -> None:
        """Move a count on, remembering where it was so the meter can be seen to step."""
        self.stepped[id(pr)] = (self.frame, pr.progress[0])
        pr.progress = progress

    def cast_now(self) -> list[Member]:
        return members(self.prs)

    def member(self, key: Key | None) -> Member | None:
        return next((m for m in self.cast_now() if key_of(m) == key), None) if key else None

    def lit(self) -> set[int] | None:
        """The rows a picked member owns, and the rows that wait on them; None with no pick."""
        picked = self.member(self.pick)
        if picked is None:
            return None
        return {id(pr) for pr in owned_by(self.prs, picked)}

    def sync_who(self, settle: bool = False) -> None:
        """Notice every owner cell, waits-on cell and card that changed since the last look,
        so it can wash for a moment. With settle, nothing washes (a fresh load)."""
        cells: dict[int, tuple[str, str]] = {}
        for pr in flat(self.prs):
            landed = id(pr) in self.leaving
            cells[id(pr)] = ("landed" if landed else plain(owner_segments(pr, "")),
                             "" if landed else plain(waits_segments(pr, "")))
        cards: dict[Key, tuple] = {}
        for m in self.cast_now():
            cards[key_of(m)] = (m.kind, m.state, waits_first(m.pr) if m.pr is not None else "",
                                tuple(waited_names(m)))
        if not settle:
            for key, sig in cells.items():
                if key in self.cell_sig and self.cell_sig[key] != sig:
                    self.cell_flash[key] = self.frame
            for card, sig in cards.items():
                if self.card_sig.get(card) != sig:
                    self.card_flash[card] = self.frame
        self.cell_sig, self.card_sig = cells, cards
        if self.pick is not None and self.pick not in cards:   # the picked member is gone
            self.pick = None

    def sync_effort(self, settle: bool = False) -> None:
        """Bring the list up to date with the data and move each item one step toward its
        place. With settle, everything jumps to its place (a fresh load)."""
        found = needs_effort(self.prs)
        keys = [id(e.pr) for e in found]
        before = set(self.efforts)
        self.efforts = dict(zip(keys, found))
        for key in before - set(keys):                         # it stopped holding things up
            if key in self.gone:
                continue
            pr = next((p for p in flat(self.prs) if id(p) == key), None)
            here = pipe(pr) if pr is not None else None
            why = ("landed" if pr is None or key in self.leaving or here is None
                   else "moving again" if here.state == "working"
                   else f"now waits on {short(here.on_pr)}" if here.on_pr is not None
                   else "no longer holds anything up")
            self.gone[key] = (self.frame, round(self.slots.get(key, 0)), why)
        for key in keys:
            self.gone.pop(key, None)
            self.said[key] = short(self.efforts[key].pr)
            if key not in before and not settle:
                self.entered[key] = self.frame
        for key, (at, _, _) in list(self.gone.items()):
            if self.frame - at > LEAVE or settle:
                del self.gone[key]
                self.slots.pop(key, None)
        order = list(keys)
        for key, (_, row, _) in sorted(self.gone.items(), key=lambda item: item[1][1]):
            order.insert(min(row, len(order)), key)            # it keeps its row until it leaves
        self.order = order
        for i, key in enumerate(order):
            at = self.slots.get(key, float(i))
            self.slots[key] = float(i) if settle else at + max(-SLIDE, min(SLIDE, i - at))

    # ---- the clock ---------------------------------------------------------
    def tick(self) -> None:
        if self.paused:
            return
        self.frame += 1
        if self.repo == "agent-frolic" and self.frame >= self.next_at:
            self.fire()
        for pr in flat(self.prs):                 # a merged branch leaves once it has been seen
            if self.frame - self.leaving.get(id(pr), self.frame) > LANDING:
                self.remove(pr)
        self.sync_effort()
        self.sync_who()
        self.draw()

    def remove(self, pr: PR) -> None:
        """Take a merged branch out of the map; what sat on it now hangs where it did."""
        for kid in pr.children:
            kid.base, kid.behind = pr.base, pr.behind
        siblings = self.prs if pr.parent is None else pr.parent.children
        at = next(i for i, other in enumerate(siblings) if other is pr)
        siblings[at:at + 1] = pr.children
        link(self.prs)
        self.leaving.pop(id(pr), None)
        if self.sel is pr:
            branches = self.branches()
            self.sel = branches[0] if branches else None

    def action_fire(self) -> None:
        """The e key: the next event lands now, paused or not."""
        self.fire()
        self.sync_effort()
        self.sync_who()
        self.draw()

    def fire(self) -> None:
        if self.repo != "agent-frolic":
            return
        self.next_at = self.frame + int(EVERY * FPS)
        if self.step >= len(SCRIPT):
            sel_number = self.sel.number if self.sel else None
            sel_notes = self.sel is not None and self.sel.state == "findings"
            self.load(self.repo)
            if sel_number is not None:
                self.sel = next((pr for pr in self.branches() if pr.number == sel_number
                                 and (pr.state == "findings") == sel_notes), self.sel)
            self.events.append((self.frame, "replaying the made-up events from the start"))
            return
        hit, said = SCRIPT[self.step](self)
        self.step += 1
        for pr in hit:
            pr.age = "now"
            self.flash[id(pr)] = self.frame
            self.touched.add(id(pr))
        self.events.append((self.frame, said))

    # ---- drawing -----------------------------------------------------------
    def draw(self) -> None:
        seconds = (self.frame - self.refreshed_at) // FPS
        try:
            self.query_one("#tabs", Static).update(top_line(
                self.repo, data={self.repo: self.prs}, frame=self.frame, paused=self.paused,
                refreshed=f"{seconds}s ago" if seconds else "just now"))
            self.query_one("#strip", Static).update(self.strip())
            self.query_one("#map", Static).update(self.map())
            self.query_one("#effort", Static).update(self.effort())
            self.query_one("#drawer", Static).update(self.drawer())
            self.query_one("#events", Static).update(self.log_text())
        except NoMatches:                         # a last tick while the app is quitting
            return

    def spin(self, offset: int = 0) -> str:
        """The turning dots: an agent is at work."""
        return SPIN[(self.frame + offset) % len(SPIN)]

    def strip(self) -> Text:
        """The cast: a card for each agent, each missing agent and each person, in groups."""
        box = self.query_one("#strip", Static).size
        width = max(40, box.width or self.size.width - 2)
        canvas = Canvas(width, STRIP)
        found = self.cast_now()
        if not found:
            canvas.put(X_CAST, 0, "the cast", MUTED)
            canvas.put(X_CAST, 1, "nobody yet: no agents of yours and no other people on this repo", MUTED)
            self.blocks = []
            return canvas.text()
        rank = {id(e.pr): i for i, e in enumerate(needs_effort(self.prs))}
        self.blocks, forms = lay_out(found, width - X_CAST, rank, self.spin())
        starts: list[Block] = []
        for block in self.blocks:
            if not starts or starts[-1].group != block.group:
                starts.append(block)
        for i, first in enumerate(starts):                     # the heading over each group
            end = starts[i + 1].x - 2 if i + 1 < len(starts) else width
            n = sum(len(block.lines) for block in self.blocks if block.group == first.group)
            write(canvas, first.x, 0, heading(first.group, forms.get(first.group, "full"), n,
                                              end - first.x), max(0, end - first.x))
        for k, block in enumerate(self.blocks):
            for i, (line, key) in enumerate(zip(block.lines, block.keys)):
                turned = [(self.spin(k * 3 + i * 2) + " " if len(text) == 2 and text[0] in SPIN else text,
                           style) for text, style in line]         # each agent's dots turn on their own
                write(canvas, block.x, 1 + i, turned, max(0, width - block.x))
                since = self.frame - self.card_flash.get(key, -999)
                if key == self.pick:
                    wash(canvas, 1 + i, block.x - 1, block.x + block.width + 1, RAISED)
                elif 0 <= since // 2 < len(FLASH):             # this member just changed: it settles
                    wash(canvas, 1 + i, block.x - 1, block.x + block.width + 1, FLASH[since // 2])
        if self.blocks and self.blocks[-1].x + self.blocks[-1].width > width:
            canvas.put(width - 1, 2, "…", MUTED)
        return canvas.text()

    def map(self) -> Text:
        rows = tree_rows(self.repo, self.prs)
        box = self.query_one("#map", Static).size
        width = max(60, box.width or self.size.width - 2)
        height = max(3, box.height or len(rows) + 1)
        x_move, x_prog, x_age = cols = columns(width)
        canvas = Canvas(width, height)
        for label, x in (("owner", X_OWNER), ("waits on", X_WAITS), ("branch", X_TREE),
                         ("next move", x_move), ("progress", x_prog), ("quiet", x_age - 1)):
            canvas.put(x, 0, label, MUTED)

        # keep the selected branch on screen when the map is taller than its box
        at = next((i for i, row in enumerate(rows) if row.pr is not None and row.pr is self.sel), 0)
        top = max(0, min(len(rows) - (height - 1), at - (height - 1) // 2))
        sweep_row = None if self.sweep_at is None else self.frame - self.sweep_at
        lit = self.lit()

        for i, row in enumerate(rows):
            y = 1 + i - top
            if not 1 <= y < height:
                continue
            pr = row.pr
            canvas.put(X_TREE, y, row.lead, AXIS)              # the tree: drawn, never animated
            x = X_TREE + len(row.lead)
            if row.kind == "base":
                canvas.put(x, y, row.label, f"bold {INK}")
                canvas.put(x + len(row.label) + 2, y, row.note, MUTED)
            elif row.kind == "commit":
                canvas.put(x, y, row.sha, INK2)
                canvas.put(x + len(row.sha) + 1, y, row.label, MUTED)
            else:
                self.draw_branch(canvas, y, x, pr, cols)
                if lit is not None and id(pr) not in lit:      # set aside: not this member's
                    for cx in range(width):
                        canvas.cells[y][cx] = (canvas.cells[y][cx][0], DIM)

            chosen = pr is not None and pr is self.sel
            if chosen:
                wash(canvas, y, 0, width, RAISED)
                canvas.put(0, y, "▌", f"{INK} on {RAISED}")
            if pr is None:
                continue
            hit = self.frame - self.flash.get(id(pr), -999)
            changed = self.frame - self.cell_flash.get(id(pr), -999)
            if 0 <= hit // 2 < len(FLASH):                     # an event landed here: it settles
                wash(canvas, y, 1, X_TREE - 1, FLASH[hit // 2])
                wash(canvas, y, x_move - 1, width, FLASH[hit // 2])
            elif 0 <= changed // 2 < len(FLASH):               # who owns it, or what it waits on, changed
                wash(canvas, y, 1, X_TREE - 1, FLASH[changed // 2])
            elif sweep_row is not None and 0 <= sweep_row - i < 3:
                wash(canvas, y, 1, X_TREE - 1, FLASH[(sweep_row - i) * 2])
                wash(canvas, y, x_move - 1, width, FLASH[(sweep_row - i) * 2])
        if not any(row.pr is not None for row in rows):
            canvas.put(X_TREE, min(height - 1, len(rows) + 2), "no open pull requests or local branches", MUTED)
        if sweep_row is not None and sweep_row > len(rows) + 3:
            self.sweep_at = None
        return canvas.text()

    def draw_branch(self, canvas: Canvas, y: int, x: int, pr: PR, cols: tuple[int, int, int]) -> None:
        x_move, x_prog, x_age = cols
        spin = self.spin(y * 3)
        landing = id(pr) in self.leaving

        # who comes first: the owner, then what the branch waits on
        if landing:
            write(canvas, X_OWNER, y, [("✓ ", GOOD), ("landed", INK)], X_WAITS - X_OWNER - 1)
        else:
            used = write(canvas, X_OWNER, y, owner_segments(pr, spin), X_WAITS - X_OWNER - 3)
            if owner(pr).kind == "agent" and any(w.name for w in pr.workers):
                canvas.put(X_OWNER + used + 1, y, "≠", CRIT)   # a session name breaks the pattern
            write(canvas, X_WAITS, y, waits_segments(pr, spin), X_TREE - X_WAITS - 1)

        icon, color, _, _ = STATUS[pr.state]
        if landing:
            icon, color = "✓", GOOD
        canvas.put(x, y, icon, color)
        canvas.put(x + 2, y, pr.key, f"bold {INK}")
        room = x_move - 1 - (x + 2 + len(pr.key))
        slug = cut("-" + pr.slug, room)
        canvas.put(x + 2 + len(pr.key), y, slug, INK2)
        if broken(pr) and pr.state != "conflict" and len(slug) + 2 <= room:
            canvas.put(x + 2 + len(pr.key) + len(slug) + 1, y, "✗", CRIT)

        if landing:
            canvas.put(x_move, y, "landed on " + pr.root().base, INK)
            return
        here = pipe(pr)
        if here is None:                                       # a local review branch
            canvas.put(x_move, y, cut(f"review notes for {short(pr)}", x_prog - x_move - 1), MUTED)
        else:
            canvas.put(x_move, y, cut(here.what, x_prog - x_move - 1), INK2)
            working = here.state == "working"
            word = "in progress" if working else ("stalled" if here.partly else "not started")
            canvas.put(x_prog, y, word, INK2 if working or here.partly else MUTED)
            if here.total and (working or here.partly):
                self.draw_meter(canvas, x_prog + len(word) + 1, y, pr, here.done, here.total, working)
        fresh = id(pr) in self.touched
        canvas.put(x_age, y, pr.age.rjust(4), f"bold {INK}" if fresh else MUTED)

    def draw_meter(self, canvas: Canvas, x: int, y: int, pr: PR, done: int, total: int,
                   working: bool) -> None:
        """A small meter and its count. When an event moves the count on, the new cells light
        one after another, bright, and then settle."""
        cells = min(total, 5)
        filled = min(cells, round(cells * done / total))
        was_at, was = self.stepped.get(id(pr), (-999, done))
        since = self.frame - was_at
        before = min(cells, round(cells * was / total))
        for k in range(cells):
            new = k >= before and since < GLOW
            if k >= filled or (new and since < 3 * (k - before)):
                canvas.put(x + k, y, "▱", AXIS)
            elif new:
                canvas.put(x + k, y, "▰", f"bold {INK}")
            else:
                canvas.put(x + k, y, "▰", INK2 if working else MUTED)
        canvas.put(x + cells + 1, y, f"{done}/{total}", f"bold {INK}" if since < GLOW else INK2)

    def effort(self) -> Text:
        """What needs effort to get things moving, most urgent first."""
        box = self.query_one("#effort", Static).content_size
        width = min(104, max(30, box.width or 76))             # with the drawer folded, stay readable
        height = max(3, box.height or BAND - 2)
        canvas = Canvas(width, height)
        canvas.put(0, 0, "needs effort, most urgent first", MUTED)
        key_text = "● yours to fix   ○ waits on a person"
        if width >= 34 + len(key_text) + 2:
            canvas.put(width - len(key_text), 0, "●", ACCENT)
            canvas.put(width - len(key_text) + 2, 0, "yours to fix", MUTED)
            canvas.put(width - 19, 0, "○ waits on a person", MUTED)
        if not self.order:
            canvas.put(0, 1, "Everything that can move is moving." if flat(self.prs) else "Nothing open.", MUTED)
            return canvas.text()

        room = height - 1
        shown = room if len(self.order) <= room else room - 1
        _, cause = root_cause(self.sel) if self.sel is not None else (None, None)
        ranked = sorted(self.order, key=lambda k: self.slots.get(k, 0))     # stable: ties keep order
        for y, key in enumerate(ranked[:shown], start=1):
            if key in self.gone:                               # it got going: say so, then leave
                _, _, why = self.gone[key]
                if why == "landed":
                    canvas.put(0, y, "✓", GOOD)
                elif why == "moving again":
                    canvas.put(0, y, self.spin(), INK)         # the dots: it is at work again
                else:
                    canvas.put(0, y, "→", MUTED)
                canvas.put(2, y, self.said.get(key, ""), INK2)
                canvas.put(11, y, why, INK2)
                continue
            e = self.efforts[key]
            canvas.put(0, y, "●" if e.yours else "○", ACCENT if e.yours else MUTED)
            canvas.put(2, y, short(e.pr), f"bold {INK}")
            urgency = e.urgency
            x_urgency = width - len(urgency)
            if e.kind == "needs you":
                name = f"agent {e.pipe.agent.serial}" if e.pipe.agent is not None else "its agent"
                what = f"{name} at its prompt · {e.pipe.what}"
            elif e.kind == "no agent":
                what = f"no agent · {e.pipe.what}"
            else:
                what = f"{e.who} · {e.pipe.what}"
            if e.behind:
                what += f" · holds up {', '.join(short(pr) for pr in e.behind)}"
            if e.kind == "person":                             # how far the person got, said last
                what += (f" · stalled {e.pipe.done}/{e.pipe.total}" if e.pipe.partly and e.pipe.total
                         else " · stalled" if e.pipe.partly else " · not started")
            canvas.put(11, y, cut(what, x_urgency - 13), INK if e.yours else INK2)
            if e.waiting:
                held, rest = urgency.split(" · ")
                canvas.put(x_urgency, y, held, INK)
                canvas.put(x_urgency + len(held), y, " · " + rest, MUTED)
            else:
                canvas.put(x_urgency, y, urgency, MUTED)
            since = self.frame - self.entered.get(key, -999)
            if 0 <= since // 2 < len(FLASH):                   # new on the list: it settles in
                wash(canvas, y, 0, width, FLASH[since // 2])
            elif cause is not None and cause is e.pr:          # what holds the chosen branch up
                wash(canvas, y, 0, width, RAISED)
        if len(self.order) > shown:
            canvas.put(2, height - 1, f"and {len(self.order) - shown} more: n steps through them all", MUTED)
        return canvas.text()

    def drawer(self) -> Text:
        picked = self.member(self.pick)
        if picked is not None:
            return self.member_card(picked)
        pr = self.sel
        if pr is None:
            return Text("nothing selected", style=MUTED)
        spin = self.spin()
        out = Text()
        out.append(f"{self.repo}-", style=MUTED).append(pr.name + "\n", style=f"bold {INK}")

        who = owner(pr)                                        # who comes first here too
        out.append("owner    ", style=MUTED)
        if id(pr) in self.leaving:
            out.append("✓ ", style=GOOD).append("landed · nobody needs to own it now\n", style=INK2)
        elif who.kind == "agent":
            for text, style in owner_segments(pr, spin):
                out.append(text, style=style)
            out.append(f" {who.state}", style=INK if who.state == "at its prompt" else INK2)
            out.append(f" · {who.worker.note}\n", style=MUTED)
        elif who.kind == "no agent":
            if who.fixable:
                out.append("● ", style=ACCENT).append("no agent", style=f"bold {INK}")
                out.append(" · the move is yours and nobody is on it\n", style=INK2)
            else:
                out.append("no agent · none is needed until the wait ends\n", style=INK2)
        elif who.kind == "person":
            out.append(who.name, style=INK)
            out.append(" · on GitHub; their agents cannot be seen\n", style=MUTED)
        else:
            out.append(f"review notes for {short(pr)}, kept on this Mac\n", style=INK2)

        wait = waits(pr)
        here = pipe(pr)
        out.append("waits on ", style=MUTED)
        if id(pr) in self.leaving or wait is None:
            out.append("nothing\n", style=MUTED)
        elif wait.kind == "working":
            out.append("nothing: its agent is at work\n", style=INK2)
        else:
            for text, style in waits_segments(pr, spin):
                out.append(text, style=style)
            end = pipe(wait.end_pr) if wait.end_pr is not None else None
            if wait.kind == "agent":
                out.append(" at work", style=INK2)
            if end is not None:
                out.append(f" · {end.what}", style=INK2)
                if wait.kind == "person":
                    out.append(f" · {end.progress}", style=MUTED)
            out.append("\n")

        icon, color, label, _ = STATUS[pr.state]
        out.append(icon, style=color).append(f" {label} · {pr.title}\n", style=INK2)
        if here is not None:
            out.append("move     ", style=MUTED)
            out.append(f"{here.what} · {here.progress}\n", style=INK2)
        root = pr.root()
        out.append("hangs    ", style=MUTED)
        if pr.parent is not None:
            out.append(f"on {short(pr.parent)}, its stack ", style=INK2)
        out.append(f"{anchor_note(pr)} · ", style=INK2)
        out.append(sha(self.repo, root.base, root.behind) + "\n", style=INK)
        queued = self.behind(pr)
        if queued:
            out.append("holds up ", style=MUTED)
            out.append(", ".join(short(q) for q in queued) + "\n", style=INK2)

        off = [w for w in pr.workers if w.name]
        out.append("names    ", style=MUTED)
        if off:
            out.append("≠ ", style=CRIT).append(f"session is named {off[0].name}", style=INK)
        else:
            out.append("✓ ", style=GOOD).append("agree", style=INK2)
        out.append("     checks ", style=MUTED)
        out.append(" · ".join(x for x in (pr.ci_note, pr.merge) if x) or "none yet",
                   style=INK if broken(pr) else INK2)
        return out

    def member_card(self, m: Member) -> Text:
        """One member of the cast: what they own, what they wait on, and what waits on them,
        named as branches. No counts."""
        spin = self.spin()
        out = Text()
        if m.kind == "agent":
            pr = m.pr
            target = pr.parent if pr.state == "findings" and pr.parent is not None else pr
            mark, color = {"working": (spin, INK), "at its prompt": ("▷", ACCENT),
                           "waiting": ("·", MUTED)}[m.state]
            out.append(mark + " ", style=color)
            out.append(f"{m.name} · {short(pr)}", style=f"bold {INK}")
            out.append(f"   {m.state}\n", style=INK if m.state == "at its prompt" else INK2)
            out.append("owns     ", style=MUTED).append(cut(pr.name, 48) + "\n", style=INK2)
            session = owner(pr).worker
            out.append("doing    ", style=MUTED)
            out.append((session.note if session is not None else "") + "\n", style=INK2)
            out.append("waits on ", style=MUTED)
            wait = waits(target)
            if m.state == "working" or wait is None:
                out.append("nothing: it is at work\n", style=INK2)
            else:
                for text, style in waits_segments(target, spin):
                    out.append(text, style=style)
                out.append(f" · {wait.what}\n", style=INK2)
            if m.waited_by:
                out.append("holds up ", style=MUTED)
                out.append(", ".join(waited_names(m)) + "\n", style=INK2)
        elif m.kind == "no agent":
            pr = m.pr
            here = pipe(pr)
            out.append("● ", style=ACCENT).append(f"no agent · {short(pr)}", style=f"bold {INK}")
            out.append("   the move is yours and nobody is on it\n", style=INK2)
            out.append("branch   ", style=MUTED).append(cut(pr.name, 48) + "\n", style=INK2)
            out.append("move     ", style=MUTED)
            out.append((f"{here.what} · {here.progress}" if here else "") + "\n", style=INK2)
            out.append("quiet    ", style=MUTED).append(pr.age + "\n", style=INK2)
            queued = self.behind(pr)
            if queued:
                out.append("holds up ", style=MUTED)
                out.append(", ".join(short(q) for q in queued) + "\n", style=INK2)
        else:
            if m.state == "nothing to do":
                out.append("● ", style=ACCENT).append(m.name, style=f"bold {INK}")
                out.append("   has nothing to do · could be asked to review\n", style=INK)
            else:
                out.append(m.name, style=f"bold {INK}").append("   has work\n", style=INK2)
            if m.owns:
                out.append("owns     ", style=MUTED)
                out.append(cut(" ".join(short(pr) for pr in m.owns), 48) + "\n", style=INK2)
            if m.waited_by:
                out.append("waited on by\n", style=MUTED)
            for pr in m.waited_by[:4]:
                icon, color, _, _ = STATUS[pr.state]
                end, end_pr = root_cause(pr)
                out.append(f"{icon} ", style=color)
                out.append(f"{short(pr)} ", style=f"bold {INK}")
                if end is not None:
                    via = f"through {short(end_pr)}: " if end_pr is not pr else ""
                    out.append(cut(f"{via}{end.what}", 27).ljust(28), style=INK2)
                    out.append(end.progress, style=MUTED)
                out.append("\n")
            if len(m.waited_by) > 4:
                out.append("and more further down the map\n", style=MUTED)
        out.append("←→ next in the cast · esc back to the branch", style=MUTED)
        return out

    def log_text(self) -> Text:
        out = Text()
        if self.repo != "agent-frolic":
            out.append("events   ", style=MUTED)
            out.append("the made-up event feed only runs on agent-frolic", style=MUTED)
            return out
        newest = self.events[-2:][::-1]
        if not newest:
            wait = max(0, (self.next_at - self.frame) // FPS)
            out.append("events   ", style=MUTED).append(f"the first one lands in {wait}s", style=MUTED)
        for i, (at, said) in enumerate(newest):
            seconds = (self.frame - at) // FPS
            out.append(f"{'now' if not seconds else str(seconds) + 's ago':<9}",
                       style=INK if i == 0 and seconds < 2 else MUTED)
            out.append(said, style=INK if i == 0 else INK2)
            out.append("\n")
        return out

    # ---- keys --------------------------------------------------------------
    def action_move(self, step: int) -> None:
        branches = self.branches()
        if not branches:
            return
        at = next((i for i, pr in enumerate(branches) if pr is self.sel), 0)
        self.sel = branches[max(0, min(len(branches) - 1, at + step))]
        self.pick = None
        self.draw()

    def action_card(self, step: int) -> None:
        """Pick the next card along the strip; its rows stay lit and the rest are set aside."""
        keys: list[Key] = []
        for block in self.blocks:
            for key in block.keys:
                if key not in keys:
                    keys.append(key)
        if not keys:
            return
        if self.pick not in keys:
            self.pick = keys[0 if step > 0 else -1]
        else:
            self.pick = keys[max(0, min(len(keys) - 1, keys.index(self.pick) + step))]
        self.draw()

    def action_clear(self) -> None:
        self.pick = None
        self.draw()

    def action_next_effort(self) -> None:
        """Jump to the next thing that needs effort, in the list's order: most urgent first."""
        branches = self.branches()
        stops = [e.pr for e in needs_effort(self.prs) if any(e.pr is pr for pr in branches)]
        if not stops:
            return
        at = next((i for i, pr in enumerate(stops) if pr is self.sel), -1)
        self.sel = stops[(at + 1) % len(stops)]
        self.pick = None
        self.draw()

    def action_sweep(self) -> None:
        self.sweep_at = self.refreshed_at = self.frame
        self.draw()

    def action_drawer(self) -> None:
        """Fold the drawer away; the list takes its room."""
        self.drawer_open = not self.drawer_open
        drawer = self.query_one("#drawer", Static)
        if self.drawer_open:
            drawer.styles.display = "block"
            drawer.styles.animate("width", value=float(self.drawer_w), duration=0.3)
        else:
            drawer.styles.animate("width", value=0.0, duration=0.3,
                                  on_complete=lambda: setattr(drawer.styles, "display", "none"))

    def action_pause(self) -> None:
        self.paused = not self.paused
        self.draw()

    def action_repo(self, step: int) -> None:
        names = list(REPOS)
        self.load(names[(names.index(self.repo) + step) % len(names)])
        self.draw()


if __name__ == "__main__":
    LiveV4().run()
