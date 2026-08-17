"""What `swage migrate` prints (DESIGN.md 7, 8).

A conversion rewrites the whole recipe, so a diff is not the report -- every
line changed and the interesting part is a handful of them. What a maintainer
needs before pressing anything is: did it convert, what did the converter warn
about that matters, and what is about to happen to `conda-forge.yml`.

Design shorthand stays out of this. Anyone reading a terminal is reading it
without the design open, so no gate name and no section number appears here.
"""

from __future__ import annotations

from swage.migrate import Migration

__all__ = ["render_migration", "render_refusal"]


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

    if migration.concerns:
        lines.append("")
        lines.append("  read these before merging:")
        lines.extend(f"    - {concern}" for concern in migration.concerns)

    if migration.notes:
        count = len(migration.notes)
        lines.append("")
        lines.append(
            f"  {count} other message{'' if count == 1 else 's'} from the "
            "converter, none of which change what the recipe means"
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


def _size(text: str) -> str:
    count = len(text.splitlines())
    return f"{count} line{'' if count == 1 else 's'}"
