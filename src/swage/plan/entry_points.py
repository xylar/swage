"""Which entry-point lists swage would rewrite, and what it says (DESIGN.md
§9.6).

Only a list already there is reconciled; inserting a key is a different
operation, as it is for `python_version`. Nothing here writes: G15 decides
whether a person sees a drop first.
"""

from __future__ import annotations

from dataclasses import dataclass

from swage.recipe import Recipe
from swage.recipe.model import RecipeOutput
from swage.upstream import EntryPoint, RecipeUpstream, UpstreamMetadata

from .prose import fenced

__all__ = ["EntryPointChange", "plan_entry_points"]


@dataclass(frozen=True)
class EntryPointChange:
    """One output's entry-point list as swage would write it, and why."""

    path: str
    #: What the list will say, in upstream's order (design-v1.md 6).
    items: tuple[str, ...]
    #: `(name, was, now)` for each script whose target moved.
    retargeted: tuple[tuple[str, str, str], ...] = ()
    #: Scripts upstream declares that the list did not have.
    added: tuple[str, ...] = ()
    #: Lines the list had that upstream no longer declares under that name; a
    #: rename lands here and in `added` (G15).
    dropped: tuple[str, ...] = ()
    #: The output whose list this is, where the recipe names one.
    output: str | None = None

    @property
    def said(self) -> tuple[str, ...]:
        """One sentence per change swage makes on its own, for the report.

        A retarget or an addition is a note beside the verdict, not a finding
        (DESIGN.md §9.6). Every recipe token is fenced (`prose.fenced`).
        """
        where = _for(self.output)
        said = [
            f"entry point {fenced(name)}{where} now runs {fenced(now)}, which is "
            f"what upstream declares; it ran {fenced(was)}"
            for name, was, now in self.retargeted
        ]
        said.extend(
            f"entry point {fenced(item)}{where} is added, because upstream "
            "declares it and the recipe did not list it"
            for item in self.added
        )
        return tuple(said)

    @property
    def held(self) -> tuple[str, ...]:
        """One sentence per line going away, which is what G15 reports."""
        where = _for(self.output)
        return tuple(
            f"entry point {fenced(item)}{where} is dropped: upstream no longer "
            "declares a script by that name, and a command somebody may be "
            "using is going away"
            for item in self.dropped
        )


def plan_entry_points(
    recipe: Recipe, upstream: RecipeUpstream
) -> tuple[tuple[EntryPointChange, ...], tuple[str, ...]]:
    """Every entry-point list swage would rewrite, and the notes beside them.

    The notes are the lists swage looked at and left alone: one holding an
    `if:` entry (DESIGN.md §9.6).
    """
    changes: list[EntryPointChange] = []
    notes: list[str] = []
    for output in recipe.outputs:
        release = upstream.for_output(output.name or "")
        if release.entry_points is None:
            # Silence, not emptiness: nothing to reconcile against.
            continue
        where = output.name if output.index is not None else None
        change, note = _for_output(output, release, where)
        if change is not None:
            changes.append(change)
        if note is not None:
            notes.append(note)
    return tuple(changes), tuple(notes)


def _for_output(
    output: RecipeOutput, release: UpstreamMetadata, where: str | None
) -> tuple[EntryPointChange | None, str | None]:
    declared = release.entry_points or ()
    listed = output.entry_points
    if listed is None:
        return None, None
    if listed.conditional:
        return None, (
            f"the entry_points list{_for(where)} holds an if: entry, which "
            "swage reads past and does not rewrite; upstream "
            f"{release.version or ''} declares "
            f"{', '.join(fenced(point.text) for point in declared) or 'no script'}"
        )
    return _reconcile(listed.path, listed.items, declared, where), None


def _reconcile(
    path: str,
    items: tuple[str, ...],
    declared: tuple[EntryPoint, ...],
    where: str | None,
) -> EntryPointChange | None:
    """The list upstream would have written, against the one the recipe has.

    Keyed by the script's name. Where something changes the result is in
    upstream's order; where nothing does, the recipe's order and spacing stand
    (DESIGN.md §9.6).
    """
    wanted = tuple(point.text for point in declared)
    if sorted(_split(item) for item in items) == sorted(
        (point.name, point.target) for point in declared
    ):
        return None
    had = {name: target for name, target in (_split(item) for item in items)}
    now = {point.name: point.target for point in declared}
    retargeted = tuple(
        (name, had[name], now[name])
        for name in now
        if name in had and had[name] != now[name]
    )
    added = tuple(point.text for point in declared if point.name not in had)
    dropped = tuple(item for item in items if _split(item)[0] not in now)
    return EntryPointChange(
        path=path,
        items=wanted,
        retargeted=retargeted,
        added=added,
        dropped=dropped,
        output=where,
    )


def _split(item: str) -> tuple[str, str]:
    """`name = module:attr` as the recipe writes it, in its two halves."""
    name, _, target = item.partition("=")
    return name.strip(), target.strip()


def _for(where: str | None) -> str:
    return f" for {fenced(where)}" if where else ""
