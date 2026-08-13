"""A bound the recipe has and upstream does not (DESIGN.md 3.3.14).

Every other rule in the planner is about which dependencies a section holds.
This one is about a dependency that is staying, and about its *constraint* --
the one thing no gate looked at, so a maintainer's hand-applied ceiling
disappeared with G1 and G2 both satisfied.

Tested for refusal like the rest of the trust gates, and then for the escape
hatch retiring the refusal, which is the pattern the whole quirks database
works on.
"""

from __future__ import annotations

import pytest

from swage.config import ConfigError, ConfigTree, load_config
from swage.mapping import NameResolver, StaticPackageIndex
from swage.plan import PlanError, PlannedSection, PythonMin, plan_section
from swage.plan.tightening import tightening
from swage.recipe import read_recipe
from swage.upstream import parse_pyproject

from .conftest import WriteTree

PYTHON_MIN = PythonMin("3.10", "recipe")
INDEX = StaticPackageIndex.of("apache-airflow", "python", "requests")
DEFAULTS = "trust: manual\nrecipe_owned:\n  names: [python, pip]\n"

UPSTREAM = """\
[project]
name = "demo"
version = "2.0.0"
dependencies = ["apache-airflow >=2.11.0"]
"""

RECIPE = """\
requirements:
  run:
    - python >=${{ python_min }}
    - apache-airflow >=2.11.0,<3.1.3
"""


def _config(write_tree: WriteTree, feedstock: str = "feedstock: demo\n") -> ConfigTree:
    return load_config(
        write_tree({"defaults.yaml": DEFAULTS, "feedstocks/demo.yaml": feedstock})
    )


def _section(
    write_tree: WriteTree,
    feedstock: str = "feedstock: demo\n",
    recipe_text: str = RECIPE,
    upstream_text: str = UPSTREAM,
) -> PlannedSection:
    recipe = read_recipe(recipe_text)
    config = _config(write_tree, feedstock).for_feedstock("demo")
    return plan_section(
        recipe.blocks["/requirements/run"],
        parse_pyproject(upstream_text),
        config,
        NameResolver(config.name_map, INDEX),
        PYTHON_MIN,
    )


# --- what counts as tighter -----------------------------------------------


@pytest.mark.parametrize(
    ("recipe", "planned"),
    [
        # A ceiling upstream does not ask for -- the fleet's case.
        (">=2.11.0,<3.1.3", ">=2.11.0"),
        # A floor above upstream's.
        (">=3.0", ">=2.11.0"),
        # An exclusion, on an otherwise identical range.
        (">=2.11.0,!=2.12.0", ">=2.11.0"),
        # Any bound at all, where upstream states none.
        ("<3", ""),
    ],
)
def test_a_bound_that_refuses_a_version_the_plan_allows_is_reported(
    recipe: str, planned: str
) -> None:
    assert tightening("demo", recipe, planned) is not None


@pytest.mark.parametrize(
    ("recipe", "planned"),
    [
        # Identical.
        (">=2.11.0", ">=2.11.0"),
        # Stale in the harmless direction: swage tightens this as a matter of
        # course and nothing is lost by it.
        (">=2.0", ">=2.11.0"),
        # Unconstrained in the recipe, constrained by upstream.
        ("", ">=2.11.0"),
        # Written differently, admitting the same versions.
        ("<3.1.3,>=2.11.0", ">=2.11.0,<3.1.3"),
    ],
)
def test_a_bound_that_refuses_nothing_extra_is_not(recipe: str, planned: str) -> None:
    assert tightening("demo", recipe, planned) is None


def test_a_templated_bound_is_no_answer_rather_than_a_wrong_one() -> None:
    """`pandas >=${{ pandas_min }}` is a real shape and is not comparable."""
    assert tightening("demo", ">=${{ pandas_min }}", ">=2.11.0") is None


# --- and what the planner does with it -------------------------------------


def test_the_planner_reports_it_and_still_renders_upstreams_constraint(
    write_tree: WriteTree,
) -> None:
    """swage does not quietly keep it either -- the report is the point."""
    section = _section(write_tree)

    assert [item.text for item in section.requirements] == [
        "python >=${{ python_min }}",
        "apache-airflow >=2.11.0",
    ]
    assert [item.name for item in section.tightened] == ["apache-airflow"]
    assert "<3.1.3" in section.tightened[0].recipe


def test_the_report_offers_leaving_a_temporary_bound_alone(
    write_tree: WriteTree,
) -> None:
    """DESIGN.md 3.3.7's third answer, one level down.

    A ceiling added to work around a solver problem elsewhere must not go into
    `constraints:` -- the entry says the bound holds for good, so it would
    outlive the problem it exists for. `apache-airflow-providers-google` is the
    case, and offering only "record it or remove it" would send a maintainer to
    bless one.
    """
    section = _section(write_tree)

    reason = section.tightened[0].reason
    assert "constraints:" in reason
    assert "leave it" in reason
    assert "next version bump" in reason


def test_a_constraints_entry_renders_the_bound_back_and_settles_it(
    write_tree: WriteTree,
) -> None:
    """The decision moves into config, where a rerun cannot lose it."""
    feedstock = 'feedstock: demo\nconstraints:\n  apache-airflow: "<3.1.3"\n'
    section = _section(write_tree, feedstock)

    assert [item.text for item in section.requirements] == [
        "python >=${{ python_min }}",
        "apache-airflow >=2.11.0,<3.1.3",
    ]
    assert section.tightened == ()


def test_a_recipe_going_further_than_config_is_still_reported(
    write_tree: WriteTree,
) -> None:
    """An entry accounts for the bound it states, not for any bound at all."""
    feedstock = 'feedstock: demo\nconstraints:\n  apache-airflow: "<3.2"\n'
    section = _section(write_tree, feedstock)

    assert [item.name for item in section.tightened] == ["apache-airflow"]


def test_a_constraint_no_upstream_version_can_meet_stops_the_feedstock(
    write_tree: WriteTree,
) -> None:
    """Reported apart from an upstream contradiction: the fix is another file."""
    feedstock = 'feedstock: demo\nconstraints:\n  apache-airflow: "<2.0"\n'
    with pytest.raises(PlanError) as caught:
        _section(write_tree, feedstock)

    message = str(caught.value)
    assert "'apache-airflow'" in message
    assert "constraints:" in message


def test_an_unreadable_constraint_is_refused_at_load(write_tree: WriteTree) -> None:
    """A bound swage cannot parse can never be rendered or compared."""
    feedstock = 'feedstock: demo\nconstraints:\n  apache-airflow: "nonsense"\n'
    with pytest.raises(ConfigError, match="not a version constraint"):
        _config(write_tree, feedstock)
