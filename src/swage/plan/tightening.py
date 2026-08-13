"""A bound the recipe has and upstream does not (DESIGN.md 3.3.14).

Every other rule in the planner is about which dependencies a section holds.
This one is about a dependency that is staying: its *constraint*. A recipe
routinely carries a bound nobody upstream asked for --

    # temporarily constrain to earlier airflow and task-sdk to prevent
    # solver troubles
    - apache-airflow >=2.11.0,<3.1.3

-- where upstream declares `apache-airflow>=2.11.0` and nothing more. swage
renders what upstream declares, so the `<3.1.3` disappears, and every gate is
satisfied while it goes: G1 attributes the *line* and never looks at the bound,
G2 resolves a name that has not changed.

> **A bound swage cannot attribute is not a bound swage may drop.** It is the
> same rule as DESIGN.md 3.3.7's for a whole line, one level down, and it
> gets the same two answers: stop and say so, or let config record the
> decision and render it back.

And it inherits 3.3.7's *third* answer with them. A bound applied to work
around a solver problem elsewhere must not go into `constraints:` -- that says
the bound holds for good, and it would then outlive the problem it exists for.
Leaving it unrecorded is what makes this gate ask again at the next version
bump. `apache-airflow-providers-google` carries both halves under one comment:
`apache-airflow >=2.11.0,<3.1.3` reaches this gate and
`apache-airflow-task-sdk <1.1.3` reaches G1, and neither is for blessing.

Told apart by *witnessing* rather than by comparing clause sets. A recipe whose
floor is below upstream's is stale in the harmless direction and swage tightens
it as a matter of course; only a bound that excludes a version the plan would
allow costs anything. So the question is asked that way round: is there a
version this plan admits that the recipe's own constraint refuses?

**What this cannot yet tell apart** is a bound somebody applied by hand from
one upstream has since lowered, and the evidence is the same second fetch
DESIGN.md 3.3.7 pays for a whole line: the previous version's metadata. Until
the write path has it in hand, an unclassified tightening is reported rather
than dropped -- the safe direction, and the one that rule already takes.
"""

from __future__ import annotations

from dataclasses import dataclass

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

__all__ = ["Tightened", "tightening"]


@dataclass(frozen=True)
class Tightened:
    """A constraint the recipe states more tightly than upstream does."""

    #: The conda name, as the line and any `constraints:` entry spell it.
    name: str
    #: The constraint the recipe carries, e.g. ``">=2.11.0,<3.1.3"``.
    recipe: str
    #: The constraint swage would render.
    planned: str

    @property
    def reason(self) -> str:
        return (
            f"the recipe constrains {self.name!r} more tightly than upstream: "
            f"{self.name} {self.recipe} against upstream's "
            f"{self.planned or 'no constraint'} -- swage would drop the "
            f"difference. Record it in `constraints:` if the bound is meant to "
            f"hold for good, or remove it from the recipe. A temporary "
            f"constraint is neither -- leave it, and swage asks again at "
            "the next version bump, which is when it should be re-checked"
        )


def tightening(name: str, recipe: str, planned: str) -> Tightened | None:
    """Whether ``recipe`` refuses a version ``planned`` allows.

    Both are constraint text as a recipe line carries it -- ``">=2.11.0"``, or
    empty for an unconstrained dependency. A constraint swage cannot parse is
    no answer rather than a wrong one: `pandas >=${{ pandas_min }}` is a real
    shape in the fleet, and templated bounds are not comparable.

    A `constraints:` entry needs no special case here. It is already folded
    into what swage renders, so a recipe stating exactly what config says
    admits the same versions and nothing is reported -- while a recipe going
    *further* than config still is, which is the answer that should not have
    needed arranging.
    """
    try:
        wanted = SpecifierSet(recipe)
        upstream = SpecifierSet(planned)
    except InvalidSpecifier:
        return None

    for candidate in _witnesses(wanted, upstream):
        allowed = upstream.contains(candidate, prereleases=True)
        if allowed and not wanted.contains(candidate, prereleases=True):
            return Tightened(name=name, recipe=recipe, planned=planned)
    return None


def _witnesses(*specifiers: SpecifierSet) -> list[Version]:
    """Versions worth testing: every bound named, and one just above each.

    Exact for the comparisons that occur, which is the same bargain
    `markers.reachable_in_range` makes for sampling Python releases. A bound is
    only ever excluded *at* a version somebody wrote down or immediately after
    it, so those are the only places the two constraints can first disagree.
    """
    found: dict[str, Version] = {}
    for specifier in specifiers:
        for clause in specifier:
            try:
                version = Version(clause.version.rstrip(".*"))
            except InvalidVersion:
                continue
            for witness in (version, _just_above(version)):
                if witness is not None:
                    found.setdefault(str(witness), witness)
    return list(found.values())


def _just_above(version: Version) -> Version | None:
    """The next version by release segment, for witnessing a strict bound.

    Bumping the release rather than suffixing, for the reason `reconcile` gives
    at length: `0.20b0.1` is not a version, and `.post1` is excluded by `>V`
    while `.dev0` is excluded by `<V`, so neither spelling witnesses anything.
    """
    release = ".".join(str(part) for part in version.release)
    epoch = f"{version.epoch}!" if version.epoch else ""
    try:
        candidate = Version(f"{epoch}{release}.1")
    except InvalidVersion:  # pragma: no cover -- release parts are always ints
        return None
    return candidate if candidate > version else None
