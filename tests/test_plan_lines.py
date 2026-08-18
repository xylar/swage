"""Line-ownership tests (DESIGN.md 3.3.6, 11).

Worth their own file because getting this wrong blocks every multi-output
feedstock at G2, which would look like a name-resolution problem rather than a
classification one.
"""

from __future__ import annotations

import pytest

from swage.config import RecipeOwned
from swage.plan.lines import parse_line, spec_key

OWNED = RecipeOwned(
    functions=("pin_subpackage", "pin_compatible", "compiler", "stdlib"),
    names=("python", "pip"),
)


def test_a_plain_dependency_splits_into_name_and_constraint() -> None:
    line = parse_line("pandas >=2.3.3")
    assert (line.name, line.constraint, line.function) == ("pandas", ">=2.3.3", None)
    assert not line.recipe_owned(OWNED)


def test_a_bare_name_has_no_constraint() -> None:
    line = parse_line("polars")
    assert (line.name, line.constraint) == ("polars", "")


def test_a_templated_constraint_does_not_make_the_line_structural() -> None:
    """The test is on the *name*, and 612 real lines take this shape."""
    line = parse_line("pandas >=${{ x }}")
    assert line.name == "pandas"
    assert line.constraint == ">=${{ x }}"
    assert line.function is None
    assert not line.recipe_owned(OWNED)


def test_a_pin_subpackage_line_is_recipe_owned() -> None:
    """Without this, G2 blocks every multi-output feedstock in the fleet."""
    line = parse_line("${{ pin_subpackage(name, exact=True) }}")
    assert line.name == "${{ pin_subpackage(name, exact=True) }}"
    assert line.constraint == ""
    assert line.function == "pin_subpackage"
    assert line.recipe_owned(OWNED)


def test_a_quoted_pin_subpackage_argument_survives() -> None:
    line = parse_line("${{ pin_subpackage('google-cloud-bigquery-core', exact=True) }}")
    assert line.function == "pin_subpackage"
    assert line.recipe_owned(OWNED)


@pytest.mark.parametrize(
    "text",
    ["${{ compiler('c') }}", "${{ stdlib('c') }}", "${{ pin_compatible('numpy') }}"],
)
def test_the_other_blessed_functions_are_recipe_owned(text: str) -> None:
    assert parse_line(text).recipe_owned(OWNED)


def test_python_and_pip_are_recipe_owned_by_name() -> None:
    """conda-forge conventions rather than anything upstream declares."""
    assert parse_line("python ${{ python_min }}.*").recipe_owned(OWNED)
    assert parse_line("python >=${{ python_min }}").recipe_owned(OWNED)
    assert parse_line("pip").recipe_owned(OWNED)


def test_the_python_line_keeps_its_symbolic_constraint() -> None:
    """swage never replaces `${{ python_min }}` with a literal (DESIGN.md 3.3.6)."""
    line = parse_line("python >=${{ python_min }}")
    assert line.name == "python"
    assert line.constraint == ">=${{ python_min }}"


def test_an_unrecognized_function_is_not_recipe_owned() -> None:
    """Preserved unchanged, but unexplained -- so G1 stops and quotes it."""
    line = parse_line("${{ cdt('mesa-libgl-devel') }}")
    assert line.function == "cdt"
    assert not line.recipe_owned(OWNED)


def test_blessing_a_function_is_a_config_change_only() -> None:
    extended = RecipeOwned(functions=(*OWNED.functions, "cdt"), names=OWNED.names)
    assert parse_line("${{ cdt('mesa-libgl-devel') }}").recipe_owned(extended)


def test_an_interpolated_name_stays_in_one_piece() -> None:
    """`${{ name }}-with-kerberos` is a name, not an expression plus a constraint.

    Splitting on whitespace would leave the name as `${{` and turn the rest
    into a constraint, which is how a sibling-output reference would get sent
    to the name resolver as garbage.
    """
    line = parse_line("${{ name }}-with-kerberos")
    assert line.name == "${{ name }}-with-kerberos"
    assert line.constraint == ""


def test_an_interpolated_name_is_not_recipe_owned() -> None:
    """`functions` cannot describe it and `names` is for literals.

    So it is preserved and reported rather than guessed at, which is the
    allowlist behaving as specified even though the answer is unsatisfying.
    """
    assert not parse_line("${{ name }}-with-kerberos").recipe_owned(OWNED)


def test_an_interpolated_name_with_a_constraint_splits_correctly() -> None:
    line = parse_line("${{ name }}-core ==${{ version }}")
    assert line.name == "${{ name }}-core"
    assert line.constraint == "==${{ version }}"


def test_surrounding_whitespace_is_ignored() -> None:
    assert parse_line("  pandas   >=2.3.3  ").name == "pandas"
    assert parse_line("  pandas   >=2.3.3  ").constraint == ">=2.3.3"


def test_the_original_text_is_kept_verbatim() -> None:
    """It is what gets preserved when swage declines to rewrite the line."""
    assert parse_line("  pandas >=2.3.3").text == "  pandas >=2.3.3"


def test_a_constraint_needs_no_space_before_it() -> None:
    """`pyyaml>=6.0.3` is rare but real -- 8 lines across the fleet.

    Splitting on whitespace alone makes the name `pyyaml>=6.0.3`, which
    attributes to nothing and stops the feedstock at G1 complaining about a
    package upstream plainly declares.
    """
    line = parse_line("pyyaml>=6.0.3")
    assert line.name == "pyyaml"
    assert line.constraint == ">=6.0.3"


@pytest.mark.parametrize(
    ("text", "name"),
    [
        ("pluggy>=1.5.0", "pluggy"),
        ("pkg<2", "pkg"),
        ("pkg!=1.5", "pkg"),
        ("pkg~=1.2", "pkg"),
        ("pkg==1.0", "pkg"),
    ],
)
def test_every_constraint_operator_ends_the_name(text: str, name: str) -> None:
    assert parse_line(text).name == name


def test_rendering_inserts_the_space_the_linter_wants() -> None:
    """conda-forge's linter asks for it, so a recipe swage touches comes out clean."""
    assert parse_line("pyyaml>=6.0.3").rendered == "pyyaml >=6.0.3"
    assert parse_line("pluggy>=1.5.0").rendered == "pluggy >=1.5.0"


def test_rendering_collapses_runs_of_spaces() -> None:
    assert parse_line("pandas    >=2.3.3").rendered == "pandas >=2.3.3"


@pytest.mark.parametrize(
    "text",
    [
        "pandas >=2.3.3",
        "polars",
        "python ${{ python_min }}.*",
        "python >=${{ python_min }}",
        "${{ pin_subpackage(name, exact=True) }}",
        "${{ compiler('c') }}",
        "${{ name }}-with-kerberos",
        "pandas >=${{ x }}",
    ],
)
def test_an_already_clean_line_renders_byte_identical(text: str) -> None:
    """Including lines swage does not own -- rendering must not disturb them."""
    assert parse_line(text).rendered == text


def test_rendering_is_idempotent() -> None:
    """`format(format(x)) == format(x)` (DESIGN.md 6)."""
    once = parse_line("pyyaml>=6.0.3").rendered
    assert parse_line(once).rendered == once


VIRTUAL = RecipeOwned(
    functions=OWNED.functions,
    names=(*OWNED.names, "__linux", "__osx", "__win", "__unix"),
)


def test_a_template_after_a_prefix_stays_in_one_piece() -> None:
    """`__${{ noarch_platform }}` is a prefix and then an expression.

    Reading only templates that *open* a line split this at its first space,
    so the name came out as the literal `__${{` -- three characters that are
    not a name -- and eleven conda-forge feedstocks were stopped over an
    `unrecognized template` naming them.
    """
    line = parse_line("__${{ noarch_platform }}")

    assert line.name == "__${{ noarch_platform }}"
    assert line.constraint == ""


def test_the_platform_variant_expands_to_the_four_selectors() -> None:
    """Substitution of one known variant's known values, not evaluation."""
    assert parse_line("__${{ noarch_platform }}").platform_expansions == (
        "__linux",
        "__osx",
        "__win",
        "__unix",
    )


def test_an_expanded_line_is_owned_when_every_expansion_is() -> None:
    """The interpolated form is `__win` and its siblings written once."""
    assert parse_line("__${{ noarch_platform }}").recipe_owned(VIRTUAL)


def test_an_expansion_nobody_blessed_is_still_unexplained() -> None:
    """Recognition stays an allowlist: expanding is not the same as accepting.

    A feedstock interpolating the same variant into an ordinary package name
    gets no provenance from it, which is what stops this becoming the fallback
    DESIGN.md 3.3.7 depends on it not being.
    """
    line = parse_line("some-package-${{ noarch_platform }}")

    assert line.platform_expansions == (
        "some-package-linux",
        "some-package-osx",
        "some-package-win",
        "some-package-unix",
    )
    assert not line.recipe_owned(VIRTUAL)


@pytest.mark.parametrize(
    "text",
    [
        "pandas >=${{ python_min }}",
        "pandas>=${{ python_min }}",
        "python ${{ python_min }}.*",
    ],
)
def test_a_templated_constraint_still_leaves_a_plain_name(text: str) -> None:
    """The 612-line majority, which this must not pull onto the other path."""
    assert parse_line(text).name in ("pandas", "python")
    assert parse_line(text).platform_expansions == ()


# --- the build string, which upstream has no way to declare -------------------


@pytest.mark.parametrize(
    ("text", "constraint", "build_string"),
    [
        ("hdf5 * nompi_*", "*", "nompi_*"),
        ("hdf5 * ${{ mpi_prefix }}_*", "*", "${{ mpi_prefix }}_*"),
        ("esmf >=8.8.0 nompi_*", ">=8.8.0", "nompi_*"),
        ("esmf ==${{ version }} nompi_*", "==${{ version }}", "nompi_*"),
        ("hdf5 * [build=${{ mpi_prefix }}_*]", "*", "[build=${{ mpi_prefix }}_*]"),
    ],
)
def test_a_match_spec_splits_its_third_field_off(
    text: str, constraint: str, build_string: str
) -> None:
    """18 of the 91 feedstocks on disk state a requirement this way."""
    line = parse_line(text)
    assert (line.constraint, line.build_string) == (constraint, build_string)
    assert line.rendered == text


@pytest.mark.parametrize(
    "text",
    [
        "python ${{ python_min }}.*",
        "pandas >=2.3.3",
        "python >=${{ python_min }}",
        "${{ compiler('c') }}",
        "${{ pin_subpackage(name, exact=True) }}",
    ],
)
def test_an_ordinary_line_has_no_build_string(text: str) -> None:
    """The spaces inside a template are not field boundaries.

    Read as two fields, `python ${{ python_min }}.*` would carry `.*` as a
    build string -- on the `host` section of every noarch recipe in the fleet.
    """
    assert parse_line(text).build_string == ""


def test_a_build_string_is_part_of_what_names_a_requirement() -> None:
    """`hdf5` and `hdf5 * nompi_*` are two requirements, not two spellings."""
    plain, pinned = parse_line("hdf5"), parse_line("hdf5 * nompi_*")
    assert plain.name == pinned.name
    assert spec_key(plain.name, plain.build_string) != spec_key(
        pinned.name, pinned.build_string
    )
