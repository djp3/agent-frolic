# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""
The repositories agent-frolic knows about, and which of them are on screen.

They are kept in repos.json beside the settings (override the path with
AGENT_FROLIC_REPOS). The file holds repository names, which for a private repository is
itself private, so from a checkout it is ignored by git. It holds no secret.

A repository is added by name ("owner/name") or by pasting its GitHub address; `parse`
accepts both. What GitHub said about it when it was added (private, whether the token's
user may administer it) is remembered so the screen can say why a webhook can or cannot
be set up without asking again.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from apppaths import data_dir, write_json

log = logging.getLogger("agent_frolic.repos")

DEFAULT_PATH = data_dir() / "repos.json"

_OWNER = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})"
_NAME = r"[A-Za-z0-9._-]{1,100}"
_PLAIN = re.compile(rf"^({_OWNER})/({_NAME})$")
_ADDRESS = re.compile(rf"^(?:https?://(?:www\.)?github\.com/|git@github\.com:|ssh://git@github\.com/|github\.com/)"
                      rf"({_OWNER})/({_NAME}?)(?:\.git)?(?:[/?#].*)?$")


def parse(text: str) -> str:
    """ "owner/name" from what the user typed or pasted: the name itself, or a GitHub
    address (https, ssh, with or without .git, with or without a path after it). Raises
    ValueError with a sentence to show."""
    text = text.strip()
    if not text:
        raise ValueError("Type a repository as owner/name, or paste its GitHub address.")
    match = _PLAIN.match(text) or _ADDRESS.match(text)
    if match is None:
        raise ValueError(f"“{text}” is not a repository name. Use owner/name, or paste its GitHub address.")
    owner, name = match.group(1), match.group(2)
    if name.endswith(".git"):
        name = name[:-4]
    if name in (".", "..") or not name:
        raise ValueError(f"“{text}” is not a repository name. Use owner/name, or paste its GitHub address.")
    return f"{owner}/{name}"


def is_name(text: str) -> bool:
    """Whether this is exactly an "owner/name" as GitHub allows them (letters, digits, and
    in the name '.', '_' and '-'), with nothing before, after or between."""
    match = _PLAIN.fullmatch(text)
    return match is not None and match.group(2) not in (".", "..")


def branches(names) -> list[str]:
    """Branch names from what was typed ("main, develop") or read from the file: split at
    spaces and commas, without blanks, each once, in the order given. Git refuses a space
    in a branch name; it allows a comma, but hardly anyone uses one."""
    out: list[str] = []
    for name in names:
        for part in re.split(r"[\s,]+", name):
            if part and part not in out:
                out.append(part)
    return out


@dataclass
class Repo:
    name: str                     # "owner/name", spelled the way GitHub spells it
    shown: bool = True            # on screen
    private: bool = False
    admin: bool = False           # the token's user may administer it: a webhook can be set up
    hook_id: int = 0              # the webhook agent-frolic made there, 0 when there is none
    # Branches that are only ever landed on ("main", "develop"): a pull request that comes
    # from one of them is never drawn as a stack that others are built on. The default
    # branch is put here the first time GitHub is asked about the repository; after that
    # the list is the user's (b on the setup screen). None until then.
    long_lived: list[str] | None = None

    @property
    def short(self) -> str:
        return self.name.split("/", 1)[1]


class RepoStore:
    """The list, in the user's order: a new repository goes to the end, and `move` changes
    its place. Every change is saved at once."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path or os.environ.get("AGENT_FROLIC_REPOS") or DEFAULT_PATH)
        self.repos: list[Repo] = []
        self.load()

    # ---- the file ----------------------------------------------------------------------
    def load(self) -> None:
        self.repos = []
        try:
            data = json.loads(self.path.read_text())
        except FileNotFoundError:
            return
        except Exception as e:
            log.warning("could not read %s: %r (starting with no repositories)", self.path, e)
            return
        known = {f.name: f.type for f in fields(Repo)}
        kinds = {"str": str, "bool": bool, "int": int}
        for item in data.get("repos", []) if isinstance(data, dict) else []:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                continue
            try:
                name = parse(item["name"])
            except ValueError:
                continue
            repo = Repo(name)
            for key, kind in known.items():
                if kind in kinds and key != "name" and type(item.get(key)) is kinds[kind]:
                    setattr(repo, key, item[key])
            if isinstance(item.get("long_lived"), list):
                repo.long_lived = branches(b for b in item["long_lived"] if isinstance(b, str))
            if self.get(name) is None:
                self.repos.append(repo)

    def save(self) -> None:
        write_json(self.path, {"repos": [asdict(r) for r in self.repos]})

    # ---- the list ----------------------------------------------------------------------
    def get(self, name: str) -> Repo | None:
        """By name, whatever the capitals: GitHub treats Owner/Name and owner/name as one."""
        low = name.lower()
        return next((r for r in self.repos if r.name.lower() == low), None)

    def add(self, name: str, private: bool = False, admin: bool = False, default_branch: str = "",
            was: str = "") -> Repo:
        """Add a repository, or refresh what is known about one that is already there (it
        keeps its place, whether it is shown, its webhook and its long-lived branches). The
        default branch starts the list of long-lived branches, once.

        `was` is the name the entry was asked about by. GitHub answers for a renamed or
        transferred repository under its new name, so that entry takes the new name; when
        both names are in the list they are one repository, and become one entry, in the
        earlier one's place. Its webhook moved with it on GitHub."""
        repo, other = (self.get(was) if was else None), self.get(name)
        if repo is None:
            repo = other
        elif other is not None and other is not repo:
            repo, drop = sorted((repo, other), key=self.repos.index)
            repo.hook_id = repo.hook_id or drop.hook_id
            repo.shown = repo.shown or drop.shown
            if repo.long_lived is None:
                repo.long_lived = drop.long_lived
            self.repos.remove(drop)
        if repo is None:
            repo = Repo(name)
            self.repos.append(repo)
        repo.name, repo.private, repo.admin = name, private, admin
        if repo.long_lived is None and default_branch:
            repo.long_lived = [default_branch]
        self.save()
        return repo

    def set_long_lived(self, name: str, names: list[str]) -> None:
        repo = self.get(name)
        if repo is not None:
            repo.long_lived = branches(names)
            self.save()

    def remove(self, name: str) -> Repo | None:
        repo = self.get(name)
        if repo is not None:
            self.repos.remove(repo)
            self.save()
        return repo

    def move(self, name: str, step: int) -> bool:
        """Move a repository up the list (step -1) or down it (step 1), stopping at the
        ends. Says whether it moved."""
        repo = self.get(name)
        if repo is None:
            return False
        at = self.repos.index(repo)
        to = max(0, min(len(self.repos) - 1, at + step))
        if to == at:
            return False
        self.repos.insert(to, self.repos.pop(at))
        self.save()
        return True

    def show(self, name: str, on: bool) -> None:
        repo = self.get(name)
        if repo is not None and repo.shown != on:
            repo.shown = on
            self.save()

    def set_hook(self, name: str, hook_id: int) -> None:
        repo = self.get(name)
        if repo is not None and repo.hook_id != hook_id:
            repo.hook_id = hook_id
            self.save()

    def shown(self) -> list[Repo]:
        return [r for r in self.repos if r.shown]
