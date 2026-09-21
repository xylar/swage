"""A check is a row; a failure is a value (DESIGN.md §9.7).

`CHECKS` is the table: what each check asks, what it says when it fails, and
whether a failure withholds the push. `find` evaluates every row against a plan
and returns a `Finding` per thing found, nothing for a check that holds. The
trust rung is not a check; it enters the decision (§9.8). A kind is an
identifier, never a word swage says: what reaches a person is the table's
sentence or a finding's `said` (§3.1). The v1 `G`-number stays in the table
because `run.json` records the check under it.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from swage.config import FeedstockConfig
from swage.upstream import RecipeUpstream

from .assemble import accounted_extras, declares_skip
from .prose import fenced, section_phrase
from .removals import Removal

if TYPE_CHECKING:
    # `plan` builds the `Plan` and asks this module what it makes of it, so
    # the name is needed here for the annotation only.
    from .plan import Plan

__all__ = [
    "CHECKS",
    "Check",
    "Finding",
    "Kind",
    "by_kind",
    "check",
    "find",
    "summarize",
    "withheld",
]

Kind = Literal[
    "unaccounted",
    "unresolved-name",
    "unclassified-extra",
    "orphaned-output",
    "removal",
    "unassociated-constraint",
    "computed-dependencies",
    "recheck",
    "test-matrix",
    "cross-build-copy",
    "self-conflict",
    "dropped-script",
]


@dataclass(frozen=True)
class Check:
    """One row of the table: what a check asks and what it says when it fails."""

    kind: Kind
    #: The v1 name, under which `run.json` records the check and the recorded
    #: runs already name it.
    v1: str
    #: What the check asks, phrased as the claim a passing check makes.
    title: str
    #: What it says when it fails, in the same voice, so the report never prints
    #: a marker a reader has to invert.
    failure: str
    #: Whether a failure means swage's own rendering may be wrong, rather than
    #: that a decision about a sound one is outstanding. Only these withhold the
    #: push (DESIGN.md §9.7).
    withholds: bool = False
    #: Whether the remedy is one sentence about the whole set of findings,
    #: said once after them, rather than one per finding said beside it.
    shared_remedy: bool = False


#: The table, in v1's order, which is the order a report lists findings in.
#: Which checks withhold is DESIGN.md §9.7's severity column.
CHECKS: tuple[Check, ...] = (
    Check(
        "unaccounted",
        "G1",
        "every requirement is accounted for",
        "a requirement is not accounted for",
    ),
    Check(
        "unresolved-name",
        "G2",
        "every name resolves to a conda-forge package",
        "a name resolves to no conda-forge package",
        withholds=True,
    ),
    Check(
        "unclassified-extra",
        "G3",
        "every upstream extra is supported or skipped",
        "an upstream extra is neither supported nor skipped",
    ),
    Check(
        "orphaned-output",
        "G4",
        "no output has lost its upstream extra",
        "an output has lost its upstream extra",
    ),
    Check(
        "removal",
        "G8",
        "no requirement is removed without review",
        "a requirement would be removed without review",
    ),
    Check(
        "unassociated-constraint",
        "G9",
        "every run constraint matches an upstream extra",
        "a run constraint matches no upstream extra",
        withholds=True,
    ),
    Check(
        "computed-dependencies",
        "G10",
        "upstream declared its dependencies rather than computing them",
        "upstream computed its dependencies rather than declaring them",
    ),
    Check(
        "recheck",
        "G11",
        "every temporary entry has been re-checked",
        "a temporary entry has not been re-checked",
        shared_remedy=True,
    ),
    Check(
        "test-matrix",
        "G12",
        "no python test matrix was extended",
        "a python test matrix was extended",
    ),
    Check(
        "cross-build-copy",
        "G13",
        "no cross-compiled output has its host requirements changed",
        "a cross-compiled output has its host requirements changed",
    ),
    Check(
        "self-conflict",
        "G14",
        "no built package is required at another version",
        "a built package is required at another version",
        withholds=True,
    ),
    Check(
        "dropped-script",
        "G15",
        "no entry point is dropped without review",
        "an entry point would be dropped without review",
    ),
)

_BY_KIND = {row.kind: row for row in CHECKS}


def check(kind: Kind) -> Check:
    """The table's row for a kind."""
    return _BY_KIND[kind]


@dataclass(frozen=True)
class Finding:
    """One thing a check found.

    ``said`` is the half swage publishes, in terms of the recipe and of
    upstream; ``remedy`` names config keys and reaches swage's own output
    only (DESIGN.md §3.1).
    """

    kind: Kind
    #: The line, the name, the extra, the output: what the finding is about.
    subject: str
    #: "`esmf`'s `host` requirements", never a path. Empty where the check
    #: does not know the section.
    where: str
    said: str
    remedy: str = ""

    @property
    def check(self) -> Check:
        return _BY_KIND[self.kind]

    @property
    def withholds(self) -> bool:
        return self.check.withholds


def find(
    plan: Plan, config: FeedstockConfig, upstream: RecipeUpstream
) -> tuple[Finding, ...]:
    """Every finding against a plan, in the table's order."""
    return (
        *_unaccounted(plan),
        *_unresolved_names(plan),
        *_unclassified_extras(config, upstream),
        *_orphaned_outputs(config, upstream),
        *_removals(plan, config),
        *_unassociated_constraints(plan),
        *_computed_dependencies(config, upstream),
        *_rechecks(plan),
        *_test_matrix(plan, config),
        *_cross_build_copies(plan),
        *_self_conflicts(plan),
        *_dropped_scripts(plan),
    )


def withheld(findings: Iterable[Finding]) -> tuple[Finding, ...]:
    """The findings that stop swage offering the change at all (DESIGN.md §9.7)."""
    return tuple(finding for finding in findings if finding.withholds)


def by_kind(findings: Iterable[Finding]) -> dict[Kind, tuple[Finding, ...]]:
    """Findings grouped by check, in the table's order."""
    grouped: dict[Kind, list[Finding]] = {}
    for finding in findings:
        grouped.setdefault(finding.kind, []).append(finding)
    return {row.kind: tuple(grouped[row.kind]) for row in CHECKS if row.kind in grouped}


def summarize(findings: Sequence[Finding]) -> str:
    """One check's findings as the one line the terminal and `run.json` want.

    The `said` halves joined with `;`, and the remedy once where it is about
    the whole set. A per-finding remedy joins with `--`, since `;` separates
    findings.
    """
    row = findings[0].check
    if row.shared_remedy:
        listed = "; ".join(finding.said for finding in findings)
        remedy = findings[0].remedy
        separator = " " if listed.endswith((".", "!", "?")) else ". "
        return f"{listed}{separator}{remedy}" if remedy else listed
    return "; ".join(
        f"{finding.said} -- {finding.remedy}" if finding.remedy else finding.said
        for finding in findings
    )


def _unaccounted(plan: Plan) -> Iterable[Finding]:
    """Every requirement in the plan has a `Provenance` (G1).

    Each finding carries its own remedy, because the ways a line can be
    unexplained have different fixes (DESIGN.md §9.5).
    """
    for section in plan.sections:
        for item in section.unexplained:
            yield Finding(
                "unaccounted", item.text, section.where, item.reason, item.remedy
            )


def _unresolved_names(plan: Plan) -> Iterable[Finding]:
    """Every name resolution is exact -- no guesses, no unresolved names."""
    found: dict[str, Finding] = {}
    for section in plan.sections:
        # Every entry, not every line: a dependency stated per python range is
        # as much a name that had to resolve as a plain one (v1 §3.3.1.1).
        for requirement in section.entries:
            provenance = requirement.provenance
            if provenance.origin in {"recipe-kept", "config-add"}:
                # Neither reaches the resolver: one is structure, the other is
                # a conda name a human wrote down (v1 §3.3.6).
                continue
            mapping = provenance.mapping
            remedy = ""
            if mapping is None:
                said = f"no conda-forge package found for {fenced(requirement.name)}"
            elif mapping.dropped_extras:
                # A different failure from a guess, and a different remedy (v1
                # §3.2).
                named = ", ".join(fenced(extra) for extra in mapping.dropped_extras)
                said = (
                    f"{fenced(mapping.pypi_name)} resolved to "
                    f"{fenced(mapping.conda_name)}, dropping extra {named}"
                )
                remedy = (
                    "map the requirement in name_map if conda-forge has a "
                    "package for it, or write out what it pulls in under "
                    "embedded_extras"
                )
            elif not mapping.exact:
                said = (
                    f"{fenced(mapping.pypi_name)} was matched to "
                    f"{fenced(mapping.conda_name)} by guesswork "
                    "rather than by a lookup"
                )
            else:
                continue
            # One finding per sentence, however many sections say it: the same
            # name resolves the same way in each of them.
            found.setdefault(
                said,
                Finding(
                    "unresolved-name", requirement.name, section.where, said, remedy
                ),
            )
    return [found[said] for said in sorted(found)]


def _unclassified_extras(
    config: FeedstockConfig, upstream: RecipeUpstream
) -> Iterable[Finding]:
    """Every upstream extra is accounted for, where the feedstock opts in (G3).

    Exhaustiveness is opt-in, through a `skip` list in either shape (v1 §4);
    without one a new extra is a note, not a finding.
    """
    if not declares_skip(config):
        return ()
    # The plan's own definition of "accounted for", computed from config and
    # upstream rather than read off the plan.
    accounted = accounted_extras(config)
    missing = [extra for extra in upstream.extras if extra not in accounted]
    if not missing:
        return ()
    named = ", ".join(fenced(extra) for extra in missing)
    return (
        Finding(
            "unclassified-extra",
            ", ".join(missing),
            "",
            f"upstream extra {named} is neither carried by an output nor declined",
            "add it to supported or to skip, so the decision is on the record",
        ),
    )


def _orphaned_outputs(
    config: FeedstockConfig, upstream: RecipeUpstream
) -> Iterable[Finding]:
    """No published output has lost the upstream extra it is built from (G4,
    v1 §3.3.11). The set of outputs is unchanged by construction.
    """
    extras_as_outputs = config.extras_as_outputs
    if extras_as_outputs is None or not extras_as_outputs.supported:
        return ()
    orphaned = [
        extra for extra in extras_as_outputs.supported if extra not in upstream.extras
    ]
    if not orphaned:
        return ()
    named = ", ".join(fenced(extra) for extra in orphaned)
    version = f" {upstream.version}" if upstream.version else ""
    return (
        Finding(
            "orphaned-output",
            ", ".join(orphaned),
            "",
            f"an output is built from upstream extra {named}, which{version} "
            "no longer declares",
            "delete the output from the recipe and remove the extra from "
            "extras_as_outputs.supported",
        ),
    )


def _removals(plan: Plan, config: FeedstockConfig) -> Iterable[Finding]:
    """The plan removes nothing on its own reading, while `removals: review`
    (G8, DESIGN.md §9.5). A retired line is config's own decision and is not
    asked about.
    """
    if config.removals == "auto":
        return ()
    return [
        Finding(
            "removal",
            removal.text,
            section.where,
            "would remove " + fenced(removal.text) + _because(removal),
        )
        for section in plan.sections
        for removal in section.dropped
        if removal.fate in {"upstream-dropped", "out-of-range"}
    ]


def _because(removal: Removal) -> str:
    """Why the plan drops this line, where saying so takes more than the text."""
    if removal.fate == "out-of-range":
        return f" ({removal.reason})"
    return f" (gone in {removal.dropped_in})" if removal.dropped_in else ""


def _unassociated_constraints(plan: Plan) -> Iterable[Finding]:
    """Every `run_constraints` entry is associated with an upstream extra."""
    return [
        Finding("unassociated-constraint", entry.text, "", entry.reason)
        for entry in plan.unassociated_constraints
    ]


def _computed_dependencies(
    config: FeedstockConfig, upstream: RecipeUpstream
) -> Iterable[Finding]:
    """Upstream declared its dependencies rather than computing them (G10,
    v1 §3.6.3). Holds for review; the list is complete.
    """
    if config.dynamic_dependencies == "auto":
        return ()
    dynamic = sorted(upstream.dynamic_fields & {"requires-dist", "provides-extra"})
    if not dynamic:
        return ()
    named = ", ".join(fenced(field) for field in dynamic)
    return (
        Finding(
            "computed-dependencies",
            ", ".join(dynamic),
            "",
            f"upstream computed {named} at build time rather than declaring it, "
            "so another build may produce a different list",
            "proofread the change, or set dynamic_dependencies: auto for this "
            "feedstock",
        ),
    )


#: What to do about the set of temporary entries, said once after them.
_RECHECK = (
    "Re-check whether each is still needed: drop the entry and let "
    "swage reconcile the line if it is not, record a temporary "
    "constraint as permanent if the recipe is meant to keep it, or "
    "restate an overruling bound if upstream has moved"
)


def _rechecks(plan: Plan) -> Iterable[Finding]:
    """Every temporary entry has been re-checked at this version (G11).

    Three shapes: `temporary_constraints`, a temporary `add_requirements`
    entry, and `overruled_constraints` (v1 §3.3.2, §3.3.14). Asking does not
    cost the update. `constraints` and a plain `add_requirements` entry are
    meant to hold and are silent here.
    """
    found: list[Finding] = []
    for section in plan.sections:
        found.extend(
            Finding(
                "recheck",
                override.bound,
                section.where,
                f"{fenced(override.bound)} is a temporary constraint -- "
                f"{override.reason}",
                _RECHECK,
            )
            for override in section.overrides
        )
    for section in plan.sections:
        found.extend(
            Finding(
                "recheck",
                override.bound,
                section.where,
                f"{fenced(override.bound)} overrules upstream's conflicting bounds "
                f"-- {override.reason}",
                _RECHECK,
            )
            for override in section.overruled
        )
    for section in plan.sections:
        found.extend(
            Finding(
                "recheck",
                addition.text,
                section.where,
                f"{fenced(addition.text)} is a temporary requirement -- "
                f"{addition.reason}",
                _RECHECK,
            )
            for addition in section.temporary_additions
        )
    return found


def _test_matrix(plan: Plan, config: FeedstockConfig) -> Iterable[Finding]:
    """swage changed no python test matrix, while `test_matrix: review` (G12,
    DESIGN.md §9.6).
    """
    if not plan.test_matrices or config.test_matrix == "auto":
        return ()
    return [
        Finding(
            "test-matrix",
            matrix.output or "python_version",
            "",
            matrix.reason,
            "confirm it, or set test_matrix: auto for this feedstock once the "
            "change has been seen to build",
        )
        for matrix in plan.test_matrices
    ]


def _cross_build_copies(plan: Plan) -> Iterable[Finding]:
    """A `host` change on an output that also builds for another platform (G13).

    Whether a requirement belongs in the cross-compilation block is a judgment
    no metadata contains (v1 §3.3.6.1). A copy the block already holds is kept
    in step rather than asked about.
    """
    return [
        Finding(
            "cross-build-copy",
            output,
            section_phrase("host", output),
            f"{section_phrase('host', output)} changed, and this output also "
            "builds for a platform other than the one it is built on -- check "
            "whether its build section repeats what changed",
        )
        for output in plan.cross_compiled
    ]


def _self_conflicts(plan: Plan) -> Iterable[Finding]:
    """No output requires a package this recipe builds at a version it does not
    (G14).

    Each line is individually right and the pair is wrong, so this withholds
    the push (DESIGN.md §9.7). Under `source_versions: auto` it fires only
    where swage declined the edit (v1 §3.6.5).
    """
    return [
        Finding(
            "self-conflict",
            conflict.package,
            fenced(conflict.output),
            f"{fenced(conflict.output)} requires "
            f"{fenced(f'{conflict.package} {conflict.constraint}')}, and this "
            f"recipe builds {conflict.package} {conflict.built} -- update the "
            "version the recipe's source is pinned at, or the packages will "
            "not install together",
        )
        for conflict in plan.self_conflicts
    ]


def _dropped_scripts(plan: Plan) -> Iterable[Finding]:
    """No script the recipe lists goes away without a person seeing it (G15,
    DESIGN.md §9.6).

    A retarget or an addition is upstream's own declaration and is a note;
    a drop is held once. `entry_points: manual` takes the list out.
    """
    return [
        Finding("dropped-script", item, change.output or "", sentence)
        for change in plan.entry_points
        for item, sentence in zip(change.dropped, change.held, strict=True)
    ]
