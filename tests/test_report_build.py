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
from swage.plan import (
    GateResult,
    PythonMin,
    RecipePlan,
    Verdict,
    evaluate_gates,
    plan_recipe,
)
from swage.recipe import read_recipe
from swage.report import build_record, render_summary
from swage.upstream import parse_pyproject

from .conftest import WriteTree

PYTHON_MIN = PythonMin("3.10", ".ci_support/linux_64_.yaml")

RECIPE = """\
build:
  noarch: python
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
            {"defaults.yaml": "trust: never\nrecipe_owned:\n  names: [python, pip]\n"}
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


def test_a_line_under_upstreams_own_name_is_not_called_never_upstream(
    write_tree: WriteTree,
) -> None:
    """Upstream declares it; conda-forge publishes what it means as something else.

    This column holds one short phrase, so a fall-through would print a false
    statement about the line rather than a vaguer one (DESIGN.md 3.2.2).
    """
    root = write_tree(
        {
            "defaults.yaml": "trust: never\nrecipe_owned:\n  names: [python, pip]\n",
            "name-map.yaml": "psycopg2-binary: psycopg2\n",
        }
    )
    config = load_config(root).for_feedstock("demo")
    recipe = read_recipe(
        "build:\n  noarch: python\n"
        "requirements:\n  run:\n"
        "    - python >=${{ python_min }}\n"
        "    - psycopg2-binary >=2.9\n"
    )
    upstream = parse_pyproject(
        '[project]\nname = "demo"\nversion = "2.0"\n'
        'dependencies = ["psycopg2-binary >=2.9"]\n'
    )
    plan = plan_recipe(
        recipe,
        upstream,
        config,
        NameResolver(
            config.name_map, StaticPackageIndex.of("psycopg2", "psycopg2-binary")
        ),
        PYTHON_MIN,
    )
    record = build_record(
        "demo",
        "needs-review",
        plan=plan,
        verdict=evaluate_gates(plan, config, upstream),
        recipe=recipe,
        upstream=upstream,
    )

    assert _lines(record)["psycopg2-binary"][2] == "renamed on conda-forge"


def test_a_gate_failing_on_many_lines_gets_one_summary_line(
    write_tree: WriteTree,
) -> None:
    """A real feedstock fails G1 with 2,800 characters of reasons.

    Printed whole into the summary it wraps to forty lines and buries every
    other feedstock in the run -- the opposite of what grouping by outcome is
    for (DESIGN.md 9).
    """
    record = _record(write_tree)
    # The identifier is not in the line: `G1: ...` reads as though the
    # interesting half were the `G1`, and means nothing without the design.
    assert not record.detail.startswith("G1")
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
        stopped="unsupported conditional noarch in /build/noarch\n  and more detail",
    )
    assert record.detail == "unsupported conditional noarch in /build/noarch"
    assert record.sections == ()


def test_an_unaccounted_extra_becomes_a_note_not_a_detail() -> None:
    """DESIGN.md 4: reported and not gated, so it must not read as a verdict."""
    record = build_record(
        "demo",
        "merge-ready",
        plan=RecipePlan(unaccounted_extras=("tracing",)),
        upstream=parse_pyproject('[project]\nname = "demo"\nversion = "2.19.0"\n'),
    )
    assert record.detail == ""
    assert record.notes == (
        "upstream 2.19.0 declares extra 'tracing', which no output draws on",
    )


def test_a_plan_with_everything_accounted_for_carries_no_notes() -> None:
    record = build_record("demo", "merge-ready", plan=RecipePlan())
    assert record.notes == ()


BUILD_PINNED_RECIPE = """\
requirements:
  host:
    - python ${{ python_min }}.*
    - pip
    - hdf5
    - hdf5 * nompi_*
  run:
    - python >=${{ python_min }}
    - requests >=2.0
"""


def test_a_plain_line_is_not_reported_as_a_bump_of_the_build_pinned_one(
    write_tree: WriteTree,
) -> None:
    """Both are kept, and a report saying otherwise describes an edit nobody made.

    `esmf` states `hdf5` and `hdf5 * ${{ mpi_prefix }}_*` in one section on
    purpose. Filed under the package name alone, the section had one "before"
    for the two of them -- the last one read -- so the plain line came out as
    `hdf5 * nompi_* -> hdf5`.
    """
    tree = _tree(write_tree)
    config = tree.for_feedstock("demo")
    recipe = read_recipe(BUILD_PINNED_RECIPE)
    resolver = NameResolver(
        config.name_map,
        StaticPackageIndex(frozenset({"requests", "hdf5", "setuptools"})),
    )
    plan = plan_recipe(recipe, UPSTREAM, config, resolver, PYTHON_MIN)
    record = build_record(
        "demo",
        "needs-review",
        plan=plan,
        verdict=evaluate_gates(plan, config, UPSTREAM),
        recipe=recipe,
        upstream=UPSTREAM,
    )

    host = next(s for s in record.sections if s.section == "host")
    assert [(line.action, line.text) for line in host.lines if "hdf5" in line.text] == [
        ("keep", "hdf5"),
        ("keep", "hdf5 * nompi_*"),
    ]


def _held(*gates: tuple[str, str]) -> Verdict:
    return Verdict(
        gates=tuple(
            GateResult(name=name, passed=False, detail=detail) for name, detail in gates
        )
    )


LADDER = ("G6", "not approved for automatic merging (trust: propose)")


def test_a_feedstock_that_would_be_pushed_says_how_much_would_change() -> None:
    """Every feedstock in the bucket is there for the same reason.

    That reason is the bucket's own heading, so repeating it per feedstock
    printed "not approved for automatic merging (trust: propose)" down thirty
    consecutive lines. What differs between them is the size of the change,
    which is also what says which one to open first.
    """
    record = build_record(
        "demo",
        "proposed",
        verdict=_held(LADDER),
        current_recipe="a\nb\nc\n",
        rendered_recipe="a\nx\ny\nc\n",
    )
    assert record.detail == "+2 -1 in the recipe"


def test_a_held_feedstock_is_named_for_what_holds_it_not_the_trust_ladder() -> None:
    """The checks run in order and the ladder sits in the middle of them.

    `google-cloud-redis` is held because swage would drop a requirement it
    cannot account for, and every command reported "not approved for automatic
    merging (trust: propose)" beside it -- in a bucket whose heading says a
    decision is needed, naming the one failure that is not that decision.
    """
    record = build_record(
        "demo",
        "needs-review",
        verdict=_held(LADDER, ("G8", "would remove `google-api-core`")),
    )
    assert record.detail == "would remove `google-api-core`"


def test_the_rung_is_the_line_where_it_is_the_whole_story() -> None:
    """`trust: never` fails nothing else, and explains a run that wrote nothing."""
    never = "swage writes nothing to this feedstock (trust: never)"
    record = build_record(
        "demo",
        "needs-review",
        verdict=_held(("G6", never)),
        current_recipe="a\n",
        rendered_recipe="b\n",
    )
    assert record.detail == never
