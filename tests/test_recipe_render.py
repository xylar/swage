"""Tests for the requirements block renderer (DESIGN.md 6)."""

from __future__ import annotations

import dataclasses

from swage.recipe import BlockContent, Requirement
from swage.recipe.render import render_block


def test_requirements_render_as_a_yaml_list() -> None:
    content = BlockContent(
        requirements=(Requirement("python >=3.10"), Requirement("pandas >=2.3.3"))
    )
    assert render_block(content, 4) == [
        "    - python >=3.10",
        "    - pandas >=2.3.3",
    ]


def test_comments_sit_above_their_requirement_at_the_same_indent() -> None:
    content = BlockContent(
        requirements=(
            Requirement("pandas >=2.3.3", ("# more restrictive for python >=3.14",)),
        )
    )
    assert render_block(content, 8) == [
        "        # more restrictive for python >=3.14",
        "        - pandas >=2.3.3",
    ]


def test_marker_pairs_line_up_around_the_block_they_wrap() -> None:
    """The convention from DESIGN.md 6, rendered end to end."""
    content = BlockContent(
        requirements=(
            Requirement("pyhive >=0.6.0"),
            Requirement("pure-sasl >=0.6.2", ("# start pyhive[hive_pure_sasl]",)),
            Requirement("thrift >=0.10.0"),
        ),
        trailing_comments=("# end pyhive[hive_pure_sasl]",),
    )
    assert render_block(content, 4) == [
        "    - pyhive >=0.6.0",
        "    # start pyhive[hive_pure_sasl]",
        "    - pure-sasl >=0.6.2",
        "    - thrift >=0.10.0",
        "    # end pyhive[hive_pure_sasl]",
    ]


def test_reordering_moves_a_comment_with_its_subject() -> None:
    """The property conda-recipe-manager could not provide (DESIGN.md 3.1)."""
    content = BlockContent(
        requirements=(
            Requirement("aiohttp >=3.14.0"),
            Requirement("mergedeep >=1.3.4"),
            Requirement("pandas >=2.3.3", ("# more restrictive for python >=3.14",)),
        )
    )
    reordered = dataclasses.replace(
        content, requirements=(content.requirements[2], *content.requirements[:2])
    )
    assert render_block(reordered, 4) == [
        "    # more restrictive for python >=3.14",
        "    - pandas >=2.3.3",
        "    - aiohttp >=3.14.0",
        "    - mergedeep >=1.3.4",
    ]


def test_a_blank_line_renders_with_no_trailing_whitespace() -> None:
    content = BlockContent(requirements=(Requirement("pandas", ("", "# after a gap")),))
    assert render_block(content, 4) == [
        "",
        "    # after a gap",
        "    - pandas",
    ]


def test_an_empty_block_renders_to_nothing() -> None:
    assert render_block(BlockContent(), 4) == []


def test_trailing_comments_survive_an_emptied_block() -> None:
    """Losing the `# end` here would orphan a marker in the recipe."""
    content = BlockContent(trailing_comments=("# end pandas[sql-other]",))
    assert render_block(content, 6) == ["      # end pandas[sql-other]"]
