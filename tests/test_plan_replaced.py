"""Tests for renaming a package conda-forge has replaced (DESIGN.md §9.6)."""

from __future__ import annotations

from swage.plan import Replacement, replace_packages
from swage.recipe import read_recipe

RECIPE = """\
schema_version: 1

package:
  name: demo
  version: "1.0"

requirements:
  build:
    - ${{ compiler('c') }}
    - pkg-config  # finds netcdf
    - if: unix
      then: pkg-config
    - if: win
      then:
        - pkg-config
        - m2w64-pkg-config
  host:
    - netcdf-fortran
    - pkg-config >=0.21
  run:
    - netcdf-fortran

tests:
  - script:
      - pkg-config --exists netcdf-fortran
"""


def test_every_bare_line_is_renamed_and_nothing_else_moves() -> None:
    text, replaced = replace_packages(read_recipe(RECIPE))

    assert replaced == (Replacement("pkg-config", "pkgconf"),)
    expected = (
        RECIPE.replace("- pkg-config  # finds", "- pkgconf  # finds")
        .replace("then: pkg-config\n", "then: pkgconf\n")
        .replace("        - pkg-config\n", "        - pkgconf\n")
    )
    assert text == expected


def test_a_bound_a_lookalike_and_a_script_are_left_alone() -> None:
    """`pkg-config >=0.21` counts pkg-config's releases, not pkgconf's;
    `m2w64-pkg-config` is another package; a test script calls the executable,
    which pkgconf still provides.
    """
    text, _ = replace_packages(read_recipe(RECIPE))

    assert "- pkg-config >=0.21" in text
    assert "- m2w64-pkg-config" in text
    assert "- pkg-config --exists netcdf-fortran" in text


def test_a_recipe_without_one_is_returned_as_it_is() -> None:
    recipe = read_recipe(RECIPE.replace("pkg-config", "cmake"))

    assert replace_packages(recipe) == (recipe.text, ())


def test_the_summary_says_why() -> None:
    assert Replacement("pkg-config", "pkgconf").summary == (
        "pkg-config with pkgconf, which conda-forge asks for instead"
    )
