"""Fixing the Python implementation to CPython (DESIGN.md 3.3.4).

conda-forge dropped PyPy, so a marker naming the implementation has an answer
rather than a choice. These test the reduction itself, because the interesting
part is structural: `and` binds tighter than `or`, and a constant folded out of
one group must not change what the other groups say.
"""

from __future__ import annotations

import pytest
from packaging.markers import Marker
from packaging.version import Version

from swage.plan.markers import (
    MACHINE_AXIS,
    PLATFORM_AXIS,
    reach_profile,
    resolve_implementation,
    without_axis,
)


def resolved(marker: str) -> str | None:
    outcome = resolve_implementation(Marker(marker))
    return None if outcome is None else str(outcome)


def unreachable(marker: str) -> bool:
    outcome = resolve_implementation(Marker(marker))
    return outcome is not None and not outcome.evaluate({"python_version": "3.13"})


@pytest.mark.parametrize(
    "marker",
    [
        'platform_python_implementation != "PyPy"',
        'implementation_name == "cpython"',
        # The legacy spelling `packaging` folds into the canonical one.
        'python_implementation != "PyPy"',
    ],
)
def test_a_marker_true_of_cpython_leaves_nothing_conditional(marker: str) -> None:
    assert resolved(marker) is None


@pytest.mark.parametrize(
    "marker",
    [
        'platform_python_implementation == "PyPy"',
        'implementation_name != "cpython"',
        'python_version >= "3.12" and platform_python_implementation == "PyPy"',
    ],
)
def test_a_marker_false_of_cpython_reaches_nothing(marker: str) -> None:
    assert unreachable(marker)


def test_the_rest_of_the_marker_survives() -> None:
    assert (
        resolved(
            'python_version >= "3.12" and platform_python_implementation != "PyPy"'
        )
        == 'python_version >= "3.12"'
    )


def test_a_false_branch_of_an_or_drops_without_taking_the_or_with_it() -> None:
    assert (
        resolved('sys_platform == "win32" or platform_python_implementation == "PyPy"')
        == 'sys_platform == "win32"'
    )


def test_a_true_branch_of_an_or_settles_the_whole_marker() -> None:
    marker = 'sys_platform == "win32" or implementation_name == "cpython"'
    assert resolved(marker) is None


def test_precedence_survives_the_reduction() -> None:
    """`and` binds tighter than `or`, so the surviving groups stay grouped."""
    outcome = resolve_implementation(
        Marker(
            'python_version >= "3.12" and sys_platform == "linux" '
            'or platform_machine == "arm64"'
        )
    )
    assert outcome is not None
    assert outcome.evaluate({"python_version": "3.10", "platform_machine": "arm64"})
    assert not outcome.evaluate(
        {
            "python_version": "3.10",
            "sys_platform": "linux",
            "platform_machine": "x86_64",
        }
    )


def test_a_marker_naming_no_implementation_is_left_alone() -> None:
    assert resolved('python_version < "3.12"') == 'python_version < "3.12"'


# --- taking the wheel matrix as true (DESIGN.md 3.3.4.1) --------------------

WHEEL_MATRIX = PLATFORM_AXIS | MACHINE_AXIS


def folded(marker: str) -> str | None:
    outcome = without_axis(Marker(marker), WHEEL_MATRIX)
    return None if outcome is None else str(outcome)


def test_a_marker_that_is_only_about_the_wheel_matrix_folds_away() -> None:
    """`sqlalchemy`'s greenlet: what is left is an unconditional dependency."""
    assert (
        folded(
            'platform_machine == "aarch64" or (platform_machine == "ppc64le" '
            'or platform_machine == "x86_64")'
        )
        is None
    )
    assert folded('sys_platform != "darwin"') is None


def test_the_two_spellings_of_one_group_fold_to_the_same_marker() -> None:
    """The whole reason for folding rather than substituting a true comparison.

    `apache-airflow-providers-jdbc` writes macOS ARM and its complement, so
    without the fold these would be two different-looking markers describing
    the same Pythons -- and the collapse that decides between them compares
    exactly that.
    """
    on_mac_arm = (
        'python_version == "3.13" and sys_platform == "darwin" '
        'and platform_machine == "arm64"'
    )
    everywhere_else = (
        'python_version == "3.13" and (sys_platform != "darwin" '
        'or platform_machine != "arm64")'
    )
    assert folded(on_mac_arm) == folded(everywhere_else) == 'python_version == "3.13"'


def test_an_axis_outside_the_wheel_matrix_survives_the_fold() -> None:
    """So the caller still refuses it, rather than taking it as answered."""
    assert folded('platform_release >= "20"') == 'platform_release >= "20"'


def test_a_profile_says_where_in_the_range_a_marker_holds() -> None:
    """Two declarations are collapsed only where they describe the same builds."""
    same = reach_profile(Marker('python_version == "3.13"'), Version("3.10"))
    assert reach_profile(Marker('python_version == "3.13"'), Version("3.10")) == same
    assert reach_profile(Marker('python_version >= "3.13"'), Version("3.10")) != same
    # The unconditional marker holds everywhere, so a declaration carrying none
    # compares against one that does.
    assert set(reach_profile(None, Version("3.10"))) == {True}
