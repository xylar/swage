"""Parse a v1 ``recipe.yaml`` into the model (DESIGN.md 3.1).

ruamel is used for structure and source positions only. The requirements
themselves are read from the source lines rather than from the parsed values,
because swage writes by splicing those same line ranges back -- so what it reads
has to be exactly what is on disk, character for character.

Anything that would make that untrue is refused rather than guessed at: a
quoted requirement, an inline comment on a requirement line, a flow-style list.
None of these occur in the 216 real recipes surveyed, and refusing them is
cheaper than handling them wrongly.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from .errors import RecipeError
from .model import (
    BlockContent,
    Recipe,
    RecipeOutput,
    RecipeSource,
    Requirement,
    RequirementsBlock,
)

__all__ = ["read_recipe", "resolve_expression"]

#: The requirements sections swage understands. `run_exports` is deliberately
#: absent: it is a packaging decision, not a dependency reconciliation.
SECTIONS = ("build", "host", "run", "run_constraints")

#: One `${{ ... }}`, and what a recipe writes inside one: a context variable,
#: optionally indexed, optionally filtered. A package name needs no more than
#: `${{ name }}` and `${{ name|lower }}`, but a source URL does -- across the
#: 226 source entries in the maintainer's checkouts the forms that occur are
#: `version`, `name`, `name[0]` (81 of them, for PyPI's first-letter path
#: segment) and `name|replace('-', '_')` (16, written with and without spaces
#: around the pipe). Nothing else appears, and an expression outside this set
#: resolves to None rather than to a guess.
_EXPRESSION = re.compile(r"\$\{\{(.*?)\}\}")
_VARIABLE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)(?:\[(\d+)\])?$")
_REPLACE = re.compile(r"""^replace\(\s*(['"])(.*?)\1\s*,\s*(['"])(.*?)\3\s*\)$""")


def resolve_expression(expr: str, context: Mapping[str, str]) -> str | None:
    """Substitute ``${{ var }}`` from the recipe context.

    Returns ``None`` if anything is left unresolved, so a caller can tell
    "this is the package name" from "swage could not work out the name" rather
    than acting on a half-substituted string.
    """
    resolved: list[str] = []
    position = 0
    for match in _EXPRESSION.finditer(expr):
        value = _evaluate(match.group(1), context)
        if value is None:
            return None
        resolved.append(expr[position : match.start()])
        resolved.append(value)
        position = match.end()
    resolved.append(expr[position:])
    joined = "".join(resolved)
    # An unterminated `${{` matches nothing above and would otherwise survive
    # into the result, which is the half-substituted string this rules out.
    return None if "${{" in joined else joined


def _evaluate(inner: str, context: Mapping[str, str]) -> str | None:
    """Evaluate the inside of one ``${{ ... }}``, or None if swage cannot."""
    head, *filters = (part.strip() for part in inner.split("|"))
    match = _VARIABLE.match(head)
    if match is None:
        return None
    value = context.get(match.group(1))
    if value is None:
        return None
    index = match.group(2)
    if index is not None:
        if int(index) >= len(value):
            return None
        value = value[int(index)]
    for name in filters:
        applied = _apply_filter(name, value)
        if applied is None:
            return None
        value = applied
    return value


def _apply_filter(name: str, value: str) -> str | None:
    if name == "lower":
        return value.lower()
    if name == "upper":
        return value.upper()
    replace = _REPLACE.match(name)
    return value.replace(replace.group(2), replace.group(4)) if replace else None


def read_recipe(text: str, source: str = "<recipe>") -> Recipe:
    """Parse ``text`` as a v1 recipe."""
    yaml = YAML(typ="rt")
    try:
        data = yaml.load(text)
    except YAMLError as exc:
        raise RecipeError(f"{source}: invalid YAML: {exc}") from exc
    if not isinstance(data, Mapping):
        raise RecipeError(f"{source}: expected a mapping at the top level")
    if "\r" in text:
        # The writer addresses the file by line index; carriage returns would
        # make the reader's line numbering and the writer's disagree.
        raise RecipeError(f"{source}: has CRLF or CR line endings")

    lines = text.split("\n")
    context = {
        key: str(value)
        for key, value in (data.get("context") or {}).items()
        if isinstance(value, str | int | float)
    }

    raw_outputs = data.get("outputs")
    outputs: tuple[RecipeOutput, ...]
    if raw_outputs is None:
        outputs = (_read_output(data, None, "", lines, context, source),)
    else:
        outputs = tuple(
            _read_output(entry, index, f"/outputs/{index}", lines, context, source)
            for index, entry in enumerate(raw_outputs)
            if entry is not None
        )
    return Recipe(
        text=text,
        context=context,
        outputs=outputs,
        sources=_read_sources(data.get("source"), context),
    )


def _read_sources(node: Any, context: Mapping[str, str]) -> tuple[RecipeSource, ...]:
    """Read ``source``, which is a single mapping or a list of them.

    Both shapes are common -- 144 mappings to 68 lists across the maintainer's
    checkouts -- so neither is the special case.
    """
    entries = node if isinstance(node, list) else [node]
    return tuple(
        _read_source(entry, context) for entry in entries if isinstance(entry, Mapping)
    )


def _read_source(node: Mapping[str, Any], context: Mapping[str, str]) -> RecipeSource:
    url_expr = node.get("url")
    if not isinstance(url_expr, str):
        # A `git:` or `path:` source. It still occupies a position in the list.
        return RecipeSource(target_directory=_optional_str(node, "target_directory"))
    return RecipeSource(
        url_expr=url_expr,
        url=resolve_expression(url_expr, context),
        sha256=_optional_str(node, "sha256"),
        target_directory=_optional_str(node, "target_directory"),
    )


def _optional_str(node: Mapping[str, Any], key: str) -> str | None:
    value = node.get(key)
    return value if isinstance(value, str) else None


def _read_output(
    node: Any,
    index: int | None,
    prefix: str,
    lines: list[str],
    context: Mapping[str, str],
    source: str,
) -> RecipeOutput:
    name_expr = _package_name(node)
    blocks: dict[str, RequirementsBlock] = {}
    requirements = node.get("requirements")
    if isinstance(requirements, Mapping):
        for section in SECTIONS:
            if section not in requirements:
                continue
            block = _read_block(
                requirements, section, f"{prefix}/requirements/{section}", lines, source
            )
            if block is not None:
                blocks[section] = block
    return RecipeOutput(
        index=index,
        name=resolve_expression(name_expr, context) if name_expr else None,
        name_expr=name_expr,
        blocks=blocks,
    )


def _package_name(node: Any) -> str | None:
    package = node.get("package")
    if isinstance(package, Mapping):
        name = package.get("name")
        if isinstance(name, str):
            return name
    return None


def _read_block(
    requirements: Any, section: str, path: str, lines: list[str], source: str
) -> RequirementsBlock | None:
    values = requirements[section]
    if values is None:
        return None
    if not isinstance(values, list):
        raise RecipeError(f"{source}: {path} is not a list")

    key_line, key_indent = requirements.lc.key(section)
    first_line, end_line = _block_extent(lines, key_line, key_indent)
    content, item_indent = _read_body(
        lines[first_line:end_line], path, key_indent, source
    )

    if len(content.requirements) != len(values):
        raise RecipeError(
            f"{source}: {path} has {len(values)} requirements but "
            f"{len(content.requirements)} could be read from the source lines; "
            "swage only understands one plain requirement per line"
        )
    for requirement, value in zip(content.requirements, values, strict=True):
        if requirement.text != str(value):
            raise RecipeError(
                f"{source}: {path} contains a requirement swage cannot rewrite "
                f"safely: read {requirement.text!r} from the source but the "
                f"parsed value is {str(value)!r}"
            )
    return RequirementsBlock(
        path=path,
        section=section,
        content=content,
        item_indent=item_indent,
        first_line=first_line,
        end_line=end_line,
    )


def _block_extent(lines: list[str], key_line: int, key_indent: int) -> tuple[int, int]:
    """The half-open line range of a block's body.

    Trailing blank lines are left out, so they stay part of the untouched
    remainder of the file rather than being re-emitted by the renderer.
    """
    first = key_line + 1
    end = first
    for number in range(first, len(lines)):
        line = lines[number]
        if not line.strip():
            continue
        if len(line) - len(line.lstrip()) <= key_indent:
            break
        end = number + 1
    return first, end


def _read_body(
    body: list[str], path: str, key_indent: int, source: str
) -> tuple[BlockContent, int]:
    pending: list[str] = []
    requirements: list[Requirement] = []
    item_indent: int | None = None

    for line in body:
        stripped = line.strip()
        if not stripped:
            pending.append("")
        elif stripped.startswith("#"):
            pending.append(stripped)
        elif stripped.startswith("- "):
            if item_indent is None:
                item_indent = len(line) - len(line.lstrip())
            requirements.append(Requirement(stripped[2:].strip(), tuple(pending)))
            pending = []
        else:
            raise RecipeError(
                f"{source}: {path} has a line swage cannot read as a "
                f"requirement, a comment, or a blank line: {line!r}"
            )

    content = BlockContent(
        requirements=tuple(requirements), trailing_comments=tuple(pending)
    )
    return content, key_indent + 2 if item_indent is None else item_indent
