"""Load and layer the quirks database (v1 §4; DESIGN.md §5).

Three layers, defaults, family, feedstock, merged most-specific-wins. Mappings
that feed provenance stay an ordered stack of layers so a lookup can report
which file supplied the answer (v1 §3.2).
"""

from __future__ import annotations

import fnmatch
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Generic, TypeVar

from pydantic import BaseModel, TypeAdapter, ValidationError

from ._yaml import load_yaml_document
from .errors import ConfigError
from .schema import (
    BuiltEverywhere,
    Defaults,
    DynamicPolicy,
    EntryPointsPolicy,
    ExtrasAsOutputs,
    Family,
    Feedstock,
    NotPackaged,
    Output,
    Override,
    Quirks,
    RecipeOwned,
    RemovalPolicy,
    RunConstraint,
    SourceVersionPolicy,
    TestMatrixPolicy,
    TrustLevel,
    TrustList,
    Upstream,
    VariantCondition,
)

__all__ = [
    "AddedRequirement",
    "Additions",
    "ConfigTree",
    "FeedstockConfig",
    "Layered",
    "MappingLayer",
    "find_config_root",
    "load_config",
]

_V = TypeVar("_V")
_M = TypeVar("_M", bound=BaseModel)

_NAME_MAP_ADAPTER = TypeAdapter(dict[str, str])
_CMAKE_MAP_ADAPTER = TypeAdapter(dict[str, str | None])


@dataclass(frozen=True)
class MappingLayer(Generic[_V]):
    """One layer of a layered lookup, labeled with the file it came from."""

    source: str
    entries: Mapping[str, _V]


@dataclass(frozen=True)
class AddedRequirement:
    """A conda-forge-only requirement line, why it is there, and who asked.

    The source travels with the text so the line has a `Provenance`;
    ``temporary`` marks a line re-checked at every update (v1 §3.3.14).
    """

    text: str
    source: str
    reason: str = ""
    temporary: bool = False


@dataclass(frozen=True)
class Additions:
    """What config adds to a section, recipe-wide and per output (v1 §4)."""

    #: section name -> entries that apply to every output.
    every: Mapping[str, tuple[AddedRequirement, ...]] = field(default_factory=dict)
    #: output name -> section name -> entries for that output alone.
    per_output: Mapping[str, Mapping[str, tuple[AddedRequirement, ...]]] = field(
        default_factory=dict
    )

    def get(self, section: str, output: str = "") -> tuple[AddedRequirement, ...]:
        """Everything that applies to ``section`` when planning ``output``."""
        return self.every.get(section, ()) + self.per_output.get(output, {}).get(
            section, ()
        )


@dataclass(frozen=True)
class Layered(Generic[_V]):
    """Ordered layers, most specific first; first match wins."""

    layers: tuple[MappingLayer[_V], ...] = ()

    def lookup(self, key: str) -> tuple[_V, str] | None:
        """Return ``(value, source)`` for ``key``, or ``None`` if unset."""
        for layer in self.layers:
            if key in layer.entries:
                return layer.entries[key], layer.source
        return None

    def __contains__(self, key: str) -> bool:
        return any(key in layer.entries for layer in self.layers)


@dataclass(frozen=True)
class FeedstockConfig:
    """The quirks database as it applies to one feedstock."""

    feedstock: str
    family: str | None
    #: What the family's glob matched: the `{slug}` a family's `upstream.tag`
    #: and `upstream.metadata` templates are written against (v1 §4). Falls back
    #: to the feedstock's own name.
    slug: str
    #: Why nobody maintains this feedstock, or None. Only a feedstock's own
    #: file sets it, so it is read off ``entry`` rather than layered.
    unmaintained: str | None
    trust: TrustLevel
    #: The file that states this feedstock's rung, or None where nothing does
    #: (v1 §5.4).
    trust_source: str | None
    #: Where a rung for this feedstock is written: the file that states it, or
    #: the one it would go in (v1 §5.4).
    trust_file: str
    upstream: Upstream | None
    extras_as_outputs: ExtrasAsOutputs | None
    outputs: Mapping[str, Output]
    name_map: Layered[str]
    #: Library stem -> conda package (v1 §3.6.6). Global rather than layered.
    link_map: Mapping[str, str]
    #: `find_package` name -> conda package (v1 §3.6.7), keyed in lower case. A
    #: key with a value of None says no single conda-forge package answers.
    cmake_map: Mapping[str, str | None]
    embedded_extras: Layered[tuple[str, ...]]
    #: The union of every layer's allowlist, not the most specific one: a
    #: feedstock adding a local expression must not un-bless the global ones.
    recipe_owned: RecipeOwned
    #: Names whose unexplained lines swage removes (design-v1.md 3.3.7).
    retire: frozenset[str]
    #: `if:` conditions that select a conda-forge build variant rather than
    #: narrowing what upstream declares (design-v1.md 3.3.4).
    variant_conditions: tuple[VariantCondition, ...]
    #: The conda-forge-only lines to add, each carrying the file that asked
    #: for it. Provenance needs the file, not just the line.
    add_requirements: Additions
    #: conda package names whose platform and machine markers describe
    #: upstream's wheel matrix (v1 §3.3.4.1).
    built_everywhere: Mapping[str, BuiltEverywhere]
    #: conda package name -> what its `run_constraints` entry tracks (v1
    #: §3.3.9). Merged most-specific-wins.
    run_constraints: Mapping[str, RunConstraint]
    #: conda package name -> a bound the recipe adds beyond upstream's (v1
    #: §3.3.14). Merged most-specific-wins.
    constraints: Mapping[str, Override]
    #: The same, for bounds that must be re-checked at every update rather
    #: than outliving the reason they were added for (design-v1.md 3.3.14).
    temporary_constraints: Mapping[str, Override]
    #: conda package name -> the bound one noarch package states in place of
    #: contradicting upstream declarations (v1 §3.3.2). Merged
    #: most-specific-wins.
    overruled_constraints: Mapping[str, Override]
    #: Upstream name -> why conda-forge has no such package and this feedstock
    #: ships without it (design-v1.md 3.2.3). Merged most-specific-wins.
    not_packaged: Mapping[str, NotPackaged]
    removals: RemovalPolicy
    dynamic_dependencies: DynamicPolicy
    test_matrix: TestMatrixPolicy
    entry_points: EntryPointsPolicy
    source_versions: SourceVersionPolicy
    #: What `host` is built with where upstream declares no `[build-system]`
    #: at all -- PEP 517's implicit setuptools backend (design-v1.md 3.6.2).
    default_build_requires: tuple[str, ...] = ()
    #: Build requirements a cross build takes from the host prefix, so a
    #: recipe never repeats them in `build` (design-v1.md 3.3.6.1).
    pure_python_build_tools: tuple[str, ...] = ()


class ConfigTree:
    """The validated quirks database."""

    def __init__(
        self,
        root: Path,
        defaults: Defaults,
        name_map: Mapping[str, str],
        families: Mapping[str, Family],
        feedstocks: Mapping[str, Feedstock],
        link_map: Mapping[str, str] | None = None,
        cmake_map: Mapping[str, str | None] | None = None,
        trust: TrustList | None = None,
        notes: tuple[str, ...] = (),
    ) -> None:
        self.root = root
        self.defaults = defaults
        #: One line per old policy spelling a file still uses (DESIGN.md
        #: §5.3), for the terminal.
        self.notes = notes
        self.name_map = name_map
        self.trust = trust if trust is not None else TrustList()
        self.listed_rungs = self.trust.rungs
        self.link_map = link_map or {}
        self.cmake_map = cmake_map or {}
        self.families = families
        self.feedstocks = feedstocks

    def family_for(self, feedstock: str) -> Family | None:
        """Which family owns ``feedstock``.

        An explicit ``family:`` wins; any other family whose glob also
        matches is an ambiguity (v1 §12).
        """
        declared: Family | None = None
        entry = self.feedstocks.get(feedstock)
        if entry is not None and entry.family is not None:
            declared = self.families[entry.family]

        matched = [
            family
            for family in self.families.values()
            if fnmatch.fnmatch(feedstock, family.match.feedstock)
        ]
        if declared is not None:
            others = [f.family for f in matched if f.family != declared.family]
            if others:
                raise ConfigError(
                    self.root / "feedstocks" / f"{feedstock}.yaml",
                    f"declares family '{declared.family}' but also matches "
                    f"{', '.join(sorted(others))}; a feedstock belongs to one family",
                )
            return declared
        if len(matched) > 1:
            names = ", ".join(sorted(f.family for f in matched))
            raise ConfigError(
                self.root, f"feedstock '{feedstock}' matches several families: {names}"
            )
        return matched[0] if matched else None

    def _rung(
        self, feedstock: str, entry: Feedstock | None, family: Family | None
    ) -> tuple[TrustLevel, str | None, str]:
        """This feedstock's trust rung, where it is stated, and where one goes.

        A family is not one of the layers (v1 §5.4). The file a rung would go
        in is the feedstock's own where it has one, and `trust.yaml` where
        it does not.
        """
        own = f"config/feedstocks/{feedstock}.yaml"
        if entry is not None and entry.trust is not None:
            return entry.trust, own, own
        listed = self.listed_rungs.get(feedstock)
        if listed is not None:
            return listed, "config/trust.yaml", "config/trust.yaml"
        return (
            self.defaults.trust,
            None,
            own if entry is not None else "config/trust.yaml",
        )

    def for_feedstock(self, feedstock: str) -> FeedstockConfig:
        """Resolve the layered config for ``feedstock``. A feedstock with no file
        of its own resolves to its family's, `trust.yaml` and the defaults.
        """
        entry = self.feedstocks.get(feedstock)
        family = self.family_for(feedstock)
        rung, trust_source, trust_file = self._rung(feedstock, entry, family)

        name_map_layers: list[MappingLayer[str]] = []
        extras_layers: list[MappingLayer[tuple[str, ...]]] = []
        outputs: dict[str, Output] = {}
        if family is not None:
            source = f"config/families/{family.family}.yaml"
            name_map_layers.append(MappingLayer(source, family.name_map))
            extras_layers.append(MappingLayer(source, family.embedded_extras))
            outputs.update(family.outputs)
        if entry is not None:
            source = f"config/feedstocks/{feedstock}.yaml"
            name_map_layers.insert(0, MappingLayer(source, entry.name_map))
            extras_layers.insert(0, MappingLayer(source, entry.embedded_extras))
            outputs.update(entry.outputs)
        name_map_layers.append(MappingLayer("config/name-map.yaml", self.name_map))

        # Unioned rather than overridden: a feedstock naming one local
        # expression must not drop `pin_subpackage` and `python`.
        recipe_owned = self.defaults.recipe_owned
        for layer in (family, entry):
            if layer is not None and layer.recipe_owned is not None:
                recipe_owned = layer.recipe_owned.extend(recipe_owned)

        # Unioned for the same reason.
        retire = frozenset(
            name
            for layer in (family, entry)
            if layer is not None
            for name in layer.retire
        )

        # Unioned as well.
        variant_conditions = tuple(
            condition
            for layer in (family, entry)
            if layer is not None
            for condition in layer.variant_conditions
        )

        # Also unioned. Both keys land in one list, each entry knowing which key
        # it came from (v1 §3.3.14); `add_requirements` first at each layer.
        added: dict[str, list[AddedRequirement]] = {"host": [], "run": []}
        per_output: dict[str, dict[str, list[AddedRequirement]]] = {}
        for layer, source in (
            (family, f"config/families/{family.family}.yaml" if family else ""),
            (entry, f"config/feedstocks/{feedstock}.yaml"),
        ):
            if layer is None:
                continue
            for block, for_now in (
                (layer.add_requirements, False),
                (layer.temporary_requirements, True),
            ):
                if block is None:
                    continue
                for section, lines in added.items():
                    lines.extend(
                        AddedRequirement(
                            added_line.line, source, added_line.reason, for_now
                        )
                        for added_line in block.section(section)
                    )
                for output, additions in block.outputs.items():
                    sections = per_output.setdefault(output, {"host": [], "run": []})
                    for section, lines in sections.items():
                        lines.extend(
                            AddedRequirement(
                                added_line.line, source, added_line.reason, for_now
                            )
                            for added_line in additions.section(section)
                        )

        # Spelled out rather than routed through `_first`: `Upstream` is a
        # union, and a type variable inferred from one collapses to the shared
        # base.
        upstream: Upstream | None = None
        for layer in (entry, family):
            if layer is not None and layer.upstream is not None:
                upstream = layer.upstream
                break

        built_everywhere: dict[str, BuiltEverywhere] = {}
        run_constraints: dict[str, RunConstraint] = {}
        constraints: dict[str, Override] = {}
        temporary: dict[str, Override] = {}
        overruled: dict[str, Override] = {}
        not_packaged: dict[str, NotPackaged] = {}
        for layer in (family, entry):
            if layer is not None:
                built_everywhere.update(layer.built_everywhere)
                run_constraints.update(layer.run_constraints)
                constraints.update(layer.constraints)
                temporary.update(layer.temporary_constraints)
                overruled.update(layer.overruled_constraints)
                not_packaged.update(layer.not_packaged)

        return FeedstockConfig(
            feedstock=feedstock,
            family=family.family if family is not None else None,
            slug=_slug(feedstock, family.match.feedstock if family else None),
            unmaintained=entry.unmaintained if entry is not None else None,
            trust=rung,
            trust_source=trust_source,
            trust_file=trust_file,
            upstream=upstream,
            extras_as_outputs=_first(entry, family, lambda q: q.extras_as_outputs),
            outputs=outputs,
            name_map=Layered(tuple(name_map_layers)),
            link_map=self.link_map,
            cmake_map=self.cmake_map,
            embedded_extras=Layered(tuple(extras_layers)),
            recipe_owned=recipe_owned,
            retire=retire,
            variant_conditions=variant_conditions,
            add_requirements=Additions(
                every={k: tuple(v) for k, v in added.items()},
                per_output={
                    output: {k: tuple(v) for k, v in sections.items()}
                    for output, sections in per_output.items()
                },
            ),
            built_everywhere=built_everywhere,
            run_constraints=run_constraints,
            constraints=constraints,
            temporary_constraints=temporary,
            overruled_constraints=overruled,
            not_packaged=not_packaged,
            removals=(
                _first(entry, family, lambda q: q.removals) or self.defaults.removals
            ),
            source_versions=(
                _first(entry, family, lambda q: q.source_versions)
                or self.defaults.source_versions
            ),
            test_matrix=(
                _first(entry, family, lambda q: q.test_matrix)
                or self.defaults.test_matrix
            ),
            entry_points=(
                _first(entry, family, lambda q: q.entry_points)
                or self.defaults.entry_points
            ),
            dynamic_dependencies=(
                _first(entry, family, lambda q: q.dynamic_dependencies)
                or self.defaults.dynamic_dependencies
            ),
            default_build_requires=self.defaults.default_build_requires,
            pure_python_build_tools=self.defaults.pure_python_build_tools,
        )


def _slug(feedstock: str, pattern: str | None) -> str:
    """The part of ``feedstock`` its family's glob matched with ``*``."""
    if pattern is None or pattern.count("*") != 1:
        return feedstock
    prefix, suffix = pattern.split("*")
    if not feedstock.startswith(prefix) or not feedstock.endswith(suffix):
        return feedstock
    return feedstock[len(prefix) : len(feedstock) - len(suffix)]


def _first(
    entry: Feedstock | None,
    family: Family | None,
    get: Callable[[Quirks], _V | None],
) -> _V | None:
    """Most specific non-``None`` value, feedstock before family."""
    for layer in (entry, family):
        if layer is not None:
            value = get(layer)
            if value is not None:
                return value
    return None


def find_config_root(start: Path | None = None) -> Path:
    """Locate the quirks database by walking up from ``start``: swage is run
    from a checkout of its own repo.
    """
    start = (start or Path.cwd()).resolve()
    for directory in (start, *start.parents):
        candidate = directory / "config"
        if (candidate / "defaults.yaml").is_file():
            return candidate
    raise ConfigError(
        start, "no config/defaults.yaml found here or in any parent directory"
    )


def load_config(root: Path | None = None) -> ConfigTree:
    """Validate every file in the quirks database and return it."""
    root = root if root is not None else find_config_root()
    if not root.is_dir():
        raise ConfigError(root, "config root does not exist")

    notes: list[str] = []
    defaults = _load_model(root / "defaults.yaml", Defaults, notes)
    name_map = _load_name_map(root / "name-map.yaml")
    # The same shape and loader for library names (v1 §3.6.6). Not layered:
    # which package publishes a library is a fact about conda-forge.
    link_map = _load_name_map(root / "link-map.yaml")
    # And for `find_package` names (v1 §3.6.7), keyed without regard to case.
    cmake_map = _load_cmake_map(root / "cmake-map.yaml")
    # Optional, unlike `defaults.yaml`: every test fixture is a database with
    # nothing to say about any rung.
    trust = _load_trust(root / "trust.yaml", notes)

    families: dict[str, Family] = {}
    for path in _yaml_files(root / "families"):
        family = _load_model(path, Family, notes)
        _require_stem(path, family.family, "family")
        families[family.family] = family

    feedstocks: dict[str, Feedstock] = {}
    for path in _yaml_files(root / "feedstocks"):
        feedstock = _load_model(path, Feedstock, notes)
        _require_stem(path, feedstock.feedstock, "feedstock")
        if feedstock.family is not None and feedstock.family not in families:
            raise ConfigError(path, f"unknown family '{feedstock.family}'")
        feedstocks[feedstock.feedstock] = feedstock

    for name, rung in trust.rungs.items():
        entry = feedstocks.get(name)
        if entry is not None and entry.trust is not None:
            raise ConfigError(
                root / "trust.yaml",
                f"'{name}' is listed under '{rung}' here and sets "
                f"'trust: {entry.trust}' in its own file, which wins -- so this "
                "entry says something that is not true of it. State the rung in "
                "one place",
            )

    tree = ConfigTree(
        root,
        defaults,
        name_map,
        families,
        feedstocks,
        link_map,
        cmake_map,
        trust,
        tuple(notes),
    )
    # Ambiguous family membership is a load-time error for every feedstock we
    # know by name; feedstocks without a file are checked when they resolve.
    for name in feedstocks:
        tree.family_for(name)
    return tree


def _load_trust(path: Path, notes: list[str]) -> TrustList:
    """`trust.yaml`, or an empty list where the database has no such file."""
    if not path.is_file():
        return TrustList()
    return _load_model(path, TrustList, notes)


def _yaml_files(directory: Path) -> Iterator[Path]:
    if not directory.is_dir():
        return
    yield from sorted(directory.glob("*.yaml"))


def _require_stem(path: Path, value: str, field: str) -> None:
    if value != path.stem:
        raise ConfigError(
            path, f"{field} is '{value}' but the file is named {path.name}"
        )


#: v1's spelling of a policy value and v2's (DESIGN.md §5.3). The old one is
#: accepted through the v2.0 cycle, and each file that uses it gets a note.
_OLD_SPELLINGS: Mapping[str, Mapping[str, str]] = {
    "dynamic_dependencies": {"trust": "auto"},
    "source_versions": {"never": "review"},
}


def _load_model(path: Path, model: type[_M], notes: list[str]) -> _M:
    if not path.is_file():
        raise ConfigError(path, "required config file is missing")
    document = load_yaml_document(path)
    for key, spellings in _OLD_SPELLINGS.items():
        old = document.data.get(key)
        if isinstance(old, str) and old in spellings:
            document.data[key] = spellings[old]
            line = document.line_for((key,))
            location = str(path) if line is None else f"{path}:{line}"
            notes.append(
                f"{location}: '{key}: {old}' is now spelled '{key}: {spellings[old]}'"
            )
    try:
        return model.model_validate(document.data)
    except ValidationError as exc:
        error = exc.errors()[0]
        location = ".".join(str(part) for part in error["loc"]) or "<document>"
        raise ConfigError(
            path, f"{location}: {error['msg']}", document.line_for(error["loc"])
        ) from exc


def _load_cmake_map(path: Path) -> dict[str, str | None]:
    """`cmake-map.yaml`, lower-cased on the way in so lookups ignore case.

    A key with no value is kept: it says no single conda-forge package
    answers. Two entries differing only in case are an error.
    """
    if not path.is_file():
        return {}
    document = load_yaml_document(path)
    try:
        entries = _CMAKE_MAP_ADAPTER.validate_python(document.data)
    except ValidationError as exc:
        error = exc.errors()[0]
        location = ".".join(str(part) for part in error["loc"]) or "<document>"
        raise ConfigError(
            path, f"{location}: {error['msg']}", document.line_for(error["loc"])
        ) from exc
    folded: dict[str, str | None] = {}
    seen: dict[str, str] = {}
    for name, package in entries.items():
        key = name.lower()
        if key in seen:
            raise ConfigError(
                path,
                f"'{name}' and '{seen[key]}' are the same entry: this file is "
                "looked up without regard to case",
            )
        seen[key] = name
        folded[key] = package
    return folded


def _load_name_map(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    document = load_yaml_document(path)
    try:
        return _NAME_MAP_ADAPTER.validate_python(document.data)
    except ValidationError as exc:
        error = exc.errors()[0]
        location = ".".join(str(part) for part in error["loc"]) or "<document>"
        raise ConfigError(
            path, f"{location}: {error['msg']}", document.line_for(error["loc"])
        ) from exc
