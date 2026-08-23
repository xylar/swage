"""What a CMake project declares it needs, out of its top-level `CMakeLists.txt`.

The second reader for a feedstock whose upstream is not a python distribution,
and the first that is named for a build system rather than for a project. That
difference is the point: `esmf`'s reader is ESMF's rules, because a makefile is
not a metadata format, while `find_package(SQLite3 REQUIRED)` means the same
thing in every CMake project there is. 14 of the archives swage has fetched
carry a top-level `CMakeLists.txt` (DESIGN.md 3.6.7).

**CMake says which packages, rarely which versions.** Across those 14 archives
there are 64 `find_package` calls and exactly two carry a version -- the same
`gdal` line at two releases of the same archive. So this reader answers "which
packages, and where does upstream say so", the same question `esmf`'s does, and
the recipe's own bounds stay the recipe's.

**A guard says how a package is found, not whether it is needed.** This is the
one rule that had to be worked out against real files rather than assumed, and
it comes out the opposite way round from `esmf`'s. `libgeotiff` writes::

    FIND_PACKAGE(TIFF NO_MODULE QUIET)      # config mode, may miss
    if (NOT TIFF_FOUND)
      FIND_PACKAGE(TIFF REQUIRED)           # module mode, must not
    endif ()

`TIFF_FOUND` is set by the call above it, so nothing outside CMake can evaluate
that `if` -- and libgeotiff plainly requires libtiff. `netcdf-fortran` guards
its `HDF5` on a `#define` it greps out of the installed `netcdf_meta.h`, and
requires HDF5 just the same. So a `find_package` swage cannot rule out **still
counts**: the declaration stands, and only a guard swage can read *and* which
is false takes it away.

**Which guards swage can read.** The variables set by `option(...)` and
`set(... CACHE ...)` in this file, and the ones the feedstock's own build
script passes as `-D`. That is the `esmf` join again -- `common.mk` says what a
toggle implies and `recipe/build.sh` says which toggles are on -- in the form
CMake gives it::

    option(ENABLE_TIFF "..." ON)  ->  if(ENABLE_TIFF)  is true
    option(WITH_ZLIB  "..." OFF)  ->  if(WITH_ZLIB)    is false, and
                                      find_package(ZLIB REQUIRED) under it is
                                      not part of this build

Everything else is unknown, and unknown leaves the declaration standing. swage
does not run `cmake` and does not implement one: `if(MSVC)`, `if(TARGET
PROJ::proj)` and `if(NOT netCDF_LIBRARIES)` are questions about a configure
run that has not happened.

**`REQUIRED` is upstream distinguishing a hard dependency from an optional
one**, which python metadata cannot express at all, and it is what decides
whether swage proposes a line. `proj` needs both halves of the rule at once::

    if(NLOHMANN_JSON_ORIGIN STREQUAL "external")
      find_package(nlohmann_json REQUIRED)
    ...
    else()
      find_package(nlohmann_json QUIET)

`NLOHMANN_JSON_ORIGIN` defaults to `auto`, so the `REQUIRED` call is ruled out
and what is left is a `QUIET` one: PROJ vendors nlohmann/json unless it finds a
copy, and conda-forge's recipe does not carry it. Read either half alone and
swage would have proposed a dependency the recipe is right not to have.

**An optional declaration is answered by config, not by this file.**
`find_package(X)` without `REQUIRED` is upstream saying the project builds
either way, so which conda-forge does is a packaging decision no file upstream
contains -- `supported` says this build takes it, `skip` says it does not, and
a name in neither is reported at every run. The netcdf family is what the keys
were written for: `netcdf-fortran` and `netcdf-cxx4` write
`FIND_PACKAGE(netCDF QUIET)` and fall back to a `FIND_LIBRARY` with a
`FATAL_ERROR` behind it, so upstream requires netCDF and never writes
`REQUIRED`.

**A package name is not a conda-forge package name**, and `config/cmake-map.yaml`
says which is which -- the third such table, beside `name-map.yaml` for PyPI
names and `link-map.yaml` for linker names, and deliberately not merged with
either. An entry there with **no value** says no single conda-forge package
answers the name -- `Threads` and `OpenMP` are CMake asking about the compiler,
`Doxygen` and `PkgConfig` are build tools where this reader declares `host`,
and `MPI` is a real dependency whose package the build variant picks. That is
how "looked at, and it is not a host dependency" gets recorded rather than
stopping a feedstock forever. A name in neither state does stop it.

**Down through `add_subdirectory`, and only `REQUIRED` below the top.** A
project of any size states its dependencies where it uses them: `tiledb`'s
top-level file names two packages, both of them test-only, while
`tiledb/CMakeLists.txt` and the directories under it name twenty the library
genuinely links. Reaching those means following `add_subdirectory` -- but
following it naively is worse than not following it at all, because a
subdirectory's `CMakeLists.txt` declares what *that component* needs, which is
not the same claim as what the package needs.

The rule that separates them was measured rather than assumed, over every
cached archive carrying a top-level `CMakeLists.txt`. **A guard is not enough**:
`proj` reaches `test/unit/CMakeLists.txt` through an unguarded
`add_subdirectory(test)`, and `tiledb` guards its test tree on a
`TILEDB_TESTS` that `option(...)` defaults ON, so both trees are part of the
build swage reads. What separates them is that everything those trees add is
**optional** -- `find_package(GTest)`, `find_package(Python3)`,
`find_package(Doxygen)` -- while every one of `tiledb`'s twenty is `REQUIRED`.

So below the top level a declaration counts only where upstream wrote
`REQUIRED`. That is the same distinction §3.3.9 rests on, applied one level
down: at the top of the project an optional `find_package` is a packaging
decision `supported`/`skip` can answer, but in a subdirectory it is a
component's local nicety and there is no one to ask. `REQUIRED` in a directory
this build compiles is upstream saying the build fails without it, which is
exactly the claim `host` makes.

Measured against the fleet, the rule leaves `proj`, `parallelio`, `geotiff`,
`netcdf-fortran`, `netcdf-cxx4` and `cprnc` reading exactly what they read
before, and it is the difference between `tiledb` declaring two packages and
declaring twenty-two.

**A package found below the top level is quoted with the file it is in**, for
the reason DESIGN.md gives for `declared_in`: a maintainer sent to look at
`CMakeLists.txt` for a line that is in `tiledb/sm/compressors/CMakeLists.txt`
has been sent to the wrong file.

**What this reader declares is `host`**, for the reason DESIGN.md 3.6.6 gives:
a build system states what the project links, and a conda-forge `run` section
for a compiled library is run exports plus build-string variant pins, both of
them conda-forge's own reasons for a line.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping, Sequence

from .errors import UpstreamError
from .model import BUILD_SH, UpstreamMetadata, UpstreamRequirement

__all__ = [
    "CMAKE_LISTS",
    "CMAKE_MODULE",
    "FindPackage",
    "cmake_definitions",
    "find_packages",
    "parse_cmake",
]

#: Where a CMake project's top-level declaration lives, by CMake's own rule.
CMAKE_LISTS = "CMakeLists.txt"

#: What a CMake module is called, which is the other kind of file this reader
#: opens. A project may state its dependencies in one and `include()` it.
CMAKE_MODULE = ".cmake"

#: The source-directory variables an `include()` path or a module-path entry
#: is written with, and what each means to this reader. The two "current"
#: forms mean the directory of the file being read; the two project-wide ones
#: mean the archive root, which is where the top-level `CMakeLists.txt` sits.
#: Anything else leaves a `${` behind and the path is declined unresolved.
_CURRENT_DIR = ("${CMAKE_CURRENT_SOURCE_DIR}", "${CMAKE_CURRENT_LIST_DIR}")
_ROOT_DIR = ("${CMAKE_SOURCE_DIR}", "${PROJECT_SOURCE_DIR}")

#: `-D ENABLE_MPI=ON`, in the feedstock's build script. Read wherever it
#: appears, including inside a shell variable the script builds up and passes
#: later: `netcdf-fortran` writes
#: `export PARALLEL="-DENABLE_PARALLEL4=ON ..."`, and finding the flag is not
#: the same as running the script that would pass it.
_DEFINE = re.compile(r"-D\s*(?P<name>[A-Za-z_][A-Za-z0-9_]*)=(?P<value>[^\s\"'\\]*)")

#: The values CMake counts as false. Anything else is true, and a variable
#: nobody has set is neither -- swage reads it as unknown rather than as
#: CMake's false, because a value can reach it from a place this file is not.
_FALSE = frozenset({"", "0", "off", "no", "false", "n", "ignore", "notfound"})

#: A `find_package` argument that is a version rather than a keyword.
_VERSION = re.compile(r"^[0-9][0-9.]*$")

#: `if` operators that take an argument on each side. swage answers only the
#: string comparison; the rest are here so the parser knows how far to skip.
_BINARY = frozenset(
    {
        "STREQUAL",
        "STRLESS",
        "STRGREATER",
        "STRLESS_EQUAL",
        "STRGREATER_EQUAL",
        "EQUAL",
        "LESS",
        "GREATER",
        "LESS_EQUAL",
        "GREATER_EQUAL",
        "MATCHES",
        "IN_LIST",
        "PATH_EQUAL",
        "VERSION_EQUAL",
        "VERSION_LESS",
        "VERSION_GREATER",
        "VERSION_LESS_EQUAL",
        "VERSION_GREATER_EQUAL",
        "IS_NEWER_THAN",
    }
)

#: `if` operators taking one argument, whose answer swage does not have.
_UNARY_UNKNOWN = frozenset(
    {"EXISTS", "COMMAND", "TARGET", "TEST", "POLICY", "IS_DIRECTORY", "IS_ABSOLUTE"}
)


class FindPackage:
    """One `find_package` call, and what the file around it says about it."""

    def __init__(
        self,
        name: str,
        version: str,
        required: bool,
        line: int,
        where: str = CMAKE_LISTS,
        order: int = 0,
    ) -> None:
        #: The package name as CMake spells it, which is not conda-forge's.
        self.name = name
        #: The minimum version the call asks for, or ``""``. CMake reads a
        #: version here as "at least this, and compatible with it", so a bound
        #: swage writes from one is a `>=`.
        self.version = version
        #: Whether any surviving call for this package said ``REQUIRED``.
        self.required = required
        #: Where in the file the first call for this package is, which is the
        #: position DESIGN.md 6 orders the requirement by.
        self.line = line
        #: The archive-relative file that call is in. The top-level
        #: `CMakeLists.txt` for most, a subdirectory's for one reached through
        #: `add_subdirectory`, and it is the file a maintainer gets sent to.
        self.where = where
        #: Position in the walk, which orders a subdirectory's declarations
        #: after the top-level ones and in the order the build reaches them.
        #: `line` alone cannot: two files both have a line 40.
        self.order = order

    def __repr__(self) -> str:  # pragma: no cover - debugging only
        return f"FindPackage({self.name!r}, {self.version!r}, {self.required!r})"


def cmake_definitions(build_script: str) -> dict[str, str]:
    """The `-D` variables the feedstock's build script sets, and to what.

    Every one it mentions, whatever branch it sits in -- the same reading
    `esmf`'s toggles get, and for the same reason: which branch runs is a fact
    about the build variant, and swage has no variant axis (DESIGN.md 3.3.4).
    Later wins.

    A value carrying a shell substitution is dropped rather than guessed at:
    `proj` passes `-D BUILD_TESTING=${BUILD_TESTING}`, whose value is decided
    by a shell `if` that swage will not evaluate.
    """
    found: dict[str, str] = {}
    for match in _DEFINE.finditer(build_script):
        value = match.group("value")
        if "$" in value or not value:
            continue
        found[match.group("name")] = value
    return found


def find_packages(
    text: str,
    definitions: Mapping[str, str] | None = None,
    tree: Mapping[str, str] | None = None,
) -> list[FindPackage]:
    """Every package this project declares, in the order the build reaches one.

    A package named more than once is one entry: `libgeotiff` asks for TIFF in
    config mode and then again in module mode, and `proj` reaches
    nlohmann_json through two branches of the same `if`. The strongest
    surviving call decides -- ``REQUIRED`` anywhere makes it required -- and
    the first one decides where it sits.

    ``tree`` is every `CMakeLists.txt` and every `.cmake` module in the
    archive, keyed by its path relative to the archive's top-level directory.
    Given it, the walk follows `add_subdirectory` into the files it names and
    counts the ``REQUIRED`` calls it finds there, and follows `include()` into
    the modules it names. Without it only ``text`` is read, which is what a
    caller holding one file rather than an archive has to work with.
    """
    variables = dict(definitions or {})
    found: dict[str, FindPackage] = {}
    counter = _Counter()
    # ``text`` rather than the tree's own copy of it: a caller holding one
    # file and no archive passes only the first, and the two are the same
    # bytes for the caller that passes both.
    _walk(
        text,
        CMAKE_LISTS,
        0,
        variables,
        found,
        tree or {},
        counter,
        [],
        set(),
    )
    return sorted(found.values(), key=lambda package: package.order)


class _Counter:
    """A position in the walk, handed out in the order declarations are read."""

    def __init__(self) -> None:
        self.next = 0

    def take(self) -> int:
        self.next += 1
        return self.next


def _walk(
    text: str,
    path: str,
    depth: int,
    variables: dict[str, str],
    found: dict[str, FindPackage],
    tree: Mapping[str, str],
    counter: _Counter,
    modules: list[str],
    reading: set[str],
) -> None:
    """Read one CMake file, then the subdirectories it adds and the modules it
    includes.

    ``variables`` is shared down the walk, which is CMake's own scoping: a
    subdirectory inherits what the directory above it set. It is shared
    *across* siblings too, which CMake does not do -- and that is deliberate,
    because the alternative is a copy per subdirectory and a variable this
    reader cannot see is already read as unknown, which leaves a declaration
    standing rather than removing one.

    The `add_subdirectory` walk terminates because it names a directory
    *below* the one it stands in, so every step is strictly deeper and the
    archive is finite. Reaching one directory from two places at all takes an
    absolute path or a `..`, and `_subdirectory` declines both. A directory
    added twice is read twice and folds into the same entries, which is
    ordinary -- `parallelio` adds `examples/c` twice from one file.

    **`include()` has no such argument and needs `reading`.** It names a file
    anywhere in the archive, so two modules that include each other are a
    cycle CMake itself expects -- `include_guard()` exists for exactly that --
    and following one without a guard does not terminate. ``reading`` holds
    the modules open above this call and stops the second entry.

    ``modules`` is `CMAKE_MODULE_PATH`, in the order the project appended to
    it, which is how `include(BuildOptions)` finds a file called
    `BuildOptions.cmake`. Shared down the walk exactly as ``variables`` is,
    and for the same reason.
    """
    # One entry per open `if`, holding what swage makes of its current branch
    # and whether any earlier branch of the same `if` was true.
    stack: list[_Branch] = []
    for line, command, arguments in _commands(text):
        if command == "if":
            stack.append(_Branch(_truth(arguments, variables)))
        elif command == "elseif":
            if stack:
                stack[-1].next_branch(_truth(arguments, variables))
        elif command == "else":
            if stack:
                stack[-1].next_branch(True)
        elif command == "endif":
            if stack:
                stack.pop()
        elif command in {"option", "set"}:
            _define(command, arguments, variables, stack)
        elif command == "find_package":
            if any(branch.taken is False for branch in stack):
                # A guard swage can read, and it is off for this build.
                continue
            if depth and not any(argument == "REQUIRED" for argument, _ in arguments):
                # Optional, and in a component's own file: nobody at the
                # feedstock can answer for it. See this module's docstring.
                continue
            _record(arguments, line, found, path, counter)
        elif command == "list":
            _append_module_path(arguments, modules, path)
        elif command == "add_subdirectory":
            if any(branch.taken is False for branch in stack):
                continue
            child = _subdirectory(path, arguments, tree)
            if child is not None:
                _walk(
                    tree[child],
                    child,
                    depth + 1,
                    variables,
                    found,
                    tree,
                    counter,
                    modules,
                    reading,
                )
        elif command == "include":
            if any(branch.taken is False for branch in stack):
                continue
            module = _included(path, arguments, tree, modules)
            if module is not None and module not in reading:
                # An included file is pasted in where it is named, so it is
                # read at the *includer's* depth: a module the top-level file
                # includes states top-level declarations, optional ones
                # included.
                _walk(
                    tree[module],
                    module,
                    depth,
                    variables,
                    found,
                    tree,
                    counter,
                    modules,
                    reading | {module},
                )


def _resolve(text: str, path: str) -> str:
    """A path with the source-directory variables this reader knows filled in.

    Everything else is left as written, so a path naming a variable swage
    cannot see keeps its `${` and the caller declines it rather than guessing
    at half a path.
    """
    directory = (
        path[: -len(CMAKE_LISTS)].rstrip("/")
        if path.endswith(CMAKE_LISTS)
        else path.rsplit("/", 1)[0]
        if "/" in path
        else ""
    )
    for variable in _CURRENT_DIR:
        text = text.replace(variable, directory or ".")
    for variable in _ROOT_DIR:
        text = text.replace(variable, ".")
    return text.replace("./", "", 1) if text.startswith("./") else text


def _append_module_path(
    arguments: list[tuple[str, bool]], modules: list[str], path: str
) -> None:
    """Record a `list(APPEND CMAKE_MODULE_PATH ...)`, which is how `include()`
    finds a module by name rather than by path.

    Only `APPEND`, and only onto that one variable. `list` does a dozen other
    things and none of them decides where a module is looked for.
    """
    words = [argument for argument, _ in arguments]
    if len(words) < 3 or words[0] != "APPEND" or words[1] != "CMAKE_MODULE_PATH":
        return
    for entry in words[2:]:
        resolved = _resolve(entry, path).rstrip("/")
        if "${" in resolved or resolved.startswith("/"):
            continue
        if resolved not in modules:
            modules.append(resolved)


def _included(
    path: str,
    arguments: list[tuple[str, bool]],
    tree: Mapping[str, str],
    modules: list[str],
) -> str | None:
    """The `.cmake` module an `include()` names, if the archive has one.

    Two spellings, both of which the fleet writes. A path -- `include(
    cmake/dependencies.cmake)` -- is looked for beside the including file and
    then from the archive root. A bare name -- `include(BuildOptions)` -- is
    looked for as `<name>.cmake` on `CMAKE_MODULE_PATH`, which is what
    `tiledb` and `igraph` both rely on.

    A name the archive does not carry is one of CMake's own modules --
    `include(CheckSymbolExists)` ships with CMake and declares nothing about
    this project -- and falls out here, the same way `_subdirectory` declines
    a directory the archive does not carry.
    """
    if not arguments:
        return None
    named = _resolve(arguments[0][0], path)
    if "${" in named or named.startswith("/"):
        return None
    parent = path[: -len(CMAKE_LISTS)] if path.endswith(CMAKE_LISTS) else ""
    if named.endswith(CMAKE_MODULE):
        candidates = [f"{parent}{named}", named]
    else:
        candidates = [f"{directory}/{named}{CMAKE_MODULE}" for directory in modules]
        candidates += [f"{parent}{named}{CMAKE_MODULE}", f"{named}{CMAKE_MODULE}"]
    for candidate in candidates:
        if candidate in tree:
            return candidate
    return None


def _subdirectory(
    path: str, arguments: list[tuple[str, bool]], tree: Mapping[str, str]
) -> str | None:
    """The `CMakeLists.txt` an `add_subdirectory` names, if the archive has it.

    A directory the archive does not carry is one CMake would fetch or
    generate -- `tiledb` adds `test/unit/${googletest_SOURCE_DIR}`, which
    exists only after a configure run has downloaded it -- and there is
    nothing to read.

    A path reaching back out of its own directory falls out the same way,
    unresolved: `a/../b/CMakeLists.txt` is not a key the archive has, so the
    lookup below declines it without a rule of its own. CMake allows such a
    path and three archives in the fleet write one, all three inside a test or
    example tree that declares nothing this reader would take, so normalizing
    them would be code with no measured call for it.
    """
    if not arguments:
        return None
    directory = arguments[0][0]
    if "${" in directory or directory.startswith("/"):
        return None
    parent = path[: -len(CMAKE_LISTS)]
    child = f"{parent}{directory}/{CMAKE_LISTS}"
    return child if child in tree else None


def parse_cmake(
    cmake_lists: str,
    build_script: str,
    cmake_map: Mapping[str, str | None],
    name: str,
    version: str | None = None,
    source: str = CMAKE_LISTS,
    supported: Sequence[str] = (),
    skip: Sequence[str] = (),
    tree: Mapping[str, str] | None = None,
) -> UpstreamMetadata:
    """What this release needs, given the `-D` flags its feedstock passes.

    ``cmake_map`` turns a `find_package` name into the package conda-forge
    publishes it in, or into nothing where no single package answers the name.
    It is keyed in lower case and looked up that way, because CMake projects do
    not agree on one spelling -- `netcdf-fortran` writes `netCDF`, `cprnc`
    writes `NetCDF` and `moab` writes `NETCDF`, all meaning `libnetcdf`. A name
    in neither state stops the feedstock rather than resolving to whatever
    looks closest, which is the same allowlist rule every other table in
    `config/` follows.

    The required packages become `build_requires`, which is what a recipe's
    `host` reconciles against.

    ``supported`` and ``skip`` are the feedstock's answer to the optional ones
    -- the `find_package` names, matched without regard to case for the reason
    ``cmake_map`` is. An optional declaration is upstream saying the project
    builds either way, so which conda-forge does is a packaging decision that
    no file upstream answers (DESIGN.md 3.3.9); ``supported`` says this build
    takes it and makes it a requirement like any other, ``skip`` says it does
    not and puts that decision on the record. Anything in neither list stays a
    note, which is what makes a newly optional dependency impossible to miss.
    """
    packages = find_packages(cmake_lists, cmake_definitions(build_script), tree)
    if not packages:
        raise UpstreamError(
            f"{source}: declares no packages\n"
            "  swage reads `find_package(name)` calls out of this file, and "
            "found none -- either the project states its dependencies "
            "somewhere else, or this is not the file that states them"
        )

    taken = {answer.lower() for answer in supported}
    declined = {answer.lower() for answer in skip}
    answered: set[str] = set()

    requirements: list[UpstreamRequirement] = []
    optional: list[str] = []
    unmapped: list[str] = []
    for package in packages:
        if package.name.lower() not in cmake_map:
            unmapped.append(
                f"find_package({package.name}"
                f"{' REQUIRED' if package.required else ''})"
                f" in {package.where}"
            )
            continue
        conda = cmake_map[package.name.lower()]
        if conda is None:
            # Recorded in `cmake-map.yaml` as not being a package: CMake's way
            # of asking about the compiler, the toolchain or a build tool.
            continue
        required = package.required
        if not required:
            if package.name.lower() in taken:
                answered.add(package.name.lower())
                required = True
            elif package.name.lower() in declined:
                answered.add(package.name.lower())
                continue
            else:
                optional.append(f"{conda} (find_package({package.name}))")
                continue
        requirements.append(
            UpstreamRequirement(
                name=conda,
                specifier=f">={package.version}" if package.version else "",
                raw=_raw(package, package.required),
            )
        )
    if unmapped:
        raise UpstreamError(
            f"{source}: names packages swage cannot map to conda-forge\n"
            + "".join(f"    {item}\n" for item in unmapped)
            + "  add the name to config/cmake-map.yaml, which says which "
            "conda-forge package a `find_package` name means -- and which "
            "names mean no package at all"
        )

    return UpstreamMetadata(
        conda_names=True,
        name=name,
        version=version,
        # `host`, and nothing else, for the reason DESIGN.md 3.6.6 gives.
        build_requires=tuple(requirements),
        dependencies=(),
        # Both files, because neither is the declaration on its own:
        # `CMakeLists.txt` says what a guard implies and `build.sh` says which
        # `-D` flags this build passes. `azure-uamqp-c` is where that is
        # starkest -- left alone it declares nothing at all.
        declared_in=f"{CMAKE_LISTS} + {BUILD_SH}",
        notes=_notes(
            optional,
            _stale(supported, skip, answered),
            name,
            version,
        ),
    )


def _raw(package: FindPackage, declared_required: bool) -> str:
    """Where upstream says so, in the words upstream used.

    A `supported` entry does not get to claim upstream wrote `REQUIRED` when
    it did not. `netcdf-fortran` is the whole reason the key exists -- it
    writes `FIND_PACKAGE(netCDF QUIET)` and falls back to a `FIND_LIBRARY`
    with a `FATAL_ERROR` behind it -- so quoting a `REQUIRED` back at a
    maintainer who went and opened the file would be swage inventing the
    evidence for its own proposal.
    """
    if declared_required:
        return f"find_package({package.name} REQUIRED) in {package.where}"
    return (
        f"find_package({package.name}) in {package.where}, which this "
        "feedstock's config lists as supported"
    )


def _stale(
    supported: Sequence[str], skip: Sequence[str], answered: set[str]
) -> list[str]:
    """Answers this release gives nothing to answer.

    An entry naming a declaration that is no longer optional here says
    something false about the release, and it says it silently: upstream
    dropping a `find_package`, or promoting one to `REQUIRED`, leaves the
    config still listing it and nothing looking. The same reason `_check_extras`
    exists for the key this one is modeled on.
    """
    return [answer for answer in (*supported, *skip) if answer.lower() not in answered]


def _notes(
    optional: list[str], stale: list[str], name: str, version: str | None
) -> tuple[str, ...]:
    """What to say about the packages upstream can use but does not require.

    Not a gate, and not a proposal. `find_package(X)` without `REQUIRED` is
    upstream saying the project builds either way, so whether conda-forge
    carries X is a packaging decision nothing in this file answers -- the same
    shape as an upstream extra, and the same answer: swage reports it and a
    person decides (DESIGN.md 3.3.9).

    Worth saying at all because it is the half of the declaration a maintainer
    cannot get from the recipe. A new optional dependency in a new release is
    exactly the thing this reader exists to surface.
    """
    release = f"{name} {version}" if version else name
    notes = []
    if optional:
        notes.append(
            f"{release} can optionally use {', '.join(optional)}, declared in "
            f"{CMAKE_LISTS} without REQUIRED; the recipe decides whether "
            "conda-forge builds against them"
        )
    if stale:
        notes.append(
            f"config answers {', '.join(sorted(stale))} for this feedstock, "
            f"and {release} declares no optional find_package of that name; "
            "drop the entry, or check whether upstream now requires it"
        )
    return tuple(notes)


def _record(
    arguments: list[tuple[str, bool]],
    line: int,
    into: dict[str, FindPackage],
    where: str,
    counter: _Counter,
) -> None:
    """Fold one surviving `find_package` call into what is known of its package.

    The first call for a package keeps its file, not the strongest one: the
    file is where a maintainer is being sent to read the declaration, and the
    first is the one the build reaches first.
    """
    if not arguments:
        return
    name = arguments[0][0]
    version = ""
    if len(arguments) > 1 and _VERSION.match(arguments[1][0]):
        version = arguments[1][0]
    required = any(argument == "REQUIRED" for argument, _ in arguments)
    existing = into.get(name)
    if existing is None:
        into[name] = FindPackage(name, version, required, line, where, counter.take())
        return
    existing.required = existing.required or required
    existing.version = existing.version or version


def _define(
    command: str,
    arguments: list[tuple[str, bool]],
    variables: dict[str, str],
    stack: list[_Branch],
) -> None:
    """Record what an `option` or a cache `set` makes a variable's default.

    Only at the top level of the file, and only where the build script has not
    already said otherwise: a `-D` flag is what the build actually passes, and
    a default is what happens when nothing does. An assignment inside an `if`
    is skipped whatever swage makes of that `if` -- it is a value computed
    during a configure run rather than a default declared for one.
    """
    if stack or not arguments:
        return
    name = arguments[0][0]
    if name in variables:
        return
    if command == "option":
        # `option(NAME "doc" [value])`, and CMake's default when the value is
        # left out is OFF.
        variables[name] = arguments[2][0] if len(arguments) > 2 else "OFF"
        return
    words = [argument for argument, _ in arguments]
    if "CACHE" not in words or len(arguments) < 2:
        # A plain `set` is an assignment rather than a default, and swage has
        # no way to know which of several ran.
        return
    value = arguments[1][0]
    if "${" not in value:
        variables[name] = value


class _Branch:
    """One open `if`, and what swage makes of the branch it is now in.

    ``taken`` is True, False, or None for a guard swage cannot read -- and
    None is what keeps a declaration standing, since only a guard that is
    *known* to be off removes one.

    An `else()` is only true where every branch before it was known false, so
    the three states have to survive the whole chain: `proj`'s `else()` reaches
    its `find_package(nlohmann_json QUIET)` because the two branches above it
    both compare a variable whose default this file states.
    """

    def __init__(self, taken: bool | None) -> None:
        self.taken = taken
        self.settled = taken is True
        self.unreadable = taken is None

    def next_branch(self, taken: bool | None) -> None:
        if self.unreadable:
            # Somewhere above, a branch swage could not read; any branch after
            # it might be the one that runs.
            self.taken = None
            return
        if self.settled:
            self.taken = False
            return
        self.taken = taken
        self.settled = taken is True
        self.unreadable = taken is None


def _truth(
    arguments: list[tuple[str, bool]], variables: Mapping[str, str]
) -> bool | None:
    """What an `if` condition comes to, or None where swage cannot say."""
    parser = _Condition(arguments, variables)
    try:
        answer = parser.expression()
    except _Unreadable:
        return None
    return answer if parser.done else None


class _Unreadable(Exception):
    """This condition asks something swage has no answer to."""


class _Condition:
    """CMake's `if` grammar, as far as swage reads it.

    Precedence is CMake's: `OR` binds loosest, then `AND`, then `NOT`, then
    the comparisons. Three-valued throughout, so an unknown operand does not
    make the whole condition unknown -- `if(WIN32 AND WITH_ZLIB)` is false
    whatever `WIN32` turns out to be, and that is the answer that removes a
    declaration correctly.
    """

    def __init__(
        self, arguments: list[tuple[str, bool]], variables: Mapping[str, str]
    ) -> None:
        self.arguments = arguments
        self.variables = variables
        self.at = 0

    @property
    def done(self) -> bool:
        return self.at == len(self.arguments)

    def expression(self) -> bool | None:
        answer = self.conjunction()
        while self._word() == "OR":
            self.at += 1
            right = self.conjunction()
            answer = _or(answer, right)
        return answer

    def conjunction(self) -> bool | None:
        answer = self.term()
        while self._word() == "AND":
            self.at += 1
            right = self.term()
            answer = _and(answer, right)
        return answer

    def term(self) -> bool | None:
        word = self._word()
        if word == "NOT":
            self.at += 1
            answer = self.term()
            return None if answer is None else not answer
        if word == "(":
            self.at += 1
            answer = self.expression()
            if self._word() != ")":
                raise _Unreadable
            self.at += 1
            return answer
        if word == "DEFINED":
            self.at += 1
            name = self._word()
            self.at += 1
            # True where this file or the build script says so; unknown
            # otherwise, since a value can reach a variable from elsewhere.
            return True if name in self.variables else None
        if word in _UNARY_UNKNOWN:
            self.at += 2
            return None
        return self.comparison()

    def comparison(self) -> bool | None:
        left = self._operand()
        word = self._word()
        if word not in _BINARY:
            return _truthy(left)
        self.at += 1
        right = self._operand()
        if word != "STREQUAL":
            return None
        if left is None or right is None:
            return None
        return left == right

    def _operand(self) -> str | None:
        """The value of the next argument, or None where swage has no value.

        A quoted argument is its own text, with `${NAME}` filled in where this
        file states it. An unquoted one is a variable name, and CMake's rule
        that an unset one falls back to its own text is not followed here: a
        variable this file does not set is one swage has no answer for, not
        one it can read as a string.
        """
        if self.at >= len(self.arguments):
            raise _Unreadable
        text, quoted = self.arguments[self.at]
        self.at += 1
        if quoted or "${" in text:
            return _expand(text, self.variables)
        return self.variables.get(text)

    def _word(self) -> str | None:
        if self.at >= len(self.arguments):
            return None
        text, quoted = self.arguments[self.at]
        return None if quoted else text


def _expand(text: str, variables: Mapping[str, str]) -> str | None:
    """``"${NLOHMANN_JSON_ORIGIN}"`` as its value, where this file states one."""
    if "${" not in text:
        return text
    answer = text
    for match in re.finditer(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", text):
        value = variables.get(match.group(1))
        if value is None:
            return None
        answer = answer.replace(match.group(0), value)
    return None if "${" in answer else answer


def _truthy(value: str | None) -> bool | None:
    if value is None:
        return None
    lowered = value.lower()
    return not (lowered in _FALSE or lowered.endswith("-notfound"))


def _and(left: bool | None, right: bool | None) -> bool | None:
    if left is False or right is False:
        return False
    if left is None or right is None:
        return None
    return True


def _or(left: bool | None, right: bool | None) -> bool | None:
    if left is True or right is True:
        return True
    if left is None or right is None:
        return None
    return False


def _commands(text: str) -> Iterator[tuple[int, str, list[tuple[str, bool]]]]:
    """Every `name(arguments)` in the file, as (line, lowercased name, args).

    Each argument comes back with whether it was quoted, which `if` needs: a
    quoted argument is a string and an unquoted one is a variable name.

    Comments come out first, and that is not a tidying step. `proj` explains
    its own `if(NLOHMANN_JSON_ORIGIN ...)` block in a comment that names
    another `if`, and a scanner reading those as commands leaves an `if` open
    that never closes -- so every `option` below it looks like one set inside
    a conditional, and every default this reader depends on goes missing.
    """
    text = _uncomment(text)
    for match in re.finditer(r"([A-Za-z_][A-Za-z0-9_]*)[ \t]*\(", text):
        arguments, end = _arguments(text, match.end())
        if end is None:
            continue
        yield text.count("\n", 0, match.start()) + 1, match.group(1).lower(), arguments


def _uncomment(text: str) -> str:
    """The same file with its comments blanked out, newlines and all kept.

    Line numbers have to survive, because they are what orders the
    requirements (DESIGN.md 6), so a comment becomes spaces rather than
    nothing. A `#` inside a quoted string is not a comment.
    """
    out = list(text)
    at = 0
    while at < len(text):
        char = text[at]
        if char == '"':
            at += 1
            while at < len(text) and text[at] != '"':
                at += 2 if text[at] == "\\" else 1
            at += 1
            continue
        if char == "#":
            while at < len(text) and text[at] != "\n":
                out[at] = " "
                at += 1
            continue
        at += 1
    return "".join(out)


def _arguments(text: str, start: int) -> tuple[list[tuple[str, bool]], int | None]:
    """Split one command's arguments, stopping at the `)` that closes it.

    Quoting and nested parentheses both have to be honored here rather than by
    a regular expression: `if(NOT (A AND B))` nests, and `set(X "a)b")` does
    not close. Comments are already gone by this point.
    """
    arguments: list[tuple[str, bool]] = []
    word: list[str] = []
    quoted = False
    depth = 0
    at = start
    while at < len(text):
        char = text[at]
        if char == '"':
            at += 1
            value: list[str] = []
            while at < len(text) and text[at] != '"':
                if text[at] == "\\" and at + 1 < len(text):
                    value.append(text[at + 1])
                    at += 2
                    continue
                value.append(text[at])
                at += 1
            arguments.append(("".join(value), True))
            at += 1
            quoted = True
            continue
        if char in " \t\r\n":
            if word:
                arguments.append(("".join(word), False))
                word = []
            quoted = False
            at += 1
            continue
        if char == "(":
            if word:
                arguments.append(("".join(word), False))
                word = []
            arguments.append(("(", False))
            depth += 1
            at += 1
            continue
        if char == ")":
            if word:
                arguments.append(("".join(word), False))
                word = []
            if depth == 0:
                return arguments, at + 1
            arguments.append((")", False))
            depth -= 1
            at += 1
            continue
        if quoted:
            # Text run on from a closing quote, as in `"a"b`; CMake joins
            # them and nothing swage reads writes it, so drop the tail.
            at += 1
            continue
        word.append(char)
        at += 1
    return arguments, None
