"""What swage does with a pull request, and which bucket that is (DESIGN.md
§9.8).

A pure function of the findings, whether the rendering already matches the
recipe, the trust rung, and, where nothing is to be pushed, what CI said. A dry
run reaches the same answer. The order of the rules is the precedence.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Literal, Protocol

from swage.config import FeedstockConfig

from .findings import Finding, withheld

__all__ = ["Action", "Ci", "Decision", "Outcome", "decide", "rung_sentence"]

#: What swage does about the pull request. Only `automerge` names anything on
#: GitHub; the other two are stated in a comment (v1 §5.4).
Action = Literal["nothing", "push", "push-label"]

#: The vocabulary swage *writes*: thirteen outcomes (DESIGN.md §11.2). Every
#: value has a row in the report's `OUTCOMES` table, which is what prints it,
#: and `tests/test_run_artifact.py` holds the two to each other.
Outcome = Literal[
    "merged",
    "closed",
    "ready-to-merge",
    "automerge",
    "awaiting-ci",
    "needs-review",
    "unchanged",
    "skipped",
    "not-read",
    "declaration-moved",
    "needs-migration",
    "migrated",
    "failed",
]


class Ci(Protocol):
    """What CI said about a pull request swage has nothing to push to.

    `forge.checks.CiStatus` is one; the shape is stated here because the plan
    layer does not read GitHub.
    """

    @property
    def pending(self) -> bool:
        """True where the only reason it is not verified is that CI has not finished."""
        ...

    @property
    def verified(self) -> bool: ...


@dataclass(frozen=True)
class Decision:
    """What to do, which bucket, and the sentence where no finding supplies one."""

    action: Action
    outcome: Outcome
    #: The sentence beside the feedstock's name where the findings do not supply
    #: one: the rung, on a feedstock swage does not write to.
    reason: str = ""

    @property
    def labels(self) -> bool:
        return self.action == "push-label"

    @property
    def pushes(self) -> bool:
        return self.action != "nothing"


def decide(
    findings: Sequence[Finding],
    unchanged: bool,
    config: FeedstockConfig,
    ci: Ci | None = None,
    converted: bool = False,
    pull_request: bool = True,
) -> Decision:
    """The decision for one pull request, or one feedstock (DESIGN.md §9.8).

    ``unchanged`` is byte identity with the recipe in the pull request.
    ``ci`` is None where swage did not ask. ``converted`` is a v0 recipe
    swage converted in this run (v1 §7). ``pull_request`` is False for a
    feedstock planned on its default branch, which is what an audit does
    (v1 §8.2): `unchanged` where a pull request would wait on CI, and
    `needs-migration` for a v0 feedstock, a finding surviving either.
    """
    if converted and not pull_request:
        would = decide(findings, unchanged, config, pull_request=False)
        return would if findings else replace(would, outcome="needs-migration")
    trust = config.trust
    if unchanged and not converted:
        # Nothing to push whatever the findings said; the three answers are
        # different work for the reader.
        if findings:
            return Decision("nothing", "needs-review")
        if not pull_request:
            return Decision("nothing", "unchanged")
        if ci is None or ci.pending:
            return Decision("nothing", "awaiting-ci")
        return Decision("nothing", "ready-to-merge" if ci.verified else "needs-review")
    if trust == "never":
        return Decision("nothing", "needs-review", _never(config))
    if converted:
        # Pushed whatever the findings said (v1 §7).
        return Decision("push", "needs-review")
    if withheld(findings):
        return Decision("nothing", "needs-review")
    if findings:
        return Decision("push", "needs-review")
    if trust == "auto":
        return Decision("push-label", "automerge")
    return Decision("push", "needs-review")


def rung_sentence(config: FeedstockConfig) -> str:
    """The rung, said for a comment on a pull request swage pushed and left
    (DESIGN.md §3.1). Empty on `auto`.
    """
    if config.trust == "auto":
        return ""
    return (
        f"`trust` is `{config.trust}` for this feedstock, which leaves the "
        "label to a person"
    )


def _never(config: FeedstockConfig) -> str:
    """The rung, said for a run that wrote nothing because of it. The remedy
    names the file, because the rung is a standing decision (v1 §5.4).
    """
    stated = config.trust_source or "config/defaults.yaml"
    return (
        "`trust` is `never` for this feedstock. Remove that line from "
        f"{stated} for swage to push the change and comment"
    )
