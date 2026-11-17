# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""Settings load tolerantly and save where they are told to."""
import json
import os

from repos import RepoStore
from settings import Settings


def test_defaults_when_there_is_no_file():
    s = Settings.load()
    assert (s.webhooks, s.ngrok_domain, s.poll_minutes) == (False, "", 5)


def test_saved_settings_come_back(tmp_path):
    s = Settings(webhooks=True, ngrok_domain="frolic.ngrok-free.dev", poll_minutes=3)
    s.save()
    assert Settings.load() == s
    assert json.loads((tmp_path / "settings.json").read_text())["ngrok_domain"] == "frolic.ngrok-free.dev"


def test_wrong_types_and_unknown_keys_are_ignored(tmp_path):
    (tmp_path / "settings.json").write_text(json.dumps(
        {"webhooks": "yes", "poll_minutes": True, "ngrok_domain": "ok.ngrok-free.dev", "surprise": 1}))
    s = Settings.load()
    assert (s.webhooks, s.poll_minutes, s.ngrok_domain) == (False, 5, "ok.ngrok-free.dev")


def test_the_poll_stays_between_a_minute_and_a_day(tmp_path):
    """0 or less would poll every second and use up GitHub's hourly allowance in minutes."""
    for written, kept in ((0, 1), (-5, 1), (1, 1), (30, 30), (100_000, 24 * 60)):
        (tmp_path / "settings.json").write_text(json.dumps({"poll_minutes": written}))
        assert Settings.load().poll_minutes == kept, written


def test_a_broken_file_gives_defaults(tmp_path):
    (tmp_path / "settings.json").write_text("{not json")
    assert Settings.load() == Settings()


def files(folder):
    return {p.name: p.read_text() for p in folder.iterdir()}


def test_a_save_leaves_the_file_and_nothing_beside_it(tmp_path):
    Settings(poll_minutes=7).save()
    RepoStore().add("acme/web", admin=True)
    assert sorted(files(tmp_path)) == ["repos.json", "settings.json"]
    assert files(tmp_path)["settings.json"].endswith("}\n")


def test_a_save_that_cannot_finish_keeps_the_last_file_whole_and_leaves_nothing_behind(tmp_path, monkeypatch,
                                                                                        caplog):
    settings, store = Settings(poll_minutes=7), RepoStore()
    settings.save()
    store.add("acme/web", admin=True)
    before = files(tmp_path)

    def refuse(src, dst):                                       # a read-only folder, a full disk
        raise OSError(30, "Read-only file system")

    monkeypatch.setattr(os, "replace", refuse)
    settings.poll_minutes = 9
    settings.save()
    store.add("acme/api")
    assert files(tmp_path) == before                            # the same two files, as they were
    assert caplog.text.count("could not write") == 2
    assert store.get("acme/api") is not None and settings.poll_minutes == 9   # the app goes on with them
