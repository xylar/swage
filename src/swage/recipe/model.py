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
    "Conditional",
    "Entry",
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
class Conditional:
    """One ``if:`` / ``then:`` / ``else:`` entry in a requirements section.

    This is how a v1 recipe says that a requirement belongs to some builds and
    not others -- a platform, an mpi variant, a Python (DESIGN.md 3.1). It is
    the recipe grammar rather than a shape a few feedstocks happen to use, so
    the reader models it instead of refusing it.

    ``otherwise`` is ``None`` where the entry has no ``else:`` at all, which is
    almost all of them: 446 of the fleet's 456 conditional entries are
    ``if``/``then`` alone.

    Branches hold *entries*, not requirements, because a branch can hold
    another conditional. That is one entry in the whole fleet -- `apache-beam`
    nests a Python check inside its cross-compilation block -- and modeling it
    costs a recursive call where refusing it would cost a feedstock.

    The layout fields exist so that reading and writing a recipe swage did not
    author is byte-exact. `then: foo` and a `then:` with a list under it are
    the same content, and swage must not turn one into the other on a recipe it
    was only asked to reconcile. Where swage writes a conditional of its own,
    the defaults are what the fleet overwhelmingly uses.
    """

    condition: str
    then: tuple[Entry, ...] = ()
    otherwise: tuple[Entry, ...] | None = None
    comments: tuple[str, ...] = ()
    #: Indent of `then:`/`else:` relative to the entry's `- `, and of a
    #: branch's own items relative to the same. 735 of 735 and 991 of 1002 in
    #: the fleet.
    key_offset: int = 2
    item_offset: int = 4
    #: `then: pywin32` rather than a list underneath it.
    then_inline: bool = False
    otherwise_inline: bool = False

    def __post_init__(self) -> None:
        if not self.condition.strip():
            raise RecipeError("a conditional requirement needs a condition")
        if "\n" in self.condition:
            raise RecipeError(f"condition spans more than one line: {self.condition!r}")
        _check_comments(self.comments, "comment above a conditional requirement")
        for inline, branch in (
            (self.then_inline, self.then),
            (self.otherwise_inline, self.otherwise or ()),
        ):
            if inline and len(branch) != 1:
                raise RecipeError(
                    f"an inline branch holds exactly one requirement, not {len(branch)}"
                )
            if inline and not isinstance(branch[0], Requirement):
                raise RecipeError("an inline branch cannot hold another conditional")


#: What a requirements section is a list of: a requirement, or a condition with
#: requirements under it.
Entry = Requirement | Conditional


def _requirements(entries: tuple[Entry, ...]) -> tuple[Requirement, ...]:
    return tuple(entry for entry in entries if isinstance(entry, Requirement))


@dataclass(frozen=True)
class BlockContent:
    """Everything inside one requirements section.

    ``trailing_comments`` are the comments after the last entry and still
    inside the block. They are the reason this is not just a list: the ``# end``
    half of an embedded-extras marker pair (DESIGN.md 6) has no requirement to
    sit above, and dropping it would orphan its ``# start``.
    """

    entries: tuple[Entry, ...] = ()
    trailing_comments: tuple[str, ...] = field(default=())

    def __post_init__(self) -> None:
        _check_comments(self.trailing_comments, "trailing comment")

    @property
    def requirements(self) -> tuple[Requirement, ...]:
        """The unconditional entries, in order.

        **Not everything in the section**, which is the point of the name: a
        caller that reasons about requirements one at a time -- ordering,
        attribution, the gates -- has nothing correct to say about a
        conditional yet, so it sees them and `conditionals` is where they are.
        A section swage plans is checked for conditionals first and refused
        while that is true (DESIGN.md 3.3.1.1), so no caller silently drops
        one.
        """
        return _requirements(self.entries)

    @property
    def conditionals(self) -> tuple[Conditional, ...]:
        return tuple(entry for entry in self.entries if isinstance(entry, Conditional))

    def texts(self) -> tuple[str, ...]:
        """Just the unconditional dependencies, in order, without comments."""
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

    Only the `python_version` list is modeled, because it is the only part
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
    #: `staging.name`, for an output that builds something later outputs
    #: consume rather than a package of its own. `gdal`'s `core-build` is one,
    #: and it has requirements swage plans like any other -- but no
    #: `package.name`, so it is what a report has to call it by.
    staging: str | None = None
    #: `build.noarch`, which is what scopes the test-matrix rule (DESIGN.md 3.7)
    #: and is read per output because conda-smithy reads it per output.
    noarch: str | None = None
    python_tests: tuple[PythonTest, ...] = ()

    @property
    def label(self) -> str:
        """What a report calls this output, empty where the recipe names none.

        The package it builds, or what it stages. Distinct from `name`, which
        is the *package* name and is what config entries and output roles are
        matched against -- a staging output has requirements to talk about and
        no package to match.
        """
        return self.name or self.staging or ""

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
