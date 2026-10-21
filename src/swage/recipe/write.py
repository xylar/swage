"""Write a recipe back by replacing the line ranges the reader found (DESIGN.md
§7).

swage never re-emits a whole recipe. Every region is a range the reader
identified; swage cannot write a line it did not first locate.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from .errors import RecipeError
from .model import BlockContent, Recipe
from .render import render_block

__all__ = ["render_entry_points", "render_python_version", "render_recipe"]

#: The value of a `python_min:` line, and the quote around it, which is kept.
_PYTHON_MIN_VALUE = re.compile(r"""^(\s*python_min:\s*)(['"]?)[0-9.]+\2""")


def render_recipe(
    recipe: Recipe,
    changes: Mapping[str, BlockContent] | None = None,
    matrices: Mapping[str, Sequence[str]] | None = None,
    entry_points: Mapping[str, Sequence[str]] | None = None,
    python_min: str | None = None,
    drop_python_min: bool = False,
    skips: Mapping[str, str] | None = None,
) -> str:
    """Return ``recipe``'s text with the named ranges replaced.

    ``changes`` maps a requirements block's path to its new contents,
    ``matrices`` a python test's path to its versions, and ``entry_points``
    an output's list path to its items, and ``python_min`` the context entry's
    new value or ``drop_python_min`` its removal. ``skips`` maps a `build.skip`
    condition's path to its new text. Anything left out is not re-rendered.
    """
    floor = python_min is not None or drop_python_min
    if not changes and not matrices and not entry_points and not floor and not skips:
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
    if floor:
        edits.append(_python_min_edit(recipe, lines, python_min))
    edits += [
        _skip_edit(recipe, lines, path, text) for path, text in (skips or {}).items()
    ]
    for first, end, rendered in sorted(edits, key=lambda edit: edit[0], reverse=True):
        lines[first:end] = rendered
    return "\n".join(lines)


def _skip_edit(
    recipe: Recipe, lines: list[str], path: str, text: str
) -> tuple[int, int, list[str]]:
    """Rewrite one `build.skip` condition where it stands on its line, so its
    quotes, its list marker and any comment after it stay.
    """
    condition = recipe.skip_conditions.get(path)
    if condition is None:
        raise RecipeError(f"no such build.skip condition in this recipe: {path}")
    if condition.line is None:
        raise RecipeError(
            f"build.skip condition is not on one line as written: {condition.text!r}"
        )
    line = lines[condition.line]
    return condition.line, condition.line + 1, [line.replace(condition.text, text, 1)]


def _python_min_edit(
    recipe: Recipe, lines: list[str], value: str | None
) -> tuple[int, int, list[str]]:
    """Replace the value of `context.python_min`, add the entry where the floor
    came from `.ci_support`, or, for None, remove it.
    """
    at = recipe.python_min_at
    if at is None:
        raise RecipeError("recipe has no context to set python_min in")
    if value is None:
        if not at.present:
            raise RecipeError("recipe has no context.python_min to remove")
        return at.comments_from, at.line + 1, []
    if not at.present:
        return at.line, at.line, [f'{" " * at.indent}python_min: "{value}"']
    line = lines[at.line]
    match = _PYTHON_MIN_VALUE.match(line)
    if match is None:
        raise RecipeError(f"python_min is not a version swage can replace: {line!r}")
    quote = match.group(2)
    rendered = f"{match.group(1)}{quote}{value}{quote}{line[match.end() :]}"
    return at.line, at.line + 1, [rendered]


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
