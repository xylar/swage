"""Trust-gate tests (DESIGN.md 5.4, 11).

The highest-value tests in the suite, and every one of them is a test that a
gate *blocks* something it should block. A false negative here means an
unreviewed bad recipe merges automatically -- the one outcome the whole design
exists to prevent -- so acceptance is checked once per gate and refusal is
checked for each way it can happen.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from swage.config import ConfigTree, load_config
from swage.mapping import Resolution
from swage.plan import (
    PlannedRequirement,
    PlannedSection,
    Provenance,
    RecipePlan,
    Unexplained,
    evaluate_gates,
)
from swage.plan.constrained import UnassociatedConstraint
from swage.plan.removals import Removal
from swage.plan.test_matrix import TestMatrix
from swage.plan.tightening import Tightened
from swage.upstream import parse_pyproject

from .conftest import WriteTree

UPSTREAM = parse_pyproject(
    '[project]\nname = "demo"\nversion = "2.0.0"\n'
    'dependencies = ["requests >=2"]\n'
    '[project.optional-dependencies]\npandas = ["pandas >=1"]\ntests = ["pytest"]\n'
)

EXACT = Resolution("requests", "requests", "identity", exact=True)


def _tree(write_tree: WriteTree, feedstock: str = "") -> ConfigTree:
    files = {
        "defaults.yaml": (
            "trust: manual\nrecipe_owned:\n  names: [python, pip]\n"
            "removals: review\ndynamic_dependencies: review\n"
        )
    }
    if feedstock:
        files["feedstocks/demo.yaml"] = feedstock
    return load_config(write_tree(files))


def _plan(**kwargs: object) -> RecipePlan:
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
    return RecipePlan(**defaults)  # type: ignore[arg-type]


def _gate(verdict: object, name: str) -> object:
    return next(g for g in verdict.gates if g.name == name)  # type: ignore[attr-defined]


def test_a_blessed_feedstock_with_a_clean_plan_automerges(
    write_tree: WriteTree,
) -> None:
    """The one acceptance case; everything below is a refusal."""
    tree = _tree(write_tree, "feedstock: demo\ntrust: auto\n")
    verdict = evaluate_gates(_plan(), tree.for_feedstock("demo"), UPSTREAM)
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
                    Unexplained("nowhere", "leftpad >=1", "came from nowhere"),
                ),
            ),
        )
    )
    tree = _tree(write_tree, "feedstock: demo\ntrust: auto\n")
    verdict = evaluate_gates(plan, tree.for_feedstock("demo"), UPSTREAM)
    assert verdict.decision == "needs-review"
    assert "G1" in verdict.summary


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
    verdict = evaluate_gates(plan, tree.for_feedstock("demo"), UPSTREAM)
    assert "G2" in verdict.summary
    detail = _gate(verdict, "G2").detail  # type: ignore[attr-defined]
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
    assert "G2" in evaluate_gates(plan, tree.for_feedstock("demo"), UPSTREAM).summary


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
    verdict = evaluate_gates(plan, tree.for_feedstock("demo"), UPSTREAM)
    assert verdict.decision == "automerge"


def test_g3_blocks_an_extra_in_neither_list(write_tree: WriteTree) -> None:
    tree = _tree(
        write_tree,
        "feedstock: demo\ntrust: auto\nextras_as_outputs:\n"
        "  suffix: '{name}-with-{extra}'\n  supported: [pandas]\n  skip: [docs]\n",
    )
    verdict = evaluate_gates(_plan(), tree.for_feedstock("demo"), UPSTREAM)
    assert "G3" in verdict.summary
    assert "'tests'" in _gate(verdict, "G3").detail  # type: ignore[attr-defined]


def test_g3_is_not_satisfied_by_an_embedded_extras_name_collision(
    write_tree: WriteTree,
) -> None:
    """A dependency's name is not one of the project's extras (DESIGN.md 5.4).

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
    verdict = evaluate_gates(_plan(), tree.for_feedstock("demo"), UPSTREAM)
    assert "G3" in verdict.summary
    assert "'tests'" in _gate(verdict, "G3").detail  # type: ignore[attr-defined]


def test_g3_does_not_apply_without_a_skip_list(write_tree: WriteTree) -> None:
    """Exhaustiveness is opt-in; a new extra is reported, not gated."""
    tree = _tree(
        write_tree,
        "feedstock: demo\ntrust: auto\nextras_as_outputs:\n"
        "  suffix: '{name}-with-{extra}'\n  supported: [pandas]\n",
    )
    verdict = evaluate_gates(_plan(), tree.for_feedstock("demo"), UPSTREAM)
    assert _gate(verdict, "G3").passed is None  # type: ignore[attr-defined]
    assert verdict.decision == "automerge"


def test_g4_blocks_an_output_whose_extra_disappeared(write_tree: WriteTree) -> None:
    """The orphaned output of DESIGN.md 3.3.11, with both halves of the fix."""
    tree = _tree(
        write_tree,
        "feedstock: demo\ntrust: auto\nextras_as_outputs:\n"
        "  suffix: '{name}-with-{extra}'\n  supported: [gone]\n"
        "  skip: [pandas, tests]\n",
    )
    verdict = evaluate_gates(_plan(), tree.for_feedstock("demo"), UPSTREAM)
    assert "G4" in verdict.summary
    detail = _gate(verdict, "G4").detail  # type: ignore[attr-defined]
    assert "delete the output" in detail
    assert "extras_as_outputs.supported" in detail


def test_g5_holds_by_construction(write_tree: WriteTree) -> None:
    tree = _tree(write_tree, "feedstock: demo\ntrust: auto\n")
    verdict = evaluate_gates(_plan(), tree.for_feedstock("demo"), UPSTREAM)
    assert _gate(verdict, "G5").passed is True  # type: ignore[attr-defined]


@pytest.mark.parametrize("trust", ["manual", "propose"])
def test_g6_blocks_an_unblessed_feedstock(write_tree: WriteTree, trust: str) -> None:
    tree = _tree(write_tree, f"feedstock: demo\ntrust: {trust}\n")
    verdict = evaluate_gates(_plan(), tree.for_feedstock("demo"), UPSTREAM)
    assert "G6" in verdict.summary


def test_g6_blocks_a_feedstock_with_no_config_at_all(write_tree: WriteTree) -> None:
    """New feedstocks start at manual, so silence is a refusal."""
    tree = _tree(write_tree)
    assert "G6" in evaluate_gates(_plan(), tree.for_feedstock("demo"), UPSTREAM).summary


def test_g7_does_not_apply_on_path_a(write_tree: WriteTree) -> None:
    tree = _tree(write_tree, "feedstock: demo\ntrust: auto\n")
    verdict = evaluate_gates(_plan(), tree.for_feedstock("demo"), UPSTREAM)
    assert _gate(verdict, "G7").passed is None  # type: ignore[attr-defined]


def test_g7_blocks_path_b_when_the_rendering_differs(write_tree: WriteTree) -> None:
    """On path B swage is the only thing between the bot's PR and main."""
    tree = _tree(write_tree, "feedstock: demo\ntrust: auto\n")
    verdict = evaluate_gates(
        _plan(), tree.for_feedstock("demo"), UPSTREAM, path_b=True, unchanged=False
    )
    assert "G7" in verdict.summary


def test_g7_blocks_path_b_when_nothing_was_compared(write_tree: WriteTree) -> None:
    """An unverified claim is not a verified one; the default must refuse."""
    tree = _tree(write_tree, "feedstock: demo\ntrust: auto\n")
    verdict = evaluate_gates(_plan(), tree.for_feedstock("demo"), UPSTREAM, path_b=True)
    assert "G7" in verdict.summary


def test_g7_passes_path_b_on_a_byte_identical_rendering(write_tree: WriteTree) -> None:
    tree = _tree(write_tree, "feedstock: demo\ntrust: auto\n")
    verdict = evaluate_gates(
        _plan(), tree.for_feedstock("demo"), UPSTREAM, path_b=True, unchanged=True
    )
    assert verdict.decision == "automerge"


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
    verdict = evaluate_gates(plan, tree.for_feedstock("demo"), UPSTREAM)
    assert "G8" in verdict.summary
    assert "2.0.0" in _gate(verdict, "G8").detail  # type: ignore[attr-defined]


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
    verdict = evaluate_gates(plan, tree.for_feedstock("demo"), UPSTREAM)
    assert _gate(verdict, "G8").passed is None  # type: ignore[attr-defined]
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
    verdict = evaluate_gates(plan, tree.for_feedstock("demo"), UPSTREAM)
    assert verdict.decision == "automerge"


def test_g9_blocks_an_unassociated_run_constraint(write_tree: WriteTree) -> None:
    plan = _plan(
        unassociated_constraints=(UnassociatedConstraint("protobuf >=4", "protobuf"),)
    )
    tree = _tree(write_tree, "feedstock: demo\ntrust: auto\n")
    assert "G9" in evaluate_gates(plan, tree.for_feedstock("demo"), UPSTREAM).summary


def test_g10_blocks_a_computed_dependency_list(write_tree: WriteTree) -> None:
    upstream = parse_pyproject('[project]\nname = "demo"\n')
    dynamic = type(upstream)(
        name=upstream.name, dynamic_fields=frozenset({"requires-dist"})
    )
    tree = _tree(write_tree, "feedstock: demo\ntrust: auto\n")
    verdict = evaluate_gates(_plan(), tree.for_feedstock("demo"), dynamic)
    assert "G10" in verdict.summary
    assert "dynamic_dependencies: trust" in _gate(verdict, "G10").detail  # type: ignore[attr-defined]


def test_g10_does_not_apply_when_the_feedstock_trusts_it(write_tree: WriteTree) -> None:
    upstream = parse_pyproject('[project]\nname = "demo"\n')
    dynamic = type(upstream)(
        name=upstream.name, dynamic_fields=frozenset({"requires-dist"})
    )
    tree = _tree(
        write_tree, "feedstock: demo\ntrust: auto\ndynamic_dependencies: trust\n"
    )
    verdict = evaluate_gates(_plan(), tree.for_feedstock("demo"), dynamic)
    assert _gate(verdict, "G10").passed is None  # type: ignore[attr-defined]


def test_an_unrelated_dynamic_field_does_not_block(write_tree: WriteTree) -> None:
    """`Dynamic: license-file` says nothing about the dependency list."""
    upstream = parse_pyproject('[project]\nname = "demo"\n')
    dynamic = type(upstream)(
        name=upstream.name, dynamic_fields=frozenset({"license-file"})
    )
    tree = _tree(write_tree, "feedstock: demo\ntrust: auto\n")
    assert evaluate_gates(_plan(), tree.for_feedstock("demo"), dynamic).decision == (
        "automerge"
    )


# --- the verdict itself ----------------------------------------------------


def test_every_failing_gate_is_named_not_just_the_first(write_tree: WriteTree) -> None:
    """A report that stops at the first failure costs a second round trip."""
    plan = _plan(
        sections=(
            PlannedSection(
                path="/requirements/run",
                section="run",
                unexplained=(Unexplained("nowhere", "leftpad", "nowhere"),),
                removals=(Removal("upstream-dropped", "six", "dropped"),),
            ),
        ),
        unassociated_constraints=(UnassociatedConstraint("protobuf >=4", "protobuf"),),
    )
    tree = _tree(write_tree, "feedstock: demo\ntrust: propose\n")
    verdict = evaluate_gates(plan, tree.for_feedstock("demo"), UPSTREAM)
    assert {gate.name for gate in verdict.failures} == {"G1", "G6", "G8", "G9"}


def test_every_gate_is_always_reported(write_tree: WriteTree) -> None:
    """`swage explain` prints every gate, including the ones that did not apply."""
    tree = _tree(write_tree, "feedstock: demo\ntrust: auto\n")
    verdict = evaluate_gates(_plan(), tree.for_feedstock("demo"), UPSTREAM)
    assert [gate.name for gate in verdict.gates] == [f"G{n}" for n in range(1, 14)]


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

    verdict = evaluate_gates(plan, tree.for_feedstock("demo"), UPSTREAM)

    assert _gate(verdict, "G12").passed is False  # type: ignore[attr-defined]
    assert verdict.decision == "needs-review"


def test_g12_does_not_apply_once_a_feedstock_opts_out(write_tree: WriteTree) -> None:
    """Promotion is one commit, exactly as `removals` and dynamic lists are."""
    tree = _tree(write_tree, "feedstock: demo\ntrust: auto\ntest_matrix: auto\n")
    plan = replace(
        _plan(),
        test_matrices=(TestMatrix(path="/tests/0/python", was=(), versions=("*",)),),
    )

    verdict = evaluate_gates(plan, tree.for_feedstock("demo"), UPSTREAM)

    assert _gate(verdict, "G12").passed is None  # type: ignore[attr-defined]
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
    verdict = evaluate_gates(_plan(), tree.for_feedstock("demo"), UPSTREAM)

    gate = _gate(verdict, "G3")
    assert gate.passed is False  # type: ignore[attr-defined]
    assert "'tests'" in gate.detail  # type: ignore[attr-defined]


def test_g3_passes_once_a_folded_output_accounts_for_everything(
    write_tree: WriteTree,
) -> None:
    tree = _tree(
        write_tree,
        "feedstock: demo\ntrust: auto\noutputs:\n  demo:\n    run:\n"
        "      core: true\n      extras: [pandas]\n      skip: [docs, tests]\n",
    )
    verdict = evaluate_gates(_plan(), tree.for_feedstock("demo"), UPSTREAM)

    assert _gate(verdict, "G3").passed is True  # type: ignore[attr-defined]
    assert verdict.decision == "automerge"


# --- G11: a bound the recipe has and upstream does not ---------------------


def test_g11_blocks_a_constraint_the_recipe_states_and_upstream_does_not(
    write_tree: WriteTree,
) -> None:
    """`apache-airflow-providers-google` is the fleet's case.

    Its recipe pins `apache-airflow >=2.11.0,<3.1.3` under a comment saying the
    ceiling is there to keep the solver happy, and upstream declares only
    `>=2.11.0`. Every other gate is satisfied while that ceiling goes: G1 asks
    about the line, not about its bound.
    """
    tree = _tree(write_tree, "feedstock: demo\ntrust: auto\n")
    plan = _plan(
        sections=(
            PlannedSection(
                path="/requirements/run",
                section="run",
                tightened=(
                    Tightened(
                        name="apache-airflow",
                        recipe=">=2.11.0,<3.1.3",
                        planned=">=2.11.0",
                    ),
                ),
            ),
        )
    )
    verdict = evaluate_gates(plan, tree.for_feedstock("demo"), UPSTREAM)

    gate = _gate(verdict, "G11")
    assert gate.passed is False  # type: ignore[attr-defined]
    assert "'apache-airflow'" in gate.detail  # type: ignore[attr-defined]
    assert "constraints:" in gate.detail  # type: ignore[attr-defined]
    assert verdict.decision == "needs-review"


def test_g11_passes_when_nothing_is_tightened(write_tree: WriteTree) -> None:
    tree = _tree(write_tree, "feedstock: demo\ntrust: auto\n")
    verdict = evaluate_gates(_plan(), tree.for_feedstock("demo"), UPSTREAM)

    assert _gate(verdict, "G11").passed is True  # type: ignore[attr-defined]


def test_a_host_change_on_a_cross_compiled_output_is_held(
    write_tree: WriteTree,
) -> None:
    """15 of the fleet's 19 cross-compilation blocks repeat a host requirement.

    Which ones belong there is a judgement per dependency -- `pyproj` mirrors
    `cython` and not `proj` -- so swage writes the host change and leaves the
    mirroring to a human, which means not merging it unattended.
    """
    plan = _plan(cross_compiled=("/requirements/host",))
    verdict = evaluate_gates(plan, _tree(write_tree).for_feedstock("demo"), UPSTREAM)
    failed = {gate.name: gate for gate in verdict.failures}
    assert "G13" in failed
    assert "build section" in failed["G13"].detail


def test_an_output_that_does_not_cross_compile_passes(write_tree: WriteTree) -> None:
    verdict = evaluate_gates(_plan(), _tree(write_tree).for_feedstock("demo"), UPSTREAM)
    assert {gate.name: gate.passed for gate in verdict.gates}["G13"] is True
