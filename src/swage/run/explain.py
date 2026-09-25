"""`swage explain`, rendered from the record (DESIGN.md §11.3, v1 §9.2).

Nothing here recomputes anything. The sections are ordered the way the questions
get asked, findings and verdict last, CI between them.
"""

from __future__ import annotations

import re
import textwrap
from collections.abc import Iterator

from swage.plan import CHECKS
from swage.plan.prose import output_phrase, section_phrase

from .record import FindingRecord, OutputRecord, PlannedLine, Record, SectionRecord

__all__ = ["render_explain"]

#: The first token of a planned line, so a plan reads as a diff (v1 §9.2).
#: Anything unrecognized prints as itself.
_ACTIONS = {"keep": "keep", "bump": "~bump", "add": "+add", "drop": "-drop"}

_LABEL = 12


def render_explain(record: Record, run: str = "", width: int = 88) -> str:
    """Render one feedstock's record as the report of v1 §9.2."""
    lines = list(_header(record, run, width))
    lines.extend(_inputs(record, width))
    if record.stopped:
        # A feedstock that never got a plan still explains itself, and then says
        # which bucket that put it in.
        lines.extend(_stopped(record, width))
    for section in record.sections:
        lines.extend(_plan(section))
    if record.findings:
        lines.extend(_findings(record, width))
    if record.merge_check is not None:
        lines.extend(_ci(record, width))
    lines.extend(_verdict(record, width))
    return "\n".join(lines) + "\n"


def _header(record: Record, run: str, width: int) -> Iterator[str]:
    left = f"swage explain {record.feedstock}"
    right = f"run {run}" if run else ""
    gap = width - len(left) - len(right)
    yield (
        f"{left}{' ' * gap}{right}".rstrip() if gap > 0 else f"{left}  {right}".rstrip()
    )
    yield ""


def _inputs(record: Record, width: int) -> Iterator[str]:
    yield "INPUTS"
    if record.pull_request is not None:
        head = f"  head {record.head}" if record.head else ""
        # "newest of 4 open" rather than nothing: acting on one of several
        # without saying so hides the rest (v1 §3.4.1).
        others = (
            f"  newest of {record.pull_requests} open"
            if record.pull_requests > 1
            else ""
        )
        yield _field("bot PR", f"#{record.pull_request}{head}{others}")
    upstream = record.upstream
    if upstream is not None:
        version = f"{upstream.name} {upstream.version or '?'}"
        yield _field("upstream", _pair(version, upstream.source, width))
        if upstream.declared_in:
            # Under the release rather than beside it: two steps of one lookup.
            yield _field("", f"declared in {upstream.declared_in}")
        if upstream.previous:
            yield _field(
                "",
                _pair(
                    f"previous {upstream.previous}",
                    "for removal classification (3.3.7)",
                    width,
                ),
            )
    if any(output.name for output in record.outputs):
        for output in record.outputs:
            yield from _output(output, width)
    elif record.outputs:
        yield from _counted_outputs(record.outputs, width)
    for index, layer in enumerate(record.config_layers):
        yield _field("config" if index == 0 else "", layer)
    yield ""


#: How an output's artifacts read (DESIGN.md §9.1): what one marker is read
#: against, said as the fact about the package it is.
_ARTIFACTS = {
    "one": "one noarch package for every python and platform",
    "per-platform": "one noarch package per platform",
    "per-cell": "built once per python and platform",
}


def _output(output: OutputRecord, width: int) -> Iterator[str]:
    """One output's build model, on one line, with its floor under it."""
    said = [output.name]
    if output.artifacts:
        said.append(_ARTIFACTS.get(output.artifacts, output.artifacts))
    if output.pythons:
        said.append(f"python {output.pythons}")
    yield _field("output", "  ".join(said))
    if output.floor:
        yield _field("", _pair(f"floor {output.floor}", output.floor_source, width))


def _counted_outputs(outputs: tuple[OutputRecord, ...], width: int) -> Iterator[str]:
    """What a v1 record knows of its outputs: how many, and the recipe's floor."""
    count = len(outputs)
    yield _field("outputs", str(count))
    floor = outputs[0]
    if floor.floor:
        yield _field("", _pair(f"floor {floor.floor}", floor.floor_source, width))


def _field(label: str, value: str) -> str:
    return f"  {label.ljust(_LABEL)}{value}".rstrip()


def _pair(left: str, right: str, width: int) -> str:
    """Two columns inside one field, as the example sets `3.9` beside its source."""
    if not right:
        return left
    column = min(32, max(0, width - _LABEL - len(right) - 4))
    return f"{left.ljust(column)}  {right}"


def _plan(section: SectionRecord) -> Iterator[str]:
    # `path` is the fallback: a run written before sections carried their words
    # has only the key.
    yield f"PLAN  {_where(section)}"
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
        # is said out loud.
        source = f"{source} (inexact)"
    rendered = (
        f"  {action:>5}  {line.text.ljust(texts)}  "
        f"{line.origin.ljust(origins)}  {source}"
    )
    return rendered.rstrip()


def _stopped(record: Record, width: int) -> Iterator[str]:
    yield "STOPPED"
    for line in record.stopped.splitlines() or [""]:
        yield from textwrap.wrap(
            line, width - 2, initial_indent="  ", subsequent_indent="    "
        ) or ["  "]
    yield ""


def _findings(record: Record, width: int) -> Iterator[str]:
    """Every finding, under the check that made it, both halves (DESIGN.md
    §11.3).

    The check is named by its failure sentence, never its identifier. The
    remedy follows the finding it belongs to, or once after the list where it
    is about the set. A v1 record has the two already joined.
    """
    yield "FINDINGS"
    for kind, found in _grouped(record.findings).items():
        yield f"  {found[0].title or kind}"
        shared = kind in _SHARED_REMEDY
        for finding in found:
            said = finding.said
            if finding.remedy and not shared:
                said = f"{said} -- {finding.remedy}"
            yield from _wrapped(said, width, "        ")
        if shared and found[0].remedy:
            yield from _wrapped(found[0].remedy, width, "        ")
    yield ""


#: The checks whose remedy is one sentence about the whole set of findings.
_SHARED_REMEDY = frozenset(row.kind for row in CHECKS if row.shared_remedy)


def _grouped(findings: tuple[FindingRecord, ...]) -> dict[str, list[FindingRecord]]:
    """Findings by check, in the record's order, which is the table's."""
    grouped: dict[str, list[FindingRecord]] = {}
    for finding in findings:
        grouped.setdefault(finding.kind, []).append(finding)
    return grouped


#: How a check's state prints: four characters, so the CI block reads down as
#: one list.
_STATES = {"passed": "pass", "failed": "FAIL", "pending": "wait"}


def _ci(record: Record, width: int) -> Iterator[str]:
    """The checks swage waited on, and what they said (v1 §5.2)."""
    check = record.merge_check
    if check is None:
        return
    yield "CI"
    for state in check.checks:
        yield f"  {_STATES.get(state.state, state.state)}  {state.name}"
    if check.reason:
        yield from _wrapped(check.reason, width, "        ")
    yield ""


def _wrapped(text: str, width: int, indent: str) -> Iterator[str]:
    yield from textwrap.wrap(
        text,
        max(40, width),
        initial_indent=indent,
        subsequent_indent=indent,
        break_long_words=False,
        break_on_hyphens=False,
    )


#: How a decision reads to somebody who has not read the design (DESIGN.md
#: §9.8). `automerge` is the label's name. The last two are v1's.
_DECISIONS = {
    "push-label": "push, then label `automerge`",
    "label": "label `automerge`, and push nothing",
    "push": "push, and leave the label to a person",
    "nothing": "push nothing",
    "automerge": "push, then label `automerge`",
    "needs-review": "leave the label to a person",
}


def _verdict(record: Record, width: int) -> Iterator[str]:
    """What became of this feedstock: the bucket, the reason, the decision.
    A record that never reached a plan has the first two and not the third.
    """
    found = len(record.findings)
    count = f"   ({found} finding{'' if found == 1 else 's'})" if found else ""
    yield f"VERDICT  {record.outcome}{count}"
    if record.reason and not found and record.reason not in record.stopped:
        # Where there are findings or a stop the reason is already printed
        # above; elsewhere it is the only sentence there is.
        yield from _wrapped(record.reason, width, "  ")
    if record.decision:
        yield f"  decision  {_DECISIONS.get(record.decision, record.decision)}"


#: A stored section key, which is the only thing a run written before `where`
#: existed has to say which section its plan was of.
_KEY = re.compile(r"^(?:/outputs/(\d+))?/requirements/(\w+)$")


def _where(section: SectionRecord) -> str:
    """Which section this plan is of, as words rather than as a key. A run
    artifact may predate the `where` field, and then the key is read, never
    printed (`plan.prose`).
    """
    if section.where:
        return section.where
    match = _KEY.match(section.path)
    if match is None:
        return section_phrase(section.section)
    index, name = match.groups()
    owner = output_phrase(index=int(index)) if index is not None else output_phrase()
    return f"{section_phrase(name)} of {owner}"
