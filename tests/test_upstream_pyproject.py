"""Tests for reading upstream metadata from pyproject.toml (design-v1.md 3).

The eight vendored corpus triples carry real airflow provider metadata, so
these run against what upstream actually ships rather than against invented
examples.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from swage.upstream import (
    EntryPoint,
    UpstreamError,
    normalize_extra,
    parse_entry_points,
    parse_entry_points_txt,
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
    """design-v1.md 6 orders requirements by upstream's order, not alphabetically."""
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
    """`embedded_extras` is keyed by exactly this (design-v1.md 4)."""
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
    dependencies (design-v1.md 3.3.6).
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


# --- entry points (design-v1.md 3.3.15) ----------------------------------------


def test_scripts_and_gui_scripts_are_read_in_declaration_order() -> None:
    """A recipe's `entry_points` draws no distinction, so neither does swage."""
    metadata = parse_pyproject(
        '[project]\nname = "m2r2"\n'
        '[project.scripts]\nm2r2 = "m2r2.cli.m2r2:main"\n'
        '[project.gui-scripts]\nm2r2-gui = "m2r2.gui:main"\n'
    )
    assert metadata.entry_points is not None
    assert [point.text for point in metadata.entry_points] == [
        "m2r2 = m2r2.cli.m2r2:main",
        "m2r2-gui = m2r2.gui:main",
    ]


def test_a_project_table_naming_no_script_states_that_there_are_none() -> None:
    """PEP 621: a backend may supply a field only where the table says `dynamic`."""
    metadata = parse_pyproject('[project]\nname = "calver"\n')
    assert metadata.entry_points == ()


def test_dynamic_scripts_are_none_rather_than_empty() -> None:
    """Silence and emptiness are different claims, here as for `build_requires`.

    Reading a `dynamic = ["scripts"]` table as "declares none" would have the
    planner empty a recipe's list on the strength of a file that was never
    going to state it.
    """
    metadata = parse_pyproject('[project]\nname = "x"\ndynamic = ["scripts"]\n')
    assert metadata.entry_points is None


def test_poetry_s_own_table_is_read_where_there_is_no_project_table() -> None:
    """A poetry sdist carries no `entry_points.txt`, so this is the only place.

    The long form names a callable and the extras it needs; the `file` form
    is a script poetry copies into place and no entry point at all.
    """
    found = parse_entry_points(
        '[tool.poetry]\nname = "x"\n'
        "[tool.poetry.scripts]\n"
        'a = "a:main"\n'
        'b = { callable = "b:main", extras = ["x"] }\n'
        'c = { reference = "bin/c", type = "file" }\n'
    )
    assert found is not None
    assert [point.text for point in found] == ["a = a:main", "b = b:main"]


def test_a_file_with_neither_table_cannot_say() -> None:
    assert parse_entry_points("[build-system]\nrequires = []\n") is None


def test_entry_points_txt_reads_the_two_script_groups_and_nothing_else() -> None:
    """Every other group -- `pytest11`, a project's own plugins -- is not a script.

    The extras a line may end in are dropped: a recipe's list has no place
    for them, and the dependencies behind them are reconciled through the
    extras machinery already. Names keep their case, since configparser
    would otherwise lowercase a script called `Influx3`.
    """
    found = parse_entry_points_txt(
        "[console_scripts]\n"
        "influx3 = influxdb_client_3.cli:main [cli,extra]\n"
        "[pytest11]\n"
        "foo = foo.plugin\n"
        "[gui_scripts]\n"
        "Viewer = viewer.app:main\n"
    )
    assert [point.text for point in found] == [
        "influx3 = influxdb_client_3.cli:main",
        "Viewer = viewer.app:main",
    ]


def test_an_entry_points_txt_with_no_script_group_states_none() -> None:
    assert parse_entry_points_txt("[pytest11]\nfoo = foo.plugin\n") == ()


def test_a_backend_table_without_scripts_states_that_there_are_none() -> None:
    """poetry and flit install what their table names and nothing else."""
    assert parse_entry_points('[tool.poetry]\nname = "x"\n') == ()
    assert parse_entry_points('[tool.flit.metadata]\nmodule = "x"\n') == ()
    assert parse_entry_points(
        '[tool.flit.metadata]\nmodule = "x"\n[tool.flit.scripts]\nx = "x:main"\n'
    ) == (EntryPoint("x", "x:main"),)
