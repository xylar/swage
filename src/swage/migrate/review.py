"""What the conversion did with each of the v0 recipe's conditions (DESIGN.md 7).

A conversion rewrites the file from end to end, so the diff a reviewer is
handed says only that everything changed. For a `noarch: python` recipe that
costs little -- there is usually nothing conditional in it at all -- but a
compiled recipe is conditions from top to bottom, and those are exactly what
the conversion is doing work on: a v0 recipe states them in selector comments
(`# [win]`, `# [not use_noarch]`) and a v1 recipe states them in `if:`/`then:`
structure. So this module answers, for each condition the v0 recipe wrote, what
became of it. That is the review a diff cannot give.

**Two of the answers mean the converted recipe is wrong**, and neither is
something CRM reports as an error:

- a condition that landed **nowhere**. `igraph` puts an `arm64` selector on an
  entry of `build.script_env`; the converter emits `script: {}` and the
  environment variable is gone. `aiohttp` conditions four `{% set %}`
  statements that build up a test-skip list, and loses all four.
- a value the converter **truncated**. Folding a condition into a scalar, CRM
  strips three characters off each end, meaning to unwrap a value that is
  wholly a `{{ ... }}` expression. Where the value merely *starts* with one --
  `{{ PYTHON }} -m pip install . -vv --no-deps --no-build-isolation` -- it eats
  the last two characters and the opening brace instead, and what comes out is
  `${{ PYTHON }} ... --no-build-isolati if unix else '' }}`. Valid YAML, read
  back by swage without complaint, and a different build command.

Both were found by running the converter over the fleet's 148 v0 recipes and
looking at the artifact rather than at the commentary, which is the same
division of labour `licenses` exists for. Over those 148 the check is quiet on
139: five conditions land nowhere, across `aiohttp` and `igraph`, and two
recipes are truncated, `fiona` and `backports-datetime-fromisoformat`. **All
four are compiled**, because conditioning a scalar is a compiled-recipe idiom
-- a noarch recipe conditions list *members*, and those convert faithfully.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from conda_recipe_manager.parser._types import Regex

__all__ = ["Condition", "Review", "review_conversion"]

#: A v0 selector: a trailing comment holding a bracketed boolean expression.
#: Anchored at the end of the line because that is the only place conda-build
#: reads one, and a `# [win]` in the middle of a sentence is prose.
_SELECTOR = re.compile(r"#\s*\[([^\]]+)\]\s*$")

#: `${{ value if condition else default }}` -- the shape CRM folds a condition
#: on a scalar into. The `if` is what identifies the condition inside it.
_INLINE = "inline"


@dataclass(frozen=True)
class Condition:
    """One condition the v0 recipe stated, and what became of it."""

    #: As `meta.yaml` writes it, inside the brackets: `win`, `py<310`.
    selector: str
    #: As a v1 recipe spells the same thing. Identical for a platform
    #: condition; a Python one becomes `match(python, "<3.10")`.
    expression: str
    #: What the selector was attached to in `meta.yaml`, in file order. Kept as
    #: the text rather than as line numbers, because a reviewer looking for a
    #: lost condition is looking for the line, not for where it used to be.
    guarded: tuple[str, ...]
    #: How the converted recipe states it: `if` for an `if:`/`then:` entry,
    #: `skip` for a build skip, `inline` for a value folded into `${{ }}`.
    #: Empty means the condition is not in the converted recipe at all.
    landed: tuple[str, ...]

    @property
    def lost(self) -> bool:
        """Whether the conversion dropped this condition and what it held."""
        return not self.landed


@dataclass(frozen=True)
class Review:
    """One conversion, read against the recipe it was made from."""

    #: Every condition in the v0 recipe, in the order it first appears.
    conditions: tuple[Condition, ...]
    #: What the converter provably got wrong, in sentences a reviewer can act
    #: on. Empty for a conversion with nothing wrong with it, which is 139 of
    #: the fleet's 148.
    damage: tuple[str, ...]


def review_conversion(meta_yaml: str, recipe_text: str) -> Review:
    """Read ``recipe_text`` against the ``meta_yaml`` it was converted from."""
    conditions = _conditions(meta_yaml, recipe_text)
    return Review(
        conditions=conditions,
        damage=_lost(conditions) + _truncated(meta_yaml, recipe_text),
    )


def _conditions(meta_yaml: str, recipe_text: str) -> tuple[Condition, ...]:
    """Every v0 selector, paired with where the conversion put it."""
    found: dict[str, list[str]] = {}
    for line in meta_yaml.splitlines():
        stripped = line.strip()
        if match := _SELECTOR.search(stripped):
            guarded = _SELECTOR.sub("", stripped).strip()
            found.setdefault(match.group(1).strip(), []).append(guarded)
    return tuple(
        Condition(
            selector=selector,
            expression=(expression := _as_v1(selector)),
            guarded=tuple(guarded),
            landed=_landings(recipe_text, expression),
        )
        for selector, guarded in found.items()
    )


def _as_v1(selector: str) -> str:
    """A v0 selector as a v1 recipe spells the same condition.

    **CRM's own substitutions, not a re-implementation of them.** Platform
    conditions come through unchanged and Python ones do not -- `py<310`
    becomes `match(python, "<3.10")` -- and this has to produce the string CRM
    produced, character for character, or every Python condition in the fleet
    reads as lost. Borrowing the patterns is what guarantees that; writing them
    out again would guarantee it only until CRM's next release.
    """
    expression = Regex.SELECTOR_PYTHON_VERSION_REPLACEMENT.sub(
        r'match(python, "\1\2.\3")', selector
    )
    expression = Regex.SELECTOR_PYTHON_VERSION_EQ_REPLACEMENT.sub(
        r'match(python, "==\1.\2")', expression
    )
    expression = Regex.SELECTOR_PYTHON_VERSION_NE_REPLACEMENT.sub(
        r'match(python, "!=\1.\2")', expression
    )
    expression = Regex.SELECTOR_PYTHON_VERSION_PY2K_REPLACEMENT.sub(
        r'match(python, ">=2,<3")', expression
    )
    expression = Regex.SELECTOR_PYTHON_VERSION_PY3K_REPLACEMENT.sub(
        r'match(python, ">=3,<4")', expression
    )
    return expression.strip()


def _landings(recipe_text: str, expression: str) -> tuple[str, ...]:
    """Every place the converted recipe states ``expression``.

    Three shapes, because CEP-13 gives a condition three homes: `if:`/`then:`
    for a list member, the `build.skip` list, and an inline `${{ x if c else
    '' }}` for a scalar that is not one.

    **Matched as a whole expression, never as a substring**, so that `win` is
    not found inside `not win`. A condition wrongly reported as landed is the
    expensive direction: it hides a value the conversion dropped, which is the
    one thing here nothing else would catch.
    """
    inside = re.compile(rf"(?<![\w.]){re.escape(expression)}(?![\w.])")
    landed = []
    for line in recipe_text.splitlines():
        stripped = line.strip()
        if condition := re.match(r"(?:- )?if:\s*(.+)$", stripped):
            if condition.group(1).strip() == expression:
                landed.append("if")
            continue
        if skip := re.match(r"(?:- )?skip:\s*(.+)$", stripped):
            if _states(skip.group(1), expression, inside):
                landed.append("skip")
            continue
        folded = rf"\bif\s+{re.escape(expression)}\b"
        if "${{" in stripped and re.search(folded, stripped):
            landed.append(_INLINE)
    return tuple(landed)


def _states(clause: str, expression: str, inside: re.Pattern[str]) -> bool:
    """Whether ``clause`` asserts ``expression`` rather than its negation.

    `skip` holds one boolean expression rather than a list of entries, so a
    condition reaches it joined to the others: `skip: win or match(python,
    ">=3.11")` is where two v0 selectors went. Substring matching is therefore
    the only option, and the trap it walks into is `not win`, which contains
    `win` and states the opposite. Anything the condition appears negated in is
    not a landing for it.
    """
    return any(
        inside.search(stated) and stated != f"not {expression}"
        for term in re.split(r"\band\b|\bor\b", clause)
        if (stated := term.strip())
    )


def _lost(conditions: tuple[Condition, ...]) -> tuple[str, ...]:
    """Conditions the converted recipe does not state anywhere.

    One sentence per condition rather than per line it guarded, because they
    are one finding: `aiohttp` loses three lines to a single `linux`, and they
    are three clauses of the same test-skip list.
    """
    return tuple(
        f"the `{condition.selector}` condition is not in the converted recipe "
        f"at all, and neither is what it applied to: "
        + "; ".join(f"`{line}`" for line in condition.guarded)
        for condition in conditions
        if condition.lost
    )


def _truncated(meta_yaml: str, recipe_text: str) -> tuple[str, ...]:
    """Values the converter cut short while folding a condition into them.

    Detected on the artifact -- a line opening a `${{` it never closes --
    rather than by predicting which values CRM will mangle. The recipe is the
    thing that has to be right, and an oracle about it keeps holding when the
    converter's next release moves the bug somewhere else. Over 182 real v1
    recipes in the maintainer's checkouts it fires on nothing.
    """
    damage = []
    for line in recipe_text.splitlines():
        stripped = line.strip()
        if "${{" not in stripped or stripped.count("${{") == stripped.count("}}"):
            continue
        written = _written_as(meta_yaml, stripped)
        damage.append(
            f"the converter cut a value short while making it conditional, "
            f"leaving a `${{{{` it never closes -- `{stripped}`"
            + (f", where the recipe said `{written}`" if written else "")
        )
    return tuple(damage)


def _written_as(meta_yaml: str, truncated: str) -> str:
    """The `meta.yaml` line ``truncated`` was cut from, or empty if unclear.

    What survives the truncation is a prefix of the original with its opening
    `{{` replaced by `${{`, so the original is the v0 line that starts with it.
    Worth the trouble because the two side by side are the whole finding: the
    converted line alone looks like a line with a condition in it, and only the
    pair shows the word that lost its ending.
    """
    value = truncated.split(":", 1)[-1].strip()
    prefix = value.split(" if ")[0].removeprefix("$").strip()
    if not prefix:
        return ""
    for line in meta_yaml.splitlines():
        stripped = line.strip()
        if stripped.split(":", 1)[-1].strip().startswith(prefix):
            return stripped
    return ""
