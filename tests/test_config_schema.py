"""Tests for the quirks database schema (DESIGN.md 4).

These exercise the models directly. What they are really testing is that the
schema *refuses* things -- a quirks database that silently ignores a key is
worse than one that fails loudly, because the ignored key looks like it is
doing something.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from swage.config import (
    ArchiveUpstream,
    Defaults,
    ExtrasAsOutputs,
    Family,
    Feedstock,
    GitHubUpstream,
)


def test_family_accepts_a_full_definition() -> None:
    family = Family.model_validate(
        {
            "family": "airflow-providers",
            "match": {"feedstock": "apache-airflow-providers-*"},
            "upstream": {
                "source": "github",
                "repo": "apache/airflow",
                "tag": "providers-{slug}/{version}",
                "metadata": "providers/{slug_path}/pyproject.toml",
            },
            "trust": "propose",
            "name_map": {"docker": "docker-py"},
        }
    )
    assert isinstance(family.upstream, GitHubUpstream)
    assert family.upstream.repo == "apache/airflow"
    assert family.name_map == {"docker": "docker-py"}


def test_feedstock_defaults_to_unset_rather_than_guessed() -> None:
    """Unset means "ask the next layer", not "assume something"."""
    feedstock = Feedstock.model_validate({"feedstock": "demo-widget"})
    assert feedstock.family is None
    assert feedstock.trust is None
    assert feedstock.upstream is None
    assert feedstock.outputs == {}
    assert feedstock.name_map == {}
    assert feedstock.embedded_extras == {}


def test_unknown_keys_are_rejected() -> None:
    with pytest.raises(ValidationError, match="trsut"):
        Family.model_validate(
            {
                "family": "demo",
                "match": {"feedstock": "demo-*"},
                "trsut": "propose",
            }
        )


def test_unknown_trust_level_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Feedstock.model_validate({"feedstock": "demo", "trust": "always"})


def test_defaults_must_state_the_trust_floor() -> None:
    """The bottom of the trust ladder is stated out loud, never inferred."""
    with pytest.raises(ValidationError, match="trust"):
        Defaults.model_validate({})
    stated = {"trust": "never", "recipe_owned": {"names": ["python"]}}
    assert Defaults.model_validate(stated).trust == "never"


def test_github_upstream_requires_the_whole_coordinate() -> None:
    with pytest.raises(ValidationError, match="metadata"):
        Family.model_validate(
            {
                "family": "demo",
                "match": {"feedstock": "demo-*"},
                "upstream": {
                    "source": "github",
                    "repo": "apache/airflow",
                    "tag": "providers-{slug}/{version}",
                },
            }
        )


def test_archive_upstream_needs_nothing_but_its_source() -> None:
    feedstock = Feedstock.model_validate(
        {"feedstock": "demo", "upstream": {"source": "archive"}}
    )
    assert isinstance(feedstock.upstream, ArchiveUpstream)
    assert feedstock.upstream.metadata is None


def test_archive_upstream_rejects_a_project_name() -> None:
    """`source.url` locates the archive, so naming the project says nothing.

    Worth a test rather than just an absence: the key was in the schema
    unread for the whole of its life, and the failure it invites -- a config
    that sets it and expects an effect -- is silent unless the model refuses.
    """
    with pytest.raises(ValidationError):
        Feedstock.model_validate(
            {
                "feedstock": "demo",
                "upstream": {"source": "archive", "project": "demo"},
            }
        )


def test_unknown_upstream_source_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Feedstock.model_validate(
            {"feedstock": "demo", "upstream": {"source": "gitlab"}}
        )


def test_an_extra_cannot_be_both_supported_and_skipped() -> None:
    """Contradicting yourself about an extra is a mistake, not a preference."""
    with pytest.raises(ValidationError, match="both 'supported' and 'skip'"):
        ExtrasAsOutputs.model_validate(
            {
                "suffix": "{name}-with-{extra}",
                "supported": ["pandas", "polars"],
                "skip": ["pandas"],
            }
        )


def test_embedded_extras_keeps_an_empty_list() -> None:
    """Declared-but-empty has to survive as a different thing from absent."""
    feedstock = Feedstock.model_validate(
        {
            "feedstock": "demo",
            "embedded_extras": {"aiobotocore[boto3]": [], "pandas[sql-other]": ["a"]},
        }
    )
    assert feedstock.embedded_extras["aiobotocore[boto3]"] == ()
    assert feedstock.embedded_extras["pandas[sql-other]"] == ("a",)


def test_a_skip_entry_must_be_spelled_the_way_swage_reads_it() -> None:
    """`apache.iceberg` would never match, and nothing downstream would say so.

    G3 would report the extra as unaccounted for while the maintainer had
    already declined it in `skip` -- advice pointing at the wrong fix.
    """
    with pytest.raises(ValidationError, match="write 'apache-iceberg'"):
        ExtrasAsOutputs.model_validate(
            {"suffix": "{name}-with-{extra}", "skip": ["apache.iceberg"]}
        )


def test_a_supported_entry_must_be_normalized() -> None:
    with pytest.raises(ValidationError, match="write 'bigquery-v2'"):
        ExtrasAsOutputs.model_validate(
            {"suffix": "{name}-with-{extra}", "supported": ["bigquery_v2"]}
        )


def test_an_output_run_extra_must_be_normalized() -> None:
    with pytest.raises(ValidationError, match="write 'bigquery-v2'"):
        Feedstock.model_validate(
            {
                "feedstock": "demo",
                "outputs": {"demo": {"run": {"extras": ["bigquery_v2"]}}},
            }
        )


def test_an_embedded_extras_key_must_be_normalized() -> None:
    """The key is matched against `UpstreamRequirement.key`, which is normalized."""
    with pytest.raises(ValidationError, match="write 'hive-pure-sasl'"):
        Feedstock.model_validate(
            {"feedstock": "demo", "embedded_extras": {"pyhive[hive_pure_sasl]": []}}
        )


def test_an_embedded_extras_key_naming_no_extra_is_refused() -> None:
    """Without an extra the key can never match, since that is what it keys on."""
    with pytest.raises(ValidationError, match="names no extra"):
        Feedstock.model_validate(
            {"feedstock": "demo", "embedded_extras": {"pyhive": []}}
        )


def test_an_embedded_extras_key_that_is_not_a_requirement_is_refused() -> None:
    with pytest.raises(ValidationError, match="not a requirement"):
        Feedstock.model_validate(
            {"feedstock": "demo", "embedded_extras": {"not a req!!": []}}
        )


def test_an_output_run_skip_must_be_normalized() -> None:
    """Same rule, same reason: a stale spelling never matches (DESIGN.md 3.6.1)."""
    with pytest.raises(ValidationError, match="write 'apache-iceberg'"):
        Feedstock.model_validate(
            {
                "feedstock": "demo",
                "outputs": {"demo": {"run": {"skip": ["apache.iceberg"]}}},
            }
        )


def test_an_output_cannot_both_fold_in_an_extra_and_decline_it() -> None:
    """Two opposite decisions about one extra is a typo, not a policy."""
    with pytest.raises(ValidationError, match="both 'extras' and 'skip': pandas"):
        Feedstock.model_validate(
            {
                "feedstock": "demo",
                "outputs": {
                    "demo": {"run": {"extras": ["pandas"], "skip": ["pandas"]}}
                },
            }
        )


def test_models_are_frozen() -> None:
    """Config is read many times and written never."""
    feedstock = Feedstock.model_validate({"feedstock": "demo"})
    with pytest.raises(ValidationError):
        feedstock.feedstock = "other"
