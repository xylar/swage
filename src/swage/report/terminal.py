"""The grouped terminal summary (DESIGN.md 9).

Modeled on the airflow tool's ranked, colorized summary, which DESIGN.md 9
calls genuinely good and worth keeping. What it keeps is the shape -- grouped
by outcome, counts in the heading, the actionable buckets unmissable -- and
the color conventions, down to honoring `NO_COLOR` and `CLICOLOR_FORCE` the
way that tool already does.

**Which feedstocks get listed by name is a property of the record, not a list
in this file.** A record carries a `detail` when there is something to say
about that feedstock specifically, and those are exactly the ones worth
printing: the failing gate, the reason it stopped, the API call that did not
land. `UNCHANGED (206)` needs no 206 lines saying "no open bot PR", and would
bury the nine that need reading. So the rule is "list what has something to
say", which means a new outcome that needs listing gets it by having something
to say rather than by being added here.

A `notes` entry counts as having something to say (DESIGN.md 4). It is how a
feedstock with no failing gate still gets named -- `MERGE-READY` beside a note
that upstream declares an extra nothing draws on. Notes print *under* the
detail line rather than beside the name, because they are advice about the
feedstock rather than the reason it is in this bucket, and running them into
the same column would make the two indistinguishable.
"""

from __future__ import annotations

import os
import shutil
import sys
import textwrap
from collections.abc import Iterator, Mapping
from pathlib import Path

from .build import was_shortened
from .model import OUTCOMES, FeedstockRecord, RunRecord

__all__ = ["render_summary", "supports_color"]

#: Inherited from the tool this replaces rather than invented: bright red for
#: what failed, green for what landed, blue for what is in flight, yellow for
#: what wants a human, cyan for what did nothing.
_COLORS = {
    "merged": "1;32",
    "closed": "1;36",
    "ready-to-merge": "1;32",
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

#: Yellow, the color this report already uses for what wants a human. A dry
#: run is exactly that: swage has a change ready and is waiting to be told.
_BANNER = "1;33"

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
    counted: str = "scanned",
    banner: str = "",
) -> str:
    """Render the whole run as the terminal summary of DESIGN.md 9.

    ``descriptions`` replaces what a bucket says it means, for a command that
    did not do what the default wording claims. A read-only `scan` produces
    the same `merge-ready` records as `update` -- an outcome is a statement
    about the gates rather than about what was written -- but a bucket reading
    "pushed + labeled automerge" would describe something `scan` is
    structurally incapable of. The vocabulary stays; only the sentence moves.

    ``counted`` is the verb in the header's tally, for the same reason.
    `swage status` did not scan feedstocks -- it followed up pull requests
    earlier runs acted on, and a header claiming a sweep it did not make would
    be the one line of the report a reader takes on trust.

    ``banner`` states something about the run as a whole, above every bucket.
    It exists because whether `update` wrote anything was inferable only from
    two bucket descriptions, and a feedstock that lands in neither -- one held
    for review, which is most of them -- produced a dry run and an `--execute`
    run that were byte-identical. Which of those a reader is looking at is not
    a detail about one feedstock, so it does not belong in a bucket.
    """
    columns = width or _terminal_width()
    said = descriptions or {}
    paint = _painter(supports_color() if color is None else color)
    # One name column for the whole run rather than one per bucket, so every
    # detail in the report starts at the same place and the eye can run down
    # them. Per-bucket widths would step in and out for no reason a reader
    # could infer.
    listed = [record for record in run.feedstocks if _says_something(record)]
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
        if _says_something(record):
            yield from _detail(record, names, columns)


def _says_something(record: FeedstockRecord) -> bool:
    """Whether this feedstock is worth naming in the summary at all."""
    return bool(record.detail or record.notes)


#: The outcomes that name a pull request worth opening, which are the ones that
#: get its address printed under them. DESIGN.md 9: swage cannot merge, so the
#: most useful thing it can do about a pull request that is ready is put it one
#: click away.
#:
#: `merged` and `closed` are here for a different reason than the rest. Nothing
#: is being asked of the reader -- both are finished -- but they are the answer
#: to "what did swage do while I was asleep", and that answer is only worth
#: anything if the pull request it names can be read.
_LINKED = frozenset(
    {"merged", "closed", "ready-to-merge", "proposed", "degraded", "needs-review"}
)

#: How many notes a feedstock gets before the rest are counted instead.
#:
#: The same rule a gate's detail already follows -- name the first reasons and
#: count the remainder, because `explain` is where all of them live. It became
#: load-bearing when `audit` started reporting every feedstock rather than the
#: handful with an open pull request: `google-cloud-aiplatform` declares 35
#: extras no output draws on, and printing every one of them filled half a
#: fifty-feedstock report with a single feedstock's advisories and buried the
#: line naming the decision it actually needs.
_NOTES = 3


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
    if record.detail:
        yield f"{left}{wrapped[0]}"
        for extra in wrapped[1:]:
            yield f"{' ' * len(left)}{extra}"
    elif record.notes:
        # No detail to hang the name on, so the name gets its own line and the
        # notes sit under it like they would under a detail.
        yield left.rstrip()
    for note in record.notes[:_NOTES]:
        for piece in textwrap.wrap(
            f"note: {note}", body, break_long_words=False, break_on_hyphens=False
        ):
            yield f"{' ' * len(left)}{piece}"
    counted = max(0, len(record.notes) - _NOTES)
    if counted:
        yield f"{' ' * len(left)}note: and {counted} more"
    if counted or was_shortened(record.detail):
        # On its own line and never wrapped, for the reason the URL below is
        # not: a command broken across two lines is a command nobody can
        # paste. It is printed only where the line above is not the whole
        # story -- a maintainer sent to another command for a detail that was
        # already in front of them has been given an errand, not an answer.
        yield (
            f"{' ' * len(left)}for the full explanation, run: "
            f"swage explain {record.feedstock}"
        )
    if record.outcome in _LINKED and record.pull_request is not None:
        # Never wrapped, whatever the terminal width: a URL broken across two
        # lines is a URL nobody can click and nobody can paste.
        yield f"{' ' * len(left)}{_url(record)}"


def _url(record: FeedstockRecord) -> str:
    """Where the pull request is, spelled out rather than reconstructed.

    Built here rather than stored, because it is derivable from two fields the
    record already has and a URL in `run.json` would be a second thing to keep
    true. Terminals linkify a bare `https://` and most of them make it
    clickable, which is the whole point.
    """
    return (
        f"https://github.com/conda-forge/{record.feedstock}-feedstock"
        f"/pull/{record.pull_request}"
    )


def _header(run: RunRecord, columns: int, counted: str = "scanned") -> str:
    right = f"({len(run.feedstocks)} {counted})"
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
