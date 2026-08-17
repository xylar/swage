"""v0 `meta.yaml` to v1 `recipe.yaml`, via conda-recipe-manager (DESIGN.md 7).

CRM is the converter and swage is the proofreader. That division is the whole
of this module: CRM knows how a v0 recipe maps onto v1's schema -- selector
comments into `if:`/`then:`, Jinja `{{ }}` into `${{ }}`, the `test:` section
into `tests:` -- and swage knows whether the result is something it can go on
to read, plan against and splice. Neither question answers the other.

**The conversion is verified before anything is reconciled against it**
(DESIGN.md 7.1), and this is not a formality. Over the 137 v0 feedstocks in
the maintainer's checkouts, CRM produced a `recipe.yaml` that swage's own
reader rejects exactly once, and the reason was not something a message table
would have reported: `apache-airflow-providers-common-sql` ends one output's
`run` list with a whole-line comment, and CRM re-emits that comment ahead of
the *next* output while dropping the `-` that opens it. The second output's
keys then land in the first output's mapping and the file has duplicate keys.
CRM reported no error at all. Only re-reading the file catches it, which is
why that step is here rather than in a caller.

**What CRM cannot parse, swage does not attempt.** 17 of the 137 fail before
conversion begins, and all but one are compiled: ten open a `{% if %}` block,
which is a Jinja statement rather than an expression and has no v1 spelling
CRM will guess at, and seven declare a key twice under different selectors --
`script` once for Windows and once for Unix. Those are refusals with a
message, not crashes: the recipe is untouched and a person converts it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from conda_recipe_manager.parser._message_table import MessageCategory, MessageTable
from conda_recipe_manager.parser.recipe_parser_convert import RecipeParserConvert

from swage.recipe import Recipe, RecipeError, read_recipe

from .errors import MigrationError
from .licenses import license_problems
from .review import Review, review_conversion

__all__ = ["Conversion", "convert_recipe"]


@dataclass(frozen=True)
class Conversion:
    """A converted recipe, and everything the converter said while doing it."""

    #: The v1 recipe text, already proven readable by swage's own reader.
    text: str
    #: That reader's result, so a caller planning against the conversion does
    #: not parse it a second time.
    recipe: Recipe
    #: What became of each condition the v0 recipe stated -- read out of the
    #: converted recipe rather than taken from the converter (`review`).
    review: Review
    #: What a person reviewing this conversion has to look at. Migration is
    #: reviewed by hand whatever is in here (DESIGN.md 7); what this decides is
    #: what the review is pointed at.
    concerns: tuple[str, ...] = ()
    #: Everything else the converter said. Worth keeping, not worth reading.
    notes: tuple[str, ...] = ()


def convert_recipe(meta_yaml: str, feedstock: str) -> Conversion:
    """Convert one `meta.yaml`, or refuse it with a reason.

    Raises `MigrationError` where CRM cannot read the v0 recipe, where it
    cannot render a v1 one, or where swage cannot read what it rendered.
    """
    try:
        converter = RecipeParserConvert(meta_yaml)
    except Exception as exc:
        raise MigrationError(_unparsed(feedstock, exc)) from exc

    try:
        text, messages, _ = converter.render_to_v1_recipe_format()
    except Exception as exc:
        raise MigrationError(
            f"{feedstock}: the converter failed part-way through this recipe\n"
            f"    {_first_line(exc)}\n"
            "  convert this feedstock by hand"
        ) from exc

    concerns, notes = _sort_messages(messages)

    try:
        recipe = read_recipe(text, feedstock)
    except RecipeError as exc:
        raise MigrationError(
            f"{feedstock}: the converted recipe is not one swage can read\n"
            f"    {_first_line(exc)}\n"
            "  the conversion has not been written anywhere -- convert this "
            "feedstock by hand"
        ) from exc

    # swage's own reading of the conversion, rather than a relay of CRM's.
    # The licence is the one field CRM reports on and cannot judge: it carries
    # every licence through unchanged -- 120 of the maintainer's 121
    # convertible recipes byte-identical, the last a `BSD 3-Clause` corrected
    # to `BSD-3-Clause` -- and then says it "could not patch" the ones its
    # table did not recognise, which includes every valid compound expression.
    concerns += license_problems(text)

    # Damage first, and ahead of anything CRM said, because it is the only
    # thing in a conversion report that means the recipe is *wrong* rather
    # than worth a look. The converter reports none of it (`review`).
    review = review_conversion(meta_yaml, text)

    return Conversion(
        text=text,
        recipe=recipe,
        concerns=review.damage + concerns,
        notes=notes,
        review=review,
    )


#: What CRM's own exception types mean, said in terms of the recipe rather than
#: of the parser. Its messages name their class and a line number, which is
#: enough to find the construct and not enough to know what to do about it.
_UNPARSED = {
    "ParsingJinjaException": (
        "it uses a Jinja `{% if %}` block, which selects whole sections rather "
        "than one line, and has no direct v1 spelling"
    ),
    "DuplicateKeyException": (
        "it declares one key twice under different selectors, which v0 allows "
        "and YAML does not"
    ),
}


def _unparsed(feedstock: str, exc: Exception) -> str:
    explanation = _UNPARSED.get(type(exc).__name__)
    detail = f"    {explanation}\n" if explanation else ""
    return (
        f"{feedstock}: the converter cannot read this recipe\n"
        f"{detail}"
        f"    {_first_line(exc)}\n"
        "  convert this feedstock by hand"
    )


#: Messages reporting that a field v1 does not have was removed. Dropped
#: outright rather than kept as a note: the field is gone because it no longer
#: exists, there is no version of this a reader would act on, and it is the
#: single most common thing the converter says -- `license_family` alone
#: appears in 62 of the maintainer's 137 v0 recipes. Counting it would put a
#: number in the report that means nothing.
_DISCARDED = re.compile(
    r"^Field at `/(?:outputs/\d+/)?about/\w+` is no longer supported"
)

#: Messages that say nothing a reviewer has to act on, matched on their opening
#: text.
_BENIGN = (
    # Fires on any line whose *constraint* holds a template, and means CRM
    # declined to normalize that constraint -- not that it failed to convert
    # the line. It converts them all: `{{ compiler('c') }}` becomes
    # `${{ compiler('c') }}`, `python {{ python_min }}` becomes
    # `python ${{ python_min }}`, and a `# [use_noarch]` selector beside it
    # becomes an `if:`/`then:` entry. What it will not do is turn
    # `python {{ python_min }}` into a globbed form the way it turns
    # `six 1.11.0` into `six 1.11.0.*`, which is the right call. 395 messages
    # over 78 recipes, every one of them about a Jinja expression.
    "Recipe upgrades cannot currently upgrade ambiguous version constraints",
    # CRM's licence table does not parse compound expressions, so this fires on
    # `MIT AND Apache-2.0`, which is impeccable, and on `Apache Software`,
    # which is not an identifier at all. swage checks the licence in the
    # converted recipe itself and says something useful about it, so CRM's
    # inability to look one up adds nothing (see `licenses`).
    "Could not patch unrecognized license",
)


def _sort_messages(messages: MessageTable) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Split what the converter said into what to read, keep, and throw away.

    **CRM's own severities are not the axis.** It files everything below an
    outright failure as a warning, and that bucket runs from "a v0 field that
    no longer exists was removed" to "this dependency's version has been
    changed" -- the latter being `six 1.11.0` becoming `six 1.11.0.*`, which is
    a different requirement and is the single thing in a conversion most worth
    a second pair of eyes.

    So the benign classes are named and everything else is a concern, rather
    than the other way round. An unrecognized message reaches a person, which
    is the same direction every other allowlist in swage points.

    **Repeats are collapsed, in first-seen order.** The converter reports per
    occurrence rather than per finding, so `wetterdienst` says
    `Version on dependency changed to: python 3.10.*` thirty-five times and
    `airflow` says its own nineteen. That is one thing to check in each case,
    and thirty-five copies of it bury the rest of the report exactly the way
    the benign classes would.
    """
    concerns: dict[str, None] = {}
    notes: dict[str, None] = {}
    for category in MessageCategory:
        for message in messages.get_messages(category):
            text = str(message).strip()
            if _DISCARDED.match(text):
                continue
            benign = category is MessageCategory.WARNING and text.startswith(_BENIGN)
            (notes if benign else concerns)[text] = None
    return tuple(concerns), tuple(notes)


def _first_line(exc: Exception) -> str:
    """One line of an exception, since these carry multi-line parser dumps."""
    lines = [line.strip() for line in str(exc).splitlines() if line.strip()]
    return lines[0] if lines else type(exc).__name__
