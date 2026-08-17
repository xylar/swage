"""Reading a converted recipe's license rather than relaying the converter's.

The premise these rest on, established by running the converter over the
maintainer's 121 convertible v0 recipes: **the license is never lost.** It
comes through byte-identical 120 times and is corrected once, `BSD 3-Clause`
to `BSD-3-Clause`, which the converter reported. So "Could not patch
unrecognized license" never means something went missing -- it means CRM's
table did not recognise the string it left exactly as it found it.

That makes the message useless on its own, because it fires on
`MIT AND Apache-2.0`, which is impeccable, and on `Apache Software`, which is
not a license at all. swage checks the artifact instead.
"""

from __future__ import annotations

import pytest

from swage.migrate import license_problems, spdx_problems

#: Every license the maintainer's checkouts contain that is valid, plus the
#: two grammar corners no feedstock uses yet.
VALID = [
    "MIT",
    "Apache-2.0",
    "MIT AND Apache-2.0",
    "Apache-2.0 AND BSD-3-Clause AND PSF-2.0 AND MIT",
    "BSD-3-Clause OR Apache-2.0",
    "MIT AND BSD-3-Clause AND BSD-2-Clause AND Apache-2.0",
    "GPL-2.0-or-later",
    "(MIT OR Apache-2.0)",
    "LicenseRef-spherepack",
    "Apache-2.0 WITH LLVM-exception",
]

#: Every license in those checkouts that is not usable, with the word that
#: makes it unusable. All eight are real; none was invented for this test.
INVALID = [
    ("Apache 2.0", "Apache"),
    ("Apache Software", "Software"),
    ("BSD 3-Clause", "3-Clause"),
    ("BSD-3-Clause AND custom", "custom"),
    ("BSD-3-clause and GPL-3.0 and Public Domain", "and"),
    ("GPL-2.0-or-later and NetCDF and Zlib", "and"),
    ("PSF-2.0 and MIT", "and"),
    ("http://www.unidata.ucar.edu/software/netcdf/copyright.html", "unidata"),
]


@pytest.mark.parametrize("expression", VALID)
def test_a_valid_expression_says_nothing(expression: str) -> None:
    """Including every compound one, which is what started this.

    `MIT AND Apache-2.0` is one SPDX expression in one scalar. A v1 recipe
    keeps it that way -- `airflow` writes four licenses joined by `AND` on a
    single line -- so there is nothing to split into a list and nothing to
    report.
    """
    assert spdx_problems(expression) == ""


@pytest.mark.parametrize(("expression", "culprit"), INVALID)
def test_an_unusable_expression_says_which_word(expression: str, culprit: str) -> None:
    """Naming the word is the difference between a report and a chore."""
    problem = spdx_problems(expression)

    assert problem, expression
    assert culprit in problem


def test_a_lower_case_operator_is_reported_as_the_operator() -> None:
    """Three feedstocks write `and`, and SPDX's operators are upper-case.

    Reported as one finding about the operator rather than three about the
    licenses around it: `PSF-2.0 and MIT` is not two unknown licenses, it is
    two known ones joined wrongly, and saying otherwise sends the reader to
    check licenses that are fine.
    """
    problem = spdx_problems("PSF-2.0 and MIT")

    assert "upper-case" in problem
    assert "PSF-2.0`" not in problem


def test_a_licence_written_as_prose_is_one_finding_not_several() -> None:
    """`Apache Software` is one license badly written, not two unknown ones."""
    assert spdx_problems("Apache Software").count("is not a license") == 1


def test_no_correction_is_ever_suggested() -> None:
    """The lookup behind this is fuzzy and answers for prose too.

    Asked about `Apache 2.0` it offers `Apache-2.0` for the first word and
    `ZPL-2.0` for the second. Repeating that would be swage inventing a
    license for somebody, on the one field where being wrong is a legal
    problem rather than a build failure.
    """
    problem = spdx_problems("Apache 2.0")

    assert "ZPL-2.0" not in problem
    assert "is now" not in problem


def test_the_exception_after_with_is_not_looked_up_as_a_license() -> None:
    """`WITH` takes a license and an *exception*, from a different table.

    No feedstock writes one yet. The rule exists so the first that does is not
    stopped by swage for being correct.
    """
    assert spdx_problems("Apache-2.0 WITH LLVM-exception") == ""
    assert spdx_problems("Apache-2.0 WITH nonsense") == ""


RECIPE = """\
schema_version: 1

package:
  name: demo
  version: 1.0.0

about:
  license: {license}
  summary: demo
"""


def test_the_license_is_read_out_of_the_converted_recipe() -> None:
    assert license_problems(RECIPE.format(license="MIT AND Apache-2.0")) == ()

    problems = license_problems(RECIPE.format(license="Apache Software"))
    assert len(problems) == 1
    assert problems[0].startswith("about.license: ")


def test_every_output_s_license_is_read_too() -> None:
    """A multi-output recipe can license each package differently.

    `google-api-core` carries an `about` per output, so a check that read only
    the top-level one would pass a recipe whose second package says something
    unusable.
    """
    recipe = """\
schema_version: 1

outputs:
  - package:
      name: demo
    about:
      license: MIT
  - package:
      name: demo-extras
    about:
      license: Apache Software
"""

    problems = license_problems(recipe)

    assert len(problems) == 1
    assert problems[0].startswith("outputs[1].about.license: ")


def test_a_recipe_naming_no_license_is_not_swage_s_problem() -> None:
    """conda-forge's linter enforces that, and a conversion is a bad moment.

    The recipe was already breaking the rule before swage touched it, so
    raising it here would attach an old complaint to a new commit.
    """
    assert license_problems("schema_version: 1\npackage:\n  name: demo\n") == ()
