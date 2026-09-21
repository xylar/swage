"""``run_constraints`` is read, never authored (v1 §3.3.9).

swage never adds an entry, never removes one, and may update one once config
says which upstream extra it tracks. This module checks that every entry is
explained, which is G9 (DESIGN.md §9.7).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from swage.config import RunConstraint
from swage.mapping import normalize_name

from .lines import parse_line
from .prose import fenced

__all__ = ["UnassociatedConstraint", "check_run_constraints"]


@dataclass(frozen=True)
class UnassociatedConstraint:
    """A `run_constraints` entry no config association explains."""

    #: The entry as the recipe has it, quoted back in the report.
    text: str
    #: The package it constrains.
    name: str

    @property
    def reason(self) -> str:
        return (
            f"run_constraints {fenced(self.name)} is associated with no upstream "
            "extra; add it to run_constraints in config -- `extra: <name>` if "
            "it tracks one, `extra: null` if the bound is deliberate and "
            "tracks nothing"
        )


def check_run_constraints(
    entries: Sequence[str], associations: Mapping[str, RunConstraint]
) -> tuple[UnassociatedConstraint, ...]:
    """Return the entries no association explains, in the order they appear.

    Empty means G9 passes. `extra: null` is an association: the bound is
    deliberate and tracks nothing upstream.
    """
    unassociated: list[UnassociatedConstraint] = []
    for entry in entries:
        name = parse_line(entry).name
        if _associated(name, associations):
            continue
        unassociated.append(UnassociatedConstraint(text=entry, name=name))
    return tuple(unassociated)


def _associated(name: str, associations: Mapping[str, RunConstraint]) -> bool:
    if name in associations:
        return True
    # conda names are not PEP 503-normalized, so try both spellings, as
    # attribution does.
    normalized = normalize_name(name)
    return any(normalize_name(key) == normalized for key in associations)
