"""Tests for resolving the build floor (DESIGN.md 3.3.3).

`python_min` decides which environment markers are reachable, so getting it
wrong changes the meaning of every constraint the planner reconciles. It is
read from the pull request rather than fetched, and where it cannot be read
the feedstock stops.
"""

from __future__ import annotations

import pytest

from swage.plan import PlanError, resolve_python_min
from swage.plan.python_min import python_ceiling
from swage.recipe import read_recipe

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


def test_ci_support_supplies_the_floor() -> None:
    resolved = resolve_python_min(
        read_recipe(RECIPE), [(".ci_support/linux_64_.yaml", CI_SUPPORT)]
    )
    assert resolved.value == "3.10"
    assert resolved.source == ".ci_support/linux_64_.yaml"


def test_the_recipes_own_context_wins_outright() -> None:
    """That is what `${{ python_min }}` expands to in *this* recipe."""
    resolved = resolve_python_min(
        read_recipe(RECIPE_WITH_PYTHON_MIN),
        [(".ci_support/linux_64_.yaml", CI_SUPPORT)],
    )
    assert resolved.value == "3.11"
    assert resolved.source == "recipe"


def test_the_first_ci_support_file_answers() -> None:
    """`python_min` cannot differ per architecture, so one file is enough.

    Confirmed across the 217 feedstock checkouts on the maintainer's machine
    that carry it: no feedstock's build variants disagree.
    """
    resolved = resolve_python_min(
        read_recipe(RECIPE),
        [
            (".ci_support/linux_64_.yaml", CI_SUPPORT),
            (".ci_support/osx_64_.yaml", "python_min:\n- '3.12'\n"),
        ],
    )
    assert resolved.value == "3.10"


def test_a_ci_support_file_without_the_key_is_skipped() -> None:
    """conda-smithy writes files that carry no python_min at all."""
    resolved = resolve_python_min(
        read_recipe(RECIPE),
        [
            (".ci_support/migrations.yaml", "migrator_ts: 1234\n"),
            (".ci_support/linux_64_.yaml", CI_SUPPORT),
        ],
    )
    assert resolved.value == "3.10"


def test_a_scalar_python_min_is_accepted() -> None:
    resolved = resolve_python_min(
        read_recipe(RECIPE), [(".ci_support/linux_64_.yaml", "python_min: '3.9'\n")]
    )
    assert resolved.value == "3.9"


def test_no_source_at_all_stops_the_feedstock() -> None:
    """requires_python.min is deliberately not a fallback -- different number."""
    with pytest.raises(PlanError, match="cannot determine python_min"):
        resolve_python_min(read_recipe(RECIPE), [])


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
    resolved = resolve_python_min(
        read_recipe(RECIPE), [(".ci_support/linux_64_.yaml", CI_SUPPORT)]
    )
    assert resolved.version.major == 3
    assert resolved.version.minor == 10


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
