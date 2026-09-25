"""Tests for expanding an extra that names the project's own (design-v1.md
3.3.12).

The fixtures are real: `sqlalchemy` 2.1.0's PKG-INFO, whose legacy extra
spellings make eleven extras name themselves, and `google-cloud-bigquery`
3.43.0's `all`.
"""

from __future__ import annotations

from swage.config import Layered, MappingLayer
from swage.mapping import NameResolver, StaticPackageIndex
from swage.plan.own_extras import expand_own_extras
from swage.upstream import (
    RecipeUpstream,
    UpstreamMetadata,
    UpstreamRequirement,
    parse_metadata,
    parse_pyproject,
    parse_requirement,
)

from .conftest import REPO_ROOT

CORPUS = REPO_ROOT / "tests" / "corpus"
SQLALCHEMY = parse_metadata(
    (CORPUS / "sqlalchemy" / "PKG-INFO").read_text(encoding="utf-8")
)
BIGQUERY = CORPUS / "google-cloud" / "google-cloud-bigquery"


def _resolver(name_map: dict[str, str] | None = None) -> NameResolver:
    layer = MappingLayer("config/name-map.yaml", name_map or {})
    return NameResolver(Layered((layer,)), StaticPackageIndex.of())


def _expanded(
    release: UpstreamMetadata, name_map: dict[str, str] | None = None
) -> dict[str, tuple[UpstreamRequirement, ...]]:
    return dict(expand_own_extras(release, _resolver(name_map)).optional_dependencies)


def _names(requirements: tuple[UpstreamRequirement, ...]) -> list[str]:
    return [r.name for r in requirements]


def _declaring(**extras: list[str]) -> UpstreamMetadata:
    return UpstreamMetadata(
        name="Pkg",
        optional_dependencies={
            extra: tuple(parse_requirement(raw) for raw in raws)
            for extra, raws in extras.items()
        },
    )


def test_no_extra_names_the_project_once_expanded() -> None:
    """`SQLAlchemy` is the declared name; `sqlalchemy[...]` still matches it."""
    for extra, requirements in _expanded(SQLALCHEMY).items():
        assert "sqlalchemy" not in {r.name.lower() for r in requirements}, extra


def test_a_reference_is_spliced_in_place() -> None:
    extras = _expanded(SQLALCHEMY)
    assert _names(extras["postgresql-asyncpg"]) == ["greenlet", "asyncpg"]
    assert _names(extras["aiosqlite"]) == ["greenlet", "aiosqlite"]
    assert extras["aiosqlite"][0].specifier == ">=1"


def test_an_extra_naming_itself_adds_nothing() -> None:
    """The legacy `mssql_pymssql` alias normalizes onto `mssql-pymssql`."""
    extras = _expanded(SQLALCHEMY)
    assert _names(extras["mssql-pymssql"]) == ["pymssql"]
    assert _names(extras["oracle-oracledb"]) == ["oracledb"]


def test_the_extras_list_is_unchanged() -> None:
    """G3 asks about every extra upstream declares, bundles included."""
    expanded = expand_own_extras(SQLALCHEMY, _resolver())
    assert expanded.extras == SQLALCHEMY.extras
    assert len(expanded.extras) == 26


def test_a_mapped_reference_is_kept() -> None:
    """A `name_map` entry says to depend on the output publishing the extra."""
    extras = _expanded(SQLALCHEMY, {"sqlalchemy[asyncio]": "sqlalchemy-with-asyncio"})
    assert [r.key for r in extras["aiosqlite"]] == ["sqlalchemy[asyncio]", "aiosqlite"]


def test_a_bundle_is_the_union_of_what_it_names() -> None:
    """`all` names nine extras; it installs what they do, each once."""
    for release in (
        parse_metadata((BIGQUERY / "PKG-INFO").read_text(encoding="utf-8")),
        parse_pyproject((BIGQUERY / "pyproject.toml").read_text(encoding="utf-8")),
    ):
        extras = _expanded(release)
        named = [r for extra in extras if extra != "all" for r in extras[extra]]
        bundled = extras["all"]
        assert "google-cloud-bigquery" not in _names(bundled)
        assert len(set(bundled)) == len(bundled)
        assert {(r.name, r.specifier, r.marker) for r in bundled} == {
            (r.name, r.specifier, r.marker) for r in named
        }


def test_a_marker_on_the_reference_is_carried() -> None:
    extras = _expanded(
        _declaring(fast=["ujson"], all=['pkg[fast]; python_version < "3.13"'])
    )
    (spliced,) = extras["all"]
    assert spliced.name == "ujson"
    assert spliced.marker == 'python_version < "3.13"'


def test_markers_on_both_are_conjoined() -> None:
    extras = _expanded(
        _declaring(
            fast=['ujson; platform_system != "Windows"'],
            all=['pkg[fast]; python_version < "3.13"'],
        )
    )
    (spliced,) = extras["all"]
    assert spliced.marker == (
        '(python_version < "3.13") and (platform_system != "Windows")'
    )


def test_a_cycle_terminates() -> None:
    extras = _expanded(_declaring(a=["pkg[b]", "x"], b=["pkg[a]", "y"]))
    assert _names(extras["a"]) == ["y", "x"]
    assert _names(extras["b"]) == ["x", "y"]


def test_another_projects_extra_is_left_alone() -> None:
    """`other[extra]` is an ordinary dependency, resolved by the mapping layer."""
    release = _declaring(s3=["aiobotocore[boto3]"])
    assert _expanded(release) == dict(release.optional_dependencies)


def test_a_multi_source_recipe_keeps_which_output_draws_on_which() -> None:
    other = _declaring(fast=["ujson"], all=["pkg[fast]"])
    empty = UpstreamMetadata(name="nothing")
    upstream = RecipeUpstream(
        releases=(SQLALCHEMY, other),
        by_output={"sqlalchemy": SQLALCHEMY, "pkg": other, "docs": empty},
    )
    expanded = upstream.map(lambda release: expand_own_extras(release, _resolver()))
    assert expanded.releases[0].name == "SQLAlchemy"
    assert expanded.for_output("sqlalchemy") is expanded.releases[0]
    assert expanded.for_output("pkg") is expanded.releases[1]
    assert _names(expanded.for_output("pkg").optional_dependencies["all"]) == ["ujson"]
    assert expanded.for_output("docs") == empty
