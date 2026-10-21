"""Which test matrices swage would complete (design-v1.md 3.7).

conda-smithy's linter skips the whole check when `run` caps Python; swage does
not, because `"*"` resolves to the newest Python the cap allows (DESIGN.md §16).
"""

from __future__ import annotations

import re

import pytest

from swage.plan import plan_test_matrices
from swage.recipe import read_recipe

PYTHON_TEST = """  - python:
      imports:
        - demo
      pip_check: true
      python_version: ${{ python_min }}.*

"""


def recipe(
    run: str = "    - python >=${{ python_min }}",
    noarch: str = "  noarch: python",
    tests: str = PYTHON_TEST,
) -> str:
    return f"""context:
  python_min: '3.10'

package:
  name: demo
  version: '1.0'

build:
{noarch}

requirements:
  run:
{run}

tests:
{tests}
about:
  summary: demo
"""


def test_a_scalar_matrix_gains_the_latest_python() -> None:
    (matrix,) = plan_test_matrices(read_recipe(recipe()))

    assert matrix.path == "/tests/0/python"
    assert matrix.was == ("${{ python_min }}.*",)
    assert matrix.versions == ("${{ python_min }}.*", "*")


@pytest.mark.parametrize(
    "ceiling", ["<3.13", "<4.0", "<${{ python_over }}"], ids=["3.13", "4.0", "var"]
)
def test_a_capped_python_still_gains_the_latest(ceiling: str) -> None:
    """conda-smithy exempts these, and `openlineage-python`'s `<4.0` kept its
    test on the minimum for it. `"*"` is solved against `run`, so it tests the
    newest Python below the cap.
    """
    capped = recipe(run=f"    - python >=${{{{ python_min }}}},{ceiling}")

    (matrix,) = plan_test_matrices(read_recipe(capped))
    assert matrix.versions == ("${{ python_min }}.*", "*")


def test_a_recipe_that_is_not_noarch_python_is_out_of_scope() -> None:
    assert plan_test_matrices(read_recipe(recipe(noarch="  number: 0"))) == ()


def test_a_version_independent_recipe_gains_the_latest_python() -> None:
    """`pinecone`'s abi3 build: one package per platform installed on every python
    from the floor up, which conda-smithy's rule covers as it covers noarch.
    """
    abi3 = recipe(noarch="  python:\n    version_independent: true")

    (matrix,) = plan_test_matrices(read_recipe(abi3))
    assert matrix.versions == ("${{ python_min }}.*", "*")
    assert "this Python-version-independent package" in matrix.reason


def test_a_matrix_that_already_covers_the_latest_is_left_alone() -> None:
    """Most of the fleet: 93 of 334 blocks already carry the two-item list."""
    covered = recipe(
        tests="""  - python:
      python_version:
        - ${{ python_min }}.*
        - "*"

"""
    )

    assert plan_test_matrices(read_recipe(covered)) == ()


def test_a_test_with_no_version_key_is_not_offered_a_change() -> None:
    """It fails conda-smithy's check, and swage still does not write it.

    Inserting a key is a different operation from replacing one, and it is one
    recipe in 242 -- so swage leaves it rather than growing a second code path
    for a case it would exercise once.
    """
    bare = recipe(
        tests="""  - python:
      imports:
        - demo

"""
    )

    assert plan_test_matrices(read_recipe(bare)) == ()


def test_a_script_only_test_is_not_a_python_test() -> None:
    script = recipe(
        tests="""  - script:
      - python -c "import demo"

"""
    )

    assert plan_test_matrices(read_recipe(script)) == ()


def test_the_reason_reads_without_the_design_open() -> None:
    """It is printed in a report and published in a pull-request comment.

    Pinned on saying what swage *does*. The first version of this sentence
    described only the state swage found and stopped, and was published to a
    real pull request whose diff visibly changed the matrix -- so the one
    reader it was written for got no account of the change they were looking
    at.
    """
    (matrix,) = plan_test_matrices(read_recipe(recipe()))

    # Nothing in it that a maintainer cannot find in their own recipe. The
    # first version pointed at `/tests/0/python`, which is swage's way of
    # addressing the block and appears nowhere in the file being described.
    assert "/tests/" not in matrix.reason
    assert "the python test ran only on" in matrix.reason
    assert "`python_version`" in matrix.reason
    assert "`noarch: python`" in matrix.reason
    # The literal token that appears in the diff, so the sentence and the
    # change the reader is looking at name the same thing.
    assert 'swage added `"*"`' in matrix.reason
    # The key that stops this being held is in swage's config, not the
    # recipe, so it is the finding's remedy and not this sentence (§3.1).
    assert "test_matrix" not in matrix.reason
    assert not any(f"G{n}" in matrix.reason for n in range(1, 14))


def test_the_reason_survives_being_rendered_as_markdown() -> None:
    """It is published in a pull-request comment, which GitHub renders.

    This shipped: the sentence names `${{ python_min }}.*` and the `"*"` it
    adds, which put exactly two bare asterisks in one line, and GitHub paired
    them into emphasis. The published comment read `swage added ""` with the
    middle of the sentence italicized -- the token the sentence is *about*
    was the one thing the reader could not see.

    Pinned as the general property rather than on those two asterisks,
    because `_` in a name like `ruamel_yaml` pairs the same way, and the next
    token to break this will not be one anybody predicted.
    """
    (matrix,) = plan_test_matrices(read_recipe(recipe()))

    outside_code = re.sub(r"`[^`]*`", "", matrix.reason)
    assert "*" not in outside_code
    assert "_" not in outside_code


def test_the_reason_names_the_output_where_a_recipe_has_several() -> None:
    """One `tests:` block per output, so "the python test" needs saying which.

    Named by the output rather than by position, because the name is what the
    reader can search their own recipe for.
    """
    several = """context:
  python_min: '3.10'

package:
  name: demo
  version: '1.0'

outputs:
  - package:
      name: demo-core
    build:
      noarch: python
    requirements:
      run:
        - python >=${{ python_min }}
    tests:
      - python:
          imports:
            - demo
          python_version: ${{ python_min }}.*
"""
    (matrix,) = plan_test_matrices(read_recipe(several))

    assert "the python test for `demo-core` ran only on" in matrix.reason
