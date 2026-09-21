"""The budgets of DESIGN.md §3.2, and how a surface is measured against them.

`tests/test_wording_budgets.py` asserts them over the corpus, and
`scripts/budgets.py` over recorded runs (§3.3). A code span counts as one
word: it is one thing a reader would type or grep (§3.1), however many
spaces are inside it.
"""

from __future__ import annotations

import re

__all__ = [
    "COMMENT_BODY",
    "COMMIT_BODY",
    "COMMIT_SUBJECT",
    "FINDING_SAID",
    "TERMINAL_LINE",
    "comment_body",
    "commit_body",
    "words",
]

#: A pull request comment without its findings list or trailer, in words.
COMMENT_BODY = 55
#: One finding's `said` half, in words.
FINDING_SAID = 32
#: The line beside a feedstock in the terminal, in words.
TERMINAL_LINE = 12
#: A commit message swage writes: its subject, in characters.
COMMIT_SUBJECT = 60
#: A commit message swage writes: its body without its lists, in words.
COMMIT_BODY = 62

_SPAN = re.compile(r"(`+).*?\1(?!`)")


def words(text: str) -> int:
    """Whitespace-separated words, a code span counting as one."""
    return len(_SPAN.sub("x", text).split())


def comment_body(comment: str) -> str:
    """A comment without its findings list and its trailer."""
    body = comment.partition("\n---\n")[0]
    return "\n".join(line for line in body.splitlines() if not line.startswith("- "))


def commit_body(message: str) -> str:
    """A commit message's prose: after the subject, before the trailer, no lists.

    A list is a paragraph with an indented line in it -- a heading over the
    lines it introduces, which are quoted rather than written.
    """
    paragraphs = message.strip().split("\n\n")[1:-1]
    return "\n\n".join(
        paragraph
        for paragraph in paragraphs
        if not any(line.startswith(" ") for line in paragraph.splitlines())
    )
