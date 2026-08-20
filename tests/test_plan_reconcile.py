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
from packaging.version import Version

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
    assert result.note == "tightest of upstream's floors (python >=3.14)"


def test_a_note_names_both_ends_where_they_came_from_different_places() -> None:
    """`google-ads` wrote this distinction by hand before swage ran on it.

    A note naming only the floor invites the reader to assume the whole
    constraint came from that declaration -- and here the ceiling comes from a
    different one entirely, which is the thing worth knowing before anybody
    edits the line.
    """
    result = reconcile(
        "protobuf",
        [
            parse_requirement('protobuf>=4.25.3,<8.0.0; python_version >="3.10"'),
            parse_requirement('protobuf>=5.26.1; python_version >="3.13"'),
        ],
        PY310,
    )

    assert result.specifier == ">=5.26.1,<8.0.0"
    assert result.note == (
        "tightest of upstream's floors (python >=3.13) and ceilings (python >=3.10)"
    )


def test_a_note_names_one_end_where_both_came_from_the_same_place() -> None:
    """The common line keeps the short sentence."""
    result = reconcile(
        "protobuf",
        [
            parse_requirement("protobuf>=4.25.3"),
            parse_requirement('protobuf>=5.26.1,<8.0.0; python_version >="3.13"'),
        ],
        PY310,
    )

    assert result.note == "tightest of upstream's floors (python >=3.13)"


def test_a_ceiling_alone_is_worth_a_note_too() -> None:
    """The mirror image: every declaration agrees on the floor, one caps it.

    Rarer, and the same reading problem -- the recipe demands less than
    upstream does on most Pythons with nothing on the line saying why.
    """
    result = reconcile(
        "protobuf",
        [
            parse_requirement("protobuf>=4.25.3"),
            parse_requirement('protobuf<8.0.0; python_version >="3.13"'),
        ],
        PY310,
    )

    assert result.note == "tightest of upstream's ceilings (python >=3.13)"


def test_a_window_marker_reads_as_one_constraint() -> None:
    """`apache-airflow-providers-snowflake` is the fleet's case.

    Falling back to the marker itself is never wrong and is unreadable here:
    the note quoted `python_version >= "3.12" and python_version < "3.14"` back
    verbatim, in a section where every other note said `python >=3.14`.
    """
    result = reconcile(
        "snowflake-snowpark-python",
        [
            parse_requirement("snowflake-snowpark-python>=1.17.0"),
            parse_requirement(
                "snowflake-snowpark-python>=1.27.0; "
                'python_version >= "3.12" and python_version < "3.14"'
            ),
        ],
        PY310,
    )
    assert result.note == "tightest of upstream's floors (python >=3.12,<3.14)"


def test_a_marker_swage_cannot_reduce_is_quoted_rather_than_guessed_at() -> None:
    """An `or` has no comma-joined reading, so the marker itself is the answer."""
    result = reconcile(
        "importlib-metadata",
        [
            parse_requirement("importlib-metadata>=4.0"),
            parse_requirement(
                'importlib-metadata>=6.0; python_version < "3.11" '
                'or python_version >= "3.13"'
            ),
        ],
        PY310,
    )
    assert result.note is not None
    assert "or" in result.note


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
    assert result.note == "tightest of upstream's floors (python >=3.14)"


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
    # The reason is spelled out rather than cited: this is a message a
    # maintainer reads with no design document to hand.
    assert "swage does not edit conda-forge.yml" in message
    assert "G5" not in message


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


def test_a_declaration_gated_on_pypy_is_dropped() -> None:
    """conda-forge builds no PyPy, so upstream asks for it on no package here."""
    result = reconcile(
        "pypy-fix",
        [parse_requirement('pypy-fix>=1.0; platform_python_implementation == "PyPy"')],
        PY310,
    )
    assert result.specifier == ""


def test_a_declaration_gated_off_pypy_is_unconditional() -> None:
    """`trino-python-client`'s `orjson`. conda-forge builds CPython and only
    CPython, so the condition holds on every package built from that recipe --
    there is nothing for a maintainer to decide and swage does not stop."""
    result = reconcile(
        "orjson",
        [
            parse_requirement(
                'orjson >= 3.11.0 ; platform_python_implementation != "PyPy"'
            )
        ],
        PY310,
    )
    assert result.specifier == ">=3.11.0"
    # And nothing to attribute: the bound binds on every Python, so the comment
    # explaining why it is tighter than upstream would be explaining nothing.
    assert result.note is None


def test_the_implementation_half_of_a_marker_resolves_away() -> None:
    """What is left is the python half, and it reconciles as it always did."""
    result = reconcile(
        "pkg",
        [
            parse_requirement("pkg>=1.0"),
            parse_requirement(
                'pkg>=2.0; python_version >= "3.12" and '
                'platform_python_implementation != "PyPy"'
            ),
        ],
        PY310,
    )
    assert result.specifier == ">=2.0"
    assert result.note == "tightest of upstream's floors (python >=3.12)"


def test_a_platform_marker_is_still_refused_beside_a_resolved_one() -> None:
    """Resolving the implementation away does not resolve anything else away."""
    with pytest.raises(PlanError, match="platform-conditional"):
        reconcile(
            "pywin32",
            [
                parse_requirement(
                    'pywin32>=306; sys_platform == "win32" and '
                    'platform_python_implementation != "PyPy"'
                )
            ],
            PY310,
        )


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
    """Every marker comment in the corpus, with the line it annotates.

    These are the bespoke tool's real published output, so they are the closest
    thing to ground truth available for DESIGN.md 3.3.1.

    **What a case carries is the python range, not the comment's wording.**
    Both tools wrote their own and swage writes a third, because theirs read as
    though the constraint applied only above the version named when it in fact
    binds on every python (`reconcile._note`). The range is the half all three
    agree on, and the half a reconciliation bug would get wrong.
    """
    cases: list[tuple[str, str, str]] = []
    for directory in sorted(CORPUS.iterdir()):
        recipe = directory / "recipe.yaml"
        if not recipe.exists():
            continue
        lines = recipe.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines):
            if "more restrictive" in line:
                # "# more restrictive [constraint] for python >=3.13"
                _, _, python_range = line.strip().partition("for ")
                cases.append(
                    (
                        directory.name,
                        python_range,
                        lines[index + 1].strip().removeprefix("- "),
                    )
                )
    return cases


MARKER_CASES = _corpus_marker_cases()


def test_the_corpus_actually_exercises_marker_reconciliation() -> None:
    """Guard against the parametrized test below silently covering nothing."""
    assert len(MARKER_CASES) >= 7


@pytest.mark.parametrize(
    ("provider", "python_range", "dependency"),
    MARKER_CASES,
    ids=[f"{p}-{d.split()[0]}" for p, _, d in MARKER_CASES],
)
def test_reconciling_reproduces_the_corpus_line(
    provider: str, python_range: str, dependency: str
) -> None:
    """swage's reconciliation matches what the tool it replaces published.

    Both halves are compared: the intersected constraint *and* the python
    range the comment names, since the comment is what makes a recipe stricter
    than upstream legible rather than mysterious. The wording around that
    range is swage's own -- see `_corpus_marker_cases`.
    """
    name = dependency.split()[0]
    groups = _upstream_groups(CORPUS / provider / "pyproject.toml")
    result = reconcile(name, groups[name], PY310)
    assert f"{name} {result.specifier}" == dependency
    assert result.note == f"tightest of upstream's floors ({python_range})"


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


def test_a_variant_above_the_recipes_python_cap_is_discarded() -> None:
    """`google-cloud-pubsublite` caps at 3.14 and upstream declares for 3.14.

    Reconciling that variant in demands a `grpcio` conda-forge does not have,
    on a package the cap says is never installed on 3.14 -- which is what the
    cap's own comment in that recipe says the feedstock is waiting out.
    """
    variants = [
        parse_requirement("grpcio<2.0.0,>=1.38.1"),
        parse_requirement('grpcio<2.0.0,>=1.75.1; python_version >= "3.14"'),
    ]
    assert reconcile("grpcio", variants, PY310).specifier == ">=1.75.1,<2.0.0"

    capped = reconcile("grpcio", variants, PY310, python_max=Version("3.14"))
    assert capped.specifier == ">=1.38.1,<2.0.0"
    assert capped.note is None


def test_a_cap_above_every_marker_changes_nothing() -> None:
    """`<4.0` is the fleet's commonest cap and reaches no marker at all."""
    variants = [
        parse_requirement("grpcio>=1.38.1"),
        parse_requirement('grpcio>=1.75.1; python_version >= "3.14"'),
    ]
    assert (
        reconcile("grpcio", variants, PY310, python_max=Version("4.0")).specifier
        == ">=1.75.1"
    )


def test_a_platform_marker_is_answerable_once_a_platform_is_bound() -> None:
    """The same declaration, asked once per artifact instead of once.

    `colorlog` is the fleet's case and its recipe already writes the answer:
    `if: win` / `then: colorama`, from `colorama; sys_platform == "win32"`.
    """
    declaration = [parse_requirement('colorama; sys_platform == "win32"')]

    assert reconcile("colorama", declaration, PY310, platform="win").considered
    assert not reconcile("colorama", declaration, PY310, platform="linux").considered
    assert not reconcile("colorama", declaration, PY310, platform="osx").considered


def test_a_bound_platform_keeps_the_specifier_of_the_platform_it_holds_on() -> None:
    """`poetry`, whose recipe says `if: osx` / `then: xattr >=1.0.0,<2.0.0`."""
    declaration = [parse_requirement("xattr>=1.0.0,<2.0.0 ; sys_platform == 'darwin'")]

    assert reconcile("xattr", declaration, PY310, platform="osx").specifier == (
        ">=1.0.0,<2.0.0"
    )
    assert reconcile("xattr", declaration, PY310, platform="linux").specifier == ""


def test_a_python_marker_answers_the_same_on_every_platform() -> None:
    """What keeps this model from writing a condition it has no reason to.

    The python axis is collapsed inside each artifact exactly as it is for a
    single one, so a declaration that says nothing about the platform gives
    the same answer three times -- and the caller writes one line.
    """
    results = {
        platform: reconcile("pandas", PANDAS, PY310, platform=platform).specifier
        for platform in ("linux", "osx", "win")
    }

    assert set(results.values()) == {">=2.3.3"}
    assert results["win"] == reconcile("pandas", PANDAS, PY310).specifier


def test_a_bound_platform_does_not_repeat_itself_in_the_note() -> None:
    """The comment would sit inside the condition that already says it."""
    result = reconcile(
        "xattr",
        [parse_requirement("xattr>=1.0.0,<2.0.0 ; sys_platform == 'darwin'")],
        PY310,
        platform="osx",
    )

    assert result.specifier == ">=1.0.0,<2.0.0"
    assert result.note is None


def test_a_machine_marker_stops_even_with_a_platform_bound() -> None:
    """`noarch_platforms` lists whole subdirs, so the machine is still fixed.

    A different reason from the single-artifact one, and the message says so
    rather than talking about the Pythons a noarch package is installed on.
    """
    with pytest.raises(PlanError) as caught:
        reconcile(
            "some-package",
            [parse_requirement('some-package; platform_machine == "aarch64"')],
            PY310,
            platform="linux",
        )

    message = str(caught.value)
    assert "build-conditional constraint" in message
    assert "platform_machine" in message
    assert "per-platform noarch packages" in message
    assert "installed on" not in message
