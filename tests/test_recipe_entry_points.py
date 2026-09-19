"""Reading and writing `build.python.entry_points` (DESIGN.md 3.3.15).

The third kind of range swage writes. What the matrix tests pinned for the
second holds here too: each new kind is a line range the reader located, and
the ordering between the kinds is something a test has to hold down, because
`build:` sits above the requirements and the tests sit below them.
"""

from __future__ import annotations

import pytest

from swage.recipe import RecipeError, read_recipe, render_recipe

ONE = """    entry_points:
      - m2r2 = m2r2:main
"""

TWO = """    entry_points:
      - m2r2 = m2r2:main
      - m2r2-gui=m2r2.gui:main
"""

CONDITIONAL = """    entry_points:
      - m2r2 = m2r2:main
      - if: win
        then: m2r2-win = m2r2.win:main
"""


def recipe(entry_points: str = ONE, python: str = "  python:\n") -> str:
    return f"""context:
  version: '1.1.0'
  python_min: '3.10'

package:
  name: m2r2
  version: ${{{{ version }}}}

build:
  number: 0
  noarch: python
  script: ${{{{ PYTHON }}}} -m pip install . -vv
{python}{entry_points}
requirements:
  host:
    - python ${{{{ python_min }}}}.*
    - pip
  run:
    - python >=${{{{ python_min }}}}

tests:
  - python:
      imports:
        - m2r2
      python_version: ${{{{ python_min }}}}.*

about:
  summary: demo
"""


def test_the_list_is_read_with_its_items_as_written() -> None:
    """Item text is kept verbatim: `m2r2-gui=m2r2.gui:main` and all."""
    output = read_recipe(recipe(TWO), "m2r2").outputs[0]

    assert output.entry_points is not None
    assert output.entry_points.path == "/build/python/entry_points"
    assert output.entry_points.items == ("m2r2 = m2r2:main", "m2r2-gui=m2r2.gui:main")
    assert not output.entry_points.conditional


def test_an_output_without_the_key_has_none() -> None:
    output = read_recipe(recipe("", ""), "m2r2").outputs[0]

    assert output.entry_points is None


def test_an_if_entry_is_read_past_and_marked() -> None:
    """The string items are still there; the list is flagged, not refused."""
    output = read_recipe(recipe(CONDITIONAL), "m2r2").outputs[0]

    assert output.entry_points is not None
    assert output.entry_points.items == ("m2r2 = m2r2:main",)
    assert output.entry_points.conditional


def test_writing_replaces_the_list_and_nothing_else() -> None:
    before = recipe(ONE)
    parsed = read_recipe(before, "m2r2")
    path = parsed.outputs[0].entry_points.path  # type: ignore[union-attr]

    after = render_recipe(
        parsed, entry_points={path: ["m2r2 = m2r2.cli.m2r2:main", "x = y:z"]}
    )

    assert after == before.replace(
        "      - m2r2 = m2r2:main\n",
        "      - m2r2 = m2r2.cli.m2r2:main\n      - x = y:z\n",
    )


def test_the_items_keep_the_recipe_s_own_indent() -> None:
    """A rewrite that moved the items would be a diff nobody asked for."""
    flush = "    entry_points:\n    - m2r2 = m2r2:main\n"
    parsed = read_recipe(recipe(flush), "m2r2")
    path = parsed.outputs[0].entry_points.path  # type: ignore[union-attr]

    after = render_recipe(parsed, entry_points={path: ["m2r2 = m2r2.cli.m2r2:main"]})

    assert "    entry_points:\n    - m2r2 = m2r2.cli.m2r2:main\n" in after


def test_what_was_written_reads_back() -> None:
    parsed = read_recipe(recipe(ONE), "m2r2")
    path = parsed.outputs[0].entry_points.path  # type: ignore[union-attr]

    after = render_recipe(parsed, entry_points={path: ["a = a:main", "b = b:main"]})
    reread = read_recipe(after, "m2r2").outputs[0].entry_points

    assert reread is not None
    assert reread.items == ("a = a:main", "b = b:main")


def test_all_three_kinds_of_edit_land_in_one_pass() -> None:
    """`build:` is above the requirements and `tests:` below them.

    Applied from the bottom up in one sorted list; applied kind by kind,
    the first kind's edit would move the others' line numbers.
    """
    parsed = read_recipe(recipe(ONE), "m2r2")
    output = parsed.outputs[0]
    scripts = output.entry_points.path  # type: ignore[union-attr]
    host = output.blocks["host"]
    test = output.python_tests[0].path

    from swage.recipe.model import BlockContent, Requirement

    after = render_recipe(
        parsed,
        changes={
            host.path: BlockContent(
                entries=(
                    *host.content.entries,
                    Requirement(text="hatchling"),
                )
            )
        },
        matrices={test: ["${{ python_min }}.*", "*"]},
        entry_points={scripts: ["m2r2 = m2r2.cli.m2r2:main"]},
    )

    assert "      - m2r2 = m2r2.cli.m2r2:main\n" in after
    assert "    - hatchling\n" in after
    assert '        - "*"\n' in after
    assert read_recipe(after, "m2r2").outputs[0].entry_points is not None


def test_writing_a_list_that_is_not_there_is_an_error() -> None:
    parsed = read_recipe(recipe("", ""), "m2r2")

    with pytest.raises(RecipeError, match="no such entry_points list"):
        render_recipe(parsed, entry_points={"/build/python/entry_points": ["a = a:b"]})


def test_writing_a_conditional_list_is_refused() -> None:
    """An `if:` entry is a decision the recipe made; a flat list unmakes it."""
    parsed = read_recipe(recipe(CONDITIONAL), "m2r2")
    path = parsed.outputs[0].entry_points.path  # type: ignore[union-attr]

    with pytest.raises(RecipeError, match="holds an if: entry"):
        render_recipe(parsed, entry_points={path: ["a = a:b"]})
