"""Reading and writing a `noarch: python` test matrix (DESIGN.md 3.7).

This is the second kind of range swage writes, and the first outside a
requirements block. What is worth pinning here is the part that used to be
free: with one kind of range, "the diff touches only requirements" held
because no other code path existed. With two, the ordering between them is
something a test has to hold down.
"""

from __future__ import annotations

import pytest

from swage.recipe import RecipeError, read_recipe
from swage.recipe.model import LATEST
from swage.recipe.write import render_python_version, render_recipe


def recipe(tests: str, run: str = "    - python >=${{ python_min }}\n") -> str:
    return f"""context:
  version: '3.43.0'
  python_min: '3.10'

package:
  name: demo
  version: ${{{{ version }}}}

build:
  noarch: python

requirements:
  run:
{run}
tests:
{tests}
about:
  summary: demo
"""


SCALAR = """  - python:
      imports:
        - demo
      pip_check: true
      python_version: ${{ python_min }}.*

"""

COVERED = """  - python:
      imports:
        - demo
      python_version:
        - ${{ python_min }}.*
        - "*"

"""

NO_VERSION = """  - python:
      imports:
        - demo
      pip_check: true

"""

SCRIPT_ONLY = """  - script:
      - python -c "import demo"

"""

FLUSH_VERSIONS = """  - python:
      imports:
        - demo
      python_version:
      - ${{ python_min }}.*

"""


def test_a_scalar_version_is_read_as_the_one_version_it_names() -> None:
    """241 of the 242 blocks needing the edit are this shape."""
    test = read_recipe(recipe(SCALAR)).python_tests[0]
    assert test.path == "/tests/0/python"
    assert test.versions == ("${{ python_min }}.*",)
    assert test.present is True
    assert test.covers_latest is False


def test_a_list_that_already_covers_the_latest_is_read_as_complete() -> None:
    test = read_recipe(recipe(COVERED)).python_tests[0]
    assert test.versions == ("${{ python_min }}.*", LATEST)
    assert test.covers_latest is True


def test_a_missing_version_is_read_rather_than_refused() -> None:
    """conda-smithy counts this as failing, so swage has to be able to see it.

    It is one recipe in 242, and swage reads it without offering to write it:
    inserting a key is a different operation from replacing one.
    """
    test = read_recipe(recipe(NO_VERSION)).python_tests[0]
    assert test.present is False
    assert test.versions == ()
    assert test.covers_latest is False


def test_a_test_with_no_python_key_is_not_a_python_test() -> None:
    """The airflow providers' nineteen outputs are all this, and are skipped.

    conda-smithy skips them too -- the rule only looks at entries that have a
    `python:` key -- which is why a nineteen-output recipe needs no edit at all.
    """
    assert read_recipe(recipe(SCRIPT_ONLY)).python_tests == ()


def test_the_build_section_and_the_python_cap_are_read_per_output() -> None:
    """Both scope the rule, and conda-smithy reads both per output."""
    plain = read_recipe(recipe(SCALAR)).outputs[0]
    assert plain.noarch == "python"
    assert plain.caps_python is False

    capped = read_recipe(
        recipe(SCALAR, run="    - python >=${{ python_min }},<3.13\n")
    ).outputs[0]
    assert capped.caps_python is True


def test_a_scalar_becomes_the_two_item_list_conda_smithy_looks_for() -> None:
    parsed = read_recipe(recipe(SCALAR))
    rendered = render_recipe(
        parsed, matrices={"/tests/0/python": ["${{ python_min }}.*", LATEST]}
    )

    assert (
        """      python_version:
        - ${{ python_min }}.*
        - "*"
"""
        in rendered
    )
    # Everything else is byte-identical, which is the whole splicing bargain.
    assert (
        rendered.replace(
            """      python_version:
        - ${{ python_min }}.*
        - "*\"""",
            "      python_version: ${{ python_min }}.*",
        )
        == parsed.text
    )


def test_the_latest_marker_is_quoted_because_yaml_needs_it_to_be() -> None:
    """Unquoted, a leading `*` opens an alias and the recipe stops parsing.

    conda-smithy matches the exact string `*`, so quoting is not a style
    choice -- it is the only way to write the thing it looks for.
    """
    assert render_python_version(["3.10.*", LATEST], 8) == [
        "      python_version:",
        "        - 3.10.*",
        '        - "*"',
    ]
    reread = read_recipe(
        render_recipe(
            read_recipe(recipe(SCALAR)),
            matrices={"/tests/0/python": ["${{ python_min }}.*", LATEST]},
        )
    )
    assert reread.python_tests[0].versions == ("${{ python_min }}.*", LATEST)


def test_writing_a_matrix_that_was_never_there_is_refused() -> None:
    """An unread range points at line 0, which is a recipe's first line."""
    parsed = read_recipe(recipe(NO_VERSION))
    with pytest.raises(RecipeError, match="no python_version to replace"):
        render_recipe(parsed, matrices={"/tests/0/python": [LATEST]})


def test_an_unknown_test_path_is_refused() -> None:
    with pytest.raises(RecipeError, match="no such python test"):
        render_recipe(read_recipe(recipe(SCALAR)), matrices={"/tests/9/python": ["*"]})


def test_requirements_and_a_matrix_are_spliced_in_one_pass() -> None:
    """The ordering hazard the second kind of range introduced.

    Tests sit below requirements, so applying either set of edits without the
    other in view would use line numbers the first set had already invalidated.
    Both are sorted together, bottom up.
    """
    parsed = read_recipe(recipe(SCALAR))
    rendered = render_recipe(
        parsed,
        changes={"/requirements/run": parsed.blocks["/requirements/run"].content},
        matrices={"/tests/0/python": ["${{ python_min }}.*", LATEST]},
    )

    lines = rendered.splitlines()
    assert "    - python >=${{ python_min }}" in lines
    assert '        - "*"' in lines
    # The keys still sit under the right parents rather than one block adrift.
    assert lines[lines.index("      python_version:") - 1] == "      pip_check: true"
    assert read_recipe(rendered).python_tests[0].covers_latest is True


def test_versions_level_with_their_key_are_covered_by_the_range() -> None:
    """A range that stopped at the key line would leave the old ones behind.

    Nothing in the fleet writes `python_version` this way, and the reason to
    hold it down anyway is that the failure is silent: the versions parse, so
    swage would report the edit as made while the recipe listed both the new
    list and the list it was supposed to replace.
    """
    parsed = read_recipe(recipe(FLUSH_VERSIONS))
    rendered = render_recipe(
        parsed, matrices={"/tests/0/python": ["${{ python_min }}.*", LATEST]}
    )
    assert rendered.count("${{ python_min }}.*") == parsed.text.count(
        "${{ python_min }}.*"
    )
    assert (
        """      python_version:
        - ${{ python_min }}.*
        - "*"
"""
        in rendered
    )
