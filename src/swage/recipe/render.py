"""Turn a requirements block back into YAML lines (v1 §6).

Comments are emitted at the requirements' indentation. A conditional entry is
rendered from its own layout, not from a house style (v1 §3.1).
"""

from __future__ import annotations

from .model import BlockContent, Conditional, Entry, Requirement

__all__ = ["inline_text", "render_block"]


def inline_text(entry: Entry) -> str:
    """One entry on one line, for a report rather than for a recipe: the
    recipe's own syntax, flattened.
    """
    if isinstance(entry, Requirement):
        return entry.text
    branches = f"if: {entry.condition} then: {_branch_text(entry.then)}"
    if entry.otherwise is None:
        return branches
    return f"{branches} else: {_branch_text(entry.otherwise)}"


def _branch_text(branch: tuple[Entry, ...]) -> str:
    return ", ".join(inline_text(entry) for entry in branch)


def render_block(content: BlockContent, item_indent: int) -> list[str]:
    """Render one requirements section's body as source lines.

    The lines have no trailing newline; they are joined by the writer.
    """
    pad = " " * item_indent
    lines = _render_entries(content.entries, item_indent)
    lines.extend(_comment_lines(content.trailing_comments, pad))
    return lines


def _render_entries(entries: tuple[Entry, ...], item_indent: int) -> list[str]:
    pad = " " * item_indent
    lines: list[str] = []
    for entry in entries:
        lines.extend(_comment_lines(entry.comments, pad))
        if isinstance(entry, Requirement):
            lines.append(f"{pad}- {entry.text}")
        else:
            lines.extend(_render_conditional(entry, item_indent))
    return lines


def _render_conditional(entry: Conditional, item_indent: int) -> list[str]:
    pad = " " * item_indent
    key_pad = " " * (item_indent + entry.key_offset)
    lines = [f"{pad}- if: {entry.condition}"]
    lines.extend(
        _render_branch(
            "then", entry.then, entry.then_inline, key_pad, entry, item_indent
        )
    )
    if entry.otherwise is not None:
        lines.extend(
            _render_branch(
                "else",
                entry.otherwise,
                entry.otherwise_inline,
                key_pad,
                entry,
                item_indent,
            )
        )
    return lines


def _render_branch(
    name: str,
    branch: tuple[Entry, ...],
    inline: bool,
    key_pad: str,
    entry: Conditional,
    item_indent: int,
) -> list[str]:
    if inline:
        # The model guarantees exactly one plain requirement here.
        only = branch[0]
        assert isinstance(only, Requirement)
        return [f"{key_pad}{name}: {only.text}"]
    return [
        f"{key_pad}{name}:",
        *_render_entries(branch, item_indent + entry.item_offset),
    ]


def _comment_lines(comments: tuple[str, ...], pad: str) -> list[str]:
    # A blank line is emitted as a genuinely empty line, not as indentation.
    return [f"{pad}{comment}" if comment else "" for comment in comments]
