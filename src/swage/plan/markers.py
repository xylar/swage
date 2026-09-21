"""Which environment markers swage can reduce to the Python-version axis
(DESIGN.md §9.2).

A marker along the platform or machine axis is a packaging decision on a noarch
output (v1 §3.3.4). The Python implementation is folded away first: conda-forge
builds CPython only.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from packaging._parser import Variable
from packaging.markers import Marker

__all__ = [
    "CPYTHON",
    "IMPLEMENTATION_AXIS",
    "MACHINE_AXIS",
    "PLATFORM_AXIS",
    "PLATFORM_MARKERS",
    "PYTHON_AXIS",
    "marker_variables",
    "optimistic",
    "resolve_implementation",
    "summarize_python",
    "without_axis",
]

#: The two variables a single noarch package can reason about, because they are
#: the only ones that vary across the Pythons it will be installed on.
PYTHON_AXIS = frozenset({"python_version", "python_full_version"})

#: The variables that say which platform a package is being built for (v1
#: §3.3.4).
PLATFORM_AXIS = frozenset({"sys_platform", "platform_system", "os_name"})

#: The machine a build runs on. Separate from `PLATFORM_AXIS` because the arch
#: path writes conditions on each (v1 §3.3.4).
MACHINE_AXIS = frozenset({"platform_machine"})

#: The interpreter every artifact in this fleet runs on: conda-forge no longer
#: builds PyPy.
CPYTHON: dict[str, str] = {
    "platform_python_implementation": "CPython",
    "implementation_name": "cpython",
}

#: The variables `CPYTHON` fixes. `packaging` folds the legacy spelling into
#: `platform_python_implementation`.
IMPLEMENTATION_AXIS = frozenset(CPYTHON)

#: Each platform conda-forge builds for, spelled the way a marker sees it. Every
#: variable is given a value, because `packaging` fills an unset one from the
#: interpreter running swage.
PLATFORM_MARKERS: dict[str, dict[str, str]] = {
    "linux": {"sys_platform": "linux", "platform_system": "Linux", "os_name": "posix"},
    "osx": {"sys_platform": "darwin", "platform_system": "Darwin", "os_name": "posix"},
    "win": {"sys_platform": "win32", "platform_system": "Windows", "os_name": "nt"},
}

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


#: A comparison that holds in every environment, to stand in for one swage has
#: nothing to say about.
_ALWAYS = 'python_version >= "0"'


def optimistic(marker: Marker, modeled: frozenset[str]) -> Marker:
    """The marker with every comparison swage does not model taken as true.

    For asking whether a declaration can reach any build at all. Taking the
    unmodeled half as true is the safe direction: only declarations no
    assignment could rescue are dropped.
    """
    return Marker(_rewritten(marker._markers, modeled))


def _rewritten(node: Any, modeled: frozenset[str]) -> str:
    if isinstance(node, list):
        return "(" + " ".join(_rewritten(item, modeled) for item in node) + ")"
    if isinstance(node, tuple):
        named = {item.serialize() for item in node if isinstance(item, Variable)}
        if not named <= modeled:
            return _ALWAYS
        return " ".join(item.serialize() for item in node)
    return str(node)


#: A comparison that holds in no environment: a declaration gated on PyPy
#: reaches nothing conda-forge builds.
_NEVER = 'python_version < "0"'


def resolve_implementation(marker: Marker) -> Marker | None:
    """The marker with the Python implementation fixed to CPython (DESIGN.md
    §9.2 step 1).

    ``None`` is a marker that survives as always-true; one that survives as
    always-false comes back as `_NEVER`, which reachability then drops.
    """
    return _folded(marker, _as_cpython)


def _as_cpython(named: set[str], text: str) -> str | bool:
    # A comparison of one implementation variable against another survives, and
    # the caller refuses the axis.
    if not named or not named <= IMPLEMENTATION_AXIS:
        return text
    return Marker(text).evaluate(CPYTHON)


def without_axis(marker: Marker, axis: frozenset[str]) -> Marker | None:
    """The marker with every comparison on ``axis`` taken as true, and folded
    (DESIGN.md §9.2 step 2).

    Folding rather than substituting filler, so that two spellings of the
    same builds fold to the same marker. ``None`` where nothing is left.
    """

    def decide(named: set[str], text: str) -> str | bool:
        return True if named & axis else text

    return _folded(marker, decide)


def _folded(
    marker: Marker, decide: Callable[[set[str], str], str | bool]
) -> Marker | None:
    """``marker`` with each comparison put to ``decide`` and the result reduced.

    ``None`` is always-true; always-false comes back as `_NEVER`.
    """
    resolved = _resolve(marker._markers, decide)
    if resolved is True:
        return None
    if resolved is False:
        return Marker(_NEVER)
    return Marker(resolved)


def _resolve(node: Any, decide: Callable[[set[str], str], str | bool]) -> str | bool:
    """One node with ``decide`` applied, or what it reduces to.

    A `packaging` marker is an `or` over `and`-groups and is reduced group by
    group.
    """
    if isinstance(node, tuple):
        named = {item.serialize() for item in node if isinstance(item, Variable)}
        return decide(named, " ".join(item.serialize() for item in node))

    groups: list[list[str] | None] = [[]]
    for item in node:
        if item == "or":
            groups.append([])
            continue
        if item == "and":
            continue
        resolved = _resolve(item, decide)
        if isinstance(item, list) and isinstance(resolved, str):
            # The parentheses were in what upstream wrote and have to stay:
            # `and` binds tighter than `or`.
            resolved = f"({resolved})"
        if groups[-1] is None or resolved is True:
            continue
        if resolved is False:
            groups[-1] = None
            continue
        groups[-1].append(resolved)

    surviving = [group for group in groups if group is not None]
    if any(group == [] for group in surviving):
        # Every comparison in that group is true, so the `or` is decided.
        return True
    if not surviving:
        return False
    conjunctions = [" and ".join(group) for group in surviving]
    if len(conjunctions) == 1:
        return conjunctions[0]
    return " or ".join(f"({text})" for text in conjunctions)


def summarize_python(marker: Marker) -> str:
    """Render a Python-axis marker the way a recipe comment says it (v1 §3.3.1).

    ``python_version >= "3.14"`` becomes ``python >=3.14``; a window becomes
    ``python >=3.12,<3.14``. Anything else falls back to the marker itself.
    """
    reduced = _conjunction(marker._markers)
    return f"python {','.join(reduced)}" if reduced else str(marker)


def _conjunction(nodes: Any) -> list[str] | None:
    """Every clause of an `and`-chain of Python comparisons, or None where it
    cannot be reduced.
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
