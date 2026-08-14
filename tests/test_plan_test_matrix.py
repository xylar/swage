"""Which test matrices swage would complete (DESIGN.md 3.7).

The test that matters most here is the one asserting swage does *nothing*. The
hint conda-forge prints says "add `"*"`"; the linter's own source skips the
whole check when `run` caps Python, and 22 of the 45 affected feedstocks are
in exactly that state. Building this from the hint would have written a
latest-Python test into every one of them.
"""

from __future__ import annotations

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


def test_a_capped_python_is_left_entirely_alone() -> None:
    """The rule conda-smithy applies and the hint text does not mention.

    A capped Python makes a latest-Python test meaningless, so the linter
    skips the check -- and swage adding the entry anyway would demand the
    package work on a Python the recipe says it does not support. Half the
    feedstocks that would otherwise be edited are in this state.
    """
    capped = recipe(run="    - python >=${{ python_min }},<3.13")

    assert plan_test_matrices(read_recipe(capped)) == ()


def test_a_recipe_that_is_not_noarch_python_is_out_of_scope() -> None:
    assert plan_test_matrices(read_recipe(recipe(noarch="  number: 0"))) == ()


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

    assert "noarch: python" in matrix.reason
    # The literal token that appears in the diff, so the sentence and the
    # change the reader is looking at name the same thing.
    assert 'swage added "*"' in matrix.reason
    # A real key in a real file, which is what can be changed to stop this
    # being held.
    assert "test_matrix is `review`" in matrix.reason
    assert not any(f"G{n}" in matrix.reason for n in range(1, 14))
