"""Agent Frolic mock-up, option 1 as first drawn, before the data-viz guidance was applied.
Kept for comparison; option1_tree_table.py is the redone version.

    .venv/bin/python mockup/option1_original.py

Keys: tab moves between the repo list and the map, arrows move, enter folds a row,
f cycles the filter, l hides the legend, r pretends to refresh, q quits.
"""
from __future__ import annotations

from rich.style import Style
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header, OptionList, Static, Tree
from textual.widgets.option_list import Option
from textual.widgets.tree import TreeNode

from frolic_common import (BLUE, FAINT, FILTERS, GREY, GUIDE, LOOK, PR, REPOS, YOURS,
                           column_header, detail_left, detail_right, legend, repo_option, row)


class StackTree(Tree):
    """The map. Each row starts with its state icon in its state color, and the selected
    row keeps those colors."""
    BINDINGS = [Binding("enter", "toggle_node", "Fold", show=False)]

    def render_label(self, node: TreeNode, base_style: Style, style: Style) -> Text:
        label = node._label.copy()
        label.stylize(style.without_color + Style(bgcolor=style.bgcolor))
        pr = node.data
        if pr is None:
            return label
        icon, color, bold, _ = LOOK[pr.state]
        icon_style = base_style + Style(color=FAINT if pr.dimmed else color, bold=bold)
        if node.allow_expand and not node.is_expanded:
            hidden = sum(1 for _ in pr.walk()) - 1
            label = Text.assemble(label, (f"  +{hidden} folded", Style(color=GREY, italic=True)))
        return Text.assemble((icon + " ", icon_style), label)


class FrolicMock(App):
    TITLE = "agent-frolic"
    SUB_TITLE = "mock-up · every name and number is made up"
    CSS = """
    Screen { layout: vertical; }
    #body { height: 1fr; }
    #side { width: 33; height: 1fr; border-right: solid $primary-darken-2; }
    #side-title { height: 1; padding: 0 1; }
    #repos { height: 1fr; border: none; padding: 0 1; background: $background; }
    #repos:focus { border: none; }
    #repos > .option-list--option-highlighted { background: #18283a; text-style: none; }
    #repos:focus > .option-list--option-highlighted { background: #1f3b5c; text-style: none; }
    #legend { height: auto; padding: 0 1; border-top: solid $primary-darken-2; }
    #legend.hidden { display: none; }
    #main { width: 1fr; height: 1fr; }
    #strip { height: 1; padding: 0 1; }
    #cols { height: 1; padding: 0 1; }
    #tree { height: 1fr; border: none; padding: 0 1; scrollbar-size-horizontal: 0; }
    #tree > .tree--cursor { background: #18283a; text-style: bold; }
    #tree:focus > .tree--cursor { background: #1f3b5c; text-style: bold; }
    #detail { height: 18; border-top: solid $primary-darken-2; }
    #strip, #cols, #legend, #detail-left, #detail-right { text-wrap: nowrap; text-overflow: ellipsis; }
    #detail-left { width: 1fr; height: 1fr; padding: 0 1; }
    #detail-right { width: 56; height: 1fr; padding: 0 1; border-left: solid $primary-darken-2; }
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
                yield Static(Text("repos", style="bold"), id="side-title")
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
        self.show_repo(self.repo)
        self.query_one("#tree", Tree).focus()

    # ---- the map -----------------------------------------------------------
    def show_repo(self, repo: str) -> None:
        self.repo = repo
        tree = self.query_one("#tree", Tree)
        tree.clear()
        tree.root.set_label(Text.assemble(("main", "bold"), ("  the default branch", GREY)))
        prs = REPOS[repo]
        self.apply_filter()

        def add(parent: TreeNode, pr: PR) -> None:
            node = parent.add(row(pr), data=pr, expand=True, allow_expand=bool(pr.children))
            for child in pr.children:
                add(node, child)

        for pr in prs:
            add(tree.root, pr)
        if not prs:
            tree.root.add_leaf(Text("no open pull requests or local branches", style=GREY))
        tree.root.expand()
        tree.cursor_line = 1 if prs else 0
        self.draw_strip()
        self.draw_detail(prs[0] if prs else None)

    def apply_filter(self) -> None:
        keep = FILTERS[self.filter][1]
        for root in REPOS[self.repo]:
            for pr in root.walk():
                pr.dimmed = not keep(pr)

    def draw_strip(self) -> None:
        everything = [pr for root in REPOS[self.repo] for pr in root.walk()]
        strip = Text()
        strip.append(self.repo, style="bold")
        strip.append(f"  {sum(pr.is_pr for pr in everything)} open", style=GREY)
        for state in ("ready", "approved", "review", "rereview", "changes", "conflict",
                      "draft", "local"):
            n = sum(pr.state == state for pr in everything)
            if n:
                icon, color, _, _ = LOOK[state]
                strip.append(f"  {icon}{n}", style=color)
        yours = sum(pr.next[0] == "you" for pr in everything)
        strip.append(f"    → you {yours}", style=f"bold {YOURS}")
        strip.append(f"    refreshed {self.refreshed}", style=GREY)
        if self.filter:
            strip.append(f"    showing {FILTERS[self.filter][0]} only", style=f"bold {BLUE}")
        self.query_one("#strip", Static).update(strip)

    def draw_detail(self, pr: PR | None) -> None:
        left, right = self.query_one("#detail-left", Static), self.query_one("#detail-right", Static)
        if pr is None:
            left.update(Text("main\nSelect a branch to see its names, review rounds and "
                             "who is on it.", style=GREY))
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
