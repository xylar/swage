"""v0 -> v1 conversion, against eight real recipes (DESIGN.md 7).

Each fixture reaches a different one of the outcomes a conversion can have,
and the set comes from running the converter over all 148 v0 feedstocks in the
fleet rather than from imagining what could go wrong:

| fixture | what it proves |
|---|---|
| `calver` | the mechanical case -- converts, and swage can read it back |
| `aiohttp` | a conversion with something in it a person has to read |
| `libspatialite` | a Jinja `{% if %}` block, which CRM will not parse |
| `sqlalchemy-jsonfield` | one key declared twice under different selectors |
| `apache-airflow-providers-common-sql` | CRM reports success, emits bad YAML |
| `tiledb` | a compiled recipe whose twenty selectors all convert faithfully |
| `igraph` | a selector on a scalar, whose value CRM drops entirely |
| `fiona` | a selector on a scalar, whose value CRM truncates |

**`apache-airflow-providers-common-sql` is not a live v0 feedstock and is here
on purpose.** Nothing in the fleet's 148 makes CRM emit a file swage cannot
read, so a corpus drawn only from the fleet would have nothing to hold
DESIGN.md 7.1's verification step in place, and the step would look like
caution rather than like something that has fired. It fires here. The recipe is
a copy taken before that feedstock was migrated by hand.

**The last three are compiled, and are the reason that half of the migration is
its own phase.** A selector on a scalar value is a compiled-recipe idiom, and
it is the shape CRM handles worst: over the fleet's 148, every recipe it
truncates and every recipe it silently drops a condition from is compiled.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conda_recipe_manager.parser.recipe_parser_convert import RecipeParserConvert

from swage.migrate import MigrationError, convert_recipe

CORPUS = Path(__file__).resolve().parent / "corpus" / "v0"


def meta_yaml(feedstock: str) -> str:
    return (CORPUS / feedstock / "meta.yaml").read_text(encoding="utf-8")


def test_the_corpus_is_the_outcomes_a_conversion_can_have() -> None:
    assert sorted(path.name for path in CORPUS.iterdir() if path.is_dir()) == [
        "aiohttp",
        "apache-airflow-providers-common-sql",
        "calver",
        "fiona",
        "igraph",
        "libspatialite",
        "sqlalchemy-jsonfield",
        "tiledb",
    ]


def test_a_plain_noarch_recipe_converts() -> None:
    """The mechanical case, and 104 of the fleet's 105 noarch v0 recipes.

    `calver` is a `noarch: python` package with no selectors and nothing
    conditional. What the conversion has to get right is the schema: v1's
    `schema_version`, `${{ }}` interpolation, `tests:` rather than `test:`.
    """
    converted = convert_recipe(meta_yaml("calver"), "calver")

    assert "schema_version: 1" in converted.text
    assert converted.recipe.outputs[0].noarch == "python"
    assert not converted.concerns


def test_the_conversion_is_readable_by_swage_itself() -> None:
    """The point of returning a `Recipe` rather than only text.

    A conversion swage cannot read is one swage cannot plan against, so the
    caller gets the reader's own result and never parses the text twice.
    """
    converted = convert_recipe(meta_yaml("calver"), "calver")

    assert [output.name for output in converted.recipe.outputs] == ["calver"]


def test_the_python_floor_is_written_the_way_a_v1_recipe_writes_it() -> None:
    """v0's `python {{ python_min }}` asks for the series; v1's asks for one.

    Every v1 recipe on the fleet writes the trailing `.*` in `host` -- 392 of
    392 -- and the converter carries the v0 spelling straight across. swage
    writes it, because nothing about it is a decision.
    """
    converted = convert_recipe(meta_yaml("calver"), "calver")
    host = converted.recipe.outputs[0].blocks["host"].content.texts()

    assert "python ${{ python_min }}.*" in host
    assert "python ${{ python_min }}" not in host


def test_the_run_floor_is_left_exactly_as_the_converter_wrote_it() -> None:
    """v0 and v1 spell that one the same way, so there is nothing to correct."""
    converted = convert_recipe(meta_yaml("calver"), "calver")
    run = converted.recipe.outputs[0].blocks["run"].content.texts()

    assert "python >=${{ python_min }}" in run


def test_writing_the_floor_is_reported_rather_than_done_quietly() -> None:
    """swage wrote a line the converter did not, and the reviewer is told."""
    converted = convert_recipe(meta_yaml("calver"), "calver")

    assert converted.corrections == (
        "the python floor in calver's host requirements now reads "
        "`python ${{ python_min }}.*` -- the v0 spelling asks for that version "
        "alone once it is a v1 recipe",
    )
    # It is not a concern: a concern is something still to decide.
    assert not converted.concerns


def test_a_recipe_with_no_python_floor_is_not_corrected() -> None:
    """`tiledb` is compiled and states no `python_min` anywhere."""
    converted = convert_recipe(meta_yaml("tiledb"), "tiledb")

    assert converted.corrections == ()


def test_the_correction_leaves_every_other_byte_of_the_conversion_alone() -> None:
    """It is spliced through the recipe model like any other write.

    The whole file is already being rewritten, so a correction that also
    reformatted something would be invisible -- which is the argument for
    checking it here rather than trusting the splice.
    """
    converted = convert_recipe(meta_yaml("calver"), "calver")
    uncorrected, _, _ = RecipeParserConvert(
        meta_yaml("calver")
    ).render_to_v1_recipe_format()
    restored = converted.text.replace(
        "python ${{ python_min }}.*", "python ${{ python_min }}"
    )

    assert converted.text != uncorrected
    assert restored == uncorrected


def test_the_templated_lines_a_converter_cannot_normalize_are_only_notes() -> None:
    """The message swage must not put in front of a reviewer.

    CRM reports that it "cannot currently upgrade ambiguous version
    constraints" for any line holding a template, and what it does about it is
    leave the line exactly as written -- which is what swage wants. It fires
    395 times over the maintainer's 137 v0 recipes, on `python
    {{ python_min }}`, `{{ compiler('c') }}`, `{{ pin_subpackage(...) }}` and
    nothing else. A report that shows it has buried whatever else it says.
    """
    converted = convert_recipe(meta_yaml("calver"), "calver")

    assert any("ambiguous version constraints" in note for note in converted.notes)
    assert not converted.concerns


def test_what_a_reviewer_has_to_read_is_separated_from_what_they_do_not() -> None:
    """The classification, on a real recipe that produces both kinds.

    `aiohttp` makes the converter say nine things, and exactly one of them
    changes what the recipe means: `tests_to_skip` is defined twice and the
    conversion cannot carry both. Two report the removal of a field v1 does
    not have and are dropped outright; six are the benign classes and are
    counted rather than quoted.

    This is the direction that matters: the one concern is on no list swage
    keeps. It is a concern because it is *not* on the benign list, so a
    message nobody anticipated reaches a person rather than being filed away.

    Read past what swage found for itself, which for this recipe is the four
    conditions the conversion dropped (`review`). Those come first in the
    concerns and are counted by a test of their own; this one is about how the
    converter's own messages are sorted.
    """
    converted = convert_recipe(meta_yaml("aiohttp"), "aiohttp")

    said = converted.concerns[len(converted.review.damage) :]
    assert len(said) == 1
    assert "defined multiple times" in said[0]
    assert len(converted.notes) == 6


def test_a_field_v1_no_longer_has_is_dropped_rather_than_counted() -> None:
    """`license_family` alone appears in 62 of the maintainer's 137 recipes.

    It is gone because v1 does not have it, there is no version of that a
    reader would act on, and counting it would put a number in the report
    that means nothing. So it is discarded rather than kept as a note.
    """
    converted = convert_recipe(meta_yaml("aiohttp"), "aiohttp")

    said = converted.concerns + converted.notes
    assert not any("license_family" in item for item in said)
    assert not any("no longer supported" in item for item in said)


def test_a_compound_license_is_not_a_concern() -> None:
    """The message that started this: `MIT AND Apache-2.0`.

    A v1 recipe keeps one SPDX expression in one scalar, so this is correct
    as written and comes through the conversion untouched. The converter says
    it "could not patch" it because its own table cannot parse a compound
    expression, which is a fact about the table.
    """
    converted = convert_recipe(meta_yaml("aiohttp"), "aiohttp")

    assert "license: MIT AND Apache-2.0" in converted.text
    assert not any("license" in item for item in converted.concerns)


def test_a_jinja_if_block_is_refused_with_its_reason() -> None:
    """`{% if %}` is a Jinja statement rather than an expression.

    It selects whole sections of a recipe, so there is no `if:`/`then:` entry
    it maps onto and CRM does not guess. The refusal has to name the
    construct, because the exception says only "unsupported JINJA statement"
    and a line number.
    """
    with pytest.raises(MigrationError) as raised:
        convert_recipe(meta_yaml("libspatialite"), "libspatialite")

    message = str(raised.value)
    assert "libspatialite: the converter cannot read this recipe" in message
    assert "Jinja `{% if %}` block" in message
    assert "convert this feedstock by hand" in message


def test_a_key_declared_twice_is_refused_with_its_reason() -> None:
    """Five of the fleet's 148, and the most common refusal there is.

    v0 allows a key to appear once per selector, because the selectors are
    comments and the file is preprocessed before it is YAML -- `script` once
    for Windows and once for Unix. A v1 recipe is YAML first, so the same
    recipe has a duplicate key and no parser will read it.
    """
    with pytest.raises(MigrationError) as raised:
        convert_recipe(meta_yaml("sqlalchemy-jsonfield"), "sqlalchemy-jsonfield")

    assert "one key twice under different selectors" in str(raised.value)


def test_a_conversion_crm_calls_clean_can_still_be_unreadable() -> None:
    """Why DESIGN.md 7.1 verifies rather than trusting the message table.

    `apache-airflow-providers-common-sql` ends one output's `run` list with a
    whole-line comment. CRM re-emits that comment ahead of the *next* output
    and drops the `-` that opens it, so the second output's keys land in the
    first output's mapping and `package` is declared twice. CRM reports no
    error whatever; only reading the file back finds it.

    The refusal says the conversion was not written anywhere, because what a
    maintainer needs to know first is that nothing was pushed.
    """
    with pytest.raises(MigrationError) as raised:
        convert_recipe(
            meta_yaml("apache-airflow-providers-common-sql"),
            "apache-airflow-providers-common-sql",
        )

    message = str(raised.value)
    assert "the converted recipe is not one swage can read" in message
    assert "has not been written anywhere" in message


def test_the_feedstock_is_named_in_every_refusal() -> None:
    """A sweep reports these one per line, so the name has to be in the text."""
    for feedstock in ("libspatialite", "sqlalchemy-jsonfield"):
        with pytest.raises(MigrationError) as raised:
            convert_recipe(meta_yaml(feedstock), feedstock)
        assert str(raised.value).startswith(f"{feedstock}: ")


def test_a_repeated_message_is_said_once() -> None:
    """The converter reports per occurrence, not per finding.

    `wetterdienst` says `Version on dependency changed to: python 3.10.*`
    thirty-five times and `airflow` says its own nineteen. That is one thing
    to check in each case, and thirty-five copies of it bury the rest of the
    report exactly the way the benign classes would.
    """
    converted = convert_recipe(meta_yaml("aiohttp"), "aiohttp")

    assert len(converted.notes) == len(set(converted.notes))
    assert len(converted.concerns) == len(set(converted.concerns))
