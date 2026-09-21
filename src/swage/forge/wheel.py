"""The wheel's METADATA, for a release whose sdist does not state its own (v1
§3.6.2).

A `setup.py` project's sdist names itself and says nothing about what it needs;
the wheel built from the same release states the list. This is declarative
metadata upstream published, read as `PKG-INFO` is; swage runs no upstream code.
The bytes are verified against the digest PyPI publishes, which is weaker than
the recipe's pin, and the metadata records that the dependencies came from here.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from dataclasses import replace
from pathlib import PurePosixPath
from typing import Any

from swage.upstream import (
    UpstreamError,
    UpstreamMetadata,
    parse_entry_points_txt,
    parse_metadata,
)

from .archive import Fetcher, download, entry_points_member
from .errors import ForgeError, NotFound

__all__ = ["PYPI_JSON", "wheel_metadata"]

#: PyPI's per-release JSON, which lists every distribution of one version.
PYPI_JSON = "https://pypi.org/pypi/{name}/{version}/json"


def wheel_metadata(
    name: str, version: str, fetch: Fetcher = download
) -> tuple[UpstreamMetadata, str] | None:
    """The metadata in this release's wheel, and the wheel's filename.

    ``None`` where the release publishes no wheel, or PyPI has never heard
    of it: an answer, not an error. Every other failure is a `ForgeError`.
    """
    try:
        payload = fetch(PYPI_JSON.format(name=name, version=version))
    except NotFound:
        return None
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
    """The wheel to read, preferring the pure-Python one; any wheel states the
    release's dependencies.
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
            # The wheel is the built artifact, so here absence is emptiness
            # rather than silence.
            scripts = entry_points_member(wheel.namelist())
            scripts_text = (
                "" if scripts is None else wheel.read(scripts).decode("utf-8")
            )
    except zipfile.BadZipFile as exc:
        raise ForgeError(f"{url}: cannot read as a wheel: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise ForgeError(f"{url}: METADATA is not UTF-8 text: {exc}") from exc

    try:
        return replace(
            parse_metadata(text, filename),
            entry_points=parse_entry_points_txt(scripts_text, f"{filename}::{scripts}"),
        )
    except UpstreamError as exc:
        raise ForgeError(str(exc)) from exc


def _metadata_member(names: list[str]) -> str | None:
    """`pkg-1.2.3.dist-info/METADATA`, and not a vendored copy deeper in
    (v1 §3.6).
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
