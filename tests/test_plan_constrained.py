"""`run_constraints` tests (DESIGN.md 3.3.9, 11).

All three of the rules are refusals: swage never adds an entry even where an
upstream extra would obviously suggest one, never removes one, and blocks
automerge at G9 while any entry is unassociated. The first deserves the hardest
guard, because "upstream declares an extra, so emit a constraint" is exactly
the plausible-looking behaviour the rule exists to prevent.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from swage.config import Feedstock, RunConstraint
from swage.plan import check_run_constraints
from swage.plan.constrained import UnassociatedConstraint


def test_an_unassociated_entry_is_reported() -> None:
    found = check_run_constraints(("protobuf >=4.0",), {})
    assert [f.name for f in found] == ["protobuf"]


def test_an_associated_entry_passes() -> None:
    found = check_run_constraints(
        ("pandas >=1.3",), {"pandas": RunConstraint(extra="pandas")}
    )
    assert found == ()


def test_a_deliberate_null_association_passes() -> None:
    """`extra: null` is an answer, not a missing one (DESIGN.md 3.3.9)."""
    found = check_run_constraints(("jinja2 >=3",), {"jinja2": RunConstraint()})
    assert found == ()


def test_an_empty_section_passes() -> None:
    assert check_run_constraints((), {}) == ()


def test_every_unassociated_entry_is_named_in_order() -> None:
    """The report names them, so a maintainer fixes them in one pass."""
    found = check_run_constraints(
        ("protobuf >=4.0", "pandas >=1.3", "grpcio >=1.5"),
        {"pandas": RunConstraint(extra="pandas")},
    )
    assert [f.name for f in found] == ["protobuf", "grpcio"]


def test_the_message_gives_both_ways_to_resolve_it() -> None:
    """Either the entry tracks an extra or it deliberately tracks nothing.

    Offering only the first would push a maintainer into inventing an
    association for a bound that never had one.
    """
    reason = UnassociatedConstraint("protobuf >=4.0", "protobuf").reason
    assert "extra: <name>" in reason
    assert "extra: null" in reason


def test_an_association_matches_either_spelling_of_a_conda_name() -> None:
    """conda names are not PEP 503-normalized; config should still explain them."""
    found = check_run_constraints(
        ("msal_extensions >=1.3",), {"msal-extensions": RunConstraint()}
    )
    assert found == ()


def test_a_run_constraints_association_is_schema_validated() -> None:
    entry = Feedstock.model_validate(
        {
            "feedstock": "demo",
            "run_constraints": {"pandas": {"extra": "pandas"}, "jinja2": {}},
        }
    )
    assert entry.run_constraints["pandas"].extra == "pandas"
    assert entry.run_constraints["jinja2"].extra is None


def test_an_association_naming_a_non_normalized_extra_is_refused() -> None:
    """Same rule as everywhere else: the extra must be spelled as swage reads it."""
    with pytest.raises(ValidationError, match="write 'bigquery-v2'"):
        Feedstock.model_validate(
            {"feedstock": "demo", "run_constraints": {"x": {"extra": "bigquery_v2"}}}
        )
