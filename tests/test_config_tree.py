"""The quirks database in this repo is a test fixture whether we like it or not.

Every feedstock swage touches is described by these files, so a mistake in them
is a mistake in swage's behavior. They get the same treatment as code.
"""

from __future__ import annotations

from fnmatch import fnmatch

import pytest

from swage.config import Layered, MappingLayer, load_config
from swage.mapping import NameResolver, StaticPackageIndex

from .conftest import CONFIG_ROOT


def test_the_shipped_tree_validates() -> None:
    tree = load_config(CONFIG_ROOT)
    assert tree.defaults.trust == "propose"
    assert set(tree.families) == {
        "airflow-providers",
        "gcloud-aio",
        "google-cloud",
        "microsoft-kiota",
    }


def test_the_shipped_policies_are_pinned() -> None:
    """The fleet-wide answers, asserted so that changing one is visible.

    Each of these decides what swage may do to a feedstock nobody is watching,
    and each is one word in one file. Promoting `test_matrix` to `auto` changed
    what 91 feedstocks would merge unattended and no test noticed, which is
    the wrong amount of friction for a change of that size.
    """
    tree = load_config(CONFIG_ROOT)
    assert tree.defaults.trust == "propose"
    assert tree.defaults.removals == "review"
    assert tree.defaults.dynamic_dependencies == "review"
    assert tree.defaults.test_matrix == "auto"


def test_every_feedstock_file_resolves() -> None:
    """Ambiguous family membership is only caught by resolving each feedstock."""
    tree = load_config(CONFIG_ROOT)
    for name in tree.feedstocks:
        assert tree.for_feedstock(name).feedstock == name


def test_unattended_merging_by_family_is_pinned() -> None:
    """A family granting `auto` blesses every feedstock its glob matches.

    This began as "nothing is blessed yet", and it fired the first time a
    feedstock was promoted on purpose -- which is a tripwire working, and the
    wrong shape for a rule that has to outlive the event it was watching for.
    It became "no family may confer it", on the reasoning that fifty feedstocks
    blessed by one line is exactly the thing that drifts in unnoticed.

    `google-cloud` is why that is now a pin rather than a ban. A family file
    asserts that its members behave alike, so evidence from some of them is
    evidence about all of them -- and copying one conclusion into fifty
    per-feedstock files, for a reason specific to none of them, denies the
    premise the family is built on. Twelve of the fifty were updated in a
    single round with approval the only thing outstanding on any of them, and a
    full audit said the same of the rest.

    So what is checked is that the list stays short and deliberate. Adding a
    family to it costs a line here as well as a line in that family's file,
    which is the friction a fifty-feedstock blessing should cost. A family
    whose members are alike only in their prefix does not belong here at all,
    and that part no test can check.
    """
    tree = load_config(CONFIG_ROOT)
    blessed = {name for name, family in tree.families.items() if family.trust == "auto"}
    assert blessed == {"google-cloud"}


def test_a_blessed_file_says_why() -> None:
    """An entry that silences a check and explains nothing is worth less than none.

    `trust: auto` is the one setting that ends with swage merging somebody's
    pull request unattended, so the file granting it has to carry the argument
    for having granted it -- which is a comment, since the schema has nowhere
    else to put one. Whichever layer grants it owes the reason: a feedstock's
    own file, or the family file that blesses the whole glob at once.
    """
    tree = load_config(CONFIG_ROOT)
    granting = [
        ("families", name)
        for name, family in tree.families.items()
        if family.trust == "auto"
    ] + [
        ("feedstocks", name)
        for name, entry in tree.feedstocks.items()
        if entry.trust == "auto"
    ]
    assert granting, "nothing is blessed, so this test is checking nothing"
    for kind, name in granting:
        text = (CONFIG_ROOT / kind / f"{name}.yaml").read_text(encoding="utf-8")
        comment = [line for line in text.splitlines() if line.startswith("#")]
        assert len(comment) >= 5, f"{name} is blessed with no reason written down"


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


def test_no_feedstock_is_claimed_by_two_families() -> None:
    """Two globs matching one feedstock is a load-time question nobody asks.

    The loader only knows the feedstocks that have files of their own, so an
    ambiguity between two family globs surfaces per feedstock at scan time --
    long after the commit that introduced it. Adding a family is exactly when
    it is cheap to check, so this checks the shipped globs against each other
    rather than against a list of names.
    """
    tree = load_config(CONFIG_ROOT)
    globs = {name: family.match.feedstock for name, family in tree.families.items()}
    for name, glob in globs.items():
        others = [
            other
            for other, pattern in globs.items()
            if other != name
            and (fnmatch(glob.rstrip("*") + "x", pattern) or fnmatch(glob, pattern))
        ]
        assert not others, f"{name} overlaps {others}"
