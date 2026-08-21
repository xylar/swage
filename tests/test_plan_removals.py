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

from swage.config import Layered, RecipeOwned
from swage.mapping import NameResolver, StaticPackageIndex
from swage.plan import AttributionIndex, Removal, build_index, classify_removal
from swage.plan.lines import parse_line
from swage.upstream import UpstreamMetadata, parse_pyproject
from swage.upstream.cmake import parse_cmake

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
