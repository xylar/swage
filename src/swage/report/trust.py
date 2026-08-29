"""Which feedstocks the recorded audits say have earned a trust rung, and the
report `swage trust` prints (DESIGN.md 8.4).

Promoting a feedstock to `auto` is a claim that it behaves, and the evidence
for it is a run of fleet audits in which approval was the only thing
outstanding. That evidence was assembled by hand for the first batch of a
hundred, out of a throwaway script over two `run.json` files -- a claim nobody
else can re-derive, and a script the next batch would have had to write again.
This is that script, kept.

**It reads swage's own runs and nothing else.** No GitHub, no archives, no
planning: every fact it needs was recorded by the audits it summarizes. The
evidence is the point, so the header says what was read and the count in the
heading is `states` rather than `audits` -- a replayed audit is the same
reading again.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from swage.config import ConfigTree

from .artifact import RECIPES_DIR, read_run
from .errors import ReportError
from .model import FeedstockRecord, RunRecord

__all__ = [
    "TRUST_READINGS",
    "Earned",
    "FleetState",
    "earned",
    "fleet_states",
    "render_trust",
]

#: How many readings of the fleet to require agreement from, by default.
#:
#: **Counted in readings rather than in days, because the fleet moves.** A
#: window of a month held 48 distinct readings on the machine this was written
#: on -- the bot files a pull request, a maintainer merges one, and the next
#: live sweep reads a fleet that is not the one before it. Requiring agreement
#: from all 48 left four candidates, none of which says anything about the
#: other four hundred: a feedstock is disqualified by any single reading in
#: which its release happened to be mid-flight.
#:
#: Three consecutive readings is a claim somebody can check and act on -- the
#: three are named, with their dates -- and asking for more is one flag away.
TRUST_READINGS = 3

#: What a fleet audit's command line looks like. A `--feedstock` run says
#: nothing about the feedstocks it did not read, and treating one as a fleet
#: reading would let a feedstock qualify by never having been looked at.
_FLEET = "audit --all"

#: The outcomes that are evidence for a rung. `proposed` says every check but
#: approval passed; `unchanged` says the recipe already reads as swage would
#: write it, with nothing but approval outstanding either (§8.2) -- which is
#: the same claim with the diff removed, and the stronger of the two.
_EARNED = frozenset({"proposed", "unchanged"})

_OUTPUTS = re.compile(r"(\d+) output")
_NOARCH = re.compile(r"^\s*noarch:\s*python\s*$", re.MULTILINE)


@dataclass(frozen=True)
class FleetState:
    """One reading of the fleet, and every audit that reported on it.

    **The unit of evidence is the fleet as it was read, not the run that read
    it.** `swage audit --all --cached` replays recorded reads, so a day of
    developing swage leaves a dozen audits of one fleet -- and counting those
    as a dozen readings would inflate the evidence for a promotion by the
    number of times somebody re-ran a sweep. Runs are grouped by the bytes
    they read, and each state is judged by its newest audit, which is the one
    the current swage produced.
    """

    fingerprint: str
    #: When each audit of this state started, oldest first.
    audits: tuple[str, ...]
    directory: Path
    record: RunRecord

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


def _qualifies(record: FeedstockRecord) -> bool:
    """Whether this audit found approval the only thing outstanding.

    `recipe` has to be there. `unchanged` is also what an org team with no
    repository behind it comes back as, and a feedstock swage never read is
    not one it found nothing wrong with.
    """
    return record.outcome in _EARNED and bool(record.recipe)


def _fingerprint(directory: Path) -> str:
    """What this audit read, as one digest.

    The recipes as they stood, which is exactly what `--cached` guarantees it
    replayed. Two audits agreeing here read the same fleet, whether or not one
    of them fetched it.
    """
    digest = hashlib.sha256()
    for path in sorted((directory / RECIPES_DIR).glob("*/recipe.before.yaml")):
        digest.update(path.parent.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def fleet_states(
    directories: Sequence[Path], readings: int = TRUST_READINGS
) -> tuple[tuple[FleetState, ...], int]:
    """The most recent ``readings`` readings of the fleet, oldest first.

    Walked newest first and stopped once enough distinct readings are in hand,
    which is what keeps this cheap: a machine developing swage accumulates
    hundreds of run directories, and fingerprinting one means reading every
    recipe it recorded.

    The count returned beside them is how many runs could not be read, for the
    same reason `swage status` counts rather than lists them: a window quietly
    covering less than it claims is how a report comes back clean by having
    looked at less. Only runs this walk actually reached are counted, since a
    run it never opened was not left out of anything.
    """
    grouped: dict[str, list[tuple[Path, RunRecord]]] = {}
    skipped = 0
    for directory in reversed(directories):
        if len(grouped) >= readings:
            break
        try:
            record = read_run(directory)
        except ReportError:
            skipped += 1
            continue
        if _FLEET not in record.command:
            continue
        fingerprint = _fingerprint(directory)
        if fingerprint not in grouped and len(grouped) >= readings:
            break
        grouped.setdefault(fingerprint, []).append((directory, record))

    states = []
    for fingerprint, runs in grouped.items():
        runs.sort(key=lambda pair: pair[1].started)
        directory, record = runs[-1]
        states.append(
            FleetState(
                fingerprint=fingerprint,
                audits=tuple(run.started for _, run in runs),
                directory=directory,
                record=record,
            )
        )
    states.sort(key=lambda state: state.first)
    return tuple(states), skipped


def earned(states: Sequence[FleetState], tree: ConfigTree) -> tuple[Earned, ...]:
    """The feedstocks every reading agrees have approval outstanding and nothing else.

    Every reading, rather than most or the newest: a feedstock absent from an
    older one is a feedstock swage has read once, and one that qualified then
    and not now is one something has changed about. Both wait for the next
    audit, which is the conservative direction and costs nothing.
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
    """Which batch's argument this feedstock would be promoted by.

    A batch's reason has to be true of everyone in it (DESIGN.md 5.4), so what
    a candidate list is for is saying which feedstocks one sentence could
    cover. A family is that answer where there is one -- its members are
    already asserted to behave alike -- and the shape of the recipe is the
    answer everywhere else, because that is what decides how much a wrong
    line costs.
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
    outputs = _OUTPUTS.search(record.recipe)
    if outputs is not None and int(outputs.group(1)) > 1:
        return "several outputs"
    if config.extras_as_outputs is not None:
        return "publishes extras"
    return "one noarch: python output, no extras published"


#: Where the answer goes, which is the one thing a reader of this listing has
#: left to do. The reason is theirs to write: a stub that filled it in would
#: be swage arguing for its own promotion.
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
) -> str:
    """The whole report, as one string."""
    return "\n".join(_lines(states, found, skipped, width))


def _lines(
    states: Sequence[FleetState],
    found: Sequence[Earned],
    skipped: int,
    width: int,
) -> Iterator[str]:
    plural = "" if len(states) == 1 else "s"
    yield f"swage trust    --readings {len(states)}"
    yield ""
    yield f"  {len(states)} reading{plural} of the fleet{_span(states)}, newest last:"
    for state in states:
        yield f"    {_when(state)}"
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
    """How long the readings cover, where that is more than a moment.

    Three readings taken in one afternoon and three taken over a fortnight are
    different evidence for the same claim, and the difference is invisible in
    the count. The dates below say it too, but a reader deciding whether to
    promote a hundred feedstocks should not have to subtract them.
    """
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
    """One reading: when it was read, and by how many audits.

    The date is when the fleet was read, which is the first audit of it --
    several audits of one reading is the ordinary case while swage is being
    developed, since `audit --all --cached` replays what the last live sweep
    recorded, and a replay is not a second reading. The verdicts quoted here
    come from the newest of those audits, so they are the current swage's.
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
    """The names, comma-separated, filling the terminal rather than a column.

    A line each would run to thirty lines for one family, and what a reader
    does with this list is copy it -- so it is laid out to be read as a set and
    pasted as one.
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
