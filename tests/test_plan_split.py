"""Writing upstream's python markers as conditions (DESIGN.md 3.3.1.1).

An architecture-specific output is built once per python, so a dependency
upstream gates on the python version belongs in the recipe as a condition. The
collapse `reconcile` performs is not merely unnecessary there -- it is wrong:
`grpcio >=1.67.0` on the 3.10 build is a claim upstream never made, and one
that can make a solve fail for no reason.

`apache-beam` writes this shape by hand, which is why it is in the corpus.
"""

from __future__ import annotations

import pytest

from swage.config import load_config
from swage.mapping import NameResolver, StaticPackageIndex
from swage.plan import (
    PlanError,
    PlannedConditional,
    PlannedRequirement,
    PlannedSection,
    PythonMin,
    RecipePlan,
    plan_section,
    planned_blocks,
)
from swage.plan.split import split_by_python
from swage.recipe import read_recipe, render_block
from swage.upstream import UpstreamRequirement, parse_pyproject

from .conftest import WriteTree


def declared(*raw: str) -> tuple[UpstreamRequirement, ...]:
    """Upstream's declarations of one package, as the metadata reader yields them."""
    parsed = []
    for text in raw:
        requirement, _, marker = text.partition(";")
        name = requirement.split()[0].split(">")[0].split("<")[0].split("!")[0]
        parsed.append(
            UpstreamRequirement(
                name=name,
                specifier=requirement.strip().removeprefix(name),
                marker=marker.strip() or None,
                raw=text,
            )
        )
    return tuple(parsed)


def test_one_declaration_is_one_unconditional_line() -> None:
    """The common case, and it must stay a plain line rather than a condition."""
    split = split_by_python("pandas", declared("pandas>=2.1.2"))
    assert [(b.condition, b.specifier) for b in split.branches] == [(None, ">=2.1.2")]


def test_two_ranges_become_one_entry_with_an_else() -> None:
    """`apache-beam`'s own grpcio split, which is the fleet's example."""
    split = split_by_python(
        "grpcio",
        declared(
            'grpcio>=1.33.1,<1.66.0; python_version <"3.13"',
            'grpcio>=1.67.0; python_version >="3.13"',
        ),
    )
    assert split.complementary
    assert [(b.condition, b.specifier) for b in split.branches] == [
        ('python < "3.13"', ">=1.33.1,<1.66.0"),
        ('python >= "3.13"', ">=1.67.0"),
    ]


def test_three_ranges_stay_three_entries() -> None:
    """One `else:` cannot express three answers, so each range says itself."""
    split = split_by_python(
        "pandas",
        declared(
            'pandas>=2.1.2; python_version <"3.13"',
            'pandas>=2.2.3; python_version >="3.13" and python_version <"3.14"',
            'pandas>=2.3.3; python_version >="3.14"',
        ),
    )
    assert not split.complementary
    assert [(b.condition, b.specifier) for b in split.branches] == [
        ('python < "3.13"', ">=2.1.2"),
        ('python >= "3.13" and python < "3.14"', ">=2.2.3"),
        ('python >= "3.14"', ">=2.3.3"),
    ]


def test_a_dependency_upstream_asks_for_on_some_pythons_only() -> None:
    """No `else:` branch: below 3.13 upstream does not ask for it at all.

    Rendering one would put a bare dependency into the recipe that upstream
    never declared, which is the difference between "no constraint" and "not
    required".
    """
    split = split_by_python(
        "typing-extensions", declared('typing-extensions; python_version <"3.11"')
    )
    assert not split.complementary
    assert [(b.condition, b.specifier) for b in split.branches] == [
        ('python < "3.11"', "")
    ]


def test_a_declaration_no_python_can_reach_is_not_written_at_all() -> None:
    """conda-forge builds python 3, so a python 2 marker asks for nothing."""
    split = split_by_python("mock", declared('mock>=2.0; python_version <"3.0"'))
    assert split.branches == ()
    assert split.considered == ()


def test_an_unmarked_declaration_binds_on_every_range() -> None:
    """Upstream says both things about 3.12, so the recipe has to say both."""
    split = split_by_python(
        "grpcio",
        declared("grpcio<2", 'grpcio>=1.67.0; python_version >="3.13"'),
    )
    assert [(b.condition, b.specifier) for b in split.branches] == [
        ('python < "3.13"', "<2"),
        ('python >= "3.13"', ">=1.67.0,<2"),
    ]


def test_the_build_floor_does_not_clip_anything() -> None:
    """An arch output has no range to serve, so nothing is discarded.

    A variant unreachable on the pythons this feedstock builds produces a
    condition that is never selected, which is which pythons `.ci_support`
    lists rather than anything `python_min` says (DESIGN.md 3.3.1.1).
    """
    split = split_by_python(
        "importlib-metadata", declared('importlib-metadata>=4; python_version <"3.8"')
    )
    assert [b.condition for b in split.branches] == ['python < "3.8"']


def test_config_constrains_every_range_rather_than_one() -> None:
    """A `constraints:` entry holds on every python, so it binds in each branch."""
    split = split_by_python(
        "grpcio",
        declared(
            'grpcio>=1.33.1; python_version <"3.13"',
            'grpcio>=1.67.0; python_version >="3.13"',
        ),
        constraint="<2",
    )
    assert [b.specifier for b in split.branches] == [">=1.33.1,<2", ">=1.67.0,<2"]


def test_a_marker_on_a_patch_release_is_refused() -> None:
    """conda-forge builds one package per minor release and none per patch."""
    with pytest.raises(PlanError) as caught:
        split_by_python("numpy", declared('numpy>=2.0; python_full_version >="3.12.4"'))
    assert "one package per minor release" in str(caught.value)


def test_declarations_that_contradict_on_one_python_are_refused() -> None:
    """Not the noarch collapse: these two apply to the very same build."""
    with pytest.raises(PlanError) as caught:
        split_by_python(
            "pandas",
            declared("pandas<2.1.2", 'pandas>=2.3.3; python_version >="3.13"'),
        )
    message = str(caught.value)
    assert "contradictory upstream constraints" in message
    assert "python 3.13" in message


def test_a_marker_on_another_axis_stops_rather_than_being_ignored() -> None:
    """Writing the python part alone would drop half of what upstream said."""
    with pytest.raises(PlanError) as caught:
        split_by_python("pywin32", declared('pywin32>=306; sys_platform =="win32"'))
    assert "sys_platform" in str(caught.value)


# --- what the section ends up looking like --------------------------------

ARCH_RECIPE = """\
schema_version: 1

build:
  number: 0

requirements:
  host:
    - python
    - setuptools
  run:
    - python
"""

UPSTREAM = """\
[project]
name = "demo"
version = "1.0.0"
dependencies = [
  "fasteners>=0.3,<1.0",
  "grpcio>=1.33.1,<1.66.0; python_version <'3.13'",
  "grpcio>=1.67.0; python_version >='3.13'",
]

[build-system]
requires = ["setuptools"]
"""


def _plan_run(
    write_tree: WriteTree, recipe_text: str, upstream: str = UPSTREAM
) -> PlannedSection:
    tree = load_config(
        write_tree(
            {"defaults.yaml": "trust: manual\nrecipe_owned:\n  names: [python]\n"}
        )
    )
    config = tree.for_feedstock("demo")
    return plan_section(
        read_recipe(recipe_text).outputs[0].blocks["run"],
        parse_pyproject(upstream),
        config,
        NameResolver(
            config.name_map, StaticPackageIndex.of("fasteners", "grpcio", "python")
        ),
        PythonMin("3.10", "recipe"),
        noarch=False,
    )


def _rendered(section: PlannedSection) -> list[str]:
    content = planned_blocks(RecipePlan(sections=(section,)))["/requirements/run"]
    return render_block(content, 4)


def test_the_planned_section_states_the_dependency_per_python(
    write_tree: WriteTree,
) -> None:
    by_name = {
        entry.name: entry for entry in _plan_run(write_tree, ARCH_RECIPE).entries
    }
    assert isinstance(by_name["fasteners"], PlannedRequirement)
    grpcio = by_name["grpcio"]
    assert isinstance(grpcio, PlannedConditional)
    assert grpcio.provenance.origin == "upstream-core"


def test_the_section_renders_as_the_fleet_writes_it(write_tree: WriteTree) -> None:
    """The bytes, because that is what lands in somebody's feedstock."""
    section = _plan_run(write_tree, ARCH_RECIPE)
    content = planned_blocks(RecipePlan(sections=(section,)))["/requirements/run"]
    assert render_block(content, 4) == [
        "    - python",
        "    - fasteners >=0.3,<1.0",
        '    - if: python < "3.13"',
        "      then: grpcio >=1.33.1,<1.66.0",
        "      else: grpcio >=1.67.0",
    ]


# --- a conditional the recipe already has ---------------------------------

WITH_CONDITIONALS = """\
schema_version: 1

build:
  number: 0

requirements:
  run:
    - python
    - fasteners >=0.3,<1.0
    - if: python < "3.13"
      then: grpcio >=1.33.1,<1.66.0
      else: grpcio >=1.67.0
"""


def test_a_second_run_writes_back_what_the_first_one_wrote(
    write_tree: WriteTree,
) -> None:
    """The entry swage authored is replaced by the one it derives again.

    Without this the recipe's conditional and the planned one would be two
    different things and the section would end up with both -- which is how a
    dependency acquires a second, contradictory entry every time swage runs.
    """
    assert _rendered(_plan_run(write_tree, WITH_CONDITIONALS)) == [
        "    - python",
        "    - fasteners >=0.3,<1.0",
        '    - if: python < "3.13"',
        "      then: grpcio >=1.33.1,<1.66.0",
        "      else: grpcio >=1.67.0",
    ]


def test_a_conditional_upstream_says_nothing_about_is_kept_exactly(
    write_tree: WriteTree,
) -> None:
    """`libnetcdf` and `netcdf-fortran` condition on their mpi variant.

    Nothing in upstream metadata explains that, and swage does not delete
    structure it cannot explain -- it keeps it verbatim and reports it, which
    holds the feedstock for a human rather than merging it.
    """
    recipe = WITH_CONDITIONALS.replace(
        "    - python\n",
        '    - python\n    - if: mpi != "nompi"\n      then: ${{ mpi }}\n',
    )
    section = _plan_run(write_tree, recipe)
    assert "      then: ${{ mpi }}" in _rendered(section)
    assert [item.kind for item in section.unexplained] == ["unrecognized-template"]


def test_two_conditionals_naming_the_same_package_first_both_survive(
    write_tree: WriteTree,
) -> None:
    """`libnetcdf` has two `mpi != "nompi"` entries in one section.

    Keyed by the first name inside them they would be one entry, and swage
    would drop whichever it saw first.
    """
    recipe = WITH_CONDITIONALS.replace(
        "    - python\n",
        "    - python\n"
        '    - if: mpi == "openmpi"\n      then: openmpi\n'
        '    - if: mpi == "mpich"\n      then: openmpi\n',
    )
    rendered = _rendered(_plan_run(write_tree, recipe))
    assert rendered.count("      then: openmpi") == 2


def test_a_condition_swage_would_delete_stops_the_feedstock(
    write_tree: WriteTree,
) -> None:
    """Upstream declares `fasteners` for every build; the recipe does not.

    Rendering the plan would drop the condition, and whether this package
    should carry `fasteners` everywhere is a packaging decision rather than a
    reconciliation.
    """
    recipe = WITH_CONDITIONALS.replace(
        "    - fasteners >=0.3,<1.0\n",
        "    - if: unix\n      then: fasteners >=0.3,<1.0\n",
    )
    with pytest.raises(PlanError) as caught:
        _plan_run(write_tree, recipe)
    message = str(caught.value)
    assert "'fasteners' conditionally" in message
    assert "if: unix" in message
