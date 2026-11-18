# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""
The board: the main screen. Who, human or agent, is on which pull request, and what each
one waits on, drawn from GitHub's records (picture.py). If it is golden yellow, it is
still, and it is yours to get going.

Top to bottom:

  • THE TOP LINE. Every repository in the list, the ones on screen bold and underlined,
    each with ●n (what you can get moving there) and ○n (other people something there
    waits on). At the right, the key to the two thin bars each repository's line ends in
    (below). Resting the pointer on it says what they are.
  • THE CARDS. One per human: you, then everyone something waits on. A yellow dot on yours
    means things wait on you. The agents a human operates sit beneath their name. The
    background shows the focus. A team a review was asked of has a card too; a team you
    are on comes right after yours, with the yellow dot while its requests are yours to
    answer.
  • THE LIST, in two columns with a rule between them. Column 1 is who: owner | state |
    waits on, as many lines as a pull request needs. Column 2 is the tree: repository,
    base branch, the commit a pull request stands on, then the pull requests in stack
    order, with review (each reviewer's latest word, ◇ comments to resolve), checks,
    progress and how long it has been quiet: a bar under "m h d w", two cells to each of
    minutes, hours, days and weeks. The whole bar has the color of the unit the silence
    has reached, the color of that unit's letter. Only the weeks' color fades: at three
    months the bar is full and grey.
    A double rule starts a repository, a heavy one a base branch, a light one a commit.
    A repository's rule ends in two thin bars that share one row, for that repository
    alone: the upper one empties until it is next read on its timer, the lower one since
    GitHub last sent a webhook delivery about it (empty where it sends none).
    A list longer than its box scrolls: it follows the selected pull request, and the
    wheel, the page keys and the bar at its right edge move it freely, to every
    repository on screen, the ones with nothing open included.
  • THE BAND. What needs effort, most urgent first, and a drawer for the selected pull
    request: owner and waits on first, then GitHub's facts and its address.
  • THE TICKER. The last thing that happened: a webhook delivery, or what changed when
    GitHub was read again. A row that changed washes pale for a moment. A word about what
    the user just did (o is opening a page) takes its line for a few seconds, then gives
    it back.

Keys: up/down pick a pull request, and so does a click on one, on any of its lines in the
list or on its line where it needs effort; o (or O) opens its page on GitHub in the browser, page
up/down and home/end scroll the list (as the wheel does), n the next thing needing effort,
tab / shift+tab step the focus (everyone, a human with their agents, the human alone, each
agent; not f, which on the setup screen asks to forget the token),
escape back to everyone, m the repository menu (space shows or hides one; it is saved),
u read GitHub now, d fold the drawer, s the setup, q quit.

The board holds no data of its own: every draw builds the picture from what the app has
read. Nothing here talks to GitHub.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from rich.cells import cell_len
from rich.text import Text
from textual import events
from textual.binding import Binding
from textual.containers import Horizontal
from textual.css.query import NoMatches
from textual.screen import Screen
from textual.widgets import Static

from canvas import Canvas, Segments, cut, plain, seg_len, wash, write
from picture import (CHECK_MARK, CHECK_SAID, QUIET, SPIN, Agent, Effort, Focus, Key,
                     Person, Picture, Row, cells, counts, focuses, humans, in_focus, key_of, labels,
                     line_agents, mark, move_words, needs_effort, progress, quiet, quiet_bar, quiet_block,
                     quiet_scale, review_cell, reviewers, reviewing, tree)
from pulls import PullRequest, span
from theme import (AXIS, CRIT, DIVIDER, INK, INK2, MUTED, PLANE, RAISED, SURFACE, WAITING, WARN, darker)

BAR = 16                         # cells in each repository's two freshness bars
POLL_INK = INK2                  # the poll bar: neutral, it is the steady one
HOOK = "#199e70"                 # the webhook bar
TRACK = AXIS                     # the part of a bar that has drained
FLASH = ["#4a4a46", "#444441", "#3d3d3a", "#363633", "#2f2f2d", "#292927"]   # a wash, fading
FLASH_STEP = 0.17                # seconds each shade of the wash lasts
MARK_KEPT_S = 60.0               # how long a "this just changed" record is kept: far past the
                                 # wash, and past the three seconds the "changed" notes look back
DIM = "#5c5b56"                  # pull requests out of focus
STRIP = 5                        # the most rows the strip of cards takes: a name, four agents
PER_COL = 4                      # agents beneath a human before a card takes another column
BAND = 10                        # rows of the bottom band: the list and the drawer
X_OWNER, X_STATE, X_WAITS, X_RULE, X_TREE = 1, 18, 33, 51, 53
ORDER = {"at its prompt": 0, "working": 1, "waiting": 2}
HINTS = ["↑↓ pull request", "o open on GitHub", "PgUp PgDn scroll", "n next needing effort", "tab focus",
         "esc everyone", "m repos", "u update", "d drawer", "s setup", "q quit"]
# When the window is too narrow for every hint, these are left out, the first first. The way
# back to the setup and out of the board always stay: they are what a lost user looks for.
LEFT_OUT_FIRST = ["d drawer", "u update", "m repos", "esc everyone", "tab focus", "PgUp PgDn scroll",
                  "n next needing effort", "o open on GitHub", "↑↓ pull request"]
HINT_GAP = "   "
THUMB, TRACK_LINE = "┃", "│"     # the list's scroll bar: its thumb, and the track it runs in


TREE_ROOM = 16                   # the tree's least: its connectors, the icon, the number, a little title


def columns(width: int) -> tuple[int | None, int | None, int | None, bool, int]:
    """Where review, checks, progress and quiet start in a list this wide, and whether
    reviewers are named. Column 1 and the tree never move; a narrow window gives up
    progress first, then the reviewers' names, and with the names the quiet bar, which
    becomes a single block; then the review column, then the checks (the drawer still
    shows both), so that no column is ever drawn over the tree. None is a column given up."""
    x_quiet = width - QUIET
    x_prog: int | None = x_quiet - 22
    x_ci: int | None = x_prog - 4
    x_review: int | None = x_ci - 24
    names = True
    if x_review - X_TREE < 32:
        x_prog, x_ci = None, x_quiet - 5
        x_review = x_ci - 24
        if x_review - X_TREE < 30:
            names, x_quiet = False, width - 5
            x_ci = x_quiet - 4
            x_review = x_ci - 9
            if x_review - X_TREE < TREE_ROOM:
                x_review = None
                if x_ci - X_TREE < TREE_ROOM:
                    x_ci = None
    return x_review, x_ci, x_prog, names, x_quiet


def title_end(cols: tuple) -> int:
    """Where a row's title must stop: the first column after the tree that is drawn."""
    x_review, x_ci, x_prog, _, x_quiet = cols
    return next(x for x in (x_review, x_ci, x_prog, x_quiet) if x is not None)


def hint_line(width: int) -> str:
    """The key hints that fit in `width` cells, in their order, the least needed left out
    first (LEFT_OUT_FIRST). Narrower than "s setup   q quit", the line is cut as text is."""
    shown = list(HINTS)
    for hint in LEFT_OUT_FIRST:
        if cell_len(HINT_GAP.join(shown)) <= width:
            break
        shown.remove(hint)
    return HINT_GAP.join(shown)


def thumb_size(rows: int, total: int) -> int:
    """How many cells the thumb takes, for a list of `total` lines in a box of `rows`:
    the share of the list on screen, wherever in it the box is."""
    if total <= rows:
        return rows
    last = total - rows
    # Room on the track for the thumb at the top, at the bottom and, once the list can be
    # anywhere between (two lines or more out of sight), somewhere in the middle too.
    return max(1, min(rows - min(last, 2), round(rows * rows / total)))


def thumb(rows: int, top: int, total: int) -> tuple[int, int]:
    """Where the scroll bar's thumb starts and how many cells it takes, for a list of
    `total` lines that shows `rows` of them from line `top`. The thumb touches an end of
    its track only when the list is at that end, so a line out of sight always shows (in a
    box of three rows or more: one of two rows has no middle to show)."""
    if total <= rows:
        return 0, rows
    last = total - rows                          # the furthest the list scrolls
    size = thumb_size(rows, total)
    top = max(0, min(top, last))
    start = round((rows - size) * top / last)
    if rows - size >= 2:
        if top > 0:
            start = max(1, start)
        if top < last:
            start = min(rows - size - 1, start)
    return start, size


# ---- the strip: one card per human, their agents beneath -------------------------------

@dataclass
class Card:
    human: str                                   # the login, lower case
    head: Segments                               # the name line
    cols: list[list[tuple[Segments, tuple | None]]]   # columns of (agent line, the agent's key)
    widths: list[int]
    width: int
    x: int = 0


def agent_line(agent: Agent, yours: bool) -> Segments:
    """ "⠋ #13": the agent's mark and the pull request it is on. Yellow only on an agent of
    yours at its prompt; someone else's is grey."""
    said = f"#{agent.number}"
    if agent.working:
        return [(SPIN[0] + " ", INK), (said, INK2)]
    if agent.state == "at its prompt":
        return [("▷ ", WAITING if yours else MUTED), (said, f"bold {INK}" if yours else INK2)]
    return [("· ", MUTED), (said, MUTED)]


def count_marks(yours: int, nudge: int, gap: str = " ") -> Segments:
    """A repository's counts, drawn the same wherever they show: ●n, what you can get
    moving there, and ○n, the other people something there waits on, each after `gap`.
    Nothing for a count of 0; what to show when both are is the caller's to say."""
    out: Segments = []
    if yours:
        out += [(gap + "●", WAITING), (str(yours), INK)]
    if nudge:
        out += [(gap + "○", MUTED), (str(nudge), MUTED)]
    return out


def remember_change(marks: dict, key: object, now: float) -> None:
    """Note that `key` just changed, in one of the "this just changed" records, and let go
    of what changed longer ago than MARK_KEPT_S: nothing looks that far back, and the board
    stays open for days."""
    for old in [k for k, at in marks.items() if now - at > MARK_KEPT_S]:
        del marks[old]
    marks[key] = now


def head_line(person: Person) -> Segments:
    """A card's name line: the name alone. On yours, and on a team's you are on, the yellow
    dot says things wait on you. The name sits where the agents' pull requests sit beneath
    it, dot or no dot."""
    if person.dot:
        return [("● ", WAITING), (person.name, f"bold {INK}")]
    return [("  ", MUTED), (person.name, INK if person.yours else INK2)]


def make_card(person: Person, max_cols: int) -> Card:
    """A human and the agents they operate: the ones at a prompt first, then the ones at
    work, then the ones that wait. What does not fit is folded, and the card says so."""
    agents = sorted(person.agents, key=lambda a: ORDER[a.state])
    slots = max(1, max_cols) * PER_COL
    folded: list[Agent] = []
    if len(agents) > slots:
        agents, folded = agents[:slots - 1], agents[slots - 1:]
    entries: list[tuple[Segments, tuple | None]] = [(agent_line(a, person.viewer), a.key) for a in agents]
    if folded:
        entries.append(([("  ", MUTED), (f"{len(folded)} more, folded", MUTED)], None))
    cols = [entries[at:at + PER_COL] for at in range(0, len(entries), PER_COL)]
    widths = [max(seg_len(line) for line, _ in col) for col in cols]
    head = head_line(person)
    return Card(person.login.lower(), head, cols, widths,
                max(seg_len(head), sum(widths) + 2 * max(0, len(cols) - 1)))


def lay_cards(people: list[Person], room: int) -> list[Card]:
    """Lay the cards out so the strip fits, folding your card to fewer columns when it does
    not."""
    def need(person: Person) -> int:
        return max(1, -(-len(person.agents) // PER_COL))

    def build(yours: int) -> list[Card]:
        return [make_card(p, min(need(p), yours) if p.viewer else 1) for p in people]

    def span(cards: list[Card]) -> int:
        return sum(card.width for card in cards) + 3 * max(0, len(cards) - 1)

    most = max([need(p) for p in people if p.viewer] or [1])
    tries = list(range(most, 0, -1))
    cards = next((made for made in map(build, tries) if span(made) <= room), None)
    if cards is None:                            # nothing fits: the narrowest, folding as little as it can
        cards = build(min(tries, key=lambda n: (span(build(n)), -n)))
    x = 0
    for card in cards:
        card.x = x
        x += card.width + 3
    return cards


@dataclass
class Line:
    """One line of the list: a rule (repo, base, commit), a note, or one line of a pull request."""
    row: Row
    part: int = 0                                # which line of its pull request this is
    cells: tuple[Segments, Segments, Segments] | None = None      # owner, state, waits on
    lead: str = ""                               # the tree connectors on this line


def fresh_key() -> Text:
    """The key to the two bars at the end of each repository's line, for the top line."""
    return (Text().append("▀", style=POLL_INK).append(" read  ", style=MUTED)
            .append("▄", style=HOOK).append(" webhook", style=MUTED))


FRESH_KEY_W = len(fresh_key().plain)


def fresh_tip() -> Text:
    """What the two freshness bars are, for the pointer resting on their key."""
    tip = Text()
    tip.append("Each repository's line ends in two thin bars.\n\n", style=INK2)
    tip.append("▀", style=POLL_INK).append(" upper · the next read\n", style=f"bold {INK}")
    tip.append("Empties at a steady pace. When it is empty, that repository is read again. Any "
               "read of it fills the bar: a webhook's, u, or the timer's own. Where GitHub sends "
               "webhooks, the timer is a slow one.\n\n", style=INK2)
    tip.append("▄", style=HOOK).append(" lower · webhooks\n", style=f"bold {INK}")
    tip.append("Refills each time GitHub sends a change there. If it keeps emptying, the "
               "webhooks have gone quiet. Empty for a repository GitHub sends none for.", style=INK2)
    return tip


def fresh_bar(read: float, hook: float) -> Segments:
    """Two thin bars in one row of BAR cells, for one repository: `read` and `hook` are how
    much of each is left, from 1 down to 0 (Frolic.freshness). Each cell is an upper half
    block: its ink is the read bar, its background the webhook bar. What is left sits
    against the right edge, and blocks leave from the left. So the row is at most three runs
    of like cells, from the left: where neither bar reaches, where only the longer one
    does, and where both do."""
    def cells(part: float) -> int:
        return max(0, min(BAR, -int(-part * BAR // 1)))       # rounded up: full until a block's time is gone

    r, h = cells(read), cells(hook)
    runs = [(BAR - max(r, h), TRACK, TRACK),
            (abs(r - h), POLL_INK if r > h else TRACK, HOOK if h > r else TRACK),
            (min(r, h), POLL_INK, HOOK)]
    return [("▀" * n, f"{ink} on {back}") for n, ink, back in runs if n]


class ListBar(Static):
    """The list's scroll bar. The thumb is dragged from wherever it was taken hold of; a
    press on the track beside it brings the middle of the thumb there first. Not named
    ScrollBar: Textual's styles pick widgets by class name, and its own scroll bar is
    called that. Its geometry is thumb(), not Textual's, which keeps no rule about the ends."""

    ALLOW_SELECT = False          # a press here is a drag of the bar, never the start of a text
                                  # selection: Textual decides that on the press, before on_mouse_down

    def on_mouse_down(self, event: events.MouseDown) -> None:
        if event.button != 1:                    # only the main button takes hold of the bar
            return
        self.capture_mouse()
        self.screen.press_bar(event.y)
        event.stop()

    def on_mouse_move(self, event: events.MouseMove) -> None:
        if self.app.mouse_captured is self:
            self.screen.drag(event.y)
            event.stop()

    def on_mouse_up(self, event: events.MouseUp) -> None:
        self.release_mouse()
        self.screen.grab = None
        event.stop()


class Board(Screen):
    CSS = f"""
    Board {{ background: {PLANE}; color: {INK}; layers: base overlay; }}
    #topline {{ height: 1; margin: 0 2; }}
    #tabs {{ width: 1fr; height: 1; text-wrap: nowrap; text-overflow: ellipsis; }}
    #fresh {{ width: {FRESH_KEY_W}; height: 1; margin: 0 0 0 2; }}
    Tooltip {{ background: {RAISED}; color: {INK2}; }}
    #toprule {{ height: 1; margin: 0 1; }}
    #strip {{ height: 1; max-height: {STRIP}; margin: 0 1; }}
    #striprule {{ height: 1; margin: 0 1; }}
    #head {{ height: 1; margin: 0 1; }}
    /* 100% wide with padding, not 1fr with a margin: beside a 1fr height, Textual sizes a
       1fr width by the widest margin among the row's neighbours, which is not its own. */
    #maprow {{ width: 100%; height: 1fr; padding: 0 0 0 1; }}
    #map {{ width: 1fr; height: 100%; }}
    #scroll {{ width: 1; height: 100%; }}
    #band {{ height: {BAND}; margin: 0 1; }}
    .pane {{ height: {BAND}; background: {SURFACE}; padding: 1 2;
             text-wrap: nowrap; text-overflow: ellipsis; overflow: hidden; }}
    #effort {{ width: 1fr; margin: 0 1 0 0; }}
    #drawer {{ width: 66; }}
    #events {{ height: 1; margin: 0 2; text-wrap: nowrap; text-overflow: ellipsis; }}
    #boardhints {{ height: 1; margin: 0 2; color: {MUTED}; text-wrap: nowrap; text-overflow: ellipsis; }}
    #menu {{ layer: overlay; display: none; width: 64; height: auto; margin: 2 0 0 6;
             padding: 1 2; background: {RAISED}; border: round {DIVIDER}; }}
    """
    BINDINGS = [
        Binding("up", "move(-1)", show=False), Binding("down", "move(1)", show=False),
        Binding("pageup", "page(-1)", show=False), Binding("pagedown", "page(1)", show=False),
        Binding("home", "edge(-1)", show=False), Binding("end", "edge(1)", show=False),
        Binding("n", "next_effort", show=False),
        Binding("o", "open_pull", show=False), Binding("O", "open_pull", show=False),
        # tab, not f: on the setup screen f asks to forget the GitHub token, and a key that
        # is harmless on one screen should not be the destructive one on the other
        Binding("tab", "focus(1)", show=False), Binding("shift+tab", "focus(-1)", show=False),
        Binding("escape", "clear", show=False), Binding("m", "menu", show=False),
        Binding("enter", "close_menu", show=False), Binding("space", "toggle_repo", show=False),
        Binding("u", "app.update", show=False), Binding("d", "drawer", show=False),
        Binding("s", "app.setup", show=False), Binding("q", "app.quit", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.pic = Picture([], {})
        self.efforts: list[Effort] = []
        self.drawn_from: tuple = ()               # what self.pic was built from (look)
        self.hints_shown = ""                     # the key hints as last fitted to the window
        self.sel: Key | None = None               # the selected pull request
        self.focus = Focus()
        self.top = 0                              # first line of the list on screen
        self.roam = False                         # the list was scrolled by hand: it stays where it was put
        self.grab: tuple[int, int] | None = None  # the scroll bar is held: (the row it was taken at, top then)
        self.page = 1                             # lines of the list its box holds
        self.where = (0, 0, 0)                    # (first line shown, last line shown, lines in all)
        self.effort_rows: list[Key] = []          # the pull request on each line of what needs effort,
                                                  # as last drawn, top to bottom: a click finds it there
        self.unseen: tuple[list[str], list[str]] = ([], [])
        self.menu_open = False
        self.menu_at = 0
        self.drawer_open = True
        self.drawer_w = 66
        self.card_lines: list[int] = []           # where the lines between the humans' cards stand
        self.strip_rows = 1                       # rows the strip takes now: the names, and the agents beneath
        self.card_sig: dict[str, str] = {}        # what each card's name line said at the last look
        self.card_flash: dict[str, float] = {}    # a card whose name line just changed
        self.effort_seen: set[Key] | None = None  # what needed effort at the last look
        self.entered: dict[Key, float] = {}       # what just joined the needs-effort list

    def compose(self):
        with Horizontal(id="topline"):
            yield Static(id="tabs")
            yield Static(fresh_key(), id="fresh")     # the key to each repository's two bars
        yield Static(id="toprule")
        yield Static(id="strip")
        yield Static(id="striprule")
        yield Static(id="head")
        with Horizontal(id="maprow"):
            yield Static(id="map")
            yield ListBar(id="scroll")           # in the window's last column, beside the list
        with Horizontal(id="band"):
            yield Static(id="effort", classes="pane")
            yield Static(id="drawer", classes="pane")
        yield Static(id="events")
        yield Static(hint_line(9999), id="boardhints")             # drawn to the width (draw)
        yield Static(id="menu")

    def on_mount(self) -> None:
        self.query_one("#fresh", Static).tooltip = fresh_tip()
        self.fit()
        self.set_interval(0.25, self.animate_tick)      # washes fade and the bars drain between the app's ticks
        self.draw()
        self.call_after_refresh(self.draw)

    def on_resize(self) -> None:
        self.fit()
        self.draw()
        self.call_after_refresh(self.draw)

    def on_screen_resume(self) -> None:
        self.draw()
        self.call_after_refresh(self.draw)

    def animate_tick(self) -> None:
        if self.is_current:
            self.draw()

    def fit(self) -> None:
        """The drawer gives way to the list in a narrow window."""
        self.drawer_w = 66 if self.size.width >= 140 else max(30, self.size.width * 2 // 5)
        if self.drawer_open:
            try:
                self.query_one("#drawer", Static).styles.width = self.drawer_w
            except NoMatches:
                return

    # ---- the picture, from what the app has read ----------------------------------------
    def look(self) -> None:
        """Build the picture afresh, when what it is drawn from has changed, and notice what
        changed on the cards and the list. The board is drawn five times a second; most of
        those times nothing has, and the picture from before stands."""
        app = self.app
        names = [r.name for r in app.store.shown()]
        notes: dict[str, str] = {}
        for name in names:
            low = name.lower()
            if low in app.pull_trouble:
                notes[low] = f"could not be read: {app.pull_trouble[low]}"
            elif low not in app.pulls:
                notes[low] = "reading…" if low in app.fetching else "not read yet: u reads GitHub now"
        viewer = app.identity.login if app.identity is not None else ""
        # The readings themselves are kept and compared as objects: a new reading is a new
        # object. The minute is in it because how long a pull request has been quiet moves on.
        # The viewer's teams arrive on their own, after the sign-in: they are in it so that the
        # picture is built again when they do. Everything but the readings is compared by value.
        teams, held = app.teams_now()
        drawn_from = (names, notes, viewer, repr(sorted(app.agents.items())), int(time.time() // 60),
                      teams, held, list(app.pulls.items()))
        if self.same_sources(drawn_from):             # the focus and selection are still on it, too
            return
        self.drawn_from = drawn_from
        self.pic = Picture(names, app.pulls, viewer, app.agents, notes, teams=teams, held=held)
        self.efforts = needs_effort(self.pic)
        now = time.monotonic()
        cards = {p.login.lower(): plain(head_line(p)) for p in humans(self.pic)}
        for login, said in cards.items():
            if login in self.card_sig and self.card_sig[login] != said:
                remember_change(self.card_flash, login, now)
        self.card_sig = cards
        keys = {key_of(e.pull) for e in self.efforts}
        if self.effort_seen is not None:
            for key in keys - self.effort_seen:
                remember_change(self.entered, key, now)
        self.effort_seen = keys
        self.keep_in_bounds()

    def same_sources(self, drawn_from: tuple) -> bool:
        if not self.drawn_from or self.drawn_from[:-1] != drawn_from[:-1]:
            return False
        before, now = self.drawn_from[-1], drawn_from[-1]
        return len(before) == len(now) and all(k == j and a is b for (k, a), (j, b) in zip(before, now))

    def keep_in_bounds(self) -> None:
        """The focus and the selection stay on something there is. Only a new picture can
        take it away: the keys only ever pick from the picture's own lists."""
        if self.focus not in focuses(self.pic):                # who was in focus is gone
            self.focus = Focus()
        order = self.keys()
        if self.sel not in order:
            # Not a pick (see pick): a list scrolled by hand stays where it was put, though
            # the selection, and the drawer with it, move to the first pull request.
            self.sel = order[0] if order else None

    def keys(self) -> list[Key]:
        """The pull requests on screen, in list order."""
        return [key_of(row.pull) for row in tree(self.pic) if row.pull is not None]

    def spin(self, offset: int = 0) -> str:
        return SPIN[(int(time.monotonic() * 8) + offset) % len(SPIN)]

    def shade(self, since: float) -> str | None:
        """The wash for something that changed `since` seconds ago, or None once it has faded."""
        step = int(since / FLASH_STEP)
        return FLASH[step] if 0 <= step < len(FLASH) else None

    def flashed(self, key: Key) -> float:
        """Seconds since this pull request last changed on a re-read (large when it never did)."""
        return time.time() - self.app.flashes.get(key, 0.0)

    def ref(self, pull: PullRequest) -> str:
        """ "#12", or "name#12" when more than one repository is on screen."""
        if len(self.pic.names) < 2:
            return f"#{pull.number}"
        return f"{labels(self.pic.names).get(pull.repo, pull.repo)}#{pull.number}"

    # ---- drawing -------------------------------------------------------------------------
    def draw(self) -> None:
        if not self.is_mounted:
            return
        try:
            self.look()
            self.query_one("#tabs", Static).update(self.top_line())
            self.query_one("#strip", Static).update(self.strip())      # sets where the card lines stand
            self.query_one("#toprule", Static).update(self.card_rule("#toprule", "┬"))
            self.query_one("#striprule", Static).update(self.card_rule("#striprule", "┴", X_RULE))
            self.list_parts()
            self.query_one("#effort", Static).update(self.effort())
            self.query_one("#drawer", Static).update(self.drawer())
            self.query_one("#events", Static).update(self.ticker())
            hints = self.query_one("#boardhints", Static)
            fitted = hint_line(hints.size.width or self.size.width - 4)
            if fitted != self.hints_shown:                         # only when the width calls for it
                self.hints_shown = fitted
                hints.update(fitted)
            if self.menu_open:
                self.query_one("#menu", Static).update(self.menu_text())
        except NoMatches:                         # a last tick while the app is quitting
            return

    def list_parts(self) -> None:
        self.query_one("#map", Static).update(self.map())          # sets what the header reports
        self.query_one("#scroll", Static).update(self.scroll_bar())
        self.query_one("#head", Static).update(self.head())

    def draw_list(self) -> None:
        """Only what scrolling changes: the list, its bar, and the header that says which
        rows show. A trackpad sends dozens of wheel ticks a second; the cards, the panes and
        the rest of the board stay as they are until something else changes them."""
        if not self.is_mounted:
            return
        try:
            self.list_parts()
        except NoMatches:                         # a last tick while the app is quitting
            return

    def top_line(self) -> Text:
        """Every repository in the list: the ones on screen bold and underlined with ●n (what
        you can get moving there) and ○n (other people something there waits on)."""
        repos = self.app.store.repos
        out = Text()
        if not repos:
            out.append("agent-frolic", style=f"bold {INK}")
            return out.append("   no repositories yet", style=MUTED)
        short = labels([r.name for r in repos])
        for repo in repos:
            out.append(short[repo.name], style=f"bold underline {INK}" if repo.shown else MUTED)
            if repo.shown and repo.name.lower() in self.pic.pulls:
                for text, style in count_marks(*counts(self.pic, repo.name)) or [(" ·", AXIS)]:
                    out.append(text, style=style)
            out.append("      ")
        return out

    def strip(self) -> Text:
        """Humans across the top, each with the agents they operate beneath them. What is in
        focus has the raised background, and that is the only place the focus is shown:
        every card when everyone is in focus; one human's whole card for that human with
        their agents; only the name for the human alone; only its own line for one agent."""
        strip = self.query_one("#strip", Static)
        width = max(40, strip.size.width or self.size.width - 2)
        people = humans(self.pic)
        cards = lay_cards(people, width - 2) if people else []
        rows = 1 + max((len(col) for card in cards for col in card.cols), default=0)
        if rows != self.strip_rows:                            # the strip is only as tall as the agents need
            self.strip_rows = rows
            strip.styles.height = rows
            self.call_after_refresh(self.draw)
        canvas = Canvas(width, rows)
        if not people:
            self.card_lines = []
            canvas.put(1, 0, "nobody yet: the people something waits on show here", MUTED)
            return canvas.text()
        kind = self.focus.kind
        now = time.monotonic()
        turn = 0
        for card in cards:
            x = 1 + card.x
            picked = kind in ("crew", "human") and self.focus.human == card.human
            whole = kind == "all" or (kind == "crew" and picked)   # the whole card is in focus
            head = ([(text, f"bold underline {INK}" if text.strip() and text.strip() != "●" else style)
                     for text, style in card.head] if picked else card.head)
            write(canvas, x, 0, head, max(0, width - x))
            lines: list[tuple[int, int, int, bool, float]] = [
                (0, x, card.width, picked, now - self.card_flash.get(card.human, -999.0))]
            cx = x
            for col, col_w in zip(card.cols, card.widths):
                for i, (line, key) in enumerate(col):
                    turn += 3
                    turned = [(self.spin(turn) + " " if len(text) == 2 and text[0] in SPIN else text, style)
                              for text, style in line]             # each agent's dots turn on their own
                    write(canvas, cx, 1 + i, turned, max(0, width - cx))
                    lines.append((1 + i, cx, col_w, kind == "agent" and key is not None
                                  and self.focus.agent == key, 999.0))
                cx += col_w + 2
            if whole:                                              # backgrounds go on after the words
                for y in range(rows):
                    wash(canvas, y, x - 1, x + card.width + 1, RAISED)
            for y, at, w, lit, since in lines:
                shade = self.shade(since)
                if shade:
                    wash(canvas, y, at - 1, at + w + 1, shade)
                elif lit:
                    wash(canvas, y, at - 1, at + w + 1, RAISED)
        self.card_lines = [x for card in cards[:-1] if (x := 1 + card.x + card.width + 1) < width]
        for x in self.card_lines:                              # a line between one human and the next
            for y in range(rows):
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

    def lines(self) -> list[Line]:
        """The list, line by line: rules for repo, base and commit, notes, and each pull
        request's lines. A pull request's extra lines keep the tree's connectors going, so
        the tree never breaks."""
        out: list[Line] = []
        for i, row in enumerate(tree(self.pic)):
            if row.pull is None:
                out.append(Line(row, lead=row.lead))
                continue
            tail = (row.lead[:-2] + ("│ " if row.lead.endswith("├ ") else "  ")
                    + ("│ " if row.pull.children else "  "))
            for part, three in enumerate(cells(self.pic, row.pull, self.spin(i * 3))):
                out.append(Line(row, part, three, row.lead if part == 0 else tail))
        return out

    def empty_words(self) -> str:
        """What to say in place of the list when there is nothing to list."""
        app = self.app
        if app.token_state == "none":
            return "No GitHub token yet. s opens the setup, where t pastes one."
        if app.token_state == "bad":
            return "GitHub did not accept the token. s opens the setup, where t pastes another."
        if app.token_state == "unchecked":
            return (f"GitHub could not be reached to check the token. Trying again every "
                    f"{app.TOKEN_RETRY_S} seconds; u tries now.")
        if not app.store.repos:
            return "No repositories yet. s opens the setup, where a adds one."
        if not app.store.shown():
            return "No repository is on screen. m shows one."
        return ""

    def map(self) -> Text:
        box = self.query_one("#map", Static).size
        width = max(70, box.width or self.size.width - 2)
        height = max(1, box.height)        # the rows really there: the page keys, End and the bar
                                           # all count by it, so in the smallest window too
        lines = self.lines()
        canvas = Canvas(width, height)
        cols = columns(width)
        self.page = height
        empty = self.empty_words()
        if empty:
            self.where, self.unseen = (0, 0, 0), ([], [])
            for y in range(height):
                canvas.put(X_RULE, y, "│", DIVIDER)
            canvas.put(X_TREE, 1, cut(empty, width - X_TREE - 1), INK2)
            return canvas.text()

        mine = [i for i, line in enumerate(lines)
                if line.row.pull is not None and key_of(line.row.pull) == self.sel]
        if mine and not self.roam:                             # keep the selected pull request in view
            # with two lines above it and one below, where the box has room for them
            above, below = (2, 1) if height >= len(mine) + 3 else (0, 0)
            if mine[0] - above < self.top:
                self.top = mine[0] - above
            if mine[-1] + below >= self.top + height:
                self.top = mine[-1] + below + 1 - height
            if mine[0] <= 3:
                self.top = 0
            elif all(line.row.pull is None for line in lines[mine[-1] + 1:]):
                # the last pull request: what follows it (repositories with nothing open)
                # comes into view with it, as far as it can while the pull request shows
                self.top = max(self.top, min(len(lines) - height, mine[0] - above))
            if not self.top <= mine[0] < self.top + height:    # however short the box, it shows
                self.top = mine[0]
        self.top = max(0, min(self.top, len(lines) - height))
        if len(lines) <= height:                               # the clamp above has put it at the top
            self.roam = False                                  # it all fits: there is nowhere to roam
        self.where = (self.top + 1, min(len(lines), self.top + height), len(lines))
        self.unseen = ([], [])                                 # what just changed, above and below the box
        for i, line in enumerate(lines):
            pull = line.row.pull
            if pull is None or line.part or 0 <= i - self.top < height:
                continue
            if self.flashed(key_of(pull)) < 3:
                self.unseen[i >= self.top + height].append(f"#{pull.number}")

        for i, line in enumerate(lines):
            y = i - self.top
            if not 0 <= y < height:
                continue
            row, pull = line.row, line.row.pull
            if row.kind == "note":
                self.draw_note(canvas, y, row, width)
                continue
            if pull is None:
                self.draw_rule(canvas, y, row, width)
                continue
            self.draw_pull(canvas, y, line, cols, width)
            chosen = key_of(pull) == self.sel
            if not chosen and not in_focus(self.pic, pull, self.focus):
                for x in range(width):                         # out of focus: dimmed where it stands
                    if x != X_RULE:
                        ink = canvas.cells[y][x][1].split(" ")[-1]
                        faded = x >= cols[4] and ink.startswith("#")       # a quiet bar is not made lighter
                        canvas.cells[y][x] = (canvas.cells[y][x][0], darker(ink, DIM) if faded else DIM)
            if chosen:
                wash(canvas, y, 0, width, RAISED)
                canvas.put(0, y, "▌", f"{INK} on {RAISED}")
            shade = self.shade(self.flashed(key_of(pull)))
            if shade:                                          # it changed on the last read: it settles
                wash(canvas, y, 1, X_RULE, shade)
                wash(canvas, y, X_TREE - 1, width, shade)
        for y in range(max(0, len(lines) - self.top), height):
            canvas.put(X_RULE, y, "│", DIVIDER)                # the rule runs on under a short list
        return canvas.text()

    def scroll_bar(self) -> Text:
        """The bar beside the list: a thumb for the lines on screen, in a track as tall as
        the box. Empty while the whole list fits."""
        first, _, total = self.where
        rows = self.page
        if total <= rows:
            return Text("")
        start, size = thumb(rows, first - 1, total)
        out = Text()
        for y in range(rows):
            inside = start <= y < start + size
            out.append(THUMB if inside else TRACK_LINE, style=INK2 if inside else AXIS)
            if y < rows - 1:
                out.append("\n")
        return out

    def scroll(self, by: int) -> None:
        """Scroll the list by hand. From then on it stays where it was put, the selected
        pull request in view or not, until a pull request is picked again."""
        if self.menu_open or self.where[2] <= self.page:
            return
        self.roam = True
        self.top += by                                         # map() keeps it within the list
        self.draw_list()

    def press_bar(self, y: int) -> None:
        """The scroll bar was pressed at its row `y`. On the thumb nothing moves: it is
        taken hold of there, and the drag that follows moves the list by as much as the
        pointer moves. On the track beside it, the middle of the thumb goes to `y` first."""
        self.grab = None
        total, rows = self.where[2], self.page
        if self.menu_open or total <= rows:
            return
        start, size = thumb(rows, self.top, total)
        if not start <= y < start + size:
            self.roam = True
            self.top = round((y - (size - 1) / 2) * (total - rows) / max(1, rows - size))
            self.draw_list()
        self.grab = (y, self.top)

    def drag(self, y: int) -> None:
        """The held scroll bar was dragged to its row `y`: the list moves as far as the
        thumb has, from where it was taken hold of."""
        total, rows = self.where[2], self.page
        if self.grab is None or self.menu_open or total <= rows:
            return
        at, top = self.grab
        size = thumb_size(rows, total)
        moved = round(top + (y - at) * (total - rows) / max(1, rows - size))
        if moved != self.top:
            self.roam = True
            self.top = moved
            self.draw_list()

    def over_list(self, event: events.MouseEvent) -> bool:
        """Whether the pointer is over the list or its scroll bar: the wheel scrolls the list
        there and nowhere else, not over the cards, the needs-effort pane or the drawer.
        Told by where on the screen the pointer is. Not by `event.widget`: Textual names the
        widget under the pointer on a click only, and a turn of the wheel, as the terminal
        reports it, names none."""
        return self.query_one("#maprow").region.contains(event.screen_x, event.screen_y)

    def on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        if self.over_list(event):
            self.scroll(max(1, round(self.app.scroll_sensitivity_y)))
            event.stop()

    def on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        if self.over_list(event):
            self.scroll(-max(1, round(self.app.scroll_sensitivity_y)))
            event.stop()

    def draw_rule(self, canvas: Canvas, y: int, row: Row, width: int) -> None:
        """A rule across both columns, carrying its name: double at a repo, heavy at a base,
        light at a commit (with its short hash). It crosses the rule between the columns.

        A repository's rule says how many pull requests are open there (the row carries
        the count), and for one with none that is the only place the board says so: tree()
        gives it no line beneath. So in a window too narrow for all of it the count is what
        stays: first the "most recent shown" goes, then ●n ○n (the top line has those too),
        then the name is cut short."""
        line, cross = {"repo": ("═", "╪"), "base": ("━", "┿"), "commit": ("─", "┼")}[row.kind]
        canvas.put(0, y, line * width, DIVIDER)
        canvas.put(X_RULE, y, cross, DIVIDER)
        room = max(0, width - X_TREE)
        parts: Segments = [(" ", "")]
        if row.kind == "repo":
            said: Segments = []
            if row.total is not None:
                marks = count_marks(*counts(self.pic, row.repo), gap="  ")
                opened: Segments = [(f"  {row.total} open", MUTED)]
                more: Segments = ([(f", the {row.listed} most recent shown", MUTED)]
                                  if row.total > row.listed else [])
                beside = room - 2 - cell_len(row.label)        # what the whole name leaves, between two spaces
                said = next((all_ for all_ in (marks + opened + more, marks + opened) if seg_len(all_) <= beside),
                            opened)
            parts += [(cut(row.label, room - 2 - seg_len(said)), f"bold {INK}")] + said
        elif row.kind == "base":
            parts += [(row.lead, AXIS), (row.label, f"bold {INK}")]
        else:
            parts += [(row.lead[:-2], AXIS), (row.lead[-2:], MUTED), (row.sha, INK2)]
            if row.label:
                parts += [(" " + row.label, MUTED)]
        parts += [(" ", "")]
        write(canvas, X_TREE - 1, y, parts, room)
        at = width - BAR - 2                                   # the repository's two bars, at the rule's end
        if row.kind == "repo" and at >= X_TREE - 1 + seg_len(parts):
            write(canvas, at, y, [(" ", ""), *fresh_bar(*self.app.freshness(row.repo)), (" ", "")], BAR + 2)

    def draw_note(self, canvas: Canvas, y: int, row: Row, width: int) -> None:
        """A line under a repository saying why it has no pull requests to show."""
        canvas.put(X_RULE, y, "│", DIVIDER)
        trouble = row.label.startswith("could not be read")
        if trouble:
            canvas.put(X_TREE + 2, y, "✗", CRIT)
        canvas.put(X_TREE + 4, y, cut(row.label, width - X_TREE - 5), INK if trouble else MUTED)

    def draw_pull(self, canvas: Canvas, y: int, line: Line, cols: tuple, width: int) -> None:
        x_review, x_ci, x_prog, names, x_quiet = cols
        pull = line.row.pull
        owner, state, waits = line.cells
        write(canvas, X_OWNER, y, owner, X_STATE - X_OWNER - 1)        # column 1: who
        write(canvas, X_STATE, y, state, X_WAITS - X_STATE - 1)
        write(canvas, X_WAITS, y, waits, X_RULE - X_WAITS - 1)
        canvas.put(X_RULE, y, "│", DIVIDER)
        canvas.put(X_TREE, y, line.lead, AXIS)                         # column 2: the tree
        x = X_TREE + len(line.lead)
        if line.part:
            said: Segments = [(move_words(self.pic, pull), INK2)] if line.part == 1 else []
            for agent in line_agents(self.pic, pull)[line.part]:       # what the agent on this line is doing
                if agent.note:
                    said += [(" · " if said else "", AXIS), (agent.note, MUTED)]
            write(canvas, x, y, said, max(0, width - x - 1))
            return
        icon, color, _ = mark(self.pic, pull)
        canvas.put(x, y, icon, color)
        number = f"#{pull.number}"
        canvas.put(x + 2, y, number, f"bold {INK}")
        canvas.put(x + 3 + len(number), y, cut(pull.title, title_end(cols) - 1 - (x + 3 + len(number))), INK2)
        if x_review is not None:
            write(canvas, x_review, y, review_cell(self.pic, pull, names, x_ci - x_review - 1), x_ci - x_review - 1)
        if x_ci is not None:
            ci_icon, ci_color = CHECK_MARK[pull.checks]
            canvas.put(x_ci, y, ci_icon, ci_color)
        if x_prog is not None:
            word, done, total = progress(self.pic, pull)
            canvas.put(x_prog, y, word, INK2 if word != "not started" else MUTED)
            if total and word != "not started":
                at = x_prog + len(word) + 1
                size = min(total, 5)
                filled = min(size, round(size * done / total))
                canvas.put(at, y, "▰" * filled, INK2)
                canvas.put(at + filled, y, "▱" * (size - filled), AXIS)
                canvas.put(at + size + 1, y, f"{done}/{total}", INK2)
        canvas.put(x_quiet, y, *(quiet_bar if names else quiet_block)(pull))

    def head(self) -> Text:
        """The column headings: column 1 (owner, state, waits on), the rule, column 2."""
        box = self.query_one("#head", Static).size
        width = max(70, box.width or self.size.width - 2)
        x_review, x_ci, x_prog, names, x_quiet = cols = columns(width)
        end = title_end(cols)                                  # where column 2's own heading must stop
        canvas = Canvas(width, 1)
        canvas.put(X_OWNER + 2, 0, "owner", MUTED)
        canvas.put(X_STATE, 0, "state", MUTED)
        canvas.put(X_WAITS + 2, 0, "waits on", MUTED)
        canvas.put(X_RULE, 0, "│", DIVIDER)
        canvas.put(X_TREE, 0, "pull request", MUTED)
        first, last, total = self.where
        if total > last - first + 1:                           # the list is longer than its box
            said = f"rows {first}–{last} of {total}"
            if X_TREE + 14 + len(said) + 1 > end:              # a narrow window: the short form, or nothing
                said = f"{first}–{last}/{total}"
            if X_TREE + 14 + len(said) + 1 <= end:
                canvas.put(X_TREE + 14, 0, said, INK2)
            above, below = self.unseen                         # a change out of sight is not lost
            room = end - (X_TREE + 14 + len(said) + 3)
            note = "  ".join(part for part in ("▲ " + " ".join(above) + " changed" if above else "",
                                               "▼ " + " ".join(below) + " changed" if below else "") if part)
            if len(note) > room:
                note = "  ".join(part for part in (f"▲ {len(above)} changed" if above else "",
                                                   f"▼ {len(below)} changed" if below else "") if part)
            if note and room > 8:
                canvas.put(X_TREE + 14 + len(said) + 2, 0, cut(note, room), f"bold {INK}")
        if x_review is not None:
            canvas.put(x_review, 0, "review  ◇ to resolve" if names else "review", MUTED)
        if x_ci is not None:
            canvas.put(x_ci, 0, "ci", MUTED)
        if x_prog is not None:
            canvas.put(x_prog, 0, "progress", MUTED)
        if names:
            write(canvas, x_quiet, 0, quiet_scale(), QUIET)
        else:
            canvas.put(x_quiet, 0, "quiet", MUTED)
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
            canvas.put(width - len(key_text), 0, "●", WAITING)
            canvas.put(width - len(key_text) + 2, 0, "yours to fix", MUTED)
            canvas.put(width - 23, 0, "○ waits on someone else", MUTED)
        self.effort_rows = []
        if not self.efforts:
            canvas.put(0, 1, "Nothing waits on anyone." if self.pic.all() else "Nothing is open.", MUTED)
            return canvas.text()

        room = height - 1
        shown = room if len(self.efforts) <= room else room - 1
        self.effort_rows = [key_of(e.pull) for e in self.efforts[:shown]]
        chosen = self.pic.find(self.sel)
        cause = None
        if chosen is not None:
            here = self.pic.stand(chosen)
            cause = key_of((here.end or here).pull)
        refs = [self.ref(e.pull) for e in self.efforts[:shown]]
        key_w = max(len(r) for r in refs)
        now = time.monotonic()
        for y, (e, ref) in enumerate(zip(self.efforts[:shown], refs), start=1):
            if not e.undecided:                        # else whose it is is not known yet: no mark
                canvas.put(0, y, "●" if e.mine else "○", WAITING if e.mine else MUTED)
            canvas.put(2, y, ref, f"bold {INK}")
            urgency = e.urgency
            x_urgency = width - len(urgency)
            what = f"{e.who} · {e.standing.what}"
            if e.behind:
                what += f" · holds up {', '.join('#' + str(p.number) for p in e.behind)}"
            canvas.put(3 + key_w, y, cut(what, x_urgency - key_w - 5), INK if e.mine else INK2)
            if e.waiting:
                held, rest = urgency.split(" · ")
                canvas.put(x_urgency, y, held, INK)
                canvas.put(x_urgency + len(held), y, " · " + rest, MUTED)
            else:
                canvas.put(x_urgency, y, urgency, MUTED)
            shade = self.shade(now - self.entered.get(key_of(e.pull), -999.0))
            if shade:                                          # new on the list: it settles in
                wash(canvas, y, 0, width, shade)
            elif cause == key_of(e.pull):                      # what holds the selected pull request up
                wash(canvas, y, 0, width, RAISED)
        if len(self.efforts) > shown:
            canvas.put(2, height - 1, f"and {len(self.efforts) - shown} more: n steps through them all", MUTED)
        return canvas.text()

    def drawer(self) -> Text:
        """The selected pull request: owner and waits on in plain words, then what GitHub
        records, then its address."""
        pull = self.pic.find(self.sel)
        if pull is None:
            return Text("nothing selected: no open pull requests", style=MUTED)
        pic = self.pic
        here = pic.stand(pull)
        end = here.end or here
        spin = self.spin()
        out = Text()
        out.append(f"{pull.repo} · ", style=MUTED).append(f"#{pull.number} ", style=f"bold {INK}")
        out.append(pull.title + "\n", style=INK)

        def actor(agent: Agent) -> None:
            icon, color = {"working": (spin, INK), "at its prompt": ("▷", INK2), "waiting": ("·", MUTED)}[agent.state]
            out.append(" › ", style=MUTED).append(icon + " ", style=color)
            out.append(f"{agent.name} {'at work' if agent.working else agent.state}",
                       style=INK if agent.working else INK2)

        def human(login: str) -> None:
            if pic.mine(pull) and pic.acts_as(login, end.pull):     # the viewer, or a team they are on
                out.append("● ", style=WAITING).append(pic.shown(login), style=f"bold {INK}")
            else:
                out.append(pic.shown(login), style=INK)

        out.append("owner    ", style=MUTED).append(pic.shown(pull.author), style=INK)
        own = [a for a in pic.on(pull) if a.role == "author"]
        for agent in own:
            actor(agent)
        if not own:
            out.append(" · no agent on it", style=MUTED)
        out.append("\n")

        out.append("waits on ", style=MUTED)
        if here.queued:
            out.append(f"#{here.chain[0]} → ", style=INK2)
        if not end.who:
            out.append("nobody: " + end.what, style=INK2)
        else:
            for i, who in enumerate(end.who):
                out.append(", " if i else "", style=MUTED)
                human(who)
                for agent in reviewing(pic, end.pull, who):
                    actor(agent)
            role = (", the owner," if end.by == "author" and not here.queued
                    else ", the maintainer," if end.by == "maintainer" else "")
            out.append(f"{role} to {end.what}", style=INK2)
        out.append("\n")

        out.append("review   ", style=MUTED)
        said = reviewers(pic, pull)
        for i, (marks, who, words) in enumerate(said):
            out.append("  " if i else "")
            for icon, color in marks:
                out.append(icon, style=color)
            out.append(f" {who} {words}", style=INK2)
        if not said:
            out.append("nobody has been asked", style=MUTED)
            if not pull.can_ask:                               # GitHub offers this author no reviewers
                out.append(f" · {pic.shown(pull.author)} cannot ask here", style=MUTED)
        out.append("\n")
        out.append("comments ", style=MUTED)
        if pull.comments_open:
            out.append("◇ ", style=MUTED).append(f"{pull.comments_open} to resolve", style=INK)
            out.append(f" of {pull.comments_total}", style=MUTED)
        else:
            out.append("none to resolve", style=MUTED)
        icon, color, label = mark(pic, pull)
        out.append("      ").append(icon + " ", style=color).append(label + "\n", style=INK2)
        ci_icon, ci_color = CHECK_MARK[pull.checks]
        out.append("checks   ", style=MUTED).append(ci_icon + " ", style=ci_color)
        merge = {"yes": "can merge", "conflict": "has a merge conflict",
                 "unknown": "GitHub is still working out whether it can merge"}[pull.mergeable]
        out.append(f"{CHECK_SAID[pull.checks]} · {merge}",
                   style=INK if pull.checks == "fail" or pull.mergeable == "conflict" else INK2)
        age = quiet(pull)
        if age:                                                # the figure the quiet bar stands for
            out.append(" · changed just now" if age == "now" else f" · last changed {age} ago", style=MUTED)
        out.append("\n")
        agents = pic.on(pull)
        if agents:
            out.append("agents   ", style=MUTED)
            for i, agent in enumerate(agents):
                out.append("  " if i else "")
                out.append(f"{pic.shown(agent.operator)} › {agent.name}", style=INK)
                out.append(" reviewing" if agent.role == "review" and agent.working else
                           " at work" if agent.working else f" {agent.state}", style=INK2)
            out.append("\n")
        else:
            out.append("branch   ", style=MUTED).append(pull.head, style=INK2)
            out.append(f" → {pull.base} · stands on {pull.short_sha}", style=MUTED)
            if pull.fork:
                out.append(f" · from {pull.head_owner}'s fork", style=MUTED)
            out.append("\n")
        return out.append(pull.page, style=MUTED)              # the address o opens

    def ticker(self) -> Text:
        """The last thing that happened, and how long ago; or, for a moment, a word about
        what the user just did."""
        out = Text()
        notice = self.app.notice_now()
        if notice:
            return out.append(notice, style=INK)
        news = self.app.news
        if news is None:
            waiting = "reading GitHub…" if self.app.fetching else "nothing has happened yet · u reads GitHub now"
            return out.append(waiting, style=MUTED)
        at, said = news
        seconds = max(0.0, time.time() - at)
        when = "now" if seconds < 2 else f"{span(seconds, fine=True)} ago"
        out.append(f"{when:<9}", style=INK if seconds < 2 else MUTED)
        return out.append(said, style=INK if seconds < 2 else INK2)

    def menu_text(self) -> Text:
        """The repository menu: every repository, with a check mark on the ones on screen."""
        out = Text()
        out.append("repositories on screen\n", style=f"bold {INK}")
        out.append("↑↓ move · space shows or hides one · enter closes\n\n", style=MUTED)
        repos = self.app.store.repos
        if not repos:
            return out.append("none yet: s opens the setup, where a adds one", style=INK2)
        longest = max(len(r.name) for r in repos)
        self.menu_at = max(0, min(len(repos) - 1, self.menu_at))
        for i, repo in enumerate(repos):
            line = Text()
            line.append("▸ " if i == self.menu_at else "  ", style=INK)
            line.append("✓ " if repo.shown else "· ", style=INK if repo.shown else MUTED)
            line.append(f"{repo.name:<{longest + 2}}", style=f"bold {INK}" if repo.shown else INK2)
            low = repo.name.lower()
            if repo.shown and low in self.pic.pulls:
                for text, style in count_marks(*counts(self.pic, repo.name)) or [(" nothing waits on anyone", MUTED)]:
                    line.append(text, style=style)
            elif repo.shown:
                line.append(" not read yet", style=MUTED)
            out.append_text(line).append("\n")
        return out.append("\n● what you can get moving there   ○ people it waits on", style=MUTED)

    # ---- keys ----------------------------------------------------------------------------
    def action_move(self, step: int) -> None:
        if self.menu_open:
            count = len(self.app.store.repos)
            if count:
                self.menu_at = (self.menu_at + step) % count
            self.draw()
            return
        order = self.keys()
        if not order:
            return
        at = order.index(self.sel) if self.sel in order else 0
        self.pick(order[max(0, min(len(order) - 1, at + step))])

    def action_page(self, way: int) -> None:
        """Scroll the list a page, keeping two lines of the last one in sight."""
        self.scroll(way * max(1, self.page - 2))

    def action_edge(self, way: int) -> None:
        """Scroll to the list's first line (way -1) or its last (way 1)."""
        self.scroll(way * max(1, self.where[2]))

    def action_next_effort(self) -> None:
        """Jump to the next thing that needs effort, in the list's order: most urgent first.
        Not to one that waits on a team while it is not known yet whether it is yours."""
        stops = [key_of(e.pull) for e in self.efforts if not e.undecided]
        if not stops or self.menu_open:
            return
        at = stops.index(self.sel) if self.sel in stops else -1
        self.pick(stops[(at + 1) % len(stops)])

    def pick(self, key: Key, stay: bool = False) -> None:
        """The user picks a pull request: it is selected, and a list scrolled by hand follows
        the selection again. Every way of picking one goes through here; the board's own
        change of selection, when the selected one is gone, is not a pick (keep_in_bounds).

        `stay` is for one picked where it is drawn, with a click on it in the list: it is in
        view already, and the list is left as it is, as when it is scrolled by hand, so
        that nothing moves under the pointer."""
        self.sel = key
        self.roam = stay
        self.draw()

    def on_click(self, event: events.Click) -> None:
        """A click with the main button on a pull request picks it: on any of its lines in
        the list, or on its line where it needs effort. Which one is told by where on the
        screen the pointer is, as for the wheel (over_list)."""
        if self.menu_open or event.button != 1:
            return
        x, y = event.screen_x, event.screen_y
        box = self.query_one("#map").region
        if box.contains(x, y):
            lines = self.lines()
            at = self.top + y - box.y
            pull = lines[at].row.pull if 0 <= at < len(lines) else None
            if pull is not None:                               # not on a rule, a note or the space below
                self.pick(key_of(pull), stay=True)
                event.stop()
            return
        pane = self.query_one("#effort").content_region
        at = y - pane.y - 1                                    # its first line is the heading
        if pane.contains(x, y) and 0 <= at < len(self.effort_rows):
            self.pick(self.effort_rows[at])                    # the list goes to it, as it does for n
            event.stop()

    def action_open_pull(self) -> None:
        """Show the selected pull request's page on GitHub in the browser."""
        pull = self.pic.find(self.sel)
        if self.menu_open or pull is None:
            return
        self.app.open_pull(pull, self.ref(pull))

    def action_focus(self, step: int) -> None:
        """Step the focus: everyone, a human with all their agents, the human alone, each agent."""
        if self.menu_open:
            return
        steps = focuses(self.pic)
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
        self.menu_open = True
        self.query_one("#menu", Static).styles.display = "block"
        self.draw()

    def action_close_menu(self) -> None:
        if not self.menu_open:
            return
        self.menu_open = False
        self.query_one("#menu", Static).styles.display = "none"
        self.draw()

    def action_toggle_repo(self) -> None:
        """In the menu, space shows or hides the repository under the cursor. The app saves
        it and reads a newly shown one."""
        repos = self.app.store.repos
        if not self.menu_open or not repos:
            return
        repo = repos[max(0, min(len(repos) - 1, self.menu_at))]
        self.app.show_repo(repo.name, not repo.shown)
        self.top, self.roam = 0, False
        self.draw()

    def action_drawer(self) -> None:
        """Fold the drawer away; the list takes its room."""
        self.drawer_open = not self.drawer_open
        drawer = self.query_one("#drawer", Static)
        drawer.styles.display = "block" if self.drawer_open else "none"
        if self.drawer_open:
            drawer.styles.width = self.drawer_w
        self.call_after_refresh(self.draw)
