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

**What swage does with each entry today.** All nine are read by conda-forge and
by rattler-build; eight of the nine swage refuses, every one of them over
`requirements/build` -- a section swage reads strictly, validates, and then
never plans (DESIGN.md 3.3.6 plans `host` and `run` only). `TODAY` records that
so the scope work moves a table in this file rather than leaving the change
invisible.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
import yaml

from swage.plan import PlanError, check_preconditions, resolve_python_min
from swage.recipe import RecipeError, read_recipe

from .conftest import REPO_ROOT

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

#: Where swage stops on each entry today, before the scope work. `read` means
#: the recipe parses; everything else names what refused it.
TODAY: dict[str, str] = {
    "cprnc": "read",
    "libnetcdf": "refused: /requirements/build",
    "netcdf-fortran": "refused: build-variant switch",
    "moab": "refused: /requirements/build",
    "pyproj": "refused: /requirements/build",
    "python-eccodes": "refused: /requirements/build",
    "snowflake-connector-python": "refused: /requirements/build",
    "apache-beam": "refused: /outputs/0/requirements/build",
    "gdal": "refused: /outputs/0/requirements/build",
}


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
    return frozenset(found)


def test_the_compiled_corpus_is_not_empty() -> None:
    assert set(ENTRIES) == set(SHAPES) == set(TODAY)
    assert len(ENTRIES) >= 9


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
def test_where_swage_stops_on_each_entry_today(entry: str) -> None:
    """The gap, measured. Every line of this table is work still to do."""
    assert stopped_at(entry) == TODAY[entry]


def stopped_at(entry: str) -> str:
    text = recipe_text(entry)
    try:
        check_preconditions(text, conda_build_config(entry))
    except PlanError as exc:
        assert "build-variant switch" in str(exc)
        return "refused: build-variant switch"
    try:
        read_recipe(text, entry)
    except RecipeError as exc:
        section = str(exc).removeprefix(f"{entry}: ").split(" ", 1)[0]
        return f"refused: {section}"
    return "read"


def test_a_compiled_feedstock_may_have_no_python_min_to_resolve() -> None:
    """`cprnc` builds no Python package at all, and conda-smithy knows it.

    None of the 26 `.ci_support` variants pyproj renders declares `python_min`
    either, and that is not an omission -- it is what conda-smithy writes for a
    feedstock whose Python is a build variant rather than a floor. swage treats
    the absence as a stop, with advice ("run conda-smithy on this feedstock")
    that would not help, because it has only ever met noarch feedstocks where
    the value is always there (DESIGN.md 3.3.3).
    """
    recipe = read_recipe(recipe_text("cprnc"), "cprnc")
    with pytest.raises(PlanError, match="cannot determine python_min"):
        resolve_python_min(recipe, ci_support("pyproj"))
