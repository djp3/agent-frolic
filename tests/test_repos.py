# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""Repository names are parsed from what people paste, and the list is kept in its file."""
import json

import pytest

from repos import RepoStore, is_name, parse


@pytest.mark.parametrize("typed, name", [
    ("djp3/agent-frolic", "djp3/agent-frolic"),
    ("  Acme/ledger-api  ", "Acme/ledger-api"),
    ("https://github.com/djp3/agent-frolic", "djp3/agent-frolic"),
    ("https://github.com/djp3/agent-frolic.git", "djp3/agent-frolic"),
    ("https://github.com/djp3/agent-frolic/pull/17", "djp3/agent-frolic"),
    ("https://www.github.com/djp3/agent-frolic/", "djp3/agent-frolic"),
    ("git@github.com:djp3/agent-frolic.git", "djp3/agent-frolic"),
    ("ssh://git@github.com/djp3/agent-frolic.git", "djp3/agent-frolic"),
    ("github.com/djp3/agent.frolic_2", "djp3/agent.frolic_2"),
])
def test_parse_accepts_a_name_or_a_github_address(typed, name):
    assert parse(typed) == name


@pytest.mark.parametrize("typed", ["", "   ", "agent-frolic", "djp3/", "/agent-frolic", "a/b/c",
                                   "https://gitlab.com/djp3/agent-frolic", "djp3/agent frolic",
                                   "-bad/name", "djp3/..", "https://github.com/djp3"])
def test_parse_refuses_what_is_not_a_repository(typed):
    with pytest.raises(ValueError) as caught:
        parse(typed)
    assert "owner/name" in str(caught.value)


def test_a_plain_name_is_owner_slash_name_and_nothing_else():
    assert all(is_name(n) for n in ("djp3/agent-frolic", "Acme/web.console_2", "a/b"))
    assert not any(is_name(n) for n in ("", "acme", "acme/", "/web", "acme/web/x", "acme/web console",
                                        "acme/..", "acme/web\n", " acme/web", "-acme/web",
                                        "https://github.com/acme/web"))


def test_the_list_is_saved_on_every_change_and_read_back(tmp_path):
    store = RepoStore()
    store.add("djp3/agent-frolic", private=False, admin=True)
    store.add("Acme/ledger-api", private=True, admin=False)
    store.show("Acme/ledger-api", False)
    store.set_hook("djp3/agent-frolic", 42)

    again = RepoStore()
    assert [(r.name, r.shown, r.private, r.admin, r.hook_id) for r in again.repos] == [
        ("djp3/agent-frolic", True, False, True, 42), ("Acme/ledger-api", False, True, False, 0)]
    assert [r.name for r in again.shown()] == ["djp3/agent-frolic"]
    assert again.repos[1].short == "ledger-api"


def test_adding_a_repository_twice_keeps_one_entry_whatever_the_capitals():
    store = RepoStore()
    store.add("djp3/agent-frolic", admin=False)
    store.show("djp3/agent-frolic", False)
    store.set_hook("djp3/agent-frolic", 7)
    store.add("DJP3/Agent-Frolic", private=True, admin=True)        # refreshed, as GitHub now spells it
    assert [(r.name, r.shown, r.private, r.admin, r.hook_id) for r in store.repos] == [
        ("DJP3/Agent-Frolic", False, True, True, 7)]


def test_move_changes_the_order_stops_at_the_ends_and_is_saved():
    store = RepoStore()
    for name in ("o/one", "o/two", "o/three"):
        store.add(name)
    assert store.move("o/three", -1) and [r.name for r in store.repos] == ["o/one", "o/three", "o/two"]
    assert store.move("O/Three", -1) and [r.name for r in store.repos] == ["o/three", "o/one", "o/two"]
    assert not store.move("o/three", -1)                            # already first
    assert not store.move("o/two", 1) and not store.move("o/missing", 1)
    assert store.move("o/three", 1)
    assert [r.name for r in RepoStore().repos] == ["o/one", "o/three", "o/two"]


def test_remove_takes_it_out_of_the_file():
    store = RepoStore()
    store.add("djp3/agent-frolic")
    assert store.remove("djp3/AGENT-frolic").name == "djp3/agent-frolic"
    assert store.remove("djp3/agent-frolic") is None and RepoStore().repos == []


def test_a_damaged_file_is_read_as_far_as_it_makes_sense(tmp_path):
    (tmp_path / "repos.json").write_text(json.dumps({"repos": [
        {"name": "djp3/agent-frolic", "shown": "yes", "hook_id": "12", "admin": True},
        {"name": "not a repo"}, "nonsense", {"shown": True},
        {"name": "djp3/agent-frolic"}]}))
    store = RepoStore()
    assert [(r.name, r.shown, r.admin, r.hook_id) for r in store.repos] == [("djp3/agent-frolic", True, True, 0)]
    (tmp_path / "repos.json").write_text("{broken")
    assert RepoStore().repos == []


def test_the_file_holds_names_and_flags_only(tmp_path):
    store = RepoStore()
    store.add("djp3/agent-frolic", admin=True)
    saved = json.loads((tmp_path / "repos.json").read_text())
    assert saved == {"repos": [{"name": "djp3/agent-frolic", "shown": True, "private": False,
                                "admin": True, "hook_id": 0, "long_lived": None}]}


def test_the_default_branch_starts_the_long_lived_list_once_and_then_it_is_the_users():
    store = RepoStore()
    store.add("djp3/agent-frolic")                                  # GitHub has not said yet
    assert store.get("djp3/agent-frolic").long_lived is None
    store.add("djp3/agent-frolic", default_branch="main")
    assert store.get("djp3/agent-frolic").long_lived == ["main"]
    store.set_long_lived("djp3/agent-frolic", ["main, develop", " develop  release "])
    assert store.get("djp3/agent-frolic").long_lived == ["main", "develop", "release"]
    store.add("djp3/agent-frolic", default_branch="trunk")         # refreshed: the user's list stays
    assert RepoStore().get("djp3/agent-frolic").long_lived == ["main", "develop", "release"]
    store.set_long_lived("djp3/agent-frolic", [""])                 # emptied on purpose: not filled again
    store.add("djp3/agent-frolic", default_branch="main")
    assert RepoStore().get("djp3/agent-frolic").long_lived == []


def test_a_repository_renamed_on_github_keeps_its_entry_under_the_new_name():
    store = RepoStore()
    for name in ("acme/first", "acme/old", "acme/last"):
        store.add(name, default_branch="main")
    store.show("acme/old", False)
    store.set_hook("acme/old", 7)
    store.set_long_lived("acme/old", ["main", "develop"])
    store.add("Acme/new", private=True, was="acme/old")            # GitHub answered for acme/old as Acme/new
    assert [r.name for r in RepoStore().repos] == ["acme/first", "Acme/new", "acme/last"]
    renamed = RepoStore().get("acme/new")
    assert (renamed.shown, renamed.private, renamed.hook_id, renamed.long_lived) == (
        False, True, 7, ["main", "develop"])


def test_both_names_of_one_repository_become_one_entry_in_the_earlier_place():
    store = RepoStore()
    store.add("acme/old")
    store.add("acme/other")
    store.add("acme/new", default_branch="trunk")                  # the duplicate the old code made
    store.set_hook("acme/new", 9)
    store.add("acme/new", was="acme/old")
    assert [(r.name, r.hook_id, r.long_lived) for r in RepoStore().repos] == [
        ("acme/new", 9, ["trunk"]), ("acme/other", 0, None)]


def test_a_damaged_long_lived_list_keeps_the_names_that_make_sense(tmp_path):
    (tmp_path / "repos.json").write_text(json.dumps({"repos": [
        {"name": "o/one", "long_lived": ["main", 3, "", "main", "develop"]},
        {"name": "o/two", "long_lived": "main"}]}))
    assert [r.long_lived for r in RepoStore().repos] == [["main", "develop"], None]
