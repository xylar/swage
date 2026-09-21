"""A check is a row; a failure is a value (DESIGN.md §9.7).

`CHECKS` is the table: what each check asks, what it says when it fails, and
whether a failure withholds the push. `find` evaluates every row against a plan
and returns a `Finding` per thing found -- nothing at all for a check that
holds, because "asked and satisfied" is not a value anybody acts on.

What a failure costs is a property of what the check is *about* (v1 §5.4).
Most say a decision is outstanding about a recipe that is otherwise sound: a
line swage kept but cannot explain, an extra nobody has classified, a
temporary bound due for a re-check. The change is complete either way, so it
is pushed and the pull request carries a comment naming what is outstanding.
A few say the rendering itself may be wrong -- a guessed name, a
`run_constraints` entry the reconciled `run` may now contradict, outputs that
cannot be installed together -- and those withhold the push: offering a diff
swage cannot vouch for asks a maintainer to check it line by line in a
repository swage does not own, which is the one review nobody has time for.

The trust rung is not a check. It says which rung the feedstock is on rather
than anything about the change, and it enters the decision (`decision.py`)
rather than the findings. Nor is "the recipe already says what swage would
write": that is the fact `unchanged`, read off the plan.

**A kind is an identifier, never a word swage says out loud.** Every string
that reaches a person is the table's sentence or a finding's `said`, both
written to stand on their own in a comment on a repository swage does not own
(CLAUDE.md). The v1 `G`-number stays in the table for one reason: `run.json`
records this schema's checks under it, and the runs already recorded name
them that way.

These are the highest-value tests in the suite, and they are tested for
*refusal* rather than for acceptance. A false negative here means an unreviewed
bad recipe merges automatically, which is the one outcome this whole design
exists to prevent.
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
    #: What it says when it fails, in the same voice. A marker beside a claim
    #: is not a sentence a reader should have to invert: `FAIL  every
    #: requirement is accounted for` is a true marker attached to a false
    #: sentence, so the report prints the claim where it holds and this where
    #: it does not.
    failure: str
    #: Whether a failure means swage's own rendering may be wrong, rather than
    #: that a decision about a sound one is outstanding. Only these withhold
    #: the push.
    withholds: bool = False
    #: Whether the remedy is one sentence about the whole set of findings,
    #: said once after them, rather than one per finding said beside it.
    shared_remedy: bool = False


#: The table, in v1's order, which is the order a report lists findings in.
#:
#: The distinction `withholds` draws is what the check is *about*, and it is
#: worth stating why each of those is on that side. `unresolved-name` rendered
#: a name it guessed at, so the recipe may ask for the wrong package.
#: `unassociated-constraint` rewrote `run` and cannot tell whether the
#: `run_constrained` entries derived from the same extras still agree with it,
#: so the recipe may now contradict itself. `self-conflict` wrote a recipe
#: whose outputs cannot be installed together.
#:
#: `cross-build-copy` was on that side and its reason has since gone. It was
#: put there because a host change could leave a cross build's *copy* of that
#: requirement stale, which is a rendering swage could not vouch for. swage
#: now keeps those copies in step with the lines they copy, so no copy goes
#: stale. What is left is a name the block does not repeat at all, and whether
#: it belongs there is a judgment about a section swage never writes -- a
#: decision outstanding about a recipe that is otherwise sound. Withholding
#: the push over it left a maintainer asked for that judgment with no diff to
#: make it against.
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

    ``said`` is what is wrong, in terms of the recipe and of upstream and of
    nothing else. **It is the half swage publishes**: a comment on the
    feedstock's own pull request says it, so it names the line, the section
    and what upstream does or does not declare, and never a key in swage's
    config. ``remedy`` is what to do about it, which is where swage's own
    config keys belong, and it reaches swage's own output only (v1 §5.4,
    CLAUDE.md).
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
    """The findings that stop swage offering the change at all.

    Empty for a plan whose only findings are decisions outstanding, which is
    what lets `airflow` be updated while somebody still owes an answer about
    four lines of it (v1 §5.4).
    """
    return tuple(finding for finding in findings if finding.withholds)


def by_kind(findings: Iterable[Finding]) -> dict[Kind, tuple[Finding, ...]]:
    """Findings grouped by check, in the table's order."""
    grouped: dict[Kind, list[Finding]] = {}
    for finding in findings:
        grouped.setdefault(finding.kind, []).append(finding)
    return {row.kind: tuple(grouped[row.kind]) for row in CHECKS if row.kind in grouped}


def summarize(findings: Sequence[Finding]) -> str:
    """One check's findings as the one line the terminal and `run.json` want.

    The `said` halves joined, and the remedy once where it is about the whole
    set. A finding usually ends in a period of its own -- a `reason` is a
    sentence somebody wrote in config -- and appending another produced
    `repodata-patched.. Re-check whether each`, in the terminal report and in
    `swage explain`. Where each finding carries its own remedy the two are
    joined with `--` rather than `;`, because `;` is what separates one
    finding from the next and a remedy containing the separator reads as a
    second finding.
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
    """Every requirement in the plan has a `Provenance`.

    A finding's `said` says what is wrong in terms of the recipe and of
    upstream; its `remedy` names the config key that answers it, and those
    keys exist in swage's repository and not in the feedstock somebody is
    reading. The remedies differ between the ways a line can be unexplained
    and confusing them gives confidently wrong advice, so each finding carries
    its own (v1 §5.4, CLAUDE.md).
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
                # A different failure from a guess, and a different remedy, so
                # it gets its own sentence (v1 §3.2). The line itself is right
                # as far as it goes -- what is missing is whatever the extra
                # pulls in, which is invisible in the recipe.
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
    """Every upstream extra is accounted for -- where the feedstock opts in.

    Exhaustiveness is opt-in and attributability is not (v1 §4). A feedstock
    declares a `skip` list to say "I mean to account for all of these", and
    only then is it held to it. Without one, a newly appeared extra is a note
    beside the record and not a finding, because nothing is wrong when an
    extra shows up that no recipe line comes from.

    Either shape can declare it. Reading only `extras_as_outputs.skip` meant
    the feedstocks that fold extras into an existing output could not opt in
    at all -- they had nowhere to write the decision down, so the check was
    permanently unavailable to exactly the shape the google-cloud family uses.
    """
    if not declares_skip(config):
        return ()
    # The same definition of "accounted for" that the plan reports against, so
    # the finding and the note beside it cannot reach different conclusions
    # about one extra. Still computed from config and upstream rather than read
    # off the plan: this asks what the maintainer wrote down, and the answer
    # should not depend on whether a plan was ever built.
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
    """No published output has lost the upstream extra it is built from.

    The other half -- that the set of outputs is unchanged -- holds by
    construction: swage has no code path that adds or removes one. Adding is a
    packaging decision, and removing an orphaned output is the maintainer's job
    (v1 §3.3.11).
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
    """The plan removes nothing on its own reading -- while `removals: review`.

    A proving period rather than a permanent rule (v1 §3.3.8). The failure
    mode it guards is silent: a dependency that vanishes from a recipe is
    invisible until something fails to import.

    Only the removals swage inferred. A retired line is one config already
    accounted for, and re-asking about it holds every feedstock the entry
    covers, forever.
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
    """Why the plan drops this line, where saying so takes more than the text.

    An upstream-dropped line is explained by the version it went in, which is
    what a reviewer checks. An out-of-range one is not explained by anything
    visible in the diff at all -- upstream still declares the package, and the
    finding is about the pythons it declares it for -- so the reason carries
    the whole of it.
    """
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
    """Upstream declared its dependencies rather than computing them.

    PEP 643 lets a sdist flag that its list was computed at build time, so
    another build might compute a different one (v1 §3.6.3). The list is
    complete, so this holds the pull request for review rather than refusing
    the feedstock.
    """
    if config.dynamic_dependencies == "trust":
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
            "proofread the change, or set dynamic_dependencies: trust for this "
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
    """Every temporary entry has been re-checked at this version.

    A bound the recipe states and upstream does not is drift by default: swage
    reconciles it like any other difference, in either direction, and the
    change is visible as a bump line in the plan and in the pushed diff. What
    is *not* drift is something somebody wrote down in config, and this check
    is about the half of that which must not outlive its reason.

    **Three shapes say it, because there are three things to say it about.**
    `temporary_constraints` tightens a bound on a dependency upstream declares.
    A temporary `add_requirements` entry carries a line upstream does not
    declare at all -- `airflow` dodging a bad `snowflake-connector-python`
    release that nothing it depends on names, which no override can express
    because there is no upstream bound to tighten. An `overruled_constraints`
    entry does not tighten upstream's bound, it stands in for a set of bounds
    upstream cannot make agree -- so the line it produces is right only for as
    long as upstream keeps disagreeing with itself in the same terms, and a
    new version is exactly when that stops being true (v1 §3.3.2, §3.3.14).

    **Asking does not cost the update.** The recipe swage rendered is sound,
    so it is pushed and the question is put on the pull request. Under the
    previous rule a workaround nobody could retire blocked every other change
    to the feedstock, which made "swage asks again at the next bump" mean
    "swage never gets to the next bump".

    `constraints` and an ordinary `add_requirements` entry are the other halves
    and are silent here: both say the line is meant to hold, so re-asking would
    be asking about a decision already on the record.
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
    """swage changed no python test matrix -- while `test_matrix: review`.

    A proving period rather than a permanent rule (v1 §3.7), and the reason
    it exists is structural rather than about any one recipe. Every other edit
    swage makes is inside a requirements block; this is the first one that is
    not, so while the behavior is new a recipe swage completed the matrix of
    gets a human before it merges.

    What it guards is *not* whether the edit is right. CI decides that, and
    decides it well: adding the latest Python to the matrix makes the test run
    on that Python, so a green run is the change proving itself and a red one
    is a real incompatibility that was already shipping.
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
    """A `host` change on an output that also builds for another platform.

    A cross-compilation block repeats `host` requirements so that the build
    tools resolve for the platform doing the building, and 15 of the 19 outputs
    in the fleet with such a block do exactly that. Which requirements belong
    there is a judgment per dependency that no metadata contains (v1
    §3.3.6.1), so swage writes the `host` change it can justify and leaves that
    judgment to a human -- which means not merging it unattended.

    **A copy the block already holds is not that judgment**, and swage keeps
    it in step with the line it copies rather than asking. What reaches here
    is the rest: a name the block does not repeat, or one whose copy already
    said something different from `host` and was left as written.
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
    """No output requires a package this recipe builds at a version it does not.

    A split recipe builds several archives and its outputs depend on each
    other, so the two can disagree -- and the disagreement is invisible in the
    diff, because each line is individually right. `airflow` pins the
    `apache-airflow-task-sdk` sdist at 1.3.0 through `context.task_sdk_version`
    while `apache-airflow-core` 3.3.1 requires `==1.3.1`, which is the manual
    step beside that line not having been taken.

    Merging it would ship packages built from one release that ask for another,
    so this withholds the push (v1 §5.4).

    **Where the feedstock sets `source_versions: auto` this rarely fires**, and
    when it does it is because swage declined to make the edit rather than
    because it could not: two releases asking for different versions, a
    `context` entry swage could not identify, an archive whose metadata
    contradicts the URL it came from (v1 §3.6.5). Everywhere else the fix is
    still a person's, and the message says which line to change.
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
    """No script the recipe lists goes away without a person seeing it.

    A retarget or an addition is upstream's own declaration and needs no
    more review than a dependency bound does; CI runs the command where the
    recipe tests it. A line going away is different in kind: the command a
    user has installed stops existing, whether upstream renamed it or
    removed it, and that is worth one look -- once, since after the push the
    recipe says what upstream says and the next run has nothing to hold
    (v1 §3.3.15).

    Not a policy knob. `entry_points: manual` is the escape hatch for a list
    that is deliberately conda-forge's own, and there the plan holds no
    change at all.
    """
    return [
        Finding("dropped-script", item, change.output or "", sentence)
        for change in plan.entry_points
        for item, sentence in zip(change.dropped, change.held, strict=True)
    ]
