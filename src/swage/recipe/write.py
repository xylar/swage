"""Write a recipe back by replacing the line ranges the reader found.

swage never re-emits a whole recipe. It replaces the ranges it identified and
leaves every other byte of the file exactly as it found it.

That is a deliberate choice against the obvious alternative of dumping the
parsed document. Round-tripping a whole YAML file through any emitter
normalizes things nobody asked to change -- quoting, blank lines, line wrapping
-- and every one of those shows up as a diff on someone else's feedstock.

**There are two kinds of range now, and that cost something.** While
requirements blocks were the only one, "the diff touches only requirements
sections" was true because there was no code path that could touch anything
else. A second region makes it a claim to check rather than a property to rely
on (DESIGN.md 3.7), so the check that used to be structural now reads the diff.
What has not changed is that both regions are *ranges the reader identified*:
swage still cannot write a line it did not first locate.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from .errors import RecipeError
from .model import BlockContent, Recipe
from .render import render_block

__all__ = ["render_python_version", "render_recipe"]


def render_recipe(
    recipe: Recipe,
    changes: Mapping[str, BlockContent] | None = None,
    matrices: Mapping[str, Sequence[str]] | None = None,
) -> str:
    """Return ``recipe``'s text with the named ranges replaced.

    ``changes`` maps a requirements block's path to its new contents, and
    ``matrices`` maps a python test's path to the versions it should test.
    Anything left out is not re-rendered at all, so it cannot change. Passing
    everything is how swage asks "what would this recipe look like if I wrote
    it?", which is the comparison the byte-identical check depends on.
    """
    if not changes and not matrices:
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
        # Inserting the key is a different operation from replacing it, and
        # swage does not do it (DESIGN.md 3.7). Refusing here rather than
        # writing at line 0, which is where an unread range would point.
        raise RecipeError(
            f"python test has no python_version to replace: {', '.join(absent)}"
        )

    lines = recipe.text.split("\n")
    # One list of edits, replaced from the bottom up so that the line numbers
    # of everything above stay valid while everything below has already moved.
    # Interleaving the two kinds matters: a recipe's tests sit below its
    # requirements, and sorting each kind separately would apply them in an
    # order that invalidates the other's offsets.
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
    for first, end, rendered in sorted(edits, key=lambda edit: edit[0], reverse=True):
        lines[first:end] = rendered
    return "\n".join(lines)


def render_python_version(versions: Sequence[str], item_indent: int) -> list[str]:
    """The `python_version` key and its list, as source lines.

    Always the list form, even for one entry: the recipes being migrated are
    scalars becoming lists, and a renderer that preserved scalar-ness for a
    single version would need a second code path to serve a case that does not
    arise -- swage only ever writes two.

    `"*"` is quoted because it has to be. Unquoted, a leading `*` opens a YAML
    alias and the file stops parsing; conda-smithy matches the exact string
    `*`, so the quotes are the only way to write the thing it looks for.
    """
    key_indent = " " * max(0, item_indent - 2)
    body = " " * item_indent
    return [f"{key_indent}python_version:"] + [
        f"{body}- {_quoted(version)}" for version in versions
    ]


def _quoted(version: str) -> str:
    return f'"{version}"' if version.startswith(("*", "&", "!")) else version
