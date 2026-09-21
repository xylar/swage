"""Read upstream metadata from a core-metadata ``METADATA`` / ``PKG-INFO`` (v1
§3.6). No network is involved here.

Extras live in the markers, not in a table, and `Provides-Extra` is what makes
the extras list complete. A dynamic `Requires-Dist` is recorded, not refused (v1
§3.6.3). Build requirements are not in this format, so `build_requires` is None.
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

    # PEP 643. Recorded, not refused (`UpstreamMetadata.dynamic_fields`).
    dynamic = frozenset(
        value.strip().lower() for value in message.get_all("Dynamic") or []
    )

    # Declaration order is preserved, which is what design-v1.md 6 orders the
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
            # inconsistent metadata; adding it is the safe direction.
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
    """Split ``extra == "..."`` out of a marker, returning it and the remainder
    as a marker in its own right.
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

    # Anything but a flat `and` chain is refused rather than guessed at; no
    # build backend emits one.
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
