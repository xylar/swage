"""Write a recipe back by replacing the line ranges the reader found (DESIGN.md
§7).

swage never re-emits a whole recipe. Every region is a range the reader
identified; swage cannot write a line it did not first locate.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from .errors import RecipeError
from .model import BlockContent, Recipe
from .render import render_block

__all__ = ["render_entry_points", "render_python_version", "render_recipe"]


def render_recipe(
    recipe: Recipe,
    changes: Mapping[str, BlockContent] | None = None,
    matrices: Mapping[str, Sequence[str]] | None = None,
    entry_points: Mapping[str, Sequence[str]] | None = None,
) -> str:
    """Return ``recipe``'s text with the named ranges replaced.

    ``changes`` maps a requirements block's path to its new contents,
    ``matrices`` a python test's path to its versions, and ``entry_points``
    an output's list path to its items. Anything left out is not re-rendered.
    """
    if not changes and not matrices and not entry_points:
        return recipe.text

    blocks = recipe.blocks
    unknown = sorted(set(changes or {}) - set(blocks))
    if unknown:
        raise RecipeError(
            f"no such requirements block in this recipe: {', '.join(unknown)}"
        )
    tests = {test.path: test for test in recipe.python_tests}
    unknown_tests = sorted(set(matrices or {}) - set(tests))
    if unknown_tests:
        raise RecipeError(
            f"no such python test in this recipe: {', '.join(unknown_tests)}"
        )
    absent = sorted(path for path in (matrices or {}) if not tests[path].present)
    if absent:
        # Inserting the key is a different operation from replacing it (v1
        # §3.7).
        raise RecipeError(
            f"python test has no python_version to replace: {', '.join(absent)}"
        )

    scripts = recipe.entry_points
    unknown_scripts = sorted(set(entry_points or {}) - set(scripts))
    if unknown_scripts:
        raise RecipeError(
            f"no such entry_points list in this recipe: {', '.join(unknown_scripts)}"
        )
    held = sorted(path for path in (entry_points or {}) if scripts[path].conditional)
    if held:
        # An `if:` entry is a decision the recipe made; the planner never asks
        # for this.
        raise RecipeError(
            "entry_points list holds an if: entry swage does not rewrite: "
            f"{', '.join(held)}"
        )

    lines = recipe.text.split("\n")
    # One list of edits, replaced from the bottom up so line numbers above stay
    # valid. The kinds are interleaved, since sorting each separately would
    # invalidate the others' offsets.
    edits = [
        (
            blocks[path].first_line,
            blocks[path].end_line,
            render_block(content, blocks[path].item_indent),
        )
        for path, content in (changes or {}).items()
    ]
    edits += [
        (
            tests[path].first_line,
            tests[path].end_line,
            render_python_version(versions, tests[path].item_indent),
        )
        for path, versions in (matrices or {}).items()
    ]
    edits += [
        (
            scripts[path].first_line,
            scripts[path].end_line,
            render_entry_points(
                items, scripts[path].key_indent, scripts[path].item_indent
            ),
        )
        for path, items in (entry_points or {}).items()
    ]
    for first, end, rendered in sorted(edits, key=lambda edit: edit[0], reverse=True):
        lines[first:end] = rendered
    return "\n".join(lines)


def render_python_version(versions: Sequence[str], item_indent: int) -> list[str]:
    """The `python_version` key and its list, as source lines. Always the list
    form. `"*"` is quoted because an unquoted `*` opens a YAML alias.
    """
    key_indent = " " * max(0, item_indent - 2)
    body = " " * item_indent
    return [f"{key_indent}python_version:"] + [
        f"{body}- {_quoted(version)}" for version in versions
    ]


def render_entry_points(
    items: Sequence[str], key_indent: int, item_indent: int
) -> list[str]:
    """The `entry_points` key and its list, as source lines, at the recipe's own
    indents. Never quoted.
    """
    key = " " * key_indent
    body = " " * item_indent
    return [f"{key}entry_points:"] + [f"{body}- {item}" for item in items]


def _quoted(version: str) -> str:
    return f'"{version}"' if version.startswith(("*", "&", "!")) else version
