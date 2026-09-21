"""Split a recipe requirement line, and decide whether swage owns it (DESIGN.md
§9.4).

The test is on the name position: a line whose name is a template expression is
structural, one whose constraint is templated is an ordinary dependency.
Recognition is an allowlist, never a fallback: a template swage does not
recognize is preserved unchanged and gets no provenance (v1 §3.3.6).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from swage.config import RecipeOwned

__all__ = ["ParsedLine", "parse_line", "spec_key"]

#: The name is a call when the expression opens with ``f(``.
_CALL = re.compile(r"^\$\{\{\s*([A-Za-z_]\w*)\s*\(")

#: The name is a bare variable when the whole name position is one interpolation
#: of one identifier: ``${{ mpi }}``, not ``${{ name }}-x``.
_VARIABLE = re.compile(r"^\$\{\{\s*([A-Za-z_]\w*)\s*\}\}$")

#: The name runs up to whitespace or the first constraint operator.
_NAME = re.compile(r"^[^\s<>=!~]+")

_OPEN = "${{"
_CLOSE = "}}"

#: A whole template expression, masked out before a line is split on
#: whitespace so that the spaces inside one do not read as field boundaries.
_TEMPLATE = re.compile(r"\$\{\{.*?\}\}")

#: A match spec's bracket section, which ends the line: `[build=nompi_*]`.
#: Matched on the masked text, so a `[` inside a template is not one of these.
_BRACKET = re.compile(r"\[[^]]*\]$")

#: The variant conda-forge feedstocks interpolate to name the platform an
#: artifact was built for, under `noarch_platforms`. Declared per feedstock in
#: its variants file, not a conda-smithy variable.
_NOARCH_PLATFORM = re.compile(r"\$\{\{\s*noarch_platform\s*\}\}")

#: Every value that variant is given. Expanded over all four rather than the
#: declared set, which only ever narrows the answer.
_PLATFORM_VALUES = ("linux", "osx", "win", "unix")

#: The other half of the idiom: a whole dependency chosen by the platform.
#: Matched as a whole line rather than parsed; anything else stays unexplained.
_PLATFORM_CHOICE = re.compile(
    r'^\$\{\{\s*"([^"]+)"\s+if\s+noarch_platform\s*==\s*"(\w+)"'
    r'(?:\s+else\s+"([^"]+)")?\s*\}\}$'
)


@dataclass(frozen=True)
class ParsedLine:
    """A requirement line split at the boundary that decides its treatment."""

    #: The line exactly as the recipe has it, which is what gets preserved
    #: when swage declines to rewrite it.
    text: str
    #: The name position: a package name, or a whole template expression.
    name: str
    #: The version part of what follows the name, e.g. ``">=2.3.3"`` or
    #: ``"${{ python_min }}.*"``. Empty where the line is a bare name.
    constraint: str
    #: The function called in the name position, e.g. ``"pin_subpackage"``, or
    #: None where the name is not a template call.
    function: str | None
    #: What pins the requirement past its version, in the recipe's own spelling:
    #: the match spec's third field, or the bracket saying the same thing. Held
    #: apart rather than normalized (DESIGN.md §9.4). Never anything upstream
    #: declared.
    build_string: str = ""

    @property
    def templated_name(self) -> bool:
        """Whether the name position contains a template at all."""
        return _OPEN in self.name

    @property
    def interpolated_variable(self) -> str | None:
        """The context variable this line's whole name is, or None.

        The whole name position, deliberately: `variables` blesses a build
        variant's key, not every name built on one.
        """
        found = _VARIABLE.match(self.name)
        return found.group(1) if found is not None else None

    @property
    def rendered(self) -> str:
        """The line as swage writes it: name, one space, constraint.

        Safe for every line: a recipe-owned template has an empty constraint
        and renders back byte-identical.
        """
        parts = (self.name, self.constraint, self.build_string)
        return " ".join(part for part in parts if part)

    @property
    def platform_expansions(self) -> tuple[str, ...]:
        """Every package name this line can name, across the platforms.

        The two shapes of the `noarch_platform` idiom, and empty otherwise.
        Expansion rather than evaluation; order is reading order, duplicates
        dropped.
        """
        choice = _PLATFORM_CHOICE.match(self.name)
        if choice is not None:
            chosen, _, otherwise = choice.groups()
            names = [chosen] + ([otherwise] if otherwise else [])
            return tuple(dict.fromkeys(names))
        if not _NOARCH_PLATFORM.search(self.name):
            return ()
        return tuple(
            _NOARCH_PLATFORM.sub(value, self.name) for value in _PLATFORM_VALUES
        )

    def recipe_owned(self, owned: RecipeOwned) -> bool:
        """Whether this line is conda-forge structure swage preserves verbatim."""
        if self.function is not None:
            return self.function in owned.functions
        expansions = self.platform_expansions
        if expansions:
            # Structure on every platform or on none: requiring all of them
            # keeps this an allowlist.
            return all(name in owned.names for name in expansions)
        if variable := self.interpolated_variable:
            # A build variant's own key, which config blesses by name the way
            # it blesses a function.
            return variable in owned.variables
        if self.templated_name:
            # An interpolated name that is not a call: neither `functions` nor
            # `names` describes it, so it stays unexplained.
            return False
        return self.name in owned.names


def spec_key(name: str, build_string: str) -> str:
    """What tells one requirement on a package apart from another: the name and
    the build string (DESIGN.md §9.4).
    """
    return f"{name} {build_string}" if build_string else name


def _masked(text: str) -> str:
    """`text` with every template expression replaced by filler of its length,
    so match spec fields can be found at their original offsets.
    """
    return _TEMPLATE.sub(lambda match: "T" * (match.end() - match.start()), text)


def _split_bracket(stripped: str) -> tuple[str, str]:
    """Take a match spec's bracket section off the end of a line.

    `hdf5 [build=nompi_*]` is `hdf5 * nompi_*` said the other way, and comes
    off before anything else is read (DESIGN.md §9.4).
    """
    masked = _masked(stripped)
    found = _BRACKET.search(masked)
    if found is None:
        return stripped, ""
    return stripped[: found.start()].strip(), stripped[found.start() :]


def _split_build_string(rest: str) -> tuple[str, str]:
    """Split what follows the name into a version part and a build string.

    A match spec is three whitespace-separated fields; templates are masked
    first because they contain spaces, and a space after a comparison
    operator is part of the version field. Exactly two fields, or nothing is
    split.
    """
    masked = _masked(rest)
    fields = [(span.start(), span.group(0)) for span in re.finditer(r"\S+", masked)]
    if len(fields) != 2:
        return rest, ""
    if not fields[0][1].strip("<>=!~"):
        return rest, ""
    start = fields[1][0]
    return rest[: fields[0][0] + len(fields[0][1])], rest[start:]


def parse_line(text: str) -> ParsedLine:
    """Split ``text`` into its name position and the rest.

    A template in the name position runs to its ``}}`` and through any suffix
    attached without a space, and need not open the line. Otherwise the name
    runs to whitespace or the first constraint operator. A trailing bracket
    comes off first (`_split_bracket`).
    """
    stripped, bracket = _split_bracket(text.strip())
    opens = stripped.find(_OPEN)
    # Only where the template is in the name position: nothing before it but a
    # literal prefix.
    if opens != -1 and not any(
        character.isspace() or character in "<>=!~" for character in stripped[:opens]
    ):
        close = stripped.find(_CLOSE, opens)
        if close != -1:
            end = close + len(_CLOSE)
            # A suffix glued straight onto the expression is part of the name.
            while end < len(stripped) and not stripped[end].isspace():
                end += 1
            name = stripped[:end]
            constraint, build_string = _split_build_string(stripped[end:].strip())
            return ParsedLine(
                text=text,
                name=name,
                constraint=constraint,
                function=_function(name),
                build_string=bracket or build_string,
            )

    # A constraint need not be separated by a space.
    match = _NAME.match(stripped)
    name = match.group(0) if match else stripped
    constraint, build_string = _split_build_string(stripped[len(name) :].strip())
    return ParsedLine(
        text=text,
        name=name,
        constraint=constraint,
        function=_function(name),
        build_string=bracket or build_string,
    )


def _function(name: str) -> str | None:
    match = _CALL.match(name)
    return match.group(1) if match else None
