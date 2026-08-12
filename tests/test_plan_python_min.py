"""Tests for resolving the build floor (DESIGN.md 3.3.3).

`python_min` decides which environment markers are reachable, so getting it
wrong changes the meaning of every constraint the planner reconciles. It is
read from the pull request rather than fetched, and where it cannot be read
the feedstock stops.
"""

from __future__ import annotations

import pytest

from swage.plan import PlanError, resolve_python_min
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
