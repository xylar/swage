"""Read upstream metadata from a setuptools ``setup.cfg`` (DESIGN.md §6.2).

v1 §3.6 declined a reader at one feedstock and said a second would reopen
it; weaviate-client 4.24.0 stopped publishing sdists, and its tag tarball
states its run dependencies nowhere else. Values are split the way
setuptools' ``config/setupcfg.py`` splits them, so the list is the one the
build installs. No network is involved here.
"""

from __future__ import annotations

import configparser

from .errors import UpstreamError
from .model import EntryPoint, UpstreamMetadata, UpstreamRequirement, normalize_extra
from .pyproject import parse_requirement

__all__ = ["parse_setup_cfg"]

#: setuptools' directives for a value it computes rather than states.
_DIRECTIVES = ("file:", "attr:")


def parse_setup_cfg(text: str, source: str = "setup.cfg") -> UpstreamMetadata | None:
    """Parse a ``setup.cfg`` into normalized upstream metadata.

    None where the file names no project, which makes it tool configuration
    (flake8, pytest) rather than a declaration. Where it does name one, an
    absent ``install_requires`` states that there are none: the caller has
    already checked that ``setup.py`` adds nothing.
    """
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(text)
    except configparser.Error as exc:
        raise UpstreamError(f"{source}: cannot read as setup.cfg: {exc}") from exc

    name = _option(parser, "metadata", "name")
    if not name:
        return None

    version = _option(parser, "metadata", "version")
    if version is not None and version.startswith(_DIRECTIVES):
        # `attr: pkg.__version__` states where the version is, not what it is.
        version = None

    entry_points: list[EntryPoint] = []
    for group in ("console_scripts", "gui_scripts"):
        value = _option(parser, "options.entry_points", group)
        for line in _split(value or "", ","):
            script, sep, target = line.partition("=")
            if not sep or not target.strip():
                raise UpstreamError(
                    f"{source}: [options.entry_points] {group} {line!r} "
                    "names no callable"
                )
            entry_points.append(
                EntryPoint(name=script.strip(), target=target.split("[")[0].strip())
            )

    return UpstreamMetadata(
        name=name,
        version=version,
        requires_python=_option(parser, "options", "python_requires"),
        dependencies=_requirements(
            _option(parser, "options", "install_requires"),
            "[options] install_requires",
            source,
        ),
        optional_dependencies=_extras(parser, source),
        entry_points=tuple(entry_points),
    )


def _option(parser: configparser.ConfigParser, section: str, key: str) -> str | None:
    """One value, under either spelling setuptools accepts for its key."""
    for spelling in (key, key.replace("_", "-")):
        if parser.has_option(section, spelling):
            return parser.get(section, spelling).strip()
    return None


def _split(value: str, separator: str) -> list[str]:
    """setuptools' ``_parse_list``: by line where there are several, else on
    ``separator``. Comment lines are dropped, as its requirement lists do.
    """
    chunks = value.splitlines() if "\n" in value else value.split(separator)
    return [
        chunk.strip()
        for chunk in chunks
        if chunk.strip() and not chunk.strip().startswith("#")
    ]


def _extras(
    parser: configparser.ConfigParser, source: str
) -> dict[str, tuple[UpstreamRequirement, ...]]:
    """``[options.extras_require]``, normalized under PEP 685 as
    ``pyproject.toml``'s are.
    """
    section = "options.extras_require"
    if not parser.has_section(section):
        return {}
    collected: dict[str, tuple[UpstreamRequirement, ...]] = {}
    seen: dict[str, str] = {}
    for extra, value in parser.items(section):
        normalized = normalize_extra(extra)
        if normalized in seen:
            raise UpstreamError(
                f"{source}: [{section}] declares {seen[normalized]!r} and "
                f"{extra!r}, which are the same extra once normalized to "
                f"{normalized!r}"
            )
        seen[normalized] = extra
        collected[normalized] = _requirements(value, f"[{section}] {extra}", source)
    return collected


def _requirements(
    value: str | None, label: str, source: str
) -> tuple[UpstreamRequirement, ...]:
    if value is None:
        return ()
    if value.strip().startswith(_DIRECTIVES):
        raise UpstreamError(
            f"{source}: {label} reads {value.strip()!r}, a file swage does not follow"
        )
    parsed = []
    for line in _split(value, ";"):
        try:
            parsed.append(parse_requirement(line))
        except UpstreamError as exc:
            raise UpstreamError(f"{source}: {label}: {exc}") from exc
    return tuple(parsed)
