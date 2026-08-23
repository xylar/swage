"""What conda-forge actually publishes, for the name resolver (DESIGN.md 3.2).

The resolver is layered and every layer above this one is something a human
wrote down. These are the two that are not, and they are what stop the fleet
from resolving nothing:

**The grayskull mapping** -- layer 4 -- is regro's
`grayskull_pypi_mapping.json`, the same table `grayskull` and conda-forge's own
autotick bot resolve names against. It holds only the pairs that *differ*:
`docker` is there because the package is `docker-py`, and `pandas` is not there
at all because there is nothing to say. So it answers "what is this called on
conda-forge" and cannot answer "does conda-forge have this", which is why it is
not enough on its own.

**The channel's package list** -- layer 5 -- is conda-forge's
`channeldata.json`, and it is what makes identity a *check* rather than an
assumption. Without it every unknown name would resolve to itself and the
unresolved state would be unreachable, which would quietly disarm G2 -- the
gate whose whole job is to notice that swage guessed.

Both are cached under `~/.cache/swage` with a TTL, because a scan over the
fleet would otherwise download 24 MB per run to answer questions whose answers
move on the order of days. A stale cache is a correctness question rather than
a speed one: a name that resolves today because conda-forge added the package
last week should not depend on when swage last swept.

**A cache that cannot be read is refreshed, never trusted.** Half a download,
a truncated write, a file from a future swage -- all of them are the same
answer here, which is that the network is the source of truth and the cache is
only ever an optimization.
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

#: conda-forge's own summary of what it publishes. ~22 MB, and the smallest
#: complete list of package names the channel offers -- `repodata.json` is an
#: order of magnitude larger and answers a question about *files*.
CHANNELDATA_URL = "https://conda.anaconda.org/conda-forge/channeldata.json"

#: The PyPI-to-conda-forge table grayskull and the autotick bot both use.
GRAYSKULL_URL = (
    "https://raw.githubusercontent.com/regro/cf-graph-countyfair/master/"
    "mappings/pypi/grayskull_pypi_mapping.json"
)

#: What a resolution out of that table records as its source. A named layer
#: rather than a file path, because the file is a cache and naming it would
#: send someone to `~/.cache` to find out why a name resolved (DESIGN.md 9.2).
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
    """PyPI name to conda-forge name, as the bottom layer of the name map.

    Bottom because it is the only layer nobody in this project reviewed: an
    entry in `config/name-map.yaml` is a fact a maintainer wrote down, and it
    wins over this one by sitting above it.
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
    """Assemble the resolver for one feedstock, in DESIGN.md 3.2's layer order.

    Here rather than at each call site because the order *is* the policy:
    grayskull goes below `config/name-map.yaml` so that a fact a maintainer
    wrote down always beats a table nobody in this project reviewed. Two
    commands assembling that separately is two places for it to drift.
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
