"""Which feedstocks the recorded audits say have earned a trust rung, and the
report `swage trust` prints (v1 §8.4; DESIGN.md §11.3).

It reads swage's own runs and nothing else. The unit of evidence is a reading of
the fleet: a live sweep and every replay after it.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from swage.config import ConfigTree

from .artifact import RECIPES_DIR, read_run
from .errors import ReportError
from .record import Record, Run

__all__ = [
    "Earned",
    "FleetState",
    "earned",
    "fleet_states",
    "render_trust",
]

#: What a fleet audit's command line looks like. A `--feedstock` run says
#: nothing about the feedstocks it did not read.
_FLEET = "audit --all"

#: What marks a fleet audit as a replay of the last live sweep's cache rather
#: than a reading of its own (design-v1.md 8.2).
_REPLAY = "--cached"

#: The outcomes that are evidence for a rung on their own: every one says no
#: check but approval was outstanding. `unchanged` (v1 §8.2); `automerge`, which
#: only a promoted feedstock reaches; and `needs-review` with nothing found,
#: which a v1 `proposed` maps to (DESIGN.md §11.3).
_EARNED = frozenset({"unchanged", "automerge"})

_NOARCH = re.compile(r"^\s*noarch:\s*python\s*$", re.MULTILINE)


@dataclass(frozen=True)
class FleetState:
    """One reading of the fleet, and every audit that reported on it.

    A reading is a live sweep and every replay after it, judged by its newest
    audit. Grouped by the sweep, not by the bytes read: a replay after a
    `--feedstock` audit reads one recipe more and is not a new fleet.
    """

    #: When each audit of this state started, oldest first. The first is the
    #: live sweep; the rest replayed it.
    audits: tuple[str, ...]
    directory: Path
    record: Run

    @property
    def first(self) -> str:
        return self.audits[0]

    @property
    def last(self) -> str:
        return self.audits[-1]

    def qualifying(self) -> frozenset[str]:
        """The feedstocks this reading is evidence for."""
        return frozenset(
            record.feedstock for record in self.record.feedstocks if _qualifies(record)
        )


@dataclass(frozen=True)
class Earned:
    """A feedstock every reading agrees about, and the batch it belongs in."""

    feedstock: str
    #: Its family, or the shape of its recipe -- which is what decides the
    #: argument for a batch rather than the feedstock's own name.
    group: str


def _qualifies(record: Record) -> bool:
    """Whether this audit found approval the only thing outstanding. `outputs`
    has to be there: an org team with no repository comes back `unchanged`.
    """
    if not record.outputs:
        return False
    if record.outcome == "needs-review":
        return not record.findings
    return record.outcome in _EARNED


def fleet_states(
    directories: Sequence[Path], readings: int
) -> tuple[tuple[FleetState, ...], int]:
    """The most recent ``readings`` readings of the fleet, oldest first.

    Walked newest first, gathering replays until the live sweep they replayed
    closes the reading. Replays with no live sweep recorded before them are
    one reading of whatever the cache held. The count beside them is how many
    runs this walk reached and could not read.
    """
    states: list[FleetState] = []
    pending: list[tuple[Path, Run]] = []
    skipped = 0
    for directory in reversed(directories):
        if len(states) >= readings:
            break
        try:
            record = read_run(directory)
        except ReportError:
            skipped += 1
            continue
        if _FLEET not in record.command:
            continue
        pending.append((directory, record))
        if _REPLAY not in record.command:
            states.append(_state(pending))
            pending = []
    if pending and len(states) < readings:
        states.append(_state(pending))
    states.reverse()
    return tuple(states), skipped


def _state(runs: list[tuple[Path, Run]]) -> FleetState:
    """One reading out of the live sweep and the replays of it, judged by the newest."""
    runs.sort(key=lambda pair: pair[1].started)
    directory, record = runs[-1]
    return FleetState(
        audits=tuple(run.started for _, run in runs),
        directory=directory,
        record=record,
    )


def earned(states: Sequence[FleetState], tree: ConfigTree) -> tuple[Earned, ...]:
    """The feedstocks every reading agrees have approval outstanding and nothing
    else. Every reading, rather than most or the newest.
    """
    if not states:
        return ()
    agreed = frozenset.intersection(*(state.qualifying() for state in states))
    newest = states[-1]
    found = []
    for feedstock in sorted(agreed):
        config = tree.for_feedstock(feedstock)
        # `auto` has nothing to earn and `never` is a decision, not a gap.
        if config.trust != "propose":
            continue
        found.append(Earned(feedstock, _group(newest, feedstock, tree)))
    return tuple(found)


def _group(state: FleetState, feedstock: str, tree: ConfigTree) -> str:
    """Which batch's argument this feedstock would be promoted by (v1 §5.4): a
    family where there is one, the shape of the recipe otherwise.
    """
    config = tree.for_feedstock(feedstock)
    if config.family is not None:
        return config.family
    record = next(
        item for item in state.record.feedstocks if item.feedstock == feedstock
    )
    recipe = state.directory / RECIPES_DIR / feedstock / "recipe.before.yaml"
    if not recipe.is_file() or not _NOARCH.search(recipe.read_text(encoding="utf-8")):
        return "compiled"
    if len(record.outputs) > 1:
        return "several outputs"
    # `supported`, not the key: a family sets `extras_as_outputs.suffix` as a
    # naming convention for every member.
    if config.extras_as_outputs is not None and config.extras_as_outputs.supported:
        return "publishes extras"
    return "one noarch: python output, no extras published"


#: Where the answer goes. The reason is the reader's to write.
_STUB = """    auto:
      - reason: >-
          What these have in common, and what says so.
        feedstocks:
          - ..."""


def render_trust(
    states: Sequence[FleetState],
    found: Sequence[Earned],
    skipped: int = 0,
    width: int = 88,
    readings: int | None = None,
) -> str:
    """The whole report, as one string. `readings` is how many were asked for;
    where fewer are recorded the report says so.
    """
    return "\n".join(_lines(states, found, skipped, width, readings))


def _lines(
    states: Sequence[FleetState],
    found: Sequence[Earned],
    skipped: int,
    width: int,
    readings: int | None,
) -> Iterator[str]:
    plural = "" if len(states) == 1 else "s"
    yield f"swage trust    --readings {readings or len(states)}"
    yield ""
    yield f"  {len(states)} reading{plural} of the fleet{_span(states)}, newest last:"
    for state in states:
        yield f"    {_when(state)}"
    if readings is not None and len(states) < readings:
        yield (
            f"    ({readings} asked for; a reading is a live `swage audit --all`, "
            f"and only {len(states)} {'is' if len(states) == 1 else 'are'} recorded)"
        )
    if skipped:
        yield (
            f"    ({skipped} run{'' if skipped == 1 else 's'} among them could "
            "not be read, and are left out)"
        )
    yield ""

    if not found:
        yield (
            "NOTHING HAS EARNED A MOVE   every feedstock still at `propose` had "
            "something"
        )
        yield (
            "                            else outstanding in at least one of "
            "those readings"
        )
        yield ""
        return

    in_all = "in it" if len(states) == 1 else f"in all {len(states)}"
    yield (
        f"EARNED A RUNG ({len(found)})   approval outstanding and nothing else, "
        f"{in_all}"
    )
    groups: dict[str, list[str]] = {}
    for item in found:
        groups.setdefault(item.group, []).append(item.feedstock)
    for group, names in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        yield ""
        yield f"  {group} ({len(names)})"
        yield from _wrapped(sorted(names), width)
    yield ""
    yield "To promote a group, add it to config/trust.yaml with the argument for it:"
    yield ""
    yield _STUB
    yield ""


def _span(states: Sequence[FleetState]) -> str:
    """How long the readings cover, where that is more than a moment."""
    if len(states) < 2:
        return ""
    started = datetime.fromisoformat(states[0].first)
    ended = datetime.fromisoformat(states[-1].first)
    hours = (ended - started).total_seconds() / 3600
    if hours < 1:
        return " within the hour"
    if hours < 48:
        counted = round(hours)
        return f" over {counted} hour{'' if counted == 1 else 's'}"
    days = round(hours / 24)
    return f" over {days} days"


def _when(state: FleetState) -> str:
    """One reading: when it was read, which is its first audit, and by how many
    audits. The verdicts quoted come from the newest of them.
    """
    audits = len(state.audits)
    counted = f"{audits} audit{'' if audits == 1 else 's'}"
    return (
        f"{_stamp(state.first)}   {len(state.record.feedstocks)} feedstocks, {counted}"
    )


def _stamp(started: str) -> str:
    """`2026-08-29T08:39:57+00:00` as `2026-08-29 08:39`, which is enough."""
    return started[:16].replace("T", " ")


def _wrapped(names: Sequence[str], width: int) -> Iterator[str]:
    """The names, comma-separated, filling the terminal, to be read as a set
    and pasted as one.
    """
    line = "   "
    for index, name in enumerate(names):
        piece = name if index == len(names) - 1 else f"{name},"
        if len(line) + len(piece) + 1 > width:
            yield line
            line = "   "
        line += f" {piece}"
    if line.strip():
        yield line
