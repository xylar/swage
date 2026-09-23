"""``run_constraints`` is read, never authored (v1 §3.3.9).

swage never adds an entry and never removes one. What it does is notice the
entry that transcribes an upstream extra and say so, because the two mean
different things: an extra is opted into, a run constraint binds every
environment holding the package (DESIGN.md §9.7).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from swage.config import RunConstraint
from swage.mapping import NameResolver, normalize_name
from swage.upstream import RecipeUpstream

from .lines import parse_line

__all__ = ["ExtraConstraint", "transcribed_extras"]


@dataclass(frozen=True)
class ExtraConstraint:
    """A `run_constraints` entry that transcribes an upstream extra."""

    #: The entry as the recipe has it, quoted back in the report.
    text: str
    #: The package it constrains, as the recipe names it.
    name: str
    #: The extra upstream declares the package under.
    extra: str


def transcribed_extras(
    entries: Sequence[str],
    associations: Mapping[str, RunConstraint],
    upstream: RecipeUpstream,
    resolver: NameResolver,
) -> tuple[ExtraConstraint, ...]:
    """Every entry upstream declares only under an extra, in recipe order.

    Config decides two things and not a third. ``extra: null`` says the bound
    is deliberate and tracks nothing upstream, so there is nothing to report;
    ``keep`` says the entry transcribes an extra and is staying anyway, which
    is a decision rather than an excuse and is the only way to quiet one.
    ``extra: <name>`` alone says which extra and is reported, because naming
    the thing is not the same as deciding about it.

    Config's extra wins over the reading: a name swage cannot map resolves to
    nothing and would otherwise go unnoticed, which is the safe direction for
    detection and the wrong one for an entry somebody has already identified.
    """
    by_conda_name = _extras_by_conda_name(upstream, resolver)
    found: list[ExtraConstraint] = []
    for entry in entries:
        name = parse_line(entry).name
        association = _association(name, associations)
        if association is not None and (association.keep or association.extra is None):
            continue
        extra = (association.extra if association is not None else None) or (
            by_conda_name.get(normalize_name(name))
        )
        if extra is not None:
            found.append(ExtraConstraint(text=entry, name=name, extra=extra))
    return tuple(found)


def _extras_by_conda_name(
    upstream: RecipeUpstream, resolver: NameResolver
) -> dict[str, str]:
    """Normalized conda name -> the first extra declaring it.

    The first, because an entry naming a package two extras both declare is
    about the practice rather than about which extra, and the sentence reads
    better with one.
    """
    found: dict[str, str] = {}
    for release in upstream.releases:
        for extra, requirements in release.optional_dependencies.items():
            for requirement in requirements:
                resolution = resolver.resolve(requirement.name)
                conda = resolution.conda_name if resolution else requirement.name
                found.setdefault(normalize_name(conda), extra)
    return found


def _association(
    name: str, associations: Mapping[str, RunConstraint]
) -> RunConstraint | None:
    if name in associations:
        return associations[name]
    # conda names are not PEP 503-normalized, so try both spellings, as
    # attribution does.
    normalized = normalize_name(name)
    for key, association in associations.items():
        if normalize_name(key) == normalized:
            return association
    return None
