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

__all__ = ["PYTHON_AXIS", "marker_variables", "reachable_above", "summarize_python"]

#: The two variables a single noarch package can reason about, because they are
#: the only ones that vary across the Pythons it will be installed on.
PYTHON_AXIS = frozenset({"python_version", "python_full_version"})

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


def reachable_above(marker: Marker, python_min: Version) -> bool:
    """Whether the marker can be true on any Python at or above ``python_min``.

    This is what makes a ``python_version < "3.9"`` variant disappear rather
    than participate in the intersection: on a feedstock whose floor is already
    3.10, upstream's advice about 3.8 describes a Python this package will
    never be installed on.

    Decided by sampling each minor release rather than by solving the marker,
    which is exact for the comparisons that occur. Both ends of each release
    are tried so that a window like ``python_full_version >= "3.12.4"`` is not
    mistaken for unreachable -- being wrong in the *discard* direction would
    silently drop a real constraint.
    """
    for minor in range(python_min.minor, _CEILING):
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
    reads ``# more restrictive for python >=3.14`` (DESIGN.md 3.3.1). Anything
    more involved falls back to the marker itself, which is longer but never
    wrong.
    """
    nodes = marker._markers
    if len(nodes) == 1 and isinstance(nodes[0], tuple):
        lhs, op, rhs = nodes[0]
        if isinstance(lhs, Variable) and lhs.serialize() in PYTHON_AXIS:
            return f"python {op.serialize()}{rhs.value}"
        if isinstance(rhs, Variable) and rhs.serialize() in PYTHON_AXIS:
            return f"python {_mirror(op.serialize())}{lhs.value}"
    return str(marker)


def _mirror(operator: str) -> str:
    """``"3.14" <= python_version`` says the same as ``python_version >= "3.14"``."""
    return {"<": ">", "<=": ">=", ">": "<", ">=": "<="}.get(operator, operator)
