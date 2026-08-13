"""The wheel's METADATA, for a release whose sdist does not state its own.

An sdist built by setuptools' `sdist` command writes a `PKG-INFO` from the
core-metadata fields it knows, and **`Requires-Dist` is not among them unless
the project declares its dependencies declaratively.** A project that sets
`install_requires` in `setup.py` therefore publishes an sdist that names itself
and its version and says nothing at all about what it needs -- while the wheel
built from the very same release carries the complete list, because the wheel
is built *after* setup.py has run and its METADATA is written from the result.

`alibabacloud-adb20211201` 4.1.0 is the case in the maintainer's fleet. Its
sdist's `PKG-INFO` is `Metadata-Version: 2.1` with no `Requires-Dist` line at
all; its `py3-none-any` wheel declares both `alibabacloud-tea-openapi` and
`darabonba-core`, which are exactly the two dependencies the recipe carries and
which swage had been reporting as coming from nowhere.

**This is not the same as executing `setup.py`, and the difference is the whole
reason it is worth doing.** The wheel's METADATA is declarative metadata that
upstream published, read the same way `PKG-INFO` is read. swage runs no upstream
code and parses no Python; it reads a second file that the same release already
ships.

**The bytes are verified, against PyPI rather than against the recipe.** The
recipe pins the sdist's `sha256` and swage checks it (DESIGN.md 3.6), which is
the strongest guarantee available and does not extend to a distribution the
recipe never mentions. So the wheel is checked against the digest PyPI publishes
for it in the same response that named it. That is weaker -- it trusts the index
rather than the pull request -- and it is worth saying out loud, which is why
the metadata records that the dependencies came from here.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import PurePosixPath
from typing import Any

from swage.upstream import UpstreamError, UpstreamMetadata, parse_metadata

from .archive import Fetcher, download
from .errors import ForgeError

__all__ = ["PYPI_JSON", "wheel_metadata"]

#: PyPI's per-release JSON, which lists every distribution of one version.
PYPI_JSON = "https://pypi.org/pypi/{name}/{version}/json"


def wheel_metadata(
    name: str, version: str, fetch: Fetcher = download
) -> tuple[UpstreamMetadata, str] | None:
    """The metadata in this release's wheel, and the wheel's filename.

    ``None`` where the release publishes no wheel, which is an answer rather
    than an error: `hdfs` 2.7.3 ships an sdist alone, so a project can be
    silent in its sdist and have nowhere else to look. The caller keeps what
    the sdist said and the feedstock stops at G1 as before.

    Every other failure -- an index that will not answer, a wheel whose digest
    does not match, a wheel with no METADATA in it -- is a `ForgeError`. Those
    are not "no wheel"; they are swage being unable to tell whether there is
    one, and quietly treating them as absence would turn a broken index into a
    feedstock that looks dependency-free.
    """
    payload = fetch(PYPI_JSON.format(name=name, version=version))
    try:
        release = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ForgeError(
            f"{name} {version}: PyPI's release JSON is not JSON: {exc}"
        ) from exc

    chosen = _pick(release.get("urls") or [])
    if chosen is None:
        return None

    url = str(chosen["url"])
    filename = str(chosen["filename"])
    expected = (chosen.get("digests") or {}).get("sha256")
    data = fetch(url)
    digest = hashlib.sha256(data).hexdigest()
    if expected and digest != expected:
        raise ForgeError(
            f"{url}: sha256 does not match the digest PyPI published for it\n"
            f"    index:      {expected}\n"
            f"    downloaded: {digest}"
        )
    return _read(data, url, filename), filename


def _pick(urls: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The wheel to read, preferring the pure-Python one.

    conda-forge builds `noarch: python` packages from these feedstocks, so a
    `py3-none-any` wheel describes the same single artifact the recipe does.
    Where a release ships only platform wheels, any of them still states the
    release's dependencies -- the file name varies, `Requires-Dist` does not --
    so the first is taken rather than the release being treated as wheel-less.
    """
    wheels = [entry for entry in urls if entry.get("packagetype") == "bdist_wheel"]
    if not wheels:
        return None
    for wheel in wheels:
        if str(wheel.get("filename", "")).endswith("-py3-none-any.whl"):
            return wheel
    return wheels[0]


def _read(data: bytes, url: str, filename: str) -> UpstreamMetadata:
    """Parse the `*.dist-info/METADATA` member of a wheel."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as wheel:
            member = _metadata_member(wheel.namelist())
            if member is None:
                raise ForgeError(
                    f"{url}: a wheel with no .dist-info/METADATA in it, so "
                    "there is nothing here to read the dependencies from"
                )
            text = wheel.read(member).decode("utf-8")
    except zipfile.BadZipFile as exc:
        raise ForgeError(f"{url}: cannot read as a wheel: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise ForgeError(f"{url}: METADATA is not UTF-8 text: {exc}") from exc

    try:
        return parse_metadata(text, filename)
    except UpstreamError as exc:
        raise ForgeError(str(exc)) from exc


def _metadata_member(names: list[str]) -> str | None:
    """`pkg-1.2.3.dist-info/METADATA`, and not a vendored copy deeper in.

    The same rule the sdist reader applies to `PKG-INFO` (DESIGN.md 3.6): a
    wheel keeps its metadata exactly one directory down, so the shallowest
    match is the real one and anything deeper belongs to something the wheel
    happens to bundle.
    """
    candidates = [
        name
        for name in names
        if PurePosixPath(name).name == "METADATA"
        and PurePosixPath(name).parent.name.endswith(".dist-info")
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda name: (len(PurePosixPath(name).parts), name))
