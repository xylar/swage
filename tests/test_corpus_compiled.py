"""The compiled corpus: recipes that build something other than one noarch wheel.

Every fixture swage had until now was `noarch: python`, because both tools it
replaces only ever produced those. That is a property of the corpus and not of
conda-forge: a feedstock with compilers, with several architecture-specific
outputs, or with an arch base output beside noarch metapackages is an ordinary
feedstock, and swage's scope was never meant to exclude one.

Two things are pinned here.

**What each entry is for.** A fixture earns its place by carrying a shape no
other fixture carries, so re-vendoring one at a later version must not quietly
drop the shape it was chosen for. `SHAPES` is that claim, checked against the
file.

**What swage does with each entry today.** All of them read, round-trip
byte-exactly, and now plan: an output built once per python has its markers
written as conditions rather than collapsed (DESIGN.md 3.3.1.1). `TODAY`
records that, so a regression moves a table in this file rather than going
unnoticed.

> Eight of the nine used to be refused over `requirements/build` -- a section
> swage read strictly, validated strictly enough to reject the whole recipe,
> and then never planned. What replaced that refusal is not a looser reader but
> one that understands `if:`/`then:`, which is the v1 grammar for anything
> conditional. The stop that remains is a decision about reconciliation rather
> than an inability to parse.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
import yaml

from swage.config import load_config
from swage.mapping import NameResolver, StaticPackageIndex
from swage.plan import (
    PlanError,
    RecipePlan,
    check_preconditions,
    needs_python_min,
    plan_recipe,
    planned_blocks,
    resolve_python_min,
)
from swage.recipe import Recipe, RecipeError, Requirement, read_recipe, render_recipe
from swage.upstream import RecipeUpstream, parse_pyproject

from .conftest import CONFIG_ROOT, REPO_ROOT

COMPILED = REPO_ROOT / "tests" / "corpus" / "compiled"
ENTRIES = sorted(path.name for path in COMPILED.iterdir() if path.is_dir())

#: What each entry is here to carry. Every tag is derived from the file below,
#: so a fixture that loses its shape fails rather than passing silently.
SHAPES: dict[str, frozenset[str]] = {
    "cprnc": frozenset({"compiler"}),
    "libnetcdf": frozenset(
        {"compiler", "conditional-build", "conditional-host", "pin-subpackage"}
    ),
    "netcdf-fortran": frozenset(
        {
            "compiler",
            "conditional-build",
            "conditional-host",
            "conditional-run",
            "pin-subpackage",
            "declared-variant",
        }
    ),
    "moab": frozenset({"compiler", "conditional-build", "conditional-host"}),
    # The one entry with a reader of its own (DESIGN.md 3.6.6), so it carries
    # upstream's files as well as the recipe -- `tests/test_upstream_esmf.py`
    # reads those. Here it is another compiled recipe with conditionals in all
    # three sections and a build variant from conda-forge's global pinning.
    "esmf": frozenset(
        {
            "compiler",
            "conditional-build",
            "conditional-host",
            "conditional-run",
            "upstream-files",
        }
    ),
    "pyproj": frozenset(
        {"compiler", "conditional-build", "cross-compilation", "python"}
    ),
    "python-eccodes": frozenset(
        {"compiler", "conditional-build", "cross-compilation", "python"}
    ),
    "snowflake-connector-python": frozenset(
        {"compiler", "conditional-build", "cross-compilation", "python"}
    ),
    "apache-beam": frozenset(
        {
            "compiler",
            "conditional-build",
            "cross-compilation",
            "python",
            "mixed-noarch",
            "resolved-python-min",
        }
    ),
    "gdal": frozenset(
        {"compiler", "conditional-build", "conditional-host", "conditional-run"}
    ),
}

#: What swage does with each entry today. Until the build model became a
#: property of each output, every line of this table read "planner: builds no
#: noarch: python package".
TODAY: dict[str, str] = dict.fromkeys(ENTRIES, "plans")


def recipe_text(entry: str) -> str:
    return (COMPILED / entry / "recipe.yaml").read_text(encoding="utf-8")


def conda_build_config(entry: str) -> str | None:
    path = COMPILED / entry / "conda_build_config.yaml"
    return path.read_text(encoding="utf-8") if path.is_file() else None


def ci_support(entry: str) -> list[tuple[str, str]]:
    """The `.ci_support` variants vendored with this entry, if any."""
    return [
        (path.name, path.read_text(encoding="utf-8"))
        for path in sorted((COMPILED / entry).glob("linux_*.yaml"))
    ]


def outputs(document: dict[str, Any]) -> list[dict[str, Any]]:
    declared = document.get("outputs")
    return declared if isinstance(declared, list) else [document]


def noarch_values(document: dict[str, Any]) -> list[str | None]:
    """The `noarch` of each output, with the top-level build as the fallback."""
    declared = document.get("build")
    top: dict[str, Any] = declared if isinstance(declared, dict) else {}
    found: list[str | None] = []
    for output in outputs(document):
        build = output.get("build")
        found.append((build if isinstance(build, dict) else top).get("noarch"))
    return found


def conditional_sections(document: dict[str, Any]) -> Iterator[str]:
    """Sections holding an `if:`/`then:` entry rather than a plain requirement."""
    for output in outputs(document):
        requirements = output.get("requirements")
        if not isinstance(requirements, dict):
            continue
        for section, entries in requirements.items():
            if isinstance(entries, list) and any(
                isinstance(entry, dict) and "if" in entry for entry in entries
            ):
                yield f"conditional-{section}"


def shapes(entry: str) -> frozenset[str]:
    text = recipe_text(entry)
    document = yaml.safe_load(text)
    found = set(conditional_sections(document))
    if "compiler(" in text or "stdlib(" in text:
        found.add("compiler")
    if "pin_subpackage" in text:
        found.add("pin-subpackage")
    if "cross-python_" in text:
        found.add("cross-compilation")
    if "python" in {
        str(line).split()[0]
        for output in outputs(document)
        for entries in (output.get("requirements") or {}).values()
        if isinstance(entries, list)
        for line in entries
        if isinstance(line, str) and line.split()
    }:
        found.add("python")
    noarch = noarch_values(document)
    if "python" in noarch and any(value != "python" for value in noarch):
        found.add("mixed-noarch")
    config = conda_build_config(entry)
    if config and any(
        isinstance(values, list) and len(values) > 1
        for values in (yaml.safe_load(config) or {}).values()
    ):
        found.add("declared-variant")
    if any("python_min" in text for _, text in ci_support(entry)):
        found.add("resolved-python-min")
    if (COMPILED / entry / "common.mk").is_file():
        # Upstream's own declaration, vendored beside the recipe. Only `esmf`
        # has one, because only `esmf` has a reader that reads one.
        found.add("upstream-files")
    return frozenset(found)


def test_the_compiled_corpus_is_not_empty() -> None:
    assert set(ENTRIES) == set(SHAPES) == set(TODAY)
    assert len(ENTRIES) >= 10


@pytest.mark.parametrize("entry", ENTRIES)
def test_every_entry_builds_something_architecture_specific(entry: str) -> None:
    """The one property that makes this corpus what the other two are not."""
    assert any(
        value != "python" for value in noarch_values(yaml.safe_load(recipe_text(entry)))
    )


@pytest.mark.parametrize("entry", ENTRIES)
def test_every_entry_still_carries_the_shape_it_was_chosen_for(entry: str) -> None:
    assert shapes(entry) >= SHAPES[entry]


def test_the_corpus_covers_the_shapes_the_scope_work_needs() -> None:
    """Coverage of the corpus as a whole, so a gap is visible here.

    Each of these is a rule swage does not have yet: a conditional entry in a
    section it plans, a compiled feedstock with no `python_min` to resolve, one
    that has it because it also builds noarch outputs, and a variant the
    feedstock declares for itself.
    """
    covered = frozenset().union(*(shapes(entry) for entry in ENTRIES))
    assert covered >= {
        "conditional-build",
        "conditional-host",
        "conditional-run",
        "cross-compilation",
        "declared-variant",
        "mixed-noarch",
        "pin-subpackage",
        "resolved-python-min",
    }
    without = [entry for entry in ENTRIES if "resolved-python-min" not in shapes(entry)]
    assert without, "no entry exercises a feedstock with no python_min to resolve"


@pytest.mark.parametrize("entry", ENTRIES)
def test_every_entry_reads_and_round_trips_byte_for_byte(entry: str) -> None:
    """The claim the whole write path rests on, on recipes swage did not write.

    Rendering every block of a recipe swage has not changed must reproduce the
    file exactly, or "no changes needed" is a statement about swage's formatter
    rather than about its plan (gate G7). These nine are where that claim is
    hardest: conditional entries, inline branches and block branches, one
    conditional nested inside another's list.
    """
    text = recipe_text(entry)
    recipe = read_recipe(text, entry)
    rendered = render_recipe(
        recipe, {path: block.content for path, block in recipe.blocks.items()}
    )
    assert rendered == text


def test_the_reader_sees_the_conditionals_rather_than_skipping_them() -> None:
    """Round-tripping is not proof that anything was understood.

    A reader that carried a conditional entry as opaque text would pass the
    test above and be no use to the planner, so this asserts on the model.
    """
    libnetcdf = read_recipe(recipe_text("libnetcdf"), "libnetcdf")
    build = libnetcdf.blocks["/requirements/build"].content
    assert len(build.conditionals) == 8
    assert build.conditionals[0].condition == "unix"
    assert [
        entry.text
        for entry in build.conditionals[0].then
        if isinstance(entry, Requirement)
    ] == ["make", "pkg-config", "gnuconfig"]
    host = libnetcdf.blocks["/requirements/host"].content
    assert [entry.condition for entry in host.conditionals] == [
        'mpi != "nompi"',
        'mpi != "nompi"',
        "unix",
    ]
    # The inline spelling says the same thing and has to read the same way.
    assert host.conditionals[0].then_inline
    only = host.conditionals[0].then[0]
    assert isinstance(only, Requirement)
    assert only.text == "${{ mpi }}"


@pytest.mark.parametrize("entry", ENTRIES)
def test_where_swage_stops_on_each_entry_today(entry: str) -> None:
    """The gap, measured. Every line of this table is work still to do."""
    assert stopped_at(entry) == TODAY[entry]


#: Upstream metadata that asks for nothing at all. What each entry's *own*
#: upstream declares is a question about a feedstock's config rather than about
#: the recipe, and `libnetcdf` has no reader at all -- so the corpus is planned
#: against silence, which exercises every rule that reads the recipe and none
#: that needs a dependency list. An empty `requires` says "this needs nothing"
#: rather than "nothing was said", so no build backend is added either
#: (DESIGN.md 3.6.2).
NOTHING_DECLARED = """\
[project]
name = "demo"
version = "1.0.0"
dependencies = []

[build-system]
requires = []
"""


def plan(entry: str, upstream: str = NOTHING_DECLARED) -> tuple[Recipe, RecipePlan]:
    """Plan one entry against the repo's real config."""
    recipe = read_recipe(recipe_text(entry), entry)
    config = load_config(CONFIG_ROOT).for_feedstock(entry)
    return recipe, plan_recipe(
        recipe,
        RecipeUpstream.of(parse_pyproject(upstream)),
        config,
        NameResolver(config.name_map, StaticPackageIndex.of()),
        resolve_python_min(recipe, ci_support(entry)),
    )


def stopped_at(entry: str) -> str:
    text = recipe_text(entry)
    try:
        check_preconditions(text)
    except PlanError as exc:
        return f"refused: {str(exc).splitlines()[0]}"
    try:
        read_recipe(text, entry)
    except RecipeError as exc:
        section = str(exc).removeprefix(f"{entry}: ").split(" ", 1)[0]
        return f"reader: {section}"
    try:
        plan(entry)
    except PlanError as exc:
        return f"planner: {str(exc).splitlines()[0]}"
    return "plans"


@pytest.mark.parametrize("entry", ENTRIES)
def test_no_conditional_entry_is_lost_in_planning(entry: str) -> None:
    """The claim that matters on a recipe swage did not write.

    A planned section is rebuilt from the plan rather than edited, so an entry
    the plan does not carry is an entry that disappears from somebody's
    feedstock. These nine are where that is most likely: 24 conditional entries
    across the sections swage plans -- mpi variants, cross-compilation blocks,
    platform splits -- and nothing in upstream metadata explains any of them.
    """
    recipe, planned = plan(entry)
    written = planned_blocks(planned)
    for path, content in written.items():
        before = recipe.blocks[path].content.conditionals
        assert [entry.condition for entry in content.conditionals] == [
            entry.condition for entry in before
        ], path


def test_a_compiled_feedstock_may_have_no_python_min_to_resolve() -> None:
    """`cprnc` builds no Python package at all, and conda-smithy knows it.

    None of the 26 `.ci_support` variants pyproj renders declares `python_min`
    either, and that is not an omission -- it is what conda-smithy writes for a
    feedstock whose Python is a build variant rather than a floor. So the
    absence resolves to None, swage does not read `.ci_support` looking for
    one, and the stop belongs to an output that needed a floor and had none
    (DESIGN.md 3.3.3).
    """
    recipe = read_recipe(recipe_text("cprnc"), "cprnc")
    assert not needs_python_min(recipe)
    assert resolve_python_min(recipe, ci_support("pyproj")) is None


def test_a_feedstock_with_noarch_outputs_among_compiled_ones_still_needs_it() -> None:
    """`apache-beam` declares it in every variant: eleven outputs are noarch."""
    recipe = read_recipe(recipe_text("apache-beam"), "apache-beam")
    assert needs_python_min(recipe)
    found = resolve_python_min(recipe, ci_support("apache-beam"))
    assert found is not None and found.value == "3.10"


def test_a_host_change_on_a_cross_compiled_output_is_held_for_review() -> None:
    """`pyproj` repeats `cython` inside its cross-compilation block and not `proj`.

    A `host` requirement swage adds or bumps may need mirroring there, and a
    recipe that got only half of that builds natively and fails cross-compiled.
    Which requirements belong in the block is a judgment per dependency that
    no metadata contains (DESIGN.md 3.3.6.1), so the plan holds for a human.
    """
    declares = NOTHING_DECLARED.replace("requires = []", 'requires = ["cython>=3.1"]')
    recipe, planned = plan("pyproj", declares)
    assert planned.cross_compiled == ("`pyproj`'s `host` requirements",)
    # The block the mirroring would go in, so the fixture losing it is a
    # failure here rather than a test that passes for the wrong reason.
    build = recipe.blocks["/requirements/build"].content
    assert any("cython" in str(entry.then) for entry in build.conditionals)


def test_a_host_swage_would_leave_alone_is_not_held() -> None:
    """The hold is about a change, not about cross-compiling."""
    _, planned = plan("pyproj")
    assert planned.cross_compiled == ()


def test_a_host_change_confined_to_pure_python_tools_is_not_held() -> None:
    """A cross build takes `setuptools` from the host prefix, so no block wants it.

    `pyproj` states it in `host` and does not repeat it in `build`, and neither
    does any other cross-compiled output in the fleet bar one. Asking whether
    a bumped `setuptools` needs mirroring is asking a question with no answer
    in it, which is what `pure_python_build_tools` settles (DESIGN.md 3.3.6.1).
    """
    declares = NOTHING_DECLARED.replace(
        "requires = []", 'requires = ["setuptools >=70"]'
    )
    recipe, planned = plan("pyproj", declares)
    before = recipe.blocks["/requirements/host"].content.texts()
    after = planned_blocks(planned)["/requirements/host"].texts()
    assert sorted(before) != sorted(after)
    assert planned.cross_compiled == ()


def test_the_same_holds_for_a_tool_stated_beside_ones_that_are_mirrored() -> None:
    """`packaging` on snowflake-connector-python, beside `cython` and `numpy`.

    The block repeats both of those, so the output is one where mirroring is a
    live question -- and still not one this change asks it about.
    """
    declares = NOTHING_DECLARED.replace(
        "requires = []", 'requires = ["packaging >=24"]'
    )
    recipe, planned = plan("snowflake-connector-python", declares)
    before = recipe.blocks["/requirements/host"].content.texts()
    after = planned_blocks(planned)["/requirements/host"].texts()
    assert sorted(before) != sorted(after)
    assert planned.cross_compiled == ()


def test_a_pure_python_tool_the_block_does_repeat_is_still_held() -> None:
    """`libcst` copies setuptools into its block, so a bump leaves that stale.

    The list says which requirements a cross build never needs a second copy
    of. It does not say a copy somebody wrote is none of swage's business, so
    the recipe's own block wins wherever the two disagree.
    """
    text = recipe_text("pyproj").replace(
        "        - cython\n", "        - cython\n        - setuptools\n", 1
    )
    recipe = read_recipe(text, "pyproj")
    config = load_config(CONFIG_ROOT).for_feedstock("pyproj")
    declares = NOTHING_DECLARED.replace(
        "requires = []", 'requires = ["setuptools >=70"]'
    )
    planned = plan_recipe(
        recipe,
        RecipeUpstream.of(parse_pyproject(declares)),
        config,
        NameResolver(config.name_map, StaticPackageIndex.of()),
        resolve_python_min(recipe, ci_support("pyproj")),
    )
    before = recipe.blocks["/requirements/host"].content.texts()
    after = planned_blocks(planned)["/requirements/host"].texts()
    assert sorted(before) != sorted(after)
    assert planned.cross_compiled == ("`pyproj`'s `host` requirements",)


def test_a_host_swage_only_reorders_is_not_held() -> None:
    """Ordering is swage's (DESIGN.md 6) and says nothing about mirroring.

    Declaring what the recipe already lists, in upstream's order rather than
    the recipe's, moves `setuptools` above `cython` and changes no requirement.
    Half of what this gate stopped across the fleet was exactly that.
    """
    declares = NOTHING_DECLARED.replace(
        "requires = []", 'requires = ["setuptools", "cython"]'
    )
    recipe, planned = plan("pyproj", declares)
    before = recipe.blocks["/requirements/host"].content.texts()
    after = planned_blocks(planned)["/requirements/host"].texts()
    assert before != after and sorted(before) == sorted(after)
    assert planned.cross_compiled == ()
