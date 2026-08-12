"""Split a recipe requirement line, and decide whether swage owns it.

Not every line in a requirements section came from upstream, and treating them
alike breaks immediately (DESIGN.md 3.3.6). A `${{ pin_subpackage(...) }}` sent
to the name resolver would fail to resolve and G2 would block **every
multi-output feedstock in the fleet** -- the rule is load-bearing, not a
refinement.

**The test is on the name position specifically.** ``pandas >=${{ x }}`` has
the name ``pandas`` and is an ordinary upstream dependency whose constraint
happens to be templated; only a line whose *name* is a template expression is
structural. That distinction is not academic: across the 110 recipes readable
in the maintainer's checkouts there are 612 lines with a plain name and a
templated constraint, against 198 whose name is a template call. Getting it
backwards would misclassify the larger group.

**Recognition is an allowlist, never a fallback.** A template swage does not
recognize is preserved unchanged -- swage never rewrites what it does not
understand -- but it gets no provenance, so G1 stops the feedstock with the
expression quoted. Were this a fallback, every never-upstream dependency would
quietly acquire provenance and the protection in DESIGN.md 3.3.7 would
evaporate. The two rules only hold each other up while this one stays an
allowlist.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from swage.config import RecipeOwned

__all__ = ["ParsedLine", "parse_line"]

#: The name is a call when the expression opens with ``f(``.
_CALL = re.compile(r"^\$\{\{\s*([A-Za-z_]\w*)\s*\(")

_OPEN = "${{"
_CLOSE = "}}"


@dataclass(frozen=True)
class ParsedLine:
    """A requirement line split at the boundary that decides its treatment."""

    #: The line exactly as the recipe has it, which is what gets preserved
    #: when swage declines to rewrite it.
    text: str
    #: The name position: a package name, or a whole template expression.
    name: str
    #: Everything after the name, e.g. ``">=2.3.3"`` or ``"${{ python_min }}.*"``.
    #: Empty where the line is a bare name.
    constraint: str
    #: The function called in the name position, e.g. ``"pin_subpackage"``, or
    #: None where the name is not a template call. A template that is *not* a
    #: call -- ``${{ name }}-with-kerberos`` -- is None too, and so cannot be
    #: blessed by `functions`.
    function: str | None

    @property
    def templated_name(self) -> bool:
        """Whether the name position contains a template at all."""
        return _OPEN in self.name

    def recipe_owned(self, owned: RecipeOwned) -> bool:
        """Whether this line is conda-forge structure swage preserves verbatim."""
        if self.function is not None:
            return self.function in owned.functions
        if self.templated_name:
            # An interpolated name that is not a call. `functions` cannot
            # describe it and `names` is for literals, so it stays unexplained
            # and G1 reports it rather than swage guessing.
            return False
        return self.name in owned.names


def parse_line(text: str) -> ParsedLine:
    """Split ``text`` into its name position and the rest.

    The name cannot be found by splitting on whitespace: template expressions
    contain spaces, so ``${{ pin_subpackage(name, exact=True) }}`` would come
    apart into ``${{`` and a constraint. Where the line opens a template, the
    name runs to the matching ``}}`` and through any suffix attached to it
    without a space -- which is what keeps ``${{ name }}-with-kerberos`` in one
    piece.
    """
    stripped = text.strip()
    if stripped.startswith(_OPEN):
        close = stripped.find(_CLOSE)
        if close != -1:
            end = close + len(_CLOSE)
            # A suffix glued straight onto the expression is part of the name.
            while end < len(stripped) and not stripped[end].isspace():
                end += 1
            name = stripped[:end]
            return ParsedLine(
                text=text,
                name=name,
                constraint=stripped[end:].strip(),
                function=_function(name),
            )

    name, _, constraint = stripped.partition(" ")
    return ParsedLine(
        text=text,
        name=name,
        constraint=constraint.strip(),
        function=_function(name),
    )


def _function(name: str) -> str | None:
    match = _CALL.match(name)
    return match.group(1) if match else None
