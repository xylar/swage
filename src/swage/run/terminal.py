"""The grouped terminal summary (v1 §9; DESIGN.md §11.3).

Grouped by outcome, counts in the heading, the actionable buckets first. A
feedstock is listed by name where its record has something to say, a `reason` or
a note, and always where the reader named it on the command line. Notes print
under the reason, not beside the name.
"""

from __future__ import annotations

import os
import shutil
import sys
import textwrap
from collections.abc import Collection, Iterator, Mapping
from pathlib import Path

from .artifact import DECLARATIONS_DIR
from .record import OUTCOMES, Record, Run, is_known, was_shortened

__all__ = ["render_summary", "supports_color"]

#: Inherited from the tool this replaces: red for what failed, green for what
#: landed, blue for what is in flight, yellow for what wants a human, cyan for
#: what did nothing.
_COLORS = {
    "merged": "1;32",
    "closed": "1;36",
    "ready-to-merge": "1;32",
    "automerge": "1;34",
    "labeled": "1;34",
    "awaiting-ci": "1;34",
    "needs-review": "1;33",
    "migrated": "1;32",
    "needs-migration": "1;33",
    "unchanged": "1;36",
    # Yellow with the other buckets that want a person, cyan with the ones
    # that want nothing.
    "declaration-moved": "1;33",
    "not-read": "1;36",
    "failed": "1;31",
}

#: Yellow, the color this report already uses for what wants a human. A dry
#: run is exactly that: swage has a change ready and is waiting to be told.
_BANNER = "1;33"

#: The column the bucket descriptions start at (v1 §9): one past the longest
#: heading.
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
    run: Run,
    run_directory: Path | None = None,
    width: int | None = None,
    color: bool | None = None,
    descriptions: Mapping[str, str] | None = None,
    counted: str = "scanned",
    banner: str = "",
    named: Collection[str] = (),
) -> str:
    """Render the whole run as the terminal summary (v1 §9).

    ``descriptions`` replaces what a bucket says it means, for a command that
    did not do what the default wording claims; ``counted`` is the verb in the
    header's tally, for the same reason. ``named`` is the feedstocks the
    reader asked for by name, each listed whatever bucket it lands in.
    ``banner`` states something about the run as a whole, above every bucket.
    """
    columns = width or _terminal_width()
    said = descriptions or {}
    paint = _painter(supports_color() if color is None else color)
    # One name column for the whole run rather than one per bucket.
    listed = [record for record in run.feedstocks if _says_something(record, named)]
    names = max((len(record.feedstock) for record in listed), default=0)
    lines = [_header(run, columns, counted), ""]
    if banner:
        lines.extend([f"{' ' * _INDENT}{paint(banner, _BANNER)}", ""])
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
                run_directory,
                named,
            )
        )
    lines.extend(_unknown(run, names, columns, paint, named))
    # A diff ends with a blank line so the next bucket does not read as part
    # of the file; the last one in the report has nothing to be separated from.
    while lines and not lines[-1]:
        lines.pop()
    if run_directory is not None:
        # Trailing separator because it is a directory, in the separator this
        # platform actually uses.
        lines.extend(["", f"{' ' * _INDENT}run: {_tilde(run_directory)}{os.sep}"])
    return "\n".join(lines) + "\n"


def _unknown(
    run: Run,
    names: int,
    columns: int,
    paint: _Painter,
    named: Collection[str] = (),
) -> list[str]:
    """Whatever this swage has no row in `OUTCOMES` for, printed anyway in one
    bucket, in the order the run recorded them (DESIGN.md §11.1).
    """
    records = tuple(record for record in run.feedstocks if not is_known(record.outcome))
    if not records:
        return []
    outcomes = sorted({record.outcome for record in records})
    return list(
        _bucket(
            records,
            "failed",
            "UNRECOGNIZED",
            f"{', '.join(outcomes)} -- written by a newer swage than this one",
            names,
            columns,
            paint,
            None,
            named,
        )
    )


def _bucket(
    records: tuple[Record, ...],
    outcome: str,
    heading: str,
    description: str,
    names: int,
    columns: int,
    paint: _Painter,
    run_directory: Path | None = None,
    named: Collection[str] = (),
) -> Iterator[str]:
    label = f"{heading} ({len(records)})"
    painted = paint(label, _COLORS.get(outcome))
    padding = " " * max(1, _COLUMN - _INDENT - len(label))
    yield f"{' ' * _INDENT}{painted}{padding}{description}".rstrip()
    for record in records:
        if _says_something(record, named):
            yield from _entry(record, names, columns, run_directory)


def _says_something(record: Record, named: Collection[str] = ()) -> bool:
    """Whether this feedstock is worth naming in the summary at all."""
    return bool(
        record.reason
        or record.notes
        or record.declaration_diff
        or record.feedstock in named
    )


#: The outcomes that get the pull request's address printed under them (v1 §9).
#: `merged` and `closed` because they are the answer to what happened overnight;
#: `awaiting-ci` because its line asks for something with a deadline
#: (docs/conda-forge.md); `needs-migration` because the line asks for a rerun
#: against that pull request.
_LINKED = frozenset(
    {
        "merged",
        "closed",
        "ready-to-merge",
        "awaiting-ci",
        "needs-review",
        "needs-migration",
    }
)

#: How many lines of a declaration diff the summary prints before naming the
#: file that holds the rest: about a screen.
_DIFF_LINES = 40

#: How many notes a feedstock gets before the rest are counted instead; the rule
#: a check's findings already follow.
_NOTES = 3


def _entry(
    record: Record,
    names: int,
    columns: int,
    run_directory: Path | None = None,
) -> Iterator[str]:
    """One feedstock, with its reason wrapped under itself rather than beside."""
    left = f"{' ' * (_INDENT + 2)}{record.feedstock.ljust(names)}  "
    body = max(20, columns - len(left))
    # Long words and hyphens are never broken: a URL or a package name split
    # across two lines is one nobody can copy.
    wrapped = textwrap.wrap(
        record.reason, body, break_long_words=False, break_on_hyphens=False
    ) or [""]
    if record.reason:
        yield f"{left}{wrapped[0]}"
        for extra in wrapped[1:]:
            yield f"{' ' * len(left)}{extra}"
    else:
        # No reason to hang the name on, so the name gets its own line.
        yield left.rstrip()
    for note in record.notes[:_NOTES]:
        for piece in textwrap.wrap(
            f"note: {note}", body, break_long_words=False, break_on_hyphens=False
        ):
            yield f"{' ' * len(left)}{piece}"
    counted = max(0, len(record.notes) - _NOTES)
    if counted:
        yield f"{' ' * len(left)}note: and {counted} more"
    if counted or was_shortened(record.reason):
        # On its own line and never wrapped: a command broken across two lines
        # is a command nobody can paste. Printed only where the line above is
        # not the whole story.
        yield (
            f"{' ' * len(left)}for the full explanation, run: "
            f"swage explain {record.feedstock}"
        )
    if record.outcome in _LINKED and record.pull_request is not None:
        # Never wrapped, whatever the terminal width: a URL broken across two
        # lines is a URL nobody can click and nobody can paste.
        yield f"{' ' * len(left)}{_url(record)}"
    yield from _diff(record, len(left), run_directory)


def _diff(record: Record, indent: int, run_directory: Path | None) -> Iterator[str]:
    """What this release did to a declaration swage cannot read (v1 §3.6.8).

    Never wrapped, and capped rather than complete; the last line names the
    file `write_declarations` wrote.
    """
    if not record.declaration_diff:
        return
    lines = record.declaration_diff.splitlines()
    for line in lines[:_DIFF_LINES]:
        yield f"{' ' * (indent + 2)}{line}"
    rest = len(lines) - _DIFF_LINES
    if rest > 0:
        where = ""
        if run_directory is not None:
            path = run_directory / DECLARATIONS_DIR / f"{record.feedstock}.diff"
            where = f": {_tilde(path)}"
        yield f"{' ' * (indent + 2)}... and {rest} more lines{where}"
    # A blank line under it, because a diff is a block.
    yield ""


def _url(record: Record) -> str:
    """Where the pull request is, built from two fields the record already has."""
    return (
        f"https://github.com/conda-forge/{record.feedstock}-feedstock"
        f"/pull/{record.pull_request}"
    )


def _header(run: Run, columns: int, counted: str = "scanned") -> str:
    right = f"({len(run.feedstocks)} {counted})"
    stamp = run.started[:16].replace("T", " ")
    left = f"{run.command}{'    ' if run.command else ''}{stamp}".rstrip()
    gap = columns - len(left) - len(right)
    return f"{left}{' ' * gap}{right}" if gap > 0 else f"{left}  {right}"


def _terminal_width() -> int:
    # Capped rather than used raw: the reasons are prose, and prose set across
    # a 200-column terminal is not readable in the way a wide table is.
    return min(shutil.get_terminal_size(fallback=(88, 24)).columns, 100)


def _tilde(path: Path) -> str:
    """Abbreviate under the home directory, in the platform's own separators."""
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
