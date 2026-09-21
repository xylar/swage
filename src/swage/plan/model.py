"""What a plan is made of.

One requirement swage intends to write, with its provenance. An entry is not
always one line: a per-python output states a python-gated dependency as an
`if:`/`then:` entry, which `PlannedConditional` is (DESIGN.md §9.3).
"""

from __future__ import annotations

from dataclasses import dataclass

from swage.recipe import Conditional, Entry, Requirement

from .attribute import Provenance
from .lines import parse_line

__all__ = [
    "PlannedConditional",
    "PlannedEntry",
    "PlannedRequirement",
    "first_name",
]


@dataclass(frozen=True)
class PlannedRequirement:
    """One line of a planned requirements section."""

    #: The line as swage will write it -- already normalized, so `pyyaml>=6.0.3`
    #: has become `pyyaml >=6.0.3` before it gets here.
    text: str
    #: Why this line is in the plan (design-v1.md 3.3).
    provenance: Provenance
    #: Whole-line comments to render above it, generated from the plan (v1 §6).
    comments: tuple[str, ...] = ()

    @property
    def name(self) -> str:
        """The name position, which is what ordering and gates key on."""
        return parse_line(self.text).name


@dataclass(frozen=True)
class PlannedConditional:
    """One dependency the recipe states as conditions on what is being built.

    ``conditionals`` is what the section holds, in order: one entry with an
    `else:`, or several `if:` entries, for one dependency.
    """

    conditionals: tuple[Conditional, ...]
    #: Why this dependency is in the plan, exactly as for a plain line.
    provenance: Provenance
    #: Rendered above the first of the entries.
    comments: tuple[str, ...] = ()
    #: True where swage found this entry rather than deriving it: structure,
    #: which ordering leaves where it was.
    preserved: bool = False

    @property
    def name(self) -> str:
        """The name position of the requirement the branches are about, from the
        first branch found.
        """
        for conditional in self.conditionals:
            found = first_name(conditional)
            if found is not None:
                return found
        return ""


def first_name(conditional: Conditional) -> str | None:
    """The first package named inside a conditional entry's branches, which is
    what the entry is filed under.
    """
    branches: tuple[Entry, ...] = (*conditional.then, *(conditional.otherwise or ()))
    for entry in branches:
        if isinstance(entry, Requirement):
            return parse_line(entry.text).name
        nested = first_name(entry)
        if nested is not None:
            return nested
    return None


#: What a planned section is a list of.
PlannedEntry = PlannedRequirement | PlannedConditional
