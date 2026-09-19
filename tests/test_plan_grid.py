"""The grid's fidelity property (DESIGN.md §9.3).

For every cell in the universe, evaluating the rendered entries at that cell
yields the constraint of the artifact containing it: the intersection of
every declaration active somewhere in that artifact, and nothing where none
is. The condition evaluator here is the test's own, so the property is
checked against what a recipe's `if:` means rather than against the module's
reading of its own selectors.
"""

from __future__ import annotations

import itertools
import re
from collections.abc import Iterable, Sequence

import pytest
from packaging.markers import Marker
from packaging.specifiers import SpecifierSet
from packaging.version import Version

from swage.plan import PlanError, PythonMin, Universe, reconcile
from swage.plan.grid import TARGETS, Artifacts, Branch, Target
from swage.upstream import UpstreamRequirement

# --- generated declarations ---------------------------------------------------

MARKERS = (
    None,
    'python_version < "3.13"',
    'python_version >= "3.13"',
    'python_version >= "3.12" and python_version < "3.14"',
    'sys_platform == "win32"',
    'platform_system != "Windows"',
    'platform_machine == "aarch64"',
    'platform_machine != "arm64"',
    'sys_platform != "win32" and python_version < "3.13"',
    'platform_python_implementation != "PyPy"',
)

SPECIFIERS = ("", ">=1", ">=2,<3", "!=1.5")

#: Enough versions to tell every specifier above from every other.
VERSIONS = tuple(Version(v) for v in ("0.5", "1", "1.5", "2", "2.5", "3", "4"))


def declaration(specifier: str, marker: str | None) -> UpstreamRequirement:
    raw = f"pkg{specifier}" + (f"; {marker}" if marker else "")
    return UpstreamRequirement(name="pkg", specifier=specifier, marker=marker, raw=raw)


DECLARATIONS = tuple(
    declaration(specifier, marker) for marker in MARKERS for specifier in SPECIFIERS
)

UNIVERSES = (
    Universe.arch(pythons=(10, 11, 12, 13)),
    Universe.noarch(PythonMin("3.11", "test"), Version("3.14")),
    Universe.noarch(
        PythonMin("3.11", "test"), Version("3.14"), platforms=("linux", "win")
    ),
)


def sets_of_declarations() -> Iterable[tuple[UpstreamRequirement, ...]]:
    for single in DECLARATIONS:
        yield (single,)
    yield from itertools.combinations(DECLARATIONS, 2)


# --- the test's own reading of a recipe condition -----------------------------

SELECTS: dict[str, set[Target]] = {
    "linux": {t for t in TARGETS if t[0] == "linux"},
    "osx": {t for t in TARGETS if t[0] == "osx"},
    "win": {t for t in TARGETS if t[0] == "win"},
    "unix": {t for t in TARGETS if t[0] != "win"},
    "x86_64": {t for t in TARGETS if t[1] in ("x86_64", "AMD64")},
    "aarch64": {t for t in TARGETS if t[1] == "aarch64"},
    "arm64": {t for t in TARGETS if t[1] in ("arm64", "ARM64")},
    "ppc64le": {t for t in TARGETS if t[1] == "ppc64le"},
    "s390x": {t for t in TARGETS if t[1] == "s390x"},
}

_MATCH = re.compile(r'^match\(python, "(<|>=)3\.(\d+)"\)$')


def _terms(condition: str) -> list[str]:
    """Split on the top-level `and`, leaving parenthesised groups whole."""
    terms, depth, start = [], 0, 0
    for index in range(len(condition)):
        depth += {"(": 1, ")": -1}.get(condition[index], 0)
        if depth == 0 and condition.startswith(" and ", index):
            terms.append(condition[start:index])
            start = index + len(" and ")
    terms.append(condition[start:])
    return terms


def _holds(condition: str | None, minor: int, target: Target) -> bool:
    if condition is None:
        return True
    return all(_term_holds(term, minor, target) for term in _terms(condition))


def _term_holds(term: str, minor: int, target: Target) -> bool:
    matched = _MATCH.match(term)
    if matched:
        operator, bound = matched.group(1), int(matched.group(2))
        return minor < bound if operator == "<" else minor >= bound
    if term.startswith("not ("):
        inner = term[len("not (") : -1]
        joiner = " or " if " or " in inner else " and "
        parts = [SELECTS[name] for name in inner.split(joiner)]
        members = set.union(*parts) if joiner == " or " else set.intersection(*parts)
        return target not in members
    if term.startswith("not "):
        return target not in SELECTS[term[len("not ") :]]
    return target in SELECTS[term]


# --- what each cell should ask for --------------------------------------------


def _active(variant: UpstreamRequirement, minor: int, target: Target) -> bool:
    if variant.marker is None:
        return True
    platform, machine = target
    environment = {
        "python_version": f"3.{minor}",
        "python_full_version": f"3.{minor}.0",
        "sys_platform": {"linux": "linux", "osx": "darwin", "win": "win32"}[platform],
        "platform_system": {"linux": "Linux", "osx": "Darwin", "win": "Windows"}[
            platform
        ],
        "os_name": "nt" if platform == "win" else "posix",
        "platform_machine": machine,
        "platform_python_implementation": "CPython",
        "implementation_name": "cpython",
    }
    return Marker(variant.marker).evaluate(environment)


def _artifact_cells(
    universe: Universe, minor: int, target: Target
) -> list[tuple[int, Target]]:
    if universe.artifacts is Artifacts.ONE:
        return list(universe.cells)
    if universe.artifacts is Artifacts.PER_PLATFORM:
        return [cell for cell in universe.cells if cell[1][0] == target[0]]
    return [(minor, target)]


def _admitted(specifiers: Sequence[str]) -> frozenset[Version]:
    combined = SpecifierSet()
    for specifier in specifiers:
        combined &= SpecifierSet(specifier)
    return frozenset(v for v in VERSIONS if combined.contains(v, prereleases=True))


def _expected(
    universe: Universe,
    variants: Sequence[UpstreamRequirement],
    minor: int,
    target: Target,
) -> frozenset[Version] | None:
    """What the artifact holding this cell must admit, or None if nothing is asked."""
    asked = [
        variant
        for variant in variants
        if any(
            _active(variant, m, t) for m, t in _artifact_cells(universe, minor, target)
        )
    ]
    if not asked:
        return None
    return _admitted([variant.specifier for variant in asked])


def _rendered(
    entries: Sequence[Branch], minor: int, target: Target
) -> frozenset[Version] | None:
    selected = [entry for entry in entries if _holds(entry.condition, minor, target)]
    if not selected:
        return None
    assert len(selected) == 1, f"{len(selected)} entries hold on 3.{minor} {target}"
    return _admitted([selected[0].specifier])


# --- the property ---------------------------------------------------------------


@pytest.mark.parametrize("universe", UNIVERSES, ids=lambda u: u.artifacts.value)
def test_the_rendered_entries_say_what_each_cell_asks_for(universe: Universe) -> None:
    """DESIGN.md §9.3's fidelity property, over every pair of generated declarations.

    A stop is not a rendering and is not checked here; the stops have their
    own tests. Everything that renders has to agree with the artifact's
    constraint on every cell, and an `else:` entry has to be read as one.
    """
    rendered = 0
    for variants in sets_of_declarations():
        try:
            result = reconcile("pkg", variants, universe)
        except PlanError:
            continue
        entries = list(result.entries)
        for minor, target in universe.cells:
            expected = _expected(universe, variants, minor, target)
            actual: frozenset[Version] | None
            if result.complementary:
                # Rendered as `if:` the first condition, `else:` the second
                # entry, whose own condition the recipe never sees.
                first, second = entries
                chosen = first if _holds(first.condition, minor, target) else second
                actual = _admitted([chosen.specifier])
            else:
                actual = _rendered(entries, minor, target)
            assert actual == expected, (
                f"{[v.raw for v in variants]} on 3.{minor} {target}: "
                f"rendered {actual}, expected {expected}"
            )
        rendered += 1
    assert rendered > 100


def test_a_declaration_reaching_no_cell_renders_nothing_and_is_not_considered() -> None:
    universe = Universe.arch(pythons=(12, 13))
    result = reconcile("pkg", [declaration(">=1", 'python_version < "3.10"')], universe)
    assert result.entries == ()
    assert result.considered == ()


def test_the_targets_are_conda_forge_s_and_a_platform_universe_narrows_them() -> None:
    assert len(TARGETS) == 8
    assert {t[0] for t in TARGETS} == {"linux", "osx", "win"}
    universe = Universe.noarch(PythonMin("3.11", "test"), platforms=("linux", "win"))
    assert {t[0] for t in universe.targets} == {"linux", "win"}
    assert universe.artifact_keys == ("linux", "win")


def test_an_else_entry_is_written_only_where_two_entries_partition_the_cells() -> None:
    universe = Universe.arch(pythons=(11, 12, 13))
    result = reconcile(
        "pkg",
        [
            declaration(">=1", 'python_version < "3.13"'),
            declaration(">=2", 'python_version >= "3.13"'),
        ],
        universe,
    )
    assert result.complementary
    assert [e.condition for e in result.entries] == [
        'match(python, "<3.13")',
        'match(python, ">=3.13")',
    ]
