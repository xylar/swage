"""A `retire` entry reaching a dependency the recipe states conditionally.

swage does not delete a structure it did not author on evidence about one of
the names inside it, so a conditional entry is preserved whole and its
dependencies are attributed individually. That rule left `retire` unable to
reach two feedstocks it plainly covers: `colorlog` conditions `colorama` on
Windows in `host`, where upstream declares it only to run, and `dulwich`
conditions `setuptools` on python 3.12 and up, where upstream declares it only
to build with. Both lines are artifacts and neither could be answered in
config -- `dulwich`'s own file said so in a comment.

Where config accounts for *every* name inside, the reason to preserve is gone:
the entry states nothing anybody still means. Where it accounts for only some,
the entry stays, because removing it would take the rest away with it.
"""

from __future__ import annotations

from swage.config import ConfigTree, Layered, MappingLayer, load_config
from swage.mapping import NameResolver, StaticPackageIndex
from swage.plan import PlannedSection, PythonMin, plan_section
from swage.recipe import read_recipe
from swage.upstream import parse_pyproject

from .conftest import WriteTree

PYTHON_MIN = PythonMin("3.10", "recipe")

INDEX = StaticPackageIndex.of("colorama", "python", "setuptools", "urllib3")

DEFAULTS = "trust: never\nrecipe_owned:\n  names: [python, pip]\n"

#: Upstream's shape on `dulwich`: setuptools builds it and nothing else, and
#: the run dependency is something entirely different.
UPSTREAM = (
    '[build-system]\nrequires = ["setuptools"]\n\n'
    '[project]\nname = "demo"\nversion = "2.0.0"\n'
    'dependencies = ["urllib3 >=2.2.2"]\n'
)

RECIPE = """schema_version: 1

package:
  name: demo
  version: 2.0.0

requirements:
  host:
    - python
    - pip
    - setuptools
  run:
    - python
    - urllib3 >=2.2.2
    - if: match(python, ">=3.12")
      then: setuptools
"""


def _config(write_tree: WriteTree, feedstock: str) -> ConfigTree:
    return load_config(
        write_tree({"defaults.yaml": DEFAULTS, "feedstocks/demo.yaml": feedstock})
    )


def _section(
    write_tree: WriteTree,
    path: str,
    *,
    feedstock: str,
    recipe_text: str = RECIPE,
    upstream_text: str = UPSTREAM,
) -> PlannedSection:
    recipe = read_recipe(recipe_text)
    return plan_section(
        recipe.blocks[path],
        parse_pyproject(upstream_text),
        _config(write_tree, feedstock).for_feedstock("demo"),
        NameResolver(Layered((MappingLayer("config/name-map.yaml", {}),)), INDEX),
        PYTHON_MIN,
    )


RETIRED = "feedstock: demo\nretire:\n  - setuptools\n"
UNANSWERED = "feedstock: demo\n"


def test_a_conditional_config_accounts_for_whole_is_removed(
    write_tree: WriteTree,
) -> None:
    """`dulwich`'s `run` line, which no `retire` entry could reach before."""
    section = _section(write_tree, "/requirements/run", feedstock=RETIRED)

    assert [item.text for item in section.requirements] == [
        "python",
        "urllib3 >=2.2.2",
    ]
    assert [removal.fate for removal in section.dropped] == ["retired"]
    assert "setuptools" in section.dropped[0].reason


def test_the_removed_conditional_is_not_also_reported_as_unexplained(
    write_tree: WriteTree,
) -> None:
    """Config said what it is, so asking the maintainer again never ends."""
    section = _section(write_tree, "/requirements/run", feedstock=RETIRED)

    assert section.unexplained == ()


def test_the_same_name_is_kept_where_upstream_declares_it(
    write_tree: WriteTree,
) -> None:
    """`retire` is only ever consulted once upstream has had nothing to say.

    Upstream builds with setuptools, so the `host` line is upstream's own and
    the entry never reaches the retire list -- the same asymmetry that lets one
    entry answer a `run` line without touching the `host` line beside it.
    """
    section = _section(write_tree, "/requirements/host", feedstock=RETIRED)

    assert "setuptools" in [item.name for item in section.requirements]
    assert section.dropped == ()


def test_a_conditional_naming_something_else_too_is_preserved(
    write_tree: WriteTree,
) -> None:
    """All or nothing: removing it would take `colorama` away with it."""
    recipe = RECIPE.replace(
        '    - if: match(python, ">=3.12")\n      then: setuptools\n',
        '    - if: match(python, ">=3.12")\n      then:\n'
        "        - setuptools\n        - colorama\n",
    )
    section = _section(
        write_tree, "/requirements/run", feedstock=RETIRED, recipe_text=recipe
    )

    assert section.dropped == ()
    assert [item.text for item in section.unexplained] == ["setuptools", "colorama"]


def test_a_conditional_without_a_retire_entry_is_preserved(
    write_tree: WriteTree,
) -> None:
    """The behavior every conditional keeps: preserved, and still reported."""
    section = _section(write_tree, "/requirements/run", feedstock=UNANSWERED)

    assert section.dropped == ()
    assert [item.text for item in section.unexplained] == ["setuptools"]
