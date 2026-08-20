"""Trust-gate tests (DESIGN.md 5.4, 11).

The highest-value tests in the suite, and every one of them is a test that a
gate *blocks* something it should block. A false negative here means an
unreviewed bad recipe merges automatically -- the one outcome the whole design
exists to prevent -- so acceptance is checked once per gate and refusal is
checked for each way it can happen.
"""

from __future__ import annotations

import re
from dataclasses import replace

import pytest

from swage.config import AddedRequirement, ConfigTree, Override, load_config
from swage.mapping import Resolution
from swage.plan import (
    PlannedRequirement,
    PlannedSection,
    Provenance,
    RecipePlan,
    SelfConflict,
    Unexplained,
    evaluate_gates,
)
from swage.plan.constrained import UnassociatedConstraint
from swage.plan.removals import Removal
from swage.plan.test_matrix import TestMatrix
from swage.upstream import RecipeUpstream, parse_pyproject

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
            "trust: never\nrecipe_owned:\n  names: [python, pip]\n"
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

    assert gate.each == (  # type: ignore[attr-defined]
        "`leftpad >=1` in `/requirements/run` came from nowhere",
    )
    assert "add_requirements" in gate.detail  # type: ignore[attr-defined]


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
    assert "`tests`" in _gate(verdict, "G3").detail  # type: ignore[attr-defined]


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
    verdict = evaluate_gates(
        _plan(), tree.for_feedstock("demo"), RecipeUpstream.of(UPSTREAM)
    )
    assert "G3" in verdict.summary
    assert "`tests`" in _gate(verdict, "G3").detail  # type: ignore[attr-defined]


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
    verdict = evaluate_gates(
        _plan(), tree.for_feedstock("demo"), RecipeUpstream.of(UPSTREAM)
    )
    assert "G4" in verdict.summary
    detail = _gate(verdict, "G4").detail  # type: ignore[attr-defined]
    assert "delete the output" in detail
    assert "extras_as_outputs.supported" in detail


def test_g5_holds_by_construction(write_tree: WriteTree) -> None:
    tree = _tree(write_tree, "feedstock: demo\ntrust: auto\n")
    verdict = evaluate_gates(
        _plan(), tree.for_feedstock("demo"), RecipeUpstream.of(UPSTREAM)
    )
    assert _gate(verdict, "G5").passed is True  # type: ignore[attr-defined]


@pytest.mark.parametrize("trust", ["never", "propose"])
def test_g6_blocks_an_unblessed_feedstock(write_tree: WriteTree, trust: str) -> None:
    tree = _tree(write_tree, f"feedstock: demo\ntrust: {trust}\n")
    verdict = evaluate_gates(
        _plan(), tree.for_feedstock("demo"), RecipeUpstream.of(UPSTREAM)
    )
    assert "G6" in verdict.summary


def test_the_two_unblessed_rungs_do_not_say_the_same_thing(
    write_tree: WriteTree,
) -> None:
    """They mean opposite things about whether anything was written.

    `propose` pushed the commit and left the label; `manual` wrote nothing at
    all. Saying "not approved for automatic merging" of a `manual` feedstock
    answers a question nobody asked -- which is what a maintainer read off an
    `--execute` run they had asked for by hand, and could not account for.
    """
    manual = _tree(write_tree, "feedstock: demo\ntrust: never\n")
    propose = _tree(write_tree, "feedstock: demo\ntrust: propose\n")

    held = _gate(
        evaluate_gates(
            _plan(), manual.for_feedstock("demo"), RecipeUpstream.of(UPSTREAM)
        ),
        "G6",
    ).detail  # type: ignore[attr-defined]
    pushed = _gate(
        evaluate_gates(
            _plan(), propose.for_feedstock("demo"), RecipeUpstream.of(UPSTREAM)
        ),
        "G6",
    ).detail  # type: ignore[attr-defined]

    assert "writes nothing to this feedstock" in held
    # Where to change it, since the rung is a fact about config.
    assert "config/feedstocks/demo.yaml" in held
    assert "automatic merging" not in held
    assert pushed == "not approved for automatic merging (trust: propose)"


def test_g6_blocks_a_feedstock_with_no_config_at_all(write_tree: WriteTree) -> None:
    """New feedstocks start at manual, so silence is a refusal."""
    tree = _tree(write_tree)
    assert (
        "G6"
        in evaluate_gates(
            _plan(), tree.for_feedstock("demo"), RecipeUpstream.of(UPSTREAM)
        ).summary
    )


def test_g7_does_not_apply_on_path_a(write_tree: WriteTree) -> None:
    tree = _tree(write_tree, "feedstock: demo\ntrust: auto\n")
    verdict = evaluate_gates(
        _plan(), tree.for_feedstock("demo"), RecipeUpstream.of(UPSTREAM)
    )
    assert _gate(verdict, "G7").passed is None  # type: ignore[attr-defined]


def test_g7_blocks_path_b_when_the_rendering_differs(write_tree: WriteTree) -> None:
    """On path B swage is the only thing between the bot's PR and main."""
    tree = _tree(write_tree, "feedstock: demo\ntrust: auto\n")
    verdict = evaluate_gates(
        _plan(),
        tree.for_feedstock("demo"),
        RecipeUpstream.of(UPSTREAM),
        path_b=True,
        unchanged=False,
    )
    assert "G7" in verdict.summary


def test_g7_blocks_path_b_when_nothing_was_compared(write_tree: WriteTree) -> None:
    """An unverified claim is not a verified one; the default must refuse."""
    tree = _tree(write_tree, "feedstock: demo\ntrust: auto\n")
    verdict = evaluate_gates(
        _plan(), tree.for_feedstock("demo"), RecipeUpstream.of(UPSTREAM), path_b=True
    )
    assert "G7" in verdict.summary


def test_g7_passes_path_b_on_a_byte_identical_rendering(write_tree: WriteTree) -> None:
    tree = _tree(write_tree, "feedstock: demo\ntrust: auto\n")
    verdict = evaluate_gates(
        _plan(),
        tree.for_feedstock("demo"),
        RecipeUpstream.of(UPSTREAM),
        path_b=True,
        unchanged=True,
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
    verdict = evaluate_gates(
        plan, tree.for_feedstock("demo"), RecipeUpstream.of(UPSTREAM)
    )
    assert "G8" in verdict.summary
    assert "2.0.0" in _gate(verdict, "G8").detail  # type: ignore[attr-defined]


def test_g8_does_not_hold_a_removal_config_already_explained(
    write_tree: WriteTree,
) -> None:
    """A `retire` entry is the decision; G8 asking again never ends.

    `retire` is only reached once upstream has been asked and had nothing to
    say about the name in any version or under any extra, so the maintainer
    has already written the answer down. Holding it anyway held 36 of the
    fleet's feedstocks on one config line -- 38 of the 50 in the google-cloud
    family carry the same retired grayskull artifact (DESIGN.md 3.3.8).
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
    assert _gate(verdict, "G8").passed is True  # type: ignore[attr-defined]
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
    assert [r.text for r in plan.upstream_dropped] == ["six >=1.16"]


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
    detail = _gate(verdict, "G8").detail  # type: ignore[attr-defined]
    assert _gate(verdict, "G8").passed is False  # type: ignore[attr-defined]
    assert "six" in detail
    assert "google-api-core" not in detail


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
    verdict = evaluate_gates(
        plan, tree.for_feedstock("demo"), RecipeUpstream.of(UPSTREAM)
    )
    assert verdict.decision == "automerge"


def test_g9_blocks_an_unassociated_run_constraint(write_tree: WriteTree) -> None:
    plan = _plan(
        unassociated_constraints=(UnassociatedConstraint("protobuf >=4", "protobuf"),)
    )
    tree = _tree(write_tree, "feedstock: demo\ntrust: auto\n")
    assert (
        "G9"
        in evaluate_gates(
            plan, tree.for_feedstock("demo"), RecipeUpstream.of(UPSTREAM)
        ).summary
    )


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
    assert "dynamic_dependencies: trust" in _gate(verdict, "G10").detail  # type: ignore[attr-defined]


def test_g10_does_not_apply_when_the_feedstock_trusts_it(write_tree: WriteTree) -> None:
    upstream = parse_pyproject('[project]\nname = "demo"\n')
    dynamic = type(upstream)(
        name=upstream.name, dynamic_fields=frozenset({"requires-dist"})
    )
    tree = _tree(
        write_tree, "feedstock: demo\ntrust: auto\ndynamic_dependencies: trust\n"
    )
    verdict = evaluate_gates(
        _plan(), tree.for_feedstock("demo"), RecipeUpstream.of(dynamic)
    )
    assert _gate(verdict, "G10").passed is None  # type: ignore[attr-defined]


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
        unassociated_constraints=(UnassociatedConstraint("protobuf >=4", "protobuf"),),
    )
    tree = _tree(write_tree, "feedstock: demo\ntrust: propose\n")
    verdict = evaluate_gates(
        plan, tree.for_feedstock("demo"), RecipeUpstream.of(UPSTREAM)
    )
    assert {gate.name for gate in verdict.failures} == {"G1", "G6", "G8", "G9"}


def test_every_gate_is_always_reported(write_tree: WriteTree) -> None:
    """`swage explain` prints every gate, including the ones that did not apply."""
    tree = _tree(write_tree, "feedstock: demo\ntrust: auto\n")
    verdict = evaluate_gates(
        _plan(), tree.for_feedstock("demo"), RecipeUpstream.of(UPSTREAM)
    )
    assert [gate.name for gate in verdict.gates] == [f"G{n}" for n in range(1, 15)]


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

    assert _gate(verdict, "G12").passed is False  # type: ignore[attr-defined]
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
    verdict = evaluate_gates(
        _plan(), tree.for_feedstock("demo"), RecipeUpstream.of(UPSTREAM)
    )

    gate = _gate(verdict, "G3")
    assert gate.passed is False  # type: ignore[attr-defined]
    assert "`tests`" in gate.detail  # type: ignore[attr-defined]


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

    assert _gate(verdict, "G3").passed is True  # type: ignore[attr-defined]
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
    assert gate.passed is False  # type: ignore[attr-defined]
    assert "airflow 3.1.3 breaks the solver" in gate.detail  # type: ignore[attr-defined]


def test_g11_says_nothing_about_a_permanent_one(write_tree: WriteTree) -> None:
    """`constraints:` is a decision already on the record."""
    tree = _tree(write_tree, "feedstock: demo\ntrust: auto\n")
    verdict = evaluate_gates(
        _plan(), tree.for_feedstock("demo"), RecipeUpstream.of(UPSTREAM)
    )
    gate = _gate(verdict, "G11")
    assert gate.passed is True  # type: ignore[attr-defined]


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
    assert gate.passed is False  # type: ignore[attr-defined]
    assert "4.4.0 on conda-forge is broken" in gate.detail  # type: ignore[attr-defined]


def test_g11_does_not_withhold_the_push(write_tree: WriteTree) -> None:
    """Asking again must not cost the update (DESIGN.md 5.4).

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

    assert _gate(verdict, "G11").passed is False  # type: ignore[attr-defined]
    assert verdict.withheld == ()


def test_a_host_change_on_a_cross_compiled_output_is_held(
    write_tree: WriteTree,
) -> None:
    """15 of the fleet's 19 cross-compilation blocks repeat a host requirement.

    Which ones belong there is a judgment per dependency -- `pyproj` mirrors
    `cython` and not `proj` -- so swage writes the host change and leaves the
    mirroring to a human, which means not merging it unattended.
    """
    plan = _plan(cross_compiled=("/requirements/host",))
    verdict = evaluate_gates(
        plan, _tree(write_tree).for_feedstock("demo"), RecipeUpstream.of(UPSTREAM)
    )
    failed = {gate.name: gate for gate in verdict.failures}
    assert "G13" in failed
    assert "build section" in failed["G13"].detail


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
        unassociated_constraints=(UnassociatedConstraint("zlib 1.2.*", "zlib"),),
        test_matrices=(TestMatrix("/tests/0/python", ("3.10.*",), ("3.10.*", "*")),),
        cross_compiled=("/requirements/host",),
    )
    verdict = evaluate_gates(
        plan, tree.for_feedstock("demo"), RecipeUpstream.of(UPSTREAM)
    )

    failures = {gate.name: gate.detail for gate in verdict.failures}
    # The gates that quote recipe text are the ones this is about, so the test
    # is worthless if they did not fire.
    assert {"G1", "G2", "G8", "G9", "G11", "G12", "G13"} <= set(failures)
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
    plan = RecipePlan(
        self_conflicts=(
            SelfConflict(
                output="/outputs/1",
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
    a doubled full stop where the first ended in one -- published under the
    maintainer's name on a repository they do not own (DESIGN.md 5.4).
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

    assert len(gate.each) == 2  # type: ignore[attr-defined]
    assert all("Re-check whether" not in finding for finding in gate.each)  # type: ignore[attr-defined]
    # The advice is said once, and only where swage's own config keys belong.
    assert gate.detail.count("Re-check whether") == 1  # type: ignore[attr-defined]


def test_advice_does_not_double_a_full_stop(write_tree: WriteTree) -> None:
    """A `reason` is a sentence somebody wrote, and usually ends in one.

    `swage explain` and the terminal report read the joined form, so appending
    the advice with its own full stop printed `repodata-patched.. Re-check`.
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
    assert ".." not in gate.detail  # type: ignore[attr-defined]
    assert "broken. Re-check" in gate.detail  # type: ignore[attr-defined]


def test_a_check_that_found_one_thing_still_has_it(write_tree: WriteTree) -> None:
    """`each` is every failing check's findings, however many there are."""
    tree = _tree(write_tree, "feedstock: demo\ntrust: propose\n")
    gate = _gate(
        evaluate_gates(
            _plan(), tree.for_feedstock("demo"), RecipeUpstream.of(UPSTREAM)
        ),
        "G6",
    )
    assert gate.each == (gate.detail,)  # type: ignore[attr-defined]
