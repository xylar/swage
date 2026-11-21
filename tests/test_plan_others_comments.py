"""A comment another maintainer wrote is never removed (DESIGN.md §16).

virtualenv's `# python-sbom[hatch]` was deleted by a push because swage took
it for a wording of its own. Who wrote a comment now decides: one another
maintainer wrote is kept whatever it says, and a plan that would lose one
anyway -- its line removed, or merged into another -- is withheld.
"""

from __future__ import annotations

from collections.abc import Mapping

from swage.config import load_config
from swage.mapping import NameResolver, StaticPackageIndex
from swage.plan import Plan, check, plan_recipe
from swage.recipe import read_recipe
from swage.run.budgets import FINDING_SAID, TERMINAL_LINE, words
from swage.upstream import RecipeUpstream, parse_pyproject

from .conftest import WriteTree

RECIPE = """\
schema_version: 1

context:
  name: demo
  version: "2.0.0"

package:
  name: demo
  version: ${{ version }}

requirements:
  run:
    - python
    - celery >=5.5.0
    # celery[redis]
    - redis >=4.5.2
    # needed until the 1.x plugins are gone
    - oldpkg
"""

UPSTREAM = """\
[project]
name = "demo"
version = "2.0.0"
dependencies = ["celery[redis]>=5.5.0"]
"""

FEEDSTOCK = """\
feedstock: demo
embedded_extras:
  "celery[redis]":
    - redis >=4.5.2
retire:
  - oldpkg
"""

LABEL = "# celery[redis]"
NOTE = "# needed until the 1.x plugins are gone"


def _plan(
    write_tree: WriteTree,
    others: Mapping[str, tuple[str, ...]],
    recipe: str = RECIPE,
) -> Plan:
    tree = load_config(
        write_tree(
            {
                "defaults.yaml": "trust: never\nrecipe_owned:\n  names: [python]\n",
                "feedstocks/demo.yaml": FEEDSTOCK,
            }
        )
    )
    config = tree.for_feedstock("demo")
    return plan_recipe(
        read_recipe(recipe),
        RecipeUpstream.of(parse_pyproject(UPSTREAM)),
        config,
        NameResolver(
            config.name_map, StaticPackageIndex.of("celery", "redis", "oldpkg")
        ),
        None,
        others=others,
    )


def _lines(plan: Plan) -> list[str]:
    return [line.strip() for line in plan.rendered.splitlines()]


def test_without_a_known_author_swage_reads_comments_as_before(
    write_tree: WriteTree,
) -> None:
    """The label repeats swage's marker and goes; the retired line takes its
    note with it. Nothing is held.
    """
    plan = _plan(write_tree, {})

    assert LABEL not in _lines(plan)
    assert NOTE not in _lines(plan)
    assert plan.findings == ()


def test_a_label_another_maintainer_wrote_stays_beside_swages_marker(
    write_tree: WriteTree,
) -> None:
    plan = _plan(write_tree, {LABEL: ("ocefpaf",)})

    lines = _lines(plan)
    assert "# start celery[redis]" in lines
    assert lines.index(LABEL) == lines.index("- redis >=4.5.2") - 1
    assert plan.findings == ()


def test_a_removed_line_with_another_maintainers_note_is_withheld(
    write_tree: WriteTree,
) -> None:
    plan = _plan(write_tree, {NOTE: ("ocefpaf",)})

    [finding] = plan.findings
    assert finding.kind == "others-comment"
    assert finding.withholds
    assert finding.said == f"would remove `{NOTE}`, which ocefpaf wrote"


def test_two_lines_merged_into_one_lose_the_first_ones_note(
    write_tree: WriteTree,
) -> None:
    """The comments above the earlier of two lines that plan as one are
    dropped (DESIGN.md §16); another maintainer's among them holds the plan.
    """
    note = "# the floor upstream forgot to state"
    recipe = RECIPE.replace(
        "    - celery >=5.5.0\n", f"    {note}\n    - celery\n    - celery >=5.5.0\n"
    )
    plan = _plan(write_tree, {note: ("ocefpaf", "jaimergp")}, recipe)

    assert [finding.said for finding in plan.findings] == [
        f"would remove `{note}`, which ocefpaf and jaimergp wrote"
    ]


def test_a_comment_kept_once_of_twice_is_still_lost(write_tree: WriteTree) -> None:
    """Counted, not looked up: a copy that survives elsewhere is not the one
    the removed line carried.
    """
    recipe = RECIPE.replace(
        "    - celery >=5.5.0\n", f"    {NOTE}\n    - celery >=5.5.0\n"
    )
    plan = _plan(write_tree, {NOTE: ("ocefpaf",)}, recipe)

    assert NOTE in _lines(plan)
    assert [finding.kind for finding in plan.findings] == ["others-comment"]


def test_the_check_reads_in_budget() -> None:
    row = check("others-comment")
    assert words(row.failure) <= TERMINAL_LINE
    assert words(f"would remove `{NOTE}`, which ocefpaf and jaimergp wrote") <= (
        FINDING_SAID
    )
