"""Read upstream metadata out of the archive a recipe builds from (v1 §3.6).

The hash is checked, always. `pyproject.toml` is preferred over `PKG-INFO`
because only it carries `[build-system]` (v1 §3.6.2), and `_reconcile_sources`
takes each half from the file that can state it. The shallowest match wins.
"""

from __future__ import annotations

import hashlib
import io
import os
import tarfile
import textwrap
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path, PurePosixPath

from swage import __version__
from swage.upstream import (
    EntryPoint,
    UpstreamError,
    UpstreamMetadata,
    parse_build_requires,
    parse_entry_points,
    parse_entry_points_txt,
    parse_metadata,
    parse_pyproject,
)

from .errors import ForgeError, NotFound

__all__ = [
    "Fetcher",
    "archive_named",
    "archive_texts",
    "caching",
    "download",
    "entry_points_member",
    "metadata_texts",
    "read_archive",
    "verified_payload",
]

#: Takes a URL and returns the bytes at it.
Fetcher = Callable[[str], bytes]


def download(url: str, timeout: float = 60.0) -> bytes:
    """Fetch a URL, raising `ForgeError` rather than a urllib exception; a 404
    is `NotFound`, which the wheel fallback acts on.
    """
    agent = {"User-Agent": f"swage/{__version__}"}
    request = urllib.request.Request(url, headers=agent)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data: bytes = response.read()
    except urllib.error.HTTPError as exc:
        message = f"{url}: download failed: {exc}"
        raise (NotFound(message) if exc.code == 404 else ForgeError(message)) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ForgeError(f"{url}: download failed: {exc}") from exc
    return data


def caching(fetch: Fetcher, root: Path) -> Fetcher:
    """``fetch``, but keeping what it returns under ``root`` (v1 §8.2).

    A decorator, because every caller already passes a `Fetcher`. Nothing
    here is trusted: `verified_payload` checks the bytes against the recipe's
    hash on a cache hit as on a download. Written through a temporary file
    and renamed.
    """

    def fetch_cached(url: str) -> bytes:
        path = root / _entry(url)
        try:
            return path.read_bytes()
        except OSError:
            # Missing, unreadable, or a directory somebody put there. All of
            # them mean the same thing to a cache: fetch it.
            pass
        payload = fetch(url)
        try:
            root.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(f"{path.name}.{os.getpid()}")
            temporary.write_bytes(payload)
            temporary.replace(path)
        except OSError:
            # A cache that cannot be written is a slow swage, not a broken
            # one. The bytes are already in hand and the caller wants those.
            pass
        return payload

    return fetch_cached


def _entry(url: str) -> str:
    """A filename for ``url`` that keeps its basename readable."""
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    name = PurePosixPath(urllib.parse.urlparse(url).path).name
    return f"{digest}-{name}" if name else digest


def read_archive(
    url: str,
    sha256: str,
    fetch: Fetcher = download,
    metadata: str | None = None,
) -> UpstreamMetadata:
    """Download the archive at ``url`` and read the metadata inside it."""
    return parse_archive(verified_payload(url, sha256, fetch), url, metadata)


def verified_payload(url: str, sha256: str, fetch: Fetcher = download) -> bytes:
    """The bytes at ``url``, or a refusal if they are not the recipe's bytes."""
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


@dataclass(frozen=True)
class _Archive:
    """One upstream archive's members, whichever way it happens to be packed:
    a tarball or a zip, and nothing above `_open` knows which.
    """

    source: str
    #: Regular files only, in the order the archive lists them. Both formats
    #: can carry directory entries, and neither helper above wants one.
    names: tuple[str, ...]
    read: Callable[[str], bytes]


@contextmanager
def _open(payload: bytes, source: str) -> Iterator[_Archive]:
    """Open ``payload`` as a zip if it is one and a tar otherwise, by content
    rather than by the URL's suffix.
    """
    buffer = io.BytesIO(payload)
    if zipfile.is_zipfile(buffer):
        buffer.seek(0)
        try:
            with zipfile.ZipFile(buffer) as zipped:
                names = tuple(
                    entry.filename for entry in zipped.infolist() if not entry.is_dir()
                )
                yield _Archive(source, names, zipped.read)
        except zipfile.BadZipFile as exc:
            raise ForgeError(f"{source}: cannot read as a zip archive: {exc}") from exc
        return
    buffer.seek(0)
    try:
        with tarfile.open(fileobj=buffer, mode="r:*") as tar:
            names = tuple(member.name for member in tar.getmembers() if member.isfile())
            yield _Archive(source, names, partial(_tar_bytes, tar))
    except tarfile.TarError as exc:
        raise ForgeError(
            f"{source}: cannot read as a tar archive\n{textwrap.indent(str(exc), '  ')}"
        ) from exc


def _tar_bytes(tar: tarfile.TarFile, name: str) -> bytes:
    """One member of a tarball, to the signature `_Archive.read` holds."""
    extracted = tar.extractfile(name)
    if extracted is None:  # pragma: no cover -- the name came from `names`
        return b""
    return extracted.read()


def metadata_texts(
    payload: bytes, source: str, metadata: str | None = None
) -> dict[str, str]:
    """The metadata files `parse_archive` reads, unparsed, keyed by file name,
    for `draft`'s workbench (v1 §8.1). Both files where both exist.
    """
    try:
        with _open(payload, source) as archive:
            if metadata is not None:
                member = _member_at(archive.names, metadata)
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
                        _shallowest(archive.names, "pyproject.toml"),
                        _shallowest(archive.names, "PKG-INFO"),
                    )
                    if found is not None
                ]
            texts = {}
            for member in chosen:
                texts[PurePosixPath(member).name] = _text(archive, member)
    except UnicodeDecodeError as exc:
        raise ForgeError(f"{source}: metadata is not UTF-8 text: {exc}") from exc
    return texts


def archive_texts(
    payload: bytes, paths: Sequence[str], source: str
) -> dict[str, str | None]:
    """Named files out of an archive, keyed by the path asked for.

    None means the archive does not carry that file, which a reader may act
    on. Paths are relative to the archive's single top-level directory.
    """
    found: dict[str, str | None] = dict.fromkeys(paths)
    try:
        with _open(payload, source) as archive:
            for path in paths:
                member = _member_at(archive.names, path)
                if member is not None:
                    found[path] = _text(archive, member)
    except UnicodeDecodeError as exc:
        raise ForgeError(f"{source}: is not UTF-8 text: {exc}") from exc
    return found


def archive_named(
    payload: bytes, name: str, source: str, suffix: str | None = None
) -> dict[str, str]:
    """Every file in the archive with this basename, keyed by its path, plus
    every file with ``suffix``: the `CMakeLists.txt` tree and the `.cmake`
    modules. A file that is not UTF-8 is left out.
    """
    found: dict[str, str] = {}
    with _open(payload, source) as archive:
        for member in archive.names:
            parts = PurePosixPath(member).parts
            if len(parts) < 2:
                continue
            if parts[-1] != name and not (
                suffix is not None and parts[-1].endswith(suffix)
            ):
                continue
            try:
                text = archive.read(member).decode("utf-8")
            except UnicodeDecodeError:
                continue
            found["/".join(parts[1:])] = text
    return found


def parse_archive(
    payload: bytes, source: str, metadata: str | None = None
) -> UpstreamMetadata:
    """Read the metadata out of an already-downloaded archive. ``metadata``
    names the file to read where the one at the root is not the right one
    (v1 §4).
    """
    try:
        with _open(payload, source) as archive:
            if metadata is not None:
                return _at_path(archive, metadata, source)
            pyproject = _read(archive, _shallowest(archive.names, "pyproject.toml"))
            pkg_info = _read(archive, _shallowest(archive.names, "PKG-INFO"))
            scripts = _computed_scripts(archive)
    except UnicodeDecodeError as exc:
        raise ForgeError(f"{source}: metadata is not UTF-8 text: {exc}") from exc
    except UpstreamError as exc:
        raise ForgeError(str(exc)) from exc

    try:
        return _reconcile_sources(pyproject, pkg_info, source, scripts)
    except UpstreamError as exc:
        raise ForgeError(str(exc)) from exc


def _at_path(archive: _Archive, metadata: str, source: str) -> UpstreamMetadata:
    """Read exactly the file config named, and nothing else: an explicit path is
    an instruction rather than a hint.
    """
    member = _member_at(archive.names, metadata)
    if member is None:
        raise ForgeError(
            f"{source}: has no {metadata}\n"
            "  the path is relative to the archive's top-level directory, and "
            "comes from `upstream.metadata` in config"
        )

    text, where = _text(archive, member), f"{source}::{member}"
    name = PurePosixPath(member).name
    if name == "pyproject.toml":
        return replace(parse_pyproject(text, where), declared_in=metadata)
    if name in ("PKG-INFO", "METADATA"):
        return replace(parse_metadata(text, where), declared_in=metadata)
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
    scripts: tuple[EntryPoint, ...] | None = None,
) -> UpstreamMetadata:
    """Take each half of the metadata from the file that can actually state it
    (v1 §3.6.2).

    `pyproject.toml` is preferred whole; where its `[project]` table is
    unreadable, the dependencies come from `PKG-INFO` and `[build-system]`
    still from `pyproject.toml`. The version follows the same rule, since a
    `dynamic` version is stated only by `PKG-INFO`. The scripts are taken
    from a `[project]` table that names them, else from the computed
    `entry_points.txt` (DESIGN.md §9.6).
    """
    if pyproject is not None:
        try:
            parsed = parse_pyproject(*pyproject)
        except UpstreamError:
            # Only worth surviving because PKG-INFO can state the same thing.
            if pkg_info is None:
                raise
        else:
            # A `[project]` table may name a field it will not state; the built
            # sdist's `PKG-INFO` is where it landed.
            parsed = replace(
                parsed, entry_points=_scripts(parsed.entry_points, scripts)
            )
            if parsed.version is None and pkg_info is not None:
                return replace(
                    parsed,
                    version=parse_metadata(*pkg_info).version,
                    declared_in=_declared_in(pyproject, pkg_info),
                )
            return replace(parsed, declared_in=_declared_in(pyproject))

    if pkg_info is None:
        raise ForgeError(f"{source}: contains neither a pyproject.toml nor a PKG-INFO")

    metadata = parse_metadata(*pkg_info)
    if pyproject is None:
        return replace(
            metadata,
            entry_points=_scripts(None, scripts),
            declared_in=_declared_in(pkg_info),
        )
    # The `[project]` table was unreadable; `[build-system]` may not be.
    return replace(
        metadata,
        build_requires=parse_build_requires(*pyproject),
        entry_points=_scripts(parse_entry_points(*pyproject), scripts),
        declared_in=_declared_in(pkg_info, pyproject),
    )


def _scripts(
    declared: tuple[EntryPoint, ...] | None,
    computed: tuple[EntryPoint, ...] | None,
) -> tuple[EntryPoint, ...] | None:
    """What the declaration said, else what the backend computed, else None."""
    return computed if declared is None else declared


def _computed_scripts(archive: _Archive) -> tuple[EntryPoint, ...] | None:
    """What setuptools wrote into the sdist's `.egg-info`, or None without one.

    `egg_info` deletes the file rather than writing an empty one, so the
    directory being there says the backend spoke and the file being absent
    is its answer.
    """
    member = entry_points_member(archive.names)
    if member is not None:
        return parse_entry_points_txt(*_read_required(archive, member))
    if any(_is_egg_info(name) for name in archive.names):
        return ()
    return None


def _read_required(archive: _Archive, member: str) -> tuple[str, str]:
    return _text(archive, member), f"{archive.source}::{member}"


def _is_egg_info(member: str) -> bool:
    return any(part.endswith(".egg-info") for part in PurePosixPath(member).parts[1:])


def entry_points_member(members: Sequence[str]) -> str | None:
    """The `entry_points.txt` a build backend wrote, and not a test fixture: at
    the sdist's `.egg-info` or the wheel's `.dist-info`, nowhere else.
    """
    candidates = [
        member
        for member in members
        if PurePosixPath(member).name == "entry_points.txt"
        and PurePosixPath(member).parent.name.endswith((".egg-info", ".dist-info"))
    ]
    return min(
        candidates,
        key=lambda member: (len(PurePosixPath(member).parts), member),
        default=None,
    )


def _declared_in(*read: tuple[str, str]) -> str:
    """Name the files this metadata was taken from, in that order, with the
    version-bearing top-level directory stripped.
    """
    return " + ".join(
        PurePosixPath(where.partition("::")[2]).as_posix().split("/", 1)[-1]
        for _, where in read
    )


def _text(archive: _Archive, member: str) -> str:
    """One member, decoded. `UnicodeDecodeError` is the caller's to phrase."""
    return archive.read(member).decode("utf-8")


def _read(archive: _Archive, member: str | None) -> tuple[str, str] | None:
    """The member's text and a name for it, or None if it is not there."""
    if member is None:
        return None
    return _text(archive, member), f"{archive.source}::{member}"


def _member_at(members: Sequence[str], metadata: str) -> str | None:
    """The member at the config-given path, inside the top directory or at it."""
    wanted = PurePosixPath(metadata).parts
    return next(
        (m for m in members if PurePosixPath(m).parts[1:] == wanted), None
    ) or next((m for m in members if PurePosixPath(m).parts == wanted), None)


def _shallowest(members: Sequence[str], name: str) -> str | None:
    """The matching member closest to the archive root, or None."""
    candidates = [member for member in members if PurePosixPath(member).name == name]
    return min(
        candidates,
        key=lambda member: (len(PurePosixPath(member).parts), member),
        default=None,
    )
