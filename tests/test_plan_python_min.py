"""Tests for resolving the build floor (DESIGN.md 3.3.3).

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
from swage.plan import PlanError, needs_python_min, plan_recipe, resolve_python_min
from swage.plan.python_min import PythonMin, python_ceiling
from swage.recipe import read_recipe
from swage.upstream import parse_pyproject

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

    Absence is an answer of its own here (DESIGN.md 3.3.3), so it has its own
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


# --- the other end of the range (DESIGN.md 3.3.3) -------------------------


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


# --- which outputs need one (DESIGN.md 3.3.3) -----------------------------

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


def test_a_noarch_output_with_no_floor_stops_the_feedstock(
    write_tree: WriteTree,
) -> None:
    """The stop is per output, and says what the floor would have been for."""
    tree = load_config(
        write_tree(
            {"defaults.yaml": "trust: manual\nrecipe_owned:\n  names: [python]\n"}
        )
    )
    config = tree.for_feedstock("demo")
    with pytest.raises(PlanError) as caught:
        plan_recipe(
            read_recipe(NOARCH),
            parse_pyproject('[project]\nname = "demo"\nversion = "1.0"\n'),
            config,
            NameResolver(config.name_map, StaticPackageIndex.of()),
            None,
        )
    message = str(caught.value)
    assert "cannot determine the python floor" in message
    assert "run conda-smithy on this feedstock" in message
