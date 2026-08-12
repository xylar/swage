"""What a plan is made of.

One requirement line swage intends to write, with the evidence for it. The
provenance travels with the line rather than being looked up again later,
because the trust gates check the plan and a line whose justification has to be
re-derived is a line that can be justified two different ways.
"""

from __future__ import annotations

from dataclasses import dataclass

from .attribute import Provenance
from .lines import parse_line

__all__ = ["PlannedRequirement"]


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
