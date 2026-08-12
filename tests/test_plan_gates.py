"""Trust-gate tests (DESIGN.md 5.4, 11).

The highest-value tests in the suite, and every one of them is a test that a
gate *blocks* something it should block. A false negative here means an
unreviewed bad recipe merges automatically -- the one outcome the whole design
exists to prevent -- so acceptance is checked once per gate and refusal is
checked for each way it can happen.
"""

from __future__ import annotations

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
                requirements=(
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
    assert verdict.label == "automerge"
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
    assert verdict.label == "swage:needs-review"
    assert "G1" in verdict.summary


def test_g2_blocks_an_unresolved_name(write_tree: WriteTree) -> None:
    plan = _plan(
        sections=(
            PlannedSection(
                path="/requirements/run",
                section="run",
                requirements=(
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
    assert "did not resolve" in _gate(verdict, "G2").detail  # type: ignore[attr-defined]


def test_g2_blocks_an_inexact_resolution(write_tree: WriteTree) -> None:
    """A guess swage did not notice is exactly what this gate exists for."""
    guessed = Resolution("Foo-Bar", "foo-bar", "grayskull", exact=False)
    plan = _plan(
        sections=(
            PlannedSection(
                path="/requirements/run",
                section="run",
                requirements=(
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
                requirements=(
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
    assert verdict.label == "automerge"


def test_g3_blocks_an_extra_in_neither_list(write_tree: WriteTree) -> None:
    tree = _tree(
        write_tree,
        "feedstock: demo\ntrust: auto\nextras_as_outputs:\n"
        "  suffix: '{name}-with-{extra}'\n  supported: [pandas]\n  skip: [docs]\n",
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
    assert verdict.label == "automerge"


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
    assert verdict.label == "automerge"


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
    assert verdict.label == "automerge"


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
    assert (
        evaluate_gates(plan, tree.for_feedstock("demo"), UPSTREAM).label == "automerge"
    )


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
    assert evaluate_gates(_plan(), tree.for_feedstock("demo"), dynamic).label == (
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


def test_all_ten_gates_are_always_reported(write_tree: WriteTree) -> None:
    """`swage explain` prints every gate, including the ones that did not apply."""
    tree = _tree(write_tree, "feedstock: demo\ntrust: auto\n")
    verdict = evaluate_gates(_plan(), tree.for_feedstock("demo"), UPSTREAM)
    assert [gate.name for gate in verdict.gates] == [f"G{n}" for n in range(1, 11)]
