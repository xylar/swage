"""What conda-forge actually publishes, for the name resolver (v1 §3.2;
DESIGN.md §8).

The grayskull mapping holds only the pairs that differ; the channel's package
list is what makes identity a check rather than an assumption, which is what
keeps G2 armed. Both are cached with a TTL, and a cache that cannot be read is
refreshed, never trusted.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from swage.cache import cache_root
from swage.config import FeedstockConfig, Layered, MappingLayer
from swage.mapping import NameResolver, PackageIndex, StaticPackageIndex

from .archive import Fetcher, download
from .errors import ForgeError

__all__ = [
    "CHANNELDATA_URL",
    "GRAYSKULL_SOURCE",
    "GRAYSKULL_URL",
    "build_resolver",
    "load_grayskull_layer",
    "load_package_index",
]

#: conda-forge's own summary of what it publishes: the smallest complete list of
#: package names.
CHANNELDATA_URL = "https://conda.anaconda.org/conda-forge/channeldata.json"

#: The PyPI-to-conda-forge table grayskull and the autotick bot both use.
GRAYSKULL_URL = (
    "https://raw.githubusercontent.com/regro/cf-graph-countyfair/master/"
    "mappings/pypi/grayskull_pypi_mapping.json"
)

#: What a resolution out of that table records as its source: a named layer,
#: never the cache path (v1 §9.2).
GRAYSKULL_SOURCE = "grayskull pypi mapping"

#: A day. Long enough that a sweep costs nothing, short enough that a package
#: that appeared this week is found this week.
DEFAULT_TTL = 24 * 60 * 60


def load_package_index(
    fetch: Fetcher = download,
    ttl: float = DEFAULT_TTL,
    directory: Path | None = None,
) -> StaticPackageIndex:
    """Every package name conda-forge publishes, for identity resolution."""
    payload = _cached(CHANNELDATA_URL, "channeldata.json", fetch, ttl, directory)
    packages = payload.get("packages")
    if not isinstance(packages, Mapping) or not packages:
        raise ForgeError(
            f"{CHANNELDATA_URL}: has no 'packages' object, so swage cannot "
            "tell which names conda-forge publishes"
        )
    return StaticPackageIndex(frozenset(packages))


def load_grayskull_layer(
    fetch: Fetcher = download,
    ttl: float = DEFAULT_TTL,
    directory: Path | None = None,
) -> MappingLayer[str]:
    """PyPI name to conda-forge name, as the bottom layer of the name map, below
    every layer a maintainer reviewed.
    """
    payload = _cached(GRAYSKULL_URL, "grayskull-mapping.json", fetch, ttl, directory)
    entries = {
        pypi_name: entry["conda_name"]
        for pypi_name, entry in payload.items()
        if isinstance(entry, Mapping) and isinstance(entry.get("conda_name"), str)
    }
    if not entries:
        raise ForgeError(f"{GRAYSKULL_URL}: names nothing swage can map")
    return MappingLayer(GRAYSKULL_SOURCE, entries)


def build_resolver(
    config: FeedstockConfig,
    index: PackageIndex,
    grayskull: MappingLayer[str],
) -> NameResolver:
    """Assemble the resolver for one feedstock, in v1 §3.2's layer order, in one
    place because the order is the policy.
    """
    return NameResolver(
        Layered((*config.name_map.layers, grayskull)), index, GRAYSKULL_SOURCE
    )


def _cached(
    url: str,
    name: str,
    fetch: Fetcher,
    ttl: float,
    directory: Path | None,
) -> Mapping[str, Any]:
    """The JSON at ``url``, from disk while it is fresh enough."""
    path = (directory if directory is not None else cache_root() / "index") / name
    cached = _read(path, ttl)
    if cached is not None:
        return cached

    payload = _parse(url, fetch(url))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(json.dumps(payload).encode("utf-8"))
    except OSError:
        # A cache swage cannot write is a slower swage, not a broken one.
        pass
    return payload


def _read(path: Path, ttl: float) -> Mapping[str, Any] | None:
    try:
        if time.time() - path.stat().st_mtime > ttl:
            return None
        payload = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError):
        # Missing, stale, half-written, or unreadable -- all the same answer,
        # which is to go and ask the network.
        return None
    return payload if isinstance(payload, Mapping) else None


def _parse(url: str, raw: bytes) -> Mapping[str, Any]:
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ForgeError(f"{url}: did not answer with JSON: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise ForgeError(
            f"{url}: answered with {type(payload).__name__}, not an object"
        )
    return payload
