"""`swage explain`, rendered from the record (DESIGN.md 9.2).

Nothing here recomputes anything, and that is the whole design rather than an
implementation convenience. The question `explain` answers is "why did it do
*that*, at 03:00, while I was asleep" -- by the time it is asked, upstream has
moved on and config may have changed, so recomputing would answer a different
question and a second computation path would be a second thing that can drift
from the planner. Rendering the stored record means `explain` cannot disagree
with what actually happened.

The sections are ordered the way the questions actually get asked, which puts
the checks and the verdict last: "why did this not merge" is what made someone
run the command. CI comes between them, because on a pull request swage would
merge itself it is the last thing standing between the gates and the verdict.
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
        lines.extend(_gates(record, width))
    if record.merge_check is not None:
        lines.extend(_ci(record, width))
    if record.gates:
        lines.extend(_verdict(record))
    elif not record.stopped:
        # Nothing above has said what became of this feedstock. A record with
        # no gates is one that never reached them -- `swage status` reporting
        # that a pull request merged, or a feedstock with no bot pull request
        # at all -- and `explain` printing its inputs and then stopping answers
        # a different question than the one that was asked.
        lines.extend(_outcome(record, width))
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
        # "newest of 4 open" rather than nothing: a feedstock at four is one
        # where conda-forge's bot has stopped filing new pull requests, so the
        # count answers a question the number alone cannot (DESIGN.md 3.4.1).
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
            # Under the release rather than beside it: "which release" and
            # "which file in it" are two steps of one lookup, and the URL has
            # already taken the width the second column had.
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
    # `path` is the fallback rather than the answer: this command renders a
    # run artifact, and one written before sections carried their words has
    # only the key. Printing nothing would lose which section the plan is of.
    yield f"PLAN  {section.where or section.path}"
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


def _gates(record: FeedstockRecord, width: int) -> Iterator[str]:
    """Every check, by what it asks rather than by its number.

    This block used to be a grid -- `G1 pass   G2 pass   G3 n/a` -- which is
    dense and unreadable at once: it fits eleven checks on one line and tells
    you nothing about any of them unless you have the design open beside it.
    One check per line costs ten lines in the command whose entire job is
    answering "why did swage decide that" (DESIGN.md 9.2), and answers it.

    Failures last, because they are what the reader came for and the eye
    finds the end of a list.
    """
    yield "CHECKS"
    for gate in record.gates:
        if gate.passed is False:
            continue
        verdict = "pass" if gate.passed else "n/a "
        yield f"  {verdict}  {gate.title or gate.name}"
        if not gate.passed and gate.detail:
            # Why it did not apply, which is the whole content of an `n/a`.
            yield from _wrapped(gate.detail, width, "        ")
    for gate in record.failures:
        yield f"  FAIL  {gate.title or gate.name}"
        # Wrapped rather than printed whole. A check that fails on many lines
        # at once reports them all, and it really does run long:
        # `apache-airflow-core-split` fails the first one with 2,800
        # characters of reasons, every one of which someone has to act on.
        if gate.detail:
            yield from _wrapped(gate.detail, width, "        ")
    yield ""


#: How a check's state prints. Four characters, in the column the gates
#: already use, so the two blocks read down as one list -- which is why the
#: record's `pending` shortens to `wait` here and stays `pending` there.
_STATES = {"passed": "pass", "failed": "FAIL", "pending": "wait"}


def _ci(record: FeedstockRecord, width: int) -> Iterator[str]:
    """The checks swage waited on before merging, and what they said.

    Printed for the pull requests swage would merge as well as the ones it
    would not, which is the point of recording it: the merge on this path is
    the one action nobody reviews, so the evidence for it belongs where
    somebody can read it afterwards (DESIGN.md 5.2).
    """
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


#: How the two decisions read to somebody who has not read the design.
#: `automerge` survives as itself because it is the name of a real label on a
#: real pull request rather than a word swage invented.
_VERDICTS = {
    "automerge": "may merge automatically",
    "needs-review": "needs review",
}


def _verdict(record: FeedstockRecord) -> Iterator[str]:
    decision = record.decision or record.outcome
    failed = len(record.failures)
    count = f"   ({failed} check{'' if failed == 1 else 's'} failed)" if failed else ""
    yield f"VERDICT  {_VERDICTS.get(decision, decision)}{count}"


def _outcome(record: FeedstockRecord, width: int) -> Iterator[str]:
    """What became of a feedstock that never reached the gates."""
    yield "OUTCOME"
    yield f"  {record.outcome}"
    if record.detail:
        yield from _wrapped(record.detail, width, "  ")
