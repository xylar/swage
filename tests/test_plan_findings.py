"""The findings (DESIGN.md §9.7; design-v1.md 5.4, 11).

The highest-value tests in the suite, and every one of them is a test that a
check *finds* something it should find. A false negative here means an
unreviewed bad recipe merges automatically -- the one outcome the whole design
exists to prevent -- so acceptance is checked once per check and refusal is
checked for each way it can happen.

These are v1's gate tests, with their assertions untouched: `_Verdict` and
`_Gate` at the top present the findings the way `evaluate_gates` presented its
gates, which is what shows every sentence a check says is the one it said. The
tests of the rung and of the byte-identity check, which are not checks any
more, are in `test_plan_decision.py`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from swage.config import (
    AddedRequirement,
    ConfigTree,
    FeedstockConfig,
    Override,
    load_config,
)
from swage.mapping import Resolution
from swage.plan import (
    CHECKS,
    Finding,
    Plan,
    PlannedRequirement,
    PlannedSection,
    Provenance,
    SelfConflict,
    Unexplained,
    find,
    summarize,
    withheld,
)
from swage.plan.entry_points import EntryPointChange
from swage.plan.removals import Removal
from swage.plan.test_matrix import TestMatrix
from swage.run.budgets import FINDING_SAID, words
from swage.upstream import RecipeUpstream, parse_pyproject

from .conftest import WriteTree, plan_of

UPSTREAM = parse_pyproject(
    '[project]\nname = "demo"\nversion = "2.0.0"\n'
    'dependencies = ["requests >=2"]\n'
    '[project.optional-dependencies]\npandas = ["pandas >=1"]\ntests = ["pytest"]\n'
)

EXACT = Resolution("requests", "requests", "identity", exact=True)


def _tree(write_tree: WriteTree, feedstock: str = "") -> ConfigTree:
    files = {
        "defaults.yaml": (
            "trust: never\nrecipe_owned:\n  names: [python, pip]\n"
            "removals: review\ndynamic_dependencies: review\n"
        )
    }
    if feedstock:
        files["feedstocks/demo.yaml"] = feedstock
    return load_config(write_tree(files))


def _plan(**kwargs: object) -> Plan:
    defaults: dict[str, object] = {
        "sections": (
            PlannedSection(
                path="/requirements/run",
                section="run",
                entries=(
                    PlannedRequirement(
                        "requests >=2", Provenance("upstream-core", "upstream", EXACT)
                    ),
                ),
            ),
        )
    }
    defaults.update(kwargs)
    return plan_of(**defaults)  # type: ignore[arg-type]


@dataclass(frozen=True)
class _Gate:
    """One check as v1's `GateResult` presented it: a row, passed or failed."""

    name: str
    passed: bool
    detail: str = ""
    findings: tuple[str, ...] = ()

    @property
    def each(self) -> tuple[str, ...]:
        return self.findings or ((self.detail,) if self.detail else ())

    @property
    def said(self) -> str:
        return self.detail


@dataclass(frozen=True)
class _Verdict:
    """v1's `Verdict` over the findings, with the rung read as v1's G6 read it."""

    found: tuple[Finding, ...]
    trust: str

    @property
    def gates(self) -> tuple[_Gate, ...]:
        rows = []
        for row in CHECKS:
            here = tuple(f for f in self.found if f.kind == row.kind)
            rows.append(
                _Gate(row.v1, True)
                if not here
                else _Gate(row.v1, False, summarize(here), tuple(f.said for f in here))
            )
        return tuple(rows)

    @property
    def failures(self) -> tuple[_Gate, ...]:
        return tuple(gate for gate in self.gates if not gate.passed)

    @property
    def summary(self) -> str:
        return ", ".join(gate.name for gate in self.failures)

    @property
    def decision(self) -> str:
        clean = not self.found and self.trust == "auto"
        return "automerge" if clean else "needs-review"

    @property
    def withheld(self) -> tuple[Finding, ...]:
        return withheld(self.found)


def evaluate_gates(
    plan: Plan, config: FeedstockConfig, upstream: RecipeUpstream
) -> _Verdict:
    found = find(plan, config, upstream)
    # Every finding this module produces is held to DESIGN.md §3.2 here,
    # where it is rendered: the corpus plans cleanly and produces none.
    for finding in found:
        assert words(finding.said) <= FINDING_SAID, finding.said
    return _Verdict(found, config.trust)


def _gate(verdict: _Verdict, name: str) -> _Gate:
    return next(g for g in verdict.gates if g.name == name)


def test_a_blessed_feedstock_with_a_clean_plan_automerges(
    write_tree: WriteTree,
) -> None:
    """The one acceptance case; everything below is a refusal."""
    tree = _tree(write_tree, "feedstock: demo\ntrust: auto\n")
    verdict = evaluate_gates(
        _plan(), tree.for_feedstock("demo"), RecipeUpstream.of(UPSTREAM)
    )
    assert verdict.decision == "automerge"
    assert verdict.failures == ()


# --- each gate, refusing ---------------------------------------------------


def test_g1_blocks_an_unexplained_requirement(write_tree: WriteTree) -> None:
    plan = _plan(
        sections=(
            PlannedSection(
                path="/requirements/run",
                section="run",
                unexplained=(
                    Unexplained(
                        "nowhere", "leftpad >=1", "came from nowhere", "drop it"
                    ),
                ),
            ),
        )
    )
    tree = _tree(write_tree, "feedstock: demo\ntrust: auto\n")
    verdict = evaluate_gates(
        plan, tree.for_feedstock("demo"), RecipeUpstream.of(UPSTREAM)
    )
    assert verdict.decision == "needs-review"
    assert "G1" in verdict.summary


def test_g1_keeps_the_remedy_out_of_what_it_publishes(write_tree: WriteTree) -> None:
    """The findings go out on the feedstock; the remedy stays in swage's own output.

    A remedy names `add_requirements` and the rest, which are keys in a config
    file in swage's repository. `detail` carries both, because the terminal
    report, `swage explain` and `run.json` are read by somebody who can act on
    them; the comment on the feedstock's pull request renders the findings
    alone (CLAUDE.md).
    """
    plan = _plan(
        sections=(
            PlannedSection(
                path="/requirements/run",
                section="run",
                unexplained=(
                    Unexplained(
                        "nowhere",
                        "leftpad >=1",
                        "`leftpad >=1` in `/requirements/run` came from nowhere",
                        "declare it in add_requirements",
                    ),
                ),
            ),
        )
    )
    tree = _tree(write_tree, "feedstock: demo\ntrust: auto\n")

    gate = _gate(
        evaluate_gates(plan, tree.for_feedstock("demo"), RecipeUpstream.of(UPSTREAM)),
        "G1",
    )

    assert gate.each == ("`leftpad >=1` in `/requirements/run` came from nowhere",)
    assert "add_requirements" in gate.detail


def test_g2_blocks_an_unresolved_name(write_tree: WriteTree) -> None:
    plan = _plan(
        sections=(
            PlannedSection(
                path="/requirements/run",
                section="run",
                entries=(
                    PlannedRequirement(
                        "mystery >=1", Provenance("upstream-core", "upstream", None)
                    ),
                ),
            ),
        )
    )
    tree = _tree(write_tree, "feedstock: demo\ntrust: auto\n")
    verdict = evaluate_gates(
        plan, tree.for_feedstock("demo"), RecipeUpstream.of(UPSTREAM)
    )
    assert "G2" in verdict.summary
    detail = _gate(verdict, "G2").detail
    assert "no conda-forge package found" in detail


def test_g2_blocks_an_inexact_resolution(write_tree: WriteTree) -> None:
    """A guess swage did not notice is exactly what this gate exists for."""
    guessed = Resolution("Foo-Bar", "foo-bar", "grayskull", exact=False)
    plan = _plan(
        sections=(
            PlannedSection(
                path="/requirements/run",
                section="run",
                entries=(
                    PlannedRequirement(
                        "foo-bar >=1", Provenance("upstream-core", "upstream", guessed)
                    ),
                ),
            ),
        )
    )
    tree = _tree(write_tree, "feedstock: demo\ntrust: auto\n")
    assert (
        "G2"
        in evaluate_gates(
            plan, tree.for_feedstock("demo"), RecipeUpstream.of(UPSTREAM)
        ).summary
    )


def test_g2_ignores_structural_and_config_lines(write_tree: WriteTree) -> None:
    """Neither reaches the resolver, so neither can be an inexact resolution."""
    plan = _plan(
        sections=(
            PlannedSection(
                path="/requirements/run",
                section="run",
                entries=(
                    PlannedRequirement("python", Provenance("recipe-kept", "owned")),
                    PlannedRequirement(
                        "grpcio-gcp", Provenance("config-add", "c.yaml")
                    ),
                ),
            ),
        )
    )
    tree = _tree(write_tree, "feedstock: demo\ntrust: auto\n")
    verdict = evaluate_gates(
        plan, tree.for_feedstock("demo"), RecipeUpstream.of(UPSTREAM)
    )
    assert verdict.decision == "automerge"


def test_g3_blocks_an_extra_in_neither_list(write_tree: WriteTree) -> None:
    tree = _tree(
        write_tree,
        "feedstock: demo\ntrust: auto\nextras_as_outputs:\n"
        "  suffix: '{name}-with-{extra}'\n  supported: [pandas]\n  skip: [docs]\n",
    )
    verdict = evaluate_gates(
        _plan(), tree.for_feedstock("demo"), RecipeUpstream.of(UPSTREAM)
    )
    assert "G3" in verdict.summary
    assert "`tests`" in _gate(verdict, "G3").detail


def test_g3_is_not_satisfied_by_an_embedded_extras_name_collision(
    write_tree: WriteTree,
) -> None:
    """A dependency's name is not one of the project's extras (design-v1.md 5.4).

    `embedded_extras` is keyed on a *dependency* and an extra *of that
    dependency*; this gate asks about the extras of the project being packaged.
    The two namespaces are unrelated, and they collide in the fleet today:
    `apache-airflow-providers-amazon` declares upstream extras `aiobotocore`
    and `pandas`, while the family config carries `aiobotocore[boto3]` and
    `pandas[sql-other]` for unrelated reasons. Counting the part before the
    bracket let a name coincidence satisfy the gate, which is a gate disarmed.
    """
    tree = _tree(
        write_tree,
        "feedstock: demo\ntrust: auto\nextras_as_outputs:\n"
        "  suffix: '{name}-with-{extra}'\n  supported: [pandas]\n  skip: [docs]\n"
        # Keyed on a dependency called `tests`, which is also the name of one of
        # UPSTREAM's own extras -- and must not account for it.
        'embedded_extras:\n  "tests[foo]": []\n',
    )
    verdict = evaluate_gates(
        _plan(), tree.for_feedstock("demo"), RecipeUpstream.of(UPSTREAM)
    )
    assert "G3" in verdict.summary
    assert "`tests`" in _gate(verdict, "G3").detail


def test_g3_does_not_apply_without_a_skip_list(write_tree: WriteTree) -> None:
    """Exhaustiveness is opt-in; a new extra is reported, not gated."""
    tree = _tree(
        write_tree,
        "feedstock: demo\ntrust: auto\nextras_as_outputs:\n"
        "  suffix: '{name}-with-{extra}'\n  supported: [pandas]\n",
    )
    verdict = evaluate_gates(
        _plan(), tree.for_feedstock("demo"), RecipeUpstream.of(UPSTREAM)
    )
    # Not asked, so nothing found: "not asked" is no longer a state of its own.
    assert _gate(verdict, "G3").passed is True
    assert verdict.decision == "automerge"


def test_g4_blocks_an_output_whose_extra_disappeared(write_tree: WriteTree) -> None:
    """The orphaned output of design-v1.md 3.3.11, with both halves of the fix."""
    tree = _tree(
        write_tree,
        "feedstock: demo\ntrust: auto\nextras_as_outputs:\n"
        "  suffix: '{name}-with-{extra}'\n  supported: [gone]\n"
        "  skip: [pandas, tests]\n",
    )
    verdict = evaluate_gates(
        _plan(), tree.for_feedstock("demo"), RecipeUpstream.of(UPSTREAM)
    )
    assert "G4" in verdict.summary
    detail = _gate(verdict, "G4").detail
    assert "delete the output" in detail
    assert "extras_as_outputs.supported" in detail


def test_g8_blocks_a_removal_while_removals_is_review(write_tree: WriteTree) -> None:
    plan = _plan(
        sections=(
            PlannedSection(
                path="/requirements/run",
                section="run",
                removals=(
                    Removal("upstream-dropped", "six >=1.16", "dropped", "2.0.0"),
                ),
            ),
        )
    )
    tree = _tree(write_tree, "feedstock: demo\ntrust: auto\n")
    verdict = evaluate_gates(
        plan, tree.for_feedstock("demo"), RecipeUpstream.of(UPSTREAM)
    )
    assert "G8" in verdict.summary
    assert "2.0.0" in _gate(verdict, "G8").detail


def test_g8_does_not_hold_a_removal_config_already_explained(
    write_tree: WriteTree,
) -> None:
    """A `retire` entry is the decision; G8 asking again never ends.

    `retire` is only reached once upstream has been asked and had nothing to
    say about the name in any version or under any extra, so the maintainer
    has already written the answer down. Holding it anyway held 36 of the
    fleet's feedstocks on one config line -- 38 of the 50 in the google-cloud
    family carry the same retired grayskull artifact (design-v1.md 3.3.8).
    """
    plan = _plan(
        sections=(
            PlannedSection(
                path="/requirements/run",
                section="run",
                removals=(Removal("retired", "google-api-core >=2.17.1", "retired"),),
            ),
        )
    )
    tree = _tree(write_tree, "feedstock: demo\ntrust: auto\n")
    verdict = evaluate_gates(
        plan, tree.for_feedstock("demo"), RecipeUpstream.of(UPSTREAM)
    )
    assert _gate(verdict, "G8").passed is True
    assert verdict.decision == "automerge"


def test_a_retired_removal_is_still_a_removal_everywhere_else() -> None:
    """G8 stops asking about it; the report still says the line is going."""
    plan = _plan(
        sections=(
            PlannedSection(
                path="/requirements/run",
                section="run",
                removals=(
                    Removal("retired", "google-api-core >=2.17.1", "retired"),
                    Removal("upstream-dropped", "six >=1.16", "dropped", "2.0.0"),
                ),
            ),
        )
    )
    assert [r.text for r in plan.dropped] == [
        "google-api-core >=2.17.1",
        "six >=1.16",
    ]
    assert [r.text for r in plan.inferred_removals] == ["six >=1.16"]


def test_g8_still_holds_a_removal_swage_inferred(write_tree: WriteTree) -> None:
    """The proving period is about swage's own reading of two releases."""
    plan = _plan(
        sections=(
            PlannedSection(
                path="/requirements/run",
                section="run",
                removals=(
                    Removal("retired", "google-api-core >=2.17.1", "retired"),
                    Removal("upstream-dropped", "six >=1.16", "dropped", "2.0.0"),
                ),
            ),
        )
    )
    tree = _tree(write_tree, "feedstock: demo\ntrust: auto\n")
    verdict = evaluate_gates(
        plan, tree.for_feedstock("demo"), RecipeUpstream.of(UPSTREAM)
    )
    detail = _gate(verdict, "G8").detail
    assert _gate(verdict, "G8").passed is False
    assert "six" in detail
    assert "google-api-core" not in detail


def test_g8_holds_a_removal_swage_read_off_the_build_floor(
    write_tree: WriteTree,
) -> None:
    """An out-of-range removal is swage's own reading too, and gates the same.

    Its evidence is stronger than an upstream-dropped one's -- the marker and
    the floor are both in hand, with nothing inferred from an absence -- but
    the proving period is about swage having a track record, which it has no
    more of here than there.
    """
    plan = _plan(
        sections=(
            PlannedSection(
                path="/requirements/run",
                section="run",
                removals=(
                    Removal(
                        "out-of-range",
                        "tomli >=2.0.1,<3.0.0",
                        "upstream declares 'tomli' only for python <3.11, and "
                        "this recipe is built for python >=3.11, so no package "
                        "it builds installs it",
                    ),
                ),
            ),
        )
    )
    tree = _tree(write_tree, "feedstock: demo\ntrust: auto\n")
    verdict = evaluate_gates(
        plan, tree.for_feedstock("demo"), RecipeUpstream.of(UPSTREAM)
    )
    detail = _gate(verdict, "G8").detail
    assert _gate(verdict, "G8").passed is False
    # The diff shows a requirement upstream still declares being deleted, so
    # the detail has to carry why -- there is no version number to point at.
    assert "python <3.11" in detail
    assert "python >=3.11" in detail


def test_g8_does_not_apply_under_removals_auto(write_tree: WriteTree) -> None:
    """The proving period ends in a config commit, not a code change."""
    plan = _plan(
        sections=(
            PlannedSection(
                path="/requirements/run",
                section="run",
                removals=(Removal("upstream-dropped", "six >=1.16", "dropped"),),
            ),
        )
    )
    tree = _tree(write_tree, "feedstock: demo\ntrust: auto\nremovals: auto\n")
    verdict = evaluate_gates(
        plan, tree.for_feedstock("demo"), RecipeUpstream.of(UPSTREAM)
    )
    # Not asked, so nothing found: "not asked" is no longer a state of its own.
    assert _gate(verdict, "G8").passed is True
    assert verdict.decision == "automerge"


def test_g8_ignores_a_line_that_was_kept(write_tree: WriteTree) -> None:
    """Only an actual drop is a removal; keeping is what the other fates mean."""
    plan = _plan(
        sections=(
            PlannedSection(
                path="/requirements/run",
                section="run",
                removals=(Removal("never-upstream", "grpcio-gcp", "kept"),),
            ),
        )
    )
    tree = _tree(write_tree, "feedstock: demo\ntrust: auto\n")
    verdict = evaluate_gates(
        plan, tree.for_feedstock("demo"), RecipeUpstream.of(UPSTREAM)
    )
    assert verdict.decision == "automerge"


def test_g10_blocks_a_computed_dependency_list(write_tree: WriteTree) -> None:
    upstream = parse_pyproject('[project]\nname = "demo"\n')
    dynamic = type(upstream)(
        name=upstream.name, dynamic_fields=frozenset({"requires-dist"})
    )
    tree = _tree(write_tree, "feedstock: demo\ntrust: auto\n")
    verdict = evaluate_gates(
        _plan(), tree.for_feedstock("demo"), RecipeUpstream.of(dynamic)
    )
    assert "G10" in verdict.summary
    assert "dynamic_dependencies: auto" in _gate(verdict, "G10").detail


def test_g10_does_not_apply_when_the_feedstock_trusts_it(write_tree: WriteTree) -> None:
    upstream = parse_pyproject('[project]\nname = "demo"\n')
    dynamic = type(upstream)(
        name=upstream.name, dynamic_fields=frozenset({"requires-dist"})
    )
    tree = _tree(
        write_tree, "feedstock: demo\ntrust: auto\ndynamic_dependencies: auto\n"
    )
    verdict = evaluate_gates(
        _plan(), tree.for_feedstock("demo"), RecipeUpstream.of(dynamic)
    )
    # Not asked, so nothing found: "not asked" is no longer a state of its own.
    assert _gate(verdict, "G10").passed is True


def test_an_unrelated_dynamic_field_does_not_block(write_tree: WriteTree) -> None:
    """`Dynamic: license-file` says nothing about the dependency list."""
    upstream = parse_pyproject('[project]\nname = "demo"\n')
    dynamic = type(upstream)(
        name=upstream.name, dynamic_fields=frozenset({"license-file"})
    )
    tree = _tree(write_tree, "feedstock: demo\ntrust: auto\n")
    assert evaluate_gates(
        _plan(), tree.for_feedstock("demo"), RecipeUpstream.of(dynamic)
    ).decision == ("automerge")


# --- the verdict itself ----------------------------------------------------


def test_every_failing_gate_is_named_not_just_the_first(write_tree: WriteTree) -> None:
    """A report that stops at the first failure costs a second round trip."""
    plan = _plan(
        sections=(
            PlannedSection(
                path="/requirements/run",
                section="run",
                unexplained=(Unexplained("nowhere", "leftpad", "nowhere", "drop it"),),
                removals=(Removal("upstream-dropped", "six", "dropped"),),
            ),
        ),
    )
    tree = _tree(write_tree, "feedstock: demo\ntrust: propose\n")
    verdict = evaluate_gates(
        plan, tree.for_feedstock("demo"), RecipeUpstream.of(UPSTREAM)
    )
    assert {gate.name for gate in verdict.failures} == {"G1", "G8"}


def test_g12_holds_a_recipe_whose_test_matrix_swage_completed(
    write_tree: WriteTree,
) -> None:
    """The first edit outside a requirements block gets a proving period.

    What it guards is not whether the edit is right -- CI decides that, and
    decides it well. It guards the fact that "only requirements changed"
    stopped being true by construction.
    """
    tree = _tree(write_tree, "feedstock: demo\ntrust: auto\n")
    plan = replace(
        _plan(),
        test_matrices=(
            TestMatrix(
                path="/tests/0/python",
                was=("${{ python_min }}.*",),
                versions=("${{ python_min }}.*", "*"),
            ),
        ),
    )

    verdict = evaluate_gates(
        plan, tree.for_feedstock("demo"), RecipeUpstream.of(UPSTREAM)
    )

    assert _gate(verdict, "G12").passed is False
    assert verdict.decision == "needs-review"


def test_g12_does_not_apply_once_a_feedstock_opts_out(write_tree: WriteTree) -> None:
    """Promotion is one commit, exactly as `removals` and dynamic lists are."""
    tree = _tree(write_tree, "feedstock: demo\ntrust: auto\ntest_matrix: auto\n")
    plan = replace(
        _plan(),
        test_matrices=(TestMatrix(path="/tests/0/python", was=(), versions=("*",)),),
    )

    verdict = evaluate_gates(
        plan, tree.for_feedstock("demo"), RecipeUpstream.of(UPSTREAM)
    )

    # Not asked, so nothing found: "not asked" is no longer a state of its own.
    assert _gate(verdict, "G12").passed is True
    assert verdict.decision == "automerge"


def test_g15_holds_a_recipe_whose_entry_point_swage_would_drop(
    write_tree: WriteTree,
) -> None:
    """A command somebody has installed going away gets one look.

    `cartopy` is the case: the recipe lists `feature_download` and upstream
    now declares `cartopy_feature_download`, which is a rename to swage and
    a command disappearing to a user. Held once -- after the push the recipe
    says what upstream says and the next run has nothing to hold.
    """
    tree = _tree(write_tree, "feedstock: demo\ntrust: auto\n")
    plan = replace(
        _plan(),
        entry_points=(
            EntryPointChange(
                path="/build/python/entry_points",
                items=("cartopy_feature_download = cartopy.feature.download:main",),
                added=("cartopy_feature_download = cartopy.feature.download:main",),
                dropped=("feature_download = tools.download:main",),
            ),
        ),
    )

    verdict = evaluate_gates(
        plan, tree.for_feedstock("demo"), RecipeUpstream.of(UPSTREAM)
    )

    gate = _gate(verdict, "G15")
    assert gate.passed is False
    assert "`feature_download = tools.download:main`" in gate.detail
    assert verdict.decision == "needs-review"


def test_g15_lets_a_retarget_or_an_addition_through(write_tree: WriteTree) -> None:
    """Upstream's own declaration, like a dependency bound; CI runs the command."""
    tree = _tree(write_tree, "feedstock: demo\ntrust: auto\n")
    plan = replace(
        _plan(),
        entry_points=(
            EntryPointChange(
                path="/build/python/entry_points",
                items=("m2r2 = m2r2.cli.m2r2:main", "m2r2-gui = m2r2.gui:main"),
                retargeted=(("m2r2", "m2r2:main", "m2r2.cli.m2r2:main"),),
                added=("m2r2-gui = m2r2.gui:main",),
            ),
        ),
    )

    verdict = evaluate_gates(
        plan, tree.for_feedstock("demo"), RecipeUpstream.of(UPSTREAM)
    )

    assert _gate(verdict, "G15").passed is True
    assert verdict.decision == "automerge"


def test_g3_can_be_opted_into_by_a_folded_output(write_tree: WriteTree) -> None:
    """The `outputs[].run` shape had nowhere to record a declined extra.

    `skip` lived only under `extras_as_outputs`, which is the other shape, so
    a feedstock folding extras into an existing output -- the whole google-cloud
    family -- could never opt into exhaustiveness. It had no way to say "I mean
    to account for all of these", so G3 was permanently unavailable to it.
    """
    tree = _tree(
        write_tree,
        "feedstock: demo\ntrust: auto\noutputs:\n  demo:\n    run:\n"
        "      core: true\n      extras: [pandas]\n      skip: [docs]\n",
    )
    verdict = evaluate_gates(
        _plan(), tree.for_feedstock("demo"), RecipeUpstream.of(UPSTREAM)
    )

    gate = _gate(verdict, "G3")
    assert gate.passed is False
    assert "`tests`" in gate.detail


def test_g3_passes_once_a_folded_output_accounts_for_everything(
    write_tree: WriteTree,
) -> None:
    tree = _tree(
        write_tree,
        "feedstock: demo\ntrust: auto\noutputs:\n  demo:\n    run:\n"
        "      core: true\n      extras: [pandas]\n      skip: [docs, tests]\n",
    )
    verdict = evaluate_gates(
        _plan(), tree.for_feedstock("demo"), RecipeUpstream.of(UPSTREAM)
    )

    assert _gate(verdict, "G3").passed is True
    assert verdict.decision == "automerge"


# --- G11: a temporary constraint, re-checked -------------------------------


def test_g11_asks_again_about_a_temporary_constraint(write_tree: WriteTree) -> None:
    """A workaround must not become permanent by nobody looking.

    `apache-airflow-providers-google` is the fleet's case: its recipe pins
    `apache-airflow >=2.11.0,<3.1.3` under a comment saying the ceiling keeps
    the solver happy, where upstream declares only `>=2.11.0`. Recorded as
    temporary, swage keeps the ceiling and holds the feedstock at every
    version bump, which is when somebody can tell whether it is still needed.
    """
    tree = _tree(write_tree, "feedstock: demo\ntrust: auto\n")
    plan = _plan(
        sections=(
            PlannedSection(
                path="/requirements/run",
                section="run",
                overrides=(
                    Override(bound="<3.1.3", reason="airflow 3.1.3 breaks the solver"),
                ),
            ),
        )
    )
    verdict = evaluate_gates(
        plan, tree.for_feedstock("demo"), RecipeUpstream.of(UPSTREAM)
    )

    gate = _gate(verdict, "G11")
    assert gate.passed is False
    assert "airflow 3.1.3 breaks the solver" in gate.detail


def test_g11_asks_again_about_an_overruling_bound(write_tree: WriteTree) -> None:
    """Overruling upstream must not mean swage stopped reading it.

    `apache-beam`'s gcp metapackage states `google-apitools >=0.5.35` where
    upstream asks for `<0.5.32` below python 3.13. That line is right only for
    as long as upstream keeps disagreeing with itself in the same terms, and a
    new version is exactly when that stops being true -- so the entry comes
    back at every update rather than settling the question for good.
    """
    tree = _tree(write_tree, "feedstock: demo\ntrust: auto\n")
    plan = _plan(
        sections=(
            PlannedSection(
                path="/requirements/run",
                section="run",
                overruled=(
                    Override(
                        bound=">=0.5.35",
                        reason="upstream caps it below 3.13 for its own test suites",
                    ),
                ),
            ),
        )
    )
    verdict = evaluate_gates(
        plan, tree.for_feedstock("demo"), RecipeUpstream.of(UPSTREAM)
    )

    gate = _gate(verdict, "G11")
    assert gate.passed is False
    assert "overrules upstream's conflicting bounds" in gate.detail
    assert "for its own test suites" in gate.detail


def test_g11_says_nothing_about_a_permanent_one(write_tree: WriteTree) -> None:
    """`constraints:` is a decision already on the record."""
    tree = _tree(write_tree, "feedstock: demo\ntrust: auto\n")
    verdict = evaluate_gates(
        _plan(), tree.for_feedstock("demo"), RecipeUpstream.of(UPSTREAM)
    )
    gate = _gate(verdict, "G11")
    assert gate.passed is True


def test_g11_asks_again_about_a_temporary_requirement(write_tree: WriteTree) -> None:
    """The half no override can express.

    `airflow` carries `snowflake-connector-python !=4.4.0` to dodge a release
    still on the channel. Nothing the recipe depends on declares that package
    -- it is a dependency of a dependency -- so there is no upstream bound for
    a `temporary_constraints` entry to tighten, and an ordinary
    `add_requirements` entry would say the recipe means to keep it forever.
    """
    tree = _tree(write_tree, "feedstock: demo\ntrust: auto\n")
    plan = _plan(
        sections=(
            PlannedSection(
                path="/requirements/run",
                section="run",
                temporary_additions=(
                    AddedRequirement(
                        text="snowflake-connector-python !=4.4.0",
                        source="config/feedstocks/demo.yaml",
                        reason="4.4.0 on conda-forge is broken",
                        temporary=True,
                    ),
                ),
            ),
        )
    )
    verdict = evaluate_gates(
        plan, tree.for_feedstock("demo"), RecipeUpstream.of(UPSTREAM)
    )

    gate = _gate(verdict, "G11")
    assert gate.passed is False
    assert "4.4.0 on conda-forge is broken" in gate.detail


def test_g11_does_not_withhold_the_push(write_tree: WriteTree) -> None:
    """Asking again must not cost the update (design-v1.md 5.4).

    The whole point of recording a workaround rather than deleting it is that
    swage re-asks at the next version bump. While a failing check meant nothing
    was pushed, a workaround nobody could retire kept the feedstock from ever
    reaching one.
    """
    tree = _tree(write_tree, "feedstock: demo\ntrust: auto\n")
    plan = _plan(
        sections=(
            PlannedSection(
                path="/requirements/run",
                section="run",
                temporary_additions=(
                    AddedRequirement(
                        text="pyexasol !=1.1.1,!=2.0.0",
                        source="config/feedstocks/demo.yaml",
                        reason="both releases resolve to a broken pyexasol",
                        temporary=True,
                    ),
                ),
            ),
        )
    )
    verdict = evaluate_gates(
        plan, tree.for_feedstock("demo"), RecipeUpstream.of(UPSTREAM)
    )

    assert _gate(verdict, "G11").passed is False
    assert verdict.withheld == ()


def test_a_host_change_on_a_cross_compiled_output_is_held(
    write_tree: WriteTree,
) -> None:
    """15 of the fleet's 19 cross-compilation blocks repeat a host requirement.

    Which ones belong there is a judgment per dependency -- `pyproj` mirrors
    `cython` and not `proj` -- so swage writes the host change and leaves the
    mirroring to a human, which means not merging it unattended.

    **Held from the label, not from the push.** The judgment is about a section
    swage does not write, and nobody can make it without the diff -- so
    withholding the push asked for a review and denied the reviewer the thing
    to review. What swage wrote is sound either way: `host` is reconciled
    against upstream, and every copy the block already holds moved with it.
    """
    plan = _plan(cross_compiled=("/requirements/host",))
    verdict = evaluate_gates(
        plan, _tree(write_tree).for_feedstock("demo"), RecipeUpstream.of(UPSTREAM)
    )
    failed = {gate.name: gate for gate in verdict.failures}
    assert "G13" in failed
    assert "build section" in failed["G13"].detail
    assert verdict.withheld == ()


def test_an_output_that_does_not_cross_compile_passes(write_tree: WriteTree) -> None:
    verdict = evaluate_gates(
        _plan(), _tree(write_tree).for_feedstock("demo"), RecipeUpstream.of(UPSTREAM)
    )
    assert {gate.name: gate.passed for gate in verdict.gates}["G13"] is True


# --- what the gates say, once GitHub has rendered it ------------------------


def test_no_gate_detail_can_be_eaten_by_markdown(write_tree: WriteTree) -> None:
    """Every failing detail is published verbatim, and GitHub renders it.

    This shipped. The test-matrix detail named `${{ python_min }}.*` and the
    `"*"` it adds, which put two bare asterisks in one line of a comment on
    https://github.com/conda-forge/weaviate-client-feedstock/pull/38 -- GitHub
    paired them into emphasis, consumed both, and published `swage added ""`.

    Asserted over every gate at once rather than per gate, because the defect
    is not in any one sentence: it is in interpolating recipe text into a
    markup language, which every one of these details does. A new gate that
    quotes a requirement is the next place it happens, and it should fail here
    on the day it is written rather than on a real pull request.

    Only `*` is checked, which is the empirical answer rather than the
    intuitive one. `_` looks equally dangerous and is not: CommonMark forbids
    intraword emphasis for `_`, so `ruamel_yaml`, `name_map` and
    `embedded_extras` all survive unfenced -- confirmed against GitHub's own
    renderer, which is the only authority that counts here.
    """
    # Every token a gate quotes, carrying the character that breaks. A real
    # `python 3.10.*` is where this comes from; the rest are shaped to match.
    tree = _tree(write_tree, "feedstock: demo\ntrust: propose\nremovals: review\n")
    plan = _plan(
        sections=(
            PlannedSection(
                path="/requirements/run",
                section="run",
                entries=(
                    PlannedRequirement(
                        "mystery 1.*", Provenance("upstream-core", "upstream", None)
                    ),
                ),
                # Already fenced, because G1 passes this reason through
                # verbatim -- it is `attribute` that builds it, and
                # `test_plan_attribute` holds it to the same rule.
                unexplained=(
                    Unexplained(
                        "nowhere", "leftpad 1.*", "`leftpad 1.*` came from", "drop it"
                    ),
                ),
                overrides=(Override(bound="<2", reason="numpy 2 breaks it"),),
                removals=(
                    Removal("upstream-dropped", "python 3.10.*", "gone upstream"),
                ),
            ),
        ),
        test_matrices=(TestMatrix("/tests/0/python", ("3.10.*",), ("3.10.*", "*")),),
        cross_compiled=("/requirements/host",),
    )
    verdict = evaluate_gates(
        plan, tree.for_feedstock("demo"), RecipeUpstream.of(UPSTREAM)
    )

    failures = {gate.name: gate.detail for gate in verdict.failures}
    # The gates that quote recipe text are the ones this is about, so the test
    # is worthless if they did not fire.
    assert {"G1", "G2", "G8", "G11", "G12", "G13"} <= set(failures)
    for name, detail in failures.items():
        outside_code = re.sub(r"`[^`]*`", "", detail)
        assert "*" not in outside_code, f"{name} publishes a bare asterisk: {detail}"


# --- G14: a split recipe that disagrees with itself --------------------------


def test_g14_holds_a_recipe_requiring_a_version_it_does_not_build(
    write_tree: WriteTree,
) -> None:
    """`airflow` built task-sdk 1.3.0 while its core output required ==1.3.1.

    Each line is individually right -- upstream really does say `==1.3.1` --
    so nothing in the diff shows the two disagreeing. The fix is in `context`,
    which swage does not write.
    """
    plan = plan_of(
        self_conflicts=(
            SelfConflict(
                output="apache-airflow-core",
                package="apache-airflow-task-sdk",
                constraint="==1.3.1",
                built="1.3.0",
            ),
        )
    )
    tree = _tree(write_tree, "feedstock: demo\ntrust: auto\n")
    verdict = evaluate_gates(
        plan, tree.for_feedstock("demo"), RecipeUpstream.of(UPSTREAM)
    )
    assert "G14" in verdict.summary
    detail = next(g.detail for g in verdict.failures if g.name == "G14")
    assert "apache-airflow-task-sdk 1.3.0" in detail


def test_g14_passes_a_recipe_that_agrees_with_itself(write_tree: WriteTree) -> None:
    tree = _tree(write_tree, "feedstock: demo\ntrust: auto\n")
    verdict = evaluate_gates(
        _plan(), tree.for_feedstock("demo"), RecipeUpstream.of(UPSTREAM)
    )
    assert "G14" not in verdict.summary


# --- what a check found, kept apart from how it reads on one line ----------


def test_a_check_that_found_two_things_keeps_them_apart(
    write_tree: WriteTree,
) -> None:
    """`detail` is one line; `each` is what the pull request comment bullets.

    Joining them made one bullet holding two findings, a `; ` between them and
    a doubled period where the first ended in one -- published under the
    maintainer's name on a repository they do not own (design-v1.md 5.4).
    """
    tree = _tree(write_tree, "feedstock: demo\ntrust: auto\n")
    plan = _plan(
        sections=(
            PlannedSection(
                path="/requirements/run",
                section="run",
                temporary_additions=(
                    AddedRequirement(
                        text="snowflake-connector-python !=4.4.0",
                        source="config/feedstocks/demo.yaml",
                        reason="4.4.0 on conda-forge is broken.",
                        temporary=True,
                    ),
                    AddedRequirement(
                        text="pyexasol !=1.1.1",
                        source="config/feedstocks/demo.yaml",
                        reason="1.1.1 on conda-forge is broken.",
                        temporary=True,
                    ),
                ),
            ),
        )
    )
    gate = _gate(
        evaluate_gates(plan, tree.for_feedstock("demo"), RecipeUpstream.of(UPSTREAM)),
        "G11",
    )

    assert len(gate.each) == 2
    assert all("Re-check whether" not in finding for finding in gate.each)
    # The advice is said once, and only where swage's own config keys belong.
    assert gate.detail.count("Re-check whether") == 1


def test_advice_does_not_double_a_period(write_tree: WriteTree) -> None:
    """A `reason` is a sentence somebody wrote, and usually ends in one.

    `swage explain` and the terminal report read the joined form, so appending
    the advice with its own period printed `repodata-patched.. Re-check`.
    The pull request comment never showed it -- it renders the findings apart
    -- which is how it survived being fixed there.
    """
    tree = _tree(write_tree, "feedstock: demo\ntrust: auto\n")
    plan = _plan(
        sections=(
            PlannedSection(
                path="/requirements/run",
                section="run",
                temporary_additions=(
                    AddedRequirement(
                        text="pyexasol !=1.1.1",
                        source="config/feedstocks/demo.yaml",
                        reason="1.1.1 on conda-forge is broken.",
                        temporary=True,
                    ),
                ),
            ),
        )
    )
    gate = _gate(
        evaluate_gates(plan, tree.for_feedstock("demo"), RecipeUpstream.of(UPSTREAM)),
        "G11",
    )
    assert ".." not in gate.detail
    assert "broken. Re-check" in gate.detail


def test_a_check_that_found_one_thing_still_has_it(write_tree: WriteTree) -> None:
    """`each` is every failing check's findings, however many there are.

    The finding is the `said` half alone; the config key that answers it is
    the remedy, which the terminal joins on and a comment leaves out
    (DESIGN.md §3.1).
    """
    upstream = parse_pyproject('[project]\nname = "demo"\n')
    dynamic = type(upstream)(
        name=upstream.name, dynamic_fields=frozenset({"requires-dist"})
    )
    tree = _tree(write_tree, "feedstock: demo\ntrust: auto\n")
    verdict = evaluate_gates(
        _plan(), tree.for_feedstock("demo"), RecipeUpstream.of(dynamic)
    )
    (finding,) = verdict.found
    assert "dynamic_dependencies" not in finding.said
    assert "dynamic_dependencies: auto" in finding.remedy
    gate = _gate(verdict, "G10")
    assert gate.each == (finding.said,)
    assert gate.detail == f"{finding.said} -- {finding.remedy}"


def test_every_check_says_something_when_it_fails() -> None:
    """A report prints the negative for a failure, so every check owes one.

    Pinned rather than derived, because deriving it would mean negating a
    sentence mechanically -- "not every requirement is accounted for" -- which
    is how the wording got into trouble in the first place. Each of these is
    written to be read on its own.
    """
    for row in CHECKS:
        assert row.failure and row.failure != row.title, row.kind
        assert not row.failure.startswith("not "), row.kind
