"""What a requirements block is made of.

The model exists to solve one problem: a comment has to stay attached to the
dependency it is about. swage reorders dependencies to match upstream source
order (DESIGN.md 6), so a representation that attaches comments to *positions*
-- which is what every YAML library does, and what ruled out
conda-recipe-manager -- produces recipes where a note about `pandas` ends up
above something else. Here a comment belongs to a `Requirement`, and moving the
requirement moves the comment with it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from .errors import RecipeError

__all__ = [
    "BlockContent",
    "PythonTest",
    "Recipe",
    "RecipeOutput",
    "RecipeSource",
    "Requirement",
    "RequirementsBlock",
]


def _check_comments(comments: tuple[str, ...], where: str) -> None:
    for comment in comments:
        # "" is a blank line, which is worth being able to round-trip.
        if comment and not comment.startswith("#"):
            raise RecipeError(f"{where} is not a comment or a blank line: {comment!r}")
        if "\n" in comment:
            raise RecipeError(f"{where} spans more than one line: {comment!r}")


@dataclass(frozen=True)
class Requirement:
    """One dependency, plus the whole-line comments written above it.

    ``text`` is the dependency exactly as it appears after the ``- ``, e.g.
    ``pandas >=2.3.3`` or ``${{ pin_subpackage(name, exact=True) }}``. swage
    does not interpret it here; that is the planner's job.
    """

    text: str
    comments: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise RecipeError("a requirement cannot be empty")
        if self.text.startswith("- "):
            raise RecipeError(
                f"requirement text still has its list marker: {self.text!r}"
            )
        if "\n" in self.text:
            raise RecipeError(f"requirement spans more than one line: {self.text!r}")
        _check_comments(self.comments, "comment above a requirement")


@dataclass(frozen=True)
class BlockContent:
    """Everything inside one requirements section.

    ``trailing_comments`` are the comments after the last requirement and still
    inside the block. They are the reason this is not just a list: the ``# end``
    half of an embedded-extras marker pair (DESIGN.md 6) has no requirement to
    sit above, and dropping it would orphan its ``# start``.
    """

    requirements: tuple[Requirement, ...] = ()
    trailing_comments: tuple[str, ...] = field(default=())

    def __post_init__(self) -> None:
        _check_comments(self.trailing_comments, "trailing comment")

    def texts(self) -> tuple[str, ...]:
        """Just the dependencies, in order, without their comments."""
        return tuple(requirement.text for requirement in self.requirements)


@dataclass(frozen=True)
class RequirementsBlock:
    """One requirements section, and where it sits in the source.

    The line range covers the body of the block -- everything after the
    ``run:`` key line, up to but not including the next shallower line and any
    blank lines before it. swage rewrites a recipe by replacing exactly these
    ranges, which is what keeps the rest of the file byte-identical.
    """

    path: str
    section: str
    content: BlockContent
    item_indent: int
    first_line: int
    end_line: int


#: The entry conda-smithy looks for to decide that a `noarch: python` recipe
#: tests the latest Python as well as the minimum. The exact string, because
#: that is what `_python_tests_cover_latest` matches -- `"*"` inside a pin like
#: `${{ python_min }}.*` is not it (DESIGN.md 3.7).
LATEST = "*"


@dataclass(frozen=True)
class PythonTest:
    """One `tests:` entry that has a `python:` key, and its version matrix.

    Only the `python_version` list is modelled, because it is the only part
    swage writes. `imports`, `pip_check` and the rest are somebody's test and
    none of swage's business.

    A test entry with no `python:` key is not one of these at all, which is
    why the airflow providers' nineteen `script:` outputs never appear here --
    conda-smithy skips them too.
    """

    path: str
    #: What `python_version` says today, in order. Empty where the key is
    #: absent, which swage reads but does not write: inserting a key is a
    #: different operation from replacing one, and it is one recipe in 242.
    versions: tuple[str, ...] = ()
    present: bool = False
    item_indent: int = 0
    first_line: int = 0
    end_line: int = 0

    @property
    def covers_latest(self) -> bool:
        """Whether conda-smithy would consider this test complete."""
        return LATEST in self.versions


@dataclass(frozen=True)
class RecipeOutput:
    """One package built by the recipe.

    ``index`` is ``None`` for a recipe with no ``outputs:`` at all, which builds
    a single package from its top-level ``requirements:``.
    """

    index: int | None
    name: str | None
    name_expr: str | None
    blocks: Mapping[str, RequirementsBlock]
    #: `build.noarch`, which is what scopes the test-matrix rule (DESIGN.md 3.7)
    #: and is read per output because conda-smithy reads it per output.
    noarch: str | None = None
    python_tests: tuple[PythonTest, ...] = ()

    @property
    def caps_python(self) -> bool:
        """Whether `run` pins an upper bound on python.

        conda-smithy skips the whole test-matrix check when it does, because a
        capped Python makes a latest-Python test meaningless -- and 22 of the
        45 feedstocks that would otherwise need the edit are in exactly this
        state (DESIGN.md 3.7). Matched the way the linter matches it: the
        requirement's first token is `python` and the line contains a `<`.
        """
        run = self.blocks.get("run")
        if run is None:
            return False
        return any(
            text.split()[:1] == ["python"] and "<" in text
            for text in run.content.texts()
        )


@dataclass(frozen=True)
class RecipeSource:
    """One entry of the recipe's ``source``.

    This is where the upstream metadata swage reconciles against actually
    comes from. The recipe already names the archive and pins its hash, so
    swage reads both out of the pull request rather than asking PyPI what the
    latest release is -- the same reasoning as `python_min` (DESIGN.md 3.3.3).
    The version being fetched is then the version the bot bumped to by
    construction, and the pinned ``sha256`` makes the download verifiable
    against what this recipe claims to build.

    ``url`` is the resolved URL and ``url_expr`` the expression as written.
    ``url`` is None where the context did not supply every variable the
    expression referenced -- the same distinction `RecipeOutput.name` draws,
    and for the same reason: a half-substituted URL is worse than an admission
    that swage could not work one out.

    ``url_expr`` is None for a source that is not a URL at all, such as a
    ``git:`` source. Every one of the 226 source entries in the maintainer's
    checkouts carries both a ``url`` and a ``sha256``, so this does not occur
    today; the field exists so that a recipe with one keeps its sources in the
    order the file lists them rather than having entries vanish from under an
    index, which is what tells a multi-source recipe's archives apart.
    """

    url_expr: str | None = None
    url: str | None = None
    sha256: str | None = None
    #: Where rattler-build unpacks this archive, which is what distinguishes
    #: the three sdists `airflow-feedstock` builds from.
    target_directory: str | None = None


@dataclass(frozen=True)
class Recipe:
    """A parsed recipe.yaml, and the text it came from.

    The text is kept because it, not the parse, is what swage writes back:
    rendering replaces the requirements blocks in this string and leaves every
    other byte alone.
    """

    text: str
    context: Mapping[str, str]
    outputs: tuple[RecipeOutput, ...]
    #: In the order the recipe lists them (DESIGN.md 3.6).
    sources: tuple[RecipeSource, ...] = ()

    @property
    def python_tests(self) -> tuple[PythonTest, ...]:
        """Every python test in the recipe, whichever output it belongs to."""
        return tuple(test for output in self.outputs for test in output.python_tests)

    @property
    def blocks(self) -> Mapping[str, RequirementsBlock]:
        """Every requirements block in the recipe, keyed by path."""
        return {
            block.path: block
            for output in self.outputs
            for block in output.blocks.values()
        }
