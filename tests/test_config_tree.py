"""The quirks database in this repo is a test fixture whether we like it or not.

Every feedstock swage touches is described by these files, so a mistake in them
is a mistake in swage's behaviour. They get the same treatment as code.
"""

from __future__ import annotations

import pytest

from swage.config import load_config

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
