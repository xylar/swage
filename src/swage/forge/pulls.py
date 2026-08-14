"""Arming conda-forge's automerge on a pull request swage just pushed to.

One function, and it exists because the obvious spelling of it is wrong.

**Adding a label that is already there produces no timeline event.** The
dispatched automerge job merges only if no commit appears after the most recent
`labeled` event whose label is `automerge` (DESIGN.md 2.2), so a pull request
that already carried the label before swage pushed would be measured against
the *original* event -- which is now older than swage's commit -- and the job
would strip the label and refuse. Re-adding it changes nothing, because GitHub
treats it as a no-op. Removing it and adding it back is what produces a fresh
event with a later timestamp.

**This is the only way a pull request swage has judged reaches `main`.** swage
merging one itself is not possible: GitHub refuses a merge that writes a
workflow file unless the credential swage borrows carries the `workflow`
scope, and conda-smithy re-renders one into most bot pull requests
(DESIGN.md 5.2). So conda-forge merges, and swage's job is to arm it.

Nothing here decides whether to arm anything. That is the trust ladder
(DESIGN.md 5.4), and it lives in the command.
"""

from __future__ import annotations

from .discover import BotPullRequest
from .github import GitHub

__all__ = ["AUTOMERGE", "arm_automerge"]


#: conda-forge's own label, which every feedstock already has. swage applies
#: this one and creates none of its own (DESIGN.md 5.4).
AUTOMERGE = "automerge"


def arm_automerge(github: GitHub, pull: BotPullRequest) -> None:
    """Give ``pull`` an `automerge` event newer than swage's commit.

    Called as the very next thing after a successful push and never before one
    (DESIGN.md 2.2, 5.5): labelling first guarantees the label is stripped,
    and pushing without labelling leaves a `[bot-automerge]` pull request
    *less* automated than swage found it, because swage's commit is not a
    bot's and breaks the all-commits-from-a-bot test forever.
    """
    github.unlabel(pull.repo, pull.number, AUTOMERGE)
    github.label(pull.repo, pull.number, AUTOMERGE)
