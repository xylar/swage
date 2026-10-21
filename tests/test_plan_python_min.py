"""Tests for resolving the build floor (design-v1.md 3.3.3).

`python_min` decides which environment markers are reachable, so getting it
wrong changes the meaning of every constraint the planner reconciles. It is
read from the pull request rather than fetched, and where it cannot be read
the feedstock stops.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from swage.config import load_config
from swage.mapping import NameResolver, StaticPackageIndex
from swage.plan import (
    Plan,
    PlanError,
    builds_per_python,
    conda_forge_python_min,
    needs_python_min,
    plan_recipe,
    resolve_python_min,
)
from swage.plan.python_min import (
    DroppedFloor,
    FloorChange,
    PythonMin,
    RaisedFloor,
    python_ceiling,
    reconcile_python_min,
)
from swage.recipe import RecipeError, read_recipe
from swage.upstream import RecipeUpstream, UpstreamMetadata, parse_pyproject

from .conftest import WriteTree

CI_SUPPORT = "channel_targets:\n- conda-forge main\npython_min:\n- '3.10'\n"

RECIPE = """\
schema_version: 1

context:
  name: demo
  version: "1.0"

requirements:
  host:
    - python ${{ python_min }}.*
  run:
    - python >=${{ python_min }}
"""

RECIPE_WITH_PYTHON_MIN = RECIPE.replace(
    'version: "1.0"', 'version: "1.0"\n  python_min: "3.11"'
)


def resolved(recipe: str, ci_support: Sequence[tuple[str, str]] = ()) -> PythonMin:
    """The floor these sources yield, asserted present.

    Absence is an answer of its own here (design-v1.md 3.3.3), so it has its own
    test rather than being something every other one has to narrow past.
    """
    found = resolve_python_min(read_recipe(recipe), ci_support)
    assert found is not None
    return found


def test_ci_support_supplies_the_floor() -> None:
    found = resolved(RECIPE, [(".ci_support/linux_64_.yaml", CI_SUPPORT)])
    assert found.value == "3.10"
    assert found.source == ".ci_support/linux_64_.yaml"


def test_the_recipes_own_context_wins_outright() -> None:
    """That is what `${{ python_min }}` expands to in *this* recipe."""
    found = resolved(
        RECIPE_WITH_PYTHON_MIN, [(".ci_support/linux_64_.yaml", CI_SUPPORT)]
    )
    assert found.value == "3.11"
    assert found.source == "recipe"


def test_the_first_ci_support_file_answers() -> None:
    """`python_min` cannot differ per architecture, so one file is enough.

    Confirmed across the 217 feedstock checkouts on the maintainer's machine
    that carry it: no feedstock's build variants disagree.
    """
    found = resolved(
        RECIPE,
        [
            (".ci_support/linux_64_.yaml", CI_SUPPORT),
            (".ci_support/osx_64_.yaml", "python_min:\n- '3.12'\n"),
        ],
    )
    assert found.value == "3.10"


def test_a_ci_support_file_without_the_key_is_skipped() -> None:
    """conda-smithy writes files that carry no python_min at all."""
    found = resolved(
        RECIPE,
        [
            (".ci_support/migrations.yaml", "migrator_ts: 1234\n"),
            (".ci_support/linux_64_.yaml", CI_SUPPORT),
        ],
    )
    assert found.value == "3.10"


def test_a_scalar_python_min_is_accepted() -> None:
    found = resolved(RECIPE, [(".ci_support/linux_64_.yaml", "python_min: '3.9'\n")])
    assert found.value == "3.9"


def test_no_source_at_all_resolves_to_nothing() -> None:
    """Absence is an answer, and the output that needed one raises the stop.

    conda-smithy writes `python_min` into `.ci_support` for a feedstock that
    builds a noarch python package and not otherwise, so a compiled feedstock
    having none is what conda-smithy meant to say rather than a feedstock
    nobody has rendered. `requires_python.min` is still not a fallback for an
    output that does need one -- it is a different number.
    """
    assert resolve_python_min(read_recipe(RECIPE), []) is None


def test_an_unquoted_python_min_is_refused() -> None:
    """A bare 3.10 is the float 3.1, which is a whole Python release out."""
    with pytest.raises(PlanError, match="not a string"):
        resolve_python_min(
            read_recipe(RECIPE), [(".ci_support/linux_64_.yaml", "python_min: 3.10\n")]
        )


def test_a_non_version_python_min_is_refused() -> None:
    with pytest.raises(PlanError, match="not a version"):
        resolve_python_min(
            read_recipe(RECIPE),
            [(".ci_support/linux_64_.yaml", "python_min:\n- latest\n")],
        )


def test_several_python_min_values_are_refused() -> None:
    with pytest.raises(PlanError, match="2 values"):
        resolve_python_min(
            read_recipe(RECIPE),
            [(".ci_support/linux_64_.yaml", "python_min:\n- '3.9'\n- '3.10'\n")],
        )


def test_invalid_yaml_names_the_file() -> None:
    with pytest.raises(PlanError, match=r"\.ci_support/broken\.yaml: invalid YAML"):
        resolve_python_min(read_recipe(RECIPE), [(".ci_support/broken.yaml", "a: [\n")])


def test_the_version_is_available_for_comparison() -> None:
    found = resolved(RECIPE, [(".ci_support/linux_64_.yaml", CI_SUPPORT)])
    assert found.version.major == 3
    assert found.version.minor == 10


# --- the other end of the range (design-v1.md 3.3.3) -------------------------


def _capped(constraint: str) -> str:
    return RECIPE.replace("- python >=${{ python_min }}", f"- python {constraint}")


def test_a_recipe_that_caps_python_says_where_the_range_ends() -> None:
    """`google-cloud-pubsublite` caps at 3.14 because conda-forge lacks a grpcio."""
    output = read_recipe(_capped(">=${{ python_min }},<3.14")).outputs[0]
    ceiling = python_ceiling(output)
    assert ceiling is not None
    assert (ceiling.major, ceiling.minor) == (3, 14)


def test_an_uncapped_recipe_has_no_ceiling() -> None:
    assert python_ceiling(read_recipe(RECIPE).outputs[0]) is None


def test_an_inclusive_cap_excludes_the_release_after_it() -> None:
    """`<=3.13` still installs on 3.13; treating it as `<3.13` loses a Python."""
    recipe = read_recipe(_capped(">=${{ python_min }},<=3.13"))
    ceiling = python_ceiling(recipe.outputs[0])
    assert ceiling is not None
    assert (ceiling.major, ceiling.minor) == (3, 14)


def test_a_templated_cap_is_no_cap_at_all() -> None:
    """swage cannot resolve `${{ python_over }}`, and guessing is the unsafe way.

    Reconciling over the wider range renders a constraint a human can see and
    undo; the reverse would silently drop one.
    """
    output = read_recipe(_capped(">=${{ python_min }},<${{ python_over }}")).outputs[0]
    assert python_ceiling(output) is None


def test_the_cap_comes_from_run_rather_than_host() -> None:
    """`host` pins the build Python, not the range the package installs across."""
    host = "- python ${{ python_min }}.*"
    recipe = read_recipe(RECIPE.replace(host, f"{host},<3.12"))
    assert python_ceiling(recipe.outputs[0]) is None


# --- which outputs need one (design-v1.md 3.3.3) -----------------------------

NOARCH = """\
schema_version: 1

build:
  noarch: python

requirements:
  run:
    - python >=${{ python_min }}
"""

COMPILED = NOARCH.replace("  noarch: python\n", "  number: 0\n")


def test_a_noarch_python_output_needs_a_floor() -> None:
    assert needs_python_min(read_recipe(NOARCH))


def test_a_feedstock_with_no_noarch_output_is_never_asked() -> None:
    """`pyproj` renders 26 `.ci_support` variants and declares it in none.

    A feedstock whose Python is a build variant has no floor to state, so
    conda-smithy writes none and swage does not fetch `.ci_support` looking
    for one.
    """
    assert not needs_python_min(read_recipe(COMPILED))


def test_a_version_independent_output_needs_a_floor_and_no_python_variants() -> None:
    """`pinecone`'s abi3 build: `.ci_support` renders `python_min` and no
    `python`, because nothing is built once per python.
    """
    abi3 = read_recipe(
        NOARCH.replace(
            "  noarch: python\n", "  python:\n    version_independent: true\n"
        )
    )
    assert needs_python_min(abi3)
    assert not builds_per_python(abi3)


def test_a_noarch_output_with_no_floor_stops_the_feedstock(
    write_tree: WriteTree,
) -> None:
    """The stop is per output, and says what the floor would have been for."""
    tree = load_config(
        write_tree(
            {"defaults.yaml": "trust: never\nrecipe_owned:\n  names: [python]\n"}
        )
    )
    config = tree.for_feedstock("demo")
    with pytest.raises(PlanError) as caught:
        plan_recipe(
            read_recipe(NOARCH),
            RecipeUpstream.of(
                parse_pyproject('[project]\nname = "demo"\nversion = "1.0"\n')
            ),
            config,
            NameResolver(config.name_map, StaticPackageIndex.of()),
            None,
        )
    message = str(caught.value)
    assert "cannot determine the python floor" in message
    assert "run conda-smithy on this feedstock" in message


# --- the floor upstream states, against the floor conda-forge builds ---------


def _raised(
    requires_python: str | None, python_min: str = "3.10", recipe: str = NOARCH
) -> tuple[PythonMin, FloorChange | None]:
    upstream = UpstreamMetadata("demo", "1.0", requires_python=requires_python)
    return reconcile_python_min(
        read_recipe(recipe),
        RecipeUpstream.of(upstream),
        PythonMin(python_min, "recipe"),
    )


def test_an_upstream_floor_at_the_build_floor_is_fine() -> None:
    assert _raised(">=3.10") == (PythonMin("3.10", "recipe"), None)


def test_a_patch_level_floor_within_the_build_floor_is_fine() -> None:
    """`python_min` is a minor release; `gcloud-aio-bigquery` asks for 3.11.3
    against 3.11, which no `python_min` could say instead (DESIGN.md §9.2).
    """
    assert _raised(">=3.11.3,<4.0", "3.11")[1] is None


def test_upstream_saying_nothing_is_fine() -> None:
    """Most projects state a floor; one that does not contradicts nothing."""
    assert _raised(None)[1] is None


def test_an_unparseable_requires_python_says_nothing_either_way() -> None:
    """Upstream metadata is a boundary; a floor swage cannot read is not one."""
    assert _raised("not a specifier")[1] is None


def test_an_upstream_floor_above_the_build_floor_raises_it() -> None:
    """The recipe's `${{ python_min }}` lines would claim a python upstream
    dropped, and the package would not install there anyway (DESIGN.md §16).
    """
    floor, raised = _raised(">=3.12")
    assert floor == PythonMin("3.12", "recipe")
    assert raised == RaisedFloor("3.10", "3.12", ">=3.12")
    assert "from 3.10 to 3.12" in raised.said
    assert "(`>=3.12`)" in raised.said


def test_an_excluded_floor_raises_to_the_next_minor_upstream_supports() -> None:
    assert _raised(">=3.9,!=3.10.*")[0] == PythonMin("3.11", "recipe")


def test_an_arch_output_never_raises_the_floor() -> None:
    """It has no `python_min` to contradict; `.ci_support` says which pythons
    it builds for.
    """
    assert _raised(">=3.12", recipe=COMPILED)[1] is None


def test_upstream_supporting_nothing_from_the_floor_up_stops() -> None:
    """No raise helps an upstream capped below the build floor."""
    with pytest.raises(PlanError) as caught:
        _raised("<3.10")
    message = str(caught.value)
    assert "upstream supports no python from 3.10 up" in message
    assert "requires-python: <3.10" in message


NOARCH_WITH_CONTEXT = """\
schema_version: 1

context:
  name: demo
  version: "1.0"

build:
  noarch: python

requirements:
  run:
    - python >=${{ python_min }}
"""


def _with_floor(value: str) -> str:
    """`NOARCH_WITH_CONTEXT` with `context.python_min` set to ``value``."""
    return NOARCH_WITH_CONTEXT.replace(
        '  version: "1.0"\n', f'  version: "1.0"\n  python_min: "{value}"\n'
    )


def _plan_floor(
    recipe: str,
    write_tree: WriteTree,
    conda_forge: str = "3.10",
    requires_python: str = ">=3.12",
    removals: str = "review",
) -> Plan:
    """Planned as the pipeline plans it: the floor from the recipe or
    `.ci_support`, and conda-forge's own from `.ci_support` alone.
    """
    tree = load_config(
        write_tree(
            {
                "defaults.yaml": f"trust: never\nremovals: {removals}\n"
                "recipe_owned:\n  names: [python]\n"
            }
        )
    )
    config = tree.for_feedstock("demo")
    ci_support = [
        (".ci_support/linux_64_.yaml", CI_SUPPORT.replace("3.10", conda_forge))
    ]
    parsed = read_recipe(recipe)
    return plan_recipe(
        parsed,
        RecipeUpstream.of(
            parse_pyproject(
                '[project]\nname = "demo"\nversion = "1.0"\n'
                f'requires-python = "{requires_python}"\n'
            )
        ),
        config,
        NameResolver(config.name_map, StaticPackageIndex.of()),
        resolve_python_min(parsed, ci_support),
        conda_forge_floor=conda_forge_python_min(ci_support),
    )


def test_a_raised_floor_is_added_to_the_context(write_tree: WriteTree) -> None:
    """Where `.ci_support` supplied the floor, the entry goes after the last
    context entry, double-quoted, as the fleet's recipes keep it.
    """
    plan = _plan_floor(NOARCH_WITH_CONTEXT, write_tree)
    assert plan.rendered == _with_floor("3.12")
    assert plan.floor_change == RaisedFloor("3.10", "3.12", ">=3.12")
    assert plan.outputs[0].floor == PythonMin("3.12", "recipe")
    assert not plan.findings


def test_a_raised_floor_replaces_the_recipes_own(write_tree: WriteTree) -> None:
    """The quote the recipe chose, and a comment after it, are kept."""
    recipe = NOARCH_WITH_CONTEXT.replace(
        '  version: "1.0"\n',
        "  python_min: '3.11'  # what upstream asked for\n  version: \"1.0\"\n",
    )
    plan = _plan_floor(recipe, write_tree)
    assert plan.rendered == recipe.replace("'3.11'", "'3.12'")


def test_a_recipe_without_context_cannot_take_a_raised_floor(
    write_tree: WriteTree,
) -> None:
    with pytest.raises(RecipeError, match="no context to set python_min in"):
        _plan_floor(NOARCH, write_tree)


def test_a_floor_conda_forge_caught_up_with_is_removed(write_tree: WriteTree) -> None:
    """The entry says nothing conda-forge's own floor does not, and removing
    it is asked about like any removal.
    """
    plan = _plan_floor(
        _with_floor("3.11"), write_tree, conda_forge="3.11", requires_python=">=3.10"
    )
    assert plan.rendered == NOARCH_WITH_CONTEXT
    assert plan.floor_change == DroppedFloor("3.11", "3.11")
    assert plan.outputs[0].floor == PythonMin("3.11", ".ci_support/linux_64_.yaml")
    [finding] = plan.findings
    assert finding.kind == "floor-removal"
    assert finding.said == (
        "would remove `python_min: 3.11` from `context`: conda-forge's own "
        "floor is also 3.11"
    )


def test_a_removed_floor_takes_its_comment_with_it(write_tree: WriteTree) -> None:
    """`apache-airflow-providers-google` says why above the entry; the reason
    goes with it rather than staying to explain nothing.
    """
    recipe = _with_floor("3.11").replace(
        '  python_min: "3.11"\n',
        "  # required because no compatible litellm is available for python < 3.11\n"
        '  python_min: "3.11"\n',
    )
    plan = _plan_floor(recipe, write_tree, conda_forge="3.11", requires_python=">=3.10")
    assert plan.rendered == NOARCH_WITH_CONTEXT


def test_a_floor_below_conda_forges_is_removed(write_tree: WriteTree) -> None:
    """`openlineage-python` holds 3.10 after conda-forge moved to 3.11."""
    plan = _plan_floor(
        _with_floor("3.10"), write_tree, conda_forge="3.11", requires_python=">=3.9"
    )
    assert plan.rendered == NOARCH_WITH_CONTEXT
    assert "below conda-forge's own floor of 3.11" in plan.findings[0].said


def test_a_removal_under_removals_auto_is_not_asked_about(
    write_tree: WriteTree,
) -> None:
    plan = _plan_floor(
        _with_floor("3.11"),
        write_tree,
        conda_forge="3.11",
        requires_python=">=3.10",
        removals="auto",
    )
    assert plan.rendered == NOARCH_WITH_CONTEXT
    assert not plan.findings


def test_a_floor_below_conda_forges_and_upstreams_is_raised(
    write_tree: WriteTree,
) -> None:
    """Upstream asks for more than conda-forge, so the entry is still needed."""
    plan = _plan_floor(_with_floor("3.10"), write_tree, conda_forge="3.11")
    assert plan.rendered == _with_floor("3.12")
    assert plan.floor_change == RaisedFloor("3.10", "3.12", ">=3.12")


def test_a_floor_above_both_is_the_maintainers(write_tree: WriteTree) -> None:
    """`e3sm_diags` builds from 3.11 against conda-forge's 3.10."""
    plan = _plan_floor(_with_floor("3.13"), write_tree, conda_forge="3.11")
    assert plan.rendered == _with_floor("3.13")
    assert plan.floor_change is None
