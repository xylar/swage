"""Resolving a requirement that carries an extra (DESIGN.md 3.2).

The failure these guard against is the quietest one swage can produce.
Falling back to the bare name turns `celery[redis]` into `celery`, which
resolves exactly, renders a line that looks entirely reasonable and builds --
and the recipe never mentions what the extra pulls in. Nothing is visibly
wrong until something fails to import at runtime.

So the refusal is what is tested here, the same way the trust gates are: one
test per way the extra can be accounted for, and then the case where nothing
accounts for it and G2 has to stop the feedstock.
"""

from __future__ import annotations

import pytest

from swage.config import ConfigTree, Layered, MappingLayer, load_config
from swage.mapping import NameResolver, StaticPackageIndex
from swage.plan import (
    PlanError,
    PlannedSection,
    PythonMin,
    RecipePlan,
    Verdict,
    evaluate_gates,
    plan_section,
)
from swage.plan.resolve import resolve_requirement
from swage.recipe import read_recipe
from swage.upstream import RecipeUpstream, UpstreamRequirement, parse_pyproject

from .conftest import WriteTree

PYTHON_MIN = PythonMin("3.10", "recipe")

INDEX = StaticPackageIndex.of(
    "celery",
    "google-api-core",
    "google-api-core-grpc",
    "google-auth",
    "kombu",
    "python",
    "redis",
    "requests",
)

NAME_MAP = MappingLayer(
    "config/name-map.yaml",
    {
        "google-api-core[grpc]": "google-api-core-grpc",
        # An identity entry, and load-bearing: it is how "considered, and the
        # bare name is right" gets on the record (DESIGN.md 3.2).
        "google-auth[pyopenssl]": "google-auth",
    },
)

EMBEDDED: Layered[tuple[str, ...]] = Layered(
    (
        MappingLayer(
            "config/feedstocks/demo.yaml",
            {"celery[redis]": ("redis >=4.5.2", "kombu >=5.3.0")},
        ),
    )
)


def _resolver() -> NameResolver:
    return NameResolver(Layered((NAME_MAP,)), INDEX)


def _requirement(text: str) -> UpstreamRequirement:
    """The one requirement of a throwaway project, parsed as upstream states it."""
    upstream = parse_pyproject(f'[project]\nname = "demo"\ndependencies = ["{text}"]\n')
    return upstream.dependencies[0]


# --- the three ways an extra is accounted for -----------------------------


def test_a_name_map_entry_resolves_the_extra_to_its_own_package() -> None:
    """`google-api-core[grpc]` is a different conda package, not a variant."""
    resolution = resolve_requirement(
        _requirement("google-api-core[grpc] >=2.28.0"), _resolver()
    )
    assert resolution is not None
    assert resolution.conda_name == "google-api-core-grpc"
    assert resolution.exact
    assert resolution.dropped_extras == ()


def test_an_identity_name_map_entry_accounts_for_an_extra() -> None:
    """Mapping the requirement to the bare name is a decision, not a fallback."""
    resolution = resolve_requirement(
        _requirement("google-auth[pyopenssl] >=2.14.1"), _resolver()
    )
    assert resolution is not None
    assert resolution.conda_name == "google-auth"
    assert resolution.exact
    assert resolution.dropped_extras == ()


def test_embedded_extras_accounts_for_an_extra_with_no_conda_package() -> None:
    """Config wrote out what it pulls in, so the bare name is right."""
    resolution = resolve_requirement(
        _requirement("celery[redis] >=5.3.0"), _resolver(), EMBEDDED
    )
    assert resolution is not None
    assert resolution.conda_name == "celery"
    assert resolution.exact
    assert resolution.dropped_extras == ()


# --- and the case where nothing does --------------------------------------


def test_an_unaccounted_extra_resolves_but_is_not_exact() -> None:
    """The bare name still renders; what changes is that swage may not act."""
    resolution = resolve_requirement(_requirement("celery[redis] >=5.3.0"), _resolver())
    assert resolution is not None
    assert resolution.conda_name == "celery"
    assert not resolution.exact
    assert resolution.dropped_extras == ("redis",)
    # Named as upstream wrote it, so the report quotes the requirement rather
    # than the half of it that resolved.
    assert resolution.pypi_name == "celery[redis]"


def test_an_unaccounted_extra_on_an_unknown_package_is_simply_unresolved() -> None:
    """Nothing to fall back to, so this is G2's ordinary unresolved case."""
    assert resolve_requirement(_requirement("nope[thing] >=1"), _resolver()) is None


def test_a_requirement_without_extras_is_unaffected() -> None:
    resolution = resolve_requirement(_requirement("requests >=2.31.0"), _resolver())
    assert resolution is not None
    assert resolution.conda_name == "requests"
    assert resolution.exact
    assert resolution.dropped_extras == ()


# --- what that means for a plan -------------------------------------------

RECIPE = """\
schema_version: 1

package:
  name: demo
  version: 2.0.0

requirements:
  run:
    - python >=${{ python_min }}
    - celery >=5.3.0
"""

DEFAULTS = "trust: never\nrecipe_owned:\n  names: [python, pip]\n"


def _config(write_tree: WriteTree, feedstock: str) -> ConfigTree:
    return load_config(
        write_tree({"defaults.yaml": DEFAULTS, "feedstocks/demo.yaml": feedstock})
    )


def _verdict(
    write_tree: WriteTree, upstream_text: str, feedstock: str
) -> tuple[PlannedSection, Verdict]:
    upstream = parse_pyproject(upstream_text)
    config = _config(write_tree, feedstock).for_feedstock("demo")
    recipe = read_recipe(RECIPE)
    section = plan_section(
        recipe.blocks["/requirements/run"],
        upstream,
        config,
        _resolver(),
        PYTHON_MIN,
        listed_extras=("redis",),
    )
    return section, evaluate_gates(
        RecipePlan(sections=(section,)), config, RecipeUpstream.of(upstream)
    )


CORE_EXTRA = """\
[project]
name = "demo"
version = "2.0.0"
dependencies = ["celery[redis] >=5.3.0"]
"""


def test_g2_stops_a_feedstock_whose_extra_nothing_accounts_for(
    write_tree: WriteTree,
) -> None:
    section, verdict = _verdict(write_tree, CORE_EXTRA, "feedstock: demo\n")

    # The line swage would write is unchanged -- it renders what it can justify
    # and stops, rather than mangling a line or dropping it.
    assert [item.text for item in section.requirements] == [
        "python >=${{ python_min }}",
        "celery >=5.3.0",
    ]

    detail = next(gate for gate in verdict.gates if gate.name == "G2").detail
    assert "G2" in verdict.summary
    assert "`celery[redis]` resolved to `celery`" in detail
    assert "dropping extra `redis`" in detail
    # Both remedies, because either one is a legitimate answer and pointing at
    # only one of them would give half the advice.
    assert "name_map" in detail
    assert "embedded_extras" in detail


def test_g2_passes_once_embedded_extras_accounts_for_it(
    write_tree: WriteTree,
) -> None:
    feedstock = (
        'feedstock: demo\nembedded_extras:\n  "celery[redis]":\n    - redis >=4.5.2\n'
    )
    _, verdict = _verdict(write_tree, CORE_EXTRA, feedstock)
    assert "G2" not in verdict.summary


PLAIN_AND_EXTRA = """\
[project]
name = "demo"
version = "2.0.0"
dependencies = ["celery >=5.3.0"]

[project.optional-dependencies]
redis = ["celery[redis] >=5.3.0"]
"""


def test_g2_stops_an_unaccounted_extra_sharing_a_conda_name_with_a_plain_line(
    write_tree: WriteTree,
) -> None:
    """One rendered line covers both, so the extra is as dropped as if alone.

    Upstream declaring `celery` and `celery[redis]` collapses to a single
    `celery` line. Keeping only the first requirement's provenance would leave
    the gate with nothing to stop on and the recipe short of `redis`.
    """
    _, verdict = _verdict(write_tree, PLAIN_AND_EXTRA, "feedstock: demo\n")
    detail = next(gate for gate in verdict.gates if gate.name == "G2").detail
    assert "dropping extra `redis`" in detail


# --- a dependency conda-forge does not have at all (DESIGN.md 3.2.3) -------

UNPACKAGED = """\
[project]
name = "demo"
version = "2.0.0"
dependencies = ["celery >=5.3.0", "quickjs-ng >=0.14.0"]
"""

#: `apache-beam`'s case, in miniature. The wording is what a later reader
#: checks the decision against, so a bare "not available" would not pass the
#: model's own validator either.
DECLINED = (
    "feedstock: demo\nnot_packaged:\n  quickjs-ng:\n"
    "    reason: >-\n"
    "      only the javascript UDFs use it, and upstream imports it under\n"
    "      try/except and raises a clear error if a pipeline reaches one\n"
)


def test_g2_stops_a_dependency_with_no_conda_package(write_tree: WriteTree) -> None:
    """The default, and it stays the default: swage cannot write the line."""
    _, verdict = _verdict(write_tree, UNPACKAGED, "feedstock: demo\n")

    detail = next(gate for gate in verdict.gates if gate.name == "G2").detail
    assert "no conda-forge package found for `quickjs-ng`" in detail


def test_not_packaged_ships_without_it(write_tree: WriteTree) -> None:
    """Declining the dependency is the same act as declining an extra."""
    section, verdict = _verdict(write_tree, UNPACKAGED, DECLINED)

    assert [item.text for item in section.requirements] == [
        "python >=${{ python_min }}",
        "celery >=5.3.0",
    ]
    assert "G2" not in verdict.summary


def test_not_packaged_refuses_a_name_conda_forge_does_have(
    write_tree: WriteTree,
) -> None:
    """The entry is the only thing keeping the line out, so it cannot go idle.

    conda-forge packaging the thing is exactly when somebody should be told,
    and an entry that quietly stopped applying would say nothing at all.
    """
    feedstock = (
        "feedstock: demo\nnot_packaged:\n  celery:\n"
        "    reason: pretend conda-forge has no celery\n"
    )
    with pytest.raises(PlanError) as raised:
        _verdict(write_tree, UNPACKAGED, feedstock)

    message = str(raised.value)
    assert "`not_packaged` says conda-forge has no 'celery'" in message
    assert "drop it in config/feedstocks/demo.yaml" in message
