"""Agent Frolic mock-up, option 1: tree table, redone with the data-viz guidance. The layout is
the first version's (a repo list, the stack map as an indented tree with a column per fact,
a detail pane for the selected branch); what changed is how it uses color:

- color sits on marks only: icons, dots and bars. Names, labels and numbers stay in ink.
- nine states, five color classes (moving, with reviewers, with the author, broken, not in
  review); the icon tells the states within a class apart.
- blue means one thing, the next move is yours. Yellow no longer doubles as "yours".
- every color comes from the validated dark palette and clears 3:1 on the plain, panel and
  selected-row backgrounds. The first version's navy cursor row left red below that.
- the repo bars are thin segments with gaps, not solid blocks.

    .venv/bin/python mockup/option1_tree_table.py

Keys: tab moves between the repo list and the map, arrows move, enter folds a row,
f cycles the filter, l hides the legend, r pretends to refresh, q quits.
"""
from __future__ import annotations

from rich.style import Style
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.theme import Theme
from textual.widgets import Footer, Header, OptionList, Static, Tree
from textual.widgets.option_list import Option
from textual.widgets.tree import TreeNode

from frolic_common import (ACCENT, AXIS, CRIT, FILTERS, GOOD, GROUPS, HAIR, INK, INK2, ME,
                           MUTED, PLANE, PR, RAISED, REPOS, STATUS, SURFACE, WARN, WORD, fit,
                           flat, is_stale, movers, verdicts)

GUIDE = 3                        # columns the tree indents per level
NAME_COL = 47                    # the name column, indent and icon included
W_REVIEW, W_THREADS, W_CI, W_WHO, W_NEXT, W_AGE = 9, 5, 4, 11, 31, 4
DIM = "#5c5b56"                  # rows the filter has set aside
CI = {"pass": ("✓", GOOD), "fail": ("✗", CRIT), "run": ("…", WARN), "none": ("·", AXIS)}

# Textual's own widgets (header, footer, cursor rows) drawn from the same tokens.
THEME = Theme(
    name="frolic", dark=True, primary=ACCENT, secondary=MUTED, accent=ACCENT,
    foreground=INK, background=PLANE, surface=PLANE, panel=SURFACE,
    success=GOOD, warning=WARN, error=CRIT,
    variables={
        "block-cursor-background": RAISED, "block-cursor-foreground": INK,
        "block-cursor-text-style": "none",
        "block-cursor-blurred-background": SURFACE, "block-cursor-blurred-foreground": INK,
        "block-cursor-blurred-text-style": "none", "block-hover-background": SURFACE,
        "footer-background": PLANE, "footer-foreground": MUTED,
        "footer-item-background": PLANE,
        "footer-key-foreground": INK, "footer-key-background": RAISED,
        "footer-description-foreground": MUTED, "footer-description-background": PLANE,
    },
)


# ---- drawing ------------------------------------------------------------------------

def review_cell(pr: PR) -> Text:
    latest = verdicts(pr)
    if not latest:
        return Text("·", style=AXIS)
    cell = Text(f"r{max(e.round for e in latest.values())} ", style=MUTED)
    for event in latest.values():
        icon, color, _ = WORD[event.verdict]
        cell.append(icon, style=color)
    return cell


def who_cell(pr: PR) -> Text:
    if not pr.mine:
        return Text(pr.author, style=INK2)
    cell = Text()
    for worker in pr.workers:
        cell.append("▶" if worker.live else "▷", style=ACCENT if worker.live else MUTED)
        cell.append(worker.serial + " ", style=INK if worker.live else MUTED)
        if worker.name:
            cell.append("≠ ", style=f"bold {CRIT}")
    return cell if pr.workers else Text("no session", style=AXIS)


def next_cell(pr: PR) -> Text:
    who, what = pr.next
    if not who:
        return Text(f"findings for pr-{pr.number:04d}", style=MUTED)
    if who == "you":
        return Text("● ", style=ACCENT).append(f"you · {what}", style=f"bold {INK}")
    return Text("○ ", style=MUTED).append(f"{who} · {what}", style=INK2)


def row(pr: PR) -> Text:
    """One line of the map, without its icon (StackTree draws that)."""
    name = Text(pr.key, style=f"bold {INK}")
    name.append("-" + pr.slug, style=INK2)
    line = fit(name, NAME_COL - pr.depth * GUIDE - 2)
    line.append_text(fit(review_cell(pr), W_REVIEW))
    open_threads = pr.threads[0]
    line.append_text(fit(Text("◆", style=MUTED).append(str(open_threads), style=INK) if open_threads
                         else Text("·", style=AXIS), W_THREADS))
    icon, ci_color = CI[pr.ci]
    line.append_text(fit(Text(icon, style=ci_color), W_CI))
    line.append_text(fit(who_cell(pr), W_WHO))
    line.append_text(fit(next_cell(pr), W_NEXT))
    line.append(pr.age.rjust(W_AGE), style=f"bold {INK}" if is_stale(pr.age) else MUTED)
    return Text(line.plain, style=DIM) if pr.dimmed else line


def column_header() -> Text:
    head = Text(style=MUTED)
    for label, width in (("  branch", NAME_COL), ("review", W_REVIEW), ("open", W_THREADS),
                         ("ci", W_CI), ("who", W_WHO), ("next move", W_NEXT)):
        head.append(label.ljust(width))
    head.append("age".rjust(W_AGE))
    return head


def legend() -> Text:
    """Always on screen, grouped by the five color classes, every icon with its words."""
    text = Text()
    for group, color in GROUPS:
        text.append("━ ", style=color)
        text.append(group + "\n", style=INK2)
        for icon, _, label, in_group in STATUS.values():
            if in_group == group:
                text.append(f"   {icon} ", style=color)
                text.append(label + "\n", style=MUTED)
        if group == "broken":
            text.append("   ✗ ", style=CRIT).append("a check is failing\n", style=MUTED)
    text.append("\n")
    for icon, color, meaning in (("●", ACCENT, "the next move is yours"),
                                 ("○", MUTED, "the next move is theirs"),
                                 ("▶", ACCENT, "your session at work"),
                                 ("▷", MUTED, "your session idle"),
                                 ("≠", CRIT, "a name breaks the pattern"),
                                 ("◆", MUTED, "open review threads")):
        text.append(f" {icon} ", style=color)
        text.append(meaning + "\n", style=MUTED)
    text.append(" r2 ", style=MUTED)
    for icon, color, _ in WORD.values():
        text.append(icon, style=color)
    text.append(" round, reviewers", style=MUTED)
    return text


def repo_option(name: str, prs: list[PR], width: int = 28) -> Text:
    """Two lines per repo: the counts, then its branches by state as a thin segmented bar."""
    everything = flat(prs)
    open_prs = sum(pr.is_pr for pr in everything)
    yours = sum("you" in movers(pr) for pr in everything)
    text = Text(name.ljust(width - 14), style=f"bold {INK}")
    text.append(f"{open_prs:>3} open ", style=MUTED)
    text.append(" ●", style=ACCENT if yours else AXIS)
    text.append(f"{yours:<2}", style=INK if yours else AXIS)
    text.append("\n")
    counted = [pr for pr in everything if pr.state != "findings"]
    if not counted:
        return text.append("nothing open", style=AXIS)
    parts = [(color, sum(STATUS[pr.state][3] == group for pr in counted)) for group, color in GROUPS]
    parts = [(color, n) for color, n in parts if n]
    room = width - 2 - (len(parts) - 1)                    # one cell of gap between segments
    for i, (color, n) in enumerate(parts):
        text.append("━" * max(1, round(room * n / len(counted))), style=color)
        if i < len(parts) - 1:
            text.append(" ")
    return text


def kv(text: Text, key: str, value: str | Text) -> None:
    text.append(f"{key:<10}", style=MUTED)
    text.append_text(value if isinstance(value, Text) else Text(value, style=INK))
    text.append("\n")


def detail_left(repo: str, pr: PR) -> Text:
    icon, color, label, _ = STATUS[pr.state]
    text = Text()
    text.append(f"{repo}-", style=MUTED)
    text.append(pr.name + "\n", style=f"bold underline {INK}")
    text.append(icon, style=color)
    text.append(f" {label} · ", style=INK2)
    text.append(pr.title + "\n\n", style=f"italic {INK2}")

    def name_line(mark: str, label: str, value: str, tone: str = INK) -> None:
        text.append(f"{mark} ", style={"✓": GOOD, "≠": f"bold {CRIT}", "!": WARN}.get(mark, MUTED))
        text.append(f"{label:<8}", style=MUTED)
        text.append(value + "\n", style=tone)

    # The four names that should read alike, and whether each one does.
    text.append("names\n", style=f"bold {INK}")
    name_line("✓", "branch", f"{repo}-{pr.name}")
    expected = f"{repo}-{pr.name}" if pr.state == "findings" else f"{repo}-{pr.key}"
    for worker in pr.workers if pr.mine else []:
        want = expected if pr.state == "findings" else f"{expected}-{worker.serial}"
        if worker.name:
            name_line("≠", "session", worker.name)
            name_line(" ", "", f"expected {want}", INK2)
        else:
            name_line("✓", "session", want)
    if pr.is_pr:
        name_line("✓", "PR", f"#{pr.number} · {pr.ticket.upper()} {pr.title}")
    else:
        name_line("·", "PR", f"none yet · issue #{pr.number} holds the number", INK2)
    name_line("✓", "Jira", f"{pr.ticket.upper()} · {pr.jira_status()} · {pr.author}")
    if pr.same_ticket:
        name_line("!", "also", f"{pr.ticket.upper()} is on {pr.same_ticket} too")
    text.append("\n")

    root = pr.root()
    size = sum(p.state != "findings" for p in root.walk())
    kv(text, "stack", "not stacked" if size == 1 else
       f"level {pr.depth} in a stack of {size} · "
       + ("linked on GitHub" if root.linked else "from base branches"))
    kv(text, "base", pr.parent.name if pr.parent else "main")
    ci_icon, ci_color = CI[pr.ci]
    kv(text, "checks", Text(f"{ci_icon} ", style=ci_color).append(pr.ci_note or "none yet", style=INK))
    kv(text, "merge", Text("⚠ ", style=CRIT).append(pr.merge, style=INK)
       if "conflict" in pr.merge else pr.merge)
    return text


def detail_right(repo: str, pr: PR) -> Text:
    text = Text()
    text.append("review\n", style=f"bold {INK}")
    if not pr.history:
        text.append(" no review asked for yet\n", style=INK2)
    shown_round = 0
    for event in pr.history:
        if event.kind == "push":
            text.append(" pushed   ", style=MUTED)
            text.append(f"{event.commits} commits from {event.who}".ljust(38), style=INK2)
        else:
            head = f"round {event.round}" if event.round != shown_round else ""
            shown_round = event.round
            icon, color, _ = WORD[event.verdict]
            words = {"approved": "approved", "changes": "changes requested",
                     "requested": "review requested",
                     "rerequested": "re-review requested"}[event.verdict]
            text.append(f" {head:<9}", style=MUTED)
            text.append(f"{'you' if event.who == ME else event.who:<8}", style=INK)
            text.append(icon, style=color)
            text.append((f" {words}" + (f" · {event.threads} open" if event.threads else "")).ljust(29),
                        style=INK2)
        text.append(event.age.rjust(4) + "\n", style=MUTED)
    open_threads, total = pr.threads
    if total:
        kv(text, "threads", f"{total} · {total - open_threads} resolved · {open_threads} open")
        for part in pr.thread_note.split(" · ") if pr.thread_note else []:
            kv(text, "", Text(part, style=INK2))
    text.append("\n")

    text.append("who is on it\n", style=f"bold {INK}")
    if not pr.mine:
        text.append(f"  {pr.author} on GitHub", style=INK)
        text.append(" · no session on this Mac\n", style=MUTED)
    for worker in pr.workers:
        text.append("  ▶ " if worker.live else "  ▷ ", style=ACCENT if worker.live else MUTED)
        text.append(f"session {worker.serial}  ", style=INK if worker.live else INK2)
        text.append(worker.note + "\n", style=INK2 if worker.live else MUTED)
    if pr.mine and not pr.workers:
        text.append("  no session on this Mac\n", style=MUTED)
    text.append("\n")
    kv(text, "next", next_cell(pr))
    kv(text, "quiet for", Text(pr.age, style=f"bold {INK}" if is_stale(pr.age) else INK)
       .append("  stale" if is_stale(pr.age) else "", style=INK2))
    return text


class StackTree(Tree):
    """The map. Each row starts with its state icon in its status color; the selected row
    keeps every color and only gains a background."""
    BINDINGS = [Binding("enter", "toggle_node", "Fold", show=False)]

    def render_label(self, node: TreeNode, base_style: Style, style: Style) -> Text:
        label = node._label.copy()
        label.stylize(Style(bgcolor=style.bgcolor))
        pr = node.data
        if pr is None:
            return label
        icon, color, _, _ = STATUS[pr.state]
        icon_style = Style(color=DIM if pr.dimmed else color, bgcolor=style.bgcolor)
        if node.allow_expand and not node.is_expanded:
            hidden = sum(1 for _ in pr.walk()) - 1
            label = Text.assemble(label, (f"  +{hidden} folded", Style(color=MUTED, italic=True)))
        return Text.assemble((icon + " ", icon_style), label)


class FrolicMock(App):
    TITLE = "agent-frolic"
    SUB_TITLE = "mock-up · every name and number is made up"
    CSS = f"""
    Screen {{ layout: vertical; }}
    Header {{ background: {SURFACE}; color: {INK2}; }}
    #body {{ height: 1fr; }}
    #side {{ width: 33; height: 1fr; border-right: solid {HAIR}; }}
    #side-title {{ height: 1; padding: 0 1; color: {MUTED}; }}
    #repos {{ height: 1fr; border: none; padding: 0 1; background: {PLANE}; }}
    #repos:focus {{ border: none; background-tint: {INK} 0%; }}
    #legend {{ height: auto; padding: 0 1; border-top: solid {HAIR}; }}
    #legend.hidden {{ display: none; }}
    #main {{ width: 1fr; height: 1fr; }}
    #strip {{ height: 1; padding: 0 1; }}
    #cols {{ height: 1; padding: 0 1; }}
    #tree {{ height: 1fr; border: none; padding: 0 1; scrollbar-size-horizontal: 0;
             background: {PLANE}; }}
    #tree:focus {{ background-tint: {INK} 0%; }}
    #tree > .tree--guides, #tree > .tree--guides-hover {{ color: {AXIS}; }}
    #tree > .tree--guides-selected {{ color: {MUTED}; }}
    #detail {{ height: 18; border-top: solid {HAIR}; }}
    #strip, #cols, #legend, #detail-left, #detail-right {{ text-wrap: nowrap; text-overflow: ellipsis; }}
    #detail-left {{ width: 1fr; height: 1fr; padding: 0 1; }}
    #detail-right {{ width: 56; height: 1fr; padding: 0 1; border-left: solid {HAIR}; }}
    """
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("f", "cycle_filter", "Filter"),
        Binding("l", "toggle_legend", "Legend"),
        Binding("r", "refresh_now", "Refresh"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.repo = next(iter(REPOS))
        self.filter = 0
        self.refreshed = "2m ago"

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="body"):
            with Vertical(id="side"):
                yield Static("repos", id="side-title")
                yield OptionList(*(Option(repo_option(name, prs), id=name)
                                   for name, prs in REPOS.items()), id="repos")
                yield Static(legend(), id="legend")
            with Vertical(id="main"):
                yield Static("", id="strip")
                yield Static(column_header(), id="cols")
                tree: Tree = StackTree("main", id="tree")
                tree.guide_depth = GUIDE
                tree.auto_expand = False
                yield tree
                with Horizontal(id="detail"):
                    yield Static("", id="detail-left")
                    yield Static("", id="detail-right")
        yield Footer()

    def on_mount(self) -> None:
        self.register_theme(THEME)
        self.theme = "frolic"
        self.show_repo(self.repo)
        self.query_one("#tree", Tree).focus()

    # ---- the map -----------------------------------------------------------
    def show_repo(self, repo: str) -> None:
        self.repo = repo
        tree = self.query_one("#tree", Tree)
        tree.clear()
        tree.root.set_label(Text.assemble(("main", f"bold {INK}"), ("  the default branch", MUTED)))
        prs = REPOS[repo]
        self.apply_filter()

        def add(parent: TreeNode, pr: PR) -> None:
            node = parent.add(row(pr), data=pr, expand=True, allow_expand=bool(pr.children))
            for child in pr.children:
                add(node, child)

        for pr in prs:
            add(tree.root, pr)
        if not prs:
            tree.root.add_leaf(Text("no open pull requests or local branches", style=MUTED))
        tree.root.expand()
        tree.cursor_line = 1 if prs else 0
        self.draw_strip()
        self.draw_detail(prs[0] if prs else None)

    def apply_filter(self) -> None:
        keep = FILTERS[self.filter][1]
        for pr in flat(REPOS[self.repo]):
            pr.dimmed = not keep(pr)

    def draw_strip(self) -> None:
        everything = flat(REPOS[self.repo])
        strip = Text()
        strip.append(self.repo, style=f"bold {INK}")
        strip.append(f"  {sum(pr.is_pr for pr in everything)} open", style=MUTED)
        for state, (icon, color, _, _) in STATUS.items():
            n = sum(pr.state == state for pr in everything)
            if n and state != "findings":
                strip.append(f"  {icon}", style=color)
                strip.append(str(n), style=INK2)
        yours = sum("you" in movers(pr) for pr in everything)
        strip.append("    ●", style=ACCENT)
        strip.append(f" {yours} yours", style=f"bold {INK}")
        strip.append(f"    refreshed {self.refreshed}", style=MUTED)
        if self.filter:
            strip.append("    ━ ", style=ACCENT)
            strip.append(f"{FILTERS[self.filter][0]} only", style=INK)
        self.query_one("#strip", Static).update(strip)

    def draw_detail(self, pr: PR | None) -> None:
        left, right = self.query_one("#detail-left", Static), self.query_one("#detail-right", Static)
        if pr is None:
            left.update(Text("main\nSelect a branch to see its names, review rounds and "
                             "who is on it.", style=MUTED))
            right.update("")
        else:
            left.update(detail_left(self.repo, pr))
            right.update(detail_right(self.repo, pr))

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted) -> None:
        self.draw_detail(event.node.data)

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if event.option.id and event.option.id != self.repo:
            self.show_repo(event.option.id)

    # ---- keys --------------------------------------------------------------
    def action_cycle_filter(self) -> None:
        self.filter = (self.filter + 1) % len(FILTERS)
        self.apply_filter()
        tree = self.query_one("#tree", Tree)

        def relabel(node: TreeNode) -> None:
            if node.data is not None:
                node.set_label(row(node.data))
            for child in node.children:
                relabel(child)

        relabel(tree.root)
        self.draw_strip()

    def action_toggle_legend(self) -> None:
        self.query_one("#legend", Static).toggle_class("hidden")

    def action_refresh_now(self) -> None:
        self.refreshed = "just now"
        self.draw_strip()


if __name__ == "__main__":
    FrolicMock().run()
