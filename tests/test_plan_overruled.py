"""Upstream declarations one noarch package cannot both satisfy (DESIGN.md 3.3.2).

`apache-beam`'s gcp metapackage is the fleet's case. Upstream asks it for
`google-apitools >=0.5.31,<0.5.32` below python 3.13 and `>=0.5.35` from 3.13,
and conda-forge builds one noarch package installed on every one of them --
so the two cannot both hold and no reconciliation produces a line.

`constraints` cannot resolve that: it intersects with what upstream declares,
and an empty intersection stays empty however it is narrowed.
`overruled_constraints` says which of upstream's bounds this one package
states, and G11 asks about it again at every update, so choosing one is never
the same as no longer reading the other.
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
from swage.recipe import read_recipe
from swage.upstream import parse_pyproject

from .conftest import WriteTree

PYTHON_MIN = PythonMin("3.10", ".ci_support/linux_64_.yaml")

INDEX = StaticPackageIndex.of("google-apitools", "python", "pip", "setuptools")

DEFAULTS = "trust: never\nrecipe_owned:\n  names: [python, pip]\n"

UPSTREAM = (
    '[build-system]\nrequires = ["setuptools"]\n\n'
    '[project]\nname = "demo"\nversion = "2.76.0"\n'
    "dependencies = [\n"
    '  "google-apitools >=0.5.31,<0.5.32; python_version < \\"3.13\\"",\n'
    '  "google-apitools >=0.5.35; python_version >= \\"3.13\\"",\n'
    "]\n"
)

#: What upstream looks like once it stops disagreeing with itself.
AGREED = (
    '[build-system]\nrequires = ["setuptools"]\n\n'
    '[project]\nname = "demo"\nversion = "2.77.0"\n'
    'dependencies = ["google-apitools >=0.5.35"]\n'
)

#: The recipe answers the split the only way a noarch output can read it --
#: with a condition on `python`, which resolves at build time to whichever
#: python the one artifact happens to be built with.
RECIPE_CONDITIONED = """schema_version: 1

package:
  name: demo
  version: 2.76.0

build:
  noarch: python

requirements:
  host:
    - python
    - pip
  run:
    - python
    - if: python < "3.13"
      then: google-apitools >=0.5.31,<0.5.32
      else: google-apitools >=0.5.35
"""

OVERRULED = """feedstock: demo
overruled_constraints:
  google-apitools:
    bound: ">=0.5.35"
    reason: >-
      upstream's cap below python 3.13 keeps its own test suites working
      against older releases, not because 0.5.35 is unsafe there
"""

PLAIN = "feedstock: demo\n"


def _config(write_tree: WriteTree, feedstock: str) -> ConfigTree:
    return load_config(
        write_tree({"defaults.yaml": DEFAULTS, "feedstocks/demo.yaml": feedstock})
    )


def _section(
    write_tree: WriteTree,
    *,
    feedstock: str,
    upstream: str = UPSTREAM,
) -> PlannedSection:
    recipe = read_recipe(RECIPE_CONDITIONED)
    return plan_section(
        recipe.blocks["/requirements/run"],
        parse_pyproject(upstream),
        _config(write_tree, feedstock).for_feedstock("demo"),
        NameResolver(Layered((MappingLayer("config/name-map.yaml", {}),)), INDEX),
        PYTHON_MIN,
    )


def test_without_an_entry_the_contradiction_stops_the_feedstock(
    write_tree: WriteTree,
) -> None:
    """And the message names the key that resolves it, not one that cannot."""
    with pytest.raises(PlanError) as raised:
        _section(write_tree, feedstock=PLAIN)

    message = str(raised.value)
    assert "contradictory upstream constraints for 'google-apitools'" in message
    assert "`overruled_constraints`" in message


def test_the_chosen_bound_becomes_one_plain_line(write_tree: WriteTree) -> None:
    section = _section(write_tree, feedstock=OVERRULED)

    lines = [item.text for item in section.requirements]
    assert "google-apitools >=0.5.35" in lines
    assert not [
        item for item in section.entries if isinstance(item, PlannedConditional)
    ]


def test_the_line_says_why_it_does_not_match_upstream(write_tree: WriteTree) -> None:
    """A conda-forge reader has no config file in front of them."""
    section = _section(write_tree, feedstock=OVERRULED)

    entry = next(
        item for item in section.requirements if item.text.startswith("google-apitools")
    )
    assert entry.comments == (
        "# upstream's bound varies by python; "
        "this package is built once for all of them",
    )


def test_the_entry_is_reported_for_re_checking(write_tree: WriteTree) -> None:
    """G11's input: overruling upstream is provisional, so it comes back."""
    section = _section(write_tree, feedstock=OVERRULED)

    assert [override.bound for override in section.overruled] == [">=0.5.35"]
    assert section.overrides == ()


def test_an_entry_upstream_no_longer_contradicts_is_reported(
    write_tree: WriteTree,
) -> None:
    """Otherwise it quietly overrides a version nobody is being asked about."""
    with pytest.raises(PlanError) as raised:
        _section(write_tree, feedstock=OVERRULED, upstream=AGREED)

    message = str(raised.value)
    assert "no longer contradict each other" in message
    assert "drop it in config/feedstocks/demo.yaml" in message
