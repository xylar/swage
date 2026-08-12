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

#: The buckets of DESIGN.md 9, in the order that report prints them: what
#: happened without you first, what needs you next, what did nothing last.
#: Ordering is data because the ordering *is* the design -- the actionable
#: items have to be unmissable, and a sort key hidden in rendering code is a
#: sort key nobody reviews.
OUTCOMES: tuple[tuple[str, str], ...] = (
    ("merged", "path B: no changes needed, CI already green, merged"),
    ("merge-ready", "path A: pushed + labeled automerge, awaiting CI"),
    ("awaiting-ci", "path B candidates, CI still running -- `swage status` later"),
    ("proposed", "pushed, needs your review before labeling"),
    ("needs-review", ""),
    ("degraded", "pushed but NOT labeled -- rerun `swage status`"),
    ("migrated", "v0 -> v1 converted and updated -- review both commits"),
    (
        "needs-migration",
        "v0 meta.yaml -- rerun with `--migrate` to convert in place",
    ),
    ("unchanged", "no open bot PR"),
    ("failed", ""),
)

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

    # INPUTS (DESIGN.md 9.2)
    recipe: str = ""
    pull_request: int | None = None
    head: str = ""
    upstream: UpstreamRecord | None = None
    python_min: str = ""
    python_min_source: str = ""
    #: Most specific first, as the loader resolved them.
    config_layers: tuple[str, ...] = ()

    sections: tuple[SectionRecord, ...] = ()
    gates: tuple[GateRecord, ...] = ()
    label: str = ""

    #: Why swage stopped before a plan existed -- a v0 recipe (DESIGN.md 3.1),
    #: a build-variant switch (3.3.5), contradictory constraints (3.3.2). An
    #: empty plan would be the least helpful possible answer to "what
    #: happened", so a stopped feedstock still records its inputs and prints a
    #: STOPPED section instead of a PLAN one.
    stopped: str = ""

    @property
    def failures(self) -> tuple[GateRecord, ...]:
        return tuple(gate for gate in self.gates if gate.passed is False)


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
        return any(
            record.outcome in {"needs-review", "degraded", "failed", "needs-migration"}
            for record in self.feedstocks
        )
