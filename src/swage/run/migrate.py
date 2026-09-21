"""What `swage migrate` prints (v1 §7, §8).

Four headings, because they are four different instructions: what swage found
wrong with the conversion, what the converter reported, what swage corrected,
and the ledger of conditions. No design shorthand (DESIGN.md §3.1).
"""

from __future__ import annotations

import textwrap
from collections import Counter
from collections.abc import Callable, Sequence

from swage.forge import BotPullRequest
from swage.migrate import Condition, Migration

__all__ = ["condition_rows", "render_migration", "render_refusal"]

#: Where a wrapped line stops. Narrower than a terminal, because this report is
#: quoted into commit messages and pull request threads.
_WIDTH = 76


def render_migration(
    migration: Migration,
    pulls: Sequence[BotPullRequest] | None = None,
    converted: bool = False,
) -> str:
    """One converted feedstock, as a person reads it.

    ``pulls`` is the feedstock's open bot pull requests, newest last, and
    decides the closing line: which command pushes this conversion, or why
    none does yet; `None` means the caller did not look. ``converted`` says
    the newest already carries a v1 recipe. The report is a preview and
    says "would convert" all the way down (v1 §7.1).
    """
    lines = [
        f"{migration.feedstock}  would convert to a v1 recipe at {migration.ref}",
        f"    recipe.yaml       {_size(migration.recipe_text)}, "
        f"{len(migration.recipe.outputs)} output"
        f"{'' if len(migration.recipe.outputs) == 1 else 's'}",
    ]

    if migration.forge_config_added:
        settings = ", ".join(migration.forge_config_added)
        lines.append(f"    conda-forge.yml   would set {settings}")
    else:
        lines.append("    conda-forge.yml   already builds with rattler-build")

    if migration.review.damage:
        lines.append("")
        lines.append("  the conversion is wrong here, and has to be fixed by hand:")
        lines.extend(_bullets(migration.review.damage))

    if reported := migration.reported_concerns:
        lines.append("")
        lines.append("  read these before merging:")
        lines.extend(_bullets(reported))

    if migration.corrections:
        lines.append("")
        lines.append(
            "  swage wrote these into the conversion, and they need no decision:"
        )
        lines.extend(_bullets(migration.corrections))

    lines.extend(_ledger(migration.review.conditions))

    if migration.notes:
        lines.append("")
        lines.append("  also reported, and changing nothing in what the recipe means:")
        lines.extend(_bullets(migration.notes))

    lines.append("")
    lines.append("  a converted recipe is always reviewed by hand, never merged")
    lines.append("  automatically -- conversion is imperfect and this one is no")
    lines.append("  exception until somebody has read it")
    if pulls is not None:
        lines.append("")
        lines.extend(_next_step(migration.feedstock, pulls, converted))
    return "\n".join(lines) + "\n"


def _next_step(
    feedstock: str, pulls: Sequence[BotPullRequest], converted: bool
) -> list[str]:
    """What pushes this conversion, which is not this command. The pull request
    named is the newest (v1 §3.4.1); the command is never wrapped.
    """
    if not pulls:
        return textwrap.wrap(
            f"nothing pushes it yet: {feedstock} has no open version pull "
            "request, and a conversion rides along with one rather than "
            "becoming a pull request of its own",
            _WIDTH,
            initial_indent="  ",
            subsequent_indent="  ",
        )
    newest = pulls[-1]
    if converted:
        sentence = (
            f"pull request #{newest.number} already carries a conversion, so "
            "what is left is the dependency update:"
        )
        command = f"swage update --feedstock {feedstock}"
    else:
        sentence = (
            f"to push it onto pull request #{newest.number} ahead of the "
            "dependency update, run:"
        )
        command = f"swage update --migrate --feedstock {feedstock}"
    wrapped = textwrap.wrap(
        sentence, _WIDTH, initial_indent="  ", subsequent_indent="  "
    )
    return [*wrapped, f"    {command}"]


def render_refusal(feedstock: str, reason: str) -> str:
    """A feedstock swage will not convert, with the reason it gave, its lines
    printed as written minus the first, which names the feedstock.
    """
    body = "\n".join(reason.splitlines()[1:]).rstrip()
    return f"{feedstock}  not converted\n{body}\n"


def condition_rows(conditions: tuple[Condition, ...]) -> tuple[str, ...]:
    """Every condition the old recipe stated, and where the new one puts it.

    Empty for a recipe that states none. One row per condition rather than
    per line, with the line count. Shared with the conversion commit's
    message.
    """
    if not conditions:
        return ()
    width = min(max(len(condition.selector) for condition in conditions), 34)
    return tuple(
        f"{condition.selector.ljust(width)}  {_guarded(condition)}"
        f"  ->  {_became(condition)}"
        for condition in conditions
    )


def _ledger(conditions: tuple[Condition, ...]) -> list[str]:
    """`condition_rows` as a section of the terminal report."""
    rows = condition_rows(conditions)
    if not rows:
        return []
    return ["", "  what became of each condition the old recipe stated:"] + [
        f"    {row}" for row in rows
    ]


def _guarded(condition: Condition) -> str:
    count = len(condition.guarded)
    return f"{count} line{' ' if count == 1 else 's'}"


def _became(condition: Condition) -> str:
    """Where the converted recipe states this condition, in one phrase."""
    if condition.lost:
        # Never on its own: a lost condition is also in the damage above, with
        # the line it took with it. This row is what makes the ledger add up.
        return "nowhere -- see above"
    landed = Counter(condition.landed)
    return ", ".join(
        _PLACES[kind](count) for kind, count in sorted(landed.items()) if count
    )


#: How each landing reads, as a phrase naming something in the converted file.
_PLACES: dict[str, Callable[[int], str]] = {
    "if": lambda count: f"{count} if:/then: entr{'y' if count == 1 else 'ies'}",
    "inline": lambda count: f"folded into {count} value{'' if count == 1 else 's'}",
    "skip": lambda _: "the build skip",
}


def _bullets(items: tuple[str, ...]) -> list[str]:
    """Sentences as a bulleted list, wrapped and hanging-indented. An item's own
    line breaks survive: the lines under a sentence are quotations.
    """
    rendered = []
    for item in items:
        sentence, _, quoted = item.partition("\n")
        rendered.extend(
            textwrap.wrap(
                sentence,
                _WIDTH,
                initial_indent="    - ",
                subsequent_indent="      ",
                break_long_words=False,
                break_on_hyphens=False,
            )
        )
        rendered.extend(f"    {line}" for line in quoted.splitlines())
    return rendered


def _size(text: str) -> str:
    count = len(text.splitlines())
    return f"{count} line{'' if count == 1 else 's'}"
