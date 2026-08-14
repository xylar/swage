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

    #: Not a field -- it has no annotation, so the dataclass ignores it.
    #: pytest collects any class whose name starts with `Test`, and warns that
    #: it cannot because this one takes arguments. The name is the domain's
    #: (`test_matrix` is the config key and DESIGN.md 3.7 the section), so the
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
        """Said on the pull request and in the report, so it stands alone.

        It reached a real pull request before anybody read it there, and it
        did not stand alone at all. It described the state swage *found* --
        "tests only `${{ python_min }}.*`" -- then a fact about noarch
        packages, and stopped. A maintainer looking at a diff that visibly
        changes the matrix got a sentence that never mentioned the change,
        ending on "the latest is tested too", which reads as a claim that it
        already was.

        So it now says the four things that reader needs, in the order they
        need them: which test, what it used to cover, what swage added to it,
        and why they are being asked to look.

        **Every term in it is something they can find in their own recipe.**
        The first attempt at this opened with `/tests/0/python`, which is how
        swage addresses the block and appears nowhere in the file being
        described -- the maintainer's words were "I have no idea what that
        means". A recipe has a `python_version` key, and where it has several
        outputs they have names, and those are what the sentence uses.

        **The clause break is load-bearing**, because a terminal report cuts a
        long detail at the first `; ` and counts the rest. The half before it
        has to be the half that identifies the problem.

        **Every recipe token is fenced, because this is published as
        markdown.** It went out unfenced once. The version this names is
        `${{ python_min }}.*` and the value it adds is `"*"`, so the sentence
        carried exactly two bare asterisks -- and GitHub paired them into
        emphasis, consumed both, and rendered `swage added ""` with the middle
        of the sentence in italics. The one token it exists to name was the
        one token the reader could not see. Fencing here is not house style;
        it is the difference between the sentence saying what it says and
        saying something else.
        """
        where = f" for `{self.output}`" if self.output else ""
        was = ", ".join(f"`{version}`" for version in self.was)
        return (
            f"the python test{where} ran only on {was}; this "
            "`noarch: python` package installs on every Python from that "
            'minimum up, so swage added `"*"` to its `python_version` -- held '
            "for a maintainer to confirm while `test_matrix` is `review`"
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
        # recipe in 242 (DESIGN.md 3.7).
        return None
    return TestMatrix(
        path=test.path,
        was=test.versions,
        versions=(*test.versions, LATEST),
        output=output,
    )
