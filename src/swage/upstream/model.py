"""A normalized view of what upstream declares (DESIGN.md §6.1).

Source order is preserved and nothing is collapsed: choosing between marker
variants is the planner's. Extra names are PEP 685-normalized on the way in,
from both sources, because the two spell them differently; package names are
not, since the mapping layer needs the original (v1 §3.2).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field

from swage.naming import normalize_extra

__all__ = [
    "BUILD_SH",
    "EntryPoint",
    "RecipeUpstream",
    "UpstreamMetadata",
    "UpstreamRequirement",
    "normalize_extra",
]

#: The feedstock's own build script: the second half of what every reader for a
#: compiled project reads (v1 §3.6.6).
BUILD_SH = "recipe/build.sh"


@dataclass(frozen=True)
class UpstreamRequirement:
    """One dependency exactly as upstream declared it."""

    #: The project name as written upstream, not normalized. Normalization is
    #: the mapping layer's job, and it needs the original to look up quirks.
    name: str
    #: Extras requested of the dependency, e.g. ``("boto3",)`` for
    #: ``aiobotocore[boto3]``, normalized and sorted so `key` is stable across
    #: sources. These drive `embedded_extras` (v1 §4).
    extras: tuple[str, ...] = ()
    #: The version specifier as written, e.g. ``">=2.3.3,<3"``. May be empty.
    specifier: str = ""
    #: The PEP 508 environment marker, or None if the requirement is
    #: unconditional.
    marker: str | None = None
    #: The original string, kept so an error message can quote what it saw.
    raw: str = ""

    @property
    def key(self) -> str:
        """Name and extras together, which is what `embedded_extras` is keyed by."""
        if not self.extras:
            return self.name
        return f"{self.name}[{','.join(self.extras)}]"


@dataclass(frozen=True)
class EntryPoint:
    """One console or GUI script upstream declares, as a recipe writes it:
    `name = module:attr`, extras dropped (DESIGN.md §6.3).
    """

    name: str
    target: str

    @property
    def text(self) -> str:
        return f"{self.name} = {self.target}"


@dataclass(frozen=True)
class UpstreamMetadata:
    """Everything swage needs from one upstream release."""

    name: str
    version: str | None = None
    requires_python: str | None = None
    #: What upstream needs to build, in declaration order: `[build-system]
    #: requires`, which `host` reconciles against (v1 §3.3.6). `None` means no
    #: table at all, which is not an empty one (v1 §3.6.2).
    build_requires: tuple[UpstreamRequirement, ...] | None = None
    #: Upstream's own dependencies, in declaration order.
    dependencies: tuple[UpstreamRequirement, ...] = ()
    #: Extra name -> its dependencies, both in declaration order. Keys are
    #: PEP 685-normalized, so they match whichever source they were read from.
    optional_dependencies: Mapping[str, tuple[UpstreamRequirement, ...]] = field(
        default_factory=dict
    )
    #: Core-metadata fields upstream flagged under PEP 643, lowercased. Reported
    #: rather than refused, because the list is present and complete (v1
    #: §3.6.3); G10 decides. Only `parse_metadata` populates this.
    dynamic_fields: frozenset[str] = frozenset()
    #: Whether the names in this metadata are already conda-forge package names,
    #: as `cmake` and `esmf` map them; resolving such a name again would ask a
    #: PyPI table about a C library.
    conda_names: bool = False
    #: Whether this metadata answers the version half of reconciliation. False
    #: for a reader named for a build system, where silence is not upstream
    #: declining to constrain (v1 §3.6.6, §3.6.7): the recipe's line then stands
    #: as written (DESIGN.md §9.4). Per declaration, since a version a reader
    #: did read is upstream speaking.
    states_versions: bool = True
    #: Which file inside the release stated this, relative to the archive's
    #: top-level directory; several joined by ` + `. Recorded because
    #: `_reconcile_sources` takes each half from whichever file can state it (v1
    #: §3.6.2). The wheel fallback is `dependency_source`.
    declared_in: str = ""
    #: Where `dependencies` came from, when that is not the archive the recipe
    #: builds: a wheel's filename (v1 §3.6.2). Empty for the ordinary case.
    dependency_source: str = ""
    #: Things the reader wants said about this release that are not
    #: dependencies: reported beside the verdict and never gated (v1 §3.6.6).
    notes: tuple[str, ...] = ()
    #: The console and GUI scripts this release installs, in declaration order
    #: (DESIGN.md §6.3). `None` means the source cannot say; empty means
    #: upstream installs none.
    entry_points: tuple[EntryPoint, ...] | None = None

    @property
    def extras(self) -> tuple[str, ...]:
        """The extras upstream declares, normalized, in declaration order, every
        one of which G3 asks about.
        """
        return tuple(self.optional_dependencies)


@dataclass(frozen=True)
class RecipeUpstream:
    """The releases a recipe builds, and which output reconciles against which
    (v1 §3.6).

    ``by_output`` covers every output the recipe declares, keyed by the
    output name, ``""`` for a recipe with no ``outputs`` list. An output that
    draws on nothing gets an empty release.
    """

    releases: tuple[UpstreamMetadata, ...]
    by_output: Mapping[str, UpstreamMetadata]

    @classmethod
    def of(cls, metadata: UpstreamMetadata) -> RecipeUpstream:
        """One release, drawn on by every output."""
        return cls(releases=(metadata,), by_output=_Everywhere(metadata))

    @property
    def primary(self) -> UpstreamMetadata:
        """The release the recipe is *of*, which is its first source: what names
        the feedstock's version.
        """
        return self.releases[0]

    @property
    def name(self) -> str:
        return self.primary.name

    @property
    def version(self) -> str | None:
        return self.primary.version

    @property
    def dependency_source(self) -> str:
        return self.primary.dependency_source

    @property
    def declared_in(self) -> str:
        """The primary release's file, for the reason `version` is its own."""
        return self.primary.declared_in

    @property
    def notes(self) -> tuple[str, ...]:
        """What every release this recipe builds had to say about itself, in source
        order and de-duplicated.
        """
        seen: dict[str, None] = {}
        for release in self.releases:
            for note in release.notes:
                seen.setdefault(note, None)
        return tuple(seen)

    def for_output(self, output: str) -> UpstreamMetadata:
        """The release this output's requirements are reconciled against."""
        return self.by_output[output]

    def map(
        self, transform: Callable[[UpstreamMetadata], UpstreamMetadata]
    ) -> RecipeUpstream:
        """Each release passed through ``transform``, drawn on by the same
        outputs as before.
        """
        if isinstance(self.by_output, _Everywhere):
            return RecipeUpstream.of(transform(self.primary))
        releases = tuple(transform(release) for release in self.releases)
        # An output drawing on nothing has an empty release of its own.
        moved = {id(old): new for old, new in zip(self.releases, releases, strict=True)}
        return RecipeUpstream(
            releases=releases,
            by_output={
                output: moved.get(id(release)) or transform(release)
                for output, release in self.by_output.items()
            },
        )

    @property
    def extras(self) -> tuple[str, ...]:
        """Every extra any of these releases declares, in source order. Flat,
        because an extra is accounted for by name, per feedstock.
        """
        seen: dict[str, None] = {}
        for release in self.releases:
            for extra in release.extras:
                seen.setdefault(extra, None)
        return tuple(seen)

    @property
    def dynamic_fields(self) -> frozenset[str]:
        """What any release computed at build time rather than declaring."""
        return frozenset().union(*(release.dynamic_fields for release in self.releases))


class _Everywhere(Mapping[str, UpstreamMetadata]):
    """One release, for whatever output is asked about: a single-source
    recipe's outputs all reconcile against the same archive.
    """

    def __init__(self, metadata: UpstreamMetadata) -> None:
        self._metadata = metadata

    def __getitem__(self, key: str) -> UpstreamMetadata:
        return self._metadata

    def __iter__(self) -> Iterator[str]:
        raise TypeError("a single-source recipe's outputs are not enumerated here")

    def __len__(self) -> int:
        raise TypeError("a single-source recipe's outputs are not enumerated here")
