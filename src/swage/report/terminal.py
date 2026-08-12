"""The grouped terminal summary (DESIGN.md 9).

Modelled on the airflow tool's ranked, colorized summary, which DESIGN.md 9
calls genuinely good and worth keeping. What it keeps is the shape -- grouped
by outcome, counts in the heading, the actionable buckets unmissable -- and
the colour conventions, down to honouring `NO_COLOR` and `CLICOLOR_FORCE` the
way that tool already does.

**Which feedstocks get listed by name is a property of the record, not a list
in this file.** A record carries a `detail` when there is something to say
about that feedstock specifically, and those are exactly the ones worth
printing: the failing gate, the reason it stopped, the API call that did not
land. `UNCHANGED (206)` needs no 206 lines saying "no open bot PR", and would
bury the nine that need reading. So the rule is "list what carries a detail",
which means a new outcome that needs listing gets it by having something to
say rather than by being added here.
"""

from __future__ import annotations

import os
import shutil
import sys
import textwrap
from collections.abc import Iterator, Mapping
from pathlib import Path

from .model import OUTCOMES, FeedstockRecord, RunRecord

__all__ = ["render_summary", "supports_color"]

#: Inherited from the tool this replaces rather than invented: bright red for
#: what failed, green for what landed, blue for what is in flight, yellow for
#: what wants a human, cyan for what did nothing.
_COLORS = {
    "merged": "1;32",
    "merge-ready": "1;34",
    "awaiting-ci": "1;34",
    "proposed": "1;34",
    "needs-review": "1;33",
    "degraded": "1;31",
    "migrated": "1;32",
    "needs-migration": "1;33",
    "unchanged": "1;36",
    "failed": "1;31",
}

#: The absolute column the bucket descriptions start at, as DESIGN.md 9 sets
#: them. `NEEDS MIGRATION (18)` is the longest heading and clears it by one
#: space, which is what fixes the number at 23 rather than anything rounder.
_COLUMN = 23
_INDENT = 2


def supports_color(stream: object = None) -> bool:
    """Whether to emit ANSI codes, by the rules the prior art already uses."""
    if os.environ.get("NO_COLOR"):
        return False
    forced = os.environ.get("CLICOLOR_FORCE")
    if forced not in (None, "", "0"):
        return True
    out = stream if stream is not None else sys.stdout
    isatty = getattr(out, "isatty", None)
    return bool(
        callable(isatty)
        and isatty()
        and os.environ.get("TERM") not in (None, "", "dumb")
    )


def render_summary(
    run: RunRecord,
    run_directory: Path | None = None,
    width: int | None = None,
    color: bool | None = None,
    descriptions: Mapping[str, str] | None = None,
) -> str:
    """Render the whole run as the terminal summary of DESIGN.md 9.

    ``descriptions`` replaces what a bucket says it means, for a command that
    did not do what the default wording claims. A read-only `scan` produces
    the same `merge-ready` records as `update` -- an outcome is a statement
    about the gates rather than about what was written -- but a bucket reading
    "pushed + labeled automerge" would describe something `scan` is
    structurally incapable of. The vocabulary stays; only the sentence moves.
    """
    columns = width or _terminal_width()
    said = descriptions or {}
    paint = _painter(supports_color() if color is None else color)
    # One name column for the whole run rather than one per bucket, so every
    # detail in the report starts at the same place and the eye can run down
    # them. Per-bucket widths would step in and out for no reason a reader
    # could infer.
    listed = [record for record in run.feedstocks if record.detail]
    names = max((len(record.feedstock) for record in listed), default=0)
    lines = [_header(run, columns), ""]
    for outcome, heading, description in OUTCOMES:
        records = run.by_outcome(outcome)
        if not records:
            # An empty bucket is noise. A run over one family should not print
            # the eight outcomes it could not possibly have produced.
            continue
        lines.extend(
            _bucket(
                records,
                outcome,
                heading,
                said.get(outcome, description),
                names,
                columns,
                paint,
            )
        )
    if run_directory is not None:
        # Trailing separator because it is a directory, in the separator this
        # platform actually uses.
        lines.extend(["", f"{' ' * _INDENT}run: {_tilde(run_directory)}{os.sep}"])
    return "\n".join(lines) + "\n"


def _bucket(
    records: tuple[FeedstockRecord, ...],
    outcome: str,
    heading: str,
    description: str,
    names: int,
    columns: int,
    paint: _Painter,
) -> Iterator[str]:
    label = f"{heading} ({len(records)})"
    painted = paint(label, _COLORS.get(outcome))
    padding = " " * max(1, _COLUMN - _INDENT - len(label))
    yield f"{' ' * _INDENT}{painted}{padding}{description}".rstrip()
    for record in records:
        if record.detail:
            yield from _detail(record, names, columns)


def _detail(record: FeedstockRecord, names: int, columns: int) -> Iterator[str]:
    """One feedstock, with its detail wrapped under itself rather than beside."""
    left = f"{' ' * (_INDENT + 2)}{record.feedstock.ljust(names)}  "
    body = max(20, columns - len(left))
    # Long words are never broken, and neither are hyphens. A detail routinely
    # contains a URL or a package name, and `https://github.com/dpgaspar/Flask-`
    # split across two lines is a URL nobody can copy and a name nobody can
    # grep -- overflowing the column is the smaller cost.
    wrapped = textwrap.wrap(
        record.detail, body, break_long_words=False, break_on_hyphens=False
    ) or [""]
    yield f"{left}{wrapped[0]}"
    for extra in wrapped[1:]:
        yield f"{' ' * len(left)}{extra}"


def _header(run: RunRecord, columns: int) -> str:
    right = f"({len(run.feedstocks)} scanned)"
    stamp = run.started[:16].replace("T", " ")
    left = f"{run.command}{'    ' if run.command else ''}{stamp}".rstrip()
    gap = columns - len(left) - len(right)
    return f"{left}{' ' * gap}{right}" if gap > 0 else f"{left}  {right}"


def _terminal_width() -> int:
    # Capped rather than used raw: the details are prose, and prose set across
    # a 200-column terminal is not readable in the way a wide table is.
    return min(shutil.get_terminal_size(fallback=(88, 24)).columns, 100)


def _tilde(path: Path) -> str:
    """Abbreviate under the home directory, in the platform's own separators.

    Built with pathlib rather than by gluing on `~/`, because on Windows that
    produced `~/AppData\\Local\\Temp\\...` -- a path in two separator
    conventions at once, which is a path you cannot paste back into anything.
    """
    try:
        relative = path.relative_to(Path.home())
    except ValueError:
        return str(path)
    return str(Path("~") / relative)


class _Painter:
    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def __call__(self, text: str, code: str | None) -> str:
        if not self.enabled or not code:
            return text
        return f"\033[{code}m{text}\033[0m"


def _painter(enabled: bool) -> _Painter:
    return _Painter(enabled)
