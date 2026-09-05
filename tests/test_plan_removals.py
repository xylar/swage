"""Removal-classification tests (DESIGN.md 3.3.7, 3.3.8).

The asymmetry is the whole point and every test here is about it. Keeping a
line that should have gone leaves a stale recipe, which is visible and
recoverable. Dropping one that should have stayed destroys a maintainer
decision nobody wrote down, and is invisible until something fails to import.
So the default in every uncertain case is to keep.
"""

from __future__ import annotations

from typing import Any

import pytest

from swage.config import Layered, RecipeOwned, load_config
from swage.mapping import NameResolver, StaticPackageIndex
from swage.plan import (
    AttributionIndex,
    PythonMin,
    Removal,
    build_index,
    classify_removal,
    plan_section,
)
from swage.plan.lines import parse_line
from swage.recipe import read_recipe
from swage.upstream import UpstreamMetadata, parse_pyproject
from swage.upstream.cmake import parse_cmake

from .conftest import WriteTree

OWNED = RecipeOwned(functions=("pin_subpackage",), names=("python", "pip"))

NEW = parse_pyproject(
    '[project]\nname = "demo"\ndependencies = ["requests >=2", "attrs >=24"]\n'
    '[project.optional-dependencies]\nextra = ["rich >=13"]\n'
)
OLD = parse_pyproject(
    '[project]\nname = "demo"\n'
    'dependencies = ["requests >=2", "attrs >=24", "six >=1.16"]\n'
    '[project.optional-dependencies]\nextra = ["rich >=13"]\n'
)

NAMES = frozenset({"requests", "attrs", "six", "rich", "grpcio-gcp"})


def _resolver() -> NameResolver:
    return NameResolver(Layered(()), StaticPackageIndex(NAMES))


def _index(
    metadata: UpstreamMetadata = NEW, listed: tuple[str, ...] = ("extra",)
) -> AttributionIndex:
    return build_index(metadata, listed, _resolver())


def _classify(text: str, **kwargs: Any) -> Removal:
    return classify_removal(parse_line(text), _index(), OWNED, **kwargs)


# --- the one real removal --------------------------------------------------


def test_a_dependency_upstream_dropped_is_removed() -> None:
    """The mirror of an addition: same evidence, same confidence."""
    result = _classify("six >=1.16", previous=_index(OLD), version="2.0.0")
    assert result.fate == "upstream-dropped"
    assert result.removed
    assert result.dropped_in == "2.0.0"
    assert "six" in result.reason


# --- everything else is kept ----------------------------------------------


def test_a_never_upstream_dependency_is_kept() -> None:
    """Removing it would undo a decision that was never written down."""
    result = _classify("grpcio-gcp >=0.2.2", previous=_index(OLD))
    assert result.fate == "never-upstream"
    assert not result.removed
    assert "neither version" in result.reason


def test_an_unclassifiable_removal_is_kept() -> None:
    """A yanked release or deleted tag must not become a deletion."""
    result = _classify("six >=1.16", previous=None, previous_known=False)
    assert result.fate == "unclassified"
    assert not result.removed
    assert "does not delete on a guess" in result.reason


def test_an_unclassifiable_removal_is_kept_even_with_an_empty_index() -> None:
    """An empty index and an unfetchable one look alike; only the flag differs.

    Without `previous_known`, "the old metadata had nothing" would be
    indistinguishable from "the old metadata could not be read", and the first
    reading deletes every line in the recipe.
    """
    result = _classify("six >=1.16", previous=_index(NEW), previous_known=False)
    assert result.fate == "unclassified"
    assert not result.removed


def test_a_still_declared_dependency_is_kept() -> None:
    result = _classify("requests >=2", previous=_index(OLD))
    assert result.fate == "kept"
    assert not result.removed


def test_a_recipe_owned_line_is_never_a_removal() -> None:
    """Kept by definition, not by a decision the planner makes."""
    for text in ("python >=${{ python_min }}", "${{ pin_subpackage(name) }}", "pip"):
        result = classify_removal(
            parse_line(text), _index(), OWNED, previous=_index(OLD)
        )
        assert result.fate == "kept", text
        assert not result.removed


# --- declared, and gated on a python nothing is built for -------------------


OUT_OF_RANGE = {"tomli": "python <3.11"}


def test_a_dependency_gated_below_the_build_floor_is_removed() -> None:
    """`poetry` requires `tomli` under `python_version < "3.11"`.

    conda-forge raised the floor to 3.11, so upstream's condition is true on
    no python the package is installed on and nobody who installs it receives
    the requirement.
    """
    result = _classify(
        "tomli >=2.0.1,<3.0.0",
        out_of_range=OUT_OF_RANGE,
        built_for="python >=3.11",
    )
    assert result.fate == "out-of-range"
    assert result.removed
    assert result.reason == (
        "upstream declares 'tomli' only for python <3.11, and this recipe is "
        "built for python >=3.11, so no package it builds installs it"
    )


def test_the_reason_names_both_halves_of_the_finding() -> None:
    """Neither half alone is actionable, and the diff shows neither.

    A reviewer looking at the pull request sees a requirement disappear from a
    recipe whose upstream still declares it. What makes that right is the
    marker and the floor together, so both are in the sentence.
    """
    result = _classify(
        "tomli >=2.0.1,<3.0.0",
        out_of_range=OUT_OF_RANGE,
        built_for="python >=3.11",
    )
    assert "python <3.11" in result.reason
    assert "python >=3.11" in result.reason


def test_a_line_upstream_still_reaches_is_untouched() -> None:
    """The same recipe, a floor the marker still admits: nothing to do."""
    result = _classify("requests >=2", out_of_range=OUT_OF_RANGE)
    assert result.fate == "kept"
    assert not result.removed


def test_a_recipe_owned_line_is_not_removed_for_being_out_of_range() -> None:
    """Kept by definition comes first, as it does for every other fate."""
    result = classify_removal(
        parse_line("python >=${{ python_min }}"),
        _index(),
        OWNED,
        out_of_range={"python": "python <3.11"},
    )
    assert result.fate == "kept"
    assert not result.removed


@pytest.mark.parametrize("text", ["tomli>=2.0.1", "Tomli >=2.0.1", "  tomli  "])
def test_being_out_of_range_does_not_depend_on_how_the_line_is_written(
    text: str,
) -> None:
    assert _classify(text, out_of_range=OUT_OF_RANGE).fate == "out-of-range"


# --- what counts as "declared upstream" -----------------------------------


def test_a_dependency_in_a_listed_extra_is_still_declared() -> None:
    result = _classify("rich >=13", previous=_index(OLD))
    assert result.fate == "kept"


def test_a_dependency_that_moved_into_an_unlisted_extra_is_not_dropped() -> None:
    """Upstream still declares it; it just moved. Deleting would be wrong.

    The evidence for a removal is "upstream stopped asking for this", and a
    dependency that migrated between extras is not that.
    """
    moved = parse_pyproject(
        '[project]\nname = "demo"\ndependencies = ["requests >=2"]\n'
        '[project.optional-dependencies]\nother = ["attrs >=24"]\n'
    )
    result = classify_removal(
        parse_line("attrs >=24"),
        build_index(moved, (), _resolver()),
        OWNED,
        previous=_index(OLD),
    )
    assert result.fate == "kept"
    assert not result.removed


def test_only_a_dependency_gone_from_every_part_of_upstream_is_dropped() -> None:
    gone = parse_pyproject(
        '[project]\nname = "demo"\ndependencies = ["requests >=2"]\n'
    )
    result = classify_removal(
        parse_line("rich >=13"),
        build_index(gone, (), _resolver()),
        OWNED,
        previous=_index(OLD),
        version="2.0.0",
    )
    assert result.fate == "upstream-dropped"


@pytest.mark.parametrize("text", ["six>=1.16", "Six >=1.16", "  six   >=1.16  "])
def test_classification_does_not_depend_on_how_the_line_is_written(text: str) -> None:
    assert _classify(text, previous=_index(OLD)).fate == "upstream-dropped"


def test_a_retired_name_is_removed_once_upstream_disowns_it() -> None:
    """DESIGN.md 3.3.7: config saying what a line is, so swage may drop it."""
    removal = classify_removal(
        parse_line("google-api-core >=2.17.1,<3.0.0"),
        AttributionIndex(),
        OWNED,
        previous=AttributionIndex(),
        retire=frozenset({"google-api-core"}),
    )
    assert removal.fate == "retired"
    assert removal.removed
    assert "retire" in removal.reason


def test_a_retired_name_upstream_still_declares_is_kept() -> None:
    """The safety property, and it is structural rather than careful.

    `google-cloud-storage` declares plain `google-api-core` itself, beside
    `google-api-core[grpc]` in an extra. Its line never reaches the retire
    list, so listing the name cannot remove a dependency upstream wants -- only
    one upstream has never heard of.
    """
    removal = classify_removal(
        parse_line("google-api-core >=2.27.0,<3.0.0"),
        AttributionIndex(core={"google-api-core": None}),
        OWNED,
        retire=frozenset({"google-api-core"}),
    )
    assert removal.fate == "kept"
    assert not removal.removed


# --- a reader's declaration, diffed across two releases ---------------------
#
# DESIGN.md 3.6.6 said a reader "reads one release, so it cannot yet diff two"
# and that `previous_version` was "not wired up here". Both halves turn out to
# already work, because neither is reader-specific: `fetch_upstream` dispatches
# on config for whichever release it is handed, and `build_index` reads
# `build_requires` when the section is `host`. What was missing was a test, and
# the reason nobody noticed is that not one of the seven reader-backed
# feedstocks has ever had an open bot pull request -- and `audit` reads default
# branches, where there is no previous version by construction (DESIGN.md 8.1).

CMAKE_MAP = {"netcdf": "libnetcdf", "hdf5": "hdf5"}

READER_OLD = parse_cmake(
    "FIND_PACKAGE(netCDF REQUIRED)\nFIND_PACKAGE(HDF5 REQUIRED)\n",
    "",
    CMAKE_MAP,
    name="netcdf-cxx4",
    version="4.3.1",
)
READER_NEW = parse_cmake(
    "FIND_PACKAGE(netCDF REQUIRED)\n",
    "",
    CMAKE_MAP,
    name="netcdf-cxx4",
    version="4.4.0",
)


def _host_index(metadata: UpstreamMetadata) -> AttributionIndex:
    resolver = NameResolver(
        Layered(()), StaticPackageIndex(frozenset({"libnetcdf", "hdf5"}))
    )
    return build_index(metadata, (), resolver, section="host")


def test_a_declaration_a_reader_lost_between_releases_is_a_removal() -> None:
    """`host` is indexed from `build_requires`, which is all a reader produces."""
    result = classify_removal(
        parse_line("hdf5"),
        _host_index(READER_NEW),
        OWNED,
        previous=_host_index(READER_OLD),
        version="4.4.0",
    )
    assert result.fate == "upstream-dropped"
    assert result.removed
    assert result.dropped_in == "4.4.0"


def test_a_declaration_a_reader_still_makes_is_kept() -> None:
    result = classify_removal(
        parse_line("libnetcdf"),
        _host_index(READER_NEW),
        OWNED,
        previous=_host_index(READER_OLD),
        version="4.4.0",
    )
    assert not result.removed


def test_without_a_previous_release_a_reader_drops_nothing() -> None:
    """`audit` reads default branches, so this is the case it always hits.

    The safe direction, and the reason the gap went unnoticed for as long as
    it did: every reader-backed feedstock has been audited and none has been
    scanned, so no run has ever had a previous release to compare against.
    """
    result = classify_removal(
        parse_line("hdf5"),
        _host_index(READER_NEW),
        OWNED,
        previous=None,
        previous_known=False,
    )
    assert result.fate == "unclassified"
    assert not result.removed


# --- the whole path, from a raised build floor to a dropped line ------------
#
# `poetry`'s pull request #124 is the case: conda-forge moved `python_min` from
# 3.10 to 3.11, upstream declares `tomli` under `python_version < "3.11"`, and
# swage published a recipe with the requirement still in it and the note above
# it gone. Every piece was already right on its own -- the marker was correctly
# read as reaching no python, so nothing was planned for `tomli` and nothing
# was written above it -- and the line survived because the removal step asked
# whether upstream declared the package at all, which it does.

POETRY = parse_pyproject(
    """
[project]
name = "poetry"
version = "2.4.3"
dependencies = [
  "requests (>=2.26,<3.0)",
  "tomli (>=2.0.1,<3.0.0) ; python_version < '3.11'",
]
"""
)

POETRY_RECIPE = """\
requirements:
  run:
    - python
    - requests >=2.26,<3.0
    # tightest of upstream's floors (python <3.11)
    - tomli >=2.0.1,<3.0.0
"""


def _poetry_run(write_tree: WriteTree, floor: str) -> tuple[list[str], list[Removal]]:
    root = write_tree(
        {
            "defaults.yaml": "trust: never\nrecipe_owned:\n  names: [python, pip]\n",
            "feedstocks/poetry.yaml": "feedstock: poetry\n",
        }
    )
    config = load_config(root).for_feedstock("poetry")
    section = plan_section(
        read_recipe(POETRY_RECIPE).blocks["/requirements/run"],
        POETRY,
        config,
        NameResolver(config.name_map, StaticPackageIndex.of("requests", "tomli")),
        PythonMin(floor, "recipe"),
    )
    lines = [
        text
        for requirement in section.requirements
        for text in (*requirement.comments, requirement.text)
    ]
    return lines, [removal for removal in section.removals if removal.removed]


def test_a_floor_below_the_marker_keeps_the_line_and_its_note(
    write_tree: WriteTree,
) -> None:
    """3.10 is a python the condition is true on, so the requirement is real."""
    lines, dropped = _poetry_run(write_tree, "3.10")

    assert lines == [
        "python",
        "requests >=2.26,<3.0",
        "# tightest of upstream's floors (python <3.11)",
        "tomli >=2.0.1,<3.0.0",
    ]
    assert dropped == []


def test_a_floor_that_passes_the_marker_takes_the_line_with_the_note(
    write_tree: WriteTree,
) -> None:
    """The published defect: the note went and the requirement stayed.

    Dropping the note alone is the worst of the three outcomes. The recipe
    then states an unexplained bound on a package upstream asks for on no
    python it is installed on, and the next reader has nothing to trace it to.
    """
    lines, dropped = _poetry_run(write_tree, "3.11")

    assert lines == ["python", "requests >=2.26,<3.0"]
    assert [removal.fate for removal in dropped] == ["out-of-range"]
    assert dropped[0].text == "tomli >=2.0.1,<3.0.0"
