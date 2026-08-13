"""Which python test matrices swage would complete (DESIGN.md 3.7).

conda-forge's linter hints that a `noarch: python` recipe should test the
latest Python as well as the minimum. swage is already in the recipe when the
bot bumps a version, so it makes that change while it is there -- and never
opens a pull request for it alone.

**The rule comes from `_python_tests_cover_latest`, not from the hint text**,
and the difference is the whole of this module. The hint says to add `"*"`. The
source says the check is skipped entirely when any `run` requirement is
`python` with a `<` in it, because a capped Python makes a latest-Python test
meaningless. That is not a corner case: of the 45 feedstocks in the
maintainer's checkouts whose python test does not cover the latest, 22 cap
Python. A version of this built from the hint alone would have written a
latest-Python test into 22 feedstocks that deliberately do not support the
latest Python, and CI would have been entitled to fail every one.

Nothing here writes. It says what would change, and the trust ladder decides
whether that may merge unattended.
"""

from __future__ import annotations

from dataclasses import dataclass

from swage.recipe import Recipe
from swage.recipe.model import LATEST, PythonTest, RecipeOutput

__all__ = ["TestMatrix", "plan_test_matrices"]


@dataclass(frozen=True)
class TestMatrix:
    """One python test swage would complete, and what it would say."""

    path: str
    was: tuple[str, ...]
    versions: tuple[str, ...]

    @property
    def reason(self) -> str:
        """Said on the pull request and in the report, so it stands alone."""
        return (
            f"{self.path} tests only {', '.join(self.was)}; a noarch: python "
            "package installs on every Python from the minimum up, so the "
            "latest is tested too"
        )


def plan_test_matrices(recipe: Recipe) -> tuple[TestMatrix, ...]:
    """Every python test in ``recipe`` that swage would complete.

    Empty for a recipe that already covers the latest Python, caps Python, or
    builds something other than a noarch python package -- which between them
    are most of the fleet.
    """
    return tuple(
        matrix
        for output in recipe.outputs
        for matrix in _for_output(output)
        if matrix is not None
    )


def _for_output(output: RecipeOutput) -> list[TestMatrix | None]:
    if output.noarch != "python" or output.caps_python:
        # Not in scope, or exempt. Both are the ordinary case rather than the
        # exception, and conda-smithy asks the same two questions in the same
        # order before it looks at a single test.
        return []
    return [_for_test(test) for test in output.python_tests]


def _for_test(test: PythonTest) -> TestMatrix | None:
    if test.covers_latest or not test.present:
        # Already complete, or has no `python_version` key to replace. The
        # second still fails conda-smithy's check and swage still leaves it
        # alone: inserting a key is a different operation, and it is one
        # recipe in 242 (DESIGN.md 3.7).
        return None
    return TestMatrix(
        path=test.path, was=test.versions, versions=(*test.versions, LATEST)
    )
