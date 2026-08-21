"""What ESMF declares it needs, out of the two files that say so.

The first reader for a feedstock whose upstream is not a python distribution,
and the shape of the problem is different enough to be worth stating before
the code.

**ESMF says which libraries, never which versions.** `build/common.mk` is 4,640
lines and carries no version constraint at all -- no minimum netCDF, no
supported range -- and neither does any of the 259 `.tex` files of the User's
Guide. What it declares is a set of *toggles*, each naming the libraries to
link when that toggle is on::

    ifeq ($(ESMF_NETCDF),split)
    ifneq ($(origin ESMF_NETCDF_LIBS), environment)
    ESMF_NETCDF_LIBS = -lnetcdff -lnetcdf

So this reader answers "which packages, and where does upstream say so", which
is the question a maintainer coming back to the feedstock after a year
actually has. The version half of reconciliation has nothing to reconcile
against, and the recipe's own bounds stay the recipe's (DESIGN.md 3.6.5).

**The declaration is a join across two files, and one of them is the
feedstock's.** `common.mk` says what a toggle implies; `recipe/build.sh` says
which toggles are on. Neither file is the declaration by itself: read alone,
`common.mk` offers eleven optional libraries and the recipe takes two of them.
That is not an ESMF peculiarity -- a CMake project's `option(...)` blocks are
set by `-D` flags in the same build script -- so any reader for a compiled
feedstock will meet it.

**Which assignment a toggle selects.** The value-specific block if the makefile
has one, and the "is it set at all" block otherwise::

    ESMF_NETCDF=split    -> ifeq ($(ESMF_NETCDF),split)   -> -lnetcdff -lnetcdf
    ESMF_PIO=external    -> ifdef ESMF_PIO                -> -lpioc

That is a rule about which guard *mentions the toggle*, not an evaluation of
the makefile. swage does not run `make` and does not implement one: a guard
naming something else -- `ifneq ($(origin ESMF_NETCDF_LIBS), environment)`, which
asks whether the caller overrode the variable -- is passed over rather than
guessed at. An assignment whose value is not literal `-l` flags is skipped for
the same reason; the `nc-config` path builds its list by running a program, and
swage will not execute upstream code to find out what it would say.

**What this reader does not explain, and must not.** `hdf5` appears **zero
times** in `common.mk`: it reaches ESMF through netCDF, and the recipe names it
to pin the mpi variant. `openssh` is OpenMPI's launcher. Both are conda-forge's
own reasons for a line, they are what `add_requirements` is for, and a reader
that invented a declaration for them would be doing the thing G1 exists to
prevent.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from .errors import UpstreamError
from .model import UpstreamMetadata, UpstreamRequirement

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

#: The feedstock's own build script, which is the other half of the
#: declaration: it says which of `common.mk`'s toggles are on.
BUILD_SH = "recipe/build.sh"

#: Where the vendored copy of ParallelIO states its own version. ESMF builds
#: this copy when `ESMF_PIO=internal`; conda-forge sets `external` and links
#: the packaged one instead, so the version here is not a bound on anything --
#: it is what ESMF develops and tests against, and it moves between releases.
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
    """The ``ESMF_*`` toggles the feedstock's build script sets, and to what.

    Every ``export``, whatever branch it sits in. `esmf`'s script sets
    ``ESMF_COMM`` three times in one if/elif chain, once per mpi variant, and
    ``ESMF_PIO`` inside ``if [[ "$mpi" != "nompi" ]]``. Which branch runs is a
    fact about the *variant*, and swage has no variant axis (DESIGN.md 3.3.4)
    -- so the toggles are read as the set the feedstock can turn on, and the
    condition on any resulting line is the recipe's own, blessed in config by
    `variant_conditions`.

    Later wins, which matters only for a toggle set unconditionally and then
    overridden; nothing in the script does that today.
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
    """Feature -> guard value -> the libraries linked under it.

    The guard value is the string an ``ifeq`` matched, or ``""`` for an
    ``ifdef`` guard, which is what `esmf_toggles`' answer is looked up against.
    """
    found: dict[str, dict[str, str]] = {}
    # The innermost guard naming an ESMF feature, and the feature it names.
    # A stack rather than one value: the `_LIBS` assignments sit two or three
    # `if`s deep, and only the ones naming a feature say anything.
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

    ``link_map`` turns a linker name into the package conda-forge publishes it
    in. A library with no entry stops the feedstock rather than resolving to
    whatever looks closest, which is the same allowlist rule the recipe-owned
    templates follow.
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
        name="esmf",
        version=version,
        # `host`, and nothing else. `common.mk` states what ESMF *links*,
        # which is a fact about building it; a makefile has no notion of a
        # runtime dependency and ESMF never states one. What ends up in a
        # conda-forge `run` section for a compiled library is decided by the
        # host packages' run exports, plus whatever build-string pins the
        # recipe adds to hold a variant -- both conda-forge's own reasons for
        # a line, and both `add_requirements`. A reader that copied this list
        # into `run` would be inventing a declaration to explain lines
        # somebody else's convention put there.
        build_requires=tuple(requirements),
        dependencies=(),
        notes=_notes(configure_ac, version),
    )


def _notes(configure_ac: str | None, version: str | None) -> tuple[str, ...]:
    """What to say about the ParallelIO version this release vendors.

    Not a bound, and that is the whole reason it is a note. conda-forge pins
    `parallelio` by hand -- 2.6.3 against a vendored 2.6.2, then 2.6.6 against
    a vendored 2.6.6, then 2.6.9 against a vendored 2.6.6 -- so the recipe's
    pin has tracked the vendored version without ever equalling it, and no
    reader will produce it. What a reader can do is say what upstream now
    carries, at the one moment somebody is looking at a version bump. It moved
    at ESMF 8.8.1 and will move again.
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
    """The ESMF feature this ``if`` line tests and the value it tests for.

    None where it tests something else, which is most of them:
    ``ifneq ($(origin ESMF_NETCDF_LIBS), environment)`` asks whether the caller
    overrode the variable and says nothing about what ESMF needs.
    """
    equals = _IFEQ.match(line)
    if equals is not None:
        return equals.group("feature"), equals.group("value").strip()
    defined = _IFDEF.match(line)
    if defined is not None:
        return defined.group("feature"), ""
    return None
