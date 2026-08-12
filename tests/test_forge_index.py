"""Tests for the name-resolution data sources (DESIGN.md 3.2).

No network, per DESIGN.md 11: the fetcher is a callable and every test hands
it one that counts its calls, which is also how the caching is asserted.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from swage.forge import (
    CHANNELDATA_URL,
    GRAYSKULL_SOURCE,
    GRAYSKULL_URL,
    ForgeError,
    load_grayskull_layer,
    load_package_index,
)

CHANNELDATA = {
    "channeldata_version": 1,
    "packages": {"pandas": {"version": "2.3.3"}, "docker-py": {"version": "7.1.0"}},
}

GRAYSKULL = {
    "docker": {
        "conda_name": "docker-py",
        "import_name": "docker",
        "mapping_source": "static",
        "pypi_name": "docker",
    },
    "ruamel.yaml": {"conda_name": "ruamel.yaml", "pypi_name": "ruamel.yaml"},
}


def fetcher(payload: object) -> Callable[[str], bytes]:
    """A fetcher that answers with ``payload`` and counts how often it is asked."""

    def fetch(url: str) -> bytes:
        fetch.calls += 1  # type: ignore[attr-defined]
        return json.dumps(payload).encode("utf-8")

    fetch.calls = 0  # type: ignore[attr-defined]
    return fetch


def calls(fetch: Callable[[str], bytes]) -> int:
    return int(fetch.calls)  # type: ignore[attr-defined]


def test_the_package_index_is_every_name_the_channel_publishes(
    tmp_path: Path,
) -> None:
    index = load_package_index(fetcher(CHANNELDATA), directory=tmp_path)

    assert index.has("pandas")
    assert index.has("docker-py")
    # The PyPI spelling is not a conda-forge package, which is the whole point
    # of asking the channel rather than assuming identity.
    assert not index.has("docker")


def test_the_grayskull_layer_maps_only_what_differs(tmp_path: Path) -> None:
    layer = load_grayskull_layer(fetcher(GRAYSKULL), directory=tmp_path)

    assert layer.source == GRAYSKULL_SOURCE
    assert layer.entries["docker"] == "docker-py"
    # `pandas` is absent from the table because there is nothing to say about
    # it -- identity is layer 5's job, not this one's.
    assert "pandas" not in layer.entries


def test_a_second_read_comes_from_the_cache(tmp_path: Path) -> None:
    fetch = fetcher(CHANNELDATA)

    load_package_index(fetch, directory=tmp_path)
    load_package_index(fetch, directory=tmp_path)

    assert calls(fetch) == 1


def test_a_stale_cache_is_refetched(tmp_path: Path) -> None:
    fetch = fetcher(CHANNELDATA)
    load_package_index(fetch, directory=tmp_path)

    stale = time.time() - 10
    os.utime(tmp_path / "channeldata.json", (stale, stale))
    load_package_index(fetch, ttl=1.0, directory=tmp_path)

    assert calls(fetch) == 2


def test_an_unreadable_cache_is_refetched_rather_than_trusted(tmp_path: Path) -> None:
    """Half a download and a truncated write are the same answer here."""
    fetch = fetcher(CHANNELDATA)
    load_package_index(fetch, directory=tmp_path)
    (tmp_path / "channeldata.json").write_text('{"packages": {"pand', encoding="utf-8")

    index = load_package_index(fetch, directory=tmp_path)

    assert calls(fetch) == 2
    assert index.has("pandas")


def test_the_two_caches_do_not_collide(tmp_path: Path) -> None:
    """Different files, so reading one never serves the other's contents."""
    load_package_index(fetcher(CHANNELDATA), directory=tmp_path)
    layer = load_grayskull_layer(fetcher(GRAYSKULL), directory=tmp_path)

    assert layer.entries["docker"] == "docker-py"
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "channeldata.json",
        "grayskull-mapping.json",
    ]


def test_a_channel_answer_without_packages_is_refused(tmp_path: Path) -> None:
    """An empty index would make every identity resolution fail at G2.

    Silently answering "conda-forge publishes nothing" is worse than stopping:
    it looks like a fleet-wide name-resolution problem rather than like a bad
    download.
    """
    with pytest.raises(ForgeError, match="no 'packages' object"):
        load_package_index(fetcher({"channeldata_version": 1}), directory=tmp_path)


def test_an_empty_grayskull_table_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ForgeError, match="names nothing"):
        load_grayskull_layer(fetcher({}), directory=tmp_path)


def test_a_non_json_answer_names_the_url(tmp_path: Path) -> None:
    def fetch(url: str) -> bytes:
        return b"<html>404</html>"

    with pytest.raises(ForgeError, match=CHANNELDATA_URL):
        load_package_index(fetch, directory=tmp_path)


def test_a_json_array_is_not_a_mapping(tmp_path: Path) -> None:
    with pytest.raises(ForgeError, match=GRAYSKULL_URL):
        load_grayskull_layer(fetcher([1, 2, 3]), directory=tmp_path)


def test_a_malformed_grayskull_entry_is_skipped_rather_than_fatal(
    tmp_path: Path,
) -> None:
    """One bad row in a table of twelve thousand should not stop a run."""
    layer = load_grayskull_layer(
        fetcher({"docker": GRAYSKULL["docker"], "broken": {"pypi_name": "broken"}}),
        directory=tmp_path,
    )

    assert dict(layer.entries) == {"docker": "docker-py"}


def test_an_unwritable_cache_directory_is_slower_not_broken(tmp_path: Path) -> None:
    fetch = fetcher(CHANNELDATA)
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory", encoding="utf-8")

    index = load_package_index(fetch, directory=blocked)

    assert index.has("pandas")
    assert calls(fetch) == 1
