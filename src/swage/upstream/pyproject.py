"""Read upstream metadata from a ``pyproject.toml``.

This is the airflow-providers path: each provider release is a git tag in the
apache/airflow monorepo carrying that provider's ``pyproject.toml``
(DESIGN.md 4). No network is involved here -- fetching the file is a separate
concern from understanding it.
"""

from __future__ import annotations

import tomllib
from typing import Any

from packaging.requirements import InvalidRequirement, Requirement

from .errors import UpstreamError
from .model import UpstreamMetadata, UpstreamRequirement

__all__ = ["parse_pyproject", "parse_requirement"]


def parse_requirement(raw: str) -> UpstreamRequirement:
    """Parse one PEP 508 requirement string."""
    try:
        parsed = Requirement(raw)
    except InvalidRequirement as exc:
        raise UpstreamError(f"cannot parse requirement {raw!r}: {exc}") from exc
    return UpstreamRequirement(
        name=parsed.name,
        # Sorted because PEP 508 parses extras into a set, so declaration order
        # is already lost by the time we see them. Sorting at least makes the
        # key stable, which matters -- it is what `embedded_extras` looks up.
        extras=tuple(sorted(parsed.extras)),
        specifier=str(parsed.specifier),
        marker=str(parsed.marker) if parsed.marker is not None else None,
        raw=raw,
    )


def parse_pyproject(text: str, source: str = "pyproject.toml") -> UpstreamMetadata:
    """Parse a ``pyproject.toml`` into normalized upstream metadata."""
    try:
        document = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise UpstreamError(f"{source}: invalid TOML: {exc}") from exc

    project = document.get("project")
    if not isinstance(project, dict):
        raise UpstreamError(f"{source}: has no [project] table")

    name = project.get("name")
    if not isinstance(name, str) or not name:
        raise UpstreamError(f"{source}: [project] has no name")

    # A project that computes its dependencies at build time has none to read
    # here. Returning an empty list would look exactly like "declares nothing",
    # and swage would strip the recipe's requirements on that basis.
    dynamic = project.get("dynamic") or []
    for key in ("dependencies", "optional-dependencies"):
        if key in dynamic:
            raise UpstreamError(
                f"{source}: [project] declares {key} as dynamic, so it cannot be "
                "read statically; swage will not infer requirements"
            )

    return UpstreamMetadata(
        name=name,
        version=_optional_str(project.get("version"), "version", source),
        requires_python=_optional_str(
            project.get("requires-python"), "requires-python", source
        ),
        build_requires=_build_requires(document, source),
        dependencies=_requirements(
            project.get("dependencies") or [], "[project] dependencies", source
        ),
        optional_dependencies={
            extra: _requirements(
                values or [], f"[project] optional-dependencies.{extra}", source
            )
            for extra, values in (project.get("optional-dependencies") or {}).items()
        },
    )


def _build_requires(
    document: dict[str, Any], source: str
) -> tuple[UpstreamRequirement, ...] | None:
    """Read ``[build-system] requires``, keeping absent distinct from empty.

    The recipe's `host` section is reconciled against this, so "upstream said
    nothing" must not arrive at the planner looking like "upstream needs
    nothing" -- the second would empty a host section.
    """
    build_system = document.get("build-system")
    if build_system is None:
        return None
    if not isinstance(build_system, dict):
        raise UpstreamError(f"{source}: [build-system] is not a table")
    # PEP 518 makes `requires` mandatory once the table exists, so its absence
    # is a malformed file rather than a project that needs nothing to build.
    if "requires" not in build_system:
        raise UpstreamError(f"{source}: [build-system] has no requires")
    return _requirements(build_system["requires"], "[build-system] requires", source)


def _optional_str(value: Any, key: str, source: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise UpstreamError(f"{source}: [project] {key} is not a string")
    return value


def _requirements(
    values: Any, label: str, source: str
) -> tuple[UpstreamRequirement, ...]:
    """Parse a list of requirement strings. ``label`` names the table and key."""
    if not isinstance(values, list):
        raise UpstreamError(f"{source}: {label} is not a list")
    parsed: list[UpstreamRequirement] = []
    for value in values:
        if not isinstance(value, str):
            raise UpstreamError(f"{source}: {label} contains a non-string")
        try:
            parsed.append(parse_requirement(value))
        except UpstreamError as exc:
            raise UpstreamError(f"{source}: {label}: {exc}") from exc
    return tuple(parsed)
