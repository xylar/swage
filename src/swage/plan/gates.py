"""The trust gates (DESIGN.md 5.4).

A feedstock's pull request gets the `automerge` label only if **all** of these
hold. What a failure costs beyond the label depends on *what the check is
about* (DESIGN.md 5.4).

**Most of them say a decision is outstanding about a recipe that is otherwise
sound.** G1 kept a line it could not explain -- it never deletes one -- and G3,
G4, G10, G11 and G12 are each waiting on somebody. The change swage made is
complete either way, so it is pushed, the pull request carries a comment
naming what is outstanding, and no label is applied.

**A few say the rendering itself may be wrong**, and those are `WITHHOLDS_PUSH`
below. Nothing is pushed at all: offering a diff swage cannot vouch for asks a
maintainer to check it line by line in a repository swage does not own, which
is the one review nobody has time for.

**`G1` is an identifier, never a word swage says out loud.** The numbering is
how this file, its tests and `run.json` refer to a check; every string that
reaches a human uses `TITLES` and a detail that stands on its own. A report
reading `G6: trust is 'propose', not 'auto'` can only be understood by someone
holding the design open, and by the time swage is commenting on other people's
pull requests that is no longer an acceptable thing to ask (CLAUDE.md).

These are the highest-value tests in the suite, and they are tested for
*refusal* rather than for acceptance. A false negative here means an unreviewed
bad recipe merges automatically, which is the one outcome this whole design
exists to prevent.

Two of them are structural rather than computed, and saying so is the point.
G5 is true by construction: swage writes by splicing requirements-block line
ranges into the original text (DESIGN.md 3.1), so nothing outside one *can*
change. The first half of G4 is the same -- swage has no code path that adds or
removes an output. A gate that cannot fail is still worth naming, because the
reason it cannot fail is a property someone could remove by accident.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from swage.config import FeedstockConfig
from swage.upstream import RecipeUpstream

from .assemble import RecipePlan, accounted_extras, declares_skip
from .prose import fenced

__all__ = [
    "TITLES",
    "WITHHOLDS_PUSH",
    "Decision",
    "GateResult",
    "Verdict",
    "evaluate_gates",
]

#: What each check actually asks, in words that need no design document.
#:
#: Phrased as the claim rather than as the failure, so that the same string
#: reads correctly whichever verdict is printed beside it: "pass -- every
#: requirement is accounted for" and "FAIL -- every requirement is accounted
#: for" are both sentences a reader can act on.
TITLES = {
    "G1": "every requirement is accounted for",
    "G2": "every name resolves to a conda-forge package",
    "G3": "every upstream extra is listed as supported or skipped",
    "G4": "no output has lost the upstream extra it is built from",
    "G5": "only requirements changed",
    "G6": "this feedstock is approved for automatic merging",
    "G7": "the recipe already says what swage would write",
    "G8": "nothing upstream dropped is removed without review",
    "G9": "every run constraint is tied to an upstream extra",
    "G10": "upstream declared its dependencies rather than computing them",
    "G11": "every temporary entry has been re-checked",
    "G12": "the python test matrix is left as the recipe has it",
    "G13": "no cross-compiled output has its host requirements changed",
    "G14": "every package this recipe builds is required at the version it builds",
}

#: The checks whose failure means swage's own rendering may be wrong, rather
#: than that a decision about a sound one is outstanding. Only these withhold
#: the push (DESIGN.md 5.4).
#:
#: The distinction is what the check is *about*, and it is worth stating why
#: each of these is on this side. **G2** rendered a name it guessed at, so the
#: recipe may ask for the wrong package. **G5** changed something outside the
#: regions swage owns. **G9** rewrote `run` and cannot tell whether the
#: `run_constrained` entries derived from the same extras still agree with it,
#: so the recipe may now contradict itself. **G13** changed a `host`
#: requirement that a cross build's `build` section may need a copy of, so the
#: change is incomplete. **G14** wrote a recipe whose outputs cannot be
#: installed together.
#:
#: **G6 is on neither side.** It says which rung the feedstock is on rather
#: than anything about the change, which is why `held` excludes it too: reading
#: it here would leave `propose` unable to push, and pushing is the whole of
#: what `propose` does.
WITHHOLDS_PUSH = frozenset({"G2", "G5", "G9", "G13", "G14"})

#: What swage does with the pull request once the gates have spoken.
#:
#: Only one of the two is the name of anything on GitHub. `automerge` is
#: conda-forge's own label and swage applies it; `needs-review` is a verdict
#: swage *states*, in a comment on the pull request, because no feedstock has a
#: `swage:needs-review` label and inventing one would mean creating it in
#: several hundred repositories (DESIGN.md 5.4).
Decision = Literal["automerge", "needs-review"]


@dataclass(frozen=True)
class GateResult:
    """One gate's verdict, and enough detail to act on a failure."""

    name: str
    #: None where the gate does not apply to this feedstock -- an opt-in gate
    #: the config did not opt into, or a path this run is not on. Distinct from
    #: passing, because "not asked" and "asked and satisfied" are different
    #: claims and the report prints them differently.
    passed: bool | None
    detail: str = ""

    @property
    def blocking(self) -> bool:
        return self.passed is False

    @property
    def title(self) -> str:
        """What this check asks, in words a report can print."""
        return TITLES[self.name]


@dataclass(frozen=True)
class Verdict:
    """Whether this plan may merge unattended, and why not where it may not."""

    gates: tuple[GateResult, ...]

    @property
    def failures(self) -> tuple[GateResult, ...]:
        return tuple(gate for gate in self.gates if gate.blocking)

    @property
    def decision(self) -> Decision:
        return "needs-review" if self.failures else "automerge"

    @property
    def withheld(self) -> tuple[GateResult, ...]:
        """The failures that stop swage offering the change at all.

        Empty for a plan whose only failures are decisions outstanding, which
        is what lets `airflow` be updated while somebody still owes an answer
        about four lines of it (DESIGN.md 5.4).
        """
        return tuple(gate for gate in self.failures if gate.name in WITHHOLDS_PUSH)

    @property
    def held(self) -> bool:
        """Whether any check but the trust ladder itself found something.

        What decides whether swage has a change worth offering. G6 is excluded
        because it says which rung the feedstock is on rather than anything
        about the change: reading it here would leave `propose` unable to push,
        which is the whole of what `propose` does (DESIGN.md 5.4).
        """
        return any(gate.name != "G6" for gate in self.failures)

    @property
    def summary(self) -> str:
        """The gates that blocked, named, for the report's one-line verdict."""
        return ", ".join(gate.name for gate in self.failures)


def evaluate_gates(
    plan: RecipePlan,
    config: FeedstockConfig,
    upstream: RecipeUpstream,
    path_b: bool = False,
    unchanged: bool | None = None,
    output_names: Sequence[str] = (),
) -> Verdict:
    """Evaluate G1-G11 against a plan.

    ``path_b`` marks the case where swage changed nothing and intends to merge
    the pull request itself (DESIGN.md 5.2), which is the only path G7 applies
    to. ``unchanged`` is whether swage's rendering matches what is already in
    the pull request; it is the write layer that can answer that, so it is
    passed in rather than guessed at here.
    """
    return Verdict(
        gates=(
            _g1(plan),
            _g2(plan),
            _g3(config, upstream),
            _g4(config, upstream, output_names),
            _g5(),
            _g6(config),
            _g7(path_b, unchanged),
            _g8(plan, config),
            _g9(plan),
            _g10(plan, config, upstream),
            _g11(plan),
            _g12(plan, config),
            _g13(plan),
            _g14(plan),
        )
    )


def _g14(plan: RecipePlan) -> GateResult:
    """No output requires a package this recipe builds at a version it does not.

    A split recipe builds several archives and its outputs depend on each
    other, so the two can disagree -- and the disagreement is invisible in the
    diff, because each line is individually right. `airflow` pins the
    `apache-airflow-task-sdk` sdist at 1.3.0 through `context.task_sdk_version`
    while `apache-airflow-core` 3.3.1 requires `==1.3.1`, which is the manual
    step beside that line not having been taken.

    Merging it would ship packages built from one release that ask for another,
    so this withholds the push (DESIGN.md 5.4).

    **Where the feedstock sets `source_versions: auto` this rarely fires**, and
    when it does it is because swage declined to make the edit rather than
    because it could not: two releases asking for different versions, a
    `context` entry swage could not identify, an archive whose metadata
    contradicts the URL it came from (DESIGN.md 3.6.4). Everywhere else the fix
    is still a person's, and the message says which line to change.
    """
    if not plan.self_conflicts:
        return GateResult("G14", True)
    return GateResult(
        "G14",
        False,
        "; ".join(
            f"{fenced(conflict.output)} requires "
            f"{fenced(f'{conflict.package} {conflict.constraint}')}, and this "
            f"recipe builds {conflict.package} {conflict.built} -- update the "
            "version the recipe's source is pinned at, or the packages will "
            "not install together"
            for conflict in plan.self_conflicts
        ),
    )


def _g13(plan: RecipePlan) -> GateResult:
    """A `host` change on an output that also builds for another platform.

    A cross-compilation block repeats `host` requirements so that the build
    tools resolve for the platform doing the building, and 15 of the 19 outputs
    in the fleet with such a block do exactly that. Which requirements belong
    there is a judgement per dependency that no metadata contains (DESIGN.md
    3.3.6.1), so swage writes the `host` change it can justify and leaves the
    mirroring to a human -- which means not merging it unattended.
    """
    if not plan.cross_compiled:
        return GateResult("G13", True)
    return GateResult(
        "G13",
        False,
        "; ".join(
            f"{fenced(path)} changed, and this output also builds for a "
            "platform other than the one it is built on -- check whether its "
            "build section repeats what changed"
            for path in plan.cross_compiled
        ),
    )


def _g1(plan: RecipePlan) -> GateResult:
    """Every requirement in the plan has a `Provenance`."""
    unexplained = plan.unexplained
    if not unexplained:
        return GateResult("G1", True)
    return GateResult(
        "G1",
        False,
        "; ".join(item.reason for item in unexplained),
    )


def _g2(plan: RecipePlan) -> GateResult:
    """Every name resolution is exact -- no guesses, no unresolved names."""
    inexact: list[str] = []
    for section in plan.sections:
        # Every entry, not every line: a dependency stated per python range is
        # as much a name that had to resolve as a plain one (DESIGN.md 3.3.1.1).
        for requirement in section.entries:
            provenance = requirement.provenance
            if provenance.origin in {"recipe-kept", "config-add"}:
                # Neither reaches the resolver: one is structure, the other is
                # a conda name a human wrote down (DESIGN.md 3.3.6).
                continue
            if provenance.mapping is None:
                inexact.append(
                    f"no conda-forge package found for {fenced(requirement.name)}"
                )
            elif provenance.mapping.dropped_extras:
                # A different failure from a guess, and a different remedy, so
                # it gets its own sentence (DESIGN.md 3.2). The line itself is
                # right as far as it goes -- what is missing is whatever the
                # extra pulls in, which is invisible in the recipe.
                #
                # Joined with `--` rather than `;` because `;` is what
                # separates one entry of this gate from the next, and a remedy
                # containing the separator reads as a second failure.
                mapping = provenance.mapping
                named = ", ".join(fenced(extra) for extra in mapping.dropped_extras)
                inexact.append(
                    f"{fenced(mapping.pypi_name)} resolved to "
                    f"{fenced(mapping.conda_name)}, "
                    f"dropping extra {named} -- map the requirement in name_map "
                    "if conda-forge has a package for it, or write out what it "
                    "pulls in under embedded_extras"
                )
            elif not provenance.mapping.exact:
                inexact.append(
                    f"{fenced(provenance.mapping.pypi_name)} was matched to "
                    f"{fenced(provenance.mapping.conda_name)} by guesswork "
                    "rather than by a lookup"
                )
    if not inexact:
        return GateResult("G2", True)
    return GateResult("G2", False, "; ".join(sorted(set(inexact))))


def _g3(config: FeedstockConfig, upstream: RecipeUpstream) -> GateResult:
    """Every upstream extra is accounted for -- where the feedstock opts in.

    Exhaustiveness is opt-in and attributability is not (DESIGN.md 4). A
    feedstock declares a `skip` list to say "I mean to account for all of
    these", and only then is it held to it. Without one, a newly appeared extra
    is reported and not gated, because nothing is wrong when an extra shows up
    that no recipe line comes from.

    Either shape can declare it. Reading only `extras_as_outputs.skip` meant
    the feedstocks that fold extras into an existing output could not opt in
    at all -- they had nowhere to write the decision down, so the gate was
    permanently unavailable to exactly the shape the google-cloud family uses.
    """
    if not declares_skip(config):
        return GateResult("G3", None, "feedstock declares no skip list")

    # The same definition of "accounted for" that the plan reports against, so
    # the gate and the note beside it cannot reach different conclusions about
    # one extra. Still computed from config and upstream rather than read off
    # the plan: this gate asks what the maintainer wrote down, and the answer
    # should not depend on whether a plan was ever built.
    accounted = accounted_extras(config)

    missing = [extra for extra in upstream.extras if extra not in accounted]
    if not missing:
        return GateResult("G3", True)
    named = ", ".join(fenced(extra) for extra in missing)
    return GateResult(
        "G3",
        False,
        f"upstream extra {named} is in neither supported nor skip; "
        "add it to one so the decision is on the record",
    )


def _g4(
    config: FeedstockConfig,
    upstream: RecipeUpstream,
    output_names: Sequence[str],
) -> GateResult:
    """No published output has lost the upstream extra it is built from.

    The other half -- that the set of outputs is unchanged -- holds by
    construction: swage has no code path that adds or removes one. Adding is a
    packaging decision, and removing an orphaned output is the maintainer's job
    (DESIGN.md 3.3.11).
    """
    extras_as_outputs = config.extras_as_outputs
    if extras_as_outputs is None or not extras_as_outputs.supported:
        return GateResult("G4", None, "feedstock publishes no extras as outputs")

    orphaned = [
        extra for extra in extras_as_outputs.supported if extra not in upstream.extras
    ]
    if not orphaned:
        return GateResult("G4", True)
    named = ", ".join(fenced(extra) for extra in orphaned)
    version = f" {upstream.version}" if upstream.version else ""
    return GateResult(
        "G4",
        False,
        f"output built from upstream extra {named}, which{version} no longer "
        "declares; delete the output from the recipe and remove the extra "
        "from extras_as_outputs.supported",
    )


def _g5() -> GateResult:
    """The diff touches only requirements sections.

    True by construction rather than by inspection: the writer replaces the
    line ranges the reader identified and leaves every other byte alone
    (DESIGN.md 3.1). Named anyway, because the property is one a future change
    to the write path could remove without noticing.
    """
    return GateResult("G5", True, "requirements-only by construction")


def _g6(config: FeedstockConfig) -> GateResult:
    """Blessing is explicit and opt-in.

    **The two failing rungs mean opposite things and get different sentences.**
    `propose` really is about the label: swage pushed the commit, commented,
    and left the merge to a person. `off` is about the whole feedstock --
    nothing was written, and saying "not approved for automatic merging"
    answers a question nobody asked while the fact that explains the run sits
    somewhere else. That is not hypothetical; it is what the maintainer read
    off an `--execute` run and could not account for, having asked for the
    update by hand.

    The remedy names the file, because the rung is a standing decision about a
    feedstock and taking it is a commit somebody makes on purpose
    (DESIGN.md 5.4).
    """
    if config.trust == "auto":
        return GateResult("G6", True)
    if config.trust == "never":
        return GateResult(
            "G6",
            False,
            f"swage writes nothing to this feedstock (trust: never); remove that "
            f"line from config/feedstocks/{config.feedstock}.yaml for swage to "
            "push the change and comment",
        )
    return GateResult(
        "G6",
        False,
        f"not approved for automatic merging (trust: {config.trust})",
    )


def _g7(path_b: bool, unchanged: bool | None) -> GateResult:
    """swage's rendering is byte-identical to the pull request's recipe.

    Path B only. There swage is the only thing between the bot's pull request
    and `main`, so "no modification needed" has to be a *verified* claim rather
    than an assumption (DESIGN.md 5.3). Any difference at all -- including
    formatting or dependency order -- means the feedstock goes down Path A.
    """
    if not path_b:
        return GateResult(
            "G7", None, "swage changed the recipe, so conda-forge decides the merge"
        )
    if unchanged is None:
        return GateResult("G7", False, "rendering was never compared")
    if unchanged:
        return GateResult("G7", True)
    return GateResult("G7", False, "swage's rendering differs from the recipe")


def _g8(plan: RecipePlan, config: FeedstockConfig) -> GateResult:
    """The plan drops nothing upstream dropped -- while `removals: review`.

    A proving period rather than a permanent rule (DESIGN.md 3.3.8). The
    failure mode it guards is silent: a dependency that vanishes from a recipe
    is invisible until something fails to import.
    """
    if config.removals == "auto":
        return GateResult(
            "G8", None, "the feedstock sets removals: auto, so removals need no review"
        )
    # Only the removals swage inferred. A retired line is one config already
    # accounted for, and re-asking about it holds every feedstock the entry
    # covers, forever (DESIGN.md 3.3.8).
    dropped = plan.upstream_dropped
    if not dropped:
        return GateResult("G8", True)
    named = "; ".join(
        fenced(removal.text)
        + (f" (gone in {removal.dropped_in})" if removal.dropped_in else "")
        for removal in dropped
    )
    return GateResult("G8", False, f"would remove {named}")


def _g9(plan: RecipePlan) -> GateResult:
    """Every `run_constraints` entry is associated with an upstream extra."""
    if not plan.unassociated_constraints:
        return GateResult("G9", True)
    return GateResult(
        "G9",
        False,
        "; ".join(entry.reason for entry in plan.unassociated_constraints),
    )


def _g10(
    plan: RecipePlan, config: FeedstockConfig, upstream: RecipeUpstream
) -> GateResult:
    """Upstream declared its dependencies rather than computing them.

    PEP 643 lets a sdist flag that its list was computed at build time, so
    another build might compute a different one (DESIGN.md 3.6.3). The list is
    complete, so this holds the pull request for review rather than refusing
    the feedstock.
    """
    if config.dynamic_dependencies == "trust":
        return GateResult("G10", None, "the feedstock sets dynamic_dependencies: trust")
    dynamic = sorted(upstream.dynamic_fields & {"requires-dist", "provides-extra"})
    if not dynamic:
        return GateResult("G10", True)
    named = ", ".join(fenced(field) for field in dynamic)
    return GateResult(
        "G10",
        False,
        f"upstream computed {named} at build time rather than declaring it, "
        "so another build may produce a different list -- proofread, or set "
        "dynamic_dependencies: trust for this feedstock",
    )


def _g11(plan: RecipePlan) -> GateResult:
    """Every temporary entry has been re-checked at this version.

    A bound the recipe states and upstream does not is drift by default: swage
    reconciles it like any other difference, in either direction, and the
    change is visible as a bump line in the plan and in the pushed diff. What
    is *not* drift is something somebody wrote down in config, and this gate is
    about the half of that which must not outlive its reason.

    **Two shapes say it, because there are two things to say it about.**
    `temporary_constraints` tightens a bound on a dependency upstream declares.
    A temporary `add_requirements` entry carries a line upstream does not
    declare at all -- `airflow` dodging a bad `snowflake-connector-python`
    release that nothing it depends on names, which no override can express
    because there is no upstream bound to tighten. Either way swage keeps the
    line and asks again every time the version moves (DESIGN.md 3.3.14).

    **Asking no longer costs the update.** This is an advisory check
    (DESIGN.md 5.4): the recipe swage rendered is sound, so it is pushed and
    the question is put on the pull request. Under the previous rule a
    workaround nobody could retire blocked every other change to the feedstock,
    which made "swage asks again at the next bump" mean "swage never gets to
    the next bump".

    `constraints` and an ordinary `add_requirements` entry are the other halves
    and are silent here: both say the line is meant to hold, so re-asking would
    be asking about a decision already on the record.
    """
    if not plan.overrides and not plan.temporary_additions:
        return GateResult("G11", True)
    named = "; ".join(
        [
            f"{fenced(override.bound)} is a temporary constraint -- {override.reason}"
            for override in plan.overrides
        ]
        + [
            f"{fenced(addition.text)} is a temporary requirement -- {addition.reason}"
            for addition in plan.temporary_additions
        ]
    )
    return GateResult(
        "G11",
        False,
        f"{named}. Re-check whether it is still needed: drop the entry and let "
        "swage reconcile the line if it is not, or record it as permanent if "
        "the recipe is meant to keep it",
    )


def _g12(plan: RecipePlan, config: FeedstockConfig) -> GateResult:
    """swage changed no python test matrix -- while `test_matrix: review`.

    A proving period rather than a permanent rule (DESIGN.md 3.7), and the
    reason it exists is structural rather than about any one recipe. Every
    other edit swage makes is inside a requirements block, which is what made
    "only requirements changed" true by construction. This is the first one
    that is not, so while the behaviour is new a recipe swage completed the
    matrix of gets a human before it merges.

    What it guards is *not* whether the edit is right. CI decides that, and
    decides it well: adding the latest Python to the matrix makes the test run
    on that Python, so a green run is the change proving itself and a red one
    is a real incompatibility that was already shipping.
    """
    if not plan.test_matrices:
        return GateResult("G12", True)
    if config.test_matrix == "auto":
        return GateResult("G12", None, "the feedstock sets test_matrix: auto")
    return GateResult(
        "G12", False, "; ".join(matrix.reason for matrix in plan.test_matrices)
    )
