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
from swage.plan.split import split_by_environment
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
    split = split_by_environment("pandas", declared("pandas>=2.1.2"))
    assert [(b.condition, b.specifier) for b in split.branches] == [(None, ">=2.1.2")]


def test_two_ranges_become_one_entry_with_an_else() -> None:
    """`apache-beam`'s own grpcio split, which is the fleet's example."""
    split = split_by_environment(
        "grpcio",
        declared(
            'grpcio>=1.33.1,<1.66.0; python_version <"3.13"',
            'grpcio>=1.67.0; python_version >="3.13"',
        ),
    )
    assert split.complementary
    assert [(b.condition, b.specifier) for b in split.branches] == [
        ('match(python, "<3.13")', ">=1.33.1,<1.66.0"),
        ('match(python, ">=3.13")', ">=1.67.0"),
    ]


def test_three_ranges_stay_three_entries() -> None:
    """One `else:` cannot express three answers, so each range says itself."""
    split = split_by_environment(
        "pandas",
        declared(
            'pandas>=2.1.2; python_version <"3.13"',
            'pandas>=2.2.3; python_version >="3.13" and python_version <"3.14"',
            'pandas>=2.3.3; python_version >="3.14"',
        ),
    )
    assert not split.complementary
    assert [(b.condition, b.specifier) for b in split.branches] == [
        ('match(python, "<3.13")', ">=2.1.2"),
        ('match(python, ">=3.13") and match(python, "<3.14")', ">=2.2.3"),
        ('match(python, ">=3.14")', ">=2.3.3"),
    ]


def test_a_dependency_upstream_asks_for_on_some_pythons_only() -> None:
    """No `else:` branch: below 3.13 upstream does not ask for it at all.

    Rendering one would put a bare dependency into the recipe that upstream
    never declared, which is the difference between "no constraint" and "not
    required".
    """
    split = split_by_environment(
        "typing-extensions", declared('typing-extensions; python_version <"3.11"')
    )
    assert not split.complementary
    assert [(b.condition, b.specifier) for b in split.branches] == [
        ('match(python, "<3.11")', "")
    ]


def test_a_declaration_no_python_can_reach_is_not_written_at_all() -> None:
    """conda-forge builds python 3, so a python 2 marker asks for nothing."""
    split = split_by_environment("mock", declared('mock>=2.0; python_version <"3.0"'))
    assert split.branches == ()
    assert split.considered == ()


def test_an_unmarked_declaration_binds_on_every_range() -> None:
    """Upstream says both things about 3.12, so the recipe has to say both."""
    split = split_by_environment(
        "grpcio",
        declared("grpcio<2", 'grpcio>=1.67.0; python_version >="3.13"'),
    )
    assert [(b.condition, b.specifier) for b in split.branches] == [
        ('match(python, "<3.13")', "<2"),
        ('match(python, ">=3.13")', ">=1.67.0,<2"),
    ]


def test_the_build_floor_does_not_clip_anything() -> None:
    """An arch output has no range to serve, so nothing is discarded.

    A variant unreachable on the pythons this feedstock builds produces a
    condition that is never selected, which is which pythons `.ci_support`
    lists rather than anything `python_min` says (DESIGN.md 3.3.1.1).
    """
    split = split_by_environment(
        "importlib-metadata", declared('importlib-metadata>=4; python_version <"3.8"')
    )
    assert [b.condition for b in split.branches] == ['match(python, "<3.8")']


def test_config_constrains_every_range_rather_than_one() -> None:
    """A `constraints:` entry holds on every python, so it binds in each branch."""
    split = split_by_environment(
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
        split_by_environment(
            "numpy", declared('numpy>=2.0; python_full_version >="3.12.4"')
        )
    assert "one package per minor release" in str(caught.value)


def test_declarations_that_contradict_on_one_python_are_refused() -> None:
    """Not the noarch collapse: these two apply to the very same build."""
    with pytest.raises(PlanError) as caught:
        split_by_environment(
            "pandas",
            declared("pandas<2.1.2", 'pandas>=2.3.3; python_version >="3.13"'),
        )
    message = str(caught.value)
    assert "contradictory upstream constraints" in message
    assert "python 3.13" in message


# --- the platform axis (DESIGN.md 3.3.4) ----------------------------------


def test_a_platform_marker_becomes_a_platform_condition() -> None:
    """The axis the build already varies over, so there is nothing to decide."""
    split = split_by_environment(
        "pywin32", declared('pywin32>=306; sys_platform =="win32"')
    )
    assert [(b.condition, b.specifier) for b in split.branches] == [("win", ">=306")]


def test_the_platform_is_read_from_whichever_variable_upstream_used() -> None:
    """`platform_system` and `os_name` say the same thing as `sys_platform`."""
    for marker in ('platform_system =="Windows"', 'os_name =="nt"'):
        split = split_by_environment("pywin32", declared(f"pywin32>=306; {marker}"))
        assert [b.condition for b in split.branches] == ["win"]


def test_the_two_platforms_that_are_not_windows_are_named_unix() -> None:
    split = split_by_environment(
        "uvloop", declared('uvloop>=0.19; sys_platform !="win32"')
    )
    assert [b.condition for b in split.branches] == ["unix"]


def test_one_platform_alone_is_named_by_itself() -> None:
    split = split_by_environment(
        "pyobjc-core", declared('pyobjc-core>=9; sys_platform =="darwin"')
    )
    assert [b.condition for b in split.branches] == ["osx"]


def test_a_platform_split_becomes_one_entry_with_an_else() -> None:
    split = split_by_environment(
        "colorama",
        declared(
            'colorama>=0.4; sys_platform =="win32"',
            'colorama>=0.3; sys_platform !="win32"',
        ),
    )
    assert split.complementary
    assert [(b.condition, b.specifier) for b in split.branches] == [
        ("unix", ">=0.3"),
        ("win", ">=0.4"),
    ]


def test_a_marker_turning_on_both_axes_says_both() -> None:
    """One condition per group of builds, joined to the run it holds over."""
    split = split_by_environment(
        "pywin32",
        declared('pywin32>=306; sys_platform =="win32" and python_version <"3.13"'),
        pythons=(10, 11, 12, 13, 14),
    )

    assert [(branch.condition, branch.specifier) for branch in split.branches] == [
        ('win and match(python, "<3.13")', ">=306")
    ]


def test_two_markers_that_between_them_use_both_axes_compose() -> None:
    """No single marker mixes the axes, and the answer still varies by both.

    Each group of builds gets its own runs, so the cell where both markers hold
    carries both constraints rather than either one of them.
    """
    split = split_by_environment(
        "grpcio",
        declared(
            'grpcio>=1.67.0; python_version >="3.13"',
            'grpcio<2; sys_platform =="win32"',
        ),
        pythons=(10, 11, 12, 13, 14),
    )

    assert [(branch.condition, branch.specifier) for branch in split.branches] == [
        ('unix and match(python, ">=3.13")', ">=1.67.0"),
        ('win and match(python, "<3.13")', "<2"),
        ('win and match(python, ">=3.13")', ">=1.67.0,<2"),
    ]


def test_a_marker_on_an_axis_the_build_does_not_vary_over_is_refused() -> None:
    """conda-forge builds no PyPy in this fleet, so nothing answers that marker."""
    with pytest.raises(PlanError) as caught:
        split_by_environment(
            "numpy",
            declared('numpy>=2.0; platform_python_implementation =="PyPy"'),
        )
    assert "platform_python_implementation" in str(caught.value)


def test_a_machine_marker_becomes_the_selector_a_recipe_writes() -> None:
    """conda-forge builds linux-aarch64, and a recipe selects it by name.

    swage used to refuse this as "not something this output is built once for
    each of", which is false: `aarch64`, `arm64` and `ppc64le` are as much
    build targets as `win` is, and the fleet's recipes carry all three.
    """
    split = split_by_environment(
        "numpy",
        declared('numpy>=2.0; platform_machine=="aarch64"'),
        pythons=(10, 11, 12),
    )

    assert [(branch.condition, branch.specifier) for branch in split.branches] == [
        ("aarch64", ">=2.0")
    ]


def test_apple_silicon_and_windows_on_arm_are_one_selector() -> None:
    """`arm64` names both, which is why the machine is not the platform.

    A marker says `platform_machine == "arm64"` for macOS and `"ARM64"` for
    Windows; the recipe says `arm64` for either.
    """
    split = split_by_environment(
        "pyobjc",
        declared('pyobjc>=10; platform_machine=="arm64" and sys_platform=="darwin"'),
        pythons=(10, 11, 12),
    )

    assert [branch.condition for branch in split.branches] == ["osx and arm64"]


def test_a_declaration_below_every_python_built_is_dropped_not_refused() -> None:
    """pyodps, and the reason a refusal has to come after reachability.

    Upstream asks for `oldest-supported-numpy` on aarch64 below python 3.9 and
    the feedstock is built for 3.10 up, so the declaration describes an
    artifact conda-forge does not produce. swage refused the whole feedstock
    over the `platform_machine` half of a marker whose python half had already
    made it moot -- asking a maintainer to resolve a case that cannot arise.
    """
    split = split_by_environment(
        "oldest-supported-numpy",
        declared(
            "oldest-supported-numpy==2023.10.25; "
            "platform_machine=='aarch64' and python_version<'3.9'"
        ),
        pythons=(10, 11, 12, 13, 14),
    )

    assert split.branches == ()
    # Nothing considered either, which is what the planner reads as "upstream
    # does not ask for this package on anything built here".
    assert split.considered == ()


def test_pyodps_cython_is_written_as_its_maintainer_writes_it() -> None:
    """The case that prompted all of this, end to end.

    pyodps declares cython twice, bounded differently above and below 3.12 and
    only off Windows, and its recipe answers by hand with
    `if: not win and match(python, "<=3.12")`. swage refused the feedstock
    rather than write what the recipe it was reading already said.

    **This test's name was false until the `match` fix.** It quoted the
    maintainer's `match(python, "<=3.12")` in this docstring and then asserted
    swage wrote `unix and python < "3.13"` -- which is not how the maintainer
    writes it, and is a different kind of comparison. `unix` for `not win` is
    a spelling of the same selector and `<3.13` for `<=3.12` is the same
    boundary, so both of those are fair. A string comparison standing in for a
    version comparison was not, and the docstring had the evidence in it the
    whole time.
    """
    split = split_by_environment(
        "cython",
        declared(
            "cython>=3.0,<3.1; platform_system!='Windows' and python_version <= '3.12'",
            "cython>=3.1,<3.3; platform_system!='Windows' and python_version > '3.12'",
        ),
        pythons=(10, 11, 12, 13, 14),
    )

    assert [(branch.condition, branch.specifier) for branch in split.branches] == [
        ('unix and match(python, "<3.13")', ">=3.0,<3.1"),
        ('unix and match(python, ">=3.13")', ">=3.1,<3.3"),
    ]


def test_a_run_reaching_the_oldest_python_built_is_open_ended() -> None:
    """`match(python, ">=3.10")` on a feedstock built from 3.10 up says nothing.

    Every artifact satisfies it, and the next reader takes it for a bound
    upstream asked for. The sampled axis starts where the builds start, so its
    first run is open-ended exactly as the whole axis's first run always was.
    """
    split = split_by_environment(
        "grpcio",
        declared(
            'grpcio>=1.33.1; python_version<"3.13"',
            'grpcio>=1.67.0; python_version>="3.13"',
        ),
        pythons=(10, 11, 12, 13, 14),
    )

    assert [branch.condition for branch in split.branches] == [
        'match(python, "<3.13")',
        'match(python, ">=3.13")',
    ]


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
            {"defaults.yaml": "trust: never\nrecipe_owned:\n  names: [python]\n"}
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
        '    - if: match(python, "<3.13")',
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
    - if: match(python, "<3.13")
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
        '    - if: match(python, "<3.13")',
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


def test_preserved_conditionals_keep_the_order_the_recipe_had(
    write_tree: WriteTree,
) -> None:
    """Structure swage does not understand is not structure it may rearrange.

    Sorting these by the first name inside them shuffled seven entries in
    `e3sm-unified`'s `run` and five in `magics`' `host` -- a diff swage would
    put on a recipe it was asked to reconcile, in a section it does not
    otherwise touch.
    """
    recipe = WITH_CONDITIONALS.replace(
        "    - python\n",
        "    - python\n"
        "    - if: is_abi3\n      then: zlib\n"
        '    - if: mpi != "nompi"\n      then: ${{ mpi }}\n',
    )
    rendered = _rendered(_plan_run(write_tree, recipe))
    assert rendered.index("    - if: is_abi3") < rendered.index(
        '    - if: mpi != "nompi"'
    )


def test_a_python_condition_compares_versions_rather_than_strings() -> None:
    """A bare `python < "3.13"` is a *string* comparison, and gets 3.9 wrong.

    A recipe's `if:` is evaluated by minijinja over the variant's value as
    text, so `<` compares character by character. rattler-build documents both
    halves of that: "the comparison is a string comparison done by
    minijinja... use the `match` function to compare versions."

    The failure is not hypothetical arithmetic -- `"3.9" < "3.13"` is False,
    because `'9' > '1'` -- and it is invisible while every minor conda-forge
    builds has two digits. swage shipped the bare form and rewrote a
    maintainer's `match(python, "<=3.12")` into it on a real feedstock, which
    is how it was found.
    """
    assert "3.9" > "3.13", "the string comparison this guards against"

    split = split_by_environment(
        "grpcio",
        declared(
            'grpcio>=1.33.1; python_version<"3.13"',
            'grpcio>=1.67.0; python_version>="3.13"',
        ),
        pythons=(9, 10, 11, 12, 13, 14),
    )

    conditions = [branch.condition for branch in split.branches]
    assert conditions == ['match(python, "<3.13")', 'match(python, ">=3.13")']
    for condition in conditions:
        assert "match(python, " in str(condition)
        assert '"3.13"' not in str(condition).replace('"<3.13"', "").replace(
            '">=3.13"', ""
        )
