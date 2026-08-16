"""The fourth build model: one `noarch: python` package per platform.

DESIGN.md's table has three rows -- one noarch package, an architecture-specific
one with python, an architecture-specific one without. conda-smithy's
`noarch_platforms` makes a fourth: the package is `noarch: python`, and it is
built once per listed platform, each artifact carrying the virtual package that
says which one it is.

That shape reads as a contradiction to anything that assumes noarch means one
artifact -- a `noarch: python` recipe with `if: win` conditions in `run` --
which is why these fixtures are here rather than being described in prose.

Both entries are feedstocks the maintainer maintains, which is the only reason
either is here: a shape no feedstock in the fleet has is a shape swage does not
have to read. Every one of the fleet's 487 that names a virtual package at all
also sets `noarch_platforms`, so the model and the marker arrive together and
there is no second reason to tell apart.

What the pair does carry is two *spellings* of the one model, and swage does
not read them equally well. What they settle, and what they still catch, is in
the tests below.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

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

#: `click` writes the platform into the dependency name rather than into a
#: condition, so this is the whole of what it says about its artifact.
TEMPLATED = "__${{ noarch_platform }}"


def recipe_at(entry: str) -> str:
    return (CORPUS / entry / "recipe.yaml").read_text(encoding="utf-8")


def forge_config(entry: str) -> dict[str, Any]:
    text = (CORPUS / entry / "conda-forge.yml").read_text(encoding="utf-8")
    loaded: dict[str, Any] = yaml.safe_load(text)
    return loaded


def run_block(entry: str) -> Any:
    return read_recipe(recipe_at(entry)).outputs[0].blocks["run"].content


def test_the_corpus_is_not_empty() -> None:
    assert ENTRIES == ["click", "colorlog"]


@pytest.mark.parametrize("entry", ENTRIES)
def test_each_entry_is_a_noarch_python_package(entry: str) -> None:
    recipe = read_recipe(recipe_at(entry))
    assert [output.noarch for output in recipe.outputs] == ["python"]


@pytest.mark.parametrize("entry", ENTRIES)
def test_each_entry_is_built_once_per_platform(entry: str) -> None:
    """`conda-forge.yml` is the only place the build model is written down.

    Nothing in the recipe says the package is built more than once. A reader
    with the recipe alone sees `noarch: python` and concludes one artifact,
    which is why the fixture vendors both files.
    """
    assert forge_config(entry)["noarch_platforms"]


def test_the_two_spell_the_platform_differently() -> None:
    """One model, two spellings, and only one of them is a condition.

    `colorlog` writes `if: win` / `then: __win`, which swage parses into a
    conditional it can reason about. `click` writes the platform into the
    dependency *name*, so the same fact arrives as an unconditional line
    whose text is a template.

    Neither is wrong and both are common. The pair is here so that reading
    the fleet for this build model cannot be reduced to looking for `if:`.
    """
    colorlog = run_block("colorlog")
    conditioned = {item.text for entry in colorlog.conditionals for item in entry.then}
    assert {"__unix", "__win"} <= conditioned

    click = run_block("click")
    assert not click.conditionals
    assert any(TEMPLATED in requirement.text for requirement in click.requirements)


def test_a_virtual_package_is_structure_rather_than_a_dependency() -> None:
    """Nothing upstream declares `__win`, and nothing ever will.

    Before `config/defaults.yaml` blessed them, a recipe like this reported
    its virtual packages as unexplained lines and offered `add_requirements`
    -- asking a maintainer to record a decision that was never theirs. The
    four names are structure in exactly the way `python` and `pip` are.
    """
    config = load_config(REPO_ROOT / "config").for_feedstock("colorlog")
    used = [name for name in VIRTUAL if name in recipe_at("colorlog")]
    assert used, "colorlog carries no virtual package"

    for name in used:
        assert parse_line(name).recipe_owned(config.recipe_owned), name


def test_a_templated_virtual_package_is_not_recognised_as_structure() -> None:
    """A known hole, recorded here because the fixture is what shows it.

    `config/defaults.yaml` blesses the four literal names. `click` never
    writes one, and the blessing does not reach through the template. That
    line is structure by every argument that applies to `__win`, and swage
    would treat it as a dependency it cannot explain.

    Nothing reports this today because `click` stops earlier, on the
    platform-conditional constraint below. Closing that refusal is what makes
    this one reachable, so the two belong to the same piece of work.
    """
    config = load_config(REPO_ROOT / "config").for_feedstock("click")
    assert TEMPLATED in recipe_at("click")

    assert not parse_line(TEMPLATED).recipe_owned(config.recipe_owned)


def test_a_real_dependency_rides_the_same_platform_condition() -> None:
    """The virtual package is not what the arrangement exists to deliver.

    `colorlog` writes `if: win` twice: over `__win`, which is structure, and
    over `colorama`, which is a real dependency upstream declares as
    `colorama; sys_platform == "win32"`. `click` says the same thing in one
    templated line that resolves to a *different package* per platform.

    swage refuses both feedstocks today -- `platform-conditional constraint
    for 'colorama'` -- on the grounds that the marker turns on a variable
    which "does not vary across the Pythons one noarch package is installed
    on". Under this build model it does vary, once per artifact, and each
    recipe already writes the answer swage declines to write. Three of the
    fleet's feedstocks are held by that refusal: `click`, `colorlog` and
    `poetry`.
    """
    windows = [
        entry
        for entry in run_block("colorlog").conditionals
        if entry.condition == "win"
    ]
    named = {item.text for entry in windows for item in entry.then}
    assert named == {"__win", "colorama"}

    click = run_block("click")
    assert any(
        "colorama" in requirement.text and "noarch_platform" in requirement.text
        for requirement in click.requirements
    )
