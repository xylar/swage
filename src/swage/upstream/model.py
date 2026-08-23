"""A normalized view of what upstream declares (DESIGN.md 3).

Two decisions here shape everything downstream.

**Source order is preserved.** DESIGN.md 6 requires that requirements coming
from upstream appear in upstream's own order rather than alphabetically, which
keeps swage's diffs against upstream small and legible. That is only possible
if the order survives this layer, so these are tuples and never sets.

**Nothing is collapsed.** A project routinely declares the same dependency
several times under different environment markers::

    pandas>=2.1.2; python_version <"3.13"
    pandas>=2.2.3; python_version >="3.13" and python_version <"3.14"
    pandas>=2.3.3; python_version >="3.14"

conda-forge builds one noarch package, so the recipe ends up with a single
`pandas` line. Choosing which one is a policy decision that belongs to the
planner, where it can be recorded as provenance and gated. This layer reports
what upstream said, all of it.

**Extra names are the one exception, and they must be normalized.** The two
metadata sources disagree about how an extra is spelled, for the same release
of the same project. `google-cloud-bigquery` 3.43.0's sdist carries both files:

    pyproject.toml   [project.optional-dependencies]  bigquery_v2
    PKG-INFO         Provides-Extra:                  bigquery-v2

Build backends apply PEP 685 when they write METADATA, and nothing applies it
to `pyproject.toml`. The airflow providers make this routine rather than
exotic -- `apache.iceberg`, `cncf.kubernetes`, `microsoft.azure`, `GSSAPI` all
come back hyphenated and lowercased through METADATA -- and it reaches
dependency extras too, where `pyhive[hive_pure_sasl]` becomes
`pyhive[hive-pure-sasl]`.

Left alone, an extra's name would depend on which file an sdist happened to
ship. Config lookups keyed on the other spelling would miss silently, G3 would
report an extra the maintainer had already accounted for, and the marker
comments swage renders would change spelling for no reason the diff explains.
So every extra name is PEP 685-normalized on the way in, from both sources,
and config is written in that form. Package names are *not* normalized here:
that is the mapping layer's job (DESIGN.md 3.2), and it needs the original.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field

from swage.naming import normalize_extra

__all__ = [
    "BUILD_SH",
    "RecipeUpstream",
    "UpstreamMetadata",
    "UpstreamRequirement",
    "normalize_extra",
]

#: The feedstock's own build script, and the second half of what every reader
#: for a compiled project reads. It lives here rather than in one of them
#: because it is a fact about conda-forge rather than about ESMF or about
#: CMake: `common.mk` says which libraries a toggle links and this says which
#: toggles are on, `CMakeLists.txt` says what a guard implies and this says
#: which `-D` flags are passed. Either file alone is confidently wrong
#: (DESIGN.md 3.6.6).
BUILD_SH = "recipe/build.sh"


@dataclass(frozen=True)
class UpstreamRequirement:
    """One dependency exactly as upstream declared it."""

    #: The project name as written upstream, not normalized. Normalization is
    #: the mapping layer's job, and it needs the original to look up quirks.
    name: str
    #: Extras requested *of the dependency*, e.g. ``("boto3",)`` for
    #: ``aiobotocore[boto3]``. PEP 685-normalized and sorted, so that the
    #: `key` below is the same string whichever metadata source it came
    #: from. These drive `embedded_extras` (DESIGN.md 4).
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
class UpstreamMetadata:
    """Everything swage needs from one upstream release."""

    name: str
    version: str | None = None
    requires_python: str | None = None
    #: What upstream needs to *build*, in declaration order -- PEP 518's
    #: ``[build-system] requires``. This is where a recipe's `host` section
    #: comes from, exact pin included: `flit-core ==3.12.0` is upstream's
    #: `flit_core==3.12.0` and not a conda-forge convention (DESIGN.md 3.3.6).
    #:
    #: `None` means upstream declared no `[build-system]` table at all, which
    #: is not the same as declaring an empty one. Absent means the build
    #: backend is PEP 517's implicit setuptools fallback and swage was told
    #: nothing; empty means upstream said "nothing to build with". Collapsing
    #: the two would let the planner read silence as "host should be emptied".
    build_requires: tuple[UpstreamRequirement, ...] | None = None
    #: Upstream's own dependencies, in declaration order.
    dependencies: tuple[UpstreamRequirement, ...] = ()
    #: Extra name -> its dependencies, both in declaration order. Keys are
    #: PEP 685-normalized, so they match whichever source they were read from.
    optional_dependencies: Mapping[str, tuple[UpstreamRequirement, ...]] = field(
        default_factory=dict
    )
    #: Core-metadata fields upstream flagged under PEP 643, lowercased --
    #: `{"requires-dist"}` where an sdist says its dependency list was computed
    #: at build time rather than declared, and so may differ for another build.
    #:
    #: This is reported rather than refused because, unlike a `pyproject.toml`
    #: with `dynamic = ["dependencies"]`, the list is *present and complete* --
    #: the flag says it might change, not that it is missing. Several real
    #: projects (apache-beam, pyspark-client) ship no `[project]` table at all,
    #: so refusing would stop them with usable metadata in hand and no fallback
    #: to reach for. A gate is the right place to decide, the same way an
    #: inexact `Resolution` reaches G2 rather than failing the mapper.
    #:
    #: Only `parse_metadata` ever populates this. `parse_pyproject` refuses the
    #: dependency cases outright, since there it really is nothing to read.
    #:
    #: **Revisit if the gate proves onerous.** How much maintenance this costs
    #: depends on how many feedstocks turn out to be affected and how often
    #: their dependencies actually move, neither of which is known yet. If it
    #: bites, the fix is the shape DESIGN.md 3.3.8 already uses for removals --
    #: a `dynamic_dependencies: review | trust` policy in `defaults.yaml`, so
    #: relaxing it per family or per feedstock is a config commit with an
    #: auditable record, not a code change.
    dynamic_fields: frozenset[str] = frozenset()
    #: Whether the names in this metadata are already conda-forge package
    #: names. True for the readers that map before the planner sees anything:
    #: `cmake` puts every `find_package` name through `cmake-map.yaml` and
    #: `esmf` puts every linker name through `link-map.yaml`, so what comes out
    #: has been answered once already.
    #:
    #: Resolving such a name a second time asks a PyPI table about a name that
    #: is not a PyPI distribution, and the two namespaces overlap: conda-forge
    #: answers the bare `zstd` with `python-zstd` and the bare `blosc` with
    #: `python-blosc`, which are the python bindings and the right answers for
    #: a python distribution asking. `tiledb` links the C library and so does
    #: `libnetcdf`. Nothing collided until those two, because `libnetcdf`,
    #: `hdf5`, `zlib` and the rest of what these readers produce are not names
    #: PyPI publishes.
    conda_names: bool = False
    #: Whether this metadata answers the version half of reconciliation at
    #: all. False for the readers named for a build system: a `CMakeLists.txt`
    #: says which packages, and a `find_package` call carrying a version is so
    #: rare that 64 calls across the fleet's archives produced two, both the
    #: same line (DESIGN.md 3.6.6, 3.6.7).
    #:
    #: Silence from such a reader is not upstream declining to constrain a
    #: package -- it is a build system that has no way of saying so. Read as
    #: the first, it makes every bound a recipe states drift, and reconciling
    #: drift means the recipe loses the bound: `include-what-you-use` pins
    #: `llvmdev` and `clangdev` to one LLVM series through a `llvm_version`
    #: it sets once, and the proposal was to drop both. So where this is False
    #: and the declaration carries no specifier, the recipe's line stands as
    #: written -- template included, since that is what the recipe uses to
    #: keep several such lines in step.
    #:
    #: Per declaration rather than per reader, because a version a reader
    #: *did* read is upstream speaking and reconciles like any other.
    states_versions: bool = True
    #: Which file inside the release stated this, relative to the archive's
    #: top-level directory -- `pyproject.toml`, `PKG-INFO`, `CMakeLists.txt`,
    #: `build/common.mk`. Several, joined by ` + `, where several were needed.
    #:
    #: **Not recoverable after the fact**, which is why it is recorded rather
    #: than derived at the report. `_reconcile_sources` takes each half of the
    #: metadata from whichever file can state it, so an archive shipping both
    #: `pyproject.toml` and `PKG-INFO` has four possible answers and only that
    #: function knows which one happened (DESIGN.md 3.6.2). A reader's answer
    #: is a join across two files, one of them the feedstock's, and neither is
    #: the declaration alone (DESIGN.md 3.6.6).
    #:
    #: The version-bearing top directory is stripped: `CMakeLists.txt` rather
    #: than `netcdf-cxx4-4.3.1/CMakeLists.txt`, so two runs over two releases
    #: are comparable and the answer is a path a reader can act on.
    #:
    #: The wheel fallback below is the one thing this does not cover, and does
    #: not need to: it names the file in the release the recipe pins, and
    #: `dependency_source` says separately where a dependency list came from
    #: when that was a distribution the recipe does not pin.
    declared_in: str = ""
    #: Where `dependencies` came from, when that is not the archive the recipe
    #: builds. Empty for the ordinary case. Set to a wheel's filename where the
    #: sdist declared none and the wheel did (DESIGN.md 3.6.2): the list is
    #: upstream's own either way, but it was read from a distribution the
    #: recipe does not pin, and a reader deciding whether to trust a dependency
    #: should be told which file stated it.
    dependency_source: str = ""
    #: Things the reader wants said about this release that are not
    #: dependencies. Reported beside the verdict and never gated: a note is
    #: advice, and a fact that should stop a feedstock belongs in a gate.
    #:
    #: The esmf reader is what wants this. ESMF vendors a copy of ParallelIO
    #: and states its version, and that version moves between releases -- but
    #: it is not a bound on the packaged `parallelio` the recipe pins by hand,
    #: so the only useful thing to do with it is say it, at the moment
    #: somebody is looking at a version bump (DESIGN.md 3.6.6).
    notes: tuple[str, ...] = ()

    @property
    def extras(self) -> tuple[str, ...]:
        """The extras upstream declares, normalized, in declaration order.

        Every one of these has to be accounted for by `supported`, `skip`, or
        `embedded_extras`, or gate G3 stops the feedstock -- which is what
        keeps a newly added upstream extra from silently vanishing.
        """
        return tuple(self.optional_dependencies)


@dataclass(frozen=True)
class RecipeUpstream:
    """The releases a recipe builds, and which output reconciles against which.

    Almost every recipe builds one release and every output draws on it, which
    is what `of` constructs. A handful build several: `airflow-feedstock`
    packages three sdists at two independent versions, and reconciling all of
    its outputs against whichever came first would be a silently wrong answer
    of the kind DESIGN.md 3.3.2 refuses (DESIGN.md 3.6).

    ``by_output`` covers every output the recipe declares, keyed by the output
    name -- ``""`` for a recipe with no ``outputs`` list at all. An output that
    draws on nothing gets an empty release rather than an absent key, so a
    caller cannot reach a wrong one by missing.
    """

    releases: tuple[UpstreamMetadata, ...]
    by_output: Mapping[str, UpstreamMetadata]

    @classmethod
    def of(cls, metadata: UpstreamMetadata) -> RecipeUpstream:
        """One release, drawn on by every output."""
        return cls(releases=(metadata,), by_output=_Everywhere(metadata))

    @property
    def primary(self) -> UpstreamMetadata:
        """The release the recipe is *of*, which is its first source.

        This is what names the feedstock's version -- the commit message, the
        report line, the workbench heading. A recipe building several is still
        a recipe for one thing, and the source order says which: `airflow`'s
        `apache-airflow` sdist comes first and `3.3.1` is the version the pull
        request bumped.
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
        """The primary release's file, for the same reason `version` is its own.

        A recipe building several archives is reported by its first, which is
        the one the pull request title names. `swage draft` writes every
        source's metadata out, so the reader who needs the others has them.
        """
        return self.primary.declared_in

    @property
    def notes(self) -> tuple[str, ...]:
        """What every release this recipe builds had to say about itself.

        In source order and de-duplicated, so a recipe building several
        archives that make the same remark makes it once.
        """
        seen: dict[str, None] = {}
        for release in self.releases:
            for note in release.notes:
                seen.setdefault(note, None)
        return tuple(seen)

    def for_output(self, output: str) -> UpstreamMetadata:
        """The release this output's requirements are reconciled against."""
        return self.by_output[output]

    @property
    def extras(self) -> tuple[str, ...]:
        """Every extra any of these releases declares, in source order.

        Flat, and deliberately: an extra is accounted for by name, and the
        `skip` list that records "considered and declined" is a statement
        about the feedstock rather than about one of its archives. Two
        releases declaring an extra of the same name -- `airflow`'s core and
        task-sdk both declare `otel` and `statsd` -- are one decision, and
        asking for it twice would be asking the same question twice.
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
    """One release, for whatever output is asked about.

    A single-source recipe's outputs all reconcile against the same archive,
    and there is no point in the resolver walking the recipe to write the same
    value under every name -- nor in `RecipeUpstream.of` needing a recipe to
    build one at all, which most of the tests do not have.
    """

    def __init__(self, metadata: UpstreamMetadata) -> None:
        self._metadata = metadata

    def __getitem__(self, key: str) -> UpstreamMetadata:
        return self._metadata

    def __iter__(self) -> Iterator[str]:
        raise TypeError("a single-source recipe's outputs are not enumerated here")

    def __len__(self) -> int:
        raise TypeError("a single-source recipe's outputs are not enumerated here")
