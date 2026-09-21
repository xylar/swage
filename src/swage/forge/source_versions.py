"""Maintain the version a recipe pins a second source at (v1 §3.6.5).

The bot bumps one version per feedstock; a recipe building several archives at
independent versions has the others. swage does not choose the version: a
sibling release's exact pin dictates it. swage does author the hash, the one
sha256 it writes rather than checks, and the downloaded archive must declare
that exact project at that exact version. Opt-in per feedstock at
`source_versions`.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass

from swage.config import FeedstockConfig
from swage.mapping import normalize_name
from swage.recipe import Recipe, RecipeSource, resolve_expression
from swage.upstream import RecipeUpstream, UpstreamMetadata

from .archive import Fetcher, download, parse_archive
from .errors import ForgeError
from .upstream import archive_sources

__all__ = ["SourceVersionEdit", "correct_source_versions"]

#: `${{ name }}`, which is how a v1 recipe references a `context` entry.
_REFERENCE = re.compile(r"\$\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


@dataclass(frozen=True)
class SourceVersionEdit:
    """One source's version, moved to what the rest of the recipe requires."""

    #: The `context` entry that names it.
    variable: str
    #: The project the source builds, as its own metadata declares it.
    package: str
    was: str
    now: str
    #: The release whose requirement dictated `now`, for the note swage writes.
    required_by: str

    @property
    def summary(self) -> str:
        """One line, for a commit message and a pull request comment."""
        return (
            f"{self.package} {self.was} to {self.now}, which {self.required_by} "
            f"requires"
        )


@dataclass(frozen=True)
class _Required:
    version: str
    required_by: str


def correct_source_versions(
    recipe: Recipe,
    upstream: RecipeUpstream,
    config: FeedstockConfig,
    fetch: Fetcher = download,
) -> tuple[str, tuple[SourceVersionEdit, ...]]:
    """The recipe text with every stale source version corrected, and what
    moved. Unchanged where there is nothing to do.
    """
    sources = archive_sources(recipe, config.feedstock)
    if len(sources) < 2:
        # A single-source recipe's version is the bot's, and the answer comes
        # from a sibling release, of which there is none.
        return recipe.text, ()

    text = recipe.text
    edits: list[SourceVersionEdit] = []
    for index, release in enumerate(upstream.releases):
        wanted = _required_version(release, upstream.releases)
        if wanted is None or wanted.version == release.version:
            continue
        source = sources[index]
        variable = _naming_variable(recipe, sources, index, release)
        payload = _archive_at(recipe, source, variable, wanted.version, fetch)
        _verify(payload, source, release, wanted.version, config.feedstock)
        text = _rewrite(
            text,
            variable=variable,
            was=release.version or "",
            now=wanted.version,
            old_sha256=source.sha256 or "",
            new_sha256=hashlib.sha256(payload).hexdigest(),
            feedstock=config.feedstock,
        )
        edits.append(
            SourceVersionEdit(
                variable=variable,
                package=release.name,
                was=release.version or "",
                now=wanted.version,
                required_by=wanted.required_by,
            )
        )
    return text, tuple(edits)


def _required_version(
    release: UpstreamMetadata, releases: Sequence[UpstreamMetadata]
) -> _Required | None:
    """The version this recipe's other releases pin this one at, exactly. Only
    an exact pin counts; a range names no version to move to.
    """
    if not release.name:
        return None
    name = normalize_name(release.name)
    found: dict[str, str] = {}
    for other in releases:
        if other is release:
            continue
        for requirement in other.dependencies:
            if normalize_name(requirement.name) != name:
                continue
            pinned = _exact(requirement.specifier)
            if pinned is not None:
                found.setdefault(pinned, other.name)
    if not found:
        return None
    if len(found) > 1:
        listed = ", ".join(f"{version} ({by})" for version, by in sorted(found.items()))
        raise ForgeError(
            f"{release.name}: this recipe's own releases require it at "
            f"{listed}, which cannot all be built\n"
            "  update this feedstock by hand"
        )
    version, required_by = next(iter(found.items()))
    return _Required(version=version, required_by=required_by)


def _exact(specifier: str | None) -> str | None:
    """``1.3.1`` for ``==1.3.1``, and None for anything less definite."""
    if not specifier:
        return None
    clauses = [clause.strip() for clause in specifier.split(",")]
    if len(clauses) != 1 or not clauses[0].startswith("=="):
        return None
    pinned = clauses[0][2:].strip()
    return pinned if pinned and "*" not in pinned else None


def _references(source: RecipeSource) -> list[str]:
    """The `context` entries this source's url expression reads."""
    return [match.group(1) for match in _REFERENCE.finditer(source.url_expr or "")]


def _naming_variable(
    recipe: Recipe,
    sources: Sequence[RecipeSource],
    index: int,
    release: UpstreamMetadata,
) -> str:
    """The one `context` entry that decides this source's version: referenced
    by this source and no other, holding exactly this release's version, and
    not the recipe's own `version`.
    """
    mine: set[str] = set(_references(sources[index]))
    others: set[str] = {
        name
        for position, source in enumerate(sources)
        if position != index
        for name in _references(source)
    }
    version = release.version or ""
    exact: list[str] = [
        name
        for name in sorted(mine - others - {"version"})
        if recipe.context.get(name) == version
    ]
    if len(exact) != 1:
        raise ForgeError(
            f"{release.name} {version}: swage cannot tell which context entry "
            "sets the version this source is pinned at\n"
            f"  the url references {', '.join(sorted(mine)) or 'no context entry'}, "
            f"and {', '.join(exact) or 'none of them'} holds that version and "
            "belongs to this source alone\n"
            "  update this feedstock by hand"
        )
    return exact[0]


def _archive_at(
    recipe: Recipe,
    source: RecipeSource,
    variable: str,
    version: str,
    fetch: Fetcher,
) -> bytes:
    """Download the archive this source would name at ``version``."""
    expression = source.url_expr or ""
    url = resolve_expression(expression, {**recipe.context, variable: version})
    if url is None:
        raise ForgeError(
            f"swage cannot work out the url for {variable} {version} from "
            f"{expression!r}\n  update this feedstock by hand"
        )
    return fetch(url)


def _verify(
    payload: bytes,
    source: RecipeSource,
    release: UpstreamMetadata,
    version: str,
    feedstock: str,
) -> None:
    """Refuse anything that is not the project and version that was asked for:
    what stands in for the hash check swage is not making here.
    """
    found = parse_archive(payload, source.url or "")
    if normalize_name(found.name) != normalize_name(release.name):
        raise ForgeError(
            f"{feedstock}: the archive for {release.name} {version} declares "
            f"itself to be {found.name or '(nothing)'}\n"
            "  update this feedstock by hand"
        )
    if found.version != version:
        raise ForgeError(
            f"{feedstock}: the archive for {release.name} {version} declares "
            f"version {found.version or '(nothing)'}\n"
            "  update this feedstock by hand"
        )


def _rewrite(
    text: str,
    *,
    variable: str,
    was: str,
    now: str,
    old_sha256: str,
    new_sha256: str,
    feedstock: str,
) -> str:
    """Move the `context` entry and the `sha256` beside it, and nothing else.
    Both edits are anchored on something that occurs once, and refuse
    otherwise.
    """
    pattern = re.compile(
        rf"^(?P<lead>\s*{re.escape(variable)}:\s*)"
        rf"(?P<quote>[\"']?){re.escape(was)}(?P=quote)"
        rf"(?P<rest>\s.*)?$",
        re.MULTILINE,
    )
    matches = pattern.findall(text)
    if len(matches) != 1:
        raise ForgeError(
            f"{feedstock}: expected one context entry setting {variable} to "
            f"{was}, found {len(matches)}\n  update this feedstock by hand"
        )
    if text.count(old_sha256) != 1:
        raise ForgeError(
            f"{feedstock}: expected the sha256 for {variable} {was} to appear "
            f"once in the recipe, found {text.count(old_sha256)}\n"
            "  update this feedstock by hand"
        )
    edited = pattern.sub(
        lambda match: (
            f"{match.group('lead')}{match.group('quote')}{now}"
            f"{match.group('quote')}{match.group('rest') or ''}"
        ),
        text,
    )
    return edited.replace(old_sha256, new_sha256)
