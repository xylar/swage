"""Fixing the Python implementation to CPython (DESIGN.md 3.3.4).

conda-forge dropped PyPy, so a marker naming the implementation has an answer
rather than a choice. These test the reduction itself, because the interesting
part is structural: `and` binds tighter than `or`, and a constant folded out of
one group must not change what the other groups say.
"""

from __future__ import annotations

import pytest
from packaging.markers import Marker

from swage.plan.markers import resolve_implementation


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
