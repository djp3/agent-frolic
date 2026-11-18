# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Who must act next on a pull request, worked out from GitHub's records alone.

This is the rule, checked top to bottom; the first line that fits decides:

    what GitHub shows                          who must act     what is said
    ─────────────────────────────────────────  ───────────────  ───────────────────────────
    it is a draft                              the author       finish the draft
    it has a merge conflict                    the author       resolve the conflict
    its checks are failing                     the author       fix the checks
    its checks are still running               nobody           checks running
    reviewers were asked and have not answered those reviewers  review (re-review if each
                                                                of them reviewed before)
    changes were requested, or comments are    the author       resolve N comments
      still to resolve
    it is approved, or nothing is left to do   the author       merge; if it is stacked, it
                                                                waits on the one below
    nobody was asked and nobody has reviewed   the author       request review

Checks that fail only because GitHub did not run them for lack of payment (`checks` is
"unpaid", see pulls.py) are nobody's to fix in the code: they count as passing here.

"Nothing is left to do" means: the branch asks for no review, somebody has looked, nobody
asked for changes, and no comment is open. When the branch does ask for a review and it
has not been given, the author's move is to request one.

The last two moves need rights in the repository, and not every author has them. GitHub
lets only people with write access merge or ask for a review (an organization's triage
role may also ask). To an outside contributor, whose pull request comes from a fork, it
offers no reviewers to pick from at all. So when the author lacks the right, the move is
not theirs, and the pull request waits on the repository's maintainer instead:

    it is approved, or nothing is left to do,  the maintainer   merge
      and the author may not merge
    nobody was asked, nobody has reviewed,     the maintainer   review
      and the author may not ask

The maintainer is named by the owner in "owner/name": the one account certain to have the
rights, whether it is a person or an organization. An organization never signs in, so the
picture lets a viewer whose own rights in the repository allow the move act for it
(picture.py, `maintains`).

A stacked pull request that is ready to merge cannot merge before the one it stands on,
so it waits on that one, and through it on whoever that one waits on (`settle`).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from pulls import PullRequest


@dataclass
class Standing:
    pull: PullRequest
    who: list[str]                # who must act: logins, or "team/org/slug"; empty when nobody must
    what: str                     # their move, in words
    by: str                       # "author" | "reviewers" | "maintainer" | "nobody"
    below: int = 0                # set when it only waits on the pull request it is stacked on
    chain: list[int] = field(default_factory=list)   # the pull requests it waits through, nearest first
    end: "Standing | None" = None  # where the wait ends: the standing that names who really must act

    @property
    def queued(self) -> bool:
        return bool(self.chain)

    @property
    def others(self) -> bool:
        """Its own move belongs to someone other than its author."""
        return self.by in ("reviewers", "maintainer")

    @property
    def movers(self) -> list[str]:
        """Who must really act for this one to move: its own movers, or, when it waits on
        the pull requests below it, the movers at the end of that wait."""
        return (self.end or self).who

    @property
    def move(self) -> str:
        return (self.end or self).what


def standing(pull: PullRequest) -> Standing:
    """The rule, for one pull request on its own."""
    author = [pull.author]
    maintainer = [pull.repo.split("/", 1)[0]]
    if pull.draft:
        return Standing(pull, author, "finish the draft", "author")
    if pull.mergeable == "conflict":
        return Standing(pull, author, "resolve the conflict", "author")
    if pull.checks == "fail":
        return Standing(pull, author, "fix the checks", "author")
    if pull.checks == "running":
        return Standing(pull, [], "checks running", "nobody")
    if pull.asked:
        reviewed = {review.who for review in pull.reviews}
        again = all(who in reviewed for who in pull.asked)
        return Standing(pull, list(pull.asked), "re-review" if again else "review", "reviewers")
    if pull.decision == "changes" or pull.comments_open:
        n = pull.comments_open
        what = f"resolve {n} comment{'' if n == 1 else 's'}" if n else "address the requested changes"
        return Standing(pull, author, what, "author")
    if pull.decision == "approved" or (pull.decision == "" and pull.reviews):
        if pull.can_merge:
            return Standing(pull, author, "merge", "author", below=pull.parent)
        return Standing(pull, maintainer, "merge", "maintainer", below=pull.parent)
    if pull.can_ask:
        return Standing(pull, author, "request review", "author")
    return Standing(pull, maintainer, "review", "maintainer")


def settle(pulls: list[PullRequest]) -> dict[int, Standing]:
    """The rule for every pull request of one repository, with the waits followed: one
    that only waits on the pull request below it gets that one's chain and its end."""
    found = {pull.number: standing(pull) for pull in pulls}
    for here in found.values():
        at, seen = here, {here.pull.number}
        while at.below and at.below in found and at.below not in seen:
            seen.add(at.below)
            here.chain.append(at.below)
            at = found[at.below]
        if here.chain:
            here.end = at
    return found
