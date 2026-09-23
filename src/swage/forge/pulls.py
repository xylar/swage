"""Arming conda-forge's automerge on a pull request whose CI is still running
(docs/conda-forge.md; v1 §2.2).

Re-adding a label that is already there produces no timeline event, so the label
is removed and added back. Nothing here decides whether to arm anything (v1
§5.4).
"""

from __future__ import annotations

from .discover import BotPullRequest
from .github import GitHub

__all__ = ["AUTOMERGE", "arm_automerge"]


#: conda-forge's own label, which every feedstock already has. swage applies
#: this one and creates none of its own (design-v1.md 5.4).
AUTOMERGE = "automerge"


def arm_automerge(github: GitHub, pull: BotPullRequest) -> None:
    """Give ``pull`` an `automerge` event newer than any commit on it: the very
    next thing after a successful push, never before one, and on its own where
    swage had nothing to push (v1 §2.2, §5.5; DESIGN.md §9.8).
    """
    github.unlabel(pull.repo, pull.number, AUTOMERGE)
    github.label(pull.repo, pull.number, AUTOMERGE)
