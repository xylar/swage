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
from .model import BlockContent, Recipe, RecipeOutput, Requirement, RequirementsBlock

__all__ = ["read_recipe", "resolve_expression"]

#: The requirements sections swage understands. `run_exports` is deliberately
#: absent: it is a packaging decision, not a dependency reconciliation.
SECTIONS = ("build", "host", "run", "run_constraints")

#: `${{ name }}` and `${{ name|lower }}`, which is all a package name uses.
_SUBSTITUTION = re.compile(
    r"\$\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?:\|\s*(lower|upper)\s*)?\}\}"
)


def resolve_expression(expr: str, context: Mapping[str, str]) -> str | None:
    """Substitute ``${{ var }}`` from the recipe context.

    Returns ``None`` if anything is left unresolved, so a caller can tell
    "this is the package name" from "swage could not work out the name" rather
    than acting on a half-substituted string.
    """

    def substitute(match: re.Match[str]) -> str:
        value = context.get(match.group(1))
        if value is None:
            return match.group(0)
        if match.group(2) == "lower":
            return value.lower()
        if match.group(2) == "upper":
            return value.upper()
        return value

    resolved = _SUBSTITUTION.sub(substitute, expr)
    return None if "${{" in resolved else resolved


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
    return Recipe(text=text, context=context, outputs=outputs)


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
