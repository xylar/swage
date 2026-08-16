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
        notes=converted.notes,
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
    """`aiohttp` makes the converter say nine things, two of them load-bearing.

    The other seven are counted rather than printed. Printing all nine would
    be the same as printing none, since the reader would have to sort them.
    """
    rendered = render_migration(migration_for("aiohttp"))

    assert "read these before merging:" in rendered
    assert "tests_to_skip" in rendered
    assert "unrecognized license" in rendered
    assert "7 other messages from the converter" in rendered
    assert "ambiguous version constraints" not in rendered


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
