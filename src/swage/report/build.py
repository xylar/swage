"""Turn a plan and its verdict into the record (DESIGN.md 9).

The one thing this layer computes rather than copies is the **action** on each
line -- `keep`, `add`, `bump` or `drop` -- because a plan says what a section
should be and the report has to say what changed. That needs the recipe as
well as the plan, which is why the recipe is passed in rather than the planner
being asked to remember it: the planner's job ends at the desired state, and
`PlannedSection` staying free of "what it used to say" keeps the corpus test
comparing exactly one thing.

**The outcome is a parameter, not a computation.** Which bucket a feedstock
lands in depends on what the command did with it -- pushed, labeled, merged,
or nothing at all because `scan` never writes -- and that is the command's
knowledge rather than the report's. Passing it in keeps write-path policy out
of a layer that has no business holding any.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Iterator, Mapping, Sequence

from swage.forge import CiStatus
from swage.plan import (
    GateResult,
    PlannedEntry,
    PlannedRequirement,
    RecipePlan,
    Unexplained,
    Verdict,
    first_name,
    parse_line,
    spec_key,
)
from swage.recipe import Entry, Recipe, Requirement, inline_text
from swage.upstream import UpstreamMetadata

from .model import (
    CheckRecord,
    FeedstockRecord,
    GateRecord,
    MergeCheckRecord,
    Outcome,
    PlannedLine,
    SectionRecord,
    UpstreamRecord,
)

__all__ = ["build_record", "compact", "summarize_recipe", "was_shortened"]


def build_record(
    feedstock: str,
    outcome: Outcome,
    plan: RecipePlan | None = None,
    verdict: Verdict | None = None,
    recipe: Recipe | None = None,
    upstream: UpstreamMetadata | None = None,
    previous: str | None = None,
    upstream_source: str = "",
    config_layers: Sequence[str] = (),
    pull_request: int | None = None,
    pull_requests: int = 0,
    head: str = "",
    stopped: str = "",
    detail: str = "",
    rendered_recipe: str = "",
    current_recipe: str = "",
    notes: Sequence[str] = (),
    pushed: str = "",
    ci: CiStatus | None = None,
) -> FeedstockRecord:
    """Assemble one feedstock's record out of what the run learned about it."""
    original = _original_lines(recipe)
    return FeedstockRecord(
        feedstock=feedstock,
        outcome=outcome,
        detail=detail
        or _detail(outcome, verdict, stopped, ci, current_recipe, rendered_recipe),
        # What the run did about this feedstock first, then what was noticed
        # about the feedstock itself: a note saying a push landed without its
        # label is about right now, and one about an undrawn upstream extra
        # would be equally true of a run that never happened.
        notes=tuple(notes) + _notes(plan, upstream),
        pushed=pushed,
        rendered_recipe=rendered_recipe,
        current_recipe=current_recipe,
        recipe=summarize_recipe(recipe) if recipe is not None else "",
        pull_request=pull_request,
        pull_requests=pull_requests,
        head=head,
        upstream=(
            UpstreamRecord(
                name=upstream.name,
                version=upstream.version,
                source=upstream_source,
                previous=previous,
            )
            if upstream is not None
            else None
        ),
        python_min=plan.python_min.value if plan and plan.python_min else "",
        python_min_source=plan.python_min.source if plan and plan.python_min else "",
        config_layers=tuple(config_layers),
        sections=tuple(_sections(plan, original)) if plan is not None else (),
        gates=(
            tuple(
                GateRecord(
                    name=gate.name,
                    title=gate.title,
                    passed=gate.passed,
                    detail=gate.detail,
                )
                for gate in verdict.gates
            )
            if verdict is not None
            else ()
        ),
        decision=verdict.decision if verdict is not None else "",
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


def summarize_recipe(recipe: Recipe) -> str:
    """The one line INPUTS prints about the recipe itself."""
    outputs = len(recipe.outputs)
    blocks = len(recipe.blocks)
    return (
        f"v1, {outputs} output{'' if outputs == 1 else 's'}, "
        f"{blocks} requirements block{'' if blocks == 1 else 's'}"
    )


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


def _sections(
    plan: RecipePlan, original: Mapping[str, Mapping[str, str]]
) -> list[SectionRecord]:
    records = []
    for section in plan.sections:
        was = original.get(section.path, {})
        # A line swage kept but could not account for reaches the plan carrying
        # `recipe-kept` as a placeholder provenance. Printing that would be
        # false in the one place it matters most: DESIGN.md 3.3.6 makes
        # `recipe-kept` an allowlist of recognized structure and *never* a
        # fallback, and someone running `explain` to find out why G1 failed is
        # owed the reason rather than the vocabulary of the rule it broke.
        unexplained = {item.text: _why(item) for item in section.unexplained}
        lines = [_line(entry, was, unexplained) for entry in section.entries]
        # Drops are part of the plan even though they are not in its output --
        # a report that showed only what survives could not explain a removal.
        lines.extend(
            PlannedLine(
                action="drop",
                text=removal.text,
                origin="upstream-dropped",
                source=(
                    f"absent in {removal.dropped_in}"
                    if removal.dropped_in
                    else "absent upstream"
                ),
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
    """The compact source token for a line G1 could not account for.

    DESIGN.md 9.2 wants a file path or a named layer in this column, never
    prose -- so the *kind* goes here and the remedy stays in GATES, where
    there is room for it. The remedies differ between kinds and confusing them
    gives confidently wrong advice (DESIGN.md 3.3.10), which is exactly why
    the kind is what belongs beside the line.
    """
    if item.kind == "unlisted-extra":
        return f"unlisted extra:{','.join(item.extras)}"
    if item.kind == "unrecognized-template":
        return "unrecognized template"
    if item.kind == "renamed":
        # Upstream declares this very name, so falling through below would put
        # a false statement beside the line rather than a shorter one
        # (DESIGN.md 3.2.2).
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


def _detail(
    outcome: Outcome,
    verdict: Verdict | None,
    stopped: str,
    ci: CiStatus | None = None,
    current_recipe: str = "",
    rendered_recipe: str = "",
) -> str:
    """The one line the summary prints beside the feedstock's name.

    The first failing check rather than all of them: DESIGN.md 9's report gives
    each feedstock one line, and a reader who wants the rest runs `explain`.

    **Its identifier is not in it.** `G1: 'pyiceberg' is in no upstream
    version` reads as though the interesting half were the `G1`, and sends
    anyone who does not already know what that means to a design document to
    find out. The detail is written to stand on its own, so it is printed on
    its own; the title stands in where a check has no detail to give.

    **A feedstock swage would merge is named too**, which is the one place
    this prints something that is not a problem. It is the report-only step of
    DESIGN.md 5.2 doing its whole job: what makes the merge check auditable is
    somebody being able to merge the same pull request by hand and compare, and
    a bucket that gave only a count would not tell them which ones to open.

    **Where CI answered, CI is the line.** A pull request with nothing to
    change is held by what its builds did, and the trust ladder has no bearing
    on it -- swage cannot merge it at any rung. Printing the ladder there named
    a rung instead of `CI failed: azure, github-actions`, which is the sentence
    somebody acts on.

    **A feedstock swage would push says how much would change** (DESIGN.md 9).
    Everything in MERGE-READY and PROPOSED is there for the same reason, which
    the bucket's own heading already gives, so naming the trust rung beside
    each one printed "not approved for automatic merging (trust: propose)"
    down thirty consecutive lines. What differs between them is the size of
    the change, which is also what says which to open first.

    **The ladder never outranks a check that found something.** Gates are
    evaluated in order and the ladder sits in the middle of them, so a
    feedstock held by a later check had the rung printed instead of the
    reason: `google-cloud-redis` is held because swage would drop a
    requirement it cannot account for, and reported "not approved for
    automatic merging (trust: propose)". Where the rung is the *only* failure
    and swage still would not write -- `trust: never` -- it is the whole
    story, and it stays.
    """
    if stopped:
        return stopped.splitlines()[0]
    if ci is not None and ci.reason:
        return compact(ci.reason)
    if outcome in ("merge-ready", "proposed"):
        return _would_change(current_recipe, rendered_recipe)
    failures = _reasons(verdict, outcome)
    if failures:
        first = failures[0]
        return compact(first.detail, first.each) if first.detail else first.title
    if ci is None:
        return ""
    return f"CI passed: {', '.join(check.name for check in ci.required)}"


def _reasons(verdict: Verdict | None, outcome: Outcome) -> tuple[GateResult, ...]:
    """The failing checks worth naming here, most important first.

    The trust ladder is not one of them, except where it is the only thing
    there is to say. `trust: never` fails no other check and is the whole
    explanation of a run that wrote nothing, so it is what NEEDS REVIEW prints
    for such a feedstock; anywhere else a rung answers a question nobody asked.
    """
    if verdict is None:
        return ()
    blocking = tuple(gate for gate in verdict.failures if gate.name != "G6")
    if blocking or outcome != "needs-review":
        return blocking
    return verdict.failures


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


def _notes(
    plan: RecipePlan | None, upstream: UpstreamMetadata | None
) -> tuple[str, ...]:
    """Advice about this feedstock that is not a reason for its verdict.

    Three things today. The first is where a dependency list came from when
    that was not the archive the recipe builds (DESIGN.md 3.6.2). The second
    is whatever the reader had to say about the release itself -- the esmf
    reader reports the ParallelIO version this ESMF vendors, which moves
    between releases and is not a bound on anything (DESIGN.md 3.6.5). The
    third is
    DESIGN.md 4's promise for a feedstock that never opted into
    exhaustiveness: an upstream extra no output draws on and no config entry
    accounts for is *reported and not gated*. Where a `skip` list exists, G3
    already stops the feedstock and the note would restate the gate.

    **Said of every unaccounted extra, not only a newly appeared one.** The
    spec's example reads "adds extra", which would need the previous version's
    metadata to justify -- and a note that appears for exactly one version bump
    and then goes quiet is a signal that expires while the situation does not.
    An extra nobody has decided about is worth mentioning on every run until
    somebody decides, which is the whole bargain of 4: say nothing and swage
    tells you rather than blocking you.
    """
    notes: list[str] = []
    if upstream is not None and upstream.dependency_source:
        # The recipe pins the sdist and swage checks that hash; the wheel is a
        # second distribution of the same release, so which file stated the
        # dependencies is worth a line rather than being invisible
        # (DESIGN.md 3.6.2).
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
    return tuple(notes)


def compact(detail: str, findings: Sequence[str] = (), ceiling: int = 320) -> str:
    """Cut a check's detail down to what a summary line should carry.

    **Shortening is for stopping one feedstock burying the run, not for saving
    space.** `apache-airflow-core-split` fails the accounting check on eighteen
    separate lines, whose full detail wraps to forty lines of terminal and
    hides every other feedstock -- the opposite of what grouping by outcome is
    for (DESIGN.md 9). One feedstock's one finding is not that, so a finding is
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
