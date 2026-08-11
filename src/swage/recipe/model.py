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

from dataclasses import dataclass, field

from .errors import RecipeError

__all__ = ["BlockContent", "Requirement"]


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
