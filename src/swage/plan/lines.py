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

#: The name runs up to whitespace or the first constraint operator.
_NAME = re.compile(r"^[^\s<>=!~]+")

_OPEN = "${{"
_CLOSE = "}}"

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
        return f"{self.name} {self.constraint}" if self.constraint else self.name

    @property
    def platform_expansions(self) -> tuple[str, ...]:
        """This name with `noarch_platform` replaced by each value it takes.

        `__${{ noarch_platform }}` becomes `__linux`, `__osx`, `__win`,
        `__unix` -- the four names `config/defaults.yaml` already blesses as
        recipe structure. Empty where the name does not interpolate that
        variant, which is every line in the fleet bar the eleven feedstocks
        writing this idiom.

        Expansion rather than evaluation. swage is not running the template
        engine; it is substituting one known variant's known values, which is
        the whole of what this idiom does.
        """
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
            return ParsedLine(
                text=text,
                name=name,
                constraint=stripped[end:].strip(),
                function=_function(name),
            )

    # A constraint need not be separated by a space. Rare -- 8 of the 3,617
    # requirement lines in the maintainer's checkouts, `pyyaml>=6.0.3` and
    # `pluggy>=1.5.0` -- but splitting on whitespace alone would make the name
    # `pyyaml>=6.0.3`, which attributes to nothing and stops the feedstock at
    # G1 complaining about a package upstream plainly declares.
    match = _NAME.match(stripped)
    name = match.group(0) if match else stripped
    return ParsedLine(
        text=text,
        name=name,
        constraint=stripped[len(name) :].strip(),
        function=_function(name),
    )


def _function(name: str) -> str | None:
    match = _CALL.match(name)
    return match.group(1) if match else None
