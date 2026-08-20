"""Which environment markers swage can reduce to the Python-version axis.

conda-forge builds **one `noarch: python` package**, installed on every Python
from `python_min` upward. A marker that varies along the Python-version axis is
therefore something swage can reconcile: it decides which upstream variants are
reachable and intersects what survives (DESIGN.md 3.3.1).

A marker along any *other* axis is not. That is not because no answer exists --
DESIGN.md 3.3.4 is emphatic that two answers exist and both are real -- but
because choosing between them is a packaging decision rather than a
reconciliation, so swage stops and says so.

The Python implementation is the exception, and `resolve_implementation` takes
it out of the way before any of that: conda-forge builds CPython and nothing
else, so `platform_python_implementation != "PyPy"` is not a choice between two
builds. It is a condition that holds on every artifact there is.
"""

from __future__ import annotations

from typing import Any

from packaging._parser import Variable
from packaging.markers import Marker
from packaging.version import Version

__all__ = [
    "CPYTHON",
    "IMPLEMENTATION_AXIS",
    "MACHINE_AXIS",
    "PLATFORM_AXIS",
    "PLATFORM_MARKERS",
    "PYTHON_AXIS",
    "marker_variables",
    "optimistic",
    "reachable_in_range",
    "resolve_implementation",
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

#: The machine a build runs on, which conda-forge varies over as surely as it
#: varies over the platform: `linux-aarch64`, `osx-arm64` and `win-arm64` are
#: build targets and a recipe selects them by name. Separate from
#: `PLATFORM_AXIS` because the noarch path refuses both alike while the arch
#: path writes conditions on each (DESIGN.md 3.3.4).
MACHINE_AXIS = frozenset({"platform_machine"})

#: The interpreter every artifact in this fleet runs on. conda-forge stopped
#: building PyPy variants, so the implementation axis has one point on it and
#: a marker naming it has an answer rather than a choice -- unlike the platform
#: and machine axes, where two real builds are being decided between.
CPYTHON: dict[str, str] = {
    "platform_python_implementation": "CPython",
    "implementation_name": "cpython",
}

#: The variables `CPYTHON` fixes. `packaging` folds the legacy
#: `python_implementation` spelling into `platform_python_implementation`, so
#: both forms are covered by the canonical name.
IMPLEMENTATION_AXIS = frozenset(CPYTHON)

#: Each platform conda-forge builds for, spelled the way a marker sees it.
#: Every variable in `PLATFORM_AXIS` is given a value, because `packaging`
#: fills an unset one from the interpreter running swage -- which would make a
#: plan depend on the machine it was made on.
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

    For asking whether a declaration can reach any build at all. `packaging`
    fills an unset environment variable from the interpreter running swage, so
    evaluating a marker that names one answers from the machine the plan was
    made on -- and answers *false* for `platform_release >= "20"` on the wrong
    laptop, silently discarding a declaration that should have stopped the
    feedstock instead.

    Taking the unmodeled half as true is the safe direction: everything that
    might reach a build survives, so the only declarations dropped are those no
    assignment of the unknown variables could rescue.
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


#: A comparison that holds in no environment, for the other half of the same
#: job: a declaration gated on PyPy reaches nothing conda-forge builds, and the
#: callers that already drop unreachable declarations then drop it.
_NEVER = 'python_version < "0"'


def resolve_implementation(marker: Marker) -> Marker | None:
    """The marker with the Python implementation fixed to CPython.

    conda-forge no longer builds PyPy, so every artifact in this fleet runs
    CPython and a marker naming the implementation has one answer.
    `trino-python-client` declares ``orjson >= 3.11.0 ;
    platform_python_implementation != "PyPy"``, which without this stops the
    feedstock as though a choice had to be made -- when the condition is simply
    true of every package conda-forge will build from that recipe.

    Comparisons on the implementation axis are evaluated and the constants they
    become are folded away, so what comes back names only axes that really do
    vary. ``None`` is a marker that survives as always-true, meaning the
    declaration is unconditional after all; a marker that survives as
    always-false comes back as `_NEVER`, which the reachability checks the
    callers already run then drop.
    """
    resolved = _resolve(marker._markers)
    if resolved is True:
        return None
    if resolved is False:
        return Marker(_NEVER)
    return Marker(resolved)


def _resolve(node: Any) -> str | bool:
    """One node with the implementation axis evaluated, or what it reduces to.

    A `packaging` marker list is a flat sequence of comparisons joined by
    ``and`` and ``or``, evaluated as an `or` over `and`-groups -- so it is
    reduced the same way, group by group, rather than by rebuilding a tree.
    """
    if isinstance(node, tuple):
        named = {item.serialize() for item in node if isinstance(item, Variable)}
        text = " ".join(item.serialize() for item in node)
        # A comparison of one implementation variable against another is not
        # something upstream writes, and evaluating it here would mean deciding
        # what it meant. It survives, and the caller refuses the axis as before.
        if not named or not named <= IMPLEMENTATION_AXIS:
            return text
        return Marker(text).evaluate(CPYTHON)

    groups: list[list[str] | None] = [[]]
    for item in node:
        if item == "or":
            groups.append([])
            continue
        if item == "and":
            continue
        resolved = _resolve(item)
        if isinstance(item, list) and isinstance(resolved, str):
            # The parentheses were in what upstream wrote and have to stay:
            # `and` binds tighter than `or`, so a group flattened into its
            # parent would change what the marker says.
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


def reachable_in_range(
    marker: Marker,
    python_min: Version,
    python_max: Version | None = None,
    platform: str | None = None,
) -> bool:
    """Whether the marker can be true on any Python this package is installed on.

    That range is bounded below by ``python_min``, conda-forge's build floor,
    and above by ``python_max`` where the recipe caps its own `python` line
    (DESIGN.md 3.3.3). Both ends do the same job: a variant that can only
    be true outside the range describes a Python this package will never be
    installed on, so it disappears rather than participating in the
    intersection.

    ``platform`` pins the platform half of the environment, for the build
    model where one `noarch: python` package is built per platform. Each
    artifact is still installed across the whole Python range, so the question
    is unchanged -- it is just being asked once per artifact rather than once.
    Left unset, the caller has already refused any marker that names a
    platform, so nothing needs a value.

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
                **(PLATFORM_MARKERS[platform] if platform is not None else {}),
            }
            # A marker naming anything else never reaches here: the caller
            # stops on an axis this build model does not vary over first
            # (DESIGN.md 3.3.4).
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
