"""An `if:` that selects a conda-forge build variant.

A recipe stating a dependency only under a condition, where upstream declares
it always, is a recipe missing that dependency everywhere else -- so swage
refuses to flatten the condition and holds the feedstock (DESIGN.md 3.3.4).
That rule cannot see the case where the condition is conda-forge's own axis
rather than a narrowing of upstream: `esmf` states `parallelio` under
`mpi != "nompi"` because conda-forge builds it once per mpi implementation and
ESMF turns PIO on only for the mpi builds.

The refusal is right by default and stays the default. `variant_conditions` is
how a maintainer says which conditions are on that axis, one at a time, with
the reason recorded beside it.
"""

from __future__ import annotations

import pytest

from swage.config import ConfigTree, Layered, MappingLayer, load_config
from swage.mapping import NameResolver, StaticPackageIndex
from swage.plan import (
    PlanError,
    PlannedConditional,
    PlannedSection,
    PythonMin,
    plan_section,
)
from swage.recipe import Requirement, read_recipe
from swage.upstream import parse_pyproject

from .conftest import WriteTree

PYTHON_MIN = PythonMin("3.10", "recipe")

INDEX = StaticPackageIndex.of("parallelio", "python", "pip", "requests", "setuptools")

#: `mpi` is blessed here as `config/defaults.yaml` blesses it, because the two
#: keys do different jobs on the same block and the fixture should not conflate
#: them: `variant_conditions` decides whether the entry survives,
#: `recipe_owned.variables` explains the `${{ mpi }}` line inside it. Without
#: the second, that line is unexplained and G1 stops the feedstock -- which is
#: correct, and is a fact about the other key.
DEFAULTS = "trust: never\nrecipe_owned:\n  names: [python, pip]\n  variables: [mpi]\n"

UPSTREAM = (
    '[build-system]\nrequires = ["setuptools"]\n\n'
    '[project]\nname = "demo"\nversion = "2.0.0"\n'
    'dependencies = ["parallelio", "requests >=2.21"]\n'
)

#: The dependency is stated only for the mpi builds, and pinned to a version
#: upstream never named -- `esmf`'s shape, down to the trailing `.*`.
RECIPE = """schema_version: 1

package:
  name: demo
  version: 2.0.0

requirements:
  host:
    - python
    - pip
  run:
    - python
    - requests >=2.21
    - if: mpi != "nompi"
      then:
        - ${{ mpi }}
        - parallelio 2.6.9.*
"""

BLESSED = """feedstock: demo
variant_conditions:
  - condition: mpi != "nompi"
    packages: [parallelio]
    reason: >-
      conda-forge builds this once per mpi implementation, and the dependency
      exists only in the mpi builds.
"""

UNBLESSED = "feedstock: demo\n"


def _config(write_tree: WriteTree, feedstock: str) -> ConfigTree:
    return load_config(
        write_tree({"defaults.yaml": DEFAULTS, "feedstocks/demo.yaml": feedstock})
    )


def _conditional(section: PlannedSection) -> PlannedConditional:
    """The one conditional entry the plan holds.

    `requirements` is the unconditional entries only, so a test reading it
    would pass on an entry that had vanished entirely.
    """
    found = [item for item in section.entries if isinstance(item, PlannedConditional)]
    assert len(found) == 1
    return found[0]


def _section(
    write_tree: WriteTree,
    *,
    feedstock: str,
    recipe_text: str = RECIPE,
) -> PlannedSection:
    recipe = read_recipe(recipe_text)
    return plan_section(
        recipe.blocks["/requirements/run"],
        parse_pyproject(UPSTREAM),
        _config(write_tree, feedstock).for_feedstock("demo"),
        NameResolver(Layered((MappingLayer("config/name-map.yaml", {}),)), INDEX),
        PYTHON_MIN,
    )


def test_without_an_entry_the_condition_is_refused(write_tree: WriteTree) -> None:
    """The default is the refusal, and it says what it would have deleted."""
    with pytest.raises(PlanError) as raised:
        _section(write_tree, feedstock=UNBLESSED)

    assert 'mpi != "nompi"' in str(raised.value)
    assert "parallelio" in str(raised.value)


def test_a_blessed_condition_is_preserved_exactly_as_written(
    write_tree: WriteTree,
) -> None:
    section = _section(write_tree, feedstock=BLESSED)

    entry = _conditional(section)
    assert entry.conditionals[0].condition == 'mpi != "nompi"'
    inside = entry.conditionals[0].then
    assert [item.text for item in inside if isinstance(item, Requirement)] == [
        "${{ mpi }}",
        "parallelio 2.6.9.*",
    ]
    assert entry.preserved


def test_a_blessed_condition_does_not_also_get_an_unconditional_line(
    write_tree: WriteTree,
) -> None:
    """The whole hazard: the entry takes the planned line's slot, not a new one."""
    section = _section(write_tree, feedstock=BLESSED)

    assert _conditional(section) is not None
    assert "parallelio" not in [item.text for item in section.requirements]


def test_the_preserved_entry_is_explained_by_upstream(write_tree: WriteTree) -> None:
    """Blessing the condition must not cost the line its provenance at G1."""
    section = _section(write_tree, feedstock=BLESSED)

    assert _conditional(section).provenance.origin.startswith("upstream")
    assert section.unexplained == ()


def test_whitespace_is_normalized_between_recipe_and_config(
    write_tree: WriteTree,
) -> None:
    recipe_text = RECIPE.replace('mpi != "nompi"', 'mpi!="nompi"')
    section = _section(write_tree, feedstock=BLESSED, recipe_text=recipe_text)

    assert _conditional(section).conditionals[0].condition == 'mpi!="nompi"'
    assert "parallelio" not in [item.text for item in section.requirements]


def test_a_different_condition_is_still_refused(write_tree: WriteTree) -> None:
    """Blessing one condition blesses that condition and no other."""
    recipe_text = RECIPE.replace('mpi != "nompi"', "win")
    with pytest.raises(PlanError):
        _section(write_tree, feedstock=BLESSED, recipe_text=recipe_text)


# --- the entry says which packages it decides about ------------------------

#: The same condition, wrapping a package upstream also declares
#: unconditionally but which the entry says nothing about.
OTHER = RECIPE.replace("parallelio 2.6.9.*", "requests >=2.21").replace(
    "    - requests >=2.21\n    - if:", "    - if:"
)


def test_a_line_the_entry_does_not_name_is_kept_inside_the_block(
    write_tree: WriteTree,
) -> None:
    """`packages` is not a list of what the block contains.

    swage never decides the contents of a conditional it preserves -- they are
    the recipe's, kept as written. `${{ mpi }}` is in `esmf`'s block and in no
    `packages` list, and it stays, because nothing plans a line for it and so
    there is nothing about it to decide. Reading the key as a membership list
    is the obvious mistake and this is what rules it out.
    """
    section = _section(write_tree, feedstock=BLESSED)

    inside = _conditional(section).conditionals[0].then
    assert "${{ mpi }}" in [
        item.text for item in inside if isinstance(item, Requirement)
    ]


def test_a_package_the_entry_does_not_name_is_still_refused(
    write_tree: WriteTree,
) -> None:
    """The whole reason `packages` exists.

    Blessing the condition alone reached whatever upstream-declared dependency
    happened to sit inside it, so moving an unrelated package into `esmf`'s
    `mpi != "nompi"` block would have been accepted silently -- a recipe
    claiming a dependency on the mpi builds only, where upstream asks for it
    always, which is the drift the refusal exists to catch.
    """
    with pytest.raises(PlanError) as raised:
        _section(write_tree, feedstock=BLESSED, recipe_text=OTHER)

    assert "requests" in str(raised.value)


def test_that_refusal_says_the_condition_is_blessed_for_other_packages(
    write_tree: WriteTree,
) -> None:
    """A maintainer who already decided this condition is not asked again."""
    with pytest.raises(PlanError) as raised:
        _section(write_tree, feedstock=BLESSED, recipe_text=OTHER)

    message = str(raised.value)
    assert "blesses for other packages" in message
    assert "parallelio" in message, "it names what the entry does cover"
    assert "`packages`" in message, "and the key that would cover this one"


def test_an_entry_that_names_no_package_is_a_config_error(
    write_tree: WriteTree,
) -> None:
    """A condition blessing nothing is a claim about the whole recipe."""
    with pytest.raises(Exception) as raised:
        _config(
            write_tree,
            "feedstock: demo\nvariant_conditions:\n"
            '  - condition: mpi != "nompi"\n    packages: []\n'
            "    reason: conda-forge builds one per mpi implementation.\n",
        ).for_feedstock("demo")

    assert "blesses no package" in str(raised.value)


def test_the_unblessed_refusal_points_at_the_key_that_answers_it(
    write_tree: WriteTree,
) -> None:
    """Before this it said only 'resolve by hand', which config could answer."""
    with pytest.raises(PlanError) as raised:
        _section(write_tree, feedstock=UNBLESSED)

    assert "variant_conditions" in str(raised.value)


def test_a_preserved_entry_says_which_condition_kept_it(
    write_tree: WriteTree,
) -> None:
    """`swage explain` printed a bare `upstream` and hid the entry's work."""
    section = _section(write_tree, feedstock=BLESSED)

    assert 'under if: mpi != "nompi"' in _conditional(section).provenance.detail


def test_an_entry_with_no_reason_is_a_config_error(write_tree: WriteTree) -> None:
    """`draft` makes typing one free; it leaves the thinking as expensive."""
    with pytest.raises(Exception) as raised:
        _config(
            write_tree,
            "feedstock: demo\nvariant_conditions:\n"
            "  - condition: win\n    packages: [parallelio]\n    reason: TODO\n",
        ).for_feedstock("demo")

    assert "reason" in str(raised.value) or "conda-forge's build variant" in str(
        raised.value
    )
