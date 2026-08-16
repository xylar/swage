"""v0 -> v1 conversion, against four real recipes (DESIGN.md 7).

Each fixture reaches a different one of the outcomes a conversion can have,
and the set comes from running the converter over all 148 v0 feedstocks in the
fleet rather than from imagining what could go wrong:

| fixture | what it proves |
|---|---|
| `calver` | the mechanical case -- converts, and swage can read it back |
| `libspatialite` | a Jinja `{% if %}` block, which CRM will not parse |
| `sqlalchemy-jsonfield` | one key declared twice under different selectors |
| `apache-airflow-providers-common-sql` | CRM reports success, emits bad YAML |

**The last one is not a live v0 feedstock and is here on purpose.** Nothing in
the fleet's 148 makes CRM emit a file swage cannot read, so a corpus drawn only
from the fleet would have nothing to hold DESIGN.md 7.1's verification step in
place, and the step would look like caution rather than like something that has
fired. It fires here. The recipe is a copy taken before that feedstock was
migrated by hand.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from swage.migrate import MigrationError, convert_recipe

CORPUS = Path(__file__).resolve().parent / "corpus" / "v0"


def meta_yaml(feedstock: str) -> str:
    return (CORPUS / feedstock / "meta.yaml").read_text(encoding="utf-8")


def test_the_corpus_is_the_four_outcomes() -> None:
    assert sorted(path.name for path in CORPUS.iterdir() if path.is_dir()) == [
        "apache-airflow-providers-common-sql",
        "calver",
        "libspatialite",
        "sqlalchemy-jsonfield",
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
