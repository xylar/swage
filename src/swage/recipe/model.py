"""What a requirements block is made of (DESIGN.md §7).

A comment belongs to a `Requirement`, not to a position, so reordering moves the
comment with the requirement (v1 §6.1).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from .errors import RecipeError

__all__ = [
    "BlockContent",
    "Conditional",
    "Entry",
    "EntryPoints",
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
    """One dependency, plus the whole-line comments written above it. ``text``
    is the dependency exactly as it appears after the ``- ``.
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
    """One ``if:`` / ``then:`` / ``else:`` entry in a requirements section
    (v1 §3.1).

    ``otherwise`` is ``None`` where the entry has no ``else:``. Branches hold
    entries, not requirements, because a branch can hold another conditional.
    The layout fields keep reading and writing byte-exact; where swage writes
    a conditional of its own, the defaults are the fleet's usual layout.
    """

    condition: str
    then: tuple[Entry, ...] = ()
    otherwise: tuple[Entry, ...] | None = None
    comments: tuple[str, ...] = ()
    #: Indent of `then:`/`else:` relative to the entry's `- `, and of a branch's
    #: own items relative to the same.
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
    """Everything inside one requirements section. ``trailing_comments`` are the
    comments after the last entry and still inside the block, where the
    `# end` half of a marker pair lands (v1 §6).
    """

    entries: tuple[Entry, ...] = ()
    trailing_comments: tuple[str, ...] = field(default=())

    def __post_init__(self) -> None:
        _check_comments(self.trailing_comments, "trailing comment")

    @property
    def requirements(self) -> tuple[Requirement, ...]:
        """The unconditional entries, in order; `conditionals` is where the rest
        are.
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
    """One requirements section, and where it sits in the source: the body of
    the block, which the writer replaces exactly.
    """

    path: str
    section: str
    content: BlockContent
    item_indent: int
    first_line: int
    end_line: int


#: The entry conda-smithy looks for to decide that a `noarch: python` recipe
#: tests the latest Python: the exact string (v1 §3.7).
LATEST = "*"


@dataclass(frozen=True)
class PythonTest:
    """One `tests:` entry that has a `python:` key, and its version matrix.
    Only `python_version` is modeled, because it is the only part swage
    writes.
    """

    path: str
    #: What `python_version` says today, in order. Empty where the key is
    #: absent, which swage reads but does not write.
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
class EntryPoints:
    """An output's `build.python.entry_points` list, and where it is
    (DESIGN.md §9.6).

    Only a list already there is modeled, each item as the recipe writes it.
    ``conditional`` says the list holds an `if:` entry, which swage never
    rewrites.
    """

    path: str
    items: tuple[str, ...] = ()
    conditional: bool = False
    #: Both kept, because YAML lets the items sit level with the key.
    key_indent: int = 0
    item_indent: int = 0
    first_line: int = 0
    end_line: int = 0


@dataclass(frozen=True)
class RecipeOutput:
    """One package built by the recipe. ``index`` is ``None`` for a recipe with
    no ``outputs:`` at all.
    """

    index: int | None
    name: str | None
    name_expr: str | None
    blocks: Mapping[str, RequirementsBlock]
    #: `staging.name`, for an output that builds something later outputs consume
    #: rather than a package of its own.
    staging: str | None = None
    #: `build.noarch`, which is what scopes the test-matrix rule (design-v1.md 3.7)
    #: and is read per output because conda-smithy reads it per output.
    noarch: str | None = None
    python_tests: tuple[PythonTest, ...] = ()
    #: `build.python.entry_points`, where the output declares the key at all.
    entry_points: EntryPoints | None = None

    @property
    def label(self) -> str:
        """What a report calls this output: the package it builds, or what it
        stages. Distinct from `name`, which config matches against.
        """
        return self.name or self.staging or ""

    @property
    def caps_python(self) -> bool:
        """Whether `run` pins an upper bound on python, matched the way
        conda-smithy's linter matches it (v1 §3.7).
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

    ``url`` is the resolved URL and ``url_expr`` the expression as written;
    ``url`` is None where the context did not supply every variable.
    ``sha256`` is resolved the same way. ``url_expr`` is None for a source
    that is not a URL at all, kept so the sources keep their order.
    """

    url_expr: str | None = None
    url: str | None = None
    sha256: str | None = None
    #: Where rattler-build unpacks this archive, which is what tells a
    #: several-source recipe's archives apart.
    target_directory: str | None = None


@dataclass(frozen=True)
class Recipe:
    """A parsed recipe.yaml, and the text it came from, which is what swage
    writes back.
    """

    text: str
    context: Mapping[str, str]
    outputs: tuple[RecipeOutput, ...]
    #: In the order the recipe lists them (design-v1.md 3.6).
    sources: tuple[RecipeSource, ...] = ()

    @property
    def entry_points(self) -> Mapping[str, EntryPoints]:
        """Every output's entry-point list, keyed by path, for the writer."""
        return {
            output.entry_points.path: output.entry_points
            for output in self.outputs
            if output.entry_points is not None
        }

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
