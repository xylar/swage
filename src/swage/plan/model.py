"""What a plan is made of.

One requirement swage intends to write, with the evidence for it. The
provenance travels with the entry rather than being looked up again later,
because the trust gates check the plan and a line whose justification has to be
re-derived is a line that can be justified two different ways.

**An entry is not always one line.** An output built once per python carries a
dependency upstream gates on the python version as an `if:`/`then:` entry
(DESIGN.md 3.3.1.1): one dependency, one provenance and one position in the
section's order, but two or three lines of YAML. `PlannedConditional` is what
`PlannedRequirement` is, for a dependency stated conditionally.
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
    #: Why this line is in the plan (DESIGN.md 3.3).
    provenance: Provenance
    #: Whole-line comments to render above it: an extra's block header, or a
    #: `# tightest of upstream's floors (python >=3.14)` note. Generated from the plan
    #: rather than preserved from the recipe, since requirements sections are
    #: swage's to render (DESIGN.md 6).
    comments: tuple[str, ...] = ()

    @property
    def name(self) -> str:
        """The name position, which is what ordering and gates key on."""
        return parse_line(self.text).name


@dataclass(frozen=True)
class PlannedConditional:
    """One dependency the recipe states as conditions on what is being built.

    ``conditionals`` is what the section holds, in order. Two upstream variants
    that partition the python axis are one entry with an `else:` branch; three
    or more runs cannot be written that way and stay several `if:` entries.
    They are one `PlannedConditional` either way, because they are one
    dependency: ordering places them together and the gates ask about them
    once.
    """

    conditionals: tuple[Conditional, ...]
    #: Why this dependency is in the plan, exactly as for a plain line.
    provenance: Provenance
    #: Rendered above the first of the entries.
    comments: tuple[str, ...] = ()
    #: True where swage found this entry rather than deriving it from
    #: upstream's markers. It is then structure swage does not understand, and
    #: ordering leaves it where it was rather than placing it by name.
    preserved: bool = False

    @property
    def name(self) -> str:
        """The name position of the requirement the branches are about.

        The first one found. swage authors one entry per python range for a
        single dependency, so every branch names the same package; a
        conditional swage is only preserving may name several, which is why
        ordering treats a preserved entry as structure and leaves it where it
        was rather than placing it by this.
        """
        for conditional in self.conditionals:
            found = first_name(conditional)
            if found is not None:
                return found
        return ""


def first_name(conditional: Conditional) -> str | None:
    """The first package named inside a conditional entry's branches.

    What a conditional is *about*, as far as ordering, the report and the
    recipe's existing lines are concerned. An entry swage authored names one
    package in every branch; one it is only preserving may name several, and
    the first is the one the entry is filed under.
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
