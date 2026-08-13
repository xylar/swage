"""Which environment markers swage can reduce to the Python-version axis.

conda-forge builds **one `noarch: python` package**, installed on every Python
from `python_min` upward. A marker that varies along the Python-version axis is
therefore something swage can reconcile: it decides which upstream variants are
reachable and intersects what survives (DESIGN.md 3.3.1).

A marker along any *other* axis is not. That is not because no answer exists --
DESIGN.md 3.3.4 is emphatic that two answers exist and both are real -- but
because choosing between them is a packaging decision rather than a
reconciliation, so swage stops and says so.
"""

from __future__ import annotations

from typing import Any

from packaging._parser import Variable
from packaging.markers import Marker
from packaging.version import Version

__all__ = [
    "PLATFORM_AXIS",
    "PYTHON_AXIS",
    "marker_variables",
    "reachable_in_range",
    "summarize_python",
]

#: The two variables a single noarch package can reason about, because they are
#: the only ones that vary across the Pythons it will be installed on.
PYTHON_AXIS = frozenset({"python_version", "python_full_version"})

#: The variables that say which platform a package is being built for. A
#: noarch output cannot reason about these at all (DESIGN.md 3.3.4); an
#: architecture-specific one is built once for each of them, so they are an
#: axis a condition can key on exactly as the python version is.
PLATFORM_AXIS = frozenset({"sys_platform", "platform_system", "os_name"})

#: How far above `python_min` to look for a Python the marker admits. Well past
#: anything conda-forge will ship before this code is rewritten.
_CEILING = 40


def marker_variables(marker: Marker) -> frozenset[str]:
    """Every environment variable the marker mentions."""
    return frozenset(_variables(marker._markers))


def _variables(node: Any) -> set[str]:
    if isinstance(node, list):
        found: set[str] = set()
        for item in node:
            found |= _variables(item)
        return found
    if isinstance(node, tuple):
        return {item.serialize() for item in node if isinstance(item, Variable)}
    return set()


def reachable_in_range(
    marker: Marker, python_min: Version, python_max: Version | None = None
) -> bool:
    """Whether the marker can be true on any Python this package is installed on.

    That range is bounded below by ``python_min``, conda-forge's build floor,
    and above by ``python_max`` where the recipe caps its own `python` line
    (DESIGN.md 3.3.3). Both ends do the same job: a variant that can only
    be true outside the range describes a Python this package will never be
    installed on, so it disappears rather than participating in the
    intersection.

    Decided by sampling each minor release rather than by solving the marker,
    which is exact for the comparisons that occur. Both ends of each release
    are tried so that a window like ``python_full_version >= "3.12.4"`` is not
    mistaken for unreachable -- being wrong in the *discard* direction would
    silently drop a real constraint.
    """
    for minor in range(python_min.minor, _CEILING):
        if python_max is not None and (python_max.major, python_max.minor) <= (
            python_min.major,
            minor,
        ):
            # The cap is exclusive, so the first release at or above it is
            # already outside the range.
            break
        for patch in (0, 99):
            version = f"{python_min.major}.{minor}.{patch}"
            environment = {
                "python_version": f"{python_min.major}.{minor}",
                "python_full_version": version,
            }
            # A marker naming anything else never reaches here: the caller
            # stops on a non-Python axis first (DESIGN.md 3.3.4).
            if marker.evaluate(environment):
                return True
    return False


def summarize_python(marker: Marker) -> str:
    """Render a Python-axis marker the way a recipe comment says it.

    ``python_version >= "3.14"`` becomes ``python >=3.14``, so the comment
    reads ``# tightest of upstream's floors (python >=3.14)``
    (DESIGN.md 3.3.1).

    **A window is one marker and reads as one constraint.**
    ``python_version >= "3.12" and python_version < "3.14"`` becomes
    ``python >=3.12,<3.14`` -- the comma-joined form a constraint on a
    dependency line is already written in, which is what keeps the note reading
    like the rest of the recipe. `apache-airflow-providers-snowflake` is the
    fleet's case, and without this its note quoted the marker back verbatim,
    quotes and `and` included, in a section where every other note read
    ``python >=3.14``.

    Anything else -- an `or`, a nested group, an axis this cannot reduce --
    still falls back to the marker itself, which is longer but never wrong.
    """
    reduced = _conjunction(marker._markers)
    return f"python {','.join(reduced)}" if reduced else str(marker)


def _conjunction(nodes: Any) -> list[str] | None:
    """Every clause of an `and`-chain of Python comparisons, or None.

    None rather than an empty list for "cannot reduce this", so that a marker
    reducing to nothing could never be read as one saying nothing.
    """
    if not isinstance(nodes, list):
        return None
    clauses: list[str] = []
    for index, node in enumerate(nodes):
        if index % 2:
            # The separators sit between the comparisons, and only `and` keeps
            # the comma-joined reading true.
            if node != "and":
                return None
            continue
        clause = _comparison(node)
        if clause is None:
            return None
        clauses.append(clause)
    return clauses or None


def _comparison(node: Any) -> str | None:
    """``python_version >= "3.14"`` as ``>=3.14``, or None if it is not one."""
    if not isinstance(node, tuple):
        return None
    lhs, op, rhs = node
    if isinstance(lhs, Variable) and lhs.serialize() in PYTHON_AXIS:
        return f"{op.serialize()}{rhs.value}"
    if isinstance(rhs, Variable) and rhs.serialize() in PYTHON_AXIS:
        return f"{_mirror(op.serialize())}{lhs.value}"
    return None


def _mirror(operator: str) -> str:
    """``"3.14" <= python_version`` says the same as ``python_version >= "3.14"``."""
    return {"<": ">", "<=": ">=", ">": "<", ">=": "<="}.get(operator, operator)
