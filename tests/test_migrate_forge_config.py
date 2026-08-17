"""Setting the build tools in `conda-forge.yml` (DESIGN.md 7).

The one file swage is otherwise forbidden to touch, and the one edit to it
that is not optional: `conda-forge.yml` names the recipe, and conda-forge
builds a `recipe.yaml` with `conda-build` unless told otherwise, so a
migration that converts the recipe and leaves this alone produces a feedstock
that does not build.

The shapes here are the shapes the maintainer's 159 checkouts actually have:
103 setting neither key, 51 setting both, five setting `conda_build_tool`
alone. No file anywhere gives either key a different value.
"""

from __future__ import annotations

import pytest
import yaml

from swage.migrate import MigrationError, set_build_tools

#: A real v0 feedstock's file, keys in the order it writes them -- which is not
#: alphabetical, and is why the edit appends rather than placing.
V0 = """\
build_platform:
  osx_arm64: osx_64
conda_build:
  error_overlinking: true
conda_forge_output_validation: true
github:
  branch_name: main
  tooling_branch_name: main
provider:
  linux_aarch64: default
test: native_and_emulated
"""

MIGRATED = V0 + "conda_build_tool: rattler-build\nconda_install_tool: pixi\n"


def test_both_keys_are_added_to_a_v0_feedstock() -> None:
    edit = set_build_tools(V0, "example")

    assert edit.added == ("conda_build_tool", "conda_install_tool")
    assert yaml.safe_load(edit.text)["conda_build_tool"] == "rattler-build"
    assert yaml.safe_load(edit.text)["conda_install_tool"] == "pixi"


def test_nothing_that_was_already_there_moves() -> None:
    """Why the edit is text rather than a round-trip.

    A reviewer reading this diff should see two added lines and nothing else.
    A serializer that reflowed a nested mapping on the way past would be
    correct YAML and an unreviewable diff.
    """
    edit = set_build_tools(V0, "example")

    assert edit.text.startswith(V0)
    assert edit.text == MIGRATED


def test_a_feedstock_already_saying_both_is_left_exactly_alone() -> None:
    """Not an error, and not a change either.

    `swage update --migrate` runs over feedstocks in whatever state they are
    in, and a rerun after a half-finished migration has to be a no-op rather
    than a second opinion.
    """
    edit = set_build_tools(MIGRATED, "example")

    assert edit.added == ()
    assert edit.text == MIGRATED


def test_the_five_that_set_only_the_build_tool_gain_the_other() -> None:
    """A real state in the fleet, not a hypothetical one."""
    partial = V0 + "conda_build_tool: rattler-build\n"

    edit = set_build_tools(partial, "example")

    assert edit.added == ("conda_install_tool",)
    assert edit.text == partial + "conda_install_tool: pixi\n"


def test_a_different_value_is_refused_rather_than_overwritten() -> None:
    """The line between "mandatory" and "swage's to decide".

    Setting these two during a migration is mandatory. Changing one somebody
    else set to something else is the ordinary `conda-forge.yml` case, which
    §7 keeps swage out of -- so it refuses and says which key.
    """
    disagrees = V0 + "conda_build_tool: conda-build\n"

    with pytest.raises(MigrationError) as raised:
        set_build_tools(disagrees, "example")

    message = str(raised.value)
    assert "already sets conda_build_tool to 'conda-build'" in message
    assert "swage does not overwrite this file's settings" in message


def test_a_file_without_a_trailing_newline_does_not_glue_the_key_on() -> None:
    """Two of the 159 checkouts end without one."""
    edit = set_build_tools("test: native_and_emulated", "example")

    assert edit.text.startswith("test: native_and_emulated\n")
    assert yaml.safe_load(edit.text)["conda_install_tool"] == "pixi"


def test_a_file_needing_nothing_is_returned_byte_identical() -> None:
    """Including the trailing newline it does not have.

    `apache-airflow-providers-amazon` sets both keys and ends without one.
    Normalizing that on the way through would be swage rewriting a file it
    had nothing to say about -- a one-byte diff on a feedstock it was not
    migrating, in the file §7 keeps it out of.
    """
    unterminated = MIGRATED.rstrip("\n")

    edit = set_build_tools(unterminated, "example")

    assert edit.added == ()
    assert edit.text == unterminated


def test_a_trailing_blank_line_is_kept_and_appended_after() -> None:
    """Three of the 159 end with one, and it is not swage's to tidy up."""
    padded = V0 + "\n"

    edit = set_build_tools(padded, "example")

    assert edit.text == padded + (
        "conda_build_tool: rattler-build\nconda_install_tool: pixi\n"
    )


def test_an_empty_file_becomes_the_two_settings() -> None:
    """`yaml.safe_load` reads an empty document as None rather than as {}."""
    edit = set_build_tools("", "example")

    assert edit.text == "conda_build_tool: rattler-build\nconda_install_tool: pixi\n"


def test_a_file_that_is_not_a_mapping_is_refused() -> None:
    with pytest.raises(MigrationError, match="not a mapping"):
        set_build_tools("- a\n- list\n", "example")
