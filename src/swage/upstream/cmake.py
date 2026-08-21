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

**A package name is not a conda-forge package name**, and `config/cmake-map.yaml`
says which is which -- the third such table, beside `name-map.yaml` for PyPI
names and `link-map.yaml` for linker names, and deliberately not merged with
either. An entry there with **no value** says no single conda-forge package
answers the name -- `Threads` and `OpenMP` are CMake asking about the compiler,
`Doxygen` and `PkgConfig` are build tools where this reader declares `host`,
and `MPI` is a real dependency whose package the build variant picks. That is
how "looked at, and it is not a host dependency" gets recorded rather than
stopping a feedstock forever. A name in neither state does stop it.

**The top-level file, and no other.** A subdirectory's `CMakeLists.txt`
declares what that component needs, which is not the same claim: `proj`'s
`test/unit/CMakeLists.txt` wants GTest and `test/cli/CMakeLists.txt` wants a
Python interpreter, and neither belongs in `host`.

**What this reader declares is `host`**, for the reason DESIGN.md 3.6.6 gives:
a build system states what the project links, and a conda-forge `run` section
for a compiled library is run exports plus build-string variant pins, both of
them conda-forge's own reasons for a line.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping

from .errors import UpstreamError
from .model import UpstreamMetadata, UpstreamRequirement

__all__ = [
    "CMAKE_LISTS",
    "FindPackage",
    "cmake_definitions",
    "find_packages",
    "parse_cmake",
]

#: Where a CMake project's top-level declaration lives, by CMake's own rule.
CMAKE_LISTS = "CMakeLists.txt"

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

    def __init__(self, name: str, version: str, required: bool, line: int) -> None:
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
    text: str, definitions: Mapping[str, str] | None = None
) -> list[FindPackage]:
    """Every package this file declares, in the order it first names one.

    A package named more than once is one entry: `libgeotiff` asks for TIFF in
    config mode and then again in module mode, and `proj` reaches
    nlohmann_json through two branches of the same `if`. The strongest
    surviving call decides -- ``REQUIRED`` anywhere makes it required -- and
    the first one decides where it sits.
    """
    variables = dict(definitions or {})
    # One entry per open `if`, holding what swage makes of its current branch
    # and whether any earlier branch of the same `if` was true.
    stack: list[_Branch] = []
    found: dict[str, FindPackage] = {}
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
            _record(arguments, line, found)
    return sorted(found.values(), key=lambda package: package.line)


def parse_cmake(
    cmake_lists: str,
    build_script: str,
    cmake_map: Mapping[str, str | None],
    name: str,
    version: str | None = None,
    source: str = CMAKE_LISTS,
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
    `host` reconciles against. The optional ones become a note: upstream says
    it can use them and the recipe says nothing, and which of those two is
    right is a packaging decision about CI cost and downstream benefit that
    no metadata contains (DESIGN.md 3.3.9).
    """
    packages = find_packages(cmake_lists, cmake_definitions(build_script))
    if not packages:
        raise UpstreamError(
            f"{source}: declares no packages\n"
            "  swage reads `find_package(name)` calls out of this file, and "
            "found none -- either the project states its dependencies "
            "somewhere else, or this is not the file that states them"
        )

    requirements: list[UpstreamRequirement] = []
    optional: list[str] = []
    unmapped: list[str] = []
    for package in packages:
        if package.name.lower() not in cmake_map:
            unmapped.append(
                f"find_package({package.name}{' REQUIRED' if package.required else ''})"
            )
            continue
        conda = cmake_map[package.name.lower()]
        if conda is None:
            # Recorded in `cmake-map.yaml` as not being a package: CMake's way
            # of asking about the compiler, the toolchain or a build tool.
            continue
        if not package.required:
            optional.append(f"{conda} (find_package({package.name}))")
            continue
        requirements.append(
            UpstreamRequirement(
                name=conda,
                specifier=f">={package.version}" if package.version else "",
                raw=f"find_package({package.name} REQUIRED) in {CMAKE_LISTS}",
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
        name=name,
        version=version,
        # `host`, and nothing else, for the reason DESIGN.md 3.6.6 gives.
        build_requires=tuple(requirements),
        dependencies=(),
        notes=_notes(optional, name, version),
    )


def _notes(optional: list[str], name: str, version: str | None) -> tuple[str, ...]:
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
    if not optional:
        return ()
    release = f"{name} {version}" if version else name
    return (
        f"{release} can optionally use {', '.join(optional)}, declared in "
        f"{CMAKE_LISTS} without REQUIRED; the recipe decides whether "
        "conda-forge builds against them",
    )


def _record(
    arguments: list[tuple[str, bool]], line: int, into: dict[str, FindPackage]
) -> None:
    """Fold one surviving `find_package` call into what is known of its package."""
    if not arguments:
        return
    name = arguments[0][0]
    version = ""
    if len(arguments) > 1 and _VERSION.match(arguments[1][0]):
        version = arguments[1][0]
    required = any(argument == "REQUIRED" for argument, _ in arguments)
    existing = into.get(name)
    if existing is None:
        into[name] = FindPackage(name, version, required, line)
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
