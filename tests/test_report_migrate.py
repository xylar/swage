"""What `swage migrate` prints.

A conversion rewrites the whole recipe, so the report is not a diff -- every
line changed, and the few that matter would be invisible in one. What these
pin is that the few that matter are what a reader gets.
"""

from __future__ import annotations

from pathlib import Path

from swage.migrate import Migration, convert_recipe
from swage.report import render_migration, render_refusal

CORPUS = Path(__file__).resolve().parent / "corpus" / "v0"


def migration_for(feedstock: str, added: tuple[str, ...] = ()) -> Migration:
    meta = (CORPUS / feedstock / "meta.yaml").read_text(encoding="utf-8")
    converted = convert_recipe(meta, feedstock)
    return Migration(
        feedstock=feedstock,
        ref="main",
        recipe_text=converted.text,
        recipe=converted.recipe,
        forge_config_text="",
        forge_config_added=added,
        concerns=converted.concerns,
        corrections=converted.corrections,
        notes=converted.notes,
        review=converted.review,
    )


def test_a_clean_conversion_says_what_would_change() -> None:
    rendered = render_migration(
        migration_for("calver", ("conda_build_tool", "conda_install_tool"))
    )

    assert "calver  would convert to a v1 recipe at main" in rendered
    assert "would set conda_build_tool, conda_install_tool" in rendered


def test_a_feedstock_already_building_with_rattler_says_so() -> None:
    """Five of the maintainer's checkouts set one of the two and not the other.

    "would set nothing" would read as a report that failed to work something
    out, rather than as a feedstock that has already been told.
    """
    rendered = render_migration(migration_for("calver"))

    assert "already builds with rattler-build" in rendered


def test_what_a_reviewer_must_read_is_the_only_thing_quoted() -> None:
    """`aiohttp` makes the converter say nine things, one of them load-bearing.

    Six are counted rather than printed and two are dropped entirely. Printing
    all nine would be the same as printing none, since the reader would have
    to sort them.
    """
    rendered = render_migration(migration_for("aiohttp"))

    assert "read these before merging:" in rendered
    assert "tests_to_skip" in rendered
    assert "6 other messages from the converter" in rendered
    assert "ambiguous version constraints" not in rendered
    assert "license_family" not in rendered


def test_a_noarch_conversion_has_no_ledger_to_print() -> None:
    """`calver` states no conditions, so the section does not appear at all.

    This is what keeps the review free on the noarch half: 104 of the fleet's
    105 noarch v0 recipes have nothing conditional in them, and a heading over
    an empty list would be three lines of nothing on every one of them.
    """
    rendered = render_migration(migration_for("calver"))

    assert "what became of each condition" not in rendered


def test_a_compiled_conversion_accounts_for_every_condition() -> None:
    """`tiledb`: twenty selector comments, three conditions, all landing.

    One row per condition rather than per line -- `# [win]` nine times over is
    one thing to check -- with the line count kept, since a condition that
    guarded nine lines and landed on two is worth noticing.
    """
    rendered = render_migration(migration_for("tiledb"))

    assert "what became of each condition the old recipe stated:" in rendered
    assert "win      9 lines  ->  9 if:/then: entries" in rendered
    assert "unix     2 lines  ->  2 if:/then: entries" in rendered
    assert "the conversion is wrong here" not in rendered


def test_a_damaged_conversion_says_so_before_anything_the_converter_said() -> None:
    """`igraph` loses an environment variable, and CRM calls it a selector.

    Two headings rather than one, because they are two instructions: what
    swage found means the recipe is not what the old one said and has to be
    fixed, and what the converter reported means somebody should look. Merged,
    the first would be one bullet among several.
    """
    rendered = render_migration(migration_for("igraph"))

    wrong = rendered.index("the conversion is wrong here")
    reported = rendered.index("read these before merging:")
    assert wrong < reported
    assert "F2C_EXTERNAL_ARITH_HEADER" in rendered
    assert "arm64          1 line   ->  nowhere -- see above" in rendered


def test_a_truncated_value_is_shown_beside_what_the_recipe_said() -> None:
    """`fiona`'s build command comes out cut short and swage quotes both.

    The converted line on its own reads like an ordinary conditional; only the
    pair shows the word that lost its ending, and the difference between them
    is two characters in the middle of a long command. So neither is wrapped,
    and the two sit under labels in the same column -- a build command reflowed
    across three lines of prose is one nobody can compare against anything.
    """
    rendered = render_migration(migration_for("fiona"))

    quoted = [line for line in rendered.splitlines() if "pip install" in line]
    assert quoted == [
        "      meta.yaml    script: {{ PYTHON }} -m pip install . -vv "
        "--no-deps --no-build-isolation  # [unix]",
        "      recipe.yaml  content: ${{ PYTHON }} -m pip install . -vv "
        "--no-deps --no-build-isolati if unix else '' }}",
    ]
    assert "unix                               1 line   ->  folded into 1 value" in (
        rendered
    )


def test_every_report_says_a_person_has_to_read_it() -> None:
    """Migration is reviewed by hand whatever the gates think (DESIGN.md 7).

    The clean case is the one where that needs saying: a report with nothing
    in it to worry about is exactly the one somebody would merge unread.
    """
    rendered = render_migration(migration_for("calver"))

    assert "reviewed by hand, never merged" in rendered


def test_no_design_shorthand_reaches_the_terminal() -> None:
    """Nobody reading a terminal has the design open (CLAUDE.md).

    `G4` and `3.3.7` are how this project talks to itself, and a maintainer
    who meets one in output has been handed a question they cannot research.
    """
    rendered = render_migration(migration_for("aiohttp")) + render_refusal(
        "libspatialite", "libspatialite: no\n    because\n  convert by hand"
    )

    assert "DESIGN.md" not in rendered
    assert "path A" not in rendered
    for gate in range(1, 14):
        assert f"G{gate}" not in rendered


def test_a_refusal_keeps_the_shape_of_the_message_it_was_given() -> None:
    """The message already indents its detail and its instruction differently.

    Re-indenting it on the way through shifted the two apart, which read as
    ragged rather than as structured -- and the structure is the part that
    tells a reader which line is the one to act on.
    """
    reason = (
        "libspatialite: the converter cannot read this recipe\n"
        "    it uses a Jinja block\n"
        "    unsupported JINJA statement\n"
        "  convert this feedstock by hand"
    )

    rendered = render_refusal("libspatialite", reason)

    assert rendered == (
        "libspatialite  not converted\n"
        "    it uses a Jinja block\n"
        "    unsupported JINJA statement\n"
        "  convert this feedstock by hand\n"
    )


def test_a_refusal_does_not_repeat_the_feedstock_name() -> None:
    """The heading has just said it, and the message opens by saying it."""
    rendered = render_refusal("libspatialite", "libspatialite: no\n  by hand")

    assert rendered.count("libspatialite") == 1
