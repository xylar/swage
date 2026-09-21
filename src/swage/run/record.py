"""One record per feedstock, and the one function that makes it (DESIGN.md
§11.1).

`run.json` is the record; the terminal summary and `swage explain` are
renderings of it (v1 §9.2). The models are pydantic because a past run is read
back by a later swage: reading is permissive and writing is closed. A run
written before this schema is read through `from_v1`. The outcome is a
parameter: which bucket a feedstock lands in is the command's knowledge.
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
    first_name,
    parse_line,
    spec_key,
)
from swage.plan import Outcome as _Outcome
from swage.plan.prose import fenced
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
    "declaration_diff",
    "from_v1",
    "is_known",
    "record",
    "was_shortened",
]

#: Bump when a field changes meaning or disappears; adding an optional field
#: does not need one. 5 is the first v2 schema (DESIGN.md §11.1).
SCHEMA_VERSION = 5

#: The schemas v1 wrote, every one read through `from_v1`.
V1_SCHEMAS = frozenset({1, 2, 3, 4})

#: The buckets of v1 §9 as `(outcome, heading, description)`, in the order the
#: report prints them. The descriptions say what happened, never which path of
#: the design it took (DESIGN.md §3.1). `merged` and `closed` are reached only
#: by `status`, and stay in this list so its runs compare with `update`'s (v1
#: §8).
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
    # The one bucket where the `automerge` label still does something
    # (docs/conda-forge.md), so the sentence hands the window to the reader.
    (
        "awaiting-ci",
        "AWAITING CI",
        "no changes needed; `automerge` is yours to add while CI runs",
    ),
    # The line beside each name says which kind of look (DESIGN.md §11.2).
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

#: The outcomes that mean a human still has something to do: exit code 1 (v1
#: §9.1).
_NEEDS_REVIEW = frozenset(
    {
        "needs-review",
        "failed",
        "needs-migration",
        # Nobody but a person can close a moved declaration (v1 §9.1).
        "declaration-moved",
    }
)


def is_known(outcome: str) -> bool:
    """Whether this swage has a row in `OUTCOMES` for the outcome.

    False for a run written by a newer swage, which is a supported state
    (DESIGN.md §11.1).
    """
    return any(outcome == known for known, _, _ in OUTCOMES)


#: The vocabulary swage *writes* is the decision's (DESIGN.md §9.8);
#: `tests/test_run_artifact.py` holds it to `OUTCOMES`. What swage *reads* is a
#: plain `str` (§11.1).
Outcome = _Outcome

#: v1's outcome names to this schema's, applied by `from_v1` (DESIGN.md §11.2).
V1_OUTCOMES: dict[str, str] = {
    "merge-ready": "automerge",
    "proposed": "needs-review",
    "degraded": "needs-review",
    "archived": "skipped",
    "unmaintained": "skipped",
    "not-reconciled": "not-read",
}


class _Model(BaseModel):
    # Not `extra="forbid"`: a record written by a newer swage is still readable
    # (DESIGN.md §11.1). Config is the opposite case.
    model_config = ConfigDict(frozen=True)


class UpstreamRecord(_Model):
    """Which release was read, and out of which file (v1 §3.6.2)."""

    name: str
    version: str | None = None
    #: Where the release came from -- the archive URL, or the repo and tag for
    #: a feedstock whose metadata is read out of a git tag.
    source: str = ""
    #: Which file inside it stated the dependencies, relative to the archive's
    #: top-level directory; several joined by ` + `. Empty for a record written
    #: before this was carried, or one that stopped first.
    declared_in: str = ""
    #: The version the recipe reflected before this update, which is what
    #: classifies a removal (v1 §3.3.7). None where it could not be read.
    previous: str | None = None


class OutputRecord(_Model):
    """One output's build model, which is what every marker was read against
    (DESIGN.md §1, §9.1).
    """

    #: What a report calls the output; the package, or a staging label. Empty
    #: for a v1 record, which counted its outputs and named none of them.
    name: str = ""
    #: `one`, `per-platform` or `per-cell` (DESIGN.md §9.1). Empty for a v1
    #: record.
    artifacts: str = ""
    #: The pythons it is built for, as a recipe would say it: a range for
    #: noarch, a list for an arch output. Empty where no python is rendered.
    pythons: str = ""
    #: The floor a noarch output collapses its markers over, and which file said
    #: so: a path or a named location, never prose (v1 §9.2).
    floor: str = ""
    floor_source: str = ""


class PlannedLine(_Model):
    """One requirement, and what justifies it, in three greppable columns
    (v1 §9.2).
    """

    #: `keep`, `add`, `bump` or `drop` -- the first token of the rendered line,
    #: so a plan reads as a diff at a glance.
    action: str
    text: str
    origin: str
    #: A file path or a named layer, never prose, so the next step is always
    #: opening a specific file.
    source: str
    #: Set where the resolution was a guess rather than a lookup; the
    #: `unresolved-name` check reads it.
    exact: bool | None = None


class SectionRecord(_Model):
    #: Where the block is in the parsed document: a key for the writer, never
    #: printed. Renderers use `where`.
    path: str
    section: str
    #: The same section in words, carried in the record rather than rebuilt.
    where: str = ""
    lines: tuple[PlannedLine, ...] = ()


class FindingRecord(_Model):
    """One thing a check found, as `plan.Finding` records it (DESIGN.md §9.7)."""

    #: The identifier, never printed; renderers use `title`. A `str` rather than
    #: `Kind`, because a newer swage names kinds this one lacks.
    kind: str
    #: What the check says when it fails, carried so a run read back later still
    #: says what it meant.
    title: str = ""
    #: The line, the name, the extra, the output: what the finding is about.
    subject: str = ""
    #: "`esmf`'s `host` requirements", never a path. Empty where the check
    #: does not know the section.
    where: str = ""
    #: What is wrong, in terms of the recipe and upstream: the half swage
    #: publishes. A v1 record has the two halves already joined.
    said: str = ""
    #: What to do, naming config keys: swage's own output only.
    remedy: str = ""


class CheckRecord(_Model):
    """One CI provider swage waited on, and what it reported."""

    name: str
    #: `passed`, `failed` or `pending`.
    state: str


class MergeCheckRecord(_Model):
    """Whether CI says this pull request may be merged (v1 §5.2), recorded whole
    as the evidence.
    """

    verified: bool
    #: Empty where swage would merge; otherwise a sentence that stands alone.
    reason: str = ""
    checks: tuple[CheckRecord, ...] = ()


class Record(_Model):
    """Everything swage decided about one feedstock, and why (DESIGN.md §11.1)."""

    feedstock: str
    #: `str` rather than `Outcome`: a `Literal` fails validation for the whole
    #: file over one feedstock a newer swage classified (DESIGN.md §11.1).
    #: `needs_review` counts an unknown outcome and the report buckets it.
    outcome: str
    #: The sentence beside the name in the terminal (DESIGN.md §3.2). Empty for
    #: the outcomes that need none.
    reason: str = ""
    #: Advice that is not a verdict (v1 §4), kept apart from `reason` so an
    #: advisory never reads as the reason a feedstock was held.
    notes: tuple[str, ...] = ()

    # INPUTS (v1 §9.2)
    pull_request: int | None = None
    #: How many open bot pull requests the feedstock had, where swage looked (v1
    #: §3.4.1). `0` means swage did not count, which is what `status` records.
    pull_requests: int = 0
    head: str = ""
    upstream: UpstreamRecord | None = None
    #: One per output of the recipe, in the recipe's order. Empty where no
    #: recipe was planned.
    outputs: tuple[OutputRecord, ...] = ()
    #: Most specific first, as the loader resolved them.
    config_layers: tuple[str, ...] = ()

    sections: tuple[SectionRecord, ...] = ()
    #: One per thing found, in the checks' order (DESIGN.md §9.7). Empty for a
    #: plan every check held for, and for a feedstock that never reached them.
    findings: tuple[FindingRecord, ...] = ()
    #: None where swage never asked: every feedstock with a change to push (v1
    #: §5.1), and every one a finding held.
    merge_check: MergeCheckRecord | None = None
    #: What swage does, or would do, about the pull request (DESIGN.md §9.8),
    #: apart from `outcome`, which is what became of it. A v1 record says
    #: `automerge` or `needs-review` here.
    decision: str = ""
    #: The commit swage pushed, beside `head`, which the plan was computed
    #: against; `status` needs both.
    pushed: str = ""

    #: Why swage stopped before a plan existed. A stopped feedstock still
    #: records its inputs and prints a STOPPED section.
    stopped: str = ""

    #: The recipe swage would push, and the one the pull request has today.
    #: Excluded from `run.json` (v1 §9); `write_recipes` puts them beside it.
    rendered_recipe: str = Field(default="", exclude=True)
    current_recipe: str = Field(default="", exclude=True)

    #: What this release did to the files a feedstock with no reader declares in
    #: (v1 §3.6.8), as a unified diff. Excluded from `run.json`;
    #: `write_declarations` puts it beside it.
    declaration_diff: str = Field(default="", exclude=True)

    @property
    def needs_review(self) -> bool:
        """Whether this feedstock wants a human: exit code 1 (v1 §9.1), including
        an outcome this swage has no row for (DESIGN.md §11.2).
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

#: v1's check names to this schema's kinds (DESIGN.md §9.7). `G5`, `G6` and `G7`
#: are absent on purpose: a writer invariant, the rung, and the fact
#: `unchanged`.
_V1_KINDS = {row.v1: row for row in CHECKS}

_OUTPUT_COUNT = re.compile(r"(\d+) output")


def from_v1(feedstock: Mapping[str, Any]) -> dict[str, Any]:
    """One feedstock of a v1 run, as this schema records it (DESIGN.md §11.1).

    `gates` becomes `findings`, one per failing check; a failing `G6` is not
    one. `recipe`, `python_min` and `python_min_source` become that many
    unnamed `outputs` carrying that floor. `detail` becomes `reason`, and
    the outcome is renamed through `V1_OUTCOMES`. `decision` keeps v1's two
    words.
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
        # The table's failure sentence rather than the recorded title, which for
        # most of v1's life was the claim a passing check makes.
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
    (DESIGN.md §9.8); ``upstream`` is for a record with no plan. ``decision``
    is what swage did or would do; ``reason`` overrides the sentence this
    would compute, for a command that knows something the decision does not.
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
            feedstock,
            outcome,
            findings,
            decision.reason if decision is not None else "",
            stopped,
            ci,
            current_recipe,
            rendered_recipe,
        ),
        # What the run did about this feedstock first, then what was noticed
        # about the feedstock itself.
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
    """Which pythons the output is built for, as a recipe says it. An open
    range says the range; a feedstock with no Python says nothing.
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

    A file this release added is named rather than rendered as an
    all-additions hunk. ``before`` and ``after`` label the two sides.
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

    Conditional entries included, flattened to one line each. Keyed the way
    the plan keys a requirement (DESIGN.md §9.4).
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
    """What sends the reader to the evidence for one removal: the release, the
    pythons, or the `retire` entry.
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
        # `recipe-kept` on a line swage could not explain is a placeholder (v1
        # §3.3.6); `explain` owes the reader the reason.
        unexplained = {item.text: _why(item) for item in section.unexplained}
        lines = [_line(entry, was, unexplained) for entry in section.entries]
        # Drops are part of the plan even though they are not in its output --
        # a report that showed only what survives could not explain a removal.
        lines.extend(
            PlannedLine(
                action="drop",
                text=removal.text,
                # The fate rather than a fixed token, because `swage explain |
                # grep` is the interface (v1 §9.2).
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
    """How this entry is filed among the recipe's existing lines: a conditional
    under the package its branches name.
    """
    if isinstance(requirement, PlannedRequirement):
        line = parse_line(requirement.text)
        return spec_key(line.name, line.build_string)
    return requirement.name


def _why(item: Unexplained) -> str:
    """The compact source token for a line the accounting could not explain:
    the kind, never prose (v1 §9.2). The remedy stays with the finding.
    """
    if item.kind == "unlisted-extra":
        return f"unlisted extra:{','.join(item.extras)}"
    if item.kind == "unrecognized-template":
        return "unrecognized template"
    if item.kind == "renamed":
        # Upstream declares this very name (v1 §3.2.2).
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
    feedstock: str,
    outcome: Outcome,
    findings: Sequence[Finding],
    decided: str,
    stopped: str,
    ci: CiStatus | None = None,
    current_recipe: str = "",
    rendered_recipe: str = "",
) -> str:
    """The one line the summary prints beside the feedstock's name (DESIGN.md
    §3.2): a stop's first line, what CI said, how much would change, the
    first finding as its check's sentence and subject, or the rung where it
    is the whole explanation. A stop that opens by naming the feedstock
    loses the name here.
    """
    if stopped:
        return stopped.splitlines()[0].removeprefix(f"{feedstock}: ")
    if ci is not None and ci.reason:
        return ci.reason
    if outcome in ("automerge", "needs-migration"):
        # On a v0 feedstock the two texts are the conversion and the conversion
        # reconciled, so this is the size of the second commit (v1 §7.1).
        return _would_change(current_recipe, rendered_recipe)
    if findings:
        first = findings[0]
        line = f"{first.check.failure}: {fenced(first.subject)}"
        rest = len(findings) - 1
        if rest:
            line = f"{line} (+{rest} more finding{'' if rest == 1 else 's'})"
        return line
    if decided:
        # The rung, only where it is the whole explanation of a run that wrote
        # nothing.
        return decided
    if ci is None:
        # Nothing found and nothing said: the size of the change is what says
        # which one to open first.
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

    Where a dependency list came from when that was not the archive
    (v1 §3.6.2); what the reader had to say about the release (v1 §3.6.6);
    and, for a feedstock that never opted into exhaustiveness, every upstream
    extra no output draws on, said on every run (v1 §4).
    """
    notes: list[str] = []
    if upstream is not None and upstream.dependency_source:
        # The wheel is a second distribution of the same release, so which file
        # stated the dependencies is worth a line (v1 §3.6.2).
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
        # A retarget or an addition shows in the diff; the note says why
        # (DESIGN.md §9.6). What was left alone comes after.
        notes.extend(
            sentence for change in plan.entry_points for sentence in change.said
        )
        notes.extend(plan.entry_point_notes)
    return tuple(notes)


#: What a summary line ends in where the check found more than it names. Written
#: only by `_reason`.
_COUNTED = re.compile(r"\(\+\d+ more findings?\)$")


def was_shortened(detail: str) -> bool:
    """Whether this summary line names fewer findings than there are."""
    return _COUNTED.search(detail) is not None
