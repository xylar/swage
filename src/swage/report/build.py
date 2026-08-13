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

from collections.abc import Mapping, Sequence

from swage.plan import (
    PlannedRequirement,
    RecipePlan,
    Unexplained,
    Verdict,
    parse_line,
)
from swage.recipe import Recipe
from swage.upstream import UpstreamMetadata

from .model import (
    FeedstockRecord,
    GateRecord,
    Outcome,
    PlannedLine,
    SectionRecord,
    UpstreamRecord,
)

__all__ = ["build_record", "summarize_recipe"]


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
) -> FeedstockRecord:
    """Assemble one feedstock's record out of what the run learned about it."""
    original = _original_lines(recipe)
    return FeedstockRecord(
        feedstock=feedstock,
        outcome=outcome,
        detail=detail or _detail(verdict, stopped),
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
                GateRecord(name=gate.name, passed=gate.passed, detail=gate.detail)
                for gate in verdict.gates
            )
            if verdict is not None
            else ()
        ),
        decision=verdict.decision if verdict is not None else "",
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
    """Section path -> package name -> the line the recipe has today."""
    if recipe is None:
        return {}
    return {
        path: {parse_line(text).name: text for text in block.content.texts()}
        for path, block in recipe.blocks.items()
    }


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
        lines = [
            _line(requirement, was, unexplained) for requirement in section.requirements
        ]
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
                path=section.path, section=section.section, lines=tuple(lines)
            )
        )
    return records


def _line(
    requirement: PlannedRequirement,
    was: Mapping[str, str],
    unexplained: Mapping[str, str],
) -> PlannedLine:
    before = was.get(requirement.name)
    if before is None:
        action, text = "add", requirement.text
    elif before == requirement.text:
        action, text = "keep", requirement.text
    else:
        # `google-auth >=2.14.1 -> >=2.15.0` rather than just the new line: a
        # bump nobody can see the old value of is a bump nobody can check.
        action, text = "bump", f"{before} -> {_constraint(requirement.text)}"
    provenance = requirement.provenance
    reason = unexplained.get(requirement.text)
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


def _constraint(text: str) -> str:
    """Just the version part, since the name is already on the left of the arrow."""
    _, _, rest = text.partition(" ")
    return rest or text


def _detail(verdict: Verdict | None, stopped: str) -> str:
    """The one line the summary prints beside the feedstock's name.

    The first failing gate rather than all of them: DESIGN.md 9's report gives
    each feedstock one line, and a reader who wants the rest runs `explain`.
    """
    if stopped:
        return stopped.splitlines()[0]
    if verdict is None or not verdict.failures:
        return ""
    first = verdict.failures[0]
    return f"{first.name}: {_compact(first.detail)}" if first.detail else first.name


def _notes(
    plan: RecipePlan | None, upstream: UpstreamMetadata | None
) -> tuple[str, ...]:
    """Advice about this feedstock that is not a reason for its verdict.

    Two things today. The first is where a dependency list came from when that
    was not the archive the recipe builds (DESIGN.md 3.6.2). The second is
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
    if plan is not None and plan.unaccounted_extras:
        version = f" {upstream.version}" if upstream and upstream.version else ""
        notes.extend(
            f"upstream{version} declares extra {extra!r}, which no output draws on"
            for extra in plan.unaccounted_extras
        )
    return tuple(notes)


def _compact(detail: str, limit: int = 96) -> str:
    """Cut a gate's detail down to something that fits on a summary line.

    A gate reporting several reasons joins them with `; `, and a real one does:
    `apache-airflow-core-split` fails G1 on sixteen separate lines, whose full
    detail wraps to forty lines of terminal. Printed whole it buries every
    other feedstock in the run, which is the exact opposite of what grouping
    by outcome is for (DESIGN.md 9). So the summary names the first reason and
    counts the rest, and `explain` is where all of them live.
    """
    if len(detail) <= limit:
        return detail
    reasons = detail.split("; ")
    if len(reasons) > 1:
        return f"{reasons[0]} (+{len(reasons) - 1} more)"
    return detail[: limit - 1].rstrip() + "…"
