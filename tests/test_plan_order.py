"""Ordering tests (DESIGN.md 6, 11).

The rule that rules out the obvious implementation is that upstream-derived
requirements keep *upstream's* order rather than being sorted. Alphabetizing
them would make every swage diff against upstream unreadable.
"""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from swage.plan import PlannedRequirement, Provenance, order_requirements
from swage.plan.attribute import KEPT_UNEXPLAINED

UPSTREAM_ORDER = {"apache-airflow": 0, "requests": 1, "databricks": 2, "pandas": 3}


def _upstream(text: str) -> PlannedRequirement:
    return PlannedRequirement(text, Provenance("upstream-core", "upstream"))


def _extra(text: str, extra: str) -> PlannedRequirement:
    return PlannedRequirement(text, Provenance("upstream-extra", f"extra:{extra}"))


def _kept(text: str) -> PlannedRequirement:
    return PlannedRequirement(text, Provenance("recipe-kept", "recipe_owned"))


def _unexplained(text: str) -> PlannedRequirement:
    """A line swage kept without being able to explain it (DESIGN.md 3.3.6)."""
    return PlannedRequirement(text, Provenance("recipe-kept", KEPT_UNEXPLAINED))


def _added(text: str) -> PlannedRequirement:
    return PlannedRequirement(text, Provenance("config-add", "config/f.yaml"))


def _texts(*entries: PlannedRequirement) -> list[str]:
    return _ordered(entries, UPSTREAM_ORDER)


def _ordered(
    entries: tuple[PlannedRequirement, ...], order: Mapping[str, int]
) -> list[str]:
    """The lines in the order swage writes them.

    Every entry here is a plain line; ordering a dependency stated per python
    range is `test_plan_split.py`'s subject.
    """
    return [
        entry.text
        for entry in order_requirements(entries, order)
        if isinstance(entry, PlannedRequirement)
    ]


def test_upstream_requirements_keep_upstream_order() -> None:
    """Not alphabetical -- that is what keeps diffs against upstream legible."""
    assert _texts(
        _upstream("pandas >=2.3.3"),
        _upstream("apache-airflow >=2.11.0"),
        _upstream("requests >=2.27"),
    ) == ["apache-airflow >=2.11.0", "requests >=2.27", "pandas >=2.3.3"]


def test_python_then_pip_come_first() -> None:
    assert _texts(
        _upstream("requests >=2.27"),
        _kept("pip"),
        _kept("python >=${{ python_min }}"),
    ) == ["python >=${{ python_min }}", "pip", "requests >=2.27"]


def test_python_precedes_a_pin_subpackage_line() -> None:
    """159 run sections in the fleet do this; 2 do the reverse."""
    assert _texts(
        _kept("${{ pin_subpackage(name, exact=True) }}"),
        _kept("python >=${{ python_min }}"),
    ) == ["python >=${{ python_min }}", "${{ pin_subpackage(name, exact=True) }}"]


def test_structural_lines_come_before_upstream_ones() -> None:
    assert _texts(
        _upstream("requests >=2.27"),
        _kept("${{ pin_subpackage(name, exact=True) }}"),
        _kept("python >=${{ python_min }}"),
    ) == [
        "python >=${{ python_min }}",
        "${{ pin_subpackage(name, exact=True) }}",
        "requests >=2.27",
    ]


def test_conda_forge_only_additions_trail_and_are_alphabetized() -> None:
    """They have no upstream order to inherit, so alphabetical is the answer."""
    assert _texts(
        _added("zstandard >=0.20"),
        _upstream("requests >=2.27"),
        _added("grpcio-gcp >=0.2.2"),
    ) == ["requests >=2.27", "grpcio-gcp >=0.2.2", "zstandard >=0.20"]


def test_a_kept_line_swage_cannot_explain_trails_rather_than_leading() -> None:
    """`recipe-kept` is an allowlist, and this line is not on it.

    It carries that origin as a placeholder, so ordering on the origin alone
    put a dependency swage could not account for above every upstream line in
    the section. It belongs where it will sit once somebody writes it into
    `add_requirements`, so that documenting a line does not also move it.
    """
    assert _texts(
        _unexplained("psycopg2-binary >=2.9.10"),
        _upstream("requests >=2.27"),
        _kept("python >=${{ python_min }}"),
        _added("grpcio-gcp >=0.2.2"),
    ) == [
        "python >=${{ python_min }}",
        "requests >=2.27",
        "grpcio-gcp >=0.2.2",
        "psycopg2-binary >=2.9.10",
    ]


def test_an_extra_is_ordered_with_the_rest_of_upstream() -> None:
    assert _texts(
        _extra("pandas >=1.3.0", "pandas"),
        _upstream("apache-airflow >=2.11.0"),
    ) == ["apache-airflow >=2.11.0", "pandas >=1.3.0"]


def test_the_whole_ordering_in_one_case() -> None:
    assert _texts(
        _added("grpcio-gcp >=0.2.2"),
        _extra("pandas >=1.3.0", "pandas"),
        _kept("${{ pin_subpackage(name, exact=True) }}"),
        _upstream("requests >=2.27"),
        _kept("python >=${{ python_min }}"),
        _upstream("apache-airflow >=2.11.0"),
    ) == [
        "python >=${{ python_min }}",
        "${{ pin_subpackage(name, exact=True) }}",
        "apache-airflow >=2.11.0",
        "requests >=2.27",
        "pandas >=1.3.0",
        "grpcio-gcp >=0.2.2",
    ]


def test_structural_lines_keep_the_order_they_arrived_in() -> None:
    """swage has no basis for choosing between a compiler and a stdlib line."""
    assert _texts(
        _kept("${{ compiler('c') }}"),
        _kept("${{ stdlib('c') }}"),
    ) == ["${{ compiler('c') }}", "${{ stdlib('c') }}"]


def test_an_upstream_line_missing_from_the_index_sorts_last_not_first() -> None:
    """Position 0 would be the default for a missing key and is badly wrong."""
    assert _texts(
        _upstream("mystery >=1.0"),
        _upstream("apache-airflow >=2.11.0"),
    ) == ["apache-airflow >=2.11.0", "mystery >=1.0"]


def test_ordering_is_idempotent() -> None:
    """`format(format(x)) == format(x)` (DESIGN.md 6)."""
    entries = (
        _added("grpcio-gcp >=0.2.2"),
        _upstream("requests >=2.27"),
        _kept("python >=${{ python_min }}"),
        _upstream("apache-airflow >=2.11.0"),
    )
    once = order_requirements(entries, UPSTREAM_ORDER)
    assert order_requirements(once, UPSTREAM_ORDER) == once


@pytest.mark.parametrize("section", ["host", "run"])
def test_an_empty_section_orders_to_nothing(section: str) -> None:
    assert order_requirements((), UPSTREAM_ORDER) == ()


def test_an_embedded_expansion_stays_beside_the_line_it_explains() -> None:
    """A `# start`/`# end` block is an island, not a trailing addition.

    `pure-sasl`, `thrift` and `thrift_sasl` stand in for `pyhive[hive-pure-sasl]`
    and belong under the `pyhive` line. Treating them as ordinary
    `add_requirements` scatters them into the alphabetized trailing block,
    away from the only line that explains them (DESIGN.md 6).
    """
    order = {"pyhive": 0, "pure-sasl": 0, "thrift": 0, "thrift_sasl": 0, "jmespath": 1}
    entries = (
        _upstream("pyhive >=0.7.0"),
        _added("pure-sasl >=0.6.2"),
        _added("thrift >=0.10.0"),
        _added("thrift_sasl >=0.1.0"),
        _upstream("jmespath >=0.7.0"),
    )
    assert _ordered(entries, order) == [
        "pyhive >=0.7.0",
        "pure-sasl >=0.6.2",
        "thrift >=0.10.0",
        "thrift_sasl >=0.1.0",
        "jmespath >=0.7.0",
    ]


def test_an_expansion_keeps_the_order_config_wrote_it_in() -> None:
    """Sharing the parent's position means a stable sort leaves them alone."""
    order = {"pandas": 0, "sqlalchemy": 0, "adbc-driver-sqlite": 0}
    entries = (
        _upstream("pandas >=2.3.3"),
        _added("sqlalchemy >=2.0.36"),
        _added("adbc-driver-sqlite >=1.2.0"),
    )
    assert _ordered(entries, order) == [
        "pandas >=2.3.3",
        "sqlalchemy >=2.0.36",
        "adbc-driver-sqlite >=1.2.0",
    ]


def test_a_positionless_addition_still_trails() -> None:
    """Only an expansion inherits a position; add_requirements does not."""
    entries = (_added("grpcio-gcp >=0.2.2"), _upstream("requests >=2.27"))
    assert _ordered(entries, UPSTREAM_ORDER) == [
        "requests >=2.27",
        "grpcio-gcp >=0.2.2",
    ]
