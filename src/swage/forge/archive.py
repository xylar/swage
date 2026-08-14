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

__all__ = ["Fetcher", "download", "metadata_texts", "read_archive", "verified_payload"]

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


def read_archive(
    url: str,
    sha256: str,
    fetch: Fetcher = download,
    metadata: str | None = None,
) -> UpstreamMetadata:
    """Download the archive at ``url`` and read the metadata inside it."""
    return parse_archive(verified_payload(url, sha256, fetch), url, metadata)


def verified_payload(url: str, sha256: str, fetch: Fetcher = download) -> bytes:
    """The bytes at ``url``, or a refusal if they are not the recipe's bytes.

    Split out so `draft` can quote the same archive back at a maintainer
    without a second copy of the hash check. Something that reads an sdist
    without verifying it would be the one path where swage looks at a
    different release from the one it reconciled against.
    """
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
    return payload


def metadata_texts(
    payload: bytes, source: str, metadata: str | None = None
) -> dict[str, str]:
    """The metadata files `parse_archive` reads, unparsed, keyed by file name.

    `draft` writes these into its workbench so a maintainer deciding what a
    name means can read what upstream said about it (DESIGN.md 8.1). It picks
    its members through the same helpers `parse_archive` does, because the
    file quoted beside a finding has to be the file the finding came from --
    a workbench showing a `pyproject.toml` swage did not read would answer the
    question about the wrong file, which is worse than not answering it.

    Both files where both exist: `_reconcile_sources` takes `[build-system]`
    from one and the dependencies from the other, so quoting only the
    preferred one would drop the half that explained the `host` section.
    """
    try:
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:*") as archive:
            members = [member for member in archive.getmembers() if member.isfile()]
            if metadata is not None:
                member = _member_at(members, metadata)
                if member is None:
                    raise ForgeError(
                        f"{source}: has no {metadata}\n"
                        "  the path is relative to the archive's top-level "
                        "directory, and comes from `upstream.metadata` in config"
                    )
                chosen = [member]
            else:
                chosen = [
                    found
                    for found in (
                        _shallowest(members, "pyproject.toml"),
                        _shallowest(members, "PKG-INFO"),
                    )
                    if found is not None
                ]
            texts = {}
            for member in chosen:
                read = _read(archive, member, source)
                if read is not None:
                    texts[PurePosixPath(member.name).name] = read[0]
    except tarfile.TarError as exc:
        raise ForgeError(f"{source}: cannot read as a tar archive: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise ForgeError(f"{source}: metadata is not UTF-8 text: {exc}") from exc
    return texts


def parse_archive(
    payload: bytes, source: str, metadata: str | None = None
) -> UpstreamMetadata:
    """Read the metadata out of an already-downloaded archive.

    ``metadata`` names the file to read, relative to the archive's single
    top-level directory, for an archive where the one at the root is not the
    right one. That is the monorepo case and config's job (DESIGN.md 4).
    """
    try:
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:*") as archive:
            members = [member for member in archive.getmembers() if member.isfile()]
            if metadata is not None:
                return _at_path(archive, members, metadata, source)
            pyproject = _read(archive, _shallowest(members, "pyproject.toml"), source)
            pkg_info = _read(archive, _shallowest(members, "PKG-INFO"), source)
    except tarfile.TarError as exc:
        raise ForgeError(f"{source}: cannot read as a tar archive: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise ForgeError(f"{source}: metadata is not UTF-8 text: {exc}") from exc
    except UpstreamError as exc:
        raise ForgeError(str(exc)) from exc

    try:
        return _reconcile_sources(pyproject, pkg_info, source)
    except UpstreamError as exc:
        raise ForgeError(str(exc)) from exc


def _at_path(
    archive: tarfile.TarFile,
    members: list[tarfile.TarInfo],
    metadata: str,
    source: str,
) -> UpstreamMetadata:
    """Read exactly the file config named, and nothing else.

    An explicit path is an instruction rather than a hint, so there is no
    falling back to the root's metadata: config says this subdirectory holds
    the package, and quietly reading a different one would reconcile the
    recipe against a different project. `OpenLineage` ships seven
    `pyproject.toml` files, one of which describes no package at all.
    """
    member = _member_at(members, metadata)
    if member is None:
        raise ForgeError(
            f"{source}: has no {metadata}\n"
            "  the path is relative to the archive's top-level directory, and "
            "comes from `upstream.metadata` in config"
        )

    read = _read(archive, member, source)
    if read is None:  # pragma: no cover -- isfile() already ruled this out
        raise ForgeError(f"{source}: cannot read {metadata}")
    text, where = read
    name = PurePosixPath(member.name).name
    if name == "pyproject.toml":
        return parse_pyproject(text, where)
    if name in ("PKG-INFO", "METADATA"):
        return parse_metadata(text, where)
    raise ForgeError(
        f"{where}: swage cannot read metadata out of a {name}\n"
        "  it reads pyproject.toml, PKG-INFO and METADATA; a setup.py states "
        "its dependencies only by running, and swage will not execute "
        "upstream code to find out\n"
        "  point `upstream.metadata` at one of those instead"
    )


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

    **The version is the same rule, and used not to be.** A readable
    `[project]` table was taken whole, including the `None` a project gets
    when it says `dynamic = ["version"]` and lets its backend fill the value
    in -- while the `PKG-INFO` beside it in the built sdist carried the answer
    the whole time. `pyproject.toml` cannot state that field by construction,
    so preferring it there is preferring the one file guaranteed not to know.
    """
    if pyproject is not None:
        try:
            parsed = parse_pyproject(*pyproject)
        except UpstreamError:
            # Only worth surviving because PKG-INFO can state the same thing.
            if pkg_info is None:
                raise
        else:
            # A `[project]` table is allowed to name a field it will not state
            # -- `dynamic = ["version"]` is ordinary, and the build backend
            # fills it in. The built sdist's `PKG-INFO` is where it landed, so
            # taking the version from there is this function's own rule
            # applied to one more field rather than an exception to it.
            if parsed.version is None and pkg_info is not None:
                return replace(parsed, version=parse_metadata(*pkg_info).version)
            return parsed

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


def _member_at(members: list[tarfile.TarInfo], metadata: str) -> tarfile.TarInfo | None:
    """The member at the config-given path, inside the top directory or at it."""
    wanted = PurePosixPath(metadata).parts
    return next(
        (m for m in members if PurePosixPath(m.name).parts[1:] == wanted), None
    ) or next((m for m in members if PurePosixPath(m.name).parts == wanted), None)


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
