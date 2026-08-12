"""Load and layer the quirks database (DESIGN.md 4).

The database is three layers -- defaults, family, feedstock -- merged with the
more specific layer winning. Mappings that feed provenance (``name_map``,
``embedded_extras``) are *not* flattened: they stay an ordered stack of layers
so a lookup can report which file supplied the answer (DESIGN.md 3.2).
"""

from __future__ import annotations

import fnmatch
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Generic, TypeVar

from pydantic import BaseModel, TypeAdapter, ValidationError

from ._yaml import load_yaml_document
from .errors import ConfigError
from .schema import (
    Defaults,
    ExtrasAsOutputs,
    Family,
    Feedstock,
    Output,
    Quirks,
    RecipeOwned,
    RequiresPython,
    TrustLevel,
    Upstream,
)

__all__ = [
    "AddedRequirement",
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


@dataclass(frozen=True)
class MappingLayer(Generic[_V]):
    """One layer of a layered lookup, labelled with the file it came from."""

    source: str
    entries: Mapping[str, _V]


@dataclass(frozen=True)
class AddedRequirement:
    """A conda-forge-only requirement line, and the config file that asked.

    The source is what turns the line into a `Provenance` the trust gates can
    check, so it travels with the text rather than being looked up again later.
    """

    text: str
    source: str


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
    trust: TrustLevel
    upstream: Upstream | None
    requires_python: RequiresPython | None
    extras_as_outputs: ExtrasAsOutputs | None
    outputs: Mapping[str, Output]
    name_map: Layered[str]
    embedded_extras: Layered[tuple[str, ...]]
    #: The union of every layer's allowlist, not the most specific one: a
    #: feedstock adding a local expression must not un-bless the global ones.
    recipe_owned: RecipeOwned
    #: Section name -> the conda-forge-only lines to add, each carrying the
    #: file that asked for it. Provenance needs the file, not just the line.
    add_requirements: Mapping[str, tuple[AddedRequirement, ...]]


class ConfigTree:
    """The validated quirks database."""

    def __init__(
        self,
        root: Path,
        defaults: Defaults,
        name_map: Mapping[str, str],
        families: Mapping[str, Family],
        feedstocks: Mapping[str, Feedstock],
    ) -> None:
        self.root = root
        self.defaults = defaults
        self.name_map = name_map
        self.families = families
        self.feedstocks = feedstocks

    def family_for(self, feedstock: str) -> Family | None:
        """Which family owns ``feedstock``.

        An explicit ``family:`` in the feedstock file wins, so a feedstock can
        belong to a family whose glob it does not match. Any *other* family
        whose glob also matches is an ambiguity, not a tiebreak -- families do
        not compose (DESIGN.md 12).
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

    def for_feedstock(self, feedstock: str) -> FeedstockConfig:
        """Resolve the layered config for ``feedstock``.

        Feedstocks with no file of their own are legitimate -- they resolve to
        their family's settings, or to the defaults, which start at
        ``trust: manual``.
        """
        entry = self.feedstocks.get(feedstock)
        family = self.family_for(feedstock)

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

        # Unioned rather than overridden, unlike everything else here: a
        # feedstock naming one local expression would otherwise drop
        # `pin_subpackage` and `python` and fail G1 on every line it has.
        recipe_owned = self.defaults.recipe_owned
        for layer in (family, entry):
            if layer is not None and layer.recipe_owned is not None:
                recipe_owned = layer.recipe_owned.extend(recipe_owned)

        # Also unioned: a family and a feedstock can each have a reason to add
        # something, and the more specific one does not cancel the other.
        added: dict[str, list[AddedRequirement]] = {"host": [], "run": []}
        for layer, source in (
            (family, f"config/families/{family.family}.yaml" if family else ""),
            (entry, f"config/feedstocks/{feedstock}.yaml"),
        ):
            if layer is None or layer.add_requirements is None:
                continue
            for section, lines in added.items():
                lines.extend(
                    AddedRequirement(text, source)
                    for text in layer.add_requirements.section(section)
                )

        # Spelled out rather than routed through `_first`: `Upstream` is a
        # union, and inferring a type variable from one collapses it to the
        # models' shared base.
        upstream: Upstream | None = None
        for layer in (entry, family):
            if layer is not None and layer.upstream is not None:
                upstream = layer.upstream
                break

        return FeedstockConfig(
            feedstock=feedstock,
            family=family.family if family is not None else None,
            trust=_first(entry, family, lambda q: q.trust) or self.defaults.trust,
            upstream=upstream,
            requires_python=(
                _first(entry, family, lambda q: q.requires_python)
                or self.defaults.requires_python
            ),
            extras_as_outputs=_first(entry, family, lambda q: q.extras_as_outputs),
            outputs=outputs,
            name_map=Layered(tuple(name_map_layers)),
            embedded_extras=Layered(tuple(extras_layers)),
            recipe_owned=recipe_owned,
            add_requirements={k: tuple(v) for k, v in added.items()},
        )


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
    """Locate the quirks database by walking up from ``start``.

    swage is run from a checkout of its own repo, where the database is a
    git-tracked ``config/`` directory. Distribution is a Phase 7 concern
    (DESIGN.md 10), so there is no installed-package fallback yet.
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

    defaults = _load_model(root / "defaults.yaml", Defaults)
    name_map = _load_name_map(root / "name-map.yaml")

    families: dict[str, Family] = {}
    for path in _yaml_files(root / "families"):
        family = _load_model(path, Family)
        _require_stem(path, family.family, "family")
        families[family.family] = family

    feedstocks: dict[str, Feedstock] = {}
    for path in _yaml_files(root / "feedstocks"):
        feedstock = _load_model(path, Feedstock)
        _require_stem(path, feedstock.feedstock, "feedstock")
        if feedstock.family is not None and feedstock.family not in families:
            raise ConfigError(path, f"unknown family '{feedstock.family}'")
        feedstocks[feedstock.feedstock] = feedstock

    tree = ConfigTree(root, defaults, name_map, families, feedstocks)
    # Ambiguous family membership is a load-time error for every feedstock we
    # know by name; feedstocks without a file are checked when they resolve.
    for name in feedstocks:
        tree.family_for(name)
    return tree


def _yaml_files(directory: Path) -> Iterator[Path]:
    if not directory.is_dir():
        return
    yield from sorted(directory.glob("*.yaml"))


def _require_stem(path: Path, value: str, field: str) -> None:
    if value != path.stem:
        raise ConfigError(
            path, f"{field} is '{value}' but the file is named {path.name}"
        )


def _load_model(path: Path, model: type[_M]) -> _M:
    if not path.is_file():
        raise ConfigError(path, "required config file is missing")
    document = load_yaml_document(path)
    try:
        return model.model_validate(document.data)
    except ValidationError as exc:
        error = exc.errors()[0]
        location = ".".join(str(part) for part in error["loc"]) or "<document>"
        raise ConfigError(
            path, f"{location}: {error['msg']}", document.line_for(error["loc"])
        ) from exc


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
