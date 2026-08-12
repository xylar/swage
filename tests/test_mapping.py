"""Tests for name resolution (DESIGN.md 3.2).

Most of these test what the resolver *refuses* to do. A resolver that always
returns something is worse than useless here: gate G2 exists to stop swage
acting on a name it cannot justify, and it can only do that if "I don't know"
is a real answer.
"""

from __future__ import annotations

import pytest

from swage.config import Layered, MappingLayer, load_config
from swage.mapping import (
    IDENTITY,
    NameResolver,
    Resolution,
    StaticPackageIndex,
    normalize_name,
)

from .conftest import CONFIG_ROOT

EMPTY_INDEX = StaticPackageIndex.of()


def layered(*layers: tuple[str, dict[str, str]]) -> Layered[str]:
    return Layered(tuple(MappingLayer(source, entries) for source, entries in layers))


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("pandas", "pandas"),
        ("GitPython", "gitpython"),
        ("flit_core", "flit-core"),
        ("ruamel.yaml", "ruamel-yaml"),
        ("zope..interface", "zope-interface"),
        ("Foo_Bar.Baz", "foo-bar-baz"),
    ],
)
def test_normalize_name(raw: str, expected: str) -> None:
    assert normalize_name(raw) == expected


def test_the_most_specific_layer_wins_and_says_so() -> None:
    resolver = NameResolver(
        layered(
            ("config/feedstocks/demo.yaml", {"docker": "docker-from-feedstock"}),
            ("config/families/demo.yaml", {"docker": "docker-from-family"}),
            ("config/name-map.yaml", {"docker": "docker-py"}),
        ),
        EMPTY_INDEX,
    )
    assert resolver.resolve("docker") == Resolution(
        pypi_name="docker",
        conda_name="docker-from-feedstock",
        source="config/feedstocks/demo.yaml",
        exact=True,
    )


def test_a_later_layer_is_used_when_the_earlier_one_is_silent() -> None:
    resolver = NameResolver(
        layered(
            ("config/feedstocks/demo.yaml", {"other": "x"}),
            ("config/name-map.yaml", {"docker": "docker-py"}),
        ),
        EMPTY_INDEX,
    )
    resolution = resolver.resolve("docker")
    assert resolution is not None
    assert resolution.conda_name == "docker-py"
    assert resolution.source == "config/name-map.yaml"


def test_identity_requires_the_package_to_actually_exist() -> None:
    """Otherwise every unknown name resolves to itself and G2 never fires."""
    resolver = NameResolver(layered(), StaticPackageIndex.of("pandas"))
    resolution = resolver.resolve("pandas")
    assert resolution == Resolution("pandas", "pandas", IDENTITY, exact=True)


def test_an_unknown_name_is_unresolved_rather_than_guessed() -> None:
    resolver = NameResolver(layered(), StaticPackageIndex.of("pandas"))
    assert resolver.resolve("some-package-nobody-has") is None


def test_identity_normalizes_before_looking() -> None:
    """conda-forge spells packages in normalized form; PyPI often does not."""
    resolver = NameResolver(layered(), StaticPackageIndex.of("gitpython"))
    resolution = resolver.resolve("GitPython")
    assert resolution is not None
    assert resolution.conda_name == "gitpython"
    assert resolution.source == IDENTITY


def test_a_config_entry_beats_identity() -> None:
    """A human decision outranks a name that merely happens to exist."""
    resolver = NameResolver(
        layered(("config/name-map.yaml", {"docker": "docker-py"})),
        StaticPackageIndex.of("docker", "docker-py"),
    )
    resolution = resolver.resolve("docker")
    assert resolution is not None
    assert resolution.conda_name == "docker-py"
    assert resolution.source == "config/name-map.yaml"


def test_a_config_entry_is_found_through_a_spelling_change() -> None:
    """Upstream renaming `Flit_Core` to `flit-core` must not lose the entry."""
    resolver = NameResolver(
        layered(("config/name-map.yaml", {"flit_core": "flit-core"})),
        EMPTY_INDEX,
    )
    for spelling in ("flit_core", "flit-core", "Flit.Core"):
        resolution = resolver.resolve(spelling)
        assert resolution is not None, spelling
        assert resolution.conda_name == "flit-core"
        assert resolution.source == "config/name-map.yaml"


def test_the_exact_spelling_wins_over_a_normalized_sibling() -> None:
    resolver = NameResolver(
        layered(("config/name-map.yaml", {"Foo-Bar": "first", "foo_bar": "second"})),
        EMPTY_INDEX,
    )
    exact = resolver.resolve("foo_bar")
    assert exact is not None
    assert exact.conda_name == "second"
    normalized_only = resolver.resolve("FOO.BAR")
    assert normalized_only is not None
    assert normalized_only.conda_name == "first"


def test_every_resolution_is_exact_or_absent() -> None:
    """Nothing in this layer produces a guess.

    `exact=False` is reserved for a future inferring source; today a name is
    either justified or unresolved, and G2 depends on that staying true.
    """
    resolver = NameResolver(
        layered(("config/name-map.yaml", {"docker": "docker-py"})),
        StaticPackageIndex.of("pandas"),
    )
    for name in ("docker", "pandas"):
        resolution = resolver.resolve(name)
        assert resolution is not None
        assert resolution.exact


def test_resolution_against_the_shipped_quirks_database() -> None:
    """The real name map, wired to a real feedstock's layers."""
    tree = load_config(CONFIG_ROOT)
    config = tree.for_feedstock("apache-airflow-providers-cncf-kubernetes")
    resolver = NameResolver(config.name_map, StaticPackageIndex.of("requests"))

    kubernetes = resolver.resolve("kubernetes")
    assert kubernetes is not None
    assert kubernetes.conda_name == "python-kubernetes"
    assert kubernetes.source == "config/name-map.yaml"

    # conda-forge splits this one; the library itself is the -core output.
    bigquery = resolver.resolve("google-cloud-bigquery")
    assert bigquery is not None
    assert bigquery.conda_name == "google-cloud-bigquery-core"

    identity = resolver.resolve("requests")
    assert identity is not None
    assert identity.source == IDENTITY
