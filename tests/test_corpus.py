"""The corpus is only useful if it is complete and parseable.

These are cheap guards against the corpus rotting -- a triple that lost a file,
or a recipe that was truncated on the way in -- so that a later failure in the
recipe layer means what it says rather than pointing at a broken fixture.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from .conftest import REPO_ROOT

CORPUS = REPO_ROOT / "tests" / "corpus"
TRIPLES = sorted((CORPUS / "airflow-providers").iterdir())
RECIPES = sorted(CORPUS.rglob("*recipe.yaml"))


def test_the_corpus_is_not_empty() -> None:
    assert len(TRIPLES) >= 8
    assert len(RECIPES) >= 27


@pytest.mark.parametrize("triple", TRIPLES, ids=lambda p: p.name)
def test_every_triple_is_complete(triple: Path) -> None:
    """Input metadata, the recipe before, and the recipe after."""
    for name in ("pyproject.toml", "old_recipe.yaml", "recipe.yaml"):
        assert (triple / name).is_file(), f"{triple.name} is missing {name}"


@pytest.mark.parametrize("recipe", RECIPES, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_every_recipe_parses_and_declares_requirements(recipe: Path) -> None:
    data = yaml.safe_load(recipe.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    # v1 recipes: either a single set of requirements or one set per output.
    has_requirements = "requirements" in data or any(
        "requirements" in (output or {}) for output in data.get("outputs") or []
    )
    assert has_requirements, f"{recipe} declares no requirements"


@pytest.mark.parametrize("recipe", RECIPES, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_every_recipe_is_v1(recipe: Path) -> None:
    """swage targets the v1 recipe format; v0 conversion is phase 6."""
    text = recipe.read_text(encoding="utf-8")
    assert "${{" in text, f"{recipe} does not look like a v1 recipe"
