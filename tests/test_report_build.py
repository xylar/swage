"""Tests for turning a plan into a record (DESIGN.md 9).

Three of these exist because running the layer over the fleet found what the
tests written beside it did not: a summary line that ran to forty wrapped
lines, URLs broken in half by the wrapper, and `recipe-kept` printed for a line
that is emphatically not recipe-owned.
"""

from __future__ import annotations

import pytest

from swage.config import ConfigTree, load_config
from swage.mapping import NameResolver, StaticPackageIndex
from swage.plan import PythonMin, evaluate_gates, plan_recipe
from swage.recipe import read_recipe
from swage.report import build_record, render_summary
from swage.upstream import parse_pyproject

from .conftest import WriteTree

PYTHON_MIN = PythonMin("3.10", ".ci_support/linux_64_.yaml")

RECIPE = """\
requirements:
  host:
    - python ${{ python_min }}.*
    - pip
  run:
    - python >=${{ python_min }}
    - requests >=2.0
    - leftover >=1.0
"""

UPSTREAM = parse_pyproject(
    '[project]\nname = "demo"\nversion = "2.0"\n'
    'dependencies = ["requests >=2.31"]\n'
    '[project.optional-dependencies]\nasync = ["greenlet >=3"]\n'
    '[build-system]\nrequires = ["setuptools"]\n'
)


def _tree(write_tree: WriteTree) -> ConfigTree:
    return load_config(
        write_tree(
            {"defaults.yaml": "trust: manual\nrecipe_owned:\n  names: [python, pip]\n"}
        )
    )


def _record(write_tree: WriteTree, outcome: str = "needs-review"):  # type: ignore[no-untyped-def]
    tree = _tree(write_tree)
    config = tree.for_feedstock("demo")
    recipe = read_recipe(RECIPE)
    resolver = NameResolver(
        config.name_map,
        StaticPackageIndex(frozenset({"requests", "leftover", "setuptools"})),
    )
    plan = plan_recipe(recipe, UPSTREAM, config, resolver, PYTHON_MIN)
    verdict = evaluate_gates(plan, config, UPSTREAM)
    return build_record(
        "demo",
        outcome,  # type: ignore[arg-type]
        plan=plan,
        verdict=verdict,
        recipe=recipe,
        upstream=UPSTREAM,
    )


def _lines(record) -> dict[str, tuple[str, str, str]]:  # type: ignore[no-untyped-def]
    return {
        line.text.split()[0]: (line.action, line.origin, line.source)
        for section in record.sections
        for line in section.lines
    }


def test_a_line_upstream_still_declares_is_a_bump_showing_both_values(
    write_tree: WriteTree,
) -> None:
    """A bump nobody can see the old value of is a bump nobody can check."""
    lines = _lines(_record(write_tree))
    action, origin, _ = lines["requests"]
    assert action == "bump"
    assert "requests >=2.0 -> >=2.31" in {
        line.text for section in _record(write_tree).sections for line in section.lines
    }
    assert origin == "upstream-core"


def test_a_line_the_recipe_already_has_unchanged_is_a_keep(
    write_tree: WriteTree,
) -> None:
    assert _lines(_record(write_tree))["pip"][0] == "keep"


def test_an_unattributable_line_is_not_reported_as_recipe_kept(
    write_tree: WriteTree,
) -> None:
    """`recipe-kept` is an allowlist, never a fallback (DESIGN.md 3.3.6).

    The planner carries it as a placeholder on a line it kept but could not
    explain. Printing it would be false in the one place it matters most:
    someone running `explain` to find out why G1 failed.
    """
    action, origin, source = _lines(_record(write_tree))["leftover"]
    assert action == "keep"
    assert origin == "unexplained"
    assert source == "in no upstream version"


def test_a_recipe_owned_line_still_says_recipe_kept(write_tree: WriteTree) -> None:
    """The allowlist half of the same rule has to keep working."""
    assert _lines(_record(write_tree))["python"][1] == "recipe-kept"


def test_a_gate_failing_on_many_lines_gets_one_summary_line(
    write_tree: WriteTree,
) -> None:
    """A real feedstock fails G1 with 2,800 characters of reasons.

    Printed whole into the summary it wraps to forty lines and buries every
    other feedstock in the run -- the opposite of what grouping by outcome is
    for (DESIGN.md 9).
    """
    record = _record(write_tree)
    assert record.detail.startswith("G1: ")
    assert len(record.detail) <= 120
    assert record.detail.count("\n") == 0


@pytest.mark.parametrize(
    "detail",
    [
        "https://github.com/OpenLineage/OpenLineage/archive/refs/tags/1.40.1.tar.gz",
        "apache-airflow-providers-microsoft-azure is in the recipe",
    ],
)
def test_a_url_or_a_package_name_is_never_broken_in_half(detail: str) -> None:
    """A URL split across two lines is a URL nobody can copy."""
    from swage.report import FeedstockRecord, RunRecord

    run = RunRecord(
        feedstocks=(FeedstockRecord(feedstock="demo", outcome="failed", detail=detail),)
    )
    rendered = render_summary(run, width=60, color=False)
    token = detail.split()[0]
    assert token in rendered


def test_a_stopped_feedstock_summarizes_on_its_first_line() -> None:
    record = build_record(
        "markupsafe",
        "failed",
        stopped="unsupported build-variant switch: use_noarch\n  and more detail",
    )
    assert record.detail == "unsupported build-variant switch: use_noarch"
    assert record.sections == ()
