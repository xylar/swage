"""Telling swage's own comments apart from a maintainer's (v1 §6.1).

Comments swage authors are regenerated from the plan every run; everything else
is preserved and re-anchored. A comment swage no longer writes is still swage's,
so retired wordings are recognized too. Retiring a convention means adding to
`_RETIRED`, never editing `_CURRENT`.
"""

from __future__ import annotations

import re

__all__ = ["is_swage_authored", "maintainer_comments", "repeated_header"]

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
    # The note above a line `overruled_constraints` settled. Matched whole: it
    # has no variable part.
    re.compile(
        r"^#\s*upstream's bound varies by python; "
        r"this package is built once for all of them$"
    ),
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
)

#: Hand-written labels that say what one of swage's headers says, each with
#: the header it repeats. A person writes these as readily as a tool did, so
#: one is never swage's on its shape alone: it goes only where swage writes
#: the header it repeats (DESIGN.md §16). virtualenv's `# python-sbom[hatch]`
#: was deleted while it was still the only note of where `hatchling` came from.
_LABELS = (
    # `# graphviz extra`, the shorthand for an extra's block header. Exactly
    # one token before `extra`.
    (re.compile(r"^#\s*(\S+) extra$"), "# from the {} extra"),
    # `# name[extra]`, the label above an expansion block. The whole comment
    # has to be one token.
    (re.compile(r"^#\s*(\S+\[[^\]]+\])$"), "# start {}"),
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


def repeated_header(comment: str) -> str | None:
    """The header of swage's that this hand-written label repeats, or None."""
    stripped = comment.strip()
    for pattern, header in _LABELS:
        if match := pattern.match(stripped):
            return header.format(match.group(1))
    return None
