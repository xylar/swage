"""One record per feedstock, and the one function that makes it (DESIGN.md §11.1).

The decisive choice here is not the layout of any report but the direction the
data flows. **`run.json` is the record, and both the terminal summary and
`swage explain` are renderings of it** -- not two computations that happen to
agree. v1 §9.2 makes this explicit for `explain`: the question being asked is
almost never "what would swage do now" but "why did it do *that*, at 03:00,
while I was asleep", and an `explain` that recomputed would answer a
different question against upstream that has since moved.

So these models are the contract, and they are pydantic rather than plain
dataclasses for one reason: a past run's `run.json` is read back from disk by
a later swage (`explain --from-run`, `swage trust`), which makes it a system
boundary in exactly the sense CLAUDE.md means. Reading is permissive and
writing is closed (v1 §9): a field this swage does not know is ignored, and an
outcome it has no row for is kept verbatim, rendered in a bucket of its own,
and counts as needing a person.

**A run written before this schema is read through a mapping**, `from_v1`,
rather than refused. The 593 recorded runs are what `swage trust` reads its
evidence out of, and v2 changed the record's shape without changing what any
of those runs found: their `gates` are this schema's `findings`, their
`detail` is its `reason`, and their outcomes are §11.2's under the second
column of its table.

The one thing `record` computes rather than copies is the **action** on each
line -- `keep`, `add`, `bump` or `drop` -- because a plan says what a section
should be and the record has to say what changed. That needs the recipe as
well as the plan, which is why the recipe is passed in rather than the planner
being asked to remember it. **The outcome is a parameter, not a computation.**
Which bucket a feedstock lands in depends on what the command did with it --
pushed, labeled, or nothing at all because `scan` never writes -- and that is
the command's knowledge rather than the record's.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Iterator, Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from swage.forge import CiStatus
from swage.plan import (
    CHECKS,
    Decision,
    Finding,
    Output,
    Plan,
    PlannedEntry,
    PlannedRequirement,
    Removal,
    Unexplained,
    by_kind,
    first_name,
    parse_line,
    spec_key,
    summarize,
)
from swage.plan import Outcome as _Outcome
from swage.recipe import Entry, Recipe, Requirement, inline_text
from swage.upstream import RecipeUpstream

__all__ = [
    "OUTCOMES",
    "SCHEMA_VERSION",
    "V1_OUTCOMES",
    "V1_SCHEMAS",
    "CheckRecord",
    "FindingRecord",
    "MergeCheckRecord",
    "Outcome",
    "OutputRecord",
    "PlannedLine",
    "Record",
    "Run",
    "SectionRecord",
    "UpstreamRecord",
    "compact",
    "declaration_diff",
    "from_v1",
    "is_known",
    "record",
    "was_shortened",
]

#: Bump when a field changes meaning or disappears. Adding an optional field
#: does not need a bump -- a reader that does not know about it ignores it,
#: which is the whole point of versioning the shape rather than the content.
#:
#: 5 is the first v2 schema. DESIGN.md §11.1 was drafted against v1's design,
#: which numbered the record 1; v1's code had reached 4 by the time it was
#: frozen, and the number in the file is the one that has to be unique.
SCHEMA_VERSION = 5

#: The schemas v1 wrote, every one of which is read through `from_v1`. The
#: four differ from each other in fields v2 does not carry -- 1 has no
#: `decision`, 2 no `merge_check` -- and not in anything the mapping reads.
V1_SCHEMAS = frozenset({1, 2, 3, 4})

#: The buckets of v1 §9 as `(outcome, heading, description)`, in the order the
#: report prints them: what happened without you first, what needs you next,
#: what did nothing last.
#:
#: The descriptions say what happened, never which path of the design it took.
#: "path A" and "path B" are how v1 §5 tells the two halves of the write apart
#: and they are exactly the sort of shorthand a report may not use: the reader
#: wants to know whether conda-forge will merge this or whether swage has to,
#: and both of those can be said outright.
#:
#: Ordering is data because the ordering *is* the design -- v1 §9 groups by
#: outcome so the actionable items are unmissable, and a sort key hidden in
#: rendering code is a sort key nobody reviews. The headings are spelled out
#: for the same reason rather than derived from the key: `AUTOMERGE` is one
#: word where `READY TO MERGE` is three, and a mechanical transform that got
#: that wrong would be inventing a vocabulary the spec already fixed.
#:
#: `merged` and `closed` are only ever reached by `status`, because they are
#: answers about a pull request rather than about a plan: no amount of reading
#: upstream metadata says whether somebody pressed the button. They are still
#: in this list rather than in one of their own, so that a `status` run and an
#: `update` run stay two `run.json` a reader can compare (v1 §8).
OUTCOMES: tuple[tuple[str, str, str], ...] = (
    ("merged", "MERGED", "landed since the run that made it -- nothing further"),
    (
        "ready-to-merge",
        "READY TO MERGE",
        "nothing to change and CI is green -- merge these yourself",
    ),
    (
        "automerge",
        "AUTOMERGE",
        "pushed + labeled automerge; conda-forge merges it on green CI",
    ),
    # The one bucket where the `automerge` label still does something. It is
    # inert on a pull request whose CI has finished, because conda-forge
    # dispatches its automerge job from CI status events (v1 §2.1) -- and
    # here CI has not finished, so the events still to come would dispatch it
    # for whoever labels the pull request first. swage is not that (path B
    # pushes nothing and labels nothing, v1 §5.2), so the sentence hands the
    # window to the reader. It closes when CI does, which is the moment this
    # bucket becomes READY TO MERGE.
    (
        "awaiting-ci",
        "AWAITING CI",
        "no changes needed; `automerge` is yours to add while CI runs",
    ),
    # A person must look, and the line beside each name says whether that is
    # approving a diff, answering a finding, or merging a pull request whose
    # label did not land (DESIGN.md §11.2). No "rerun `swage status`" for the
    # last: labeling it now would do nothing, because conda-forge dispatches
    # its automerge from CI status events and a label added after CI has
    # finished summons nothing (v1 §2.1).
    ("needs-review", "NEEDS REVIEW", ""),
    ("migrated", "MIGRATED", "v0 -> v1 converted and updated -- review both commits"),
    (
        "needs-migration",
        "NEEDS MIGRATION",
        "v0 meta.yaml -- rerun with `--migrate` to convert in place",
    ),
    ("unchanged", "UNCHANGED", "no open bot PR"),
    # Archived on GitHub, or config says nobody maintains it; the line beside
    # the name says which. Quiet either way: nothing swage does could land.
    ("skipped", "SKIPPED", "nothing swage does could land here -- see each line"),
    (
        "declaration-moved",
        "DECLARATION MOVED",
        "upstream's declaration changed and swage does not read it -- read it yourself",
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
#: defined by this set (v1 §9.1), so it lives beside the outcomes rather than
#: inside whichever property happened to need it first.
_NEEDS_REVIEW = frozenset(
    {
        "needs-review",
        "failed",
        "needs-migration",
        # The declaration this feedstock's config points at is not the one that
        # was last read, and swage cannot say what changed in it. Nobody but a
        # person can close that, which is what exit code 1 means (v1 §9.1).
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


#: The vocabulary swage *writes* is the decision's (DESIGN.md §9.8). Every
#: value has a row in `OUTCOMES`, and `tests/test_run_artifact.py` holds the
#: two lists to each other -- a value in one and not the other is a bucket
#: that never prints or a heading nothing lands in.
#:
#: Deliberately not what swage *reads*: `Record.outcome` is a plain `str`,
#: because a run written by a newer swage names outcomes this one has no row
#: for, and refusing the value would fail the whole file.
Outcome = _Outcome

#: What a run written before DESIGN.md §11.2 called the outcomes it wrote,
#: and what this swage calls them: the table's second column to its first.
#: Applied by `from_v1`, so the recorded runs render into today's buckets;
#: the record's `reason` and `pushed` keep the distinctions the old names
#: drew.
V1_OUTCOMES: dict[str, str] = {
    "merge-ready": "automerge",
    "proposed": "needs-review",
    "degraded": "needs-review",
    "archived": "skipped",
    "unmaintained": "skipped",
    "not-reconciled": "not-read",
}


class _Model(BaseModel):
    # Not `extra="forbid"`: a record written by a *newer* swage should still be
    # readable by this one, which is the half of forward compatibility a
    # version number cannot provide on its own. Config is the opposite case --
    # there an unknown key is a typo a human should hear about at once.
    model_config = ConfigDict(frozen=True)


class UpstreamRecord(_Model):
    """Which release was read, and out of which file.

    The file matters and is not decoration: v1 §3.6.2 reads the two halves of
    the metadata from whichever of `pyproject.toml` and `PKG-INFO` can state
    them, so "where did this dependency come from" has an answer that varies
    per archive and is not recoverable after the fact.
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
    #: classifies a removal (v1 §3.3.7). None where it could not be read.
    previous: str | None = None


class OutputRecord(_Model):
    """One output's build model, which is what every marker was read against.

    Recorded per output because the model is a property of each output, not
    of the fleet (DESIGN.md §1): the same `python_version` marker collapses to
    a bound on one output and becomes an `if:` entry on the next, and a
    reader asking why has to be told which pythons and how many artifacts
    each one was planned for.
    """

    #: What a report calls the output; the package, or a staging label. Empty
    #: for a v1 record, which counted its outputs and named none of them.
    name: str = ""
    #: `one`, `per-platform` or `per-cell` (DESIGN.md §9.1). Empty for a v1
    #: record.
    artifacts: str = ""
    #: The pythons it is built for, as a recipe would say it: `>=3.10` or
    #: `>=3.10,<3.13` for the one package a noarch output builds, and
    #: `3.10, 3.11, 3.12` for the builds `.ci_support` renders of an
    #: architecture-specific one. Empty where no python is rendered, which is
    #: a feedstock with no Python in it.
    pythons: str = ""
    #: The floor a noarch output collapses its markers over, and which file
    #: said so -- a path or a named location, never prose (v1 §9.2). Recorded
    #: so a plan that changed only because conda-forge moved the build floor
    #: is explainable after the fact rather than mysterious.
    floor: str = ""
    floor_source: str = ""


class PlannedLine(_Model):
    """One requirement, and what justifies it.

    Three columns, because v1 §9.2 asks for greppability above all:
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
    #: Set where the resolution was a guess rather than a lookup. The
    #: `unresolved-name` check reads this, and the report prints it, because
    #: an inexact mapping is the failure hardest to notice by eye.
    exact: bool | None = None


class SectionRecord(_Model):
    #: Where the block is in the parsed document. A stable key for this
    #: artifact and for the writer, and never printed -- renderers use
    #: `where`, for the same reason a finding prints `title` and not `kind`.
    path: str
    section: str
    #: The same section in words: `` `pyproj`'s `host` requirements ``.
    #: Carried in the record rather than rebuilt at render time, so a run.json
    #: read back later still says what it meant.
    where: str = ""
    lines: tuple[PlannedLine, ...] = ()


class FindingRecord(_Model):
    """One thing a check found, as `plan.Finding` records it (DESIGN.md §9.7).

    A check is a row and a failure is a value, so a check that held is no
    record at all: "asked and satisfied" is not something anybody acts on.
    """

    #: The identifier -- `unaccounted`, `recheck` -- which is a stable key for
    #: this artifact and for the code, and is never printed. Renderers use
    #: `title`. A `str` rather than the `Kind` literal, because a run written
    #: by a newer swage names kinds this one was built before.
    kind: str
    #: What the check says when it fails, in words. Carried in the record
    #: rather than looked up at render time so that a run.json read back by a
    #: later swage still says what it meant, even if a check has since been
    #: reworded.
    title: str = ""
    #: The line, the name, the extra, the output: what the finding is about.
    subject: str = ""
    #: "`esmf`'s `host` requirements", never a path. Empty where the check
    #: does not know the section.
    where: str = ""
    #: What is wrong, in terms of the recipe and upstream: the half swage
    #: publishes. For a v1 record it is the check's whole joined sentence,
    #: remedy included, because v1 recorded the two halves already joined.
    said: str = ""
    #: What to do, naming config keys: swage's own output only.
    remedy: str = ""


class CheckRecord(_Model):
    """One CI provider swage waited on, and what it reported."""

    name: str
    #: `passed`, `failed` or `pending` -- a word rather than a flag, because
    #: "has not finished" is a third answer and the one a fresh pull request
    #: usually gives.
    state: str


class MergeCheckRecord(_Model):
    """Whether CI says this pull request may be merged (v1 §5.2).

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


class Record(_Model):
    """Everything swage decided about one feedstock, and why (DESIGN.md §11.1)."""

    feedstock: str
    #: `str` rather than `Outcome`, and that is the read side of the same
    #: decision `_Model` makes about unknown fields. A `Literal` here fails
    #: validation for the *whole file* over one feedstock, so a run in which a
    #: newer swage reached one outcome this one lacks would take `explain` down
    #: for the other 486 -- and `SCHEMA_VERSION` is no help, because it
    #: versions the shape and a new outcome does not change the shape.
    #:
    #: Unknown does not mean ignorable: `needs_review` counts it, and the
    #: report prints it in a bucket of its own rather than dropping it.
    outcome: str
    #: The sentence beside the name in the terminal (DESIGN.md §3.2): why this
    #: feedstock landed in the bucket it did. Empty for the outcomes that need
    #: none -- nobody wants 206 lines saying "no open PR".
    reason: str = ""
    #: Advice that is not a verdict (v1 §4). A `reason` says why this feedstock
    #: landed in the bucket it did; a note says something worth knowing about
    #: a feedstock whose bucket is unaffected -- an upstream extra no output
    #: draws on, where the feedstock never opted into exhaustiveness.
    #: Separate rather than appended, because an `automerge` feedstock has no
    #: reason to append to, and giving it one would make an advisory read as
    #: the reason it was held.
    notes: tuple[str, ...] = ()

    # INPUTS (v1 §9.2)
    pull_request: int | None = None
    #: How many open bot pull requests the feedstock had, where swage looked.
    #: Recorded because acting on one of four without saying so is how a
    #: maintainer discovers months later that swage has been ignoring three
    #: (v1 §3.4.1) -- and because four is where conda-forge's bot stops filing
    #: new ones, which makes the number the difference between "three
    #: superseded" and "this feedstock has stopped receiving updates".
    #:
    #: `0` means swage did not count, which is what `status` records: it
    #: follows one pull request by number and never lists the feedstock's.
    pull_requests: int = 0
    head: str = ""
    upstream: UpstreamRecord | None = None
    #: One per output of the recipe, in the recipe's order. Empty where no
    #: recipe was planned, which is how a renderer tells a feedstock swage
    #: read from one it never reached.
    outputs: tuple[OutputRecord, ...] = ()
    #: Most specific first, as the loader resolved them.
    config_layers: tuple[str, ...] = ()

    sections: tuple[SectionRecord, ...] = ()
    #: One per thing found, in the checks' order (DESIGN.md §9.7). Empty for a
    #: plan every check held for, and for a feedstock that never reached them.
    findings: tuple[FindingRecord, ...] = ()
    #: None where swage never asked -- which is every feedstock it has a change
    #: to push, since there CI is conda-forge's business rather than swage's
    #: (v1 §5.1), and every one a finding already held.
    merge_check: MergeCheckRecord | None = None
    #: What swage does, or would do, about the pull request: `nothing`,
    #: `push` or `push-label` (DESIGN.md §9.8). Kept separate from `outcome`
    #: because they answer different questions: this is what swage meant to
    #: do, and the outcome is what became of it. A v1 record says `automerge`
    #: or `needs-review` here, which was the label it meant to apply or the
    #: comment it meant to leave.
    decision: str = ""
    #: The commit swage pushed to the pull request, where it pushed one. Kept
    #: beside `head` rather than replacing it, because they answer different
    #: questions: `head` is the commit the plan was computed against, and this
    #: is the one swage created from it. `swage status` needs both to tell its
    #: own commit from a later bot one.
    pushed: str = ""

    #: Why swage stopped before a plan existed -- a v0 recipe (v1 §3.1), a
    #: conditional `noarch` (§3.3.5), contradictory constraints (§3.3.2). An
    #: empty plan would be the least helpful possible answer to "what
    #: happened", so a stopped feedstock still records its inputs and prints a
    #: STOPPED section instead of a PLAN one.
    stopped: str = ""

    #: The recipe swage would push, and the one the pull request has today.
    #: **Excluded from `run.json`**: two whole recipes per feedstock would
    #: bloat a contract other things read (v1 §9), and a file is the right
    #: shape for something you are going to `diff` anyway. `write_recipes`
    #: puts them in the run directory beside it. Empty for a feedstock that
    #: never reached a plan.
    rendered_recipe: str = Field(default="", exclude=True)
    current_recipe: str = Field(default="", exclude=True)

    #: What this release did to the files a feedstock with no reader declares
    #: in (v1 §3.6.8), as a unified diff. **Excluded from `run.json`** for the
    #: reason the recipes above are: a diff is a thing you read, and a
    #: `configure.ac` is long enough that carrying two of them per feedstock
    #: would bloat a contract other things parse. `write_declarations` puts it
    #: in the run directory, and the summary prints its first lines inline.
    declaration_diff: str = Field(default="", exclude=True)

    @property
    def needs_review(self) -> bool:
        """Whether this feedstock wants a human -- exit code 1 (v1 §9.1).

        Defined per feedstock rather than only per run, because `explain` is
        asked about one of them and answers with the same exit code the sweep
        would have given for it. Two spellings of "wants a human" would drift.

        **An outcome this swage has no row for counts too.** Exit code 0 is a
        claim that nothing needs the reader, and a record swage cannot classify
        is not evidence for it -- so an unrecognized outcome resolves the way
        every other unrecognized thing in swage does, toward telling somebody.
        """
        return self.outcome in _NEEDS_REVIEW or not is_known(self.outcome)


class Run(_Model):
    """One invocation of swage, whole: `run.json`."""

    schema_version: int = Field(default=SCHEMA_VERSION, alias="schema")
    #: The command line as invoked, so a record found later explains itself.
    command: str = ""
    started: str = ""
    feedstocks: tuple[Record, ...] = ()

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    def by_outcome(self, outcome: str) -> tuple[Record, ...]:
        return tuple(record for record in self.feedstocks if record.outcome == outcome)

    def find(self, feedstock: str) -> Record | None:
        return next((r for r in self.feedstocks if r.feedstock == feedstock), None)

    @property
    def needs_review(self) -> bool:
        """Whether anything in this run wants a human -- exit code 1 (9.1)."""
        return any(record.needs_review for record in self.feedstocks)


# --------------------------------------------------------------------------
# Reading a v1 run
# --------------------------------------------------------------------------

#: v1's check names to this schema's kinds, from the table's `v1` column
#: (DESIGN.md §9.7). `G5`, `G6` and `G7` are absent on purpose: the first was
#: a writer invariant that never failed in a recorded run, the second was the
#: rung, which is a parameter of the decision and not a finding, and the
#: third was the fact `unchanged`.
_V1_KINDS = {row.v1: row for row in CHECKS}

_OUTPUT_COUNT = re.compile(r"(\d+) output")


def from_v1(feedstock: Mapping[str, Any]) -> dict[str, Any]:
    """One feedstock of a v1 run, as this schema records it.

    Three fields move. `gates` becomes `findings`: one per failing check
    rather than one per thing found, because v1 joined a check's findings
    into one `detail` and the join is not reversible -- a finding contains
    `; ` as readily as the join does. A gate that passed or did not apply is
    no finding, and a failing `G6` is not one either: it was the rung, and
    what it said is already the record's `reason` wherever it was the whole
    story, which is exactly the case `swage trust` keys on (DESIGN.md §11.3).

    `recipe`, `python_min` and `python_min_source` become `outputs`. v1
    counted the outputs and recorded one floor for the recipe, so the mapped
    outputs are that many, unnamed, each carrying that floor. `detail`
    becomes `reason`, and the outcome is renamed through `V1_OUTCOMES`.
    Everything else is carried as it is, and `decision` keeps v1's two words:
    the record says what v1 meant to do, and a renderer prints them.
    """
    mapped = dict(feedstock)
    outcome = mapped.get("outcome")
    if isinstance(outcome, str):
        mapped["outcome"] = V1_OUTCOMES.get(outcome, outcome)
    mapped["reason"] = mapped.pop("detail", "")
    mapped["findings"] = tuple(_v1_findings(mapped.pop("gates", ())))
    mapped["outputs"] = tuple(
        _v1_outputs(
            mapped.pop("recipe", ""),
            mapped.pop("python_min", ""),
            mapped.pop("python_min_source", ""),
        )
    )
    return mapped


def _v1_findings(gates: Sequence[Mapping[str, Any]]) -> Iterator[dict[str, Any]]:
    for gate in gates:
        row = _V1_KINDS.get(gate.get("name", ""))
        if row is None or gate.get("passed") is not False:
            continue
        # The table's failure sentence rather than the recorded title, which
        # for most of v1's life was the claim a passing check makes -- and
        # "every requirement is accounted for" over a finding says the
        # opposite of what happened.
        yield {"kind": row.kind, "title": row.failure, "said": gate.get("detail", "")}


def _v1_outputs(recipe: str, floor: str, source: str) -> Iterator[dict[str, Any]]:
    counted = _OUTPUT_COUNT.search(recipe or "")
    if counted is None:
        return
    for _ in range(int(counted.group(1))):
        yield {"floor": floor or "", "floor_source": source or ""}


# --------------------------------------------------------------------------
# Making a record
# --------------------------------------------------------------------------


def record(
    feedstock: str,
    outcome: Outcome,
    *,
    plan: Plan | None = None,
    decision: Decision | None = None,
    upstream: RecipeUpstream | None = None,
    previous: str | None = None,
    upstream_source: str = "",
    config_layers: Sequence[str] = (),
    pull_request: int | None = None,
    pull_requests: int = 0,
    head: str = "",
    stopped: str = "",
    reason: str = "",
    declaration_diff: str = "",
    notes: Sequence[str] = (),
    pushed: str = "",
    ci: CiStatus | None = None,
) -> Record:
    """Assemble one feedstock's record out of what the run learned about it.

    ``plan`` carries the recipe, the release, the findings and the rendering
    (DESIGN.md §9.8), and ``upstream`` is for a record with no plan -- a
    feedstock swage does not read, whose release is still named. ``decision``
    is what swage did or would do and the sentence where no finding supplies
    one; ``reason`` overrides the sentence this would otherwise compute, for a
    command that knows something the decision does not -- a push that failed,
    a label that did not land.
    """
    recipe = plan.recipe if plan is not None else None
    findings = plan.findings if plan is not None else ()
    current_recipe = recipe.text if recipe is not None else ""
    rendered_recipe = plan.rendered if plan is not None else ""
    if plan is not None:
        upstream = plan.upstream
    original = _original_lines(recipe)
    return Record(
        feedstock=feedstock,
        outcome=outcome,
        reason=reason
        or _reason(
            outcome,
            findings,
            decision.reason if decision is not None else "",
            stopped,
            ci,
            current_recipe,
            rendered_recipe,
        ),
        # What the run did about this feedstock first, then what was noticed
        # about the feedstock itself: a note saying a push landed without its
        # label is about right now, and one about an undrawn upstream extra
        # would be equally true of a run that never happened.
        notes=tuple(notes) + _notes(plan, upstream),
        pushed=pushed,
        rendered_recipe=rendered_recipe,
        current_recipe=current_recipe,
        declaration_diff=declaration_diff,
        pull_request=pull_request,
        pull_requests=pull_requests,
        head=head,
        upstream=(
            UpstreamRecord(
                name=upstream.name,
                version=upstream.version,
                source=upstream_source,
                declared_in=upstream.declared_in,
                previous=previous,
            )
            if upstream is not None
            else None
        ),
        outputs=tuple(_output(output) for output in plan.outputs) if plan else (),
        config_layers=tuple(config_layers),
        sections=tuple(_sections(plan, original)) if plan is not None else (),
        findings=tuple(
            FindingRecord(
                kind=finding.kind,
                title=finding.check.failure,
                subject=finding.subject,
                where=finding.where,
                said=finding.said,
                remedy=finding.remedy,
            )
            for finding in findings
        ),
        decision=decision.action if decision is not None else "",
        merge_check=(
            MergeCheckRecord(
                verified=ci.verified,
                reason=ci.reason,
                checks=tuple(
                    CheckRecord(name=check.name, state=check.word)
                    for check in ci.required
                ),
            )
            if ci is not None
            else None
        ),
        stopped=stopped,
    )


def _output(output: Output) -> OutputRecord:
    floor = output.python_floor
    return OutputRecord(
        name=output.name,
        artifacts=output.artifacts.value,
        pythons=_pythons(output),
        floor=floor.value if floor is not None else "",
        floor_source=floor.source if floor is not None else "",
    )


def _pythons(output: Output) -> str:
    """Which pythons the output is built for, as a recipe says it.

    The plan samples an open range to a horizon and a feedstock with no
    Python in it over the whole axis (`plan.grid`); neither is a fact about
    the package, so the record says the range and says nothing, respectively.
    """
    if output.noarch:
        return (
            output.built_for.removeprefix("python ")
            if output.python_floor is not None
            else ""
        )
    if 0 in output.pythons:
        # The whole axis, which is what an output with no rendered python is
        # given (DESIGN.md §9.1); no rendered matrix includes 3.0.
        return ""
    return ", ".join(f"3.{minor}" for minor in output.pythons)


def declaration_diff(
    texts: Mapping[str, str],
    previous: Mapping[str, str],
    moved: Sequence[str],
    before: str = "upstream.before",
    after: str = "upstream",
) -> str:
    """One unified diff per declaration file that changed, in config's order.

    A file this release added has no previous text to diff against, so it is
    named rather than rendered as an all-additions hunk -- "upstream started
    declaring in a file it did not have" is a different statement from "these
    lines changed", and running them together would hide it.

    ``before`` and ``after`` label the two sides. The workbench labels them
    with the directories it wrote them into, because they are there to open;
    the summary labels them with the two releases, because in a terminal there
    is nothing else on the screen to say which side is which.
    """
    chunks = []
    for name in moved:
        was = previous.get(name)
        if was is None:
            chunks.append(f"# {name}: new in this release; see {after}/{name}\n")
            continue
        chunks.append(
            "".join(
                difflib.unified_diff(
                    was.splitlines(keepends=True),
                    texts[name].splitlines(keepends=True),
                    fromfile=f"{before}/{name}",
                    tofile=f"{after}/{name}",
                )
            )
        )
    return "".join(chunks)


def _original_lines(recipe: Recipe | None) -> Mapping[str, Mapping[str, str]]:
    """Section path -> requirement key -> the entry the recipe has today.

    Conditional entries included, flattened to one line each: a recipe that
    already states a dependency per python range has a "before" for it, and
    without one every such entry would be reported as an addition even where
    swage is writing back exactly what it read.

    Keyed the way the plan keys a requirement rather than by name alone, so
    that `hdf5` and `hdf5 * nompi_*` each have their own "before". Under one
    key the mpi feedstocks read as though the plain line had been bumped into
    the pinned one, which is a change nobody made.
    """
    if recipe is None:
        return {}
    return {
        path: {name: text for name, text in _entry_lines(block.content.entries)}
        for path, block in recipe.blocks.items()
    }


def _entry_lines(entries: Sequence[Entry]) -> Iterator[tuple[str, str]]:
    for entry in entries:
        if isinstance(entry, Requirement):
            line = parse_line(entry.text)
            yield spec_key(line.name, line.build_string), entry.text
        else:
            named = first_name(entry)
            if named is not None:
                yield named, inline_text(entry)


def _why_dropped(removal: Removal) -> str:
    """What sends the reader to the evidence for one removal.

    Each fate points somewhere different: to the release the dependency went
    out in, to the pythons upstream gates it on, or to the `retire` entry a
    maintainer wrote. Naming the wrong one is worse than naming none.
    """
    if removal.declared_for:
        return f"declared for {removal.declared_for}"
    if removal.fate == "retired":
        return "retired in config"
    if removal.dropped_in:
        return f"absent in {removal.dropped_in}"
    return "absent upstream"


def _sections(
    plan: Plan, original: Mapping[str, Mapping[str, str]]
) -> list[SectionRecord]:
    records = []
    for section in plan.sections:
        was = original.get(section.path, {})
        # A line swage kept but could not account for reaches the plan carrying
        # `recipe-kept` as a placeholder provenance. Printing that would be
        # false in the one place it matters most: v1 §3.3.6 makes
        # `recipe-kept` an allowlist of recognized structure and *never* a
        # fallback, and someone running `explain` to find out why a line is
        # unaccounted for is owed the reason rather than the vocabulary of the
        # rule it broke.
        unexplained = {item.text: _why(item) for item in section.unexplained}
        lines = [_line(entry, was, unexplained) for entry in section.entries]
        # Drops are part of the plan even though they are not in its output --
        # a report that showed only what survives could not explain a removal.
        lines.extend(
            PlannedLine(
                action="drop",
                text=removal.text,
                # The fate rather than a fixed token: swage removes a line for
                # three different reasons and `swage explain | grep` is the
                # interface (v1 §9.2), so a reader counting the ones upstream
                # really dropped must not be handed the ones it still declares
                # for pythons conda-forge does not build.
                origin=removal.fate,
                source=_why_dropped(removal),
            )
            for removal in section.dropped
        )
        records.append(
            SectionRecord(
                path=section.path,
                section=section.section,
                where=section.where,
                lines=tuple(lines),
            )
        )
    return records


def _line(
    requirement: PlannedEntry,
    was: Mapping[str, str],
    unexplained: Mapping[str, str],
) -> PlannedLine:
    written = _entry_text(requirement)
    before = was.get(_requirement_key(requirement))
    if before is None:
        action, text = "add", written
    elif before == written:
        action, text = "keep", written
    elif isinstance(requirement, PlannedRequirement):
        # `google-auth >=2.14.1 -> >=2.15.0` rather than just the new line: a
        # bump nobody can see the old value of is a bump nobody can check.
        action, text = "bump", f"{before} -> {_constraint(written)}"
    else:
        # Both sides in full, because what changed in a conditional entry can
        # be the condition rather than the constraint.
        action, text = "bump", f"{before} -> {written}"
    provenance = requirement.provenance
    reason = unexplained.get(written)
    if reason is not None:
        return PlannedLine(
            action=action, text=text, origin="unexplained", source=reason
        )
    return PlannedLine(
        action=action,
        text=text,
        origin=provenance.origin,
        source=provenance.detail,
        exact=provenance.mapping.exact if provenance.mapping is not None else None,
    )


def _requirement_key(requirement: PlannedEntry) -> str:
    """How this entry is filed among the recipe's existing lines.

    A conditional is filed under the package its branches name, as ordering
    files it: a build string inside one is not a shape any feedstock writes.
    """
    if isinstance(requirement, PlannedRequirement):
        line = parse_line(requirement.text)
        return spec_key(line.name, line.build_string)
    return requirement.name


def _why(item: Unexplained) -> str:
    """The compact source token for a line the accounting could not explain.

    v1 §9.2 wants a file path or a named layer in this column, never prose --
    so the *kind* goes here and the remedy stays with the finding, where there
    is room for it. The remedies differ between kinds and confusing them gives
    confidently wrong advice (v1 §3.3.10), which is exactly why the kind is
    what belongs beside the line.
    """
    if item.kind == "unlisted-extra":
        return f"unlisted extra:{','.join(item.extras)}"
    if item.kind == "unrecognized-template":
        return "unrecognized template"
    if item.kind == "renamed":
        # Upstream declares this very name, so falling through below would put
        # a false statement beside the line rather than a shorter one
        # (v1 §3.2.2).
        return "renamed on conda-forge"
    return "in no upstream version"


def _entry_text(entry: PlannedEntry) -> str:
    """What swage will write, on one line."""
    if isinstance(entry, PlannedRequirement):
        return entry.text
    return " ".join(inline_text(conditional) for conditional in entry.conditionals)


def _constraint(text: str) -> str:
    """Just the version part, since the name is already on the left of the arrow."""
    _, _, rest = text.partition(" ")
    return rest or text


def _reason(
    outcome: Outcome,
    findings: Sequence[Finding],
    decided: str,
    stopped: str,
    ci: CiStatus | None = None,
    current_recipe: str = "",
    rendered_recipe: str = "",
) -> str:
    """The one line the summary prints beside the feedstock's name.

    The first check with findings rather than all of them: v1 §9's report
    gives each feedstock one line, and a reader who wants the rest runs
    `explain`.

    **Its identifier is not in it.** `G1: 'pyiceberg' is in no upstream
    version` reads as though the interesting half were the `G1`, and sends
    anyone who does not already know what that means to a design document to
    find out. The sentence is written to stand on its own, so it is printed
    on its own.

    **A feedstock swage would merge is named too**, which is the one place
    this prints something that is not a problem. What makes the merge check
    auditable is somebody being able to merge the same pull request by hand
    and compare, and a bucket that gave only a count would not tell them
    which ones to open.

    **Where CI answered, CI is the line.** A pull request with nothing to
    change is held by what its builds did, and the trust ladder has no bearing
    on it -- swage cannot merge it at any rung. Printing the ladder there named
    a rung instead of `CI failed: azure, github-actions`, which is the sentence
    somebody acts on.

    **A feedstock swage would push says how much would change** (v1 §9), and
    so does a v0 feedstock that converts, whose two texts are the conversion
    and the conversion reconciled. Everything swage pushes and leaves to a
    person is there for the same reason, which the bucket's own heading
    already gives, so naming the trust rung beside each one printed "not
    approved for automatic merging (trust: propose)" down thirty consecutive
    lines. What differs between them is the size of the change, which is also
    what says which to open first.

    **The ladder never outranks a check that found something.** Where the
    rung is the *only* explanation and swage still would not write --
    `trust: never` -- it is the whole story, and it stays.
    """
    if stopped:
        return stopped.splitlines()[0]
    if ci is not None and ci.reason:
        return compact(ci.reason)
    if outcome in ("automerge", "needs-migration"):
        # On a v0 feedstock the two texts are the conversion and the
        # conversion reconciled, so this is the size of the *second* of the
        # two commits a migration pushes (v1 §7.1) -- which is the half that
        # needs judgment and the half a whole-file diff hides. It is empty
        # where nothing was rendered, which is every other way a feedstock
        # reaches `needs-migration`.
        return _would_change(current_recipe, rendered_recipe)
    grouped = by_kind(findings)
    if grouped:
        first = next(iter(grouped.values()))
        return compact(summarize(first), tuple(finding.said for finding in first))
    if decided:
        # The rung, and only where it is the whole explanation of a run that
        # wrote nothing: `trust: never`. Anywhere else a rung answers a
        # question nobody asked.
        return decided
    if ci is None:
        # Nothing found and nothing said: a change swage pushes, or would, and
        # leaves the label to a person. What differs between those is the size
        # of the change, which is also what says which one to open first.
        return _would_change(current_recipe, rendered_recipe)
    return f"CI passed: {', '.join(check.name for check in ci.required)}"


def _would_change(current: str, rendered: str) -> str:
    """How much of the recipe swage would touch, counted in lines."""
    if not rendered or rendered == current:
        return ""
    changed = [
        line
        for line in difflib.unified_diff(
            current.splitlines(), rendered.splitlines(), n=0
        )
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
    ]
    added = sum(1 for line in changed if line.startswith("+"))
    return f"+{added} -{len(changed) - added} in the recipe"


def _notes(plan: Plan | None, upstream: RecipeUpstream | None) -> tuple[str, ...]:
    """Advice about this feedstock that is not a reason for its verdict.

    Three things today. The first is where a dependency list came from when
    that was not the archive the recipe builds (v1 §3.6.2). The second is
    whatever the reader had to say about the release itself -- the esmf
    reader reports the ParallelIO version this ESMF vendors, which moves
    between releases and is not a bound on anything (v1 §3.6.6). The third is
    v1 §4's promise for a feedstock that never opted into exhaustiveness: an
    upstream extra no output draws on and no config entry accounts for is
    *reported and not gated*. Where a `skip` list exists, the
    `unclassified-extra` check already holds the feedstock and the note would
    restate it.

    **Said of every unaccounted extra, not only a newly appeared one.** The
    spec's example reads "adds extra", which would need the previous version's
    metadata to justify -- and a note that appears for exactly one version bump
    and then goes quiet is a signal that expires while the situation does not.
    An extra nobody has decided about is worth mentioning on every run until
    somebody decides, which is the whole bargain of v1 §4: say nothing and
    swage tells you rather than blocking you.
    """
    notes: list[str] = []
    if upstream is not None and upstream.dependency_source:
        # The recipe pins the sdist and swage checks that hash; the wheel is a
        # second distribution of the same release, so which file stated the
        # dependencies is worth a line rather than being invisible
        # (v1 §3.6.2).
        notes.append(
            f"dependencies read from {upstream.dependency_source}; this "
            "release's sdist declares none"
        )
    if upstream is not None:
        notes.extend(upstream.notes)
    if plan is not None and plan.unaccounted_extras:
        version = f" {upstream.version}" if upstream and upstream.version else ""
        notes.extend(
            f"upstream{version} declares extra {extra!r}, which no output draws on"
            for extra in plan.unaccounted_extras
        )
    if plan is not None:
        # An entry point swage retargets or adds rides the trust ladder like
        # a dependency change, so the diff is where it shows; the note is
        # what says why the diff has it (v1 §3.3.15). What swage looked at
        # and left alone comes after, for the same reason an unaccounted
        # extra is said on every run.
        notes.extend(
            sentence for change in plan.entry_points for sentence in change.said
        )
        notes.extend(plan.entry_point_notes)
    return tuple(notes)


def compact(detail: str, findings: Sequence[str] = (), ceiling: int = 320) -> str:
    """Cut a check's findings down to what a summary line should carry.

    **Shortening is for stopping one feedstock burying the run, not for saving
    space.** `apache-airflow-core-split` fails the accounting check on eighteen
    separate lines, whose full detail wraps to forty lines of terminal and
    hides every other feedstock -- the opposite of what grouping by outcome is
    for (v1 §9). One feedstock's one finding is not that, so a finding is
    printed whole however long it runs: the longest in the fleet is 258
    characters, which is three wrapped lines, and three lines a maintainer can
    act on beat one line plus a command they have to go and run.

    So the only thing ever dropped is *other* findings. The first is printed
    entire and the rest are counted, and `explain` is where those live.

    **The count comes from the check, never from splitting the joined detail
    back up.** A finding contains `; ` as readily as the join does -- the
    accounting message read `...; drop it, or ...` when this was found -- so
    `mpas_tools`, held by one unaccounted requirement, reported "(+1 more)" and
    sent its maintainer looking for a second problem that did not exist. The
    punctuation has changed since and the rule does not depend on it: what a
    check found is a list, and a list has a length.

    ``ceiling`` is a backstop for a finding no fleet member has yet produced,
    where a config `reason` runs to paragraphs. Nothing today reaches it.

    **Where the rest is gets said by the renderer**, on a line of its own:
    `was_shortened` is how it knows to say it, and a command wrapped across two
    terminal lines is a command nobody can paste.
    """
    if len(findings) > 1:
        counted = len(findings) - 1
        rest = f" (+{counted} more finding{'' if counted == 1 else 's'})"
        return f"{_cut(findings[0], ceiling - len(rest))}{rest}"
    return _cut(detail, ceiling)


#: What a shortened line ends in: the count of the findings not printed, or a
#: cut finding. Both are written just above and nothing else produces either,
#: which is what lets a renderer ask whether a line is the whole story.
_COUNTED = re.compile(r"\(\+\d+ more findings?\)$")


def was_shortened(detail: str) -> bool:
    """Whether this summary line is showing less than the check found."""
    return detail.endswith("…") or _COUNTED.search(detail) is not None


def _cut(text: str, room: int) -> str:
    """``text``, shortened to ``room`` characters if it does not fit.

    Cut where the sentence breaks if it breaks in the second half of what
    there is room for, and mid-word otherwise. A finding states its claim and
    then its remedy, so cutting by width alone ends the line four characters
    into "add the extra so swage maintains it", which says nothing and costs
    the reader the space that would have carried the claim.
    """
    if len(text) <= room:
        return text
    head = text[: room - 1]
    breaks = [found for mark in ("; ", ". ") if (found := head.rfind(mark)) != -1]
    if breaks and max(breaks) > room // 2:
        head = head[: max(breaks)]
    return head.rstrip() + "…"
