"""The CMake reader, against PROJ 9.8.1's real files.

A golden comparison rather than assertions about a fixture somebody wrote:
`tests/corpus/compiled/proj/` holds the top-level `CMakeLists.txt` out of the
9.8.1 tarball and `recipe/build.sh` off the feedstock's default branch,
unedited. What is most likely to break this reader is a project restructuring
that file, and only the real one catches it.

PROJ earns the place because it needs every rule at once: an unguarded
`REQUIRED`, two guarded by an `option(... ON)`, a `REQUIRED` ruled out by a
cache default, and a bare `find_package` that is not a dependency at all.
"""

from __future__ import annotations

import pathlib

import pytest
import yaml

from swage.upstream import UpstreamError
from swage.upstream.cmake import (
    cmake_definitions,
    find_packages,
    parse_cmake,
)

CORPUS = pathlib.Path(__file__).parent / "corpus" / "compiled" / "proj"
CMAKE_LISTS = (CORPUS / "CMakeLists.txt").read_text()
BUILD_SH = (CORPUS / "build.sh").read_text()

CMAKE_MAP = {
    name.lower(): package
    for name, package in yaml.safe_load(
        (pathlib.Path(__file__).parents[1] / "config" / "cmake-map.yaml").read_text()
    ).items()
}


# --- reading the file ------------------------------------------------------


def test_every_package_the_file_names_is_found() -> None:
    """Five, and the reader must not silently start finding four."""
    found = find_packages(CMAKE_LISTS, cmake_definitions(BUILD_SH))
    assert [package.name for package in found] == [
        "nlohmann_json",
        "SQLite3",
        "TIFF",
        "CURL",
        "Threads",
    ]


def test_order_is_the_file_s_own() -> None:
    """DESIGN.md 6 orders upstream's requirements by upstream's declaration.

    `nlohmann_json` comes first in the file and stays first here even though
    it is the one call PROJ makes twice.
    """
    found = find_packages(CMAKE_LISTS, cmake_definitions(BUILD_SH))
    assert [package.line for package in found] == sorted(
        package.line for package in found
    )


def test_a_cache_default_rules_out_the_required_call() -> None:
    """PROJ's `NLOHMANN_JSON_ORIGIN` defaults to `auto`, not to `external`.

    So the `find_package(nlohmann_json REQUIRED)` in the `external` branch is
    not part of this build, and what is left is the `QUIET` call in the
    `else()`. Reading either half alone gets this backwards.
    """
    found = {
        package.name: package
        for package in find_packages(CMAKE_LISTS, cmake_definitions(BUILD_SH))
    }
    assert found["nlohmann_json"].required is False


def test_an_option_that_defaults_on_keeps_its_package() -> None:
    """`option(ENABLE_TIFF ... ON)` guards `find_package(TIFF REQUIRED)`."""
    found = {
        package.name: package
        for package in find_packages(CMAKE_LISTS, cmake_definitions(BUILD_SH))
    }
    assert found["TIFF"].required is True
    assert found["CURL"].required is True


def test_an_option_turned_off_by_the_build_script_takes_its_package_away() -> None:
    """The join the reader exists for, checked by mutating the script.

    The recipe passing `-DENABLE_TIFF=OFF` is what makes upstream's `REQUIRED`
    not apply, and nothing in `CMakeLists.txt` alone can say so.
    """
    off = cmake_definitions(BUILD_SH + "\ncmake -D ENABLE_TIFF=OFF ${SRC_DIR}\n")
    assert off["ENABLE_TIFF"] == "OFF"
    found = [package.name for package in find_packages(CMAKE_LISTS, off)]
    assert "TIFF" not in found
    assert "CURL" in found


def test_a_flag_the_build_script_passes_can_add_a_dependency_too() -> None:
    """The join in the other direction, from `azure-uamqp-c`.

    Left alone the project builds its dependencies out of vendored submodules
    and needs no packages at all; conda-forge passes
    `-D use_installed_dependencies=ON` and three appear. Reading either file
    without the other gives a confident wrong answer both ways round.
    """
    text = """
        option(use_installed_dependencies "..." OFF)
        if(NOT ${use_installed_dependencies})
            add_subdirectory(deps/umock-c)
        else()
            if (NOT umock_cFOUND)
                find_package(umock_c REQUIRED CONFIG)
            endif ()
        endif()
    """
    assert find_packages(text, cmake_definitions("")) == []
    installed = cmake_definitions("cmake -D use_installed_dependencies=ON ..")
    assert [
        (package.name, package.required) for package in find_packages(text, installed)
    ] == [("umock_c", True)]


def test_a_guard_swage_cannot_read_leaves_the_declaration_standing() -> None:
    """The rule that comes out the opposite way round from the esmf reader.

    `libgeotiff` asks for TIFF in config mode and again, `REQUIRED`, under
    `if (NOT TIFF_FOUND)` -- a variable the call above it sets. Nothing
    outside CMake can evaluate that, and libgeotiff plainly requires libtiff.
    """
    text = """
        find_package(TIFF NO_MODULE QUIET)
        if (NOT TIFF_FOUND)
          find_package(TIFF REQUIRED)
        endif ()
    """
    found = find_packages(text)
    assert [(package.name, package.required) for package in found] == [("TIFF", True)]


def test_a_comment_naming_an_if_is_not_an_if() -> None:
    """The defect that hid every default in the file below it.

    A scanner reading comments as commands leaves an `if` open that never
    closes, so `option(...)` calls after it look like ones set inside a
    conditional and the reader stops believing any of them.
    """
    text = """
        # Only used when
        #   if(SOMETHING)
        option(ENABLE_TIFF "..." OFF)
        if(ENABLE_TIFF)
          find_package(TIFF REQUIRED)
        endif()
    """
    assert find_packages(text) == []


def test_a_hash_inside_a_string_is_not_a_comment() -> None:
    text = 'set(DOC "a # b" CACHE STRING "")\nfind_package(TIFF REQUIRED)\n'
    assert [package.name for package in find_packages(text)] == ["TIFF"]


def test_a_version_becomes_a_minimum() -> None:
    """CMake reads `find_package(Foo 3.8)` as at least 3.8 and compatible."""
    found = find_packages("find_package(Foo 3.8 REQUIRED)")
    assert (found[0].name, found[0].version) == ("Foo", "3.8")


def test_a_component_list_is_not_mistaken_for_a_version() -> None:
    found = find_packages("find_package(HDF5 COMPONENTS C HL REQUIRED)")
    assert (found[0].name, found[0].version, found[0].required) == ("HDF5", "", True)


# --- the build script ------------------------------------------------------


def test_the_flags_the_build_script_passes_are_read() -> None:
    assert cmake_definitions(BUILD_SH) == {
        "CMAKE_BUILD_TYPE": "Release",
        "BUILD_SHARED_LIBS": "ON",
        "CMAKE_INSTALL_LIBDIR": "lib",
    }


def test_a_flag_whose_value_is_a_shell_variable_is_dropped() -> None:
    """`-D BUILD_TESTING=${BUILD_TESTING}` is decided by a shell `if`.

    PROJ's script writes exactly that, and swage will not run the shell to
    find out which branch took it.
    """
    assert "BUILD_TESTING" not in cmake_definitions(BUILD_SH)


def test_a_flag_inside_a_shell_string_is_still_a_flag() -> None:
    """`netcdf-fortran` builds its flags up and passes them later.

    Finding the flag is not the same as running the script that would pass it,
    and the flag is the thing the reader needs.
    """
    script = 'export PARALLEL="-DENABLE_PARALLEL4=ON -DENABLE_PARALLEL_TESTS=ON"\n'
    assert cmake_definitions(script) == {
        "ENABLE_PARALLEL4": "ON",
        "ENABLE_PARALLEL_TESTS": "ON",
    }


# --- the whole reader ------------------------------------------------------


def test_proj_declares_the_three_packages_its_recipe_has_in_host() -> None:
    """The whole claim of this reader, against a real recipe.

    `proj.4`'s `host` is `sqlite`, `libtiff`, `libcurl`, in that order.
    `libsqlite` is the package publishing the library `FindSQLite3` looks
    for; the recipe's own `name_map` says why it takes `sqlite` instead.
    """
    metadata = parse_cmake(CMAKE_LISTS, BUILD_SH, CMAKE_MAP, name="proj.4")
    assert [requirement.name for requirement in metadata.build_requires or ()] == [
        "libsqlite",
        "libtiff",
        "libcurl",
    ]


def test_nothing_is_declared_as_a_run_dependency() -> None:
    """A build system states what a project links, which is about building it.

    What ends up in a conda-forge `run` section for a compiled library is run
    exports plus build-string variant pins, both conda-forge's own reasons for
    a line (DESIGN.md 3.6.6).
    """
    metadata = parse_cmake(CMAKE_LISTS, BUILD_SH, CMAKE_MAP, name="proj.4")
    assert metadata.dependencies == ()


def test_each_requirement_says_where_upstream_states_it() -> None:
    metadata = parse_cmake(CMAKE_LISTS, BUILD_SH, CMAKE_MAP, name="proj.4")
    assert [requirement.raw for requirement in metadata.build_requires or ()] == [
        "find_package(SQLite3 REQUIRED) in CMakeLists.txt",
        "find_package(TIFF REQUIRED) in CMakeLists.txt",
        "find_package(CURL REQUIRED) in CMakeLists.txt",
    ]


def test_an_optional_package_is_reported_and_never_proposed() -> None:
    metadata = parse_cmake(
        CMAKE_LISTS, BUILD_SH, CMAKE_MAP, name="proj.4", version="9.8.1"
    )
    assert "nlohmann_json" not in [
        requirement.name for requirement in metadata.build_requires or ()
    ]
    assert metadata.notes == (
        "proj.4 9.8.1 can optionally use nlohmann_json "
        "(find_package(nlohmann_json)), declared in CMakeLists.txt without "
        "REQUIRED; the recipe decides whether conda-forge builds against them",
    )


def test_a_name_that_is_not_a_package_is_neither_proposed_nor_reported() -> None:
    """`Threads` is CMake asking about the compiler, and the map says so."""
    metadata = parse_cmake(CMAKE_LISTS, BUILD_SH, CMAKE_MAP, name="proj.4")
    assert "Threads" not in "".join(metadata.notes)


def test_a_name_nobody_has_mapped_stops_the_feedstock() -> None:
    without = {key: value for key, value in CMAKE_MAP.items() if key != "tiff"}
    with pytest.raises(UpstreamError) as caught:
        parse_cmake(CMAKE_LISTS, BUILD_SH, without, name="proj.4")
    assert "find_package(TIFF REQUIRED)" in str(caught.value)
    assert "config/cmake-map.yaml" in str(caught.value)


def test_the_map_is_looked_up_without_regard_to_case() -> None:
    """`netCDF`, `NetCDF` and `NETCDF` are three projects' spelling of one."""
    for spelling in ("netCDF", "NetCDF", "NETCDF"):
        metadata = parse_cmake(
            f"find_package({spelling} REQUIRED)", "", CMAKE_MAP, name="x"
        )
        assert [requirement.name for requirement in metadata.build_requires or ()] == [
            "libnetcdf"
        ]


def test_a_file_declaring_nothing_stops_rather_than_planning_an_empty_host() -> None:
    """`netcdf-c`'s 1,995-line `CMakeLists.txt` has no `find_package` at all.

    It finds its libraries with `find_library` and `check_include_file`, so
    reading it as an empty declaration would report every line of a real
    recipe as coming from nowhere.
    """
    with pytest.raises(UpstreamError) as caught:
        parse_cmake("project(netcdf C)\n", "", CMAKE_MAP, name="libnetcdf")
    assert "declares no packages" in str(caught.value)


# --- answering the optional declarations -----------------------------------
#
# `netcdf-cxx4` is why the keys exist and is vendored for it: it writes
# `FIND_PACKAGE(netCDF QUIET)` and falls back to a `FIND_LIBRARY` with a
# `FATAL_ERROR` behind it, so upstream requires netCDF and never says
# `REQUIRED`. PROJ carries the other half -- an optional package its recipe is
# right not to have -- so both answers get a real file.

NETCDF_CXX4 = pathlib.Path(__file__).parent / "corpus" / "compiled" / "netcdf-cxx4"
NCXX4_CMAKE_LISTS = (NETCDF_CXX4 / "CMakeLists.txt").read_text()
NCXX4_BUILD_SH = (NETCDF_CXX4 / "build.sh").read_text()


def test_netcdf_cxx4_leaves_libnetcdf_unexplained_without_an_answer() -> None:
    """The gap the keys close, pinned so it cannot come back silently."""
    metadata = parse_cmake(
        NCXX4_CMAKE_LISTS, NCXX4_BUILD_SH, CMAKE_MAP, name="netcdf-cxx4"
    )
    assert [requirement.name for requirement in metadata.build_requires or ()] == [
        "hdf5"
    ]
    assert "libnetcdf" in "".join(metadata.notes)


def test_a_supported_optional_becomes_a_requirement() -> None:
    metadata = parse_cmake(
        NCXX4_CMAKE_LISTS,
        NCXX4_BUILD_SH,
        CMAKE_MAP,
        name="netcdf-cxx4",
        supported=("netCDF",),
    )
    assert [requirement.name for requirement in metadata.build_requires or ()] == [
        "libnetcdf",
        "hdf5",
    ]
    assert metadata.notes == ()


def test_a_supported_optional_does_not_claim_upstream_wrote_required() -> None:
    """The evidence quoted back is the evidence in the file.

    A maintainer who opens `CMakeLists.txt` on the strength of this line finds
    `FIND_PACKAGE(netCDF QUIET)` there, so the line has to say so.
    """
    metadata = parse_cmake(
        NCXX4_CMAKE_LISTS,
        NCXX4_BUILD_SH,
        CMAKE_MAP,
        name="netcdf-cxx4",
        supported=("netCDF",),
    )
    libnetcdf = next(
        requirement
        for requirement in metadata.build_requires or ()
        if requirement.name == "libnetcdf"
    )
    assert "REQUIRED" not in libnetcdf.raw
    assert libnetcdf.raw == (
        "find_package(netCDF) in CMakeLists.txt, which this feedstock's "
        "config lists as supported"
    )


def test_a_skipped_optional_is_neither_proposed_nor_reported() -> None:
    """`skip` is how "considered and declined" gets on the record."""
    metadata = parse_cmake(
        CMAKE_LISTS,
        BUILD_SH,
        CMAKE_MAP,
        name="proj.4",
        version="9.8.1",
        skip=("nlohmann_json",),
    )
    assert "nlohmann_json" not in [
        requirement.name for requirement in metadata.build_requires or ()
    ]
    assert metadata.notes == ()


def test_an_answer_is_matched_without_regard_to_case() -> None:
    """`netcdf-fortran` writes `netCDF` and `cprnc` writes `NetCDF`."""
    for spelling in ("netCDF", "NetCDF", "NETCDF"):
        metadata = parse_cmake(
            NCXX4_CMAKE_LISTS,
            NCXX4_BUILD_SH,
            CMAKE_MAP,
            name="netcdf-cxx4",
            supported=(spelling,),
        )
        assert "libnetcdf" in [
            requirement.name for requirement in metadata.build_requires or ()
        ]


def test_an_answer_this_release_has_nothing_to_answer_is_reported() -> None:
    """Upstream can drop a `find_package`, and nothing else would look."""
    metadata = parse_cmake(
        CMAKE_LISTS,
        BUILD_SH,
        CMAKE_MAP,
        name="proj.4",
        version="9.8.1",
        skip=("nlohmann_json", "Boost"),
    )
    assert metadata.notes == (
        "config answers Boost for this feedstock, and proj.4 9.8.1 declares "
        "no optional find_package of that name; drop the entry, or check "
        "whether upstream now requires it",
    )


def test_answering_a_package_upstream_already_requires_is_reported() -> None:
    """A `REQUIRED` call needs no answer, so an entry naming one is stale.

    Upstream promoting an optional declaration is the case that matters: the
    config still says the feedstock decided, and the decision no longer exists.
    """
    metadata = parse_cmake(
        CMAKE_LISTS, BUILD_SH, CMAKE_MAP, name="proj.4", supported=("SQLite3",)
    )
    assert "SQLite3" in "".join(metadata.notes)
    assert [requirement.name for requirement in metadata.build_requires or ()] == [
        "libsqlite",
        "libtiff",
        "libcurl",
    ]


def test_an_answer_does_not_reach_a_declaration_a_guard_ruled_out() -> None:
    """`ENABLE_DOXYGEN=OFF` in the build script takes the call away entirely.

    The join decides what is declared before the answer decides what is taken,
    so `supported` cannot resurrect a call this build does not make.
    """
    cmake_lists = (
        "find_package(SQLite3 REQUIRED)\n"
        'option(ENABLE_TIFF "" OFF)\n'
        "if(ENABLE_TIFF)\n"
        "  find_package(TIFF)\n"
        "endif()\n"
    )
    metadata = parse_cmake(cmake_lists, "", CMAKE_MAP, name="x", supported=("TIFF",))
    assert [requirement.name for requirement in metadata.build_requires or ()] == [
        "libsqlite"
    ]
    assert "config answers TIFF" in "".join(metadata.notes)


def test_the_reader_names_both_files_it_joined() -> None:
    """Neither is the declaration alone, so neither answers on its own.

    `azure-uamqp-c` is where that is starkest: left alone its `CMakeLists.txt`
    declares nothing at all, and the feedstock's `-D use_installed_dependencies=ON`
    is what turns three `find_package` calls into the declaration.
    """
    metadata = parse_cmake(CMAKE_LISTS, BUILD_SH, CMAKE_MAP, name="proj.4")
    assert metadata.declared_in == "CMakeLists.txt + recipe/build.sh"
