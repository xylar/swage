"""Read upstream metadata from a core-metadata ``METADATA`` / ``PKG-INFO``.

This is the PyPI sdist path, which the google-cloud family needs
(DESIGN.md 4). No network is involved here -- fetching the sdist is a separate
concern from understanding what is inside it.

**Extras live in the markers, not in a table.** Where `pyproject.toml` groups
optional dependencies under `[project.optional-dependencies]`, core metadata
flattens everything into one `Requires-Dist` list and gates each entry with an
`extra == "..."` clause::

    Requires-Dist: pyarrow>=4.0.0; extra == "bqstorage"
    Requires-Dist: grpcio<2.0.0,>=1.75.1;
        python_version >= "3.14" and extra == "bqstorage"

So reading this format means splitting that clause back out: the extra it names
decides which group the requirement belongs to, and whatever marker survives is
the real condition the planner reconciles against `python_min` (DESIGN.md
3.3.1). The second line above is exactly the case that produces a
`# more restrictive for python >=3.14` comment.

**`Provides-Extra` is what makes the extras list complete.** An extra whose
every dependency sits behind some other condition still exists, and G3 requires
swage to account for it. Seeding from the declaration rather than inferring the
list from `Requires-Dist` is what keeps "declared, adds nothing" distinct from
absent -- the same distinction `embedded_extras` draws in config (DESIGN.md 4).

**A dynamic `Requires-Dist` is recorded, not refused.** PEP 643 lets an sdist
flag that its dependency list was computed rather than declared, so another
build might compute a different one. That is worth knowing and not worth
stopping for: the list is still present and complete, and the projects that do
this -- apache-beam, pyspark-client, sagemaker-studio among them -- ship no
`[project]` table to fall back to, so refusing would strand them with usable
metadata in hand. It goes into `dynamic_fields` for a gate to weigh, the way an
inexact `Resolution` reaches G2 instead of failing the mapper.

**Build requirements are not in this format.** Core metadata describes what a
release needs to *run*, never `[build-system] requires`, so `build_requires` is
reported as None -- swage was told nothing, as opposed to being told there is
nothing. A `host` section cannot be reconciled from this source alone, which is
why the sdist path should prefer an sdist's `pyproject.toml` where it has one.
"""

from __future__ import annotations

from email.parser import Parser
from typing import Any

from packaging._parser import Value, Variable
from packaging.markers import InvalidMarker, Marker

from .errors import UpstreamError
from .model import UpstreamMetadata, UpstreamRequirement, normalize_extra
from .pyproject import parse_requirement

__all__ = ["parse_metadata"]


def parse_metadata(text: str, source: str = "METADATA") -> UpstreamMetadata:
    """Parse core metadata into the same normalized model as `parse_pyproject`."""
    message = Parser().parsestr(text, headersonly=True)

    name = message.get("Name")
    if not isinstance(name, str) or not name:
        raise UpstreamError(f"{source}: has no Name")

    # PEP 643. Recorded, not refused -- see `UpstreamMetadata.dynamic_fields`.
    # Unlike `[project] dynamic`, which leaves nothing to read, a dynamic
    # Requires-Dist still ships the full computed list; the flag only says a
    # different build might compute a different one. Refusing would stop
    # projects like apache-beam and pyspark-client, which have no [project]
    # table to fall back to, while holding perfectly usable metadata.
    dynamic = frozenset(
        value.strip().lower() for value in message.get_all("Dynamic") or []
    )

    # Declaration order is preserved, which is what DESIGN.md 6 orders the
    # rendered requirements by.
    optional: dict[str, list[UpstreamRequirement]] = {}
    seen: dict[str, str] = {}
    for declared in message.get_all("Provides-Extra") or []:
        _record_extra(declared, optional, seen, source)

    dependencies: list[UpstreamRequirement] = []
    for raw in message.get_all("Requires-Dist") or []:
        requirement = parse_requirement(raw)
        extras, residual = _split_extras(requirement, source)
        if not extras:
            dependencies.append(requirement)
            continue
        without_extra = UpstreamRequirement(
            name=requirement.name,
            extras=requirement.extras,
            specifier=requirement.specifier,
            marker=residual,
            raw=requirement.raw,
        )
        for extra in extras:
            # A Requires-Dist naming an extra that Provides-Extra omitted is
            # inconsistent metadata. Adding it is the safe direction: it gives
            # G3 one more extra to account for rather than one fewer.
            optional.setdefault(extra, []).append(without_extra)

    return UpstreamMetadata(
        name=name,
        version=_optional_header(message.get("Version")),
        requires_python=_optional_header(message.get("Requires-Python")),
        build_requires=None,
        dependencies=tuple(dependencies),
        optional_dependencies={
            extra: tuple(values) for extra, values in optional.items()
        },
        dynamic_fields=dynamic,
    )


def _record_extra(
    declared: str,
    optional: dict[str, list[UpstreamRequirement]],
    seen: dict[str, str],
    source: str,
) -> None:
    normalized = normalize_extra(declared)
    if normalized in seen and seen[normalized] != declared:
        raise UpstreamError(
            f"{source}: Provides-Extra declares {seen[normalized]!r} and "
            f"{declared!r}, which are the same extra once normalized "
            f"to {normalized!r}"
        )
    seen[normalized] = declared
    optional.setdefault(normalized, [])


def _optional_header(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _split_extras(
    requirement: UpstreamRequirement, source: str
) -> tuple[tuple[str, ...], str | None]:
    """Split ``extra == "..."`` out of a marker, returning it and the remainder.

    The remainder is what the planner evaluates against `python_min`, so it has
    to come back as a marker in its own right rather than as leftover text.
    """
    if requirement.marker is None:
        return (), None
    try:
        marker = Marker(requirement.marker)
    except InvalidMarker as exc:  # pragma: no cover -- packaging already parsed it
        raise UpstreamError(
            f"{source}: cannot parse marker {requirement.marker!r}: {exc}"
        ) from exc

    nodes = marker._markers
    if not _mentions_extra(nodes):
        return (), requirement.marker

    # Anything but a flat `and` chain is refused rather than guessed at. An
    # `or` over extras has a defensible reading, but no build backend emits
    # one, so supporting it would mean shipping an untested interpretation of
    # a case that does not occur.
    operands = nodes[0::2]
    joiners = nodes[1::2]
    if any(joiner != "and" for joiner in joiners):
        raise UpstreamError(
            f"{source}: cannot separate the extra from marker "
            f"{requirement.marker!r}: `extra ==` is combined with `or`"
        )

    extras: list[str] = []
    kept: list[Any] = []
    for operand in operands:
        if not _mentions_extra(operand):
            kept.append(operand)
            continue
        named = _extra_equality(operand)
        if named is None:
            raise UpstreamError(
                f"{source}: cannot separate the extra from marker "
                f"{requirement.marker!r}: `extra` is used other than as "
                '`extra == "name"`'
            )
        extras.append(normalize_extra(named))

    residual = " and ".join(_serialize(operand) for operand in kept)
    return tuple(extras), residual or None


def _mentions_extra(node: Any) -> bool:
    if isinstance(node, list):
        return any(_mentions_extra(item) for item in node)
    if isinstance(node, tuple):
        return any(
            isinstance(item, Variable) and item.serialize() == "extra" for item in node
        )
    return False


def _extra_equality(operand: Any) -> str | None:
    """The extra named by ``extra == "x"``, or None if that is not the shape."""
    if not isinstance(operand, tuple) or len(operand) != 3:
        return None
    lhs, op, rhs = operand
    if op.serialize() != "==":
        return None
    for variable, value in ((lhs, rhs), (rhs, lhs)):
        if (
            isinstance(variable, Variable)
            and variable.serialize() == "extra"
            and isinstance(value, Value)
        ):
            return str(value.value)
    return None


def _serialize(node: Any) -> str:
    """Render a marker operand back to PEP 508 text."""
    if isinstance(node, list):
        inner = " ".join(
            item if isinstance(item, str) else _serialize(item) for item in node
        )
        return f"({inner})"
    return " ".join(item.serialize() for item in node)
