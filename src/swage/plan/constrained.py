"""``run_constraints`` is read, never authored (DESIGN.md 3.3.9).

Many conda-forge recipes use `run_constraints` to express an upstream extra:
*if you install pandas alongside this package, it must be at least this
version.* It is a natural-looking translation and a mistaken one. An extra is a
set of dependencies a user opts into; a `run_constraints` entry is a
compatibility bound imposed on everyone who happens to have that package in the
same environment. The two coincide sometimes and diverge quietly the rest of
the time.

swage takes three positions, in decreasing order of firmness.

**swage never adds an entry.** Not by default, not behind a config flag.
Putting an upstream extra into a recipe at all is a packaging decision with
real cost: on PyPI an extra is free, on conda-forge it is a package -- a build,
CI time, and a name someone maintains forever. Whether an extra earns that
turns on whether some downstream conda-forge package would benefit, and no
metadata anywhere contains that. This is G4's principle applied to the other
mechanism for the same thing.

**swage never removes one either**, for the reason in DESIGN.md 3.3.7: an entry
it cannot attribute may encode a decision nobody wrote down.

**swage may update one, once it is told what the entry means.** It cannot
otherwise, because nothing in a recipe records which upstream extra an entry
came from, and inferring it would be exactly the translation the first rule
rejects.

So this module does not plan the section. It checks that every entry is
*explained*, which is G9 -- and the recipe is still updated either way, because
`host` and `run` are reconciled as usual and withholding a correct update over
an unrelated uncertainty helps nobody.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from swage.config import RunConstraint
from swage.mapping import normalize_name

from .lines import parse_line

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
            f"run_constraints {self.name!r} is associated with no upstream "
            "extra; add it to run_constraints in config -- `extra: <name>` if "
            "it tracks one, `extra: null` if the bound is deliberate and "
            "tracks nothing"
        )


def check_run_constraints(
    entries: Sequence[str], associations: Mapping[str, RunConstraint]
) -> tuple[UnassociatedConstraint, ...]:
    """Return the entries no association explains, in the order they appear.

    Empty means G9 passes. Note that `extra: null` **is** an association -- it
    records that the bound is deliberate and tracks nothing upstream, which is
    a different statement from the entry never having been considered.

    No feedstock has associations yet, so today every recipe with a
    `run_constraints` section lands in needs-review. That is the intended
    starting state rather than a transitional annoyance: swage has just
    rewritten a `run` section whose constraints may have been derived from the
    very same extras, and it has no way to check whether the two still agree.
    The gate makes that uncertainty visible instead of silent, and retires
    itself one feedstock at a time as the associations get written down.
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
    # conda names are not PEP 503-normalized, so try both spellings the way
    # attribution does -- a config entry written `msal-extensions` should still
    # explain a recipe line saying `msal_extensions`.
    normalized = normalize_name(name)
    return any(normalize_name(key) == normalized for key in associations)
