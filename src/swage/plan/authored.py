"""Telling swage's own comments apart from a maintainer's (v1 §6.1).

Comments swage authors are regenerated from the plan every run; everything else
is preserved and re-anchored. A comment swage no longer writes is still swage's,
so retired wordings are recognized too. Retiring a convention means adding to
`_RETIRED`, never editing `_CURRENT`.
"""

from __future__ import annotations

import re

__all__ = ["is_swage_authored", "maintainer_comments"]

#: Comments swage generates today, from `assemble` and `reconcile`.
_CURRENT = (
    # `# from the bqstorage extra` -- an extra's block header (design-v1.md 6).
    re.compile(r"^#\s*from the \S+ extra$"),
    # `# start pyhive[hive-pure-sasl]` / `# end pyhive[hive-pure-sasl]` --
    # the embedded-extras round-trip markers.
    re.compile(r"^#\s*(?:start|end) \S+$"),
    # The marker note, in its three shapes: a floor, a ceiling, and both at
    # once.
    re.compile(r"^#\s*tightest of upstream's (?:floors|ceilings)\b.*$"),
    # The caption on a dependency whose extra config settled as pulling nothing
    # in. The `name[extra]` shape is required, which keeps it off a maintainer's
    # sentence.
    re.compile(r"^#\s*\S+\[[^\]]+\] needs nothing extra on conda-forge$"),
)

#: Wordings swage used to generate, or that the tools it replaces generated.
#: This list only grows.
_RETIRED = (
    # The marker note as the airflow tool wrote it, and as swage did first
    # (v1 §3.3.1).
    re.compile(r"^#\s*more restrictive for .+$"),
    # The google-cloud tool's spelling of the same note.
    re.compile(r"^#\s*more restrictive constraint for .+$"),
    # A third spelling of the marker note, in two shapes.
    re.compile(r"^#\s*strictest constraint for .+$"),
    re.compile(r"^#\s*strictest lower bound for .+$"),
    # `# conditional for python <3.13`, above each of a pair of lines swage
    # collapses into one. Anchored on `python`.
    re.compile(r"^#\s*conditional for python\b.*$"),
    # `# graphviz extra`, the hand-written shorthand for an extra's block
    # header. Exactly one token before `extra`.
    re.compile(r"^#\s*\S+ extra$"),
    # `# name[extra]`, the hand-written label above an expansion block. The
    # whole comment has to be one token.
    re.compile(r"^#\s*\S+\[[^\]]+\]$"),
)


def is_swage_authored(comment: str) -> bool:
    """Whether this comment is one swage renders rather than preserves. A blank
    line is never swage's.
    """
    if not comment:
        return False
    stripped = comment.strip()
    return any(pattern.match(stripped) for pattern in (*_CURRENT, *_RETIRED))


def maintainer_comments(comments: tuple[str, ...]) -> tuple[str, ...]:
    """The comments above a requirement that swage did not write."""
    return tuple(comment for comment in comments if not is_swage_authored(comment))
