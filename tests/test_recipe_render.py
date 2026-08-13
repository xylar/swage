"""Tests for the requirements block renderer (DESIGN.md 6)."""

from __future__ import annotations

import dataclasses

from swage.recipe import BlockContent, Conditional, Requirement, read_recipe
from swage.recipe.render import render_block


def test_requirements_render_as_a_yaml_list() -> None:
    content = BlockContent(
        entries=(Requirement("python >=3.10"), Requirement("pandas >=2.3.3"))
    )
    assert render_block(content, 4) == [
        "    - python >=3.10",
        "    - pandas >=2.3.3",
    ]


def test_comments_sit_above_their_requirement_at_the_same_indent() -> None:
    content = BlockContent(
        entries=(
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
        entries=(
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
        entries=(
            Requirement("aiohttp >=3.14.0"),
            Requirement("mergedeep >=1.3.4"),
            Requirement("pandas >=2.3.3", ("# more restrictive for python >=3.14",)),
        )
    )
    reordered = dataclasses.replace(
        content, entries=(content.requirements[2], *content.requirements[:2])
    )
    assert render_block(reordered, 4) == [
        "    # more restrictive for python >=3.14",
        "    - pandas >=2.3.3",
        "    - aiohttp >=3.14.0",
        "    - mergedeep >=1.3.4",
    ]


def test_a_blank_line_renders_with_no_trailing_whitespace() -> None:
    content = BlockContent(entries=(Requirement("pandas", ("", "# after a gap")),))
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


def test_a_conditional_renders_in_the_layout_it_was_read_in() -> None:
    """Two spellings of one thing, and neither is swage's to normalize."""
    text = (
        "requirements:\n  host:\n"
        '    - if: mpi != "nompi"\n'
        "      then: ${{ mpi }}\n"
        "    - if: win\n"
        "      then:\n"
        "        - m2-unzip\n"
        "      else:\n"
        "        - unzip\n"
    )
    recipe = read_recipe(text)
    block = recipe.blocks["/requirements/host"]
    assert render_block(block.content, block.item_indent) == text.split("\n")[2:-1]


def test_a_conditional_swage_writes_uses_the_fleet_layout() -> None:
    """Defaults for an entry with no source to preserve: `then:` at +2, items +4."""
    content = BlockContent(
        entries=(
            Conditional(
                condition='python < "3.13"',
                then=(Requirement("pandas >=2.1.2"), Requirement("numpy >=1.26")),
            ),
        )
    )
    assert render_block(content, 4) == [
        '    - if: python < "3.13"',
        "      then:",
        "        - pandas >=2.1.2",
        "        - numpy >=1.26",
    ]


def test_an_inline_branch_swage_writes_stays_on_one_line() -> None:
    content = BlockContent(
        entries=(
            Conditional(
                condition="win",
                then=(Requirement("pywin32 >=306"),),
                then_inline=True,
                comments=("# windows only",),
            ),
        )
    )
    assert render_block(content, 4) == [
        "    # windows only",
        "    - if: win",
        "      then: pywin32 >=306",
    ]


def test_trailing_whitespace_after_a_branch_key_is_dropped() -> None:
    """The one thing the fleet sweep found that does not round-trip.

    `pendulum`'s conversion branch writes `then: ` with a trailing space and a
    list underneath. Nothing distinguishes that from `then:` once read, and
    swage renders the contents of a requirements section rather than preserving
    them (DESIGN.md 6), so the space goes. It is a one-character normalization
    inside a block swage owns, on one recipe in 319.
    """
    text = (
        "requirements:\n  build:\n"
        "    - if: build_platform != target_platform\n"
        "      then: \n"
        "        - python\n"
    )
    recipe = read_recipe(text)
    block = recipe.blocks["/requirements/build"]
    assert render_block(block.content, block.item_indent) == [
        "    - if: build_platform != target_platform",
        "      then:",
        "        - python",
    ]
