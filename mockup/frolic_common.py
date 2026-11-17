"""What the Agent Frolic mock-ups share: the made-up data, the state vocabulary, and a few
drawing helpers. Every name and number here is invented; nothing is read from GitHub,
Jira or this machine."""
from __future__ import annotations

from dataclasses import dataclass, field

from rich.style import Style
from rich.text import Text

ME = "djp3"                      # the account whose sessions run on this machine

GREEN, YELLOW, RED, BLUE, PURPLE, GREY, FAINT = (
    "#3fb950", "#d29922", "#f85149", "#58a6ff", "#bc8cff", "grey62", "grey35")
YOURS = "#e3b341"                # anything waiting on you

# state -> (icon, color, bold, what it means)
LOOK = {
    "ready":    ("▲", GREEN, True,  "ready to merge"),
    "approved": ("✓", GREEN, False, "approved, waits for parent"),
    "review":   ("◔", YELLOW, False, "in review"),
    "rereview": ("↻", YELLOW, False, "fixes pushed, re-review"),
    "changes":  ("✗", RED, False,   "changes requested"),
    "conflict": ("⚠", RED, True,    "conflicts with its base"),
    "draft":    ("◌", GREY, False,  "draft"),
    "local":    ("○", BLUE, False,  "local, number reserved"),
    "findings": ("◇", PURPLE, False, "local review branch"),
}
# one reviewer's latest word -> (icon, color, text)
VERDICT = {
    "approved":    ("✓", GREEN, "approved"),
    "changes":     ("✗", RED, "changes requested"),
    "requested":   ("◔", YELLOW, "review requested"),
    "rerequested": ("↻", YELLOW, "re-review requested"),
}
CI = {"pass": ("✓", GREEN), "fail": ("✗", RED), "run": ("…", YELLOW), "none": ("·", FAINT)}

GUIDE = 3                        # columns the tree indents per level
NAME_COL = 47                    # the name column, indent and icon included
W_REVIEW, W_THREADS, W_CI, W_WHO, W_NEXT, W_AGE = 9, 5, 4, 11, 31, 4


# ---- the made-up data ---------------------------------------------------------------

@dataclass
class Worker:
    """One Claude Code session on this machine working a branch."""
    serial: str
    live: bool
    note: str
    name: str | None = None      # set only when the session's name breaks the convention
    mode: str = ""               # v2 only: "prompt" (alive, waiting for you), "waiting" (alive,
                                 # idle while its branch waits on someone), "ended"; "" = from live


@dataclass
class Event:
    """A line of review history: a reviewer's word in some round, or commits pushed."""
    kind: str                    # "review" | "push"
    who: str
    age: str
    round: int = 0
    verdict: str = ""
    threads: int = 0             # threads from this review that are still open
    commits: int = 0


def rv(round: int, who: str, verdict: str, age: str, threads: int = 0) -> Event:
    return Event("review", who, age, round, verdict, threads)


def push(who: str, commits: int, age: str) -> Event:
    return Event("push", who, age, commits=commits)


@dataclass
class PR:
    ticket: str                  # Jira key as the branch spells it: af-42
    number: int                  # the reserved issue number, which the PR keeps
    slug: str
    state: str
    title: str = ""
    author: str = ME
    history: list[Event] = field(default_factory=list)
    threads: tuple[int, int] = (0, 0)        # open, total
    thread_note: str = ""
    ci: str = "pass"
    ci_note: str = ""
    merge: str = "clean"
    workers: list[Worker] = field(default_factory=list)
    next: tuple[str, str] = ("", "")         # who has the next move, and what it is
    age: str = ""
    jira: str = ""                           # Jira status; guessed from the state when empty
    linked: bool = False                     # stack root only: linked as a stack on GitHub
    same_ticket: str = ""                    # another branch on the same Jira ticket
    children: list["PR"] = field(default_factory=list)
    parent: "PR | None" = None
    depth: int = 1
    dimmed: bool = False
    # Used by the v2 mock-ups only (frolic_v2.py fills them in).
    base: str = "main"                       # stack root only: the branch the stack is anchored on
    behind: int = 0                          # stack root only: commits behind that branch's tip
    progress: tuple[int, int] = (0, 0)       # (done, total) of the next move, when it can be counted
    unit: str = ""                           # what is being counted: "threads", "files"
    started: bool = False                    # the holder has begun the next move, uncounted
    waits_on: int = 0                        # the PR number of another branch this one waits on

    @property
    def key(self) -> str:
        return f"{self.ticket}-pr-{self.number:04d}"

    @property
    def name(self) -> str:
        return f"{self.key}-{self.slug}"

    @property
    def mine(self) -> bool:
        return self.author == ME

    @property
    def is_pr(self) -> bool:
        return self.state not in ("local", "findings")

    def jira_status(self) -> str:
        return self.jira or ("In Progress" if self.state in ("draft", "local") else "In Review")

    def root(self) -> "PR":
        return self if self.parent is None else self.parent.root()

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()


def frolic() -> list[PR]:
    return [
        PR("af-31", 12, "github-graphql-client", "ready", linked=True,
           title="GraphQL client for pull request review state",
           history=[rv(1, "mira", "approved", "1d"), rv(1, "kenji", "approved", "2h")],
           threads=(0, 4), ci_note="12 of 12", next=("you", "merge"), age="2h",
           workers=[Worker("01", False, "ended 2h ago")],
           children=[
               PR("af-32", 13, "review-state-model", "changes",
                  title="Model review rounds and whose move it is",
                  history=[rv(1, "tobias", "approved", "1d"), rv(1, "mira", "changes", "5h", 3)],
                  threads=(3, 7), thread_note="2 wait for your reply · 1 waits for mira to resolve",
                  ci_note="12 of 12", merge="clean · 1 commit behind its base",
                  next=("you", "answer 3 threads"), age="5h",
                  workers=[Worker("01", True, "editing review_state.py · 4m ago")],
                  children=[
                      PR("af-34", 15, "stack-inference", "rereview",
                         title="Infer stacks from base branches",
                         history=[rv(1, "mira", "changes", "2d"), push("session 01", 4, "1d"),
                                  rv(2, "mira", "rerequested", "1d")],
                         threads=(0, 5), ci_note="12 of 12", next=("mira", "re-review"), age="1d",
                         workers=[Worker("01", False, "waiting · idle 1d")],
                         children=[
                             PR("af-42", 17, "stack-map", "draft",
                                title="Draw the stack map as trees with state icons",
                                ci="run", ci_note="7 of 12 so far", next=("you", "finish the draft"),
                                age="1m",
                                workers=[Worker("01", False, "ended 1d ago"),
                                         Worker("02", True, "writing stack_map.py · just now")],
                                children=[
                                    PR("af-47", 21, "state-icons", "local",
                                       title="Icon and color for every review state",
                                       ci="none", merge="3 commits, not pushed",
                                       next=("you", "open the PR"), age="40m",
                                       workers=[Worker("01", True, "waiting at its prompt · 40m ago")]),
                                ]),
                         ]),
                      PR("af-32", 13, "review-01", "findings", title="Local review of pr-0013",
                         ci="none", merge="local only", age="6h",
                         workers=[Worker("01", False, "findings saved · 6h ago")]),
                  ]),
           ]),
        PR("af-27", 9, "jira-cloud-client", "review", author="mira",
           title="Jira Cloud client: tickets by key",
           history=[rv(1, "kenji", "approved", "2d"), rv(1, ME, "requested", "3d")],
           threads=(1, 2), thread_note="1 waits for mira's reply", ci_note="12 of 12",
           next=("you", "review"), age="3d",
           children=[
               PR("af-27", 9, "review-01", "findings", title="Local review of pr-0009",
                  ci="none", merge="local only", age="2m",
                  workers=[Worker("01", True, "reading jira_client.py · 2m ago")]),
               PR("af-33", 14, "jira-ticket-badges", "approved", author="mira",
                  title="Ticket status badges on the map",
                  history=[rv(1, "tobias", "approved", "2d")], threads=(0, 1), ci_note="12 of 12",
                  merge="clean · waits for pr-0009", next=("mira", "merge after pr-0009"), age="2d"),
           ]),
        PR("af-40", 16, "transcript-reader", "conflict", author="tobias",
           title="Read session names from transcripts",
           history=[rv(1, ME, "changes", "4d", 2)], threads=(2, 3),
           thread_note="2 wait for tobias's reply", ci="fail", ci_note="9 of 12 · 3 failing",
           merge="conflicts with main", next=("tobias", "rebase, 2 threads"), age="4d"),
        PR("af-44", 18, "sparkle-updates", "review",
           title="Sparkle updates from GitHub releases",
           history=[rv(1, "tobias", "requested", "1d"), rv(1, "ana", "requested", "1d")],
           ci="fail", ci_note="11 of 12 · signing step failing",
           next=("you", "fix the failing check"), age="1d",
           workers=[Worker("01", True, "waiting at its prompt · 35m ago", name="sparkle-fix")]),
        PR("af-45", 19, "overview-panel", "draft", author="ana",
           title="Overview panel: one row per repo", ci="none",
           next=("ana", "finish the draft"), age="9d", same_ticket="pr-0022 (djp3, local)"),
        PR("af-45", 22, "overview-sidebar", "local", title="Repo list in a sidebar",
           ci="none", merge="2 commits, not pushed", next=("you", "same ticket as pr-0019"),
           age="15m", same_ticket="pr-0019 (ana, draft)",
           workers=[Worker("01", True, "editing sidebar.py · 15m ago")]),
        PR("af-46", 20, "keychain-credentials", "local", title="Keep the tokens in the Keychain",
           ci="none", merge="3 commits, not pushed", next=("you", "open the PR"), age="2d",
           workers=[Worker("01", False, "ended 2d ago")]),
    ]


def ledger_api() -> list[PR]:
    return [
        PR("led-210", 341, "ledger-export-csv", "ready", author="kenji", linked=True,
           title="Export a ledger as CSV",
           history=[rv(1, ME, "approved", "1d"), rv(1, "lucia", "approved", "3h")],
           threads=(0, 6), ci_note="48 of 48", next=("kenji", "merge"), age="3h",
           children=[
               PR("led-214", 347, "export-scheduling", "review", author="kenji",
                  title="Schedule recurring exports",
                  history=[rv(1, ME, "requested", "1d")], ci_note="48 of 48",
                  next=("you", "review"), age="1d",
                  children=[
                      PR("led-219", 352, "export-retries", "draft", author="kenji",
                         title="Retry a failed export", ci="run", ci_note="30 of 48 so far",
                         next=("kenji", "finish the draft"), age="2d"),
                  ]),
           ]),
        PR("led-198", 329, "double-entry-validation", "changes", author="lucia",
           title="Reject entries that do not balance",
           history=[rv(1, "tobias", "changes", "9d"), push("lucia", 6, "8d"),
                    rv(2, "tobias", "changes", "7d"), push("lucia", 3, "6d"),
                    rv(3, "tobias", "changes", "6d", 5)],
           threads=(5, 19), thread_note="5 wait for lucia's reply", ci_note="48 of 48",
           next=("lucia", "answer 5 threads"), age="6d"),
        PR("led-221", 355, "rate-limit-middleware", "rereview",
           title="Rate limits per API key",
           history=[rv(1, "lucia", "changes", "3d"), push("session 01", 5, "2d"),
                    rv(2, "lucia", "rerequested", "2d")],
           threads=(0, 4), ci_note="48 of 48", next=("lucia", "re-review"), age="2d",
           workers=[Worker("01", False, "waiting · idle 2d")],
           children=[
               PR("led-225", 358, "rate-limit-metrics", "local",
                  title="Count rejected requests per key", ci="none",
                  merge="5 commits, not pushed", next=("you", "open the PR"), age="1h",
                  workers=[Worker("01", True, "running tests · 1h ago")]),
           ]),
        PR("led-207", 338, "currency-rounding", "conflict", author="ana",
           title="Round half to even in every currency",
           history=[rv(1, "kenji", "approved", "6d")], threads=(0, 2), ci_note="48 of 48",
           merge="conflicts with main", next=("ana", "rebase"), age="5d"),
        PR("led-230", 360, "audit-log-index", "review", author="tobias",
           title="Index the audit log by account",
           history=[rv(1, "mira", "requested", "4h"), rv(1, "kenji", "requested", "4h")],
           ci_note="48 of 48", next=("mira, kenji", "review"), age="4h"),
        PR("led-188", 312, "legacy-import-removal", "draft", author="mira",
           title="Remove the old import path", ci="none",
           next=("mira", "finish the draft"), age="21d"),
    ]


def web_console() -> list[PR]:
    return [
        PR("web-88", 140, "invoice-table-sorting", "ready", author="ana",
           title="Sort the invoice table by any column",
           history=[rv(1, "mira", "approved", "5h")], threads=(0, 2), ci_note="31 of 31",
           next=("ana", "merge"), age="5h"),
        PR("web-91", 143, "dark-theme-tokens", "review", author="mira",
           title="Color tokens for the dark theme",
           history=[rv(1, ME, "requested", "2d")], ci_note="31 of 31",
           next=("you", "review"), age="2d",
           children=[
               PR("web-91", 143, "review-01", "findings", title="Local review of pr-0143",
                  ci="none", merge="local only", age="1d",
                  workers=[Worker("01", False, "findings saved · 1d ago")]),
           ]),
        PR("web-93", 146, "session-timeout-banner", "changes",
           title="Warn before the session times out",
           history=[rv(1, "ana", "changes", "3h", 1)], threads=(1, 3),
           thread_note="1 waits for your reply", ci_note="31 of 31",
           next=("you", "answer 1 thread"), age="3h",
           workers=[Worker("01", True, "editing banner.tsx · 6m ago")]),
        PR("web-95", 149, "csv-download-button", "draft", author="lucia",
           title="Download any table as CSV", ci="none",
           next=("lucia", "finish the draft"), age="1d"),
    ]


def link(prs: list[PR], parent: PR | None = None, depth: int = 1) -> list[PR]:
    for pr in prs:
        pr.parent, pr.depth = parent, depth
        link(pr.children, pr, depth + 1)
    return prs


REPOS: dict[str, list[PR]] = {
    "agent-frolic": link(frolic()),
    "ledger-api": link(ledger_api()),
    "web-console": link(web_console()),
    "infra-scripts": [],
}

FILTERS = [
    ("everything", lambda pr: True),
    ("your move", lambda pr: pr.next[0] == "you"),
    ("your work", lambda pr: pr.mine),
]


# ---- drawing ------------------------------------------------------------------------

def fit(text: Text, width: int) -> Text:
    """The text cut or padded to exactly `width` cells."""
    if text.cell_len > width - 1:
        text.truncate(width - 1, overflow="ellipsis")
    text.append(" " * (width - text.cell_len))
    return text


def is_stale(age: str) -> bool:
    return age.endswith("d") and int(age[:-1]) >= 7


def verdicts(pr: PR) -> dict[str, Event]:
    """Each reviewer's latest word."""
    latest: dict[str, Event] = {}
    for event in pr.history:
        if event.kind == "review":
            latest[event.who] = event
    return latest


def review_cell(pr: PR) -> Text:
    latest = verdicts(pr)
    if not latest:
        return Text("·", style=FAINT)
    cell = Text(f"r{max(e.round for e in latest.values())} ", style=GREY)
    for event in latest.values():
        icon, color, _ = VERDICT[event.verdict]
        cell.append(icon, style=color)
    return cell


def who_cell(pr: PR) -> Text:
    if not pr.mine:
        return Text(pr.author, style="#79c0ff")
    cell = Text()
    for worker in pr.workers:
        cell.append(("▶" if worker.live else "▷") + worker.serial + " ",
                    style=GREEN if worker.live else GREY)
        if worker.name:
            cell.append("≠ ", style=f"bold {RED}")
    return cell if pr.workers else Text("no session", style=FAINT)


def next_cell(pr: PR) -> Text:
    who, what = pr.next
    if not who:
        return Text(f"findings for pr-{pr.number:04d}", style=GREY)
    if who == "you":
        return Text(f"→ you · {what}", style=f"bold {YOURS}")
    return Text(f"→ {who} · {what}", style="grey70")


def row(pr: PR) -> Text:
    """One line of the map, without its icon (StackTree draws that)."""
    _, color, bold, _ = LOOK[pr.state]
    name = Text(pr.key, style=Style(color=color, bold=True))
    name.append("-" + pr.slug, style=Style(color=color, bold=bold))
    line = fit(name, NAME_COL - pr.depth * GUIDE - 2)
    line.append_text(fit(review_cell(pr), W_REVIEW))
    open_threads = pr.threads[0]
    line.append_text(fit(Text(f"◆{open_threads}", style=YELLOW) if open_threads
                         else Text("·", style=FAINT), W_THREADS))
    icon, ci_color = CI[pr.ci]
    line.append_text(fit(Text(icon, style=ci_color), W_CI))
    line.append_text(fit(who_cell(pr), W_WHO))
    line.append_text(fit(next_cell(pr), W_NEXT))
    line.append(pr.age.rjust(W_AGE), style=RED if is_stale(pr.age) else GREY)
    return Text(line.plain, style=FAINT) if pr.dimmed else line


def column_header() -> Text:
    head = Text(style=GREY)
    for label, width in (("  branch", NAME_COL), ("review", W_REVIEW), ("open", W_THREADS),
                         ("ci", W_CI), ("who", W_WHO), ("next move", W_NEXT)):
        head.append(label.ljust(width))
    head.append("age".rjust(W_AGE))
    return head


def legend() -> Text:
    text = Text()
    text.append("legend\n", style="bold")
    for icon, color, _, meaning in LOOK.values():
        text.append(f" {icon} ", style=color)
        text.append(meaning + "\n", style=GREY)
    for icon, color, meaning in ((" ▶ ", GREEN, "session at work here"),
                                 (" ▷ ", GREY, "session idle or ended"),
                                 (" ≠ ", RED, "a name breaks the pattern"),
                                 (" ◆ ", YELLOW, "open review threads"),
                                 (" r2 ", GREY, "review round 2, then")):
        text.append(icon, style=color)
        text.append(meaning + "\n", style=GREY)
    text.append(" ")
    for icon, color, _ in VERDICT.values():
        text.append(icon, style=color)
    text.append(" each reviewer's word", style=GREY)
    return text


def repo_option(name: str, prs: list[PR], width: int = 28) -> Text:
    """Two lines per repo: the counts, then a bar of its branches by state."""
    everything = [pr for root in prs for pr in root.walk()]
    open_prs = sum(pr.is_pr for pr in everything)
    yours = sum(pr.next[0] == "you" for pr in everything)
    text = Text(name.ljust(width - 14), style="bold")
    text.append(f"{open_prs:>3} open ", style=GREY)
    text.append(f"→{yours:<2}", style=f"bold {YOURS}" if yours else FAINT)
    text.append("\n")
    counted = [pr for pr in everything if pr.state != "findings"]
    if not counted:
        text.append("nothing open", style=FAINT)
        return text
    bar = width - 2
    used = 0
    groups = (("ready", "approved"), ("review", "rereview"), ("changes", "conflict"),
              ("draft",), ("local",))
    for i, states in enumerate(groups):
        n = sum(pr.state in states for pr in counted)
        cells = bar - used if i == len(groups) - 1 else round(bar * n / len(counted))
        text.append("█" * cells, style=LOOK[states[0]][1])
        used += cells
    return text


def kv(text: Text, key: str, value: str | Text, style: str = "") -> None:
    text.append(f"{key:<10}", style=GREY)
    text.append_text(value if isinstance(value, Text) else Text(value, style=style))
    text.append("\n")


def detail_left(repo: str, pr: PR) -> Text:
    icon, color, _, meaning = LOOK[pr.state]
    text = Text()
    text.append(f"{repo}-", style=GREY)
    text.append(pr.name + "\n", style=Style(color=color, bold=True, underline=True))
    text.append(f"{icon} {meaning}", style=color)
    text.append(" · ", style=GREY)
    text.append(pr.title + "\n\n", style="italic")

    # The four names that should read alike, and whether each one does.
    def name_line(mark: str, label: str, value: str, style: str = "") -> None:
        color = {"✓": GREEN, "≠": f"bold {RED}"}.get(mark, GREY)
        text.append(f"{mark} ", style=color)
        text.append(f"{label:<8}", style=GREY)
        text.append(value + "\n", style=style)

    text.append("names\n", style="bold")
    name_line("✓", "branch", f"{repo}-{pr.name}")
    expected = f"{repo}-{pr.name}" if pr.state == "findings" else f"{repo}-{pr.key}"
    for worker in pr.workers if pr.mine else []:
        want = expected if pr.state == "findings" else f"{expected}-{worker.serial}"
        if worker.name:
            name_line("≠", "session", worker.name, RED)
            name_line(" ", "", f"expected {want}", GREY)
        else:
            name_line("✓", "session", want)
    if pr.is_pr:
        name_line("✓", "PR", f"#{pr.number} · {pr.ticket.upper()} {pr.title}")
    else:
        name_line("·", "PR", f"none yet · issue #{pr.number} holds the number", BLUE)
    name_line("✓", "Jira", f"{pr.ticket.upper()} · {pr.jira_status()} · {pr.author}")
    if pr.same_ticket:
        name_line("!", "also", f"{pr.ticket.upper()} is on {pr.same_ticket} too", YOURS)
    text.append("\n")

    root = pr.root()
    size = sum(p.state != "findings" for p in root.walk())
    if size == 1:
        kv(text, "stack", "not stacked")
    else:
        kv(text, "stack", f"level {pr.depth} in a stack of {size} · "
           + ("linked on GitHub" if root.linked else "from base branches"))
    kv(text, "base", pr.parent.name if pr.parent else "main")
    ci_icon, ci_color = CI[pr.ci]
    kv(text, "checks", Text(f"{ci_icon} ", style=ci_color).append(pr.ci_note or "none yet", style=""))
    kv(text, "merge", pr.merge, RED if "conflict" in pr.merge else "")
    return text


def detail_right(repo: str, pr: PR) -> Text:
    text = Text()
    text.append("review\n", style="bold")
    if not pr.history:
        text.append("  no review asked for yet\n", style=GREY)
    shown_round = 0
    for event in pr.history:
        if event.kind == "push":
            text.append(" pushed   ", style=GREY)
            text.append(f"{event.commits} commits from {event.who}".ljust(38))
        else:
            head = f"round {event.round}" if event.round != shown_round else ""
            shown_round = event.round
            icon, color, words = VERDICT[event.verdict]
            text.append(f" {head:<9}", style=GREY)
            text.append(f"{'you' if event.who == ME else event.who:<8}")
            said = f"{icon} {words}" + (f" · {event.threads} open" if event.threads else "")
            text.append(said.ljust(30), style=color)
        text.append(event.age.rjust(4) + "\n", style=GREY)
    open_threads, total = pr.threads
    if total:
        kv(text, "threads", f"{total} · {total - open_threads} resolved · {open_threads} open",
           YELLOW if open_threads else "")
        for part in pr.thread_note.split(" · ") if pr.thread_note else []:
            kv(text, "", part, GREY)
    text.append("\n")

    text.append("who is on it\n", style="bold")
    if not pr.mine:
        text.append(f"  {pr.author} on GitHub", style="#79c0ff")
        text.append(" · no session on this Mac\n", style=GREY)
    for worker in pr.workers:
        text.append("  ▶ " if worker.live else "  ▷ ", style=GREEN if worker.live else GREY)
        text.append(f"session {worker.serial}  ")
        text.append(worker.note + "\n", style=GREEN if worker.live else GREY)
    if pr.mine and not pr.workers:
        text.append("  no session on this Mac\n", style=GREY)
    text.append("\n")
    kv(text, "next", next_cell(pr))
    kv(text, "quiet for", pr.age + ("  (stale)" if is_stale(pr.age) else ""),
       RED if is_stale(pr.age) else "")
    return text


# ---- shared by options 2-5 ----------------------------------------------------------
# Dark-mode tokens from the data-viz reference palette, checked with its validator against
# these surfaces: every status color and the accent clear 3:1 on PLANE, SURFACE and RAISED.
# Color goes on marks only; text stays in the ink tokens.
PLANE, SURFACE, RAISED = "#0d0d0d", "#1a1a19", "#262624"
INK, INK2, MUTED, AXIS, HAIR = "#f0efec", "#c3c2b7", "#898781", "#383835", "#2c2c2a"
GOOD, WARN, SERIOUS, CRIT = "#0ca30c", "#fab219", "#ec835a", "#d03b3b"     # status, reserved
ACCENT = "#3987e5"               # the one emphasis hue: whatever is yours

# state -> (icon, status color, label, group). Five color classes, nine icons.
STATUS = {
    "ready":    ("▲", GOOD, "ready to merge", "moving"),
    "approved": ("✓", GOOD, "approved", "moving"),
    "review":   ("◔", WARN, "in review", "with reviewers"),
    "rereview": ("↻", WARN, "re-review asked", "with reviewers"),
    "changes":  ("✗", SERIOUS, "changes requested", "with the author"),
    "conflict": ("⚠", CRIT, "conflict", "broken"),
    "draft":    ("◌", MUTED, "draft", "not in review"),
    "local":    ("○", MUTED, "local, no PR yet", "not in review"),
    "findings": ("◇", MUTED, "local review", "not in review"),
}
GROUPS = (("moving", GOOD), ("with reviewers", WARN), ("with the author", SERIOUS),
          ("broken", CRIT), ("not in review", MUTED))
WORD = {"approved": ("✓", GOOD, "approved"), "changes": ("✗", SERIOUS, "asked for changes"),
        "requested": ("◔", WARN, "review asked"), "rerequested": ("↻", WARN, "re-review asked")}


def flat(prs: list[PR]) -> list[PR]:
    """Every branch of a repo, parents before children."""
    return [pr for root in prs for pr in root.walk()]


def movers(pr: PR) -> list[str]:
    """Who holds the next move: "you" and/or other people's names."""
    return [name.strip() for name in pr.next[0].split(",") if name.strip()]


def shown(name: str) -> str:
    return "you" if name == ME else name


def people(prs: list[PR]) -> list[str]:
    """You first, then everyone else who wrote or reviewed something, busiest first."""
    seen: dict[str, int] = {}
    for pr in flat(prs):
        for name in [pr.author] + [e.who for e in pr.history if e.kind == "review"]:
            seen[shown(name)] = seen.get(shown(name), 0) + 1
    others = sorted((n for n in seen if n != "you"), key=lambda n: (-seen[n], n))
    return ["you"] + others


def age_hours(age: str) -> float:
    try:
        return float(age[:-1]) * {"m": 1 / 60, "h": 1, "d": 24}[age[-1]]
    except (ValueError, KeyError):           # "now", or anything else that is not 5h / 2d / 40m
        return 0.0


def broken(pr: PR) -> bool:
    return pr.state == "conflict" or pr.ci == "fail"


class Canvas:
    """A grid of styled characters, for layouts a widget tree cannot draw."""

    def __init__(self, width: int, height: int) -> None:
        self.width, self.height = width, height
        self.cells: list[list[tuple[str, str]]] = [[(" ", "")] * width for _ in range(height)]

    def put(self, x: int, y: int, text: str, style: str = "") -> None:
        if not 0 <= y < self.height:
            return
        for i, ch in enumerate(text):
            if 0 <= x + i < self.width:
                self.cells[y][x + i] = (ch, style)

    def paint(self, x: int, y: int, width: int, background: str) -> None:
        """Put a background behind what is already there."""
        if not 0 <= y < self.height:
            return
        for i in range(max(0, x), min(self.width, x + width)):
            ch, style = self.cells[y][i]
            self.cells[y][i] = (ch, f"{style} on {background}".strip())

    def text(self) -> Text:
        out = Text(no_wrap=True)
        for y, line in enumerate(self.cells):
            run, run_style = "", line[0][1] if line else ""
            for ch, style in line:
                if style != run_style:
                    out.append(run, style=run_style)
                    run, run_style = "", style
                run += ch
            out.append(run.rstrip() if not run_style else run, style=run_style)
            if y < self.height - 1:
                out.append("\n")
        return out
