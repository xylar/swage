"""The record of a run, which is what everything else renders (DESIGN.md 9).

The decisive choice here is not the layout of any report but the direction the
data flows. **`run.json` is the record, and both the terminal summary and
`swage explain` are renderings of it** -- not two computations that happen to
agree. DESIGN.md 9.2 makes this explicit for `explain`: the question being
asked is almost never "what would swage do now" but "why did it do *that*, at
03:00, while I was asleep", and an `explain` that recomputed would answer a
different question against upstream that has since moved.

So these models are the contract, and they are pydantic rather than plain
dataclasses for one reason: a past run's `run.json` is read back from disk by
a later swage (`explain --from-run`), which makes it a system boundary in
exactly the sense CLAUDE.md means. A record that has drifted should fail
loudly at the read, naming the field, rather than half-render.

`schema` is versioned per DESIGN.md 9.1 so a scheduler or a future dashboard
reads the artifact instead of scraping the terminal.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "OUTCOMES",
    "SCHEMA_VERSION",
    "FeedstockRecord",
    "GateRecord",
    "Outcome",
    "PlannedLine",
    "RunRecord",
    "SectionRecord",
    "UpstreamRecord",
]

#: Bump when a field changes meaning or disappears. Adding an optional field
#: does not need a bump -- a reader that does not know about it ignores it,
#: which is the whole point of versioning the shape rather than the content.
SCHEMA_VERSION = 1

#: The buckets of DESIGN.md 9 as `(outcome, heading, description)`, in the
#: order the report prints them: what happened without you first, what needs
#: you next, what did nothing last.
#:
#: Ordering is data because the ordering *is* the design -- DESIGN.md 9 groups
#: by outcome so the actionable items are unmissable, and a sort key hidden in
#: rendering code is a sort key nobody reviews. The headings are spelled out
#: for the same reason rather than derived from the key: `MERGE-READY` keeps
#: its hyphen where `NEEDS REVIEW` does not, and a mechanical transform that
#: got that wrong would be inventing a vocabulary the spec already fixed.
OUTCOMES: tuple[tuple[str, str, str], ...] = (
    ("merged", "MERGED", "path B: no changes needed, CI already green, merged"),
    ("merge-ready", "MERGE-READY", "path A: pushed + labeled automerge, awaiting CI"),
    (
        "awaiting-ci",
        "AWAITING CI",
        "path B candidates, CI still running -- `swage status` later",
    ),
    ("proposed", "PROPOSED", "pushed, needs your review before labeling"),
    ("needs-review", "NEEDS REVIEW", ""),
    ("degraded", "DEGRADED", "pushed but NOT labeled -- rerun `swage status`"),
    ("migrated", "MIGRATED", "v0 -> v1 converted and updated -- review both commits"),
    (
        "needs-migration",
        "NEEDS MIGRATION",
        "v0 meta.yaml -- rerun with `--migrate` to convert in place",
    ),
    ("unchanged", "UNCHANGED", "no open bot PR"),
    ("failed", "FAILED", ""),
)

#: The outcomes that mean a human still has something to do. Exit code 1 is
#: defined by this set (DESIGN.md 9.1), so it lives beside the outcomes rather
#: than inside whichever property happened to need it first.
_NEEDS_REVIEW = frozenset({"needs-review", "degraded", "failed", "needs-migration"})

Outcome = Literal[
    "merged",
    "merge-ready",
    "awaiting-ci",
    "proposed",
    "needs-review",
    "degraded",
    "migrated",
    "needs-migration",
    "unchanged",
    "failed",
]


class _Record(BaseModel):
    # Not `extra="forbid"`: a record written by a *newer* swage should still be
    # readable by this one, which is the half of forward compatibility a
    # version number cannot provide on its own. Config is the opposite case --
    # there an unknown key is a typo a human should hear about at once.
    model_config = ConfigDict(frozen=True)


class UpstreamRecord(_Record):
    """Which release was read, and out of which file.

    The file matters and is not decoration: DESIGN.md 3.6.2 reads the two
    halves of the metadata from whichever of `pyproject.toml` and `PKG-INFO`
    can state them, so "where did this dependency come from" has an answer
    that varies per archive and is not recoverable after the fact.
    """

    name: str
    version: str | None = None
    source: str = ""
    #: The version the recipe reflected before this update, which is what
    #: classifies a removal (DESIGN.md 3.3.7). None where it could not be read.
    previous: str | None = None


class PlannedLine(_Record):
    """One requirement, and what justifies it.

    Three columns, because DESIGN.md 9.2 asks for greppability above all:
    `swage explain X | grep unresolved` answers a real question, and so does
    counting `upstream-core`.
    """

    #: `keep`, `add`, `bump` or `drop` -- the first token of the rendered line,
    #: so a plan reads as a diff at a glance.
    action: str
    text: str
    origin: str
    #: A file path or a named layer, never prose, so the next step is always
    #: opening a specific file.
    source: str
    #: Set where the resolution was a guess rather than a lookup. G2 reads
    #: this, and the report prints it, because an inexact mapping is the
    #: failure hardest to notice by eye.
    exact: bool | None = None


class SectionRecord(_Record):
    path: str
    section: str
    lines: tuple[PlannedLine, ...] = ()


class GateRecord(_Record):
    name: str
    #: None where the gate does not apply -- an opt-in gate the config did not
    #: opt into, or a path this run is not on. "Not asked" and "asked and
    #: satisfied" are different claims and print differently (DESIGN.md 5.4).
    passed: bool | None = None
    detail: str = ""


class FeedstockRecord(_Record):
    """Everything swage decided about one feedstock, and why."""

    feedstock: str
    outcome: Outcome
    #: The one-line reason the summary prints beside the name. Empty for the
    #: outcomes that need none -- nobody wants 206 lines saying "no open PR".
    detail: str = ""
    #: Advice that is not a verdict (DESIGN.md 4). A `detail` says why this
    #: feedstock landed in the bucket it did; a note says something worth
    #: knowing about a feedstock whose bucket is unaffected -- an upstream
    #: extra no output draws on, where the feedstock never opted into G3's
    #: exhaustiveness. Separate from `detail` rather than appended to it,
    #: because a merge-ready feedstock has no detail to append to, and giving
    #: it one would make an advisory read as the reason it was held.
    notes: tuple[str, ...] = ()

    # INPUTS (DESIGN.md 9.2)
    recipe: str = ""
    pull_request: int | None = None
    #: How many open bot pull requests the feedstock had, where swage looked.
    #: Recorded because acting on one of four without saying so is how a
    #: maintainer discovers months later that swage has been ignoring three
    #: (DESIGN.md 3.4.1) -- and because four is where conda-forge's bot stops
    #: filing new ones, which makes the number the difference between "three
    #: superseded" and "this feedstock has stopped receiving updates".
    pull_requests: int = 0
    head: str = ""
    upstream: UpstreamRecord | None = None
    python_min: str = ""
    python_min_source: str = ""
    #: Most specific first, as the loader resolved them.
    config_layers: tuple[str, ...] = ()

    sections: tuple[SectionRecord, ...] = ()
    gates: tuple[GateRecord, ...] = ()
    label: str = ""

    #: The recipe swage would push, and the one the pull request has today.
    #: **Excluded from `run.json`**: two whole recipes per feedstock would
    #: bloat a contract other things read (DESIGN.md 9), and a file is the
    #: right shape for something you are going to `diff` anyway. `write_recipes`
    #: puts them in the run directory beside it. Empty for a feedstock that
    #: never reached a plan.
    rendered_recipe: str = Field(default="", exclude=True)
    current_recipe: str = Field(default="", exclude=True)

    #: Why swage stopped before a plan existed -- a v0 recipe (DESIGN.md 3.1),
    #: a build-variant switch (3.3.5), contradictory constraints (3.3.2). An
    #: empty plan would be the least helpful possible answer to "what
    #: happened", so a stopped feedstock still records its inputs and prints a
    #: STOPPED section instead of a PLAN one.
    stopped: str = ""

    @property
    def failures(self) -> tuple[GateRecord, ...]:
        return tuple(gate for gate in self.gates if gate.passed is False)

    @property
    def needs_review(self) -> bool:
        """Whether this feedstock wants a human -- exit code 1 (DESIGN.md 9.1).

        Defined per feedstock rather than only per run, because `explain` is
        asked about one of them and answers with the same exit code the sweep
        would have given for it. Two spellings of "wants a human" would drift.
        """
        return self.outcome in _NEEDS_REVIEW


class RunRecord(_Record):
    """One invocation of swage, whole."""

    schema_version: int = Field(default=SCHEMA_VERSION, alias="schema")
    #: The command line as invoked, so a record found later explains itself.
    command: str = ""
    started: str = ""
    feedstocks: tuple[FeedstockRecord, ...] = ()

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    def by_outcome(self, outcome: str) -> tuple[FeedstockRecord, ...]:
        return tuple(record for record in self.feedstocks if record.outcome == outcome)

    def find(self, feedstock: str) -> FeedstockRecord | None:
        return next((r for r in self.feedstocks if r.feedstock == feedstock), None)

    @property
    def needs_review(self) -> bool:
        """Whether anything in this run wants a human -- exit code 1 (9.1)."""
        return any(record.needs_review for record in self.feedstocks)
