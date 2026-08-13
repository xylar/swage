"""Tests for the requirements model (DESIGN.md 3.1)."""

from __future__ import annotations

import dataclasses

import pytest

from swage.recipe import BlockContent, RecipeError, Requirement


def test_a_requirement_carries_its_own_comments() -> None:
    """The point of the model: the comment belongs to the dependency."""
    requirement = Requirement(
        text="pandas >=2.3.3",
        comments=("# more restrictive for python >=3.14",),
    )
    assert requirement.text == "pandas >=2.3.3"
    assert requirement.comments == ("# more restrictive for python >=3.14",)


def test_reordering_carries_comments_along() -> None:
    """Reordering is a list operation, so nothing can be left behind.

    This is the failure that ruled out conda-recipe-manager: there, the comment
    stayed at its old index while its subject moved away.
    """
    content = BlockContent(
        entries=(
            Requirement("python >=3.10"),
            Requirement("pandas >=2.3.3", ("# more restrictive for python >=3.14",)),
            Requirement("requests >=2.32.0"),
        )
    )
    reordered = dataclasses.replace(
        content,
        entries=(
            content.requirements[1],
            content.requirements[0],
            content.requirements[2],
        ),
    )
    moved = reordered.requirements[0]
    assert moved.text == "pandas >=2.3.3"
    assert moved.comments == ("# more restrictive for python >=3.14",)
    assert reordered.requirements[1].comments == ()


def test_texts_drops_the_comments() -> None:
    content = BlockContent(
        entries=(
            Requirement("python >=3.10"),
            Requirement("pandas >=2.3.3", ("# a note",)),
        )
    )
    assert content.texts() == ("python >=3.10", "pandas >=2.3.3")


def test_trailing_comments_survive_as_their_own_thing() -> None:
    """An `# end` marker has no requirement to sit above."""
    content = BlockContent(
        entries=(Requirement("sqlalchemy >=2.0.36", ("# start pandas[sql-other]",)),),
        trailing_comments=("# end pandas[sql-other]",),
    )
    assert content.trailing_comments == ("# end pandas[sql-other]",)


def test_a_blank_line_is_an_allowed_comment_entry() -> None:
    assert Requirement("pandas", ("", "# note")).comments == ("", "# note")


def test_an_empty_requirement_is_rejected() -> None:
    with pytest.raises(RecipeError, match="cannot be empty"):
        Requirement("   ")


def test_a_requirement_keeping_its_list_marker_is_rejected() -> None:
    """Catches a reader that forgot to strip the dash before it emits `- - x`."""
    with pytest.raises(RecipeError, match="list marker"):
        Requirement("- pandas >=2.3.3")


def test_a_multiline_requirement_is_rejected() -> None:
    with pytest.raises(RecipeError, match="more than one line"):
        Requirement("pandas\n- numpy")


def test_a_comment_without_a_hash_is_rejected() -> None:
    """Otherwise it would be emitted as YAML and change what the recipe means."""
    with pytest.raises(RecipeError, match="not a comment"):
        Requirement("pandas", ("more restrictive",))


def test_a_multiline_comment_is_rejected() -> None:
    with pytest.raises(RecipeError, match="more than one line"):
        BlockContent(trailing_comments=("# one\n# two",))


def test_the_model_is_frozen() -> None:
    requirement = Requirement("pandas")
    with pytest.raises(dataclasses.FrozenInstanceError):
        requirement.text = "numpy"  # type: ignore[misc]
