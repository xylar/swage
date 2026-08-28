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
    "CheckRecord",
    "FeedstockRecord",
    "GateRecord",
    "MergeCheckRecord",
    "Outcome",
    "PlannedLine",
    "RunRecord",
    "SectionRecord",
    "UpstreamRecord",
    "is_known",
]

#: Bump when a field changes meaning or disappears. Adding an optional field
#: does not need a bump -- a reader that does not know about it ignores it,
#: which is the whole point of versioning the shape rather than the content.
SCHEMA_VERSION = 4

#: The buckets of DESIGN.md 9 as `(outcome, heading, description)`, in the
#: order the report prints them: what happened without you first, what needs
#: you next, what did nothing last.
#:
#: The descriptions say what happened, never which path of the design it took.
#: "path A" and "path B" are how DESIGN.md 5 tells the two halves of the write
#: apart and they are exactly the sort of shorthand a report may not use: the
#: reader wants to know whether conda-forge will merge this or whether swage
#: has to, and both of those can be said outright.
#:
#: Ordering is data because the ordering *is* the design -- DESIGN.md 9 groups
#: by outcome so the actionable items are unmissable, and a sort key hidden in
#: rendering code is a sort key nobody reviews. The headings are spelled out
#: for the same reason rather than derived from the key: `MERGE-READY` keeps
#: its hyphen where `NEEDS REVIEW` does not, and a mechanical transform that
#: got that wrong would be inventing a vocabulary the spec already fixed.
#:
#: `merged` and `closed` are only ever reached by `status`, because they are
#: answers about a pull request rather than about a plan: no amount of reading
#: upstream metadata says whether somebody pressed the button. They are still
#: in this list rather than in one of their own, so that a `status` run and an
#: `update` run stay two `run.json` a reader can compare (DESIGN.md 8).
OUTCOMES: tuple[tuple[str, str, str], ...] = (
    ("merged", "MERGED", "landed since the run that made it -- nothing further"),
    (
        "ready-to-merge",
        "READY TO MERGE",
        "nothing to change and CI is green -- merge these yourself",
    ),
    (
        "merge-ready",
        "MERGE-READY",
        "pushed + labeled automerge; conda-forge merges it on green CI",
    ),
    # The one bucket where the `automerge` label still does something. It is
    # inert on a pull request whose CI has finished, because conda-forge
    # dispatches its automerge job from CI status events (DESIGN.md 2.1) --
    # and here CI has not finished, so the events still to come would dispatch
    # it for whoever labels the pull request first. swage is not that (path B
    # pushes nothing and labels nothing, DESIGN.md 5.2), so the sentence hands
    # the window to the reader. It closes when CI does, which is the moment
    # this bucket becomes READY TO MERGE.
    (
        "awaiting-ci",
        "AWAITING CI",
        "no changes needed; `automerge` is yours to add while CI runs",
    ),
    ("proposed", "PROPOSED", "pushed, needs your review before labeling"),
    ("needs-review", "NEEDS REVIEW", ""),
    # No "rerun `swage status`": labeling this now would do nothing. conda-forge
    # dispatches its automerge from CI status events, so a label added after CI
    # has finished summons nothing and the pull request sits open forever
    # (DESIGN.md 2.1). A person is the only thing that will merge it.
    ("degraded", "DEGRADED", "pushed but NOT labeled -- merge it yourself"),
    ("migrated", "MIGRATED", "v0 -> v1 converted and updated -- review both commits"),
    (
        "needs-migration",
        "NEEDS MIGRATION",
        "v0 meta.yaml -- rerun with `--migrate` to convert in place",
    ),
    ("unchanged", "UNCHANGED", "no open bot PR"),
    (
        "archived",
        "ARCHIVED",
        "read-only on GitHub -- nothing can be pushed to or merged into these",
    ),
    (
        "unmaintained",
        "UNMAINTAINED",
        "config says nobody maintains these -- swage reads no further",
    ),
    (
        "declaration-moved",
        "DECLARATION MOVED",
        "upstream's declaration changed and swage does not read it -- read it yourself",
    ),
    (
        "not-reconciled",
        "NOT RECONCILED",
        "packages no python distribution -- the config says what it does build",
    ),
    (
        "not-read",
        "NOT READ",
        "swage does not read these declarations -- the config says where they are",
    ),
    ("closed", "CLOSED", "closed without merging -- swage's work was not taken"),
    ("failed", "FAILED", ""),
)

#: The outcomes that mean a human still has something to do. Exit code 1 is
#: defined by this set (DESIGN.md 9.1), so it lives beside the outcomes rather
#: than inside whichever property happened to need it first.
_NEEDS_REVIEW = frozenset(
    {
        "needs-review",
        "degraded",
        "failed",
        "needs-migration",
        # The declaration this feedstock's config points at is not the one that
        # was last read, and swage cannot say what changed in it. Nobody but a
        # person can close that, which is what exit code 1 means (DESIGN.md 9.1).
        "declaration-moved",
    }
)


def is_known(outcome: str) -> bool:
    """Whether this swage has a row in `OUTCOMES` for the outcome.

    False for a run written by a newer swage that reached an outcome this one
    was built before. That is a supported state rather than a corrupt file, so
    everything reading a record has to have an answer for it.
    """
    return any(outcome == known for known, _, _ in OUTCOMES)


#: The vocabulary swage *writes*. Every value here has a row in `OUTCOMES`,
#: and `tests/test_report_model.py` holds the two lists to each other -- they
#: are the same seventeen strings maintained twice, and a value in one and not
#: the other is a bucket that never prints or a heading nothing lands in.
#:
#: Deliberately not what swage *reads*: `FeedstockRecord.outcome` is a plain
#: `str`, because a run written by a newer swage names outcomes this one has
#: no row for, and refusing the value would fail the whole file.
Outcome = Literal[
    "merged",
    "closed",
    "ready-to-merge",
    "merge-ready",
    "awaiting-ci",
    "proposed",
    "needs-review",
    "degraded",
    "migrated",
    "needs-migration",
    "unchanged",
    #: Archived on GitHub, so nothing swage does could ever land. Quiet: it is
    #: a fact about the repository rather than anything wrong with the recipe,
    #: and un-archiving it is the only thing that would change the answer.
    "archived",
    #: `config/feedstocks/<name>.yaml` says nobody maintains it. The same
    #: answer as the one above and a different source: that one is GitHub's
    #: fact, this one is a decision written down before GitHub carries it.
    "unmaintained",
    #: The feedstock builds something whose dependencies are declared where
    #: swage has no reader -- and its config says so, which is what makes this
    #: an answer rather than a failure.
    "not-reconciled",
    #: swage has no reader for this feedstock's declaration and config says
    #: where it is instead (DESIGN.md 3.6.8). Quiet: nothing has gone wrong and
    #: nothing needs doing, which is what separates it from the one below.
    "not-read",
    #: The same, and the declaration moved between the release the recipe
    #: reflects and the one it is being bumped to.
    "declaration-moved",
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
    #: Where the release came from -- the archive URL, or the repo and tag for
    #: a feedstock whose metadata is read out of a git tag.
    source: str = ""
    #: Which file inside it stated the dependencies, relative to the archive's
    #: top-level directory, and several joined by ` + ` where several were
    #: needed. Separate from `source` because they answer different questions:
    #: a tarball URL says which release, and a reader who wants to check a
    #: dependency has then to find the file among thousands.
    #:
    #: Empty for a record written before this was carried, and for one that
    #: stopped before any metadata was read.
    declared_in: str = ""
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
    #: Where the block is in the parsed document. A stable key for this
    #: artifact and for the writer, and never printed -- renderers use
    #: `where`, for the same reason `GateRecord` prints `title` and not
    #: `name`.
    path: str
    section: str
    #: The same section in words: `` `pyproj`'s `host` requirements ``.
    #: Carried in the record rather than rebuilt at render time, so a run.json
    #: read back later still says what it meant.
    where: str = ""
    lines: tuple[PlannedLine, ...] = ()


class GateRecord(_Record):
    #: The identifier -- `G1`, `G6` -- which is a stable key for this artifact
    #: and for the code, and is never printed. Renderers use `title`.
    name: str
    #: What the check asks, in words. Carried in the record rather than looked
    #: up at render time so that a run.json read back by a later swage still
    #: says what it meant, even if a check has since been reworded.
    title: str = ""
    #: None where the check does not apply -- an opt-in one the config did not
    #: opt into, or a path this run is not on. "Not asked" and "asked and
    #: satisfied" are different claims and print differently (DESIGN.md 5.4).
    passed: bool | None = None
    detail: str = ""


class CheckRecord(_Record):
    """One CI provider swage waited on, and what it reported."""

    name: str
    #: `passed`, `failed` or `pending` -- a word rather than a flag, because
    #: "has not finished" is a third answer and the one a fresh pull request
    #: usually gives.
    state: str


class MergeCheckRecord(_Record):
    """Whether CI says this pull request may be merged (DESIGN.md 5.2).

    Recorded whole rather than reduced to the outcome, because this is the
    evidence for the one action swage takes that nobody reviews. Somebody
    auditing a merge afterwards wants the list swage checked and the reason it
    was satisfied, months later, out of the artifact rather than out of a
    GitHub page that has since changed.
    """

    verified: bool
    #: Empty where swage would merge; otherwise a sentence that stands alone.
    reason: str = ""
    checks: tuple[CheckRecord, ...] = ()


class FeedstockRecord(_Record):
    """Everything swage decided about one feedstock, and why."""

    feedstock: str
    #: `str` rather than `Outcome`, and that is the read side of the same
    #: decision `_Record` makes about unknown fields. A `Literal` here fails
    #: validation for the *whole file* over one feedstock, so a run in which a
    #: newer swage reached one outcome this one lacks would take `explain` down
    #: for the other 486 -- and `SCHEMA_VERSION` is no help, because it
    #: versions the shape and a new outcome does not change the shape.
    #:
    #: Unknown does not mean ignorable: `needs_review` counts it, and the
    #: report prints it in a bucket of its own rather than dropping it.
    outcome: str
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
    #:
    #: `0` means swage did not count, which is what `status` records: it
    #: follows one pull request by number and never lists the feedstock's.
    pull_requests: int = 0
    head: str = ""
    upstream: UpstreamRecord | None = None
    python_min: str = ""
    python_min_source: str = ""
    #: Most specific first, as the loader resolved them.
    config_layers: tuple[str, ...] = ()

    sections: tuple[SectionRecord, ...] = ()
    gates: tuple[GateRecord, ...] = ()
    #: None where swage never asked -- which is every feedstock it has a change
    #: to push, since there CI is conda-forge's business rather than swage's
    #: (DESIGN.md 5.1), and every one a gate already stopped.
    merge_check: MergeCheckRecord | None = None
    #: `automerge` or `needs-review` -- what the gates decided. Only the first
    #: names a label; a needs-review verdict is stated in a comment, because no
    #: feedstock has a label for it (DESIGN.md 5.4). Kept separate from
    #: `outcome` because they answer different questions: this is what swage
    #: meant to do, and the outcome is what became of it.
    decision: str = ""
    #: The commit swage pushed to the pull request, where it pushed one. Kept
    #: beside `head` rather than replacing it, because they answer different
    #: questions: `head` is the commit the plan was computed against, and this
    #: is the one swage created from it. `swage status` needs both to tell its
    #: own commit from a later bot one.
    pushed: str = ""

    #: The recipe swage would push, and the one the pull request has today.
    #: **Excluded from `run.json`**: two whole recipes per feedstock would
    #: bloat a contract other things read (DESIGN.md 9), and a file is the
    #: right shape for something you are going to `diff` anyway. `write_recipes`
    #: puts them in the run directory beside it. Empty for a feedstock that
    #: never reached a plan.
    rendered_recipe: str = Field(default="", exclude=True)
    current_recipe: str = Field(default="", exclude=True)

    #: What this release did to the files a feedstock with no reader declares
    #: in (DESIGN.md 3.6.8), as a unified diff. **Excluded from `run.json`**
    #: for the reason the recipes above are: a diff is a thing you read, and a
    #: `configure.ac` is long enough that carrying two of them per feedstock
    #: would bloat a contract other things parse. `write_declarations` puts it
    #: in the run directory, and the summary prints its first lines inline.
    declaration_diff: str = Field(default="", exclude=True)

    #: Why swage stopped before a plan existed -- a v0 recipe (DESIGN.md 3.1),
    #: a conditional `noarch` (3.3.5), contradictory constraints (3.3.2). An
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

        **An outcome this swage has no row for counts too.** Exit code 0 is a
        claim that nothing needs the reader, and a record swage cannot classify
        is not evidence for it -- so an unrecognized outcome resolves the way
        every other unrecognized thing in swage does, toward telling somebody.
        """
        return self.outcome in _NEEDS_REVIEW or not is_known(self.outcome)


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
