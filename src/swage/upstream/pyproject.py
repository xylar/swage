"""Read upstream metadata from a ``pyproject.toml`` (v1 §3.6). No network is
involved here.
"""

from __future__ import annotations

import configparser
import tomllib
from typing import Any

from packaging.requirements import InvalidRequirement, Requirement

from .errors import UpstreamError
from .model import EntryPoint, UpstreamMetadata, UpstreamRequirement, normalize_extra

__all__ = [
    "parse_build_requires",
    "parse_entry_points",
    "parse_entry_points_txt",
    "parse_pyproject",
    "parse_requirement",
]


def parse_requirement(raw: str) -> UpstreamRequirement:
    """Parse one PEP 508 requirement string."""
    try:
        parsed = Requirement(raw)
    except InvalidRequirement as exc:
        raise UpstreamError(f"cannot parse requirement {raw!r}: {exc}") from exc
    return UpstreamRequirement(
        name=parsed.name,
        # Sorted because PEP 508 parses extras into a set; normalized so the key
        # does not depend on the source.
        extras=tuple(sorted({normalize_extra(extra) for extra in parsed.extras})),
        specifier=str(parsed.specifier),
        marker=str(parsed.marker) if parsed.marker is not None else None,
        raw=raw,
    )


def parse_build_requires(
    text: str, source: str = "pyproject.toml"
) -> tuple[UpstreamRequirement, ...] | None:
    """Read only ``[build-system] requires`` out of a ``pyproject.toml``, which
    is readable independently of a ``[project]`` table (v1 §3.6.2).
    """
    return _build_requires(_load(text, source), source)


def parse_pyproject(text: str, source: str = "pyproject.toml") -> UpstreamMetadata:
    """Parse a ``pyproject.toml`` into normalized upstream metadata."""
    document = _load(text, source)

    project = document.get("project")
    if not isinstance(project, dict):
        raise UpstreamError(f"{source}: has no [project] table")

    name = project.get("name")
    if not isinstance(name, str) or not name:
        raise UpstreamError(f"{source}: [project] has no name")

    # A project that computes its dependencies at build time has none to read
    # here, and an empty list would look like "declares nothing".
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
        optional_dependencies=_optional_dependencies(project, source),
        entry_points=_entry_points(document, source),
    )


def parse_entry_points(
    text: str, source: str = "pyproject.toml"
) -> tuple[EntryPoint, ...] | None:
    """Read only the scripts out of a ``pyproject.toml``, for a backend whose
    table states them and whose sdist carries no `entry_points.txt`.
    """
    return _entry_points(_load(text, source), source)


def _entry_points(
    document: dict[str, Any], source: str
) -> tuple[EntryPoint, ...] | None:
    """``[project.scripts]`` and ``[project.gui-scripts]``, or poetry's table
    (DESIGN.md §6.3).

    `None` where the file cannot say: no table that states scripts, or a
    ``[project]`` table declaring either key `dynamic`. A table that names
    neither key states that there are none. GUI scripts are read into the
    same list.
    """
    project = document.get("project")
    if isinstance(project, dict):
        dynamic = project.get("dynamic") or []
        if "scripts" in dynamic or "gui-scripts" in dynamic:
            return None
        found: list[EntryPoint] = []
        for key in ("scripts", "gui-scripts"):
            table = project.get(key) or {}
            if not isinstance(table, dict):
                raise UpstreamError(f"{source}: [project] {key} is not a table")
            found.extend(
                _entry_point(name, target, f"[project] {key}", source)
                for name, target in table.items()
            )
        return tuple(found)

    tool = document.get("tool") or {}
    poetry = tool.get("poetry")
    if isinstance(poetry, dict):
        found = []
        for name, value in (poetry.get("scripts") or {}).items():
            # `{callable = ...}` is the long form of a console script;
            # `{reference = ..., type = "file"}` is a file poetry copies and no
            # entry point.
            target = value.get("callable") if isinstance(value, dict) else value
            if target is None:
                continue
            found.append(_entry_point(name, target, "[tool.poetry.scripts]", source))
        return tuple(found)
    flit = tool.get("flit")
    if isinstance(flit, dict) and isinstance(flit.get("metadata"), dict):
        return tuple(
            _entry_point(name, target, "[tool.flit.scripts]", source)
            for name, target in (flit.get("scripts") or {}).items()
        )
    return None


def _entry_point(name: Any, target: Any, table: str, source: str) -> EntryPoint:
    if not isinstance(name, str) or not isinstance(target, str) or not target:
        raise UpstreamError(f"{source}: {table} {name!r} names no callable")
    return EntryPoint(name=name.strip(), target=target.strip())


def parse_entry_points_txt(
    text: str, source: str = "entry_points.txt"
) -> tuple[EntryPoint, ...]:
    """Read the console and GUI scripts out of an ``entry_points.txt``
    (DESIGN.md §6.3). Every other group is somebody else's business; a
    line's trailing extras are dropped.
    """
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str  # type: ignore[assignment,method-assign]
    try:
        parser.read_string(text)
    except configparser.Error as exc:
        raise UpstreamError(
            f"{source}: cannot read as entry_points.txt: {exc}"
        ) from exc
    found = []
    for group in ("console_scripts", "gui_scripts"):
        if not parser.has_section(group):
            continue
        for name, target in parser.items(group):
            bare = target.split("[", 1)[0].strip()
            if not bare:
                raise UpstreamError(f"{source}: [{group}] {name!r} names no callable")
            found.append(EntryPoint(name=name.strip(), target=bare))
    return tuple(found)


def _load(text: str, source: str) -> dict[str, Any]:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise UpstreamError(f"{source}: invalid TOML: {exc}") from exc


def _optional_dependencies(
    project: dict[str, Any], source: str
) -> dict[str, tuple[UpstreamRequirement, ...]]:
    """Read ``[project.optional-dependencies]``, normalizing the extra names
    (PEP 685).
    """
    collected: dict[str, tuple[UpstreamRequirement, ...]] = {}
    seen: dict[str, str] = {}
    for extra, values in (project.get("optional-dependencies") or {}).items():
        normalized = normalize_extra(extra)
        # Two spellings of one extra is invalid metadata, and keeping the last
        # would drop the other's dependencies.
        if normalized in seen:
            raise UpstreamError(
                f"{source}: [project] optional-dependencies declares "
                f"{seen[normalized]!r} and {extra!r}, which are the same "
                f"extra once normalized to {normalized!r}"
            )
        seen[normalized] = extra
        collected[normalized] = _requirements(
            values or [], f"[project] optional-dependencies.{extra}", source
        )
    return collected


def _build_requires(
    document: dict[str, Any], source: str
) -> tuple[UpstreamRequirement, ...] | None:
    """Read ``[build-system] requires``, keeping absent distinct from empty
    (v1 §3.6.2).
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
