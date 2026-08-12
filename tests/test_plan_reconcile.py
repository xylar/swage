"""Marker-reconciliation tests (DESIGN.md 3.3.1 - 3.3.4).

Tested for refusal as much as for results. A variant below `python_min` is
ignored, overlapping bounds intersect to the tightest, a non-overlapping pair
stops the feedstock, and a `sys_platform` marker stops it too. The two stops
get assertions on the *message*: an error nobody can act on is barely better
than the silent drop it replaces.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import pytest

from swage.plan import PlanError, PythonMin, reconcile
from swage.upstream import UpstreamRequirement, parse_pyproject, parse_requirement

from .conftest import REPO_ROOT

CORPUS = REPO_ROOT / "tests" / "corpus" / "airflow-providers"

PY310 = PythonMin("3.10", ".ci_support/linux_64_.yaml")
PY39 = PythonMin("3.9", ".ci_support/linux_64_.yaml")

#: The DESIGN.md 3.3.1 example, from apache-airflow-providers-databricks.
PANDAS = [
    parse_requirement('pandas>=2.1.2; python_version <"3.13"'),
    parse_requirement(
        'pandas>=2.2.3; python_version >="3.13" and python_version <"3.14"'
    ),
    parse_requirement('pandas>=2.3.3; python_version >="3.14"'),
]


def test_overlapping_bounds_intersect_to_the_tightest() -> None:
    """One artifact has to satisfy every Python it is installed on."""
    result = reconcile("pandas", PANDAS, PY310)
    assert result.specifier == ">=2.3.3"


def test_the_binding_marker_becomes_a_comment() -> None:
    """Otherwise the recipe demands more than upstream and never says why."""
    result = reconcile("pandas", PANDAS, PY310)
    assert result.note == "more restrictive for python >=3.14"


def test_a_variant_below_python_min_is_discarded() -> None:
    """Upstream's advice about 3.8 describes a Python this will never see."""
    result = reconcile(
        "typing-extensions",
        [
            parse_requirement('typing-extensions>=4.0; python_version <"3.9"'),
            parse_requirement("typing-extensions>=4.7"),
        ],
        PY310,
    )
    assert result.specifier == ">=4.7"
    assert [r.specifier for r in result.considered] == [">=4.7"]


def test_a_variant_below_python_min_is_kept_when_the_floor_is_lower() -> None:
    """The same declarations reconcile differently on a 3.9 feedstock."""
    result = reconcile(
        "typing-extensions",
        [
            parse_requirement('typing-extensions>=4.0; python_version <"3.10"'),
            parse_requirement("typing-extensions>=4.7"),
        ],
        PY39,
    )
    assert len(result.considered) == 2


def test_an_unconditional_declaration_needs_no_comment() -> None:
    variants = [parse_requirement("requests>=2.21.0,<3.0.0")]
    result = reconcile("requests", variants, PY310)
    assert result.note is None
    assert result.specifier == ">=2.21.0,<3.0.0"


def test_upper_and_lower_bounds_both_survive() -> None:
    """The prior art drops the upper bound here; that is the bug being fixed."""
    result = reconcile(
        "grpcio",
        [
            parse_requirement("grpcio<2.0.0,>=1.59.0"),
            parse_requirement('grpcio<2.0.0,>=1.75.1; python_version >= "3.14"'),
        ],
        PY310,
    )
    assert result.specifier == ">=1.75.1,<2.0.0"
    assert result.note == "more restrictive for python >=3.14"


def test_an_unconstrained_dependency_reconciles_to_nothing() -> None:
    result = reconcile("polars", [parse_requirement("polars")], PY310)
    assert result.specifier == ""
    assert result.note is None


def test_a_package_upstream_no_longer_asks_for_reconciles_empty() -> None:
    """Every declaration gated below the floor means upstream wants none of it."""
    result = reconcile(
        "backports-zoneinfo",
        [parse_requirement('backports-zoneinfo; python_version <"3.9"')],
        PY310,
    )
    assert result.specifier == ""
    assert result.considered == ()


# --- refusals -------------------------------------------------------------


def test_contradictory_constraints_stop_the_feedstock() -> None:
    with pytest.raises(PlanError) as caught:
        reconcile(
            "pandas",
            [
                parse_requirement('pandas<2.1.2 ; python_version <"3.13"'),
                parse_requirement('pandas>=2.3.3; python_version >="3.13"'),
            ],
            PY39,
        )
    assert "contradictory upstream constraints for 'pandas'" in str(caught.value)


def test_the_contradiction_message_is_actionable() -> None:
    """DESIGN.md 3.3.2 specifies this message; a vague one is barely better.

    It has to quote the conflict, say which Python range it holds over and
    where that number came from, and name the file to resolve it in.
    """
    with pytest.raises(PlanError) as caught:
        reconcile(
            "pandas",
            [
                parse_requirement('pandas<2.1.2 ; python_version <"3.13"'),
                parse_requirement('pandas>=2.3.3; python_version >="3.13"'),
            ],
            PY39,
            feedstock="apache-airflow-providers-databricks",
        )
    message = str(caught.value)
    assert "pandas<2.1.2" in message
    assert "pandas>=2.3.3" in message
    assert 'python_version < "3.13"' in message
    assert "python >=3.9" in message
    assert ".ci_support/linux_64_.yaml" in message
    assert "one noarch package" in message
    assert "config/feedstocks/apache-airflow-providers-databricks.yaml" in message


def test_a_contradiction_only_reachable_above_the_floor_still_stops() -> None:
    result_floor = PythonMin("3.13", ".ci_support/linux_64_.yaml")
    with pytest.raises(PlanError, match="contradictory"):
        reconcile(
            "pandas",
            [
                parse_requirement('pandas<2.1.2 ; python_version >="3.13"'),
                parse_requirement('pandas>=2.3.3; python_version >="3.14"'),
            ],
            result_floor,
        )


def test_a_contradiction_hidden_below_the_floor_does_not_stop() -> None:
    """The conflicting variant describes a Python that is out of range."""
    result = reconcile(
        "pandas",
        [
            parse_requirement('pandas<2.1.2 ; python_version <"3.10"'),
            parse_requirement('pandas>=2.3.3; python_version >="3.10"'),
        ],
        PY310,
    )
    assert result.specifier == ">=2.3.3"


def test_a_platform_marker_stops_the_feedstock() -> None:
    with pytest.raises(PlanError, match="platform-conditional"):
        reconcile(
            "pywin32",
            [parse_requirement('pywin32>=306; sys_platform == "win32"')],
            PY310,
        )


def test_the_platform_message_names_both_resolutions() -> None:
    """Saying swage *cannot* do this sends the reader somewhere very different
    from saying it will not choose for them, and only the second is true
    (DESIGN.md 3.3.4)."""
    with pytest.raises(PlanError) as caught:
        reconcile(
            "pywin32",
            [parse_requirement('pywin32>=306; sys_platform == "win32"')],
            PY310,
        )
    message = str(caught.value)
    assert "sys_platform" in message
    assert "noarch_platforms" in message
    assert "unconditionally" in message
    assert "G5" in message


def test_a_platform_marker_mixed_with_python_still_stops() -> None:
    with pytest.raises(PlanError, match="platform-conditional"):
        reconcile(
            "pywin32",
            [
                parse_requirement(
                    'pywin32>=306; sys_platform == "win32" and python_version >= "3.12"'
                )
            ],
            PY310,
        )


def test_an_implementation_marker_stops_the_feedstock() -> None:
    with pytest.raises(PlanError) as caught:
        reconcile(
            "pypy-fix",
            [
                parse_requirement(
                    'pypy-fix>=1.0; platform_python_implementation == "PyPy"'
                )
            ],
            PY310,
        )
    assert "platform_python_implementation" in str(caught.value)


def test_python_full_version_is_on_the_python_axis() -> None:
    """It varies across the Pythons one noarch package sees, so it reconciles."""
    result = reconcile(
        "pkg",
        [
            parse_requirement("pkg>=1.0"),
            parse_requirement('pkg>=2.0; python_full_version >= "3.12.4"'),
        ],
        PY310,
    )
    assert result.specifier == ">=2.0"


def test_no_declarations_at_all_is_a_programming_error() -> None:
    with pytest.raises(PlanError, match="no upstream declarations"):
        reconcile("pandas", [], PY310)


# --- golden corpus --------------------------------------------------------


def _upstream_groups(pyproject: Path) -> dict[str, list[UpstreamRequirement]]:
    """Every declaration of every package, keyed by name."""
    metadata = parse_pyproject(pyproject.read_text(encoding="utf-8"), str(pyproject))
    groups: dict[str, list[UpstreamRequirement]] = defaultdict(list)
    for requirement in metadata.dependencies:
        groups[requirement.name].append(requirement)
    for requirements in metadata.optional_dependencies.values():
        for requirement in requirements:
            groups[requirement.name].append(requirement)
    return groups


def _corpus_marker_cases() -> list[tuple[str, str, str]]:
    """Every `# more restrictive` line in the corpus, with the line it annotates.

    These are the bespoke tool's real published output, so they are the closest
    thing to ground truth available for DESIGN.md 3.3.1.
    """
    cases: list[tuple[str, str, str]] = []
    for directory in sorted(CORPUS.iterdir()):
        recipe = directory / "recipe.yaml"
        if not recipe.exists():
            continue
        lines = recipe.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines):
            if "more restrictive" in line:
                cases.append(
                    (
                        directory.name,
                        line.strip().lstrip("# "),
                        lines[index + 1].strip().removeprefix("- "),
                    )
                )
    return cases


MARKER_CASES = _corpus_marker_cases()


def test_the_corpus_actually_exercises_marker_reconciliation() -> None:
    """Guard against the parametrized test below silently covering nothing."""
    assert len(MARKER_CASES) >= 7


@pytest.mark.parametrize(
    ("provider", "note", "dependency"),
    MARKER_CASES,
    ids=[f"{p}-{d.split()[0]}" for p, _, d in MARKER_CASES],
)
def test_reconciling_reproduces_the_corpus_line(
    provider: str, note: str, dependency: str
) -> None:
    """swage's reconciliation matches what the tool it replaces published.

    Both halves are compared: the intersected constraint *and* the comment
    wording, since the comment is what makes a recipe stricter than upstream
    legible rather than mysterious.
    """
    name = dependency.split()[0]
    groups = _upstream_groups(CORPUS / provider / "pyproject.toml")
    result = reconcile(name, groups[name], PY310)
    assert f"{name} {result.specifier}" == dependency
    assert result.note == note


def test_a_prerelease_bound_does_not_crash_the_planner() -> None:
    """`opentelemetry-instrumentation >=0.20b0` is real, and `0.20b0.1` is not
    a version -- which crashed the planner on a live google-cloud feedstock."""
    result = reconcile(
        "opentelemetry-instrumentation",
        [parse_requirement("opentelemetry-instrumentation>=0.20b0")],
        PY310,
    )
    assert result.specifier == ">=0.20b0"


@pytest.mark.parametrize(
    "spec", ["1.0.dev1", "2.0.0rc1", "1!2.0", "0.20b0", "1.2.3", "2026.5.20.19"]
)
def test_unusual_version_syntax_reconciles_without_crashing(spec: str) -> None:
    result = reconcile("pkg", [parse_requirement(f"pkg>={spec}")], PY310)
    assert result.specifier == f">={spec}"


@pytest.mark.parametrize(
    ("low", "high"), [("0.20b0", "2.0"), ("1.0", "2.0"), ("2.0.0rc1", "3.0")]
)
def test_a_strictly_bounded_range_is_recognized_as_satisfiable(
    low: str, high: str
) -> None:
    """Nothing the set mentions is inside it, so a witness has to be built.

    `>V` excludes a post-release of V and `<V` excludes a pre-release of V, so
    the witness has to come from bumping the release segment.
    """
    result = reconcile(
        "pkg",
        [parse_requirement(f"pkg>{low}"), parse_requirement(f"pkg<{high}")],
        PY310,
    )
    assert result.specifier
