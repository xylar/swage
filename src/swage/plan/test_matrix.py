"""Which python test matrices swage would complete (design-v1.md 3.7).

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

from .prose import fenced

__all__ = ["TestMatrix", "plan_test_matrices"]


@dataclass(frozen=True)
class TestMatrix:
    """One python test swage would complete, and what it would say."""

    #: Not a field -- it has no annotation, so the dataclass ignores it.
    #: pytest collects any class whose name starts with `Test`, and warns that
    #: it cannot because this one takes arguments. The name is the domain's
    #: (`test_matrix` is the config key and design-v1.md 3.7 the section), so the
    #: collector is what gives way.
    __test__ = False

    path: str
    was: tuple[str, ...]
    versions: tuple[str, ...]
    #: The output whose test this is, where the recipe names one. Carried for
    #: the sentence below rather than for the writer, which locates the block
    #: by `path`.
    output: str | None = None

    @property
    def reason(self) -> str:
        """Said on the pull request, so it stands alone (DESIGN.md §3.1).

        Which test, what it covered, what swage added and why, in that order,
        in terms the recipe uses: `python_version` and the output's name,
        never the path swage addresses the block by. Every recipe token is
        fenced, because `"*"` and `${{ python_min }}.*` are two bare asterisks
        that GitHub once paired into emphasis, and the one token the sentence
        exists to name was the one the reader could not see.
        """
        where = f" for {fenced(self.output)}" if self.output else ""
        was = ", ".join(fenced(version) for version in self.was)
        return (
            f'the python test{where} ran only on {was}; swage added `"*"` to '
            "its `python_version`, since this `noarch: python` package installs "
            "on every Python from that minimum up"
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
    # Named only where the recipe has `outputs:` at all. On a single-output
    # recipe the name is the package's own and saying it adds nothing: there
    # is one python test and the reader is looking at it.
    where = output.name if output.index is not None else None
    return [_for_test(test, where) for test in output.python_tests]


def _for_test(test: PythonTest, output: str | None = None) -> TestMatrix | None:
    if test.covers_latest or not test.present:
        # Already complete, or has no `python_version` key to replace. The
        # second still fails conda-smithy's check and swage still leaves it
        # alone: inserting a key is a different operation, and it is one
        # recipe in 242 (design-v1.md 3.7).
        return None
    return TestMatrix(
        path=test.path,
        was=test.versions,
        versions=(*test.versions, LATEST),
        output=output,
    )
