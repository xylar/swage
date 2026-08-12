"""Tests for reading upstream metadata from pyproject.toml (DESIGN.md 3).

The eight vendored corpus triples carry real airflow provider metadata, so
these run against what upstream actually ships rather than against invented
examples.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from swage.upstream import (
    UpstreamError,
    normalize_extra,
    parse_pyproject,
    parse_requirement,
)

from .conftest import REPO_ROOT

CORPUS = REPO_ROOT / "tests" / "corpus" / "airflow-providers"
PYPROJECTS = sorted(CORPUS.glob("*/pyproject.toml"))


@pytest.mark.parametrize("path", PYPROJECTS, ids=lambda p: p.parent.name)
def test_every_corpus_pyproject_parses(path: Path) -> None:
    metadata = parse_pyproject(path.read_text(encoding="utf-8"), str(path))
    assert metadata.name.startswith("apache-airflow-providers-")
    assert metadata.version
    assert metadata.requires_python
    assert metadata.dependencies


def test_dependencies_keep_upstream_source_order() -> None:
    """DESIGN.md 6 orders requirements by upstream's order, not alphabetically."""
    path = CORPUS / "providers-databricks_7.18.1" / "pyproject.toml"
    metadata = parse_pyproject(path.read_text(encoding="utf-8"), str(path))
    assert [requirement.name for requirement in metadata.dependencies][:5] == [
        "apache-airflow",
        "apache-airflow-providers-common-compat",
        "apache-airflow-providers-common-sql",
        "requests",
        "databricks-sql-connector",
    ]


def test_marker_variants_are_all_reported() -> None:
    """Three pandas entries in, three out.

    Collapsing them is the planner's decision to make and record, not
    something this layer should do quietly.
    """
    path = CORPUS / "providers-databricks_7.18.1" / "pyproject.toml"
    metadata = parse_pyproject(path.read_text(encoding="utf-8"), str(path))
    pandas = [r for r in metadata.dependencies if r.name == "pandas"]
    assert len(pandas) == 3
    assert [r.specifier for r in pandas] == [">=2.1.2", ">=2.2.3", ">=2.3.3"]
    assert all(r.marker is not None for r in pandas)
    assert 'python_version >= "3.14"' in str(pandas[-1].marker)


def test_unconditional_requirements_have_no_marker() -> None:
    path = CORPUS / "providers-databricks_7.18.1" / "pyproject.toml"
    metadata = parse_pyproject(path.read_text(encoding="utf-8"), str(path))
    airflow = next(r for r in metadata.dependencies if r.name == "apache-airflow")
    assert airflow.marker is None
    assert airflow.specifier == ">=2.11.0"


def test_extras_are_reported_in_declaration_order() -> None:
    path = CORPUS / "providers-databricks_7.18.1" / "pyproject.toml"
    metadata = parse_pyproject(path.read_text(encoding="utf-8"), str(path))
    assert metadata.extras[:4] == ("avro", "amazon", "azure-identity", "fab")
    assert metadata.optional_dependencies["amazon"][0].name == (
        "apache-airflow-providers-amazon"
    )


def test_dotted_extra_names_are_normalized() -> None:
    """`cncf.kubernetes` is `cncf-kubernetes` once METADATA has been through it.

    Normalizing both sources is what keeps an extra's spelling from depending
    on which file an sdist happened to ship.
    """
    path = CORPUS / "providers-common-sql_2.1.0" / "pyproject.toml"
    metadata = parse_pyproject(path.read_text(encoding="utf-8"), str(path))
    assert "apache-iceberg" in metadata.extras
    assert "apache.iceberg" not in metadata.extras


def test_uppercase_extra_names_are_normalized() -> None:
    path = CORPUS / "providers-apache-hive_9.6.1" / "pyproject.toml"
    metadata = parse_pyproject(path.read_text(encoding="utf-8"), str(path))
    assert "gssapi" in metadata.extras
    assert "GSSAPI" not in metadata.extras


@pytest.mark.parametrize("path", PYPROJECTS, ids=lambda p: p.parent.name)
def test_no_corpus_extra_survives_unnormalized(path: Path) -> None:
    metadata = parse_pyproject(path.read_text(encoding="utf-8"), str(path))
    assert [extra for extra in metadata.extras if extra != normalize_extra(extra)] == []


def test_a_dependency_extra_is_normalized_into_the_key() -> None:
    """`embedded_extras` is keyed by this, so it must not vary by source."""
    requirement = parse_requirement("pyhive[hive_pure_sasl]>=0.7.0")
    assert requirement.extras == ("hive-pure-sasl",)
    assert requirement.key == "pyhive[hive-pure-sasl]"


def test_extras_that_normalize_alike_collapse_into_one_key() -> None:
    assert parse_requirement("pkg[a.b,a_b]>=1").key == "pkg[a-b]"


def test_two_spellings_of_one_extra_are_refused() -> None:
    """Keeping the last would silently drop the other's dependencies."""
    with pytest.raises(UpstreamError, match="same extra once normalized"):
        parse_pyproject(
            '[project]\nname = "demo"\n[project.optional-dependencies]\n'
            'foo_bar = ["a"]\nfoo-bar = ["b"]\n'
        )


def test_a_dependency_carrying_an_extra_keeps_it() -> None:
    """`embedded_extras` is keyed by exactly this (DESIGN.md 4)."""
    requirement = parse_requirement("aiobotocore[boto3]>=2.5.4")
    assert requirement.name == "aiobotocore"
    assert requirement.extras == ("boto3",)
    assert requirement.key == "aiobotocore[boto3]"
    assert requirement.specifier == ">=2.5.4"


def test_multiple_extras_produce_a_stable_key() -> None:
    """PEP 508 parses extras into a set, so swage sorts to stay deterministic."""
    assert parse_requirement("pkg[b,a]>=1").key == "pkg[a,b]"
    assert parse_requirement("pkg[a,b]>=1").key == "pkg[a,b]"


def test_a_bare_requirement_has_no_specifier() -> None:
    requirement = parse_requirement("apache-airflow-providers-standard")
    assert requirement.name == "apache-airflow-providers-standard"
    assert requirement.specifier == ""
    assert requirement.extras == ()


def test_the_original_string_is_kept() -> None:
    requirement = parse_requirement("aiohttp>=3.14.0, <4")
    assert requirement.raw == "aiohttp>=3.14.0, <4"


def test_build_requires_is_read_from_the_build_system_table() -> None:
    """`flit-core ==3.12.0` in a recipe's host section comes from here.

    It looks like a conda-forge convention and is not one: the exact pin is
    upstream's, just declared in a different table than the runtime
    dependencies (DESIGN.md 3.3.6).
    """
    path = CORPUS / "providers-databricks_7.18.1" / "pyproject.toml"
    metadata = parse_pyproject(path.read_text(encoding="utf-8"), str(path))
    assert metadata.build_requires is not None
    assert [r.name for r in metadata.build_requires] == ["flit_core"]
    assert metadata.build_requires[0].specifier == "==3.12.0"


@pytest.mark.parametrize("path", PYPROJECTS, ids=lambda p: p.parent.name)
def test_every_corpus_pyproject_declares_its_build_backend(path: Path) -> None:
    metadata = parse_pyproject(path.read_text(encoding="utf-8"), str(path))
    assert metadata.build_requires


def test_an_absent_build_system_table_is_none_not_empty() -> None:
    """Absent and empty are different claims, and host depends on which.

    An empty tuple would tell the planner upstream needs nothing to build,
    which is how a host section gets emptied. None says swage was told
    nothing.
    """
    metadata = parse_pyproject('[project]\nname = "demo"\n')
    assert metadata.build_requires is None


def test_an_empty_build_system_requires_is_empty_not_none() -> None:
    metadata = parse_pyproject(
        '[project]\nname = "demo"\n[build-system]\nrequires = []\n'
    )
    assert metadata.build_requires == ()


def test_a_build_system_table_without_requires_is_an_error() -> None:
    """PEP 518 makes `requires` mandatory once the table exists."""
    with pytest.raises(UpstreamError, match="no requires"):
        parse_pyproject(
            '[project]\nname = "demo"\n[build-system]\n'
            'build-backend = "setuptools.build_meta"\n'
        )


def test_an_unparseable_build_requirement_names_the_table() -> None:
    with pytest.raises(UpstreamError, match=r"\[build-system\] requires"):
        parse_pyproject(
            '[project]\nname = "demo"\n'
            '[build-system]\nrequires = ["not a requirement!!"]\n'
        )


def test_dynamic_dependencies_are_refused() -> None:
    """The dangerous case: no dependencies to read looks like none declared.

    swage would then strip the recipe's requirements on that basis, so this
    has to stop rather than return an empty list.
    """
    with pytest.raises(UpstreamError, match="dynamic"):
        parse_pyproject(
            '[project]\nname = "demo"\ndynamic = ["dependencies"]\n',
        )


def test_dynamic_optional_dependencies_are_refused() -> None:
    with pytest.raises(UpstreamError, match="dynamic"):
        parse_pyproject(
            '[project]\nname = "demo"\ndynamic = ["optional-dependencies"]\n',
        )


def test_a_dynamic_version_is_fine() -> None:
    """Only the dependency fields matter; swage does not bump versions."""
    metadata = parse_pyproject('[project]\nname = "demo"\ndynamic = ["version"]\n')
    assert metadata.name == "demo"
    assert metadata.version is None


def test_a_project_with_no_dependencies_is_allowed() -> None:
    metadata = parse_pyproject('[project]\nname = "demo"\n')
    assert metadata.dependencies == ()
    assert metadata.extras == ()


def test_missing_project_table_is_an_error() -> None:
    with pytest.raises(UpstreamError, match=r"no \[project\] table"):
        parse_pyproject('[build-system]\nrequires = ["hatchling"]\n')


def test_missing_name_is_an_error() -> None:
    with pytest.raises(UpstreamError, match="no name"):
        parse_pyproject('[project]\nversion = "1.0"\n')


def test_invalid_toml_is_an_error() -> None:
    with pytest.raises(UpstreamError, match="invalid TOML"):
        parse_pyproject("[project\nname = broken\n")


def test_an_unparseable_requirement_names_the_field_it_came_from() -> None:
    with pytest.raises(UpstreamError, match=r"optional-dependencies\.extra"):
        parse_pyproject(
            '[project]\nname = "demo"\n'
            '[project.optional-dependencies]\nextra = ["not a requirement!!"]\n'
        )


def test_a_non_string_requirement_is_an_error() -> None:
    with pytest.raises(UpstreamError, match="non-string"):
        parse_pyproject('[project]\nname = "demo"\ndependencies = [1]\n')
