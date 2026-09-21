"""What ESMF declares it needs, out of the two files that say so (v1 §3.6.6).

ESMF says which libraries, never which versions. The declaration is a join
across `build/common.mk`, which says what a toggle links, and the feedstock's
`recipe/build.sh`, which says which toggles are on. Which assignment a toggle
selects is a rule about which guard mentions it, not an evaluation of the
makefile. Order follows the build script. What this reader declares is `host`,
and it explains nothing conda-forge adds for its own reasons.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from .errors import UpstreamError
from .model import BUILD_SH, UpstreamMetadata, UpstreamRequirement

__all__ = [
    "BUILD_SH",
    "COMMON_MK",
    "VENDORED_PIO",
    "esmf_toggles",
    "parse_common_mk",
    "parse_esmf",
    "pio_version",
]

#: Where ESMF's makefile fragment lives inside the source archive.
COMMON_MK = "build/common.mk"

#: Where the vendored copy of ParallelIO states its own version, which is not a
#: bound on anything.
VENDORED_PIO = "src/Infrastructure/IO/PIO/ParallelIO/configure.ac"

#: `ESMF_NETCDF_LIBS = -lnetcdff -lnetcdf`, and the `:=` spelling beside it.
_LIBS = re.compile(r"^\s*ESMF_(?P<feature>[A-Z0-9]+)_LIBS\s*:?=\s*(?P<libs>.*?)\s*$")

#: `ifeq ($(ESMF_NETCDF),split)`, which is the guard that names a value.
_IFEQ = re.compile(
    r"^\s*ifeq\s*\(\s*\$\(ESMF_(?P<feature>[A-Z0-9]+)\)\s*,(?P<value>[^)]*)\)"
)

#: `ifdef ESMF_PIO`, which is the guard that asks only whether it is set.
_IFDEF = re.compile(r"^\s*ifdef\s+ESMF_(?P<feature>[A-Z0-9]+)\s*$")

#: A whole value made of nothing but literal `-lname` flags. Anything holding
#: `$(` is a shell-out or a filter and is not a declaration swage can read.
_LINK_FLAGS = re.compile(r"^-l[A-Za-z0-9._+-]+(?:\s+-l[A-Za-z0-9._+-]+)*$")

#: `export ESMF_NETCDF="split"`, in the feedstock's own build script. The
#: quotes are optional and both kinds occur in the same file.
_EXPORT = re.compile(
    r"^\s*export\s+ESMF_(?P<feature>[A-Z0-9]+)\s*=\s*"
    r"(?P<value>\"[^\"]*\"|'[^']*'|[^\s#]*)"
)

#: `AC_INIT(pio, 2.6.6)` in the vendored copy's configure script.
_AC_INIT = re.compile(r"^\s*AC_INIT\(\s*pio\s*,\s*(?P<version>[^)\s]+)\s*\)", re.M)


def esmf_toggles(build_sh: str) -> dict[str, str]:
    """The ``ESMF_*`` toggles the feedstock's build script sets, and to what:
    every ``export``, whatever branch it sits in (v1 §3.3.4), later winning.
    """
    found: dict[str, str] = {}
    for line in build_sh.splitlines():
        match = _EXPORT.match(line)
        if match is None:
            continue
        value = match.group("value").strip("\"'")
        if not value or "$" in value:
            # Set from another variable, so its value is not in this file.
            continue
        found[match.group("feature")] = value
    return found


def parse_common_mk(text: str, source: str = COMMON_MK) -> dict[str, dict[str, str]]:
    """Feature -> guard value -> the libraries linked under it. The guard value
    is the string an ``ifeq`` matched, or ``""`` for an ``ifdef``.
    """
    found: dict[str, dict[str, str]] = {}
    # The innermost guard naming an ESMF feature, and the feature it names.
    stack: list[tuple[str, str] | None] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(("ifeq", "ifneq", "ifdef", "ifndef")):
            stack.append(_guard(line))
            continue
        if stripped == "endif":
            if stack:
                stack.pop()
            continue
        match = _LIBS.match(line)
        if match is None:
            continue
        libs = match.group("libs")
        if not _LINK_FLAGS.match(libs):
            continue
        feature = match.group("feature")
        guard = next(
            (
                item
                for item in reversed(stack)
                if item is not None and item[0] == feature
            ),
            None,
        )
        if guard is None:
            continue
        found.setdefault(feature, {}).setdefault(guard[1], libs)
    if not found:
        raise UpstreamError(
            f"{source}: declares no libraries under any ESMF_* toggle\n"
            "  swage reads `ESMF_<toggle>_LIBS = -lname` assignments out of "
            "this file, and found none -- the makefile's shape has changed"
        )
    return found


def pio_version(configure_ac: str) -> str | None:
    """The version of the ParallelIO copy vendored in this ESMF release."""
    match = _AC_INIT.search(configure_ac)
    return match.group("version") if match is not None else None


def parse_esmf(
    common_mk: str,
    build_sh: str,
    link_map: Mapping[str, str],
    version: str | None = None,
    configure_ac: str | None = None,
    source: str = COMMON_MK,
) -> UpstreamMetadata:
    """What this ESMF release needs, given the toggles its feedstock sets.
    ``link_map`` turns a linker name into conda-forge's package; a library
    with no entry stops the feedstock.
    """
    declared = parse_common_mk(common_mk, source)
    toggles = esmf_toggles(build_sh)

    requirements: list[UpstreamRequirement] = []
    seen: set[str] = set()
    unmapped: list[str] = []
    for feature, value in toggles.items():
        by_value = declared.get(feature)
        if by_value is None:
            continue
        # The value-specific block if the makefile has one for this value, and
        # the "is it set at all" block otherwise.
        libs = by_value.get(value, by_value.get(""))
        if libs is None:
            continue
        for flag in libs.split():
            stem = f"lib{flag.removeprefix('-l')}"
            package = link_map.get(stem)
            if package is None:
                unmapped.append(f"{flag} (ESMF_{feature}={value})")
                continue
            if package in seen:
                continue
            seen.add(package)
            requirements.append(
                UpstreamRequirement(
                    name=package,
                    raw=(
                        f"{flag} in {COMMON_MK}, "
                        f"for ESMF_{feature}={value} in {BUILD_SH}"
                    ),
                )
            )
    if unmapped:
        raise UpstreamError(
            f"{source}: links libraries swage cannot name a package for\n"
            + "".join(f"    {item}\n" for item in unmapped)
            + "  add the library's stem to config/link-map.yaml, which says "
            "which conda-forge package publishes which library"
        )

    return UpstreamMetadata(
        conda_names=True,
        states_versions=False,
        name="esmf",
        version=version,
        # `host`, and nothing else: a makefile states what ESMF links, and a
        # compiled library's `run` section is conda-forge's own (v1 §3.6.6).
        build_requires=tuple(requirements),
        dependencies=(),
        # Both files, because neither is the declaration on its own (v1 §3.6.6).
        declared_in=f"{COMMON_MK} + {BUILD_SH}",
        notes=_notes(configure_ac, version),
    )


def _notes(configure_ac: str | None, version: str | None) -> tuple[str, ...]:
    """What to say about the ParallelIO version this release vendors: a note,
    not a bound, since the recipe pins `parallelio` by hand.
    """
    if configure_ac is None:
        return ()
    pio = pio_version(configure_ac)
    if pio is None:
        return ()
    release = f"ESMF {version}" if version else "this ESMF"
    return (
        f"{release} builds against ParallelIO {pio} ({VENDORED_PIO}); the "
        "recipe pins `parallelio` itself, so check the pin when this moves",
    )


def _guard(line: str) -> tuple[str, str] | None:
    """The ESMF feature this ``if`` line tests and the value it tests for, or
    None where it tests something else.
    """
    equals = _IFEQ.match(line)
    if equals is not None:
        return equals.group("feature"), equals.group("value").strip()
    defined = _IFDEF.match(line)
    if defined is not None:
        return defined.group("feature"), ""
    return None
