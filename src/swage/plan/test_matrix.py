"""Which python test matrices swage would complete (DESIGN.md §9.6).

A port of conda-smithy's `_python_tests_cover_latest`, not of the hint text: a
`run` requirement capping `python` with `<` exempts the recipe. Nothing here
writes.
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

    #: Not a field: no annotation, so the dataclass ignores it. It stops pytest
    #: collecting a class whose name starts with `Test`.
    __test__ = False

    path: str
    was: tuple[str, ...]
    versions: tuple[str, ...]
    #: The output whose test this is, where the recipe names one; for the
    #: sentence, not the writer.
    output: str | None = None

    @property
    def reason(self) -> str:
        """Said on the pull request, so it stands alone (DESIGN.md §3.1). Every
        recipe token is fenced.
        """
        where = f" for {fenced(self.output)}" if self.output else ""
        was = ", ".join(fenced(version) for version in self.was)
        return (
            f'the python test{where} ran only on {was}; swage added `"*"` to '
            "its `python_version`, since this `noarch: python` package installs "
            "on every Python from that minimum up"
        )


def plan_test_matrices(recipe: Recipe) -> tuple[TestMatrix, ...]:
    """Every python test in ``recipe`` that swage would complete."""
    return tuple(
        matrix
        for output in recipe.outputs
        for matrix in _for_output(output)
        if matrix is not None
    )


def _for_output(output: RecipeOutput) -> list[TestMatrix | None]:
    if output.noarch != "python" or output.caps_python:
        # Not in scope, or exempt: the same two questions conda-smithy asks
        # first.
        return []
    # Named only where the recipe has `outputs:` at all.
    where = output.name if output.index is not None else None
    return [_for_test(test, where) for test in output.python_tests]


def _for_test(test: PythonTest, output: str | None = None) -> TestMatrix | None:
    if test.covers_latest or not test.present:
        # Already complete, or has no `python_version` key to replace; inserting
        # a key is a different operation (v1 §3.7).
        return None
    return TestMatrix(
        path=test.path,
        was=test.versions,
        versions=(*test.versions, LATEST),
        output=output,
    )
