"""The quirks database in this repo is a test fixture whether we like it or not.

Every feedstock swage touches is described by these files, so a mistake in them
is a mistake in swage's behaviour. They get the same treatment as code.
"""

from __future__ import annotations

import pytest

from swage.config import Layered, MappingLayer, load_config
from swage.mapping import NameResolver, StaticPackageIndex

from .conftest import CONFIG_ROOT


def test_the_shipped_tree_validates() -> None:
    tree = load_config(CONFIG_ROOT)
    assert tree.defaults.trust == "manual"
    assert set(tree.families) == {"airflow-providers", "google-cloud"}


def test_every_feedstock_file_resolves() -> None:
    """Ambiguous family membership is only caught by resolving each feedstock."""
    tree = load_config(CONFIG_ROOT)
    for name in tree.feedstocks:
        assert tree.for_feedstock(name).feedstock == name


def test_nothing_is_blessed_yet() -> None:
    """Promotion to `auto` is a deliberate commit, not something that drifts in."""
    tree = load_config(CONFIG_ROOT)
    for name in tree.feedstocks:
        assert tree.for_feedstock(name).trust in {"manual", "propose"}


@pytest.mark.parametrize(
    ("feedstock", "family"),
    [
        ("apache-airflow-providers-amazon", "airflow-providers"),
        ("apache-airflow-providers-common-sql", "airflow-providers"),
        ("google-cloud-bigquery", "google-cloud"),
        ("google-cloud-spanner", "google-cloud"),
        ("cartopy", None),
    ],
)
def test_family_globs_match_what_they_are_meant_to(
    feedstock: str, family: str | None
) -> None:
    assert load_config(CONFIG_ROOT).for_feedstock(feedstock).family == family


def test_a_split_feedstock_describes_both_of_its_outputs() -> None:
    """google-cloud-bigquery is the `outputs[].run` model: one sdist, two outputs."""
    resolved = load_config(CONFIG_ROOT).for_feedstock("google-cloud-bigquery")
    assert set(resolved.outputs) == {
        "google-cloud-bigquery",
        "google-cloud-bigquery-core",
    }
    core = resolved.outputs["google-cloud-bigquery-core"].run
    assert core.core is True
    assert core.extras == ()
    metapackage = resolved.outputs["google-cloud-bigquery"].run
    assert metapackage.core is False
    assert "bqstorage" in metapackage.extras


def test_a_provider_describes_the_extras_it_publishes_as_outputs() -> None:
    """common-sql is the `extras_as_outputs` model."""
    resolved = load_config(CONFIG_ROOT).for_feedstock(
        "apache-airflow-providers-common-sql"
    )
    extras = resolved.extras_as_outputs
    assert extras is not None
    assert extras.suffix == "{name}-with-{extra}"
    assert "pandas" in extras.supported
    assert "sqlalchemy" in extras.skip


def test_upstream_is_inherited_from_the_family() -> None:
    resolved = load_config(CONFIG_ROOT).for_feedstock(
        "apache-airflow-providers-common-sql"
    )
    upstream = resolved.upstream
    assert upstream is not None
    assert upstream.source == "github"


def test_the_global_name_map_is_the_last_layer() -> None:
    resolved = load_config(CONFIG_ROOT).for_feedstock("google-cloud-bigquery")
    assert resolved.name_map.layers[-1].source == "config/name-map.yaml"
    # conda-forge splits this feedstock; the library itself is the -core output.
    assert resolved.name_map.lookup("google-cloud-bigquery") == (
        "google-cloud-bigquery-core",
        "config/name-map.yaml",
    )


def test_apache_airflow_keeps_its_own_name_against_grayskull() -> None:
    """The one identity entry in the global map, and why it is not redundant.

    conda-forge publishes both `airflow` and `apache-airflow`, and grayskull's
    table renames the PyPI name to `airflow` -- most likely the name from
    before Apache prefixed its projects. Identity is layer 5, *below*
    grayskull's layer 4, so nothing except an entry in this file can hold the
    name at PyPI's spelling, which is the one the provider recipes depend on.

    Asserted against a grayskull layer that really does say `airflow`, because
    the entry looks like a no-op in isolation -- this is what makes deleting it
    fail a test rather than quietly rename a dependency across ~99 feedstocks.
    """
    config = load_config(CONFIG_ROOT).for_feedstock("apache-airflow-providers-amazon")
    grayskull = MappingLayer("grayskull pypi mapping", {"apache-airflow": "airflow"})
    resolver = NameResolver(
        Layered((*config.name_map.layers, grayskull)),
        StaticPackageIndex.of("airflow", "apache-airflow"),
    )

    resolution = resolver.resolve("apache-airflow")

    assert resolution is not None
    assert resolution.conda_name == "apache-airflow"
    assert resolution.source == "config/name-map.yaml"
