"""`swage explain`, rendered from the record (DESIGN.md 9.2).

Nothing here recomputes anything, and that is the whole design rather than an
implementation convenience. The question `explain` answers is "why did it do
*that*, at 03:00, while I was asleep" -- by the time it is asked, upstream has
moved on and config may have changed, so recomputing would answer a different
question and a second computation path would be a second thing that can drift
from the planner. Rendering the stored record means `explain` cannot disagree
with what actually happened.

The four sections are ordered the way the questions actually get asked, which
puts gates and verdict last: "why did this not merge" is what made someone run
the command.
"""

from __future__ import annotations

import textwrap
from collections.abc import Iterator

from .model import FeedstockRecord, PlannedLine, SectionRecord

__all__ = ["render_explain"]

#: The first token of a planned line, so a plan reads as a diff at a glance
#: (DESIGN.md 9.2). Anything unrecognized prints as itself rather than being
#: dropped -- a record from a newer swage may know actions this one does not.
_ACTIONS = {"keep": "keep", "bump": "~bump", "add": "+add", "drop": "-drop"}

_LABEL = 12


def render_explain(record: FeedstockRecord, run: str = "", width: int = 88) -> str:
    """Render one feedstock's record as the report of DESIGN.md 9.2."""
    lines = list(_header(record, run, width))
    lines.extend(_inputs(record, width))
    if record.stopped:
        # An empty plan would be the least helpful possible answer to "what
        # happened", so a feedstock that never got one still explains itself.
        lines.extend(_stopped(record, width))
    else:
        for section in record.sections:
            lines.extend(_plan(section))
    if record.gates:
        lines.extend(_gates(record))
        lines.extend(_verdict(record))
    return "\n".join(lines) + "\n"


def _header(record: FeedstockRecord, run: str, width: int) -> Iterator[str]:
    left = f"swage explain {record.feedstock}"
    right = f"run {run}" if run else ""
    gap = width - len(left) - len(right)
    yield (
        f"{left}{' ' * gap}{right}".rstrip() if gap > 0 else f"{left}  {right}".rstrip()
    )
    yield ""


def _inputs(record: FeedstockRecord, width: int) -> Iterator[str]:
    yield "INPUTS"
    if record.recipe:
        yield _field("recipe", record.recipe)
    if record.pull_request is not None:
        head = f"  head {record.head}" if record.head else ""
        yield _field("bot PR", f"#{record.pull_request}{head}")
    upstream = record.upstream
    if upstream is not None:
        version = f"{upstream.name} {upstream.version or '?'}"
        yield _field("upstream", _pair(version, upstream.source, width))
        if upstream.previous:
            yield _field(
                "",
                _pair(
                    f"previous {upstream.previous}",
                    "for removal classification (3.3.7)",
                    width,
                ),
            )
    if record.python_min:
        yield _field(
            "python_min", _pair(record.python_min, record.python_min_source, width)
        )
    for index, layer in enumerate(record.config_layers):
        yield _field("config" if index == 0 else "", layer)
    yield ""


def _field(label: str, value: str) -> str:
    return f"  {label.ljust(_LABEL)}{value}".rstrip()


def _pair(left: str, right: str, width: int) -> str:
    """Two columns inside one field, as the example sets `3.9` beside its source."""
    if not right:
        return left
    column = min(32, max(0, width - _LABEL - len(right) - 4))
    return f"{left.ljust(column)}  {right}"


def _plan(section: SectionRecord) -> Iterator[str]:
    yield f"PLAN  {section.path}"
    if not section.lines:
        yield "  (nothing)"
        yield ""
        return
    texts = max(len(line.text) for line in section.lines)
    origins = max(len(line.origin) for line in section.lines)
    for line in section.lines:
        yield _line(line, texts, origins)
    yield ""


def _line(line: PlannedLine, texts: int, origins: int) -> str:
    action = _ACTIONS.get(line.action, line.action)
    source = line.source
    if line.exact is False:
        # An inexact resolution is the failure hardest to notice by eye, so it
        # is said out loud rather than left to the reader to infer from G2.
        source = f"{source} (inexact)"
    rendered = (
        f"  {action:>5}  {line.text.ljust(texts)}  "
        f"{line.origin.ljust(origins)}  {source}"
    )
    return rendered.rstrip()


def _stopped(record: FeedstockRecord, width: int) -> Iterator[str]:
    yield "STOPPED"
    for line in record.stopped.splitlines() or [""]:
        yield from textwrap.wrap(
            line, width - 2, initial_indent="  ", subsequent_indent="    "
        ) or ["  "]
    yield ""


def _gates(record: FeedstockRecord) -> Iterator[str]:
    yield "GATES"
    settled = [gate for gate in record.gates if gate.passed is not False]
    if settled:
        yield "  " + "   ".join(
            f"{gate.name} {'pass' if gate.passed else 'n/a'}" for gate in settled
        )
    for gate in record.failures:
        yield f"  {gate.name} FAIL   {gate.detail}".rstrip()
    yield ""


def _verdict(record: FeedstockRecord) -> Iterator[str]:
    failed = ", ".join(gate.name for gate in record.failures)
    reason = f"   ({failed})" if failed else ""
    yield f"VERDICT  {record.label or record.outcome}{reason}"
