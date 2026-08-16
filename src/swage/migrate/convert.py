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

from dataclasses import dataclass

from conda_recipe_manager.parser._message_table import MessageCategory, MessageTable
from conda_recipe_manager.parser.recipe_parser_convert import RecipeParserConvert

from swage.recipe import Recipe, RecipeError, read_recipe

from .errors import MigrationError

__all__ = ["Conversion", "convert_recipe"]


@dataclass(frozen=True)
class Conversion:
    """A converted recipe, and everything the converter said while doing it."""

    #: The v1 recipe text, already proven readable by swage's own reader.
    text: str
    #: That reader's result, so a caller planning against the conversion does
    #: not parse it a second time.
    recipe: Recipe
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

    return Conversion(text=text, recipe=recipe, concerns=concerns, notes=notes)


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


#: Messages that say nothing a reviewer has to act on, matched on their opening
#: text. Both are noise by construction rather than by luck, and between them
#: they are 457 of the 558 messages the converter produced over the maintainer's
#: 137 v0 recipes -- so leaving them in would bury the ones that matter.
_BENIGN = (
    # Fires on any line CRM cannot normalize because it holds a template, and
    # what it does then is leave the line exactly as written -- which is what
    # swage wants. Every subject of it across the fleet is Jinja:
    # `python {{ python_min }}`, `{{ compiler('c') }}`, `{{ stdlib("c") }}`,
    # `{{ pin_subpackage(name, exact=True) }}`. 395 messages, 78 recipes.
    "Recipe upgrades cannot currently upgrade ambiguous version constraints",
    # A v0 field with no v1 spelling, dropped. `license_family` is derivable
    # from `license` and v1 says so; `doc_source_url` simply went away. 66
    # messages, and `license_family` alone appears in 62 of the 137.
    "Field at `/about/",
)


def _sort_messages(messages: MessageTable) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Split what the converter said into what to read and what to keep.

    **CRM's own severities are not the axis.** It files everything below an
    outright failure as a warning, and that bucket runs from "a v0 field went
    away" to "this dependency's version has been changed" -- the latter being
    `six 1.11.0` becoming `six 1.11.0.*`, which is a different requirement and
    is the single thing in a conversion most worth a second pair of eyes.

    So the benign classes are named and everything else is a concern, rather
    than the other way round. An unrecognized message reaches a person, which
    is the same direction every other allowlist in swage points.
    """
    concerns: list[str] = []
    notes: list[str] = []
    for category in MessageCategory:
        for message in messages.get_messages(category):
            text = str(message).strip()
            benign = category is MessageCategory.WARNING and text.startswith(_BENIGN)
            (notes if benign else concerns).append(text)
    return tuple(concerns), tuple(notes)


def _first_line(exc: Exception) -> str:
    """One line of an exception, since these carry multi-line parser dumps."""
    lines = [line.strip() for line in str(exc).splitlines() if line.strip()]
    return lines[0] if lines else type(exc).__name__
