"""Tests for reading core metadata (DESIGN.md 3, DESIGN.md 4).

The fixtures are the real `google-cloud-bigquery` 3.43.0 sdist's own files.
That sdist's sha256 is the one pinned in the corpus recipe beside them, so
these are provably the metadata that recipe was generated from rather than a
plausible reconstruction of it.
"""

from __future__ import annotations

import pytest
from packaging.markers import Marker

from swage.upstream import (
    UpstreamError,
    UpstreamMetadata,
    parse_metadata,
    parse_pyproject,
)

from .conftest import REPO_ROOT

BIGQUERY = REPO_ROOT / "tests" / "corpus" / "google-cloud" / "google-cloud-bigquery"
PKG_INFO = (BIGQUERY / "PKG-INFO").read_text(encoding="utf-8")
PYPROJECT = (BIGQUERY / "pyproject.toml").read_text(encoding="utf-8")


def test_the_real_sdist_metadata_parses() -> None:
    metadata = parse_metadata(PKG_INFO, "PKG-INFO")
    assert metadata.name == "google-cloud-bigquery"
    assert metadata.version == "3.43.0"
    assert metadata.requires_python == ">=3.10"


def test_core_dependencies_exclude_every_extra() -> None:
    """A `Requires-Dist` gated on an extra is that extra's, not the project's."""
    metadata = parse_metadata(PKG_INFO, "PKG-INFO")
    assert [r.name for r in metadata.dependencies] == [
        "google-api-core",
        "google-auth",
        "google-cloud-core",
        "google-resumable-media",
        "packaging",
        "python-dateutil",
        "requests",
    ]


def test_extras_come_from_provides_extra_in_declaration_order() -> None:
    metadata = parse_metadata(PKG_INFO, "PKG-INFO")
    assert metadata.extras == (
        "bqstorage",
        "pandas",
        "ipywidgets",
        "geopandas",
        "ipython",
        "matplotlib",
        "tqdm",
        "opentelemetry",
        "bigquery-v2",
        "all",
    )


def test_both_sources_of_one_sdist_agree_once_normalized() -> None:
    """The test this whole normalization exists for.

    This sdist ships `pyproject.toml` *and* `PKG-INFO`, and they disagree on
    paper: `bigquery_v2` against `bigquery-v2`. Whichever swage reads has to
    produce the same answer, or a recipe's extra comments and its config
    lookups would depend on an sdist packaging detail.
    """
    from_metadata = parse_metadata(PKG_INFO, "PKG-INFO")
    from_pyproject = parse_pyproject(PYPROJECT, "pyproject.toml")

    assert from_metadata.name == from_pyproject.name
    assert from_metadata.requires_python == from_pyproject.requires_python
    assert set(from_metadata.extras) == set(from_pyproject.extras)
    assert "bigquery-v2" in from_metadata.extras
    assert "bigquery_v2" not in from_pyproject.extras

    def signature(
        source: UpstreamMetadata,
    ) -> list[tuple[str, tuple[str, ...], str]]:
        return [(r.name, r.extras, r.specifier) for r in source.dependencies]

    assert signature(from_metadata) == signature(from_pyproject)
    for extra in from_metadata.extras:
        assert [
            (r.name, r.specifier) for r in from_metadata.optional_dependencies[extra]
        ] == [
            (r.name, r.specifier) for r in from_pyproject.optional_dependencies[extra]
        ]


def test_a_marker_keeps_everything_that_is_not_the_extra() -> None:
    """The residual marker is what DESIGN.md 3.3.1 reconciles against python_min.

    `grpcio<2.0.0,>=1.75.1; python_version >= "3.14" and extra == "bqstorage"`
    is the line that makes the recipe say `# more restrictive for python
    >=3.14`, so the version half of the marker has to survive the split.
    """
    metadata = parse_metadata(PKG_INFO, "PKG-INFO")
    bqstorage = metadata.optional_dependencies["bqstorage"]
    grpcio = [r for r in bqstorage if r.name == "grpcio"]
    assert [(r.specifier, r.marker) for r in grpcio] == [
        ("<2.0.0,>=1.59.0", None),
        ("<2.0.0,>=1.75.1", 'python_version >= "3.14"'),
    ]


def test_an_extra_only_marker_leaves_no_residue() -> None:
    metadata = parse_metadata(PKG_INFO, "PKG-INFO")
    pyarrow = next(
        r for r in metadata.optional_dependencies["bqstorage"] if r.name == "pyarrow"
    )
    assert pyarrow.marker is None


def test_a_dependency_carrying_an_extra_keeps_it() -> None:
    """`google-api-core[grpc]` is what the recipe renders as google-api-core-grpc."""
    metadata = parse_metadata(PKG_INFO, "PKG-INFO")
    api_core = metadata.dependencies[0]
    assert api_core.key == "google-api-core[grpc]"


def test_build_requires_is_none_because_the_format_has_none() -> None:
    """Absent, not empty -- otherwise the planner would empty a host section."""
    assert parse_metadata(PKG_INFO, "PKG-INFO").build_requires is None


def test_an_extra_declared_with_no_dependencies_still_exists() -> None:
    """Declared-and-adds-nothing is not absent, and G3 depends on the difference."""
    metadata = parse_metadata(
        "Metadata-Version: 2.4\nName: demo\nProvides-Extra: docs\n\n",
    )
    assert metadata.extras == ("docs",)
    assert metadata.optional_dependencies["docs"] == ()


def test_an_extra_used_but_never_declared_is_still_collected() -> None:
    """Inconsistent metadata, resolved in the direction that cannot lose one."""
    metadata = parse_metadata(
        'Metadata-Version: 2.4\nName: demo\nRequires-Dist: rich; extra == "pretty"\n\n',
    )
    assert metadata.extras == ("pretty",)
    assert metadata.optional_dependencies["pretty"][0].name == "rich"


def test_extra_names_are_normalized() -> None:
    metadata = parse_metadata(
        "Metadata-Version: 2.4\nName: demo\nProvides-Extra: Apache.Iceberg\n"
        'Requires-Dist: pyiceberg; extra == "Apache.Iceberg"\n\n',
    )
    assert metadata.extras == ("apache-iceberg",)
    assert metadata.optional_dependencies["apache-iceberg"][0].name == "pyiceberg"


def test_a_parenthesized_group_survives_the_split() -> None:
    """Only the `extra ==` clause is removed; the rest is the planner's problem."""
    metadata = parse_metadata(
        "Metadata-Version: 2.4\nName: demo\n"
        'Requires-Dist: pywin32; (os_name == "nt" or sys_platform == "win32") '
        'and extra == "win"\n\n',
    )
    marker = metadata.optional_dependencies["win"][0].marker
    assert marker == '(os_name == "nt" or sys_platform == "win32")'


def test_a_residual_marker_is_valid_pep_508() -> None:
    """It is handed to the planner as a marker, so it has to parse as one."""
    metadata = parse_metadata(
        "Metadata-Version: 2.4\nName: demo\n"
        'Requires-Dist: pkg; python_version >= "3.12" and os_name == "posix" '
        'and extra == "x"\n\n',
    )
    marker = metadata.optional_dependencies["x"][0].marker
    assert marker is not None
    assert str(Marker(marker)) == 'python_version >= "3.12" and os_name == "posix"'


def test_an_extra_combined_with_or_is_refused() -> None:
    """No backend emits this, so swage refuses rather than inventing a reading."""
    with pytest.raises(UpstreamError, match="combined with `or`"):
        parse_metadata(
            "Metadata-Version: 2.4\nName: demo\n"
            'Requires-Dist: pkg; extra == "a" or extra == "b"\n\n',
        )


def test_extra_used_other_than_as_equality_is_refused() -> None:
    with pytest.raises(UpstreamError, match="other than as"):
        parse_metadata(
            'Metadata-Version: 2.4\nName: demo\nRequires-Dist: pkg; extra != "a"\n\n',
        )


def test_a_dynamic_requires_dist_is_recorded_and_still_read() -> None:
    """PEP 643 flags a computed list, not a missing one.

    Refusing would strand apache-beam and pyspark-client, which declare no
    `[project]` table to fall back on, while their full dependency list sits
    right there. The uncertainty goes to a gate instead.
    """
    metadata = parse_metadata(
        "Metadata-Version: 2.2\nName: demo\nDynamic: Requires-Dist\n"
        "Requires-Dist: rich>=13\n\n",
    )
    assert metadata.dynamic_fields == frozenset({"requires-dist"})
    assert [r.name for r in metadata.dependencies] == ["rich"]


def test_an_unrelated_dynamic_field_is_recorded_too() -> None:
    """This layer reports what upstream said; the planner decides what matters."""
    metadata = parse_metadata(
        "Metadata-Version: 2.4\nName: demo\nDynamic: license-file\n\n",
    )
    assert metadata.dynamic_fields == frozenset({"license-file"})


def test_the_real_sdist_flags_only_its_licence_file() -> None:
    """google-cloud-bigquery declares its dependencies statically."""
    assert parse_metadata(PKG_INFO, "PKG-INFO").dynamic_fields == frozenset(
        {"license-file"}
    )


def test_pyproject_never_reports_dynamic_fields() -> None:
    """It refuses the dependency cases outright, so there is nothing to carry."""
    assert parse_pyproject(PYPROJECT, "pyproject.toml").dynamic_fields == frozenset()


def test_missing_name_is_an_error() -> None:
    with pytest.raises(UpstreamError, match="no Name"):
        parse_metadata("Metadata-Version: 2.4\nVersion: 1.0\n\n")


def test_two_spellings_of_one_extra_are_refused() -> None:
    with pytest.raises(UpstreamError, match="same extra once normalized"):
        parse_metadata(
            "Metadata-Version: 2.4\nName: demo\n"
            "Provides-Extra: foo_bar\nProvides-Extra: foo-bar\n\n",
        )


def test_an_unparseable_requirement_is_an_error() -> None:
    with pytest.raises(UpstreamError, match="cannot parse requirement"):
        parse_metadata(
            "Metadata-Version: 2.4\nName: demo\nRequires-Dist: not a req!!\n\n",
        )
