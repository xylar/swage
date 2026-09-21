"""What swage does with a pull request, and which bucket that is (DESIGN.md §9.8).

A pure function of four things: the findings, whether the rendering already
matches the recipe byte for byte, the feedstock's trust rung, and -- only where
nothing is to be pushed -- what CI said. Every command reaches its answer here,
including a dry run, and that is the point: an outcome is a statement about
the plan rather than about what was written (v1 §8), so `swage update` and
`swage update --dry-run` bucket a feedstock identically. A dry run that
reported a different bucket would be a rehearsal of something else.

The order of the rules is the precedence. `unchanged` is decided before the
rung because swage cannot merge on any rung and a label on a finished pull
request is inert (v1 §5.2.2, §2.1), so whether the feedstock is blessed
changes nothing a reader would do. The rung comes next because `never` is
about the feedstock rather than about the change: somebody said swage does not
write here, and no finding licenses it. A withholding finding comes before a
holding one because the first says the rendering may be wrong and the second
that a decision is outstanding about a sound one (v1 §5.4). A conversion is
pushed whatever the findings said and reviewed by a person whatever the rung
said (v1 §7).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Literal, Protocol

from swage.config import FeedstockConfig

from .findings import Finding, withheld

__all__ = ["Action", "Ci", "Decision", "Outcome", "decide", "rung_sentence"]

#: What swage does about the pull request. Only one of the three names
#: anything on GitHub: `automerge` is conda-forge's own label and swage applies
#: it. The other two are what swage states, in a comment, because no feedstock
#: has a `swage:needs-review` label and inventing one would mean creating it
#: in several hundred repositories (v1 §5.4).
Action = Literal["nothing", "push", "push-label"]

#: The vocabulary swage *writes*: thirteen outcomes (DESIGN.md §11.2). Every
#: value has a row in the report's `OUTCOMES` table, which is what prints it,
#: and `tests/test_report_artifact.py` holds the two to each other.
#:
#: v1 had seventeen. `proposed` and `degraded` are `needs-review` -- a person
#: must look either way, and the record's `reason` and `pushed` say whether
#: that is approving a diff, answering a finding or fixing a label. `archived`
#: and `unmaintained` are `skipped`, `not-reconciled` is `not-read`, and
#: `merge-ready` is `automerge`: it was one word-order away from
#: `ready-to-merge` and meant the opposite thing about who acts.
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
    #: The sentence beside the feedstock's name where the findings do not
    #: supply one: the rung, on a feedstock swage does not write to. Empty
    #: everywhere else, where the record says what changed or what was found.
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

    ``unchanged`` is whether swage's rendering matches what is already in the
    pull request; the write layer answers that, so it is passed in rather than
    guessed at here. ``ci`` is asked for only where the answer would change
    something -- a pull request with nothing to push, which nothing but a
    person will ever merge -- and None where swage did not ask.

    ``converted`` is a v0 recipe swage converted in this run. A conversion is
    a change even where it needs no dependency edit, and it gets human eyes
    whatever the findings thought of its dependencies (v1 §7).

    ``pull_request`` is False for a feedstock planned on its default branch,
    which is what an audit does (v1 §8.2). There is no CI to wait for, so a
    recipe with nothing to change and nothing found is `unchanged`; and a
    conversion is one swage would make rather than one it made, so a v0
    feedstock with nothing found is `needs-migration` -- the conversion is
    work whatever the dependencies need. A finding survives the floor,
    because it is a second thing to do.
    """
    if converted and not pull_request:
        would = decide(findings, unchanged, config, pull_request=False)
        return would if findings else replace(would, outcome="needs-migration")
    trust = config.trust
    if unchanged and not converted:
        # Nothing to push whatever the findings said, so the only question
        # left is whether a person is owed a look before *they* merge it. Three
        # answers, and they are different work for the reader -- nothing to
        # do, come back later, look now -- so they are three buckets.
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
        # Pushed whatever the findings said, including one that would withhold
        # an ordinary change: a conversion's diff touches every line of the
        # recipe, so the findings have nothing to say about it and v1 §7
        # already sends it to a person.
        return Decision("push", "needs-review")
    if withheld(findings):
        return Decision("nothing", "needs-review")
    if findings:
        return Decision("push", "needs-review")
    if trust == "auto":
        return Decision("push-label", "automerge")
    return Decision("push", "needs-review")


def rung_sentence(config: FeedstockConfig) -> str:
    """The rung, said for a comment on a pull request swage pushed and left.

    What the reader of that comment is missing is what the rung *is*, and
    the only name it uses is the `trust` setting (DESIGN.md §3.1). Empty on
    `auto`, where there is no rung to explain.
    """
    if config.trust == "auto":
        return ""
    return (
        f"`trust` is `{config.trust}` for this feedstock, which leaves the "
        "label to a person"
    )


def _never(config: FeedstockConfig) -> str:
    """The rung, said for a run that wrote nothing because of it.

    `never` is about the whole feedstock: nothing was written, and the fact
    that explains the run has to be the line beside its name. The remedy
    names a file, because the rung is a standing decision about a feedstock
    and taking it is a commit somebody makes on purpose (v1 §5.4).
    """
    stated = config.trust_source or "config/defaults.yaml"
    return (
        "`trust` is `never` for this feedstock. Remove that line from "
        f"{stated} for swage to push the change and comment"
    )
