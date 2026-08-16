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
from swage.mapping import NameResolver, StaticPackageIndex
from swage.plan import PlanError, PythonMin, RecipePlan, plan_recipe, planned_blocks
from swage.plan.lines import parse_line
from swage.recipe import Requirement, read_recipe
from swage.upstream import parse_pyproject

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


def test_a_templated_virtual_package_is_structure_too() -> None:
    """`__${{ noarch_platform }}` is `__win` written once instead of four times.

    `config/defaults.yaml` blesses the four literal names, and this expands to
    exactly those four, so the same claim reaches the same answer. Requiring
    *every* expansion to be blessed is what keeps recognition an allowlist.

    `noarch_platform` is a per-feedstock variant, declared in
    `recipe/variants.yaml` or `recipe/conda_build_config.yaml`, and its values
    are always drawn from `linux`, `osx`, `win` and `unix` -- the vocabulary a
    recipe selector already uses. At least eleven conda-forge feedstocks write
    it, so it is an idiom rather than one feedstock's invention.

    It was unexplained twice over. The blessing did not reach through a
    template, and `parse_line` read only templates that *open* a line -- so
    this one was split at its first space and reported as an unrecognized
    template named `__${{`, three characters that are not a name at all.
    """
    config = load_config(REPO_ROOT / "config").for_feedstock("click")
    assert TEMPLATED in recipe_at("click")

    line = parse_line(TEMPLATED)

    assert line.name == TEMPLATED, "the whole expression is the name"
    assert line.platform_expansions == ("__linux", "__osx", "__win", "__unix")
    assert line.recipe_owned(config.recipe_owned)


def test_a_real_dependency_rides_the_same_platform_condition() -> None:
    """The virtual package is not what the arrangement exists to deliver.

    `colorlog` writes `if: win` twice: over `__win`, which is structure, and
    over `colorama`, which is a real dependency upstream declares as
    `colorama; sys_platform == "win32"`. `click` says the same thing in one
    templated line that resolves to a *different package* per platform.

    swage used to refuse both feedstocks -- `platform-conditional constraint
    for 'colorama'` -- on the grounds that the marker turns on a variable
    which "does not vary across the Pythons one noarch package is installed
    on". Under this build model it does vary, once per artifact, and each
    recipe already wrote the answer swage was declining to write. Three of the
    fleet's feedstocks were held by that refusal: `click`, `colorlog` and
    `poetry`. `split_by_platform` answers it instead, and the tests below
    plan the fixture to show what it now writes.
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


#: What `colorlog` 6.11.0 declares, reduced to the line this model turns on.
COLORLOG_UPSTREAM = """\
[project]
name = "colorlog"
version = "6.11.0"
dependencies = ["colorama; sys_platform == 'win32'"]

[build-system]
requires = ["setuptools"]
"""


def plan_entry(entry: str, platforms: tuple[str, ...]) -> RecipePlan:
    """Plan a fixture against the repo's real config, over ``platforms``."""
    recipe = read_recipe(recipe_at(entry), entry)
    config = load_config(REPO_ROOT / "config").for_feedstock(entry)
    return plan_recipe(
        recipe,
        parse_pyproject(COLORLOG_UPSTREAM),
        config,
        NameResolver(config.name_map, StaticPackageIndex.of()),
        PythonMin("3.10", ".ci_support/linux_64_.yaml"),
        platforms=platforms,
    )


def run_entries(planned: RecipePlan) -> dict[str, str]:
    """Each planned `run` line, by the name it is written against."""
    written = planned_blocks(planned)
    content = written["/requirements/run"]
    lines = {item.text: "" for item in content.requirements}
    for conditional in content.conditionals:
        for item in conditional.then:
            if isinstance(item, Requirement):
                lines[item.text] = str(conditional.condition)
    return lines


def test_planning_over_several_platforms_writes_the_condition() -> None:
    """The whole point, at the level that decides what lands in a feedstock.

    `colorlog` is built for linux and win, upstream asks for `colorama` on
    Windows alone, and the recipe already says `if: win`. swage now agrees
    with it rather than refusing the feedstock.
    """
    lines = run_entries(plan_entry("colorlog", ("linux", "win")))

    assert lines["colorama"] == "win"


def test_planning_over_one_platform_is_the_ordinary_noarch_refusal() -> None:
    """The same recipe and the same upstream, built once instead of twice.

    A single artifact is installed on every platform at once, so there is no
    condition to write and the marker has no answer -- which is the stop that
    was always right for that model, and stays.
    """
    with pytest.raises(PlanError, match="platform-conditional constraint"):
        plan_entry("colorlog", ("linux",))


def test_a_dependency_with_no_platform_marker_stays_one_plain_line() -> None:
    """What keeps this model from adding structure to the rest of a recipe.

    `python` is asked for on every platform, so it is written once and
    unconditionally, exactly as it is for a single noarch artifact. Only the
    lines whose answers actually differ get a condition.
    """
    lines = run_entries(plan_entry("colorlog", ("linux", "win")))

    assert lines["python >=${{ python_min }}"] == ""


CLICK_UPSTREAM = """\
[project]
name = "click"
version = "8.4.2"
dependencies = ["colorama; sys_platform == 'win32'"]

[build-system]
requires = ["flit-core >=3.11,<4"]
"""


def plan_click() -> RecipePlan:
    recipe = read_recipe(recipe_at("click"), "click")
    config = load_config(REPO_ROOT / "config").for_feedstock("click")
    return plan_recipe(
        recipe,
        parse_pyproject(CLICK_UPSTREAM),
        config,
        NameResolver(config.name_map, StaticPackageIndex.of()),
        PythonMin("3.10", ".ci_support/linux_64_.yaml"),
        platforms=("linux", "win"),
    )


def test_the_templated_line_is_kept_exactly_as_written() -> None:
    """Read, never authored.

    Rewriting `${{ "colorama" if ... }}` into `if: win` / `then: colorama`
    would be correct and would still be wrong: the two say the same thing,
    conda-smithy's linter accepts both, and which one a recipe uses is the
    maintainer's call. swage understands the line well enough to leave it
    alone, which is the whole point.
    """
    written = planned_blocks(plan_click())["/requirements/run"]
    texts = [item.text for item in written.requirements]

    assert '${{ "colorama" if noarch_platform == "win" else "python" }}' in texts
    assert "__${{ noarch_platform }}" in texts


def test_the_dependency_it_delivers_is_not_added_a_second_time() -> None:
    """The duplicate this work exists to remove.

    swage used to keep the templated line *and* plan its own
    `if: win` / `then: colorama` beside it, so the recipe asked for colorama
    twice in two spellings. Only review stood between that and a feedstock.
    """
    written = planned_blocks(plan_click())["/requirements/run"]

    bare = [item.text for item in written.requirements if item.text == "colorama"]
    conditioned = [
        item.text
        for entry in written.conditionals
        for item in entry.then
        if isinstance(item, Requirement) and "colorama" in item.text
    ]

    assert bare == []
    assert conditioned == []
