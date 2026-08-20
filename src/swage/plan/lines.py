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

__all__ = ["ParsedLine", "parse_line", "spec_key"]

#: The name is a call when the expression opens with ``f(``.
_CALL = re.compile(r"^\$\{\{\s*([A-Za-z_]\w*)\s*\(")

#: The name runs up to whitespace or the first constraint operator.
_NAME = re.compile(r"^[^\s<>=!~]+")

_OPEN = "${{"
_CLOSE = "}}"

#: A whole template expression, masked out before a line is split on
#: whitespace so that the spaces inside one do not read as field boundaries.
_TEMPLATE = re.compile(r"\$\{\{.*?\}\}")

#: The variant conda-forge feedstocks interpolate to name the platform an
#: artifact was built for, under `noarch_platforms`. **Not a conda-smithy
#: variable**: each feedstock declares it itself, in `recipe/variants.yaml` or
#: `recipe/conda_build_config.yaml`, and conda-smithy folds the value into the
#: rendered `.ci_support` file for each platform.
_NOARCH_PLATFORM = re.compile(r"\$\{\{\s*noarch_platform\s*\}\}")

#: Every value that variant is given. Always platform selectors, and always
#: out of this set -- checked across the eleven conda-forge feedstocks that
#: write the idiom, which declare it as `[win, unix]` or `[linux, osx, win]`.
#:
#: Expanded over all four rather than over the ones a particular feedstock
#: declares, which is the conservative direction: a line explained on every
#: value it *could* take is explained on the ones it does take, and reading
#: the declared set means parsing a second file to learn something that only
#: ever narrows the answer.
_PLATFORM_VALUES = ("linux", "osx", "win", "unix")

#: The other half of the idiom: a whole dependency chosen by the platform,
#: rather than a name with the platform spliced into it. `click` writes
#: ``${{ "colorama" if noarch_platform == "win" else "python" }}`` and
#: `terminado` writes the same without an `else`. The `else` is usually a
#: no-op filler -- `python` is a dependency regardless -- because a bare `if`
#: yields an empty entry.
#:
#: Matched as a whole line rather than parsed: this is one shape swage
#: recognizes, not an expression language it evaluates. Anything else stays
#: unexplained, which is what keeps the allowlist an allowlist.
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
    #: None where the name is not a template call. A template that is *not* a
    #: call -- ``${{ name }}-with-kerberos`` -- is None too, and so cannot be
    #: blessed by `functions`.
    function: str | None
    #: A conda match spec's third field, the build string: ``"nompi_*"`` in
    #: ``hdf5 * nompi_*``. Empty for all but the mpi corner of the fleet, and
    #: **never** anything upstream declared -- Python metadata has no way to
    #: say it (DESIGN.md 3.3.6).
    build_string: str = ""

    @property
    def templated_name(self) -> bool:
        """Whether the name position contains a template at all."""
        return _OPEN in self.name

    @property
    def rendered(self) -> str:
        """The line as swage writes it: name, one space, constraint.

        This is where `pyyaml>=6.0.3` becomes `pyyaml >=6.0.3`. conda-forge's
        linter wants the space and will ask for it eventually, so a recipe
        swage is already rewriting should come out clean rather than leaving
        the maintainer a lint comment to answer. Runs of spaces collapse for
        the same reason.

        Safe for every line, including ones swage does not own: a recipe-owned
        template has an empty constraint, so it renders back byte-identical.
        Per DESIGN.md 6 this only ever reaches a feedstock swage is modifying
        anyway -- it is not a reason to open a formatting-only pull request.
        """
        parts = (self.name, self.constraint, self.build_string)
        return " ".join(part for part in parts if part)

    @property
    def platform_expansions(self) -> tuple[str, ...]:
        """Every package name this line can name, across the platforms.

        Two shapes, both from the `noarch_platform` idiom, and empty for every
        other line in the fleet:

        - `__${{ noarch_platform }}` interpolates the platform into a name,
          and becomes `__linux`, `__osx`, `__win`, `__unix` -- the four
          `config/defaults.yaml` already blesses as recipe structure;
        - `${{ "colorama" if noarch_platform == "win" else "python" }}`
          chooses a whole dependency, and becomes `colorama` and `python`.

        Expansion rather than evaluation. swage substitutes one known
        variant's known values and matches one known shape; it is not running
        a template engine, and anything outside those two stays unexplained.

        Order is the order a reader meets the names, and duplicates are
        dropped -- an `else` naming the same package as the `if` is one name,
        not two.
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
            # Structure on every platform or structure on none: `__win` is
            # blessed and so are its three siblings, so the interpolated form
            # is the same claim written once. Requiring *all* of them keeps
            # this an allowlist -- a template expanding to something nobody
            # blessed is still unexplained.
            return all(name in owned.names for name in expansions)
        if self.templated_name:
            # An interpolated name that is not a call. `functions` cannot
            # describe it and `names` is for literals, so it stays unexplained
            # and G1 reports it rather than swage guessing.
            return False
        return self.name in owned.names


def spec_key(name: str, build_string: str) -> str:
    """What tells one requirement on a package apart from another.

    A section may state the same package twice, once with a build string and
    once without, and mean two requirements rather than one -- so everything
    that files a requirement under a name has to file it under this instead.
    Two places do: the plan, which would otherwise treat the second line as a
    constraint change to the first, and the report, which would otherwise say
    the first line was bumped into the second.
    """
    return f"{name} {build_string}" if build_string else name


def _split_build_string(rest: str) -> tuple[str, str]:
    """Split what follows the name into a version part and a build string.

    A conda match spec is three whitespace-separated fields -- name, version,
    build -- and the third is the one thing in a requirement line that upstream
    metadata cannot express. `hdf5 * nompi_*` and `hdf5` are two different
    requirements on one package, which is why the mpi feedstocks state both.

    Templates are masked before splitting, because they contain spaces:
    `python ${{ python_min }}.*` is a version and no build string, and reading
    it as two fields would make `.*` a build string on every noarch recipe in
    the fleet.

    Exactly two fields, or nothing is split. Anything longer is not a match
    spec swage can take apart, and a line it cannot take apart is one it keeps
    whole -- there is none in the fleet.

    **The space after a comparison operator is optional too.**
    `apache-airflow-core == ${{ version }}` is one version field written with a
    space in it, not a version of `==` and a build string of `${{ version }}`.
    Read the second way, the line files under a different key from the same
    requirement written without the space, so the planner sees a package it has
    no line for and writes a second one: `airflow`'s `apache-airflow` output
    came back carrying `apache-airflow-core` twice.
    """
    masked = _TEMPLATE.sub(lambda match: "T" * (match.end() - match.start()), rest)
    fields = [(span.start(), span.group(0)) for span in re.finditer(r"\S+", masked)]
    if len(fields) != 2:
        return rest, ""
    if not fields[0][1].strip("<>=!~"):
        return rest, ""
    start = fields[1][0]
    return rest[: fields[0][0] + len(fields[0][1])], rest[start:]


def parse_line(text: str) -> ParsedLine:
    """Split ``text`` into its name position and the rest.

    The name cannot be found by splitting on whitespace: template expressions
    contain spaces, so ``${{ pin_subpackage(name, exact=True) }}`` would come
    apart into ``${{`` and a constraint. Where the name position contains a
    template, it runs to the matching ``}}`` and through any suffix attached to
    it without a space -- which is what keeps ``${{ name }}-with-kerberos`` in
    one piece. Otherwise it runs to whitespace *or* the first constraint
    operator, since the space between them is conventional rather than
    required.

    **The template need not open the line.** `__${{ noarch_platform }}` is a
    prefix and then an expression, and reading only lines that *start* with
    ``${{`` split it at the first space: the name came out as the literal
    ``__${{`` and the feedstock was stopped over an `unrecognized template`
    naming three characters. Eleven conda-forge feedstocks write that line.
    """
    stripped = text.strip()
    opens = stripped.find(_OPEN)
    # Only where the template is in the *name* position: nothing before it but
    # a literal prefix. `pandas >=${{ python_min }}` and `pandas>=${{ x }}`
    # open a template too, and in both the name is `pandas` -- which is the
    # 612-line majority this module exists to keep on the other path.
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
                build_string=build_string,
            )

    # A constraint need not be separated by a space. Rare -- 8 of the 3,617
    # requirement lines in the maintainer's checkouts, `pyyaml>=6.0.3` and
    # `pluggy>=1.5.0` -- but splitting on whitespace alone would make the name
    # `pyyaml>=6.0.3`, which attributes to nothing and stops the feedstock at
    # G1 complaining about a package upstream plainly declares.
    match = _NAME.match(stripped)
    name = match.group(0) if match else stripped
    constraint, build_string = _split_build_string(stripped[len(name) :].strip())
    return ParsedLine(
        text=text,
        name=name,
        constraint=constraint,
        function=_function(name),
        build_string=build_string,
    )


def _function(name: str) -> str | None:
    match = _CALL.match(name)
    return match.group(1) if match else None
