"""Tests for raising an arch output's python floor in `build.skip` (DESIGN.md
§16).

An arch output is built once per python `.ci_support` renders, so upstream
raising its `requires-python` past one of them is a build that fails. The
recipe's `match(python, "<3.11")` is raised where swage can find it, and the
feedstock is held where it cannot.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from swage.config import load_config
from swage.forge import commit_message
from swage.mapping import NameResolver, StaticPackageIndex
from swage.plan import Plan, PlanError, check, plan_recipe
from swage.plan.skip_floor import RaisedSkip, reconcile_skip_floors
from swage.recipe import RecipeError, read_recipe, render_recipe
from swage.run.budgets import (
    COMMIT_BODY,
    FINDING_SAID,
    TERMINAL_LINE,
    commit_body,
    words,
)
from swage.upstream import RecipeUpstream, UpstreamMetadata, parse_pyproject

from .conftest import WriteTree

#: `timezonefinder`'s shape: compiled, built once per python, with its floor
#: as a list item.
COMPILED = """\
schema_version: 1

context:
  name: demo
  version: "1.0"

package:
  name: demo
  version: ${{ version }}

build:
  number: 0
  skip:
    - match(python, '<3.11')

requirements:
  host:
    - python
  run:
    - python
"""

BUILT = (11, 12, 13, 14)


def _skip(condition: str) -> str:
    """`COMPILED` with ``condition`` as its whole `skip`, a scalar."""
    return COMPILED.replace(
        "  skip:\n    - match(python, '<3.11')\n", f"  skip: {condition}\n"
    )


def _plan(
    recipe: str,
    write_tree: WriteTree,
    requires_python: str = ">=3.12,<4",
    pythons: Sequence[int] = BUILT,
) -> Plan:
    """Planned as the pipeline plans an arch feedstock: no floor, and the
    pythons `.ci_support` renders.
    """
    tree = load_config(
        write_tree(
            {"defaults.yaml": "trust: never\nrecipe_owned:\n  names: [python]\n"}
        )
    )
    config = tree.for_feedstock("demo")
    return plan_recipe(
        read_recipe(recipe),
        RecipeUpstream.of(
            parse_pyproject(
                '[project]\nname = "demo"\nversion = "1.0"\n'
                f'requires-python = "{requires_python}"\n'
                "[build-system]\nrequires = []\n"
            )
        ),
        config,
        NameResolver(config.name_map, StaticPackageIndex.of()),
        None,
        pythons=pythons,
    )


def test_a_skip_item_is_read_with_its_line() -> None:
    [condition] = read_recipe(COMPILED).outputs[0].skip
    assert condition.path == "/build/skip/0"
    assert condition.text == "match(python, '<3.11')"
    assert condition.line == COMPILED.split("\n").index("    - match(python, '<3.11')")


def test_a_scalar_skip_is_read_at_the_key() -> None:
    [condition] = read_recipe(_skip('match(python, "<3.12")')).outputs[0].skip
    assert condition.path == "/build/skip"
    assert condition.text == 'match(python, "<3.12")'


def test_a_condition_not_on_its_line_as_parsed_cannot_be_rewritten() -> None:
    """A quoted scalar's escapes are gone once parsed, so the writer could
    not find the text it would replace.
    """
    [condition] = read_recipe(_skip("'match(python, ''<3.11'')'")).outputs[0].skip
    assert condition.text == "match(python, '<3.11')"
    assert condition.line is None


def test_upstream_raising_its_floor_raises_the_skip(write_tree: WriteTree) -> None:
    """`timezonefinder` 9.1.0: `>=3.12` against a recipe that builds 3.11."""
    plan = _plan(COMPILED, write_tree)
    assert plan.rendered == COMPILED.replace("'<3.11'", "'<3.12'")
    assert plan.raised_skips == (
        RaisedSkip(
            0,
            "",
            "/build/skip/0",
            "match(python, '<3.12')",
            "3.11",
            "3.12",
            ">=3.12,<4",
        ),
    )
    assert plan.outputs[0].pythons == (12, 13, 14)
    assert not plan.findings


def test_the_raise_is_said_and_committed(write_tree: WriteTree) -> None:
    [raised] = _plan(COMPILED, write_tree).raised_skips
    assert raised.said == (
        "raised the python floor in `build.skip` from 3.11 to 3.12, the oldest "
        "python upstream's `requires-python` allows (`>=3.12,<4`)"
    )
    message = commit_message(
        "demo 1.0", "https://example.org/demo.tar.gz", (), raised.committed
    )
    assert "Also raises the Python floor in build.skip from 3.11 to 3.12" in message
    assert words(commit_body(message)) <= COMMIT_BODY


def test_a_scalar_floor_keeps_its_quotes(write_tree: WriteTree) -> None:
    """`pyproj`'s shape."""
    recipe = _skip('match(python, "<3.11")')
    assert _plan(recipe, write_tree).rendered == recipe.replace("<3.11", "<3.12")


def test_an_inclusive_floor_keeps_its_operator(write_tree: WriteTree) -> None:
    """`scikit-fmm`'s `<=3.9` says the last python skipped; so does the raise."""
    recipe = _skip('match(python, "<=3.10")')
    assert _plan(recipe, write_tree).rendered == recipe.replace("<=3.10", "<=3.11")


def test_a_floor_among_alternatives_is_raised_alone(write_tree: WriteTree) -> None:
    """`e3sm-unified`'s shape: skipping more pythons in one alternative of an
    `or` skips more and nothing else.
    """
    recipe = _skip('win or match(python, "<3.11") or match(python, ">=3.14")')
    plan = _plan(recipe, write_tree)
    assert plan.rendered == recipe.replace('"<3.11"', '"<3.12"')
    assert plan.raised_skips[0].was == "3.11"


def test_a_comment_after_the_condition_stays(write_tree: WriteTree) -> None:
    recipe = COMPILED.replace(
        "    - match(python, '<3.11')\n",
        "    - match(python, '<3.11')  # upstream's floor\n",
    )
    assert _plan(recipe, write_tree).rendered == recipe.replace("<3.11", "<3.12")


@pytest.mark.parametrize(
    "condition",
    [
        'win and match(python, "<3.11")',
        'not match(python, "<3.11")',
        '(win or match(python, "<3.11"))',
        'match(python, "<3.11") if win else false',
    ],
)
def test_a_floor_inside_another_operator_is_not_raised(
    write_tree: WriteTree, condition: str
) -> None:
    """Raising it there would skip something other than the pythons upstream
    dropped, or nothing at all.
    """
    plan = _plan(_skip(condition), write_tree)
    assert plan.rendered == _skip(condition)
    assert [finding.kind for finding in plan.findings] == ["unskipped-python"]


def test_a_recipe_with_no_skip_is_held(write_tree: WriteTree) -> None:
    """Nothing in `build.skip` states a floor, and where a new condition goes
    is the recipe's to say.
    """
    recipe = COMPILED.replace("  skip:\n    - match(python, '<3.11')\n", "")
    plan = _plan(recipe, write_tree)
    assert plan.rendered == recipe
    assert plan.outputs[0].pythons == BUILT
    [finding] = plan.findings
    assert finding.kind == "unskipped-python"
    assert finding.subject == "python 3.11"
    assert finding.said == (
        "`demo` is built for python 3.11, which upstream's `requires-python` "
        "excludes (`>=3.12,<4`), and `build.skip` states no python floor to raise"
    )
    assert finding.remedy == (
        'skip it in the recipe\'s build.skip: match(python, "<3.12")'
    )
    assert not finding.withholds
    assert words(finding.said) <= FINDING_SAID
    assert words(check("unskipped-python").failure) <= TERMINAL_LINE


def test_every_python_upstream_dropped_is_named(write_tree: WriteTree) -> None:
    recipe = COMPILED.replace("  skip:\n    - match(python, '<3.11')\n", "")
    [finding] = _plan(recipe, write_tree, requires_python=">=3.13").findings
    assert finding.subject == "python 3.11, python 3.12"
    assert "built for python 3.11, 3.12," in finding.said


def test_a_skip_already_raised_needs_nothing(write_tree: WriteTree) -> None:
    """The recipe says 3.12 and `.ci_support` has not been re-rendered since."""
    recipe = COMPILED.replace("<3.11", "<3.12")
    plan = _plan(recipe, write_tree)
    assert plan.rendered == recipe
    assert not plan.raised_skips
    assert not plan.findings


def test_upstream_at_the_oldest_python_built_needs_nothing(
    write_tree: WriteTree,
) -> None:
    plan = _plan(COMPILED, write_tree, requires_python=">=3.11")
    assert plan.rendered == COMPILED
    assert not plan.findings


def test_a_ceiling_upstream_states_is_not_a_floor(write_tree: WriteTree) -> None:
    """A python above upstream's cap is a different question from this one."""
    plan = _plan(COMPILED, write_tree, requires_python=">=3.11,<3.14")
    assert plan.rendered == COMPILED
    assert not plan.findings


def test_a_noarch_output_is_left_to_python_min(write_tree: WriteTree) -> None:
    recipe = COMPILED.replace("  number: 0\n", "  number: 0\n  noarch: python\n")
    upstream = RecipeUpstream.of(
        UpstreamMetadata("demo", "1.0", requires_python=">=3.12")
    )
    assert reconcile_skip_floors(read_recipe(recipe), upstream, BUILT) == ((), ())


def test_a_version_independent_output_is_left_to_python_min() -> None:
    """One package per target installed from the floor up: `python_min` is
    its floor, as a noarch output's is.
    """
    recipe = COMPILED.replace(
        "  number: 0\n", "  number: 0\n  python:\n    version_independent: true\n"
    )
    upstream = RecipeUpstream.of(
        UpstreamMetadata("demo", "1.0", requires_python=">=3.12")
    )
    assert read_recipe(recipe).outputs[0].version_independent
    assert reconcile_skip_floors(read_recipe(recipe), upstream, BUILT) == ((), ())


def test_an_output_not_built_against_python_is_left_alone(
    write_tree: WriteTree,
) -> None:
    """A C library beside the bindings is built once, whatever `.ci_support`
    lists for the feedstock.
    """
    recipe = COMPILED.replace("    - python\n", "    - zlib\n")
    plan = _plan(recipe, write_tree)
    assert not plan.raised_skips
    assert not plan.unskipped


def test_a_feedstock_with_no_rendered_pythons_is_left_alone(
    write_tree: WriteTree,
) -> None:
    assert _plan(COMPILED, write_tree, pythons=()).rendered == COMPILED


def test_upstream_supporting_no_python_built_stops(write_tree: WriteTree) -> None:
    """No skip leaves a build upstream supports."""
    with pytest.raises(
        PlanError, match="upstream supports no python `demo` is built for"
    ):
        _plan(COMPILED, write_tree, requires_python=">=3.15")


MULTI = """\
schema_version: 1

context:
  name: demo
  version: "1.0"

outputs:
  - package:
      name: libdemo
    requirements:
      host:
        - zlib
  - package:
      name: demo
    build:
      skip:
        - win
        - match(python, "<3.11")
    requirements:
      host:
        - python
      run:
        - python
        - libdemo
"""


def test_a_split_recipe_names_the_output_it_raised(write_tree: WriteTree) -> None:
    plan = _plan(MULTI, write_tree)
    assert plan.rendered == MULTI.replace('"<3.11"', '"<3.12"')
    [raised] = plan.raised_skips
    assert raised.path == "/outputs/1/build/skip/1"
    assert "in `build.skip` of `demo` from 3.11" in raised.said
    assert "build.skip of demo from 3.11" in raised.committed
    assert plan.outputs[1].pythons == (12, 13, 14)


def test_the_writer_refuses_a_condition_it_cannot_find() -> None:
    recipe = read_recipe(_skip("'match(python, ''<3.11'')'"))
    with pytest.raises(RecipeError, match="not on one line as written"):
        render_recipe(recipe, skips={"/build/skip": "match(python, '<3.12')"})
    with pytest.raises(RecipeError, match=r"no such build\.skip condition"):
        render_recipe(recipe, skips={"/build/skip/0": "win"})
