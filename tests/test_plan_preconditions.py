"""The one refusal that happens before planning starts (DESIGN.md 3.3.5, 11).

An output that builds both an architecture-specific and a noarch package holds
two mutually exclusive alternatives of one dependency in a single list, and
swage would collapse them. A feedstock it cannot safely touch should say so in
a way that sends the maintainer straight to the reason, so the message is
asserted on.

**Everything else about build variants is not a precondition.** Three mpi
builds, or one build per Python, are ordinary conda-forge feedstocks whose
extra artifacts differ only in lines swage keeps verbatim.
"""

from __future__ import annotations

import pytest

from swage.plan import PlanError, check_plannable, check_preconditions
from swage.recipe import read_recipe

PLAIN = """\
schema_version: 1

context:
  name: demo

build:
  noarch: python

requirements:
  run:
    - python >=${{ python_min }}
"""


def test_an_ordinary_recipe_passes() -> None:
    check_preconditions(PLAIN)


def test_a_recipe_choosing_whether_to_be_noarch_is_refused() -> None:
    """Chosen rather than stated means one output building two packages."""
    recipe = PLAIN.replace("noarch: python", 'noarch: ${{ "python" if use_noarch }}')
    with pytest.raises(PlanError) as caught:
        check_preconditions(recipe)
    message = str(caught.value)
    assert "conditional noarch" in message
    assert "use_noarch" in message, "the message has to name the variable"
    assert "update this feedstock by hand" in message


def test_an_architecture_specific_recipe_passes() -> None:
    """Saying nothing about `noarch` settles the question as surely as saying it."""
    check_preconditions(PLAIN.replace("  noarch: python\n", "  number: 0\n"))


def test_a_feedstock_that_builds_several_variants_is_not_refused() -> None:
    """`libnetcdf` builds three mpi variants and is an ordinary feedstock.

    The variants differ in `${{ mpi }}` and in build strings -- lines swage
    keeps verbatim -- not in anything it reconciles. swage refused a recipe
    that mentioned a multi-valued key from its own
    `recipe/conda_build_config.yaml` for a while; that caught five feedstocks
    in the fleet, all for `mpi`, and missed the three building the same
    variants off conda-forge's global pinning.
    """
    recipe = PLAIN.replace(
        "  noarch: python\n",
        '  number: ${{ 100 if mpi == "nompi" else 0 }}\n',
    ).replace(
        "    - python >=${{ python_min }}\n",
        '    - if: mpi != "nompi"\n      then: ${{ mpi }}\n',
    )
    check_preconditions(recipe)


def test_a_conditional_noarch_in_an_output_is_refused() -> None:
    recipe = """\
schema_version: 1
outputs:
  - package:
      name: demo
    build:
      noarch: ${{ "python" if use_noarch }}
"""
    with pytest.raises(PlanError, match=r"outputs/0/build/noarch"):
        check_preconditions(recipe)


def test_a_conditional_build_section_is_refused() -> None:
    recipe = """\
schema_version: 1
outputs:
  - package:
      name: demo
    build:
      - if: use_noarch
        then:
          noarch: python
"""
    with pytest.raises(PlanError, match="conditional build section"):
        check_preconditions(recipe)


def test_a_plain_boolean_noarch_is_fine() -> None:
    check_preconditions(PLAIN.replace("noarch: python", "noarch: generic"))


def test_invalid_yaml_names_the_file() -> None:
    with pytest.raises(PlanError, match=r"recipe\.yaml: invalid YAML"):
        check_preconditions("a: [\n")


def test_a_v0_recipe_under_a_v1_filename_says_so() -> None:
    """Reporting it as invalid YAML sends the maintainer after the wrong bug.

    swage routes v0 by filename before reading (DESIGN.md 3.1), but a feedstock
    part-way through conversion defeats that -- apache-beam has v0 Jinja in a
    file named recipe.yaml.
    """
    with pytest.raises(PlanError) as caught:
        check_preconditions('{% set name = "apache-beam" %}\npackage:\n  name: x\n')
    message = str(caught.value)
    assert "v0 recipe despite the v1 filename" in message
    assert "invalid YAML" not in message


def test_genuinely_broken_yaml_is_still_reported_as_such() -> None:
    with pytest.raises(PlanError, match="invalid YAML"):
        check_preconditions("build:\n  noarch: [\n")


def test_an_output_that_is_not_noarch_python_is_named_in_the_refusal() -> None:
    """The message has to say which output, on a recipe that has several.

    `apache-beam` builds a compiled base package beside eleven noarch outputs,
    so "this recipe" would send the reader looking through twelve of them.
    """
    recipe = read_recipe(
        "outputs:\n"
        "  - package:\n      name: demo\n"
        "    requirements:\n      run:\n        - python\n"
        "  - package:\n      name: demo-with-async\n"
        "    build:\n      noarch: python\n"
        "    requirements:\n      run:\n        - python\n"
    )
    with pytest.raises(PlanError) as caught:
        check_plannable(recipe.outputs[0])
    assert "cannot yet plan /outputs/0" in str(caught.value)
    # The noarch output beside it is fine.
    check_plannable(recipe.outputs[1])


def test_a_conditional_in_a_planned_section_stops_the_output() -> None:
    """Reading one correctly is not the same as knowing what to do with it."""
    recipe = read_recipe(
        "build:\n  noarch: python\n"
        "requirements:\n  run:\n"
        "    - if: win\n      then: pywin32 >=306\n"
    )
    with pytest.raises(PlanError) as caught:
        check_plannable(recipe.outputs[0])
    message = str(caught.value)
    assert "cannot yet plan /requirements/run" in message
    assert "if: win" in message


def test_a_conditional_in_a_section_swage_does_not_plan_is_not_a_stop() -> None:
    """`build` is full of them, and swage writes nothing there (DESIGN.md 3.3.6.1)."""
    recipe = read_recipe(
        "build:\n  noarch: python\n"
        "requirements:\n  build:\n"
        "    - if: build_platform != target_platform\n      then: cross-python\n"
        "  run:\n    - python\n"
    )
    check_plannable(recipe.outputs[0])
