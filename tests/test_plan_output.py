"""One `Output` per recipe output, from recipe, `.ci_support` and config (DESIGN.md §9).

The build model is a property of each output, and everything it implies is
derived once, before any requirement is looked at. These tests check the
derivation against the shapes the fleet has: a noarch output with its floor in
the recipe, one built per platform, a compiled output beside noarch
metapackages, a staging output with no package name, and the two config shapes
that say what an output draws on.
"""

from __future__ import annotations

import pytest
from packaging.version import Version

from swage.config import ConfigTree, load_config
from swage.plan import Artifacts, PlanError, PythonMin, derive_outputs
from swage.plan.grid import TARGETS
from swage.recipe import read_recipe

from .conftest import WriteTree

FLOOR = PythonMin("3.10", ".ci_support/linux_64_.yaml")

NOARCH = """\
context:
  python_min: "3.11"
package:
  name: demo
  version: 1.0.0
build:
  noarch: python
requirements:
  host:
    - python ${{ python_min }}.*
    - pip
  run:
    - python >=${{ python_min }},<3.14
    - requests
"""

COMPILED = """\
package:
  name: demo
  version: 1.0.0
requirements:
  build:
    - if: build_platform != target_platform
      then:
        - python
        - cython
    - ${{ compiler("c") }}
  host:
    - python
    - cython
  run:
    - python
"""

#: `sqlalchemy`'s shape: a compiled base output beside a noarch metapackage,
#: and a staging output that builds no package of its own.
SPLIT = """\
context:
  name: demo
  version: 1.0.0
recipe:
  name: ${{ name }}-split
outputs:
  - staging:
      name: core-build
    requirements:
      host:
        - python
  - package:
      name: ${{ name }}
    requirements:
      run:
        - python
  - package:
      name: ${{ name }}-with-async
    build:
      noarch: python
    requirements:
      run:
        - python >=${{ python_min }}
"""

DEFAULTS = "trust: never\nrecipe_owned:\n  names: [python, pip]\n"


def _tree(write_tree: WriteTree, feedstock: str = "feedstock: demo\n") -> ConfigTree:
    return load_config(
        write_tree({"defaults.yaml": DEFAULTS, "feedstocks/demo.yaml": feedstock})
    )


# --- the build model ----------------------------------------------------------


def test_a_noarch_output_is_one_artifact_from_the_floor_to_the_ceiling(
    write_tree: WriteTree,
) -> None:
    """The floor from the recipe's own context, the ceiling from its `python` line."""
    recipe = read_recipe(NOARCH)
    config = _tree(write_tree).for_feedstock("demo")
    (output,) = derive_outputs(recipe, config, PythonMin("3.11", "recipe"))

    assert output.noarch
    assert output.artifacts is Artifacts.ONE
    assert output.pythons == (11, 12, 13)
    assert output.python_ceiling == Version("3.14")
    assert output.universe.python_min == PythonMin("3.11", "recipe")


def test_a_noarch_output_is_built_for_every_target_conda_forge_has(
    write_tree: WriteTree,
) -> None:
    """Never the rendered subset: a condition has to survive a platform being added."""
    recipe = read_recipe(NOARCH)
    config = _tree(write_tree).for_feedstock("demo")
    (output,) = derive_outputs(recipe, config, FLOOR, platforms=("linux",))

    assert output.targets == TARGETS


def test_more_than_one_rendered_platform_means_one_artifact_per_platform(
    write_tree: WriteTree,
) -> None:
    """`noarch_platforms`: the rendered platforms are the artifacts, and the targets."""
    recipe = read_recipe(NOARCH)
    config = _tree(write_tree).for_feedstock("demo")
    (output,) = derive_outputs(recipe, config, FLOOR, platforms=("linux", "win"))

    assert output.artifacts is Artifacts.PER_PLATFORM
    assert output.platforms == ("linux", "win")
    assert all(platform in ("linux", "win") for platform, _ in output.targets)


def test_a_compiled_output_is_built_once_per_cell_over_the_rendered_pythons(
    write_tree: WriteTree,
) -> None:
    recipe = read_recipe(COMPILED)
    config = _tree(write_tree).for_feedstock("demo")
    (output,) = derive_outputs(recipe, config, None, pythons=(12, 11, 12))

    assert not output.noarch
    assert output.artifacts is Artifacts.PER_CELL
    assert output.pythons == (11, 12)
    assert output.targets == TARGETS
    assert output.built_for == "python 3.11, 3.12"


def test_a_compiled_output_with_no_rendered_pythons_is_built_for_all_of_them(
    write_tree: WriteTree,
) -> None:
    """A feedstock conda-smithy has never rendered, or one with no python in it."""
    recipe = read_recipe(COMPILED)
    config = _tree(write_tree).for_feedstock("demo")
    (output,) = derive_outputs(recipe, config, None)

    assert len(output.pythons) > 20


def test_a_compiled_output_has_no_floor_and_that_is_not_an_error(
    write_tree: WriteTree,
) -> None:
    """`.ci_support` states one for the whole feedstock; an arch output ignores it."""
    recipe = read_recipe(COMPILED)
    config = _tree(write_tree).for_feedstock("demo")
    (output,) = derive_outputs(recipe, config, FLOOR)

    assert output.python_floor is None
    assert output.universe.python_min is None


# --- the floor a noarch output demands ----------------------------------------


def test_a_noarch_output_without_a_floor_is_stopped_where_it_is_planned(
    write_tree: WriteTree,
) -> None:
    """Derivation succeeds; asking for the universe is the stop, naming the output."""
    recipe = read_recipe(SPLIT)
    config = _tree(write_tree).for_feedstock("demo")
    staging, compiled, metapackage = derive_outputs(recipe, config, None)

    assert metapackage.pythons == ()
    with pytest.raises(PlanError) as raised:
        _ = metapackage.universe
    assert "cannot determine the python floor `demo-with-async`" in str(raised.value)
    assert "context.python_min" in str(raised.value)
    # The outputs that never needed one are not stopped.
    assert compiled.universe.artifacts is Artifacts.PER_CELL
    assert staging.universe.artifacts is Artifacts.PER_CELL


def test_built_for_says_the_range_a_noarch_output_is_installed_across(
    write_tree: WriteTree,
) -> None:
    recipe = read_recipe(NOARCH)
    config = _tree(write_tree).for_feedstock("demo")
    (output,) = derive_outputs(recipe, config, PythonMin("3.11", "recipe"))

    assert output.built_for == "python >=3.11,<3.14"


# --- names, sections and the cross-compilation block -------------------------


def test_a_staging_output_has_a_name_to_report_and_no_package_to_match(
    write_tree: WriteTree,
) -> None:
    """`gdal`'s `core-build`: requirements to plan, nothing for config to name."""
    recipe = read_recipe(SPLIT)
    config = _tree(write_tree).for_feedstock("demo")
    staging, compiled, _ = derive_outputs(recipe, config, None)

    assert staging.name == "core-build"
    assert staging.package is None
    assert compiled.name == compiled.package == "demo"


def test_only_the_planned_sections_are_carried_in_their_order(
    write_tree: WriteTree,
) -> None:
    """`host` then `run`; `build` and `run_constraints` are never planned."""
    recipe = read_recipe(COMPILED)
    config = _tree(write_tree).for_feedstock("demo")
    (output,) = derive_outputs(recipe, config, None)

    assert [block.section for block in output.sections] == ["host", "run"]
    (split,) = [
        output
        for output in derive_outputs(read_recipe(SPLIT), config, None)
        if output.package == "demo"
    ]
    assert [block.section for block in split.sections] == ["run"]


def test_a_build_block_for_another_target_platform_marks_the_output_cross_compiled(
    write_tree: WriteTree,
) -> None:
    config = _tree(write_tree).for_feedstock("demo")
    (compiled,) = derive_outputs(read_recipe(COMPILED), config, None)
    (noarch,) = derive_outputs(read_recipe(NOARCH), config, FLOOR)

    assert compiled.cross_compiled
    assert not noarch.cross_compiled


def test_the_pinned_variant_keys_are_carried_to_every_output(
    write_tree: WriteTree,
) -> None:
    recipe = read_recipe(SPLIT)
    config = _tree(write_tree).for_feedstock("demo")
    outputs = derive_outputs(recipe, config, None, pinned=frozenset({"numpy"}))

    assert all(output.pinned == frozenset({"numpy"}) for output in outputs)


# --- what an output draws on --------------------------------------------------


def test_an_output_config_does_not_name_takes_core_and_no_extras(
    write_tree: WriteTree,
) -> None:
    recipe = read_recipe(NOARCH)
    config = _tree(write_tree).for_feedstock("demo")
    (output,) = derive_outputs(recipe, config, FLOOR)

    assert output.core
    assert output.extras == ()
    assert dict(output.from_extras) == {}


def test_a_published_extra_is_a_metapackage_drawing_that_extra_alone(
    write_tree: WriteTree,
) -> None:
    """The airflow shape: `extras_as_outputs` names the output from the package."""
    recipe = read_recipe(SPLIT)
    config = _tree(
        write_tree,
        "feedstock: demo\n"
        "extras_as_outputs:\n"
        '  suffix: "{name}-with-{extra}"\n'
        "  supported: [async]\n",
    ).for_feedstock("demo")
    _, compiled, metapackage = derive_outputs(recipe, config, None)

    assert (metapackage.core, metapackage.extras) == (False, ("async",))
    assert (compiled.core, compiled.extras) == (True, ())


def test_an_output_folding_extras_in_keeps_their_order_and_its_selections(
    write_tree: WriteTree,
) -> None:
    """The google-cloud shape, with one extra split across outputs."""
    recipe = read_recipe(SPLIT)
    config = _tree(
        write_tree,
        "feedstock: demo\n"
        "outputs:\n"
        "  demo:\n"
        "    run:\n"
        "      core: true\n"
        "      extras: [grpc, aio]\n"
        "      from_extras:\n"
        "        export: [Zarr, pandas]\n",
    ).for_feedstock("demo")
    _, output, _ = derive_outputs(recipe, config, None)

    assert output.extras == ("grpc", "aio", "export")
    assert dict(output.from_extras) == {"export": frozenset({"zarr", "pandas"})}
