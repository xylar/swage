"""A bound in a recipe a build system reads for, which upstream cannot state.

`CMakeLists.txt` and `build/common.mk` say which packages a project links and
almost never which versions -- 64 `find_package` calls across the fleet's
cached archives produced two carrying one, both the same line. So silence from
those readers is not upstream declining to constrain a package, and reading it
as though it were makes every bound a recipe states look like drift.

`include-what-you-use` is where that showed. It holds `llvmdev` and `clangdev`
to one LLVM series through a `llvm_version` set once in `context`, its
`CMakeLists.txt` says `find_package(LLVM CONFIG REQUIRED)` and no more, and
the first plan swage made for it dropped both bounds -- widening the recipe to
any LLVM ever built while the finding count fell.
"""

from __future__ import annotations

from swage.config import ConfigTree, Layered, MappingLayer, load_config
from swage.mapping import NameResolver, StaticPackageIndex
from swage.plan import PlannedSection, plan_section
from swage.recipe import read_recipe
from swage.upstream import UpstreamMetadata, UpstreamRequirement

from .conftest import WriteTree

INDEX = StaticPackageIndex.of("llvmdev", "clangdev", "libclang-cpp")

DEFAULTS = "trust: never\nrecipe_owned:\n  names: [python, pip]\n"

RECIPE = """schema_version: 1

context:
  llvm_version: 22.*

package:
  name: demo
  version: 0.26

requirements:
  host:
    - llvmdev ${{ llvm_version }}
    - clangdev ${{ llvm_version }}
"""


def _upstream(
    *, states_versions: bool = False, specifier: str = ""
) -> UpstreamMetadata:
    """What a build-system reader hands the planner for this recipe."""
    return UpstreamMetadata(
        name="demo",
        version="0.26",
        conda_names=True,
        states_versions=states_versions,
        build_requires=(
            UpstreamRequirement(
                name="llvmdev",
                specifier=specifier,
                raw="find_package(LLVM CONFIG REQUIRED) in CMakeLists.txt",
            ),
            UpstreamRequirement(
                name="clangdev",
                raw="find_package(Clang CONFIG REQUIRED) in CMakeLists.txt",
            ),
        ),
        dependencies=(),
        declared_in="CMakeLists.txt + recipe/build.sh",
    )


def _config(write_tree: WriteTree) -> ConfigTree:
    return load_config(
        write_tree(
            {"defaults.yaml": DEFAULTS, "feedstocks/demo.yaml": "feedstock: demo\n"}
        )
    )


def _host(
    write_tree: WriteTree,
    upstream: UpstreamMetadata,
    recipe_text: str = RECIPE,
) -> PlannedSection:
    recipe = read_recipe(recipe_text)
    return plan_section(
        recipe.blocks["/requirements/host"],
        upstream,
        _config(write_tree).for_feedstock("demo"),
        NameResolver(Layered((MappingLayer("config/name-map.yaml", {}),)), INDEX),
        None,
        noarch=False,
        context=recipe.context,
    )


def test_a_bound_survives_a_reader_that_states_no_versions(
    write_tree: WriteTree,
) -> None:
    section = _host(write_tree, _upstream())

    assert [item.text for item in section.requirements] == [
        "llvmdev ${{ llvm_version }}",
        "clangdev ${{ llvm_version }}",
    ]


def test_the_same_declaration_from_python_metadata_reconciles_the_bound_away(
    write_tree: WriteTree,
) -> None:
    """The mutation this rule is worth having: only the flag separates them.

    An unbounded `install_requires` entry really is upstream saying it accepts
    any version, and a recipe narrowing that is drift swage reconciles.
    """
    section = _host(write_tree, _upstream(states_versions=True))

    assert [item.text for item in section.requirements] == ["llvmdev", "clangdev"]


def test_a_version_the_reader_did_read_reconciles_like_any_other(
    write_tree: WriteTree,
) -> None:
    """`find_package(LLVM 21)` is upstream speaking, and the rule is per line.

    `clangdev` beside it carries no version and keeps the recipe's bound, so
    one declaration having a specifier does not settle the other.
    """
    section = _host(write_tree, _upstream(specifier=">=21"))

    assert [item.text for item in section.requirements] == [
        "llvmdev >=21",
        "clangdev ${{ llvm_version }}",
    ]


def test_a_package_the_recipe_does_not_state_is_added_bare(
    write_tree: WriteTree,
) -> None:
    """There is no bound to keep, so the line is the name and nothing else."""
    section = _host(
        write_tree,
        _upstream(),
        recipe_text=RECIPE.replace("    - clangdev ${{ llvm_version }}\n", ""),
    )

    assert [item.text for item in section.requirements] == [
        "llvmdev ${{ llvm_version }}",
        "clangdev",
    ]


#: The mpi corner of the fleet states a package twice on purpose: a bare line
#: for the version pin conda-smithy applies from `conda_build_config.yaml`, and
#: a match spec beside it for the build pin that keeps an mpi build off a
#: nompi dependency. Six feedstocks write it, each with a comment saying so.
MPI_RECIPE = """schema_version: 1

package:
  name: demo
  version: 0.26

requirements:
  host:
    # need to list it twice to get version pinning from conda_build_config and
    # build pinning from ${{ mpi_prefix }}
    - llvmdev
    - llvmdev * ${{ mpi_prefix }}_*
"""


def test_a_package_stated_twice_keeps_both_lines_apart(
    write_tree: WriteTree,
) -> None:
    """The bare line is the one upstream declared; the match spec is not it.

    Answered by the bare name, the match spec is what comes back, so the plan
    writes it twice and the version pin the bare line exists for is gone.
    """
    section = _host(write_tree, _upstream(), recipe_text=MPI_RECIPE)

    assert [item.text for item in section.requirements] == [
        "llvmdev",
        "llvmdev * ${{ mpi_prefix }}_*",
        "clangdev",
    ]
