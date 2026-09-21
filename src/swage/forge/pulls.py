"""Arming conda-forge's automerge on a pull request swage just pushed to
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
    """Give ``pull`` an `automerge` event newer than swage's commit, as the very
    next thing after a successful push and never before one (v1 §2.2, §5.5).
    """
    github.unlabel(pull.repo, pull.number, AUTOMERGE)
    github.label(pull.repo, pull.number, AUTOMERGE)
