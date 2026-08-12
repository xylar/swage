"""Build-variant refusal tests (DESIGN.md 3.3.5, 11).

A feedstock swage cannot safely touch should say so in a way that sends the
maintainer straight to the reason, so the message is asserted on -- it has to
name the variable.
"""

from __future__ import annotations

import pytest

from swage.plan import PlanError, check_preconditions

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

MARKUPSAFE_CONFIG = """\
use_noarch:
  - true
  - false
"""


def test_an_ordinary_recipe_passes() -> None:
    check_preconditions(PLAIN)


def test_a_recipe_choosing_whether_to_be_noarch_is_refused() -> None:
    """Chosen rather than stated means more than one artifact."""
    recipe = PLAIN.replace("noarch: python", 'noarch: ${{ "python" if use_noarch }}')
    with pytest.raises(PlanError) as caught:
        check_preconditions(recipe)
    assert "build-variant switch" in str(caught.value)
    assert "one noarch artifact at a time" in str(caught.value)


def test_a_feedstock_variable_the_recipe_uses_is_refused() -> None:
    """DESIGN.md 3.3.5's message names the variable so nobody has to guess."""
    recipe = PLAIN.replace("noarch: python", "noarch: python  # use_noarch")
    with pytest.raises(PlanError) as caught:
        check_preconditions(recipe, conda_build_config=MARKUPSAFE_CONFIG)
    message = str(caught.value)
    assert "use_noarch" in message
    assert "update this feedstock by hand" in message


def test_a_variant_the_recipe_never_mentions_is_ignored() -> None:
    """Defining a key is not the same as branching on it."""
    check_preconditions(PLAIN, conda_build_config=MARKUPSAFE_CONFIG)


def test_a_single_valued_config_key_is_not_a_variant() -> None:
    """One value multiplies nothing, so there is only ever one artifact."""
    check_preconditions(PLAIN, conda_build_config="use_noarch:\n  - true\n")


def test_conda_smithy_pins_are_not_feedstock_variants() -> None:
    """python_min has several values on every feedstock and means nothing here."""
    recipe = PLAIN + "\n# python_min is referenced above\n"
    check_preconditions(
        recipe, conda_build_config="python_min:\n  - '3.10'\n  - '3.11'\n"
    )


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


def test_an_unreadable_conda_build_config_is_an_error() -> None:
    with pytest.raises(PlanError, match=r"conda_build_config\.yaml: invalid YAML"):
        check_preconditions(PLAIN, conda_build_config="a: [\n")


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
