"""What `swage migrate` prints (DESIGN.md 7, 8).

A conversion rewrites the whole recipe, so a diff is not the report -- every
line changed and the interesting part is a handful of them. What a maintainer
needs before pressing anything is: did it convert, what did the converter warn
about that matters, what is about to happen to `conda-forge.yml`, and -- on a
compiled recipe, where the conditions are the substance -- what became of each
condition the old recipe stated.

**Three headings, because they are three different instructions.** What swage
found wrong with the conversion means the recipe is not what the old one said
and has to be fixed; what the converter reported means somebody should look;
the ledger means nothing on its own and is there to be read down. Merging them
would put "this build command is truncated" in the same list as "this field no
longer exists".

Design shorthand stays out of this. Anyone reading a terminal is reading it
without the design open, so no gate name and no section number appears here.
"""

from __future__ import annotations

import textwrap
from collections import Counter
from collections.abc import Callable

from swage.migrate import Condition, Migration

__all__ = ["condition_rows", "render_migration", "render_refusal"]

#: Where a wrapped line stops. Narrower than a terminal on purpose: this report
#: is quoted into commit messages and pull request threads as often as it is
#: read in a shell.
_WIDTH = 76


def render_migration(migration: Migration, wrote: bool = False) -> str:
    """One converted feedstock, as a person reads it."""
    verb = "converted" if wrote else "would convert"
    lines = [
        f"{migration.feedstock}  {verb} to a v1 recipe at {migration.ref}",
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

    lines.extend(_ledger(migration.review.conditions))

    if migration.notes:
        count = len(migration.notes)
        lines.append("")
        lines.extend(
            textwrap.wrap(
                f"{count} other message{'' if count == 1 else 's'} from the "
                "converter, none of which change what the recipe means",
                _WIDTH,
                initial_indent="  ",
                subsequent_indent="  ",
            )
        )

    lines.append("")
    lines.append("  a converted recipe is always reviewed by hand, never merged")
    lines.append("  automatically -- conversion is imperfect and this one is no")
    lines.append("  exception until somebody has read it")
    return "\n".join(lines) + "\n"


def render_refusal(feedstock: str, reason: str) -> str:
    """A feedstock swage will not convert, with the reason it gave.

    The message's own lines are printed as they were written, minus its first,
    which names the feedstock the heading has just named. They already carry
    the indentation this report uses -- four spaces for a detail, two for the
    sentence saying what to do -- and re-indenting them on the way through
    shifted the two apart and made the result look ragged.
    """
    body = "\n".join(reason.splitlines()[1:]).rstrip()
    return f"{feedstock}  not converted\n{body}\n"


def condition_rows(conditions: tuple[Condition, ...]) -> tuple[str, ...]:
    """Every condition the old recipe stated, and where the new one puts it.

    **Empty for a recipe that states none**, which is what makes this free on
    the noarch half of the fleet: 104 of the 105 noarch v0 feedstocks have
    nothing conditional in them at all, so no caller has a section to print.

    One row per condition rather than per line, because `# [win]` nine times
    over is one thing to check -- `tiledb` writes twenty selectors and has
    three conditions. The line count is still shown, since a condition that
    guarded six lines and landed on two is worth noticing even when the review
    found nothing provably wrong.

    Shared with the conversion commit's message rather than rendered twice,
    because the maintainer reading the ledger is as likely to be reading it on
    GitHub as in a shell, and two spellings of the same table would drift.
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
#: A reviewer given "an if:/then: entry" can go and find one; a reviewer given
#: a category name has to be told what it means first.
_PLACES: dict[str, Callable[[int], str]] = {
    "if": lambda count: f"{count} if:/then: entr{'y' if count == 1 else 'ies'}",
    "inline": lambda count: f"folded into {count} value{'' if count == 1 else 's'}",
    "skip": lambda _: "the build skip",
}


def _bullets(items: tuple[str, ...]) -> list[str]:
    """Sentences as a bulleted list, wrapped and hanging-indented.

    **An item's own line breaks survive.** A damage entry is a sentence
    followed by the recipe lines it is about, and those are quotations: a build
    command reflowed across three lines of prose is one nobody can compare
    against anything, and the difference this report exists to show is two
    characters in the middle of such a command. So the sentence is wrapped and
    everything under it is passed through, overflowing the column the way swage
    already lets a URL overflow it.
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
