"""The trust gates (DESIGN.md 5.4).

A feedstock's pull request gets the `automerge` label only if **all** of these
hold. Fail any one and the work is *still* pushed -- it is not thrown away --
but swage applies no label, comments on the pull request saying which checks
failed, and lists the feedstock in the report's NEEDS REVIEW section.

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
from swage.upstream import UpstreamMetadata

from .assemble import RecipePlan, accounted_extras, declares_skip

__all__ = ["TITLES", "Decision", "GateResult", "Verdict", "evaluate_gates"]

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
    "G11": "no version bound is dropped that upstream never asked for",
}

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
    def summary(self) -> str:
        """The gates that blocked, named, for the report's one-line verdict."""
        return ", ".join(gate.name for gate in self.failures)


def evaluate_gates(
    plan: RecipePlan,
    config: FeedstockConfig,
    upstream: UpstreamMetadata,
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
        )
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
        for requirement in section.requirements:
            provenance = requirement.provenance
            if provenance.origin in {"recipe-kept", "config-add"}:
                # Neither reaches the resolver: one is structure, the other is
                # a conda name a human wrote down (DESIGN.md 3.3.6).
                continue
            if provenance.mapping is None:
                inexact.append(f"no conda-forge package found for {requirement.name!r}")
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
                named = ", ".join(repr(extra) for extra in mapping.dropped_extras)
                inexact.append(
                    f"{mapping.pypi_name!r} resolved to {mapping.conda_name!r}, "
                    f"dropping extra {named} -- map the requirement in name_map "
                    "if conda-forge has a package for it, or write out what it "
                    "pulls in under embedded_extras"
                )
            elif not provenance.mapping.exact:
                inexact.append(
                    f"{provenance.mapping.pypi_name!r} was matched to "
                    f"{provenance.mapping.conda_name!r} by guesswork rather "
                    "than by a lookup"
                )
    if not inexact:
        return GateResult("G2", True)
    return GateResult("G2", False, "; ".join(sorted(set(inexact))))


def _g3(config: FeedstockConfig, upstream: UpstreamMetadata) -> GateResult:
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
    named = ", ".join(repr(extra) for extra in missing)
    return GateResult(
        "G3",
        False,
        f"upstream extra {named} is in neither supported nor skip; "
        "add it to one so the decision is on the record",
    )


def _g4(
    config: FeedstockConfig,
    upstream: UpstreamMetadata,
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
    named = ", ".join(repr(extra) for extra in orphaned)
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
    """Blessing is explicit and opt-in."""
    if config.trust == "auto":
        return GateResult("G6", True)
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
    dropped = plan.dropped
    if not dropped:
        return GateResult("G8", True)
    named = "; ".join(
        f"{removal.text!r}"
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
    plan: RecipePlan, config: FeedstockConfig, upstream: UpstreamMetadata
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
    named = ", ".join(dynamic)
    return GateResult(
        "G10",
        False,
        f"upstream computed {named} at build time rather than declaring it, "
        "so another build may produce a different list -- proofread, or set "
        "dynamic_dependencies: trust for this feedstock",
    )


def _g11(plan: RecipePlan) -> GateResult:
    """No bound is dropped that upstream never asked for.

    G1 asks whether a *line* is justified and never looks at its constraint,
    so a bound a maintainer added by hand -- `apache-airflow >=2.11.0,<3.1.3`
    against an upstream that says `>=2.11.0` -- disappeared with every gate
    satisfied. Same rule as DESIGN.md 3.3.7's for a whole line, one level
    down: what swage cannot attribute, it does not remove on its own
    (DESIGN.md 3.3.14).
    """
    if not plan.tightened:
        return GateResult("G11", True)
    return GateResult("G11", False, "; ".join(i.reason for i in plan.tightened))
