"""A package conda-forge's global pinning already states a version for.

conda-build reads that pin out of `conda_build_config.yaml` by matching a
`host` entry against a variant key, and an entry carrying a version does not
match one. So a bound swage writes there does not tighten the pin -- it
*replaces* it, taking the package out of the build matrix and standing one
recipe's reading of upstream in for a decision conda-forge makes fleet-wide.

The fleet says so plainly. Of 618 lines naming a variant key with no build
string, 609 carry no version, and both real exceptions -- `esmpy`'s
`numpy >=1.19,<3` and `apache-beam`'s `numpy >=1.14.3,<2.5.0` -- are `run`
lines, with the `host` line bare above them. The rule is about the section,
not about the package: this is as true of `numpy` in a python feedstock as of
`libnetcdf` in a compiled one.
"""

from __future__ import annotations

from swage.config import ConfigTree, Layered, MappingLayer, load_config
from swage.forge.feedstock import _variant_pins
from swage.mapping import NameResolver, StaticPackageIndex
from swage.plan import PlannedSection, PythonMin, plan_section
from swage.recipe import read_recipe
from swage.upstream import parse_pyproject

from .conftest import WriteTree

PYTHON_MIN = PythonMin("3.10", "recipe")

INDEX = StaticPackageIndex.of("numpy", "python", "pip", "requests", "setuptools")

DEFAULTS = "trust: never\nrecipe_owned:\n  names: [python, pip]\n"

#: A compiled python package's shape: `numpy` builds it *and* runs it, with
#: the same bound in both places, which is why `host` and `run` both name it.
UPSTREAM = (
    '[build-system]\nrequires = ["setuptools", "numpy >=1.19,<3"]\n\n'
    '[project]\nname = "demo"\nversion = "2.0.0"\n'
    'dependencies = ["numpy >=1.19,<3", "requests >=2.21"]\n'
)

RECIPE = """schema_version: 1

package:
  name: demo
  version: 2.0.0

requirements:
  host:
    - python
    - pip
    - numpy
  run:
    - python
    - numpy
    - requests
"""

#: What conda-smithy renders for a feedstock whose `numpy` is pinned. The
#: keys that name no package are here on purpose -- swage keeps them.
CI_SUPPORT = """channel_sources:
- conda-forge
docker_image:
- quay.io/condaforge/linux-anvil-x86_64:alma9
netcdf_fortran:
- '4.6'
numpy:
- '2'
target_platform:
- linux-64
zip_keys:
- - c_compiler_version
  - cxx_compiler_version
"""


def _config(write_tree: WriteTree) -> ConfigTree:
    return load_config(
        write_tree(
            {"defaults.yaml": DEFAULTS, "feedstocks/demo.yaml": "feedstock: demo\n"}
        )
    )


def _section(
    write_tree: WriteTree,
    path: str,
    *,
    pinned: frozenset[str] = frozenset({"numpy"}),
    recipe_text: str = RECIPE,
) -> PlannedSection:
    recipe = read_recipe(recipe_text)
    return plan_section(
        recipe.blocks[path],
        parse_pyproject(UPSTREAM),
        _config(write_tree).for_feedstock("demo"),
        NameResolver(Layered((MappingLayer("config/name-map.yaml", {}),)), INDEX),
        PYTHON_MIN,
        pinned=pinned,
    )


# --- reading the variant keys ----------------------------------------------


def test_a_variant_key_is_read_as_the_package_name_it_names() -> None:
    """conda-build matches `netcdf_fortran` against the package `netcdf-fortran`."""
    assert "netcdf-fortran" in _variant_pins(CI_SUPPORT)
    assert "numpy" in _variant_pins(CI_SUPPORT)


def test_the_keys_that_name_no_package_are_kept_and_are_harmless() -> None:
    """The set is only ever asked whether a package swage would bound is in it.

    An exclusion list of keys naming no package would need maintaining as
    conda-forge adds them, and nothing depends on a package called
    `zip_keys`.
    """
    pins = _variant_pins(CI_SUPPORT)
    assert {"zip-keys", "docker-image", "channel-sources"} <= pins


def test_a_config_that_is_not_a_mapping_pins_nothing() -> None:
    assert _variant_pins("- just\n- a\n- list\n") == frozenset()


# --- what the rule does to a plan ------------------------------------------


def test_a_pinned_host_line_takes_no_bound_from_upstream(
    write_tree: WriteTree,
) -> None:
    section = _section(write_tree, "/requirements/host")

    assert "numpy" in [item.text for item in section.requirements]
    assert "numpy >=1.19,<3" not in [item.text for item in section.requirements]


def test_the_same_package_keeps_upstreams_bound_in_run(
    write_tree: WriteTree,
) -> None:
    """`esmpy` and `apache-beam` both write it exactly this way."""
    section = _section(write_tree, "/requirements/run")

    assert "numpy >=1.19,<3" in [item.text for item in section.requirements]


def test_a_package_the_pinning_does_not_name_is_bounded_as_usual(
    write_tree: WriteTree,
) -> None:
    """The rule reaches the pinning's packages and stops there."""
    section = _section(write_tree, "/requirements/host", pinned=frozenset())

    assert "numpy >=1.19,<3" in [item.text for item in section.requirements]


def test_a_pinned_host_line_is_still_explained(write_tree: WriteTree) -> None:
    """Dropping the bound must not drop the provenance with it, or G1 stops."""
    section = _section(write_tree, "/requirements/host")

    planned = {item.text: item for item in section.requirements}
    assert planned["numpy"].provenance.origin.startswith("upstream")
    assert section.unexplained == ()


# --- the pair a recipe may state on purpose --------------------------------

PAIR = """schema_version: 1

package:
  name: demo
  version: 2.0.0

requirements:
  host:
    - python
    - pip
    - numpy
    - numpy >=1.19,<3
  run:
    - python
    - requests
"""


def test_a_bare_and_a_bounded_line_on_a_pinned_package_are_two_requirements(
    write_tree: WriteTree,
) -> None:
    """The bare line takes the pin; the bounded one asserts the pin is in range.

    Keyed alike the second would read as a constraint change to the first and
    swage would render one line, exactly as it did to the build-string pair
    before DESIGN.md 3.3.6 said otherwise.
    """
    section = _section(write_tree, "/requirements/host", recipe_text=PAIR)

    texts = [item.text for item in section.requirements]
    assert "numpy" in texts
    assert "numpy >=1.19,<3" in texts


def test_neither_line_of_the_pair_is_reported_as_unexplained(
    write_tree: WriteTree,
) -> None:
    """Both answer to the same upstream declaration, so both are accounted for."""
    section = _section(write_tree, "/requirements/host", recipe_text=PAIR)

    assert section.unexplained == ()
