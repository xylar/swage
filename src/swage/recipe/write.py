"""Write a recipe back by replacing only its requirements blocks (DESIGN.md 3.1).

swage never re-emits a whole recipe. It replaces the line ranges the reader
identified and leaves every other byte of the file exactly as it found it.

That is a deliberate choice against the obvious alternative of dumping the
parsed document. Round-tripping a whole YAML file through any emitter
normalizes things nobody asked to change -- quoting, blank lines, line wrapping
-- and every one of those shows up as a diff on someone else's feedstock.
Splicing makes trust gate G5, "the diff touches only requirements sections",
true by construction rather than something to check afterwards.
"""

from __future__ import annotations

from collections.abc import Mapping

from .errors import RecipeError
from .model import BlockContent, Recipe
from .render import render_block

__all__ = ["render_recipe"]


def render_recipe(
    recipe: Recipe, changes: Mapping[str, BlockContent] | None = None
) -> str:
    """Return ``recipe``'s text with the named requirements blocks replaced.

    ``changes`` maps a block path to its new contents. Blocks left out are not
    re-rendered at all, so they cannot change. Passing every block is how swage
    asks "what would this recipe look like if I wrote it?", which is the
    comparison gate G7 depends on.
    """
    if not changes:
        return recipe.text

    blocks = recipe.blocks
    unknown = sorted(set(changes) - set(blocks))
    if unknown:
        raise RecipeError(
            f"no such requirements block in this recipe: {', '.join(unknown)}"
        )

    lines = recipe.text.split("\n")
    # Replacing from the bottom up keeps the line numbers of the blocks above
    # valid while the ones below have already moved.
    for path in sorted(changes, key=lambda p: blocks[p].first_line, reverse=True):
        block = blocks[path]
        lines[block.first_line : block.end_line] = render_block(
            changes[path], block.item_indent
        )
    return "\n".join(lines)
