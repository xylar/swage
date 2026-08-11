"""Turn a requirements block back into YAML lines (DESIGN.md 6).

Comments are emitted at the same indentation as the requirements they sit
above, which is what makes a `# start` / `# end` marker pair line up with the
block it wraps. swage owns these lines: it renders them from the model rather
than preserving whatever was there, so the ordering rules in DESIGN.md 6 apply
uniformly instead of only to recipes swage happens to have written before.
"""

from __future__ import annotations

from .model import BlockContent

__all__ = ["render_block"]


def render_block(content: BlockContent, item_indent: int) -> list[str]:
    """Render one requirements section's body as source lines.

    The lines have no trailing newline; they are joined by the writer.
    """
    pad = " " * item_indent
    lines: list[str] = []
    for requirement in content.requirements:
        lines.extend(_comment_lines(requirement.comments, pad))
        lines.append(f"{pad}- {requirement.text}")
    lines.extend(_comment_lines(content.trailing_comments, pad))
    return lines


def _comment_lines(comments: tuple[str, ...], pad: str) -> list[str]:
    # A blank line is emitted as a genuinely empty line, not as indentation.
    return [f"{pad}{comment}" if comment else "" for comment in comments]
