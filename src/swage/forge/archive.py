"""Read upstream metadata out of the archive a recipe builds from.

This is the sdist path, which the google-cloud family needs (DESIGN.md 3.6).
The recipe names the archive and pins its hash, so both come out of the pull
request rather than out of a query about what upstream released most recently.

**The hash is checked, always.** swage decides what a recipe should say from
what is inside this archive and then pushes that decision unattended, so
"the bytes I read are the bytes this recipe claims to build" is not a nicety.
A mismatch is a hard failure rather than a warning: it means either the
download was corrupted or the recipe's `sha256` no longer describes its `url`,
and reconciling against the wrong release is worse than reconciling against
nothing.

**`pyproject.toml` is preferred over `PKG-INFO`, and the reason is
`[build-system]`.** Core metadata carries no build-system information at all,
so a `host` section cannot be reconciled from `PKG-INFO` alone
(DESIGN.md 3.6.2) -- `flit-core ==3.12.0` in a recipe's `host` is upstream's
own `[build-system] requires`, not a conda-forge convention. Both files
describe the same release and they are not interchangeable.

**Preferring it is not the same as requiring it to be readable**, which is
what `_reconcile_sources` is about: a fifth of the fleet's archives ship a
`pyproject.toml` swage cannot read the dependencies out of, next to a
`PKG-INFO` that states them completely.

**The shallowest match wins.** An sdist keeps its metadata at the root of a
single top-level directory, `pkg-1.2.3/pyproject.toml`. Taking the first
member whose name ends in `pyproject.toml`, which the prior art does, picks a
vendored or test-fixture copy from deeper in the tree whenever one sorts
earlier.
"""

from __future__ import annotations

import hashlib
import io
import tarfile
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import replace
from pathlib import PurePosixPath

from swage import __version__
from swage.upstream import (
    UpstreamError,
    UpstreamMetadata,
    parse_build_requires,
    parse_metadata,
    parse_pyproject,
)

from .errors import ForgeError

__all__ = ["Fetcher", "download", "read_archive"]

#: Takes a URL and returns the bytes at it.
Fetcher = Callable[[str], bytes]


def download(url: str, timeout: float = 60.0) -> bytes:
    """Fetch a URL, raising `ForgeError` rather than a urllib exception."""
    agent = {"User-Agent": f"swage/{__version__}"}
    request = urllib.request.Request(url, headers=agent)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data: bytes = response.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ForgeError(f"{url}: download failed: {exc}") from exc
    return data


def read_archive(url: str, sha256: str, fetch: Fetcher = download) -> UpstreamMetadata:
    """Download the archive at ``url`` and read the metadata inside it."""
    payload = fetch(url)
    digest = hashlib.sha256(payload).hexdigest()
    if digest != sha256:
        raise ForgeError(
            f"{url}: sha256 does not match the recipe\n"
            f"    recipe:     {sha256}\n"
            f"    downloaded: {digest}\n"
            "  swage reconciles against what this recipe says it builds, so "
            "these have to be the same bytes"
        )
    return parse_archive(payload, url)


def parse_archive(payload: bytes, source: str) -> UpstreamMetadata:
    """Read the metadata out of an already-downloaded archive."""
    try:
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:*") as archive:
            members = [member for member in archive.getmembers() if member.isfile()]
            pyproject = _read(archive, _shallowest(members, "pyproject.toml"), source)
            pkg_info = _read(archive, _shallowest(members, "PKG-INFO"), source)
    except tarfile.TarError as exc:
        raise ForgeError(f"{source}: cannot read as a tar archive: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise ForgeError(f"{source}: metadata is not UTF-8 text: {exc}") from exc

    try:
        return _reconcile_sources(pyproject, pkg_info, source)
    except UpstreamError as exc:
        raise ForgeError(str(exc)) from exc


def _reconcile_sources(
    pyproject: tuple[str, str] | None,
    pkg_info: tuple[str, str] | None,
    source: str,
) -> UpstreamMetadata:
    """Take each half of the metadata from the file that can actually state it.

    `pyproject.toml` is preferred whole, because only it carries
    `[build-system] requires` *and* the dependencies in one place. But
    preferring it is not the same as requiring it to be readable, and 21 of
    the 88 archives in the maintainer's fleet are the difference: a project
    using poetry or plain setuptools declares no PEP 621 ``[project]`` table,
    and three more compute their dependencies at build time. Every one of
    those is a PyPI sdist shipping a complete `PKG-INFO` beside the
    `pyproject.toml` swage cannot use.

    Refusing them would strand a fifth of the fleet with usable metadata in
    hand -- the same mistake DESIGN.md 3.6.3 rejects for a dynamic
    `Requires-Dist`, and for the same reason: the list is *present and
    complete*, and only its provenance is unusual. So the runtime
    dependencies come from `PKG-INFO` and ``[build-system]`` still comes from
    `pyproject.toml`, which is what leaves a `host` section reconcilable
    (DESIGN.md 3.6.2) instead of leaving every line in it unexplained.
    """
    if pyproject is not None:
        try:
            return parse_pyproject(*pyproject)
        except UpstreamError:
            # Only worth surviving because PKG-INFO can state the same thing.
            if pkg_info is None:
                raise

    if pkg_info is None:
        raise ForgeError(
            f"{source}: contains neither a pyproject.toml nor a PKG-INFO, so "
            "there is no upstream metadata in it to reconcile against"
        )

    metadata = parse_metadata(*pkg_info)
    if pyproject is None:
        return metadata
    # The `[project]` table was unreadable; `[build-system]` may not be, and
    # it is the only place a `host` section can come from.
    return replace(metadata, build_requires=parse_build_requires(*pyproject))


def _read(
    archive: tarfile.TarFile, member: tarfile.TarInfo | None, source: str
) -> tuple[str, str] | None:
    """The member's text and a name for it, or None if it is not there."""
    if member is None:
        return None
    extracted = archive.extractfile(member)
    if extracted is None:  # pragma: no cover -- isfile() already ruled this out
        return None
    return extracted.read().decode("utf-8"), f"{source}::{member.name}"


def _shallowest(members: list[tarfile.TarInfo], name: str) -> tarfile.TarInfo | None:
    """The matching member closest to the archive root, or None."""
    candidates = [
        member for member in members if PurePosixPath(member.name).name == name
    ]
    return min(
        candidates,
        key=lambda member: (len(PurePosixPath(member.name).parts), member.name),
        default=None,
    )
