"""The esmf reader, against ESMF 8.9.1's real files.

A golden comparison rather than a set of assertions about a fixture somebody
wrote: `tests/corpus/compiled/esmf/` holds `build/common.mk` out of the 8.9.1
tarball and `recipe/build.sh` off the feedstock's default branch, unedited. The
thing most likely to break this reader is ESMF restructuring its makefile, and
only the real file can catch that.
"""

from __future__ import annotations

import pathlib

import pytest
import yaml

from swage.upstream import UpstreamError
from swage.upstream.esmf import (
    esmf_toggles,
    parse_common_mk,
    parse_esmf,
    pio_version,
)

CORPUS = pathlib.Path(__file__).parent / "corpus" / "compiled" / "esmf"
COMMON_MK = (CORPUS / "common.mk").read_text()
BUILD_SH = (CORPUS / "build.sh").read_text()
CONFIGURE_AC = (CORPUS / "configure.ac").read_text()

LINK_MAP = yaml.safe_load(
    (pathlib.Path(__file__).parents[1] / "config" / "link-map.yaml").read_text()
)


# --- reading the makefile --------------------------------------------------


def test_every_toggle_that_names_libraries_is_found() -> None:
    """Eleven, and the reader must not silently start finding ten."""
    assert sorted(parse_common_mk(COMMON_MK)) == [
        "BABELTRACE",
        "LAPACK",
        "MOAB",
        "NETCDF",
        "NUMA",
        "NVML",
        "PIO",
        "PNETCDF",
        "PROJ4",
        "XERCES",
        "YAMLCPP",
    ]


def test_a_toggle_with_several_values_keeps_them_apart() -> None:
    assert parse_common_mk(COMMON_MK)["NETCDF"] == {
        "standard": "-lnetcdf",
        "split": "-lnetcdff -lnetcdf",
    }


def test_a_toggle_guarded_only_by_ifdef_is_keyed_on_the_empty_value() -> None:
    """`ESMF_PIO` has no per-value block: being set at all is the condition."""
    assert parse_common_mk(COMMON_MK)["PIO"] == {"": "-lpioc"}


def test_an_assignment_that_runs_a_program_is_not_read() -> None:
    """The `nc-config` path builds its list by running one.

    swage will not execute upstream code to find out what a build would link,
    so those assignments are skipped -- which is visible here as `NETCDF`
    carrying only the two literal blocks above.
    """
    netcdf = parse_common_mk(COMMON_MK)["NETCDF"]
    assert not any("$(" in libs for libs in netcdf.values())


def test_a_makefile_declaring_nothing_is_refused() -> None:
    """A shape change is a stop, not an empty dependency list."""
    with pytest.raises(UpstreamError) as raised:
        parse_common_mk("ESMF_CPPFLAGS += -DESMF_NETCDF=1\n")

    assert "declares no libraries" in str(raised.value)


# --- reading the feedstock's build script ----------------------------------


def test_the_toggles_the_feedstock_sets_are_read() -> None:
    toggles = esmf_toggles(BUILD_SH)

    assert toggles["NETCDF"] == "split"
    assert toggles["PIO"] == "external"


def test_a_toggle_set_from_another_variable_is_not_read() -> None:
    """`export ESMF_F90=${FC}` says nothing this file can answer."""
    assert "F90LINKOPTS" not in esmf_toggles(BUILD_SH)


def test_a_toggle_set_in_a_branch_is_still_read() -> None:
    """`ESMF_PIO` is set inside `if [[ "$mpi" != "nompi" ]]`.

    Which branch runs is a fact about the build variant, and swage has no
    variant axis -- so the toggles are the set the feedstock can turn on, and
    the condition on the resulting recipe line is the recipe's own.
    """
    assert esmf_toggles(BUILD_SH)["PIO"] == "external"


# --- the two files together ------------------------------------------------


def test_esmf_891_needs_exactly_these_three_packages() -> None:
    metadata = parse_esmf(COMMON_MK, BUILD_SH, LINK_MAP, version="8.9.1")

    assert [item.name for item in metadata.build_requires or ()] == [
        "netcdf-fortran",
        "libnetcdf",
        "parallelio",
    ]


def test_each_requirement_says_where_upstream_declared_it() -> None:
    """The whole point of the reader: not having to find these files again."""
    metadata = parse_esmf(COMMON_MK, BUILD_SH, LINK_MAP, version="8.9.1")

    raw = {item.name: item.raw for item in metadata.build_requires or ()}
    assert raw["netcdf-fortran"] == (
        "-lnetcdff in build/common.mk, for ESMF_NETCDF=split in recipe/build.sh"
    )
    assert raw["parallelio"] == (
        "-lpioc in build/common.mk, for ESMF_PIO=external in recipe/build.sh"
    )


def test_the_reader_says_it_has_no_versions_to_offer() -> None:
    """A linker flag names a library and cannot bound it.

    The flag is what keeps a recipe's own bound from reading as drift against
    that silence -- see `tests/test_plan_unversioned_reader.py`.
    """
    metadata = parse_esmf(COMMON_MK, BUILD_SH, LINK_MAP, version="8.9.1")

    assert metadata.states_versions is False
    assert not [
        requirement
        for requirement in metadata.build_requires or ()
        if requirement.specifier
    ]


def test_a_makefile_declares_what_a_build_links_and_nothing_to_run_with() -> None:
    """`run` is conda-forge's own: run exports, plus build-string variant pins."""
    metadata = parse_esmf(COMMON_MK, BUILD_SH, LINK_MAP, version="8.9.1")

    assert metadata.dependencies == ()


def test_hdf5_is_not_invented_because_the_recipe_has_it() -> None:
    """It appears zero times in `common.mk`; it arrives through netCDF.

    A reader that explained it would be doing the thing G1 exists to prevent.
    """
    metadata = parse_esmf(COMMON_MK, BUILD_SH, LINK_MAP, version="8.9.1")

    assert "hdf5" not in [item.name for item in metadata.build_requires or ()]


def test_a_library_with_no_entry_in_the_link_map_stops_the_feedstock() -> None:
    """An allowlist, so an unknown library is a stop rather than a guess."""
    with pytest.raises(UpstreamError) as raised:
        parse_esmf(COMMON_MK, BUILD_SH, {"libnetcdff": "netcdf-fortran"})

    assert "-lnetcdf" in str(raised.value)
    assert "link-map.yaml" in str(raised.value)


# --- the vendored ParallelIO ------------------------------------------------


def test_the_vendored_parallelio_version_is_read() -> None:
    assert pio_version(CONFIGURE_AC) == "2.6.6"


def test_the_vendored_version_is_reported_and_never_becomes_a_bound() -> None:
    """conda-forge's `parallelio` pin has tracked it without ever equalling it.

    2.6.3 against a vendored 2.6.2, then 2.6.6 against 2.6.6, then 2.6.9
    against 2.6.6 -- so no reader will produce that pin, and the useful thing
    to do with the vendored version is say it at a version bump.
    """
    metadata = parse_esmf(
        COMMON_MK, BUILD_SH, LINK_MAP, version="8.9.1", configure_ac=CONFIGURE_AC
    )

    assert metadata.notes == (
        "ESMF 8.9.1 builds against ParallelIO 2.6.6 "
        "(src/Infrastructure/IO/PIO/ParallelIO/configure.ac); the recipe pins "
        "`parallelio` itself, so check the pin when this moves",
    )
    parallelio = next(
        item for item in metadata.build_requires or () if item.name == "parallelio"
    )
    assert parallelio.specifier == ""


def test_an_archive_without_the_vendored_copy_says_nothing_about_it() -> None:
    metadata = parse_esmf(COMMON_MK, BUILD_SH, LINK_MAP, version="8.9.1")

    assert metadata.notes == ()


def test_the_reader_names_both_files_it_joined() -> None:
    """`common.mk` says what a toggle links; `build.sh` says which are on."""
    metadata = parse_esmf(COMMON_MK, BUILD_SH, LINK_MAP, version="8.9.1")
    assert metadata.declared_in == "build/common.mk + recipe/build.sh"
