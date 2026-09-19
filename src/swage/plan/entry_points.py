"""Which entry-point lists swage would rewrite, and what it says (design-v1.md 3.3.15).

A recipe may list the scripts a package installs under
`build.python.entry_points`, and conda then writes them at install time.
Upstream declares the same scripts in its metadata, and the two drift the
way dependencies do: the first conversion swage pushed left m2r2 saying
`m2r2 = m2r2:main` a release after upstream had moved it to
`m2r2.cli.m2r2:main`, and the failing test was how anybody found out.

**Only a list that is already there is reconciled.** An output with no
`entry_points` key is left alone and not remarked on: `flask` lists none,
tests `flask --help`, and passes, because a package built with pip keeps
the scripts pip wrote -- so the key's absence is a recipe that chose the
other way, not a gap. Inserting a key would in any case be a different
operation from replacing one, as it is for `python_version` (design-v1.md 3.7).

Nothing here writes. It says what would change, and G15 decides whether a
person sees it first.
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
    #: Lines the list had that upstream no longer declares under that name.
    #: A rename lands here and in `added`, which is the point: the command a
    #: user has is going away, and that is what a person looks at (G15).
    dropped: tuple[str, ...] = ()
    #: The output whose list this is, where the recipe names one.
    output: str | None = None

    @property
    def said(self) -> tuple[str, ...]:
        """One sentence per change swage makes on its own, for the report.

        A retarget or an addition is upstream's own declaration and rides the
        trust ladder like a dependency change; these sentences are notes
        beside the verdict rather than findings against it. What is held is
        `held`, and G15 says it.

        Every token a reader would look for is fenced, because this is
        published as markdown and `module:attr` is exactly the kind of thing
        an underscore in the middle of turns into italics (`prose.fenced`).
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
            f"entry point {fenced(item)}{where} is dropped, because upstream no "
            "longer declares a script by that name -- a command somebody may be "
            "using is going away, so this is held for a person to confirm"
            for item in self.dropped
        )


def plan_entry_points(
    recipe: Recipe, upstream: RecipeUpstream
) -> tuple[tuple[EntryPointChange, ...], tuple[str, ...]]:
    """Every entry-point list swage would rewrite, and the notes beside them.

    The notes are the lists swage looked at and left alone: one holding an
    `if:` entry, which is a decision the recipe made per platform and which
    a flat list would unmake. Not gated, and said on every run, which is
    design-v1.md 4's bargain for an unaccounted extra applied to a script.
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

    Keyed by the script's name, which is what a user types: a moved target
    under the same name is a retarget, and a name that is gone is a drop
    whatever else appeared. Where something does change, the result is in
    upstream's order, the rule every other list swage writes follows
    (design-v1.md 6). Where nothing does, the recipe's order stands: the same
    two scripts the other way round, or spaced differently --
    `pyproj=pyproj.__main__:main` -- is not a change to make, and
    `wetterdienst` would otherwise have carried a two-line diff for nothing.
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
