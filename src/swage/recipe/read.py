"""Parse a v1 ``recipe.yaml`` into the model (v1 §3.1; DESIGN.md §7).

ruamel is used for structure and source positions only; the requirements are
read from the source lines, because the writer splices those same ranges back.
Anything that would make that untrue is refused. A comment after a requirement
on the same line is carried as a comment above it.
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
    Conditional,
    Entry,
    EntryPoints,
    PythonTest,
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
#: optionally indexed, optionally filtered. An expression outside this set
#: resolves to None rather than to a guess.
_EXPRESSION = re.compile(r"\$\{\{(.*?)\}\}")
_VARIABLE = re.compile(
    r"^([A-Za-z_][A-Za-z0-9_]*)(?:\[(\d+)\])?((?:\.[A-Za-z_]\w*\([^()]*\))*)$"
)
_REPLACE = re.compile(r"""^replace\(\s*(['"])(.*?)\1\s*,\s*(['"])(.*?)\3\s*\)$""")

#: A method called on the variable rather than a filter piped through it: the
#: two spellings mean the same thing, and a v0 conversion carries the method
#: form across.
_METHOD = re.compile(r"\.([A-Za-z_]\w*)\(([^()]*)\)")


def resolve_expression(expr: str, context: Mapping[str, str]) -> str | None:
    """Substitute ``${{ var }}`` from the recipe context, returning ``None`` if
    anything is left unresolved.
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
    """Evaluate the inside of one ``${{ ... }}``, or None if swage cannot.
    Methods first, because they bind to the variable.
    """
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
    methods = [
        call.group(1) if not call.group(2).strip() else call.group(0)[1:]
        for call in _METHOD.finditer(match.group(3))
    ]
    for name in (*methods, *filters):
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


def _read_context(node: Any) -> dict[str, str]:
    """The context block, with each entry resolved against the ones above it,
    as rattler-build evaluates them. An entry swage cannot evaluate is
    dropped: a build variant has no value to resolve to (v1 §3.3.4).
    """
    resolved: dict[str, str] = {}
    for key, value in (node or {}).items():
        if not isinstance(value, str | int | float):
            continue
        text = str(value)
        expanded = resolve_expression(text, resolved) if "${{" in text else text
        if expanded is not None:
            resolved[key] = expanded
    return resolved


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
    context = _read_context(data.get("context"))

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
    """Read ``source``, which is a single mapping or a list of them."""
    entries = node if isinstance(node, list) else [node]
    return tuple(
        _read_source(entry, context) for entry in entries if isinstance(entry, Mapping)
    )


def _read_source(node: Mapping[str, Any], context: Mapping[str, str]) -> RecipeSource:
    url_expr = node.get("url")
    if not isinstance(url_expr, str):
        # A `git:` or `path:` source. It still occupies a position in the list.
        return RecipeSource(target_directory=_optional_str(node, "target_directory"))
    sha256 = _optional_str(node, "sha256")
    return RecipeSource(
        url_expr=url_expr,
        url=resolve_expression(url_expr, context),
        # Resolved against the context exactly as the URL above it is: a recipe
        # keeping the digest in `context` is ordinary.
        sha256=resolve_expression(sha256, context) if sha256 is not None else None,
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
    build = node.get("build")
    if not isinstance(build, Mapping):
        build = {}
    return RecipeOutput(
        index=index,
        name=resolve_expression(name_expr, context) if name_expr else None,
        name_expr=name_expr,
        staging=_staging_name(node),
        blocks=blocks,
        noarch=_optional_str(build, "noarch"),
        python_tests=_read_python_tests(node.get("tests"), prefix, lines),
        entry_points=_read_entry_points(build, f"{prefix}/build/python", lines),
    )


def _read_entry_points(build: Any, path: str, lines: list[str]) -> EntryPoints | None:
    """`build.python.entry_points`, with the line range the key occupies: the
    key line and its body, as `python_version` is read. The item indent is
    the list's own.
    """
    python = build.get("python")
    if not isinstance(python, Mapping) or "entry_points" not in python:
        return None
    value = python["entry_points"]
    items = value if isinstance(value, list) else [value]
    # ruamel's mapping, which carries line numbers; typed away like the
    # python-test reader's argument is.
    located: Any = python
    key_line, key_indent = located.lc.key("entry_points")
    _, end_line = _block_extent(lines, key_line, key_indent)
    item_indent = key_indent + 2
    for number in range(key_line + 1, end_line):
        stripped = lines[number].lstrip()
        if stripped.startswith("- "):
            item_indent = len(lines[number]) - len(stripped)
            break
    return EntryPoints(
        path=f"{path}/entry_points",
        items=tuple(str(item) for item in items if isinstance(item, str)),
        conditional=any(not isinstance(item, str) for item in items),
        key_indent=key_indent,
        item_indent=item_indent,
        first_line=key_line,
        end_line=max(end_line, key_line + 1),
    )


def _read_python_tests(
    tests: Any, prefix: str, lines: list[str]
) -> tuple[PythonTest, ...]:
    """Every `tests:` entry with a `python:` key, and its version matrix. An
    entry without one is skipped, as conda-smithy skips it (v1 §3.7).
    """
    if not isinstance(tests, list):
        return ()
    found = []
    for index, entry in enumerate(tests):
        if not isinstance(entry, Mapping):
            continue
        python = entry.get("python")
        if not isinstance(python, Mapping):
            continue
        found.append(_read_python_test(python, f"{prefix}/tests/{index}/python", lines))
    return tuple(found)


def _read_python_test(python: Any, path: str, lines: list[str]) -> PythonTest:
    """One python test, with the line range its `python_version` occupies. A
    scalar and a list are the same key with a different body.
    """
    if "python_version" not in python:
        return PythonTest(path=path)
    value = python["python_version"]
    versions = tuple(
        str(item) for item in (value if isinstance(value, list) else [value])
    )
    key_line, key_indent = python.lc.key("python_version")
    _, end_line = _block_extent(lines, key_line, key_indent)
    return PythonTest(
        path=path,
        versions=versions,
        present=True,
        item_indent=key_indent + 2,
        first_line=key_line,
        end_line=max(end_line, key_line + 1),
    )


def _staging_name(node: Any) -> str | None:
    """`staging.name`, for an output that builds no package of its own."""
    staging = node.get("staging")
    if isinstance(staging, Mapping):
        name = staging.get("name")
        if isinstance(name, str):
            return name
    return None


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

    _check_against_parse(content.entries, values, path, source)
    return RequirementsBlock(
        path=path,
        section=section,
        content=content,
        item_indent=item_indent,
        first_line=first_line,
        end_line=end_line,
    )


def _check_against_parse(
    entries: tuple[Entry, ...], values: Any, path: str, source: str
) -> None:
    """Assert that what was read off the source lines is what YAML parsed
    (v1 §3.1), which is also what rules out a quoted requirement, a
    flow-style list, or an inline comment swage would drop.
    """
    if len(entries) != len(values):
        raise RecipeError(
            f"{source}: {path} lists {len(values)} entries but only {len(entries)} "
            "could be read from its lines\n"
            "  swage understands one requirement per line and `if:`/`then:` "
            "conditionals"
        )
    for entry, value in zip(entries, values, strict=True):
        if isinstance(entry, Conditional):
            if not isinstance(value, Mapping) or "if" not in value:
                raise RecipeError(
                    f"{source}: {path} reads as a conditional in the source but "
                    f"parses as {type(value).__name__}"
                )
            if entry.condition != str(value["if"]):
                raise RecipeError(
                    f"{source}: {path} contains a condition swage cannot rewrite "
                    f"safely: read {entry.condition!r} but the parsed value is "
                    f"{str(value['if'])!r}"
                )
            for branch, key in ((entry.then, "then"), (entry.otherwise, "else")):
                if branch is None:
                    continue
                body = value.get(key)
                _check_against_parse(
                    branch,
                    body if isinstance(body, list) else [body],
                    f"{path} ({key})",
                    source,
                )
            continue
        if isinstance(value, Mapping):
            raise RecipeError(
                f"{source}: {path} parses as a conditional but reads as the "
                f"plain requirement {entry.text!r} in the source"
            )
        if entry.text != str(value):
            raise RecipeError(
                f"{source}: {path} contains a requirement swage cannot rewrite "
                f"safely: read {entry.text!r} from the source but the "
                f"parsed value is {str(value)!r}"
            )


def _block_extent(lines: list[str], key_line: int, key_indent: int) -> tuple[int, int]:
    """The half-open line range of a block's body.

    Trailing blank lines are left out. A list item may sit at its own key's
    indentation, so a body is everything more indented than the key plus the
    `- ` items level with it.
    """
    first = key_line + 1
    end = first
    for number in range(first, len(lines)):
        line = lines[number]
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        if indent < key_indent:
            break
        if indent == key_indent and not line.lstrip().startswith("- "):
            break
        end = number + 1
    return first, end


def _read_body(
    body: list[str], path: str, key_indent: int, source: str
) -> tuple[BlockContent, int]:
    """Parse a section's body into entries, and say how far its items indent."""
    entries, trailing, item_indent = _read_entries(body, path, source)
    content = BlockContent(entries=entries, trailing_comments=trailing)
    return content, key_indent + 2 if item_indent is None else item_indent


def _read_entries(
    body: list[str], path: str, source: str
) -> tuple[tuple[Entry, ...], tuple[str, ...], int | None]:
    """Every entry of one list, with the comments left over at the end. A list
    item is a plain requirement, or an `if:` opening a conditional.
    """
    pending: list[str] = []
    entries: list[Entry] = []
    item_indent: int | None = None
    index = 0

    while index < len(body):
        line = body[index]
        stripped = line.strip()
        if not stripped:
            pending.append("")
            index += 1
            continue
        if stripped.startswith("#"):
            pending.append(stripped)
            index += 1
            continue
        if not stripped.startswith("- "):
            raise RecipeError(
                f"{source}: {path} has a line swage cannot read as a "
                f"requirement, a comment, or a blank line: {line!r}"
            )
        indent = len(line) - len(line.lstrip())
        if item_indent is None:
            item_indent = indent
        text, inline = _split_inline_comment(stripped[2:].strip())
        if inline:
            pending = [*pending, inline]
        # The entry owns every following line indented past its own `- `.
        end = index + 1
        while end < len(body) and (
            not body[end].strip() or len(body[end]) - len(body[end].lstrip()) > indent
        ):
            end += 1
        # Blank lines after the last nested line belong to whatever comes next.
        while end > index + 1 and not body[end - 1].strip():
            end -= 1

        if text.startswith("if:"):
            entries.append(
                _read_conditional(
                    text[len("if:") :].strip(),
                    body[index + 1 : end],
                    indent,
                    tuple(pending),
                    path,
                    source,
                )
            )
        elif end > index + 1:
            raise RecipeError(
                f"{source}: {path} has a requirement spanning more than one line\n"
                f"  {line!r}\n"
                "  swage cannot rewrite it safely"
            )
        else:
            entries.append(Requirement(text, tuple(pending)))
        pending = []
        index = end

    return tuple(entries), tuple(pending), item_indent


def _split_inline_comment(text: str) -> tuple[str, str]:
    """A list item split into the requirement and the comment written after it,
    which is read as a comment the requirement carries (v1 §6.1). YAML starts
    a comment at a `#` with whitespace in front of it; `_check_against_parse`
    catches a wrong split.
    """
    match = re.search(r"\s#", text)
    if match is None:
        return text, ""
    return text[: match.start()].rstrip(), text[match.start() :].strip()


def _read_conditional(
    condition: str,
    body: list[str],
    dash_indent: int,
    comments: tuple[str, ...],
    path: str,
    source: str,
) -> Conditional:
    """One `if:` entry: its condition, its branches, and how it is laid out."""
    if not condition:
        raise RecipeError(
            f"{source}: {path} has an `if:` with no condition on its line\n"
            "  swage cannot rewrite it safely"
        )
    branches: dict[str, tuple[tuple[Entry, ...], bool]] = {}
    key_offset: int | None = None
    item_offset: int | None = None
    index = 0
    while index < len(body):
        line = body[index]
        if not line.strip():
            index += 1
            continue
        indent = len(line) - len(line.lstrip())
        name, separator, rest = line.strip().partition(":")
        if not separator or name not in ("then", "else"):
            raise RecipeError(
                f"{source}: {path} has a line inside a conditional that is "
                f"neither `then:` nor `else:`: {line!r}"
            )
        if name in branches:
            raise RecipeError(f"{source}: {path} repeats `{name}:` in one conditional")
        key_offset = indent - dash_indent
        end = index + 1
        while end < len(body) and (
            not body[end].strip() or len(body[end]) - len(body[end].lstrip()) > indent
        ):
            end += 1
        if rest.strip():
            if end > index + 1:
                raise RecipeError(
                    f"{source}: {path} has a `{name}:` with both a value and a "
                    f"list under it: {line!r}"
                )
            branches[name] = ((Requirement(rest.strip()),), True)
        else:
            nested, trailing, nested_indent = _read_entries(
                body[index + 1 : end], path, source
            )
            if trailing:
                raise RecipeError(
                    f"{source}: {path} has a comment at the end of a `{name}:` "
                    "branch, which swage cannot place when it renders"
                )
            if nested_indent is not None:
                item_offset = nested_indent - dash_indent
            branches[name] = (nested, False)
        index = end

    if "then" not in branches:
        raise RecipeError(f"{source}: {path} has an `if:` with no `then:`")
    then, then_inline = branches["then"]
    otherwise, otherwise_inline = branches.get("else", (None, False))
    return Conditional(
        condition=condition,
        then=then,
        otherwise=otherwise,
        comments=comments,
        key_offset=2 if key_offset is None else key_offset,
        item_offset=4 if item_offset is None else item_offset,
        then_inline=then_inline,
        otherwise_inline=otherwise_inline,
    )
