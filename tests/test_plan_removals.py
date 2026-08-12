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
