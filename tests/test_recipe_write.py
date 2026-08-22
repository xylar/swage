"""Tests for writing recipes back (DESIGN.md 3.1, 6).

The claims being tested are the ones the whole approach rests on: reading and
writing are inverses, a write touches only the requirements block it was aimed
at, and running swage twice changes nothing the second time.
"""

from __future__ import annotations

import dataclasses
import difflib
from pathlib import Path

import pytest

from swage.recipe import (
    BlockContent,
    RecipeError,
    Requirement,
    read_recipe,
    render_recipe,
)

from .conftest import REPO_ROOT

CORPUS = REPO_ROOT / "tests" / "corpus"
AIRFLOW = CORPUS / "airflow-providers"

RECIPES = sorted(CORPUS.rglob("*recipe.yaml"))


def changed_line_numbers(before: str, after: str) -> set[int]:
    """Line numbers in `before` that a diff to `after` touches."""
    before_lines = before.split("\n")
    after_lines = after.split("\n")
    matcher = difflib.SequenceMatcher(None, before_lines, after_lines, autojunk=False)
    touched: set[int] = set()
    for tag, i1, i2, _j1, _j2 in matcher.get_opcodes():
        if tag != "equal":
            touched.update(range(i1, max(i2, i1 + 1)))
    return touched


@pytest.mark.parametrize("path", RECIPES, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_rendering_every_block_reproduces_the_file_exactly(path: Path) -> None:
    """Read and write are inverses, byte for byte, on real recipes.

    This is what makes gate G7 -- "swage's rendering is identical to what is
    already in the PR" -- a statement about swage's plan rather than about its
    formatter.
    """
    text = path.read_text(encoding="utf-8")
    recipe = read_recipe(text, str(path))
    rewritten = render_recipe(
        recipe, {path_: block.content for path_, block in recipe.blocks.items()}
    )
    assert rewritten == text


@pytest.mark.parametrize("path", RECIPES, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_rendering_no_changes_returns_the_original(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert render_recipe(read_recipe(text, str(path))) == text


@pytest.mark.parametrize("path", RECIPES, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_a_write_touches_only_the_block_it_was_aimed_at(path: Path) -> None:
    """Gate G5, made structural: nothing outside a requirements block can move."""
    text = path.read_text(encoding="utf-8")
    recipe = read_recipe(text, str(path))
    for block_path, block in recipe.blocks.items():
        changed = dataclasses.replace(
            block.content,
            entries=(
                *block.content.requirements,
                Requirement("swage-test-dependency >=1.0"),
            ),
        )
        rewritten = render_recipe(recipe, {block_path: changed})
        touched = changed_line_numbers(text, rewritten)
        assert touched, f"{block_path} produced no diff at all"
        assert touched <= set(range(block.first_line, block.end_line + 1)), (
            f"{block_path} changed lines outside its own range"
        )


@pytest.mark.parametrize("path", RECIPES, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_writing_is_idempotent(path: Path) -> None:
    """DESIGN.md 6: running swage on its own output must be a no-op."""
    text = path.read_text(encoding="utf-8")
    recipe = read_recipe(text, str(path))
    once = render_recipe(
        recipe, {p: block.content for p, block in recipe.blocks.items()}
    )
    reread = read_recipe(once, str(path))
    twice = render_recipe(
        reread, {p: block.content for p, block in reread.blocks.items()}
    )
    assert twice == once


@pytest.mark.parametrize("path", RECIPES, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_what_was_written_is_what_reads_back(path: Path) -> None:
    """A longer and a shorter list both survive the round trip."""
    text = path.read_text(encoding="utf-8")
    recipe = read_recipe(text, str(path))
    for block_path, block in recipe.blocks.items():
        for wanted in (
            (*block.content.requirements, Requirement("swage-added >=1.0")),
            block.content.requirements[:-1],
        ):
            content = dataclasses.replace(block.content, entries=wanted)
            rewritten = render_recipe(recipe, {block_path: content})
            reread = read_recipe(rewritten, str(path))
            if not wanted:
                # An emptied list leaves the section with no value to read.
                assert block_path not in reread.blocks
                continue
            assert reread.blocks[block_path].content.requirements == wanted


def test_several_blocks_can_be_written_at_once() -> None:
    """Blocks are spliced bottom-up so the ones above keep their line numbers."""
    path = AIRFLOW / "providers-common-sql_2.1.0" / "recipe.yaml"
    text = path.read_text(encoding="utf-8")
    recipe = read_recipe(text, str(path))
    changes = {
        block_path: dataclasses.replace(
            block.content,
            entries=(
                *block.content.requirements,
                Requirement(f"swage-{block.section}-marker >=1.0"),
            ),
        )
        for block_path, block in recipe.blocks.items()
    }
    rewritten = render_recipe(recipe, changes)
    reread = read_recipe(rewritten, str(path))
    assert set(reread.blocks) == set(recipe.blocks)
    for block in reread.blocks.values():
        expected = f"swage-{block.section}-marker >=1.0"
        assert block.content.texts()[-1] == expected


def test_reordering_a_real_recipe_carries_its_comments() -> None:
    """End to end, the failure that ruled out conda-recipe-manager.

    The note about pandas has to arrive above pandas, not above whatever ends
    up at the index pandas used to occupy.
    """
    path = AIRFLOW / "providers-databricks_7.18.1" / "recipe.yaml"
    text = path.read_text(encoding="utf-8")
    recipe = read_recipe(text, str(path))
    block = recipe.blocks["/requirements/run"]
    requirements = list(block.content.requirements)
    pandas = next(r for r in requirements if r.text.startswith("pandas"))
    requirements.remove(pandas)
    requirements.insert(1, pandas)

    rewritten = render_recipe(
        recipe,
        {
            "/requirements/run": dataclasses.replace(
                block.content, entries=tuple(requirements)
            )
        },
    )
    lines = rewritten.split("\n")
    pandas_line = next(i for i, line in enumerate(lines) if "- pandas >=2.3.3" in line)
    assert lines[pandas_line - 1].strip() == "# more restrictive for python >=3.14"
    # And it did not also stay behind where pandas used to be.
    assert rewritten.count("# more restrictive for python >=3.14") == text.count(
        "# more restrictive for python >=3.14"
    )


def test_marker_pairs_survive_a_rewrite_of_the_block_they_wrap() -> None:
    path = AIRFLOW / "providers-common-sql_2.1.0" / "recipe.yaml"
    text = path.read_text(encoding="utf-8")
    recipe = read_recipe(text, str(path))
    block = recipe.blocks["/outputs/2/requirements/run"]
    content = dataclasses.replace(
        block.content,
        entries=(
            *block.content.requirements,
            Requirement("adbc-driver-mysql >=1.2.0"),
        ),
    )
    rewritten = render_recipe(recipe, {"/outputs/2/requirements/run": content})
    assert "# start pandas[sql-other]" in rewritten
    assert "# end pandas[sql-other]" in rewritten
    reread = read_recipe(rewritten, str(path))
    assert reread.blocks["/outputs/2/requirements/run"].content.trailing_comments == (
        "# end pandas[sql-other]",
    )


def test_writing_an_unknown_block_is_an_error() -> None:
    """A path typo must not be a silent no-op on a write path."""
    path = AIRFLOW / "providers-databricks_7.18.1" / "recipe.yaml"
    recipe = read_recipe(path.read_text(encoding="utf-8"), str(path))
    with pytest.raises(RecipeError, match="no such requirements block"):
        render_recipe(recipe, {"/outputs/9/requirements/run": BlockContent()})


def test_crlf_recipes_are_refused() -> None:
    with pytest.raises(RecipeError, match="CRLF"):
        read_recipe("requirements:\r\n  run:\r\n    - python\r\n")


FLUSH = """\
requirements:
  host:
  - python
  - pip
  run:
  - python
"""


def test_a_flush_section_is_written_back_flush() -> None:
    """swage was not asked to reformat the recipe, only to reconcile it."""
    recipe = read_recipe(FLUSH)
    block = recipe.blocks["/requirements/host"]
    content = dataclasses.replace(
        block.content, entries=(*block.content.requirements, Requirement("setuptools"))
    )
    rewritten = render_recipe(recipe, {"/requirements/host": content})
    assert rewritten == FLUSH.replace("  - pip\n", "  - pip\n  - setuptools\n")
