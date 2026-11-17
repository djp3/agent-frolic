"""Agent Frolic mock-up, option X (it began as option 3, v5): live, with humans and their agents. If it moves, work
is moving. If it is golden yellow, it is still, and it is yours to get going.

TWO KINDS OF ACTOR. Humans, and the agents each human operates. An agent always sits on its
own line beneath its human; a human doing the work alone is one line. Other people's agents
are on screen too.

THE LIST has two columns, with a rule between them.
- Column 1 is who: owner | state | waits on. A PR takes as many lines here as it needs: the
  author, then each of the author's agents on it; beside them the other parties it waits on
  (a reviewer and the agent reviewing for them, or another branch and whoever that waits on).
  When the owner side holds the move, the state says so ("to act", "at prompt") and waits on
  stays empty.
- Column 2 is the hierarchy, in one tree for every repo on screen: repo, its base (main),
  commit, branch, stack. Beside each branch: review (each reviewer GitHub knows, with their
  latest word, and ◆ the comments still to resolve), checks, progress, how long it is quiet.
  The extra lines of a PR carry its next move in words.
- Rules separate the sections: a double rule at each repo, a heavy one at each base, a light
  one at each commit, carrying its short hash.

AT THE TOP, under the repos: one card per human, their agents beneath them (the ones at a
prompt first, then the ones at work, then the ones that wait). A line stands between one
human and the next, and a line runs under the cards.

FOCUS. o and O step through everyone, a human with all their agents, the human alone, and
each agent. What is out of focus dims where it stands; nothing re-sorts. The cards show the
focus by their background: every card is raised when everyone is in focus, one whole card
for a human with their agents, only the name for the human alone, one line for one agent.

LIVE. Only one thing moves all the time: the dots that turn where an agent is at work.
Everything else moves when something happens, and then settles: the rows it touched wash
pale for a second, a progress meter steps, a card whose human or agent changed washes, and
the needs-effort list re-orders itself (most urgent first: is an agent idle because of it,
then how long it has been quiet).

FRESHNESS. At the right end of the top line, two thin bars share one row. The upper one is
the poll: full when GitHub was just polled, it loses a block at a steady pace and is empty
when the next poll is due. The lower one, in another color, is the webhooks: it drains at the
same pace, and snaps back to full whenever a webhook delivery arrives, which is often and
irregular. Both count down to the right: blocks leave from the left, and what is left sits
against the right edge. A lower bar that keeps draining says the webhooks have gone quiet and
the poll is all there is. The bars carry no words; resting the pointer on them says which is
which.

DIVIDERS. Every divider line (the line under the top line, the rules at repos, bases and
commits, the rule between the two columns) is the blue agent farm uses for its dividers.

Golden yellow means one thing: idle, and you can fix it. It is the yellow agent farm gives a
session that waits for you. Someone else's idle agent waits on its operator and is grey.
Blue is only the divider lines.

    .venv/bin/python mockup/watch.py        # runs it, and starts it again whenever its source changes
    .venv/bin/python mockup/optionx.py      # runs it once

Keys: up/down pick a PR, n the next thing needing effort, o / O step the focus, escape
back to everyone, m the repo menu (up/down, space toggles, enter closes), e the next event
now, space pause, d fold the drawer, q quit. Every name and number is made up.
"""
from __future__ import annotations

from dataclasses import dataclass

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.css.query import NoMatches
from textual.widgets import Static

from frolic_v5 import (AXIS, CRIT, GOOD, HAIR, INK, INK2, MUTED, PLANE, PR, RAISED,
                       SHOWN_AT_START, SPIN, STATUS, SURFACE, WARN, WORD, Agent, Canvas, Effort5,
                       Focus, Person, Row5, Segments, Stand, World, bay_rows, focuses,
                       fresh_world, happenings, humans, in_focus, needs_effort5, repo_status5,
                       reviews, short, stand, top_line5, tree5, use_accent, write)

FPS = 12
GAPS = (4, 8, 2, 11, 3, 16, 5, 2, 7, 20)     # seconds between webhook deliveries: irregular on purpose
POLL_EVERY = 60.0                # seconds between polls of GitHub (the real app: minutes)
BAR = 16                         # cells in the two freshness bars
POLL_INK = INK2                  # the poll bar: neutral, it is the steady one
HOOK = "#199e70"                 # the webhook bar: the reference palette's aqua, dark step
TRACK = AXIS                     # the part of a bar that has drained
DIVIDER = "#0053AA"              # every divider line: agent farm's ($primary-darken-2 in Textual's dark theme)
ACCENT = "#ffd75f"               # idle, and you can fix it: agent farm's yellow for a session that waits for you
use_accent(ACCENT)               # the shared cells and the top line mark it the same way
FLASH = ["#4a4a46", "#444441", "#3d3d3a", "#363633", "#2f2f2d", "#292927"]   # a wash, fading
DIM = "#5c5b56"                  # PRs out of focus
STRIP = 5                        # rows of the strip of cards: a name, four agents
PER_COL = 4                      # agents beneath a human before a card takes another column
BAND = 10                        # rows of the bottom band: the list and the drawer
GLOW = 16                        # frames a stepped meter stays bright
LEAVE = 16                       # frames a resolved item stays in the list, saying why
SLIDE = 0.17                     # rows a list item moves per frame on its way to its place
X_OWNER, X_STATE, X_WAITS, X_RULE, X_TREE = 1, 20, 31, 50, 52
CI = {"pass": ("✓", GOOD), "fail": ("✗", CRIT), "run": ("…", WARN), "none": ("·", AXIS)}
ORDER = {"at its prompt": 0, "working": 1, "waiting": 2}


def cut(text: str, width: int) -> str:
    return text if len(text) <= width else text[:max(0, width - 1)] + "…"


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


def columns(width: int) -> tuple[int, int, int | None, bool]:
    """Where review, checks and progress start in a list this wide, and whether reviewers
    are named. Column 1 and the tree never move; a narrow window gives up progress first,
    then the reviewers' names."""
    x_prog: int | None = width - 4 - 22
    x_ci = x_prog - 4
    x_review = x_ci - 24
    names = True
    if x_review - X_TREE < 36:
        x_prog, x_ci = None, width - 4 - 5
        x_review = x_ci - 24
        if x_review - X_TREE < 30:
            names, x_review = False, x_ci - 9
    return x_review, x_ci, x_prog, names


def review_cell(pr: PR, names: bool, room: int) -> Segments:
    """Each reviewer GitHub knows, with their latest word, then ◆ the comments to resolve."""
    found = reviews(pr)
    tail: Segments = [("◆", MUTED), (str(pr.threads[0]), INK)] if pr.threads[0] else []
    if not found and not tail:
        return [("·", AXIS)] if pr.is_pr else []
    long: Segments = []
    for who, verdict in found:
        mark, color, _ = WORD[verdict]
        long += [(mark, color), (f" {who}  ", INK2)]
    if names and seg_len(long) + seg_len(tail) <= room:
        return long + tail
    brief: Segments = [(WORD[verdict][0], WORD[verdict][1]) for _, verdict in found]
    return brief + ([(" ", MUTED)] if brief and tail else []) + tail


# ---- the strip: one card per human, their agents beneath --------------------------------------

@dataclass
class Card:
    human: str
    head: Segments                               # the name line
    cols: list[list[tuple[Segments, tuple | None]]]   # columns of (agent line, the agent's key)
    widths: list[int]
    width: int
    x: int = 0


def agent_line(agent: Agent, form: str) -> Segments:
    """ "⠋ pr-0013": the agent's mark and the PR it is on. The serial is left out: it counts
    sessions on that one PR, so on a card it read as the same agent many times over. Yellow
    only on an agent of yours at its prompt; someone else's is grey."""
    said = f"pr-{agent.number:04d}"
    yours = agent.operator == "you"
    if agent.working:
        return [(SPIN[0] + " ", INK), (said, INK2)]
    if agent.state == "at its prompt":
        return [("▷ ", ACCENT if yours else MUTED), (said, f"bold {INK}" if yours else INK2)]
    return [("· ", MUTED), (said, MUTED)]


def head_line(person: Person) -> Segments:
    """A card's name line: the name alone. The yellow dot says the rest: on you, things wait
    on you; on someone else, they have nothing to do. No dot: nothing waits on you, or they
    have work. The name sits where the agents' PRs sit beneath it, dot or no dot."""
    if person.name == "you":
        if person.to_act:
            return [("● ", ACCENT), ("you", f"bold {INK}")]
        return [("  ", MUTED), ("you", INK)]
    if person.has_work:
        return [("  ", MUTED), (person.name, INK2)]
    return [("● ", ACCENT), (person.name, f"bold {INK}")]


def make_card(person: Person, form: str, max_cols: int) -> Card:
    """A human and the agents they operate: the ones at a prompt first, then the ones at
    work, then the ones that wait. What does not fit is folded, and the card says so."""
    agents = sorted(person.agents, key=lambda a: ORDER[a.state])
    slots = max(1, max_cols) * PER_COL
    folded: list[Agent] = []
    if len(agents) > slots:
        agents, folded = agents[:slots - 1], agents[slots - 1:]
    entries: list[tuple[Segments, tuple | None]] = [(agent_line(a, form), a.key) for a in agents]
    if folded:
        kinds = {a.state for a in folded}
        what = "waiting" if kinds == {"waiting"} else "more"
        entries.append(([("  ", MUTED), (f"{len(folded)} {what}, folded", MUTED)], None))
    cols = [entries[at:at + PER_COL] for at in range(0, len(entries), PER_COL)]
    widths = [max(seg_len(line) for line, _ in col) for col in cols]
    head = head_line(person)
    return Card(person.name, head, cols, widths,
                max(seg_len(head), sum(widths) + 2 * max(0, len(cols) - 1)))


def lay_cards(people: list[Person], room: int) -> tuple[list[Card], str]:
    """Lay the cards out so the strip fits, folding your card to fewer columns when it
    does not. (The two forms it tries are now drawn alike: an agent is its mark and its PR.)"""
    def need(person: Person) -> int:
        return max(1, -(-len(person.agents) // PER_COL))

    def build(form: str, yours: int) -> list[Card]:
        return [make_card(p, form, min(need(p), yours) if p.name == "you" else 1) for p in people]

    def span(cards: list[Card]) -> int:
        return sum(card.width for card in cards) + 3 * max(0, len(cards) - 1)

    most = max([need(p) for p in people if p.name == "you"] or [1])
    tries = [("long", most), ("brief", most)] + [("brief", n) for n in range(most - 1, 0, -1)]
    cards, chosen = None, "brief"
    for form, yours in tries:
        attempt = build(form, yours)
        if span(attempt) <= room:
            cards, chosen = attempt, form
            break
    if cards is None:                            # nothing fits: the narrowest, folding as little as it can
        form, yours = min(tries, key=lambda t: (span(build(*t)), -t[1]))
        cards, chosen = build(form, yours), form
    x = 0
    for card in cards:
        card.x = x
        x += card.width + 3
    return cards, chosen


def line_agents(here: Stand) -> list[list[Agent]]:
    """The agents drawn on each line of a PR's column 1, in step with `bay_rows`: the owner
    side stacks the author and then the author's agents; the waits-on side stacks each party
    and then that party's agents."""
    left: list[list[Agent]] = [[]] + [[agent] for agent in here.owner.agents]
    right: list[list[Agent]] = []
    for party in here.waits:
        right.append([])
        if party.branch is None:
            right += [[agent] for agent in party.agents]
    lines = max(len(left), len(right))
    left += [[]] * (lines - len(left))
    right += [[]] * (lines - len(right))
    return [a + b for a, b in zip(left, right)]


@dataclass
class Line:
    """One line of the list: a rule (repo, base, commit) or one line of a PR."""
    row: Row5
    part: int = 0                                # which line of its PR this is
    cells: tuple[Segments, Segments, Segments] | None = None      # owner, state, waits on
    lead: str = ""                               # the tree connectors on this line


def fresh_tip() -> Text:
    """What the two freshness bars are, for the pointer resting on them."""
    tip = Text()
    tip.append("▀", style=POLL_INK).append(" upper · poll\n", style=f"bold {INK}")
    tip.append("Empties at a steady pace. When it is empty, GitHub is polled again.\n\n", style=INK2)
    tip.append("▄", style=HOOK).append(" lower · webhooks\n", style=f"bold {INK}")
    tip.append("Refills each time GitHub sends a change. If it keeps emptying, "
               "the webhooks have gone quiet.", style=INK2)
    return tip


class LiveV5(App):
    CSS = f"""
    Screen {{ background: {PLANE}; color: {INK}; layers: base overlay; }}
    #topline {{ height: 1; margin: 0 2; }}
    #tabs {{ width: 1fr; height: 1; text-wrap: nowrap; text-overflow: ellipsis; }}
    #fresh {{ width: {BAR}; height: 1; margin: 0 0 0 2; }}
    Tooltip {{ background: {RAISED}; color: {INK2}; }}
    #toprule {{ height: 1; margin: 0 1; }}
    #strip {{ height: {STRIP}; margin: 0 1; }}
    #striprule {{ height: 1; margin: 0 1; }}
    #head {{ height: 1; margin: 0 1; }}
    #map {{ height: 1fr; margin: 0 1; }}
    #band {{ height: {BAND}; margin: 0 1; }}
    .pane {{ height: {BAND}; background: {SURFACE}; padding: 1 2;
             text-wrap: nowrap; text-overflow: ellipsis; overflow: hidden; }}
    #effort {{ width: 1fr; margin: 0 1 0 0; }}
    #drawer {{ width: 66; }}
    #events {{ height: 1; margin: 0 2; text-wrap: nowrap; text-overflow: ellipsis; }}
    #hints {{ height: 1; margin: 0 2; color: {MUTED}; text-wrap: nowrap; text-overflow: ellipsis; }}
    #menu {{ layer: overlay; display: none; width: 60; height: auto; margin: 2 0 0 6;
             padding: 1 2; background: {RAISED}; border: round {AXIS}; }}
    """
    BINDINGS = [
        Binding("up", "move(-1)", show=False), Binding("down", "move(1)", show=False),
        Binding("n", "next_effort", show=False),
        Binding("o", "focus(1)", show=False), Binding("O", "focus(-1)", show=False),
        Binding("escape", "clear", show=False), Binding("m", "menu", show=False),
        Binding("enter", "close_menu", show=False),
        Binding("e", "fire", show=False), Binding("space", "pause", show=False),
        Binding("d", "drawer", show=False), Binding("q", "quit", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.frame = 0
        self.paused = False
        self.drawer_open = True
        self.drawer_w = 66
        self.world: World = fresh_world()
        self.script = happenings()
        self.shown: list[str] = [name for name in self.world.repos if name in SHOWN_AT_START]
        self.sel: int | None = None               # the selected PR, by number
        self.focus = Focus()
        self.top = 0                              # first line of the list on screen
        self.where = (0, 0, 0)                    # (first line shown, last line shown, lines in all)
        self.unseen: tuple[list[str], list[str]] = ([], [])
        self.menu_open = False
        self.menu_at = 0
        self.menu_note = ""
        self.step = 0
        self.arrivals = 0                         # webhook deliveries so far: picks the next gap
        self.next_at = int(GAPS[0] * FPS)
        self.changed_at = 0                       # frame of the last webhook delivery
        self.polled_at = 0                        # frame of the last poll of GitHub
        self.card_lines: list[int] = []           # where the lines between the humans' cards stand
        self.events: list[tuple[int, str]] = []
        self.flash: dict[int, int] = {}           # PR -> frame something happened to it
        self.cell_flash: dict[int, int] = {}      # PR -> frame its owner / state / waits on changed
        self.card_flash: dict[object, int] = {}   # human or agent key -> frame its card line changed
        self.stepped: dict[int, tuple[int, int]] = {}     # PR -> (frame, count before the step)
        self.cell_sig: dict[int, tuple] = {}
        self.card_sig: dict[object, object] = {}
        self.prog_sig: dict[int, tuple[int, int]] = {}
        # the needs-effort list: where each item is drawn, and what has just left it
        self.efforts: dict[int, Effort5] = {}
        self.order: list[int] = []
        self.slots: dict[int, float] = {}
        self.said: dict[int, str] = {}
        self.gone: dict[int, tuple[int, int, str]] = {}
        self.entered: dict[int, int] = {}
        numbers = self.numbers()
        self.sel = numbers[0] if numbers else None
        self.sync(settle=True)

    def compose(self) -> ComposeResult:
        with Horizontal(id="topline"):
            yield Static(id="tabs")
            yield Static(id="fresh")         # the two freshness bars, against the right edge
        yield Static(id="toprule")           # a line under the top line
        yield Static(id="strip")
        yield Static(id="striprule")         # a line under the cards
        yield Static(id="head")
        yield Static(id="map")
        with Horizontal(id="band"):
            yield Static(id="effort", classes="pane")
            yield Static(id="drawer", classes="pane")
        yield Static(id="events")
        yield Static("↑↓ PR    n next needing effort    o O focus    esc everyone    m repos    "
                     "e next event    space pause    d drawer    q quit", id="hints")
        yield Static(id="menu")

    def on_mount(self) -> None:
        self.query_one("#fresh", Static).tooltip = fresh_tip()
        self.fit()
        self.set_interval(1 / FPS, self.tick)
        self.draw()
        self.call_after_refresh(self.draw)

    def on_resize(self) -> None:
        self.fit()
        self.draw()
        self.call_after_refresh(self.draw)

    def fit(self) -> None:
        """The drawer gives way to the list in a narrow window."""
        self.drawer_w = 66 if self.size.width >= 140 else max(30, self.size.width * 2 // 5)
        if self.drawer_open:
            try:
                self.query_one("#drawer", Static).styles.width = self.drawer_w
            except NoMatches:                     # resized while the app is quitting
                return

    # ---- state -------------------------------------------------------------
    def numbers(self) -> list[int]:
        """The PRs on screen, in list order."""
        return [row.pr.number for row in tree5(self.world, self.shown) if row.pr is not None]

    def find(self, number: int | None) -> PR | None:
        if number is None:
            return None
        return next((pr for pr in self.world.all(self.shown) if pr.number == number), None)

    def spin(self, offset: int = 0) -> str:
        """The turning dots: an agent is at work."""
        return SPIN[(self.frame + offset) % len(SPIN)]

    def sync(self, settle: bool = False) -> None:
        """Notice what changed since the last look (who cells, cards, counts, the list), so
        it can wash or step for a moment. With settle, nothing washes (a fresh start)."""
        cells: dict[int, tuple] = {}
        counts: dict[int, tuple[int, int]] = {}
        for pr in self.world.all():
            cells[pr.number] = tuple(plain(o) + "|" + plain(s) + "|" + plain(w)
                                     for o, s, w in bay_rows(self.world, pr, ""))
            counts[pr.number] = pr.progress
        cards: dict[object, object] = {}
        for person in humans(self.world, self.shown):
            cards[person.name] = plain(head_line(person))
            for agent in person.agents:
                cards[agent.key] = agent.state
        if not settle:
            for number, sig in cells.items():
                if number in self.cell_sig and self.cell_sig[number] != sig:
                    self.cell_flash[number] = self.frame
            for number, count in counts.items():
                before = self.prog_sig.get(number, count)
                if before != count:
                    self.stepped[number] = (self.frame, before[0] if before[1] == count[1] else 0)
            for key, sig in cards.items():
                if self.card_sig.get(key) != sig:
                    self.card_flash[key] = self.frame
        self.cell_sig, self.card_sig, self.prog_sig = cells, cards, counts
        if self.focus not in focuses(self.world, self.shown):  # who was in focus is gone
            self.focus = Focus()
        self.sync_effort(settle)

    def sync_effort(self, settle: bool = False) -> None:
        """Bring the needs-effort list up to date and move each item one step toward its
        place. With settle, everything jumps to its place."""
        found = needs_effort5(self.world, self.shown)
        keys = [e.pr.number for e in found]
        before = set(self.efforts)
        self.efforts = dict(zip(keys, found))
        for key in before - set(keys):                         # it stopped needing effort
            if key in self.gone or settle:
                continue
            pr = self.find(key)
            if pr is None:
                continue
            here = stand(self.world, pr)
            why = ("moving again" if here.moving
                   else f"now waits on {short(here.end_pr)}" if here.queued
                   else "nobody has to act")
            self.gone[key] = (self.frame, round(self.slots.get(key, 0)), why)
        for key in keys:
            self.gone.pop(key, None)
            self.said[key] = self.efforts[key].pr.key
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
        if self.frame - self.polled_at >= int(POLL_EVERY * FPS):
            self.poll()
        if self.frame >= self.next_at:
            self.fire()
        self.sync()
        self.draw()

    def action_fire(self) -> None:
        """The e key: the next event lands now, paused or not."""
        self.fire()
        self.sync()
        self.draw()

    def poll(self) -> None:
        """The steady poll of GitHub. Here it finds nothing: the webhooks brought it all."""
        self.polled_at = self.frame
        self.events.append((self.frame, "polled GitHub · nothing new: the webhooks had brought it all"))

    def fire(self) -> None:
        """A webhook delivery arrives: the next made-up event. They come at irregular gaps."""
        self.arrivals += 1
        self.next_at = self.frame + int(GAPS[self.arrivals % len(GAPS)] * FPS)
        self.changed_at = self.frame
        if self.step >= len(self.script):                      # start the loop again
            self.world = fresh_world()
            self.step = 0
            self.flash.clear()
            self.cell_flash.clear()
            self.stepped.clear()
            self.sync(settle=True)
            self.events.append((self.frame, "replaying the made-up events from the start"))
            return
        happening = self.script[self.step]
        self.step += 1
        for number in happening.apply(self.world):
            self.flash[number] = self.frame
        aside = "" if happening.repo in self.shown else "  (not on screen)"
        self.events.append((self.frame, f"{happening.repo} · {happening.say}{aside}"))

    # ---- drawing -----------------------------------------------------------
    def draw(self) -> None:
        try:
            self.query_one("#tabs", Static).update(top_line5(
                self.world, self.shown, frame=self.frame, paused=self.paused, refreshed=None,
                at_work=False))
            self.query_one("#fresh", Static).update(self.freshness())
            self.query_one("#strip", Static).update(self.strip())      # sets where the card lines stand
            self.query_one("#toprule", Static).update(self.card_rule("#toprule", "┬"))
            self.query_one("#striprule", Static).update(self.card_rule("#striprule", "┴", X_RULE))
            self.query_one("#map", Static).update(self.map())      # sets what the header reports
            self.query_one("#head", Static).update(self.head())
            self.query_one("#effort", Static).update(self.effort())
            self.query_one("#drawer", Static).update(self.drawer())
            self.query_one("#events", Static).update(self.log_text())
            if self.menu_open:
                self.query_one("#menu", Static).update(self.menu_text())
        except NoMatches:                         # a last tick while the app is quitting
            return

    def freshness(self) -> Text:
        """Two thin bars in one row of cells, in place of "refreshed 2m ago". Each cell is an
        upper half block: its ink is the poll bar, its background the webhook bar. Both
        count down to the right over one poll interval (blocks leave from the left); the
        poll refills at each poll, the webhook bar whenever a delivery arrives."""
        def cells(since_frame: int) -> int:
            left = 1 - (self.frame - since_frame) / (POLL_EVERY * FPS)
            return max(0, min(BAR, -int(-left * BAR // 1)))       # rounded up: full until a block's time is gone

        poll, hook = cells(self.polled_at), cells(self.changed_at)
        out = Text()
        for i in range(BAR):                                   # what is left sits against the right edge
            out.append("▀", style=f"{POLL_INK if i >= BAR - poll else TRACK} "
                                  f"on {HOOK if i >= BAR - hook else TRACK}")
        return out

    def strip(self) -> Text:
        """Humans across the top, each with the agents they operate beneath them. What is in
        focus has the raised background, and that is the only place the focus is shown:
        every card when everyone is in focus; one human's whole card for that human with
        their agents; only the name for the human alone; only its own line for one agent."""
        box = self.query_one("#strip", Static).size
        width = max(40, box.width or self.size.width - 2)
        canvas = Canvas(width, STRIP)
        people = humans(self.world, self.shown)
        cards, form = lay_cards(people, width - 2)
        kind = self.focus.kind
        turn = 0
        for card in cards:
            x = 1 + card.x
            picked = kind in ("team", "human") and self.focus.human == card.human
            whole = kind == "all" or (kind == "team" and picked)   # the whole card is in focus
            head = ([(text, f"bold underline {INK}" if text == card.human else style)
                     for text, style in card.head] if picked else card.head)
            write(canvas, x, 0, head, max(0, width - x))
            lines: list[tuple[int, int, int, object, bool]] = [(0, x, card.width, card.human, picked)]
            cx = x
            for col, col_w in zip(card.cols, card.widths):
                for i, (line, key) in enumerate(col):
                    turn += 3
                    turned = [(self.spin(turn) + " " if len(text) == 2 and text[0] in SPIN else text,
                               style) for text, style in line]     # each agent's dots turn on their own
                    write(canvas, cx, 1 + i, turned, max(0, width - cx))
                    lines.append((1 + i, cx, col_w, key,
                                  kind == "agent" and key is not None and self.focus.agent == key))
                cx += col_w + 2
            if whole:                                              # backgrounds go on after the words
                for y in range(STRIP):
                    wash(canvas, y, x - 1, x + card.width + 1, RAISED)
            for y, at, w, key, lit in lines:
                self.card_wash(canvas, y, at, w, key, lit)
        self.card_lines = [x for card in cards[:-1] if (x := 1 + card.x + card.width + 1) < width]
        for x in self.card_lines:                              # a line between one human and the next
            for y in range(STRIP):
                canvas.put(x, y, "│", DIVIDER)
        if cards and 1 + cards[-1].x + cards[-1].width > width:
            canvas.put(width - 1, 0, "…", MUTED)
        return canvas.text()

    def card_rule(self, widget: str, joint: str, column_rule: int | None = None) -> Text:
        """A line above or below the cards. The lines between the humans meet it, and below
        the cards the rule between the list's two columns starts from it."""
        box = self.query_one(widget, Static).size
        width = max(40, box.width or self.size.width - 2)
        marks = {x: joint for x in self.card_lines}
        if column_rule is not None:
            marks[column_rule] = "┼" if column_rule in marks else "┬"
        return Text("".join(marks.get(x, "─") for x in range(width)), style=DIVIDER)

    def card_wash(self, canvas: Canvas, y: int, x: int, w: int, key: object, lit: bool) -> None:
        """Behind a card line: a fading wash when it just changed, and otherwise raised
        while it is the thing in focus."""
        since = self.frame - self.card_flash.get(key, -999) if key is not None else 999
        if 0 <= since // 2 < len(FLASH):
            wash(canvas, y, x - 1, x + w + 1, FLASH[since // 2])
        elif lit:
            wash(canvas, y, x - 1, x + w + 1, RAISED)

    def lines(self) -> list[Line]:
        """The list, line by line: rules for repo, base and commit, and each PR's lines. A
        PR's extra lines keep the tree's connectors going, so the tree never breaks."""
        out: list[Line] = []
        for i, row in enumerate(tree5(self.world, self.shown)):
            if row.pr is None:
                out.append(Line(row, lead=row.lead))
                continue
            tail = (row.lead[:-2] + ("│ " if row.lead.endswith("├ ") else "  ")
                    + ("│ " if row.pr.children else "  "))
            for part, cells in enumerate(bay_rows(self.world, row.pr, self.spin(i * 3))):
                out.append(Line(row, part, cells, row.lead if part == 0 else tail))
        return out

    def map(self) -> Text:
        box = self.query_one("#map", Static).size
        width = max(70, box.width or self.size.width - 2)
        height = max(3, box.height or 20)
        lines = self.lines()
        canvas = Canvas(width, height)
        cols = columns(width)

        mine = [i for i, line in enumerate(lines)
                if line.row.pr is not None and line.row.pr.number == self.sel]
        if mine:                                               # keep the selected PR in view
            if mine[0] - 2 < self.top:
                self.top = mine[0] - 2
            if mine[-1] + 1 >= self.top + height:
                self.top = mine[-1] + 2 - height
            if mine[0] <= 3:
                self.top = 0
        self.top = max(0, min(self.top, len(lines) - height))
        self.where = (self.top + 1, min(len(lines), self.top + height), len(lines))
        self.unseen = ([], [])                                 # PRs that just changed, above and below the box
        for i, line in enumerate(lines):
            pr = line.row.pr
            if pr is None or line.part or 0 <= i - self.top < height:
                continue
            since = self.frame - max(self.flash.get(pr.number, -999), self.cell_flash.get(pr.number, -999))
            if 0 <= since < 3 * FPS:
                self.unseen[i >= self.top + height].append(short(pr))

        for i, line in enumerate(lines):
            y = i - self.top
            if not 0 <= y < height:
                continue
            row, pr = line.row, line.row.pr
            if pr is None:
                self.draw_rule(canvas, y, row, width)
                continue
            self.draw_pr(canvas, y, line, cols, width)
            chosen = pr.number == self.sel
            if not chosen and not in_focus(self.world, pr, self.focus):
                for x in range(width):                         # out of focus: dimmed where it stands
                    if x != X_RULE:
                        canvas.cells[y][x] = (canvas.cells[y][x][0], DIM)
            if chosen:
                wash(canvas, y, 0, width, RAISED)
                canvas.put(0, y, "▌", f"{INK} on {RAISED}")
            hit = self.frame - self.flash.get(pr.number, -999)
            changed = self.frame - self.cell_flash.get(pr.number, -999)
            if 0 <= hit // 2 < len(FLASH):                     # something happened here: it settles
                wash(canvas, y, 1, X_RULE, FLASH[hit // 2])
                wash(canvas, y, cols[0] - 1, width, FLASH[hit // 2])
            elif 0 <= changed // 2 < len(FLASH):               # who is on it, or what it waits on, changed
                wash(canvas, y, 1, X_RULE, FLASH[changed // 2])
        if not any(line.row.pr is not None for line in lines):
            canvas.put(X_TREE, min(height - 1, len(lines) - self.top + 1),
                       "no open pull requests or local branches", MUTED)
        return canvas.text()

    def draw_rule(self, canvas: Canvas, y: int, row: Row5, width: int) -> None:
        """A rule across both columns, carrying its name: double at a repo, heavy at a base,
        light at a commit (with its short hash). It crosses the rule between the columns."""
        line, cross = {"repo": ("═", "╪"), "base": ("━", "┿"), "commit": ("─", "┼")}[row.kind]
        canvas.put(0, y, line * width, DIVIDER)
        canvas.put(X_RULE, y, cross, DIVIDER)
        parts: Segments = [(" ", "")]
        if row.kind == "repo":
            parts += [(row.label, f"bold {INK}")]
            yours, nudge = repo_status5(self.world, row.repo)
            if yours:
                parts += [("  ●", ACCENT), (str(yours), INK)]
            if nudge:
                parts += [("  ○", MUTED), (str(nudge), MUTED)]
        elif row.kind == "base":
            parts += [(row.lead, AXIS), (row.label, f"bold {INK}")]
            if row.sha and row.sha in row.note:                # "branched from main at 18deb8e, …"
                before, after = row.note.split(row.sha, 1)
                parts += [("  " + before, MUTED), (row.sha, INK2), (after, MUTED)]
            else:
                parts += [("  its history runs down from the tip", MUTED)]
        else:
            parts += [(row.lead[:-2], AXIS), (row.lead[-2:], MUTED), (row.sha, INK2),
                      (" " + row.label, MUTED)]
        parts += [(" ", "")]
        write(canvas, X_TREE - 1, y, parts, max(0, width - X_TREE))

    def draw_pr(self, canvas: Canvas, y: int, line: Line, cols: tuple, width: int) -> None:
        x_review, x_ci, x_prog, names = cols
        pr = line.row.pr
        owner, state, waits = line.cells
        write(canvas, X_OWNER, y, owner, X_STATE - X_OWNER - 1)        # column 1: who
        write(canvas, X_STATE, y, state, X_WAITS - X_STATE - 1)
        write(canvas, X_WAITS, y, waits, X_RULE - X_WAITS - 1)
        canvas.put(X_RULE, y, "│", DIVIDER)
        canvas.put(X_TREE, y, line.lead, AXIS)                         # column 2: the hierarchy
        x = X_TREE + len(line.lead)
        here = stand(self.world, pr)
        if line.part:
            said: Segments = [(pr.next[1], INK2)] if line.part == 1 else []    # its next move, in words
            for agent in line_agents(here)[line.part]:                 # what the agent on this line is doing
                if agent.note:
                    said += [(" · " if said else "", AXIS), (agent.note, MUTED)]
            write(canvas, x, y, said, max(0, width - x - 1))
            return
        icon, color, _, _ = STATUS[pr.state]
        canvas.put(x, y, icon, color)
        canvas.put(x + 2, y, pr.key, f"bold {INK}")
        canvas.put(x + 2 + len(pr.key), y, cut("-" + pr.slug, x_review - 1 - (x + 2 + len(pr.key))), INK2)
        write(canvas, x_review, y, review_cell(pr, names, x_ci - x_review - 1), x_ci - x_review - 1)
        ci_icon, ci_color = CI[pr.ci]
        canvas.put(x_ci, y, ci_icon, ci_color)
        if x_prog is not None:
            done, total = pr.progress
            partly = pr.started or done > 0
            working = here.moving and not here.queued
            word = ("in progress" if working
                    else "stalled" if partly and not here.queued else "not started")
            canvas.put(x_prog, y, word, INK2 if working or word == "stalled" else MUTED)
            if total and word != "not started":
                self.draw_meter(canvas, x_prog + len(word) + 1, y, pr, done, total, working)
        canvas.put(width - len(pr.age), y, pr.age, f"bold {INK}" if pr.age == "now" else MUTED)

    def draw_meter(self, canvas: Canvas, x: int, y: int, pr: PR, done: int, total: int,
                   working: bool) -> None:
        """A small meter and its count. When a count moves on, the new cells light one after
        another, bright, and then settle."""
        cells = min(total, 5)
        filled = min(cells, round(cells * done / total))
        was_at, was = self.stepped.get(pr.number, (-999, done))
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

    def head(self) -> Text:
        """The column headings: column 1 (owner, state, waits on), the rule, column 2."""
        box = self.query_one("#head", Static).size
        width = max(70, box.width or self.size.width - 2)
        x_review, x_ci, x_prog, names = columns(width)
        canvas = Canvas(width, 1)
        canvas.put(X_OWNER + 2, 0, "owner", MUTED)
        canvas.put(X_STATE, 0, "state", MUTED)
        canvas.put(X_WAITS + 2, 0, "waits on", MUTED)
        canvas.put(X_RULE, 0, "│", DIVIDER)
        canvas.put(X_TREE, 0, "branch", MUTED)
        first, last, total = self.where
        if total > last - first + 1:                           # the list is longer than its box
            said = f"rows {first}–{last} of {total}"
            canvas.put(X_TREE + 9, 0, said, INK2)
            above, below = self.unseen                         # a change out of sight is not lost
            room = x_review - (X_TREE + 9 + len(said) + 3)
            for words in (True, False):                        # "▼ pr-0024 changed below", or just "▼ pr-0024"
                note = "  ".join(part for part in (
                    "▲ " + " ".join(above) + (" changed above" if words else "") if above else "",
                    "▼ " + " ".join(below) + (" changed below" if words else "") if below else "") if part)
                if len(note) <= room:
                    break
            else:                                              # too many to name in the room there is
                note = "  ".join(part for part in (f"▲ {len(above)} changed" if above else "",
                                                   f"▼ {len(below)} changed" if below else "") if part)
            if note and room > 8:
                canvas.put(X_TREE + 9 + len(said) + 2, 0, cut(note, room), f"bold {INK}")
        canvas.put(x_review, 0, "review  ◆ to resolve" if names else "review", MUTED)
        canvas.put(x_ci, 0, "ci", MUTED)
        if x_prog is not None:
            canvas.put(x_prog, 0, "progress", MUTED)
        canvas.put(width - 5, 0, "quiet", MUTED)
        return canvas.text()

    def effort(self) -> Text:
        """What needs effort to get things moving, most urgent first."""
        box = self.query_one("#effort", Static).content_size
        width = min(110, max(30, box.width or 70))
        height = max(3, box.height or BAND - 2)
        canvas = Canvas(width, height)
        canvas.put(0, 0, "needs effort, most urgent first", MUTED)
        key_text = "● yours to fix   ○ waits on someone else"
        if width >= 34 + len(key_text) + 2:
            canvas.put(width - len(key_text), 0, "●", ACCENT)
            canvas.put(width - len(key_text) + 2, 0, "yours to fix", MUTED)
            canvas.put(width - 23, 0, "○ waits on someone else", MUTED)
        if not self.order:
            canvas.put(0, 1, "Everything that can move is moving." if self.numbers() else "Nothing open.", MUTED)
            return canvas.text()

        room = height - 1
        shown = room if len(self.order) <= room else room - 1
        chosen = self.find(self.sel)
        cause = stand(self.world, chosen).end_pr.number if chosen is not None else None
        key_w = max(len(text) for text in self.said.values()) if self.said else 14
        ranked = sorted(self.order, key=lambda k: self.slots.get(k, 0))     # stable: ties keep order
        for y, key in enumerate(ranked[:shown], start=1):
            if key in self.gone:                               # it got going: say so, then leave
                _, _, why = self.gone[key]
                canvas.put(0, y, self.spin() if why == "moving again" else "→",
                           INK if why == "moving again" else MUTED)
                canvas.put(2, y, self.said.get(key, ""), INK2)
                canvas.put(3 + key_w, y, why, INK2)
                continue
            e = self.efforts[key]
            canvas.put(0, y, "●" if e.blue else "○", ACCENT if e.blue else MUTED)
            canvas.put(2, y, e.pr.key, f"bold {INK}")
            urgency = e.urgency
            x_urgency = width - len(urgency)
            what = f"{e.who} · {e.stand.what}"
            if e.behind:
                what += f" · holds up {', '.join(short(pr) for pr in e.behind)}"
            canvas.put(3 + key_w, y, cut(what, x_urgency - key_w - 5), INK if e.blue else INK2)
            if e.waiting:
                held, rest = urgency.split(" · ")
                canvas.put(x_urgency, y, held, INK)
                canvas.put(x_urgency + len(held), y, " · " + rest, MUTED)
            else:
                canvas.put(x_urgency, y, urgency, MUTED)
            since = self.frame - self.entered.get(key, -999)
            if 0 <= since // 2 < len(FLASH):                   # new on the list: it settles in
                wash(canvas, y, 0, width, FLASH[since // 2])
            elif cause == key:                                 # what holds the selected PR up
                wash(canvas, y, 0, width, RAISED)
        if len(self.order) > shown:
            canvas.put(2, height - 1, f"and {len(self.order) - shown} more: n steps through them all", MUTED)
        return canvas.text()

    def drawer(self) -> Text:
        """The selected PR: owner and waits on in plain words, then what GitHub records, then
        every agent on it with its operator."""
        pr = self.find(self.sel)
        if pr is None:
            return Text("nothing selected: no open pull requests or local branches", style=MUTED)
        here: Stand = stand(self.world, pr)
        spin = self.spin()
        out = Text()
        out.append(f"{self.world.repo_of(pr)} · ", style=MUTED).append(pr.name + "\n", style=f"bold {INK}")

        def actor(agent: Agent) -> None:
            mark, color = {"working": (spin, INK), "at its prompt": ("▷", INK2),
                           "waiting": ("·", MUTED)}[agent.state]
            out.append(" › ", style=MUTED).append(mark + " ", style=color)
            out.append(f"{agent.name} {'at work' if agent.working else agent.state}",
                       style=INK if agent.working else INK2)

        out.append("owner    ", style=MUTED)
        out.append(here.owner.human, style=INK)
        for agent in here.owner.agents:
            actor(agent)
        if not here.owner.agents:
            out.append(" · no agent on it", style=MUTED)
        out.append("\n")

        out.append("waits on ", style=MUTED)
        if here.moving and not here.waits:
            out.append("nothing: an agent is at work on it", style=INK2)
        elif here.holds:                                       # the owner side holds the move
            if here.blue:
                out.append("● ", style=ACCENT).append("you", style=f"bold {INK}")
            else:
                out.append(here.owner.human, style=INK)
            out.append(f", the owner, to {here.what}", style=INK2)
        elif not here.waits:
            out.append("nothing", style=MUTED)
        else:
            first = True
            for party in here.waits:
                if party.branch is not None:
                    out.append(f"{short(party.branch)} → ", style=INK2)
                    continue
                if not first:
                    out.append(", ", style=MUTED)
                first = False
                if party.blue:
                    out.append("● ", style=ACCENT).append("you", style=f"bold {INK}")
                else:
                    out.append(party.human, style=INK)
                for agent in party.agents:
                    actor(agent)
            out.append(f" · to {here.what}", style=INK2)
        out.append("\n")

        out.append("review   ", style=MUTED)
        found = reviews(pr)
        said = {"approved": "approved", "changes": "asked for changes", "requested": "asked, no review yet",
                "rerequested": "asked to approve the fixes"}
        for i, (who, verdict) in enumerate(found):
            mark, color, _ = WORD[verdict]
            out.append("  " if i else "").append(mark + " ", style=color)
            out.append(f"{who} {said[verdict]}", style=INK2)
        if not found:
            out.append("nobody asked yet" if pr.is_pr else "no PR yet: a local branch", style=MUTED)
        out.append("\n")
        out.append("comments ", style=MUTED)
        if pr.threads[0]:
            out.append("◆ ", style=MUTED).append(f"{pr.threads[0]} to resolve", style=INK)
            out.append(f" of {pr.threads[1]}", style=MUTED)
        else:
            out.append("none to resolve", style=MUTED)
        icon, color, label, _ = STATUS[pr.state]
        out.append("      ").append(icon + " ", style=color).append(label + "\n", style=INK2)
        ci_icon, ci_color = CI[pr.ci]
        out.append("checks   ", style=MUTED).append(ci_icon + " ", style=ci_color)
        out.append(" · ".join(x for x in (pr.ci_note, pr.merge) if x) or "none yet",
                   style=INK if pr.ci == "fail" or "conflict" in pr.merge else INK2)
        out.append("\n")
        agents = self.world.on(pr)
        out.append("agents   ", style=MUTED)
        if not agents:
            out.append("none on it", style=MUTED)
        for i, agent in enumerate(agents):
            if i:
                out.append("\n         ")
            out.append(f"{agent.operator} › {agent.name}", style=INK)
            out.append(" reviewing" if agent.role == "review" and agent.working else
                       f" {agent.state}" if not agent.working else " at work", style=INK2)
            if agent.odd_name:
                out.append(" ≠ ", style=f"bold {CRIT}").append(f"named {agent.odd_name}", style=INK2)
            out.append(f" · {agent.note}", style=MUTED)
        return out

    def log_text(self) -> Text:
        out = Text()
        if not self.events:
            wait = max(0, (self.next_at - self.frame) // FPS)
            return out.append("events   ", style=MUTED).append(
                f"the first one lands in {wait}s · they loop, and e fires the next one now", style=MUTED)
        at, said = self.events[-1]
        seconds = (self.frame - at) // FPS
        out.append(f"{'now' if not seconds else str(seconds) + 's ago':<9}",
                   style=INK if seconds < 2 else MUTED)
        return out.append(said, style=INK if seconds < 2 else INK2)

    def menu_text(self) -> Text:
        """The repo menu: every repo, with a check mark on the ones on screen."""
        out = Text()
        out.append("repos on screen\n", style=f"bold {INK}")
        out.append("↑↓ move · space shows or hides one · enter closes\n\n", style=MUTED)
        longest = max(len(name) for name in self.world.repos)
        for i, name in enumerate(self.world.repos):
            here = i == self.menu_at
            on = name in self.shown
            line = Text()
            line.append("▸ " if here else "  ", style=INK)
            line.append("✓ " if on else "· ", style=INK if on else MUTED)
            line.append(f"{name:<{longest + 3}}", style=(f"bold {INK}" if on else INK2))
            yours, nudge = repo_status5(self.world, name)
            if yours:
                line.append("●", style=ACCENT).append(f"{yours} ", style=INK2)
            if nudge:
                line.append("○", style=MUTED).append(f"{nudge}", style=MUTED)
            if not yours and not nudge:
                line.append("nothing needs effort", style=MUTED)
            out.append_text(line).append("\n")
        out.append("\n" + (self.menu_note or "● what you can fix there   ○ people it waits on"), style=MUTED)
        return out

    # ---- keys --------------------------------------------------------------
    def action_move(self, step: int) -> None:
        if self.menu_open:
            self.menu_at = (self.menu_at + step) % len(self.world.repos)
            self.menu_note = ""
            self.draw()
            return
        numbers = self.numbers()
        if not numbers:
            return
        at = numbers.index(self.sel) if self.sel in numbers else 0
        self.sel = numbers[max(0, min(len(numbers) - 1, at + step))]
        self.draw()

    def action_next_effort(self) -> None:
        """Jump to the next thing that needs effort, in the list's order: most urgent first."""
        stops = [e.pr.number for e in needs_effort5(self.world, self.shown)]
        if not stops or self.menu_open:
            return
        at = stops.index(self.sel) if self.sel in stops else -1
        self.sel = stops[(at + 1) % len(stops)]
        self.draw()

    def action_focus(self, step: int) -> None:
        """Step the focus: everyone, a human with all their agents, the human alone, each agent."""
        if self.menu_open:
            return
        steps = focuses(self.world, self.shown)
        at = steps.index(self.focus) if self.focus in steps else 0
        self.focus = steps[(at + step) % len(steps)]
        self.draw()

    def action_clear(self) -> None:
        if self.menu_open:
            self.action_close_menu()
            return
        self.focus = Focus()
        self.draw()

    def action_menu(self) -> None:
        if self.menu_open:
            self.action_close_menu()
            return
        self.menu_open, self.menu_note = True, ""
        self.query_one("#menu", Static).styles.display = "block"
        self.draw()

    def action_close_menu(self) -> None:
        if not self.menu_open:
            return
        self.menu_open = False
        self.query_one("#menu", Static).styles.display = "none"
        self.draw()

    def toggle_repo(self) -> None:
        name = list(self.world.repos)[self.menu_at]
        if name in self.shown and len(self.shown) == 1:
            self.menu_note = "at least one repo stays on screen"
            return
        self.menu_note = ""
        shown = set(self.shown) ^ {name}
        self.shown = [repo for repo in self.world.repos if repo in shown]      # in the repos' own order
        numbers = self.numbers()
        if self.sel not in numbers:
            self.sel = numbers[0] if numbers else None
            self.top = 0
        self.gone.clear()
        self.sync(settle=True)

    def action_pause(self) -> None:
        if self.menu_open:                        # in the menu, space toggles a repo
            self.toggle_repo()
        else:
            self.paused = not self.paused
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


if __name__ == "__main__":
    LiveV5().run()
