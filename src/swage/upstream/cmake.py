"""What a CMake project declares it needs, out of its `CMakeLists.txt` tree (v1
§3.6.7; DESIGN.md §6.2).

CMake says which packages, rarely which versions. A guard says how a package is
found, not whether it is needed, so a `find_package` swage cannot rule out still
counts; the guards it can read are `option(...)`, cache `set`s and the build
script's `-D` flags, evaluated three-valued. `REQUIRED` decides whether swage
proposes a line; an optional declaration is answered by `supported`/`skip`.
`cmake-map.yaml` maps the names, an entry with no value recording a name no
single package answers. The walk follows `add_subdirectory` and `include()`, and
below the top level only `REQUIRED` counts. What this reader declares is `host`.
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

#: The source-directory variables an `include()` path or a module-path entry is
#: written with. Anything else leaves a `${` behind and the path is declined.
_CURRENT_DIR = ("${CMAKE_CURRENT_SOURCE_DIR}", "${CMAKE_CURRENT_LIST_DIR}")
_ROOT_DIR = ("${CMAKE_SOURCE_DIR}", "${PROJECT_SOURCE_DIR}")

#: `-D ENABLE_MPI=ON`, in the feedstock's build script, wherever it appears.
_DEFINE = re.compile(r"-D\s*(?P<name>[A-Za-z_][A-Za-z0-9_]*)=(?P<value>[^\s\"'\\]*)")

#: The values CMake counts as false. A variable nobody has set is unknown, not
#: false.
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
        #: The minimum version the call asks for, or ``""``, which swage writes
        #: as a `>=`.
        self.version = version
        #: Whether any surviving call for this package said ``REQUIRED``.
        self.required = required
        #: Where in the file the first call for this package is, which is the
        #: position design-v1.md 6 orders the requirement by.
        self.line = line
        #: The archive-relative file that call is in: where a maintainer is
        #: sent.
        self.where = where
        #: Position in the walk, which orders a subdirectory's declarations
        #: after the top-level ones; `line` alone cannot.
        self.order = order

    def __repr__(self) -> str:  # pragma: no cover - debugging only
        return f"FindPackage({self.name!r}, {self.version!r}, {self.required!r})"


def cmake_definitions(build_script: str) -> dict[str, str]:
    """The `-D` variables the feedstock's build script sets, and to what: every
    one it mentions, whatever branch (v1 §3.3.4), later winning. A value
    carrying a shell substitution is dropped.
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

    A package named more than once is one entry: the strongest surviving call
    decides, and the first decides where it sits. ``tree`` is every
    `CMakeLists.txt` and `.cmake` module in the archive, keyed by path;
    without it only ``text`` is read.
    """
    variables = dict(definitions or {})
    found: dict[str, FindPackage] = {}
    counter = _Counter()
    # ``text`` rather than the tree's own copy, for a caller holding one file.
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

    ``variables`` and ``modules`` are shared down the walk and across
    siblings, which errs toward leaving a declaration standing. The
    `add_subdirectory` walk terminates because every step is strictly
    deeper; `include()` needs ``reading`` as a cycle guard.
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
                # An included file is read at the includer's depth.
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
    """A path with the source-directory variables this reader knows filled in;
    anything else keeps its `${` and is declined.
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
    finds a module by name.
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
    """The `.cmake` module an `include()` names, if the archive has one: a path
    beside the including file or from the root, or a bare name on
    `CMAKE_MODULE_PATH`. A name the archive lacks is one of CMake's own.
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
    A directory the archive lacks, or a path reaching back out of its own,
    falls out unresolved.
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

    ``cmake_map`` turns a `find_package` name into conda-forge's package, or
    into nothing where no single package answers; keyed and looked up in
    lower case; a name in neither state stops the feedstock. The required
    packages become `build_requires`. ``supported`` and ``skip`` answer the
    optional ones (v1 §3.3.9); anything in neither list is a note.
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
        states_versions=False,
        name=name,
        version=version,
        # `host`, and nothing else, for the reason design-v1.md 3.6.6 gives.
        build_requires=tuple(requirements),
        dependencies=(),
        # Both files, because neither is the declaration on its own (v1 §3.6.7).
        declared_in=f"{CMAKE_LISTS} + {BUILD_SH}",
        notes=_notes(
            optional,
            _stale(supported, skip, answered),
            name,
            version,
        ),
    )


def _raw(package: FindPackage, declared_required: bool) -> str:
    """Where upstream says so, in the words upstream used: a `supported` entry
    does not get to claim upstream wrote `REQUIRED`.
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
    """Answers this release gives nothing to answer: an entry naming a
    declaration that is no longer optional here, as `_check_extras` does.
    """
    return [answer for answer in (*supported, *skip) if answer.lower() not in answered]


def _notes(
    optional: list[str], stale: list[str], name: str, version: str | None
) -> tuple[str, ...]:
    """What to say about the packages upstream can use but does not require:
    a note, never a gate or a proposal (v1 §3.3.9).
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
    """Fold one surviving `find_package` call into what is known of its
    package. The first call keeps its file.
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
    """Record what an `option` or a cache `set` makes a variable's default: only
    at the top level, and only where the build script has not said otherwise.
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

    ``taken`` is True, False, or None for a guard swage cannot read; only a
    guard known to be off removes a declaration. An `else()` is true only
    where every branch before it was known false.
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
    """CMake's `if` grammar, as far as swage reads it: CMake's precedence,
    three-valued throughout.
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
        """The value of the next argument, or None where swage has no value. An
        unquoted argument is a variable name, and one this file does not set
        is unknown rather than its own text.
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
    """Every `name(arguments)` in the file, as (line, lowercased name, args),
    each argument with whether it was quoted. Comments come out first, so an
    `if` named in one cannot be read as opening.
    """
    text = _uncomment(text)
    for match in re.finditer(r"([A-Za-z_][A-Za-z0-9_]*)[ \t]*\(", text):
        arguments, end = _arguments(text, match.end())
        if end is None:
            continue
        yield text.count("\n", 0, match.start()) + 1, match.group(1).lower(), arguments


def _uncomment(text: str) -> str:
    """The same file with its comments blanked out, newlines and all kept, so
    line numbers survive. A `#` inside a quoted string is not a comment.
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
    """Split one command's arguments, stopping at the `)` that closes it,
    honoring quoting and nested parentheses.
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
