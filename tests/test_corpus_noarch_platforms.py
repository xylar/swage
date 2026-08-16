"""The fourth build model: one `noarch: python` package per platform.

DESIGN.md's table has three rows -- one noarch package, an architecture-specific
one with python, an architecture-specific one without. conda-smithy's
`noarch_platforms` makes a fourth: the package is `noarch: python`, and it is
built once per listed platform, each artifact carrying the virtual package that
says which one it is.

That shape reads as a contradiction to anything that assumes noarch means one
artifact -- a `noarch: python` recipe with `if: linux` conditions in `run` --
which is why these fixtures are here rather than being described in prose.

What they settle so far, and what they still catch, is in the tests below.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from swage.config import load_config
from swage.plan.lines import parse_line
from swage.recipe import read_recipe

REPO_ROOT = Path(__file__).resolve().parent.parent
CORPUS = REPO_ROOT / "tests" / "corpus" / "noarch-platforms"
ENTRIES = sorted(path.name for path in CORPUS.iterdir() if path.is_dir())

#: Every virtual package these recipes use to mark which platform's artifact
#: they are.
VIRTUAL = ("__linux", "__osx", "__win", "__unix")


def recipe_at(entry: str) -> str:
    return (CORPUS / entry / "recipe.yaml").read_text(encoding="utf-8")


def test_the_corpus_is_not_empty() -> None:
    assert ENTRIES == ["b4", "behave"]


@pytest.mark.parametrize("entry", ENTRIES)
def test_each_entry_is_a_noarch_python_package(entry: str) -> None:
    recipe = read_recipe(recipe_at(entry))
    assert [output.noarch for output in recipe.outputs] == ["python"]


def test_behave_is_built_once_per_platform_and_b4_is_not() -> None:
    """The two fixtures are different shapes, and `conda-forge.yml` is the tell.

    `behave` lists `noarch_platforms`, so conda-smithy builds its noarch
    package three times and each artifact carries the virtual package naming
    its platform. `b4` lists none: one artifact, and its bare `- __unix` says
    the package does not work on Windows at all.

    Both are virtual packages as structure, which is the point they share --
    but only the first is a build model, and reading `b4` as one would invent a
    matrix it does not have.
    """
    behave = yaml.safe_load(
        (CORPUS / "behave" / "conda-forge.yml").read_text(encoding="utf-8")
    )
    b4 = yaml.safe_load((CORPUS / "b4" / "conda-forge.yml").read_text(encoding="utf-8"))

    assert sorted(behave["noarch_platforms"]) == ["linux_64", "osx_64", "win_64"]
    assert "noarch_platforms" not in b4


@pytest.mark.parametrize("entry", ENTRIES)
def test_a_virtual_package_is_structure_rather_than_a_dependency(entry: str) -> None:
    """Nothing upstream declares `__linux`, and nothing ever will.

    Before `config/defaults.yaml` blessed them, `behave` reported three
    unexplained lines and offered `add_requirements` -- asking a maintainer to
    record a decision that was never theirs. The four names are structure in
    exactly the way `python` and `pip` are.
    """
    config = load_config(REPO_ROOT / "config").for_feedstock(entry)
    used = [name for name in VIRTUAL if f"- {name}" in recipe_at(entry)]
    assert used, f"{entry} carries no virtual package"

    for name in used:
        assert parse_line(name).recipe_owned(config.recipe_owned), name


def test_a_dependency_sharing_a_conditional_rides_along_unasked() -> None:
    """A known hole, recorded here because the fixture is what shows it.

    `behave` writes `if: win` over *two* names -- `__win`, which is structure,
    and `win_unicode_console`, which is a real dependency upstream declares
    only for python 3.9 and below. The conditional is preserved whole, so the
    second name is neither reconciled nor reported: on a package built for 3.10
    and up, upstream asks for it nowhere and swage says nothing.

    Blessing the virtual packages did not create this -- a preserved
    conditional was always kept verbatim -- but it did remove the accident that
    was reporting it, so the gap is now silent. Closing it means deciding what
    a conditional in a noarch recipe *is*, which is the same question this
    build model raises everywhere else.
    """
    recipe = read_recipe(recipe_at("behave"))
    conditionals = recipe.outputs[0].blocks["run"].content.conditionals
    windows = [
        entry
        for entry in conditionals
        if any("win_unicode_console" in str(item) for item in entry.then)
    ]

    assert windows, "the fixture no longer carries the shape"
    assert any("__win" in str(item) for item in windows[0].then)
