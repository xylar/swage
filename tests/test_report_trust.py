"""Tests for `swage trust` (DESIGN.md 8.4).

The report makes a claim about a feedstock's history, and a promotion is taken
on the strength of it, so what is tested here is mostly refusal: a reading that
is really the same reading again, a feedstock that qualified once, an outcome
that looks like evidence and is not.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from swage.config import ConfigTree, load_config
from swage.report import (
    FeedstockRecord,
    RunRecord,
    all_runs,
    earned,
    fleet_states,
    render_trust,
    write_recipes,
    write_run,
)

from .conftest import WriteTree

NOARCH = "package:\n  name: demo\nbuild:\n  noarch: python\n"
COMPILED = "package:\n  name: demo\nbuild:\n  number: 0\n"

DEFAULTS = "trust: propose\nrecipe_owned:\n  names: [python, pip]\n"


def record(
    feedstock: str,
    outcome: str = "proposed",
    recipe_text: str = NOARCH,
    recipe: str = "v1, 1 output, 2 requirements blocks",
) -> FeedstockRecord:
    return FeedstockRecord(
        feedstock=feedstock,
        outcome=outcome,
        recipe=recipe,
        current_recipe=recipe_text,
        rendered_recipe=recipe_text,
    )


def audit(
    root: Path,
    when: datetime,
    *records: FeedstockRecord,
    command: str = "swage audit --all",
) -> Path:
    """One recorded run, written the way a real audit writes it."""
    directory = root / "runs" / when.strftime("%Y-%m-%dT%H-%M-%S")
    run = RunRecord(
        command=command,
        started=when.isoformat(timespec="seconds"),
        feedstocks=tuple(records),
    )
    write_run(run, directory)
    write_recipes(run, directory)
    return directory


@pytest.fixture
def cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    return tmp_path / "cache" / "swage"


def tree_at(write_tree: WriteTree, *feedstocks: str) -> ConfigTree:
    files = {"defaults.yaml": DEFAULTS}
    for entry in feedstocks:
        name, _, rung = entry.partition(":")
        files[f"feedstocks/{name}.yaml"] = f"feedstock: {name}\ntrust: {rung}\n"
    return load_config(write_tree(files))


def at(hours: int) -> datetime:
    return datetime(2026, 8, 1, tzinfo=UTC) + timedelta(hours=hours)


def test_replaying_a_reading_is_not_a_second_reading(cache: Path) -> None:
    """`audit --all --cached` re-renders bytes somebody already read.

    Which is the whole reason the evidence is counted in readings: a day of
    developing swage leaves a dozen audits of one fleet, and counting those as
    a dozen would inflate the case for a promotion by the number of times a
    sweep was re-run.
    """
    audit(cache, at(0), record("demo"))
    audit(cache, at(1), record("demo"), command="swage audit --all --cached")
    audit(cache, at(2), record("demo"), command="swage audit --all --cached")

    states, _ = fleet_states(all_runs(), readings=5)
    assert len(states) == 1
    assert len(states[0].audits) == 3


def test_a_fleet_that_moved_is_a_new_reading(cache: Path) -> None:
    audit(cache, at(0), record("demo", recipe_text=NOARCH))
    audit(cache, at(1), record("demo", recipe_text=NOARCH + "# moved\n"))

    states, _ = fleet_states(all_runs(), readings=5)
    assert len(states) == 2
    assert states[0].first < states[1].first


def test_only_fleet_audits_count(cache: Path) -> None:
    """A run over one feedstock says nothing about the ones it never read."""
    audit(cache, at(0), record("demo"), command="swage audit --feedstock demo")
    states, _ = fleet_states(all_runs(), readings=5)
    assert states == ()


def test_a_feedstock_every_reading_agrees_about_is_earned(
    cache: Path, write_tree: WriteTree
) -> None:
    audit(cache, at(0), record("demo"), record("other"))
    audit(
        cache,
        at(1),
        record("demo", recipe_text=NOARCH + "# moved\n"),
        record("other"),
    )

    states, _ = fleet_states(all_runs(), readings=5)
    found = earned(states, tree_at(write_tree))
    assert [item.feedstock for item in found] == ["demo", "other"]


def test_one_disagreeing_reading_is_enough_to_wait(
    cache: Path, write_tree: WriteTree
) -> None:
    """The claim is that nothing else has been outstanding, so one is enough."""
    audit(cache, at(0), record("demo", outcome="needs-review"))
    audit(cache, at(1), record("demo", recipe_text=NOARCH + "# moved\n"))

    states, _ = fleet_states(all_runs(), readings=5)
    assert earned(states, tree_at(write_tree)) == ()


def test_a_feedstock_missing_from_a_reading_waits(
    cache: Path, write_tree: WriteTree
) -> None:
    """Not being read is not the same as having been read and found sound."""
    audit(cache, at(0), record("other"))
    audit(cache, at(1), record("demo"), record("other"))

    states, _ = fleet_states(all_runs(), readings=5)
    assert [item.feedstock for item in earned(states, tree_at(write_tree))] == ["other"]


def test_nothing_to_propose_is_the_stronger_evidence(
    cache: Path, write_tree: WriteTree
) -> None:
    """`unchanged` says the recipe already reads as swage would write it.

    Which is the same claim as `proposed` with the diff removed -- the audit
    reached that outcome only once nothing but approval was outstanding
    (DESIGN.md 8.2).
    """
    audit(cache, at(0), record("demo", outcome="unchanged"))
    states, _ = fleet_states(all_runs(), readings=5)
    assert [item.feedstock for item in earned(states, tree_at(write_tree))] == ["demo"]


def test_a_feedstock_with_no_repository_is_not_evidence(
    cache: Path, write_tree: WriteTree
) -> None:
    """`unchanged` is also what an org team with no repository comes back as.

    Nothing was read, so there is nothing to have found sound, and the record
    says so by naming no recipe.
    """
    audit(
        cache,
        at(0),
        FeedstockRecord(
            feedstock="demo",
            outcome="unchanged",
            detail="no feedstock repository",
        ),
    )
    states, _ = fleet_states(all_runs(), readings=5)
    assert earned(states, tree_at(write_tree)) == ()


@pytest.mark.parametrize("rung", ["auto", "never"])
def test_a_feedstock_already_decided_is_not_listed(
    cache: Path, write_tree: WriteTree, rung: str
) -> None:
    """`auto` has nothing to earn, and `never` is a decision rather than a gap."""
    audit(cache, at(0), record("demo"))
    states, _ = fleet_states(all_runs(), readings=5)
    assert earned(states, tree_at(write_tree, f"demo:{rung}")) == ()


def test_the_group_is_what_one_argument_could_cover(
    cache: Path, write_tree: WriteTree
) -> None:
    """A batch is promoted on one reason, so the listing groups by shape."""
    audit(
        cache,
        at(0),
        record("plain"),
        record("built", recipe_text=COMPILED),
        record("split", recipe="v1, 2 outputs, 4 requirements blocks"),
    )
    states, _ = fleet_states(all_runs(), readings=5)
    found = {item.feedstock: item.group for item in earned(states, tree_at(write_tree))}
    assert found["plain"] == "one noarch: python output, no extras published"
    assert found["built"] == "compiled"
    assert found["split"] == "several outputs"


def test_a_family_is_the_group_where_there_is_one(
    cache: Path, write_tree: WriteTree
) -> None:
    """Its members are already asserted to behave alike, which is the argument."""
    root = write_tree(
        {
            "defaults.yaml": DEFAULTS,
            "families/demo.yaml": 'family: demo\nmatch:\n  feedstock: "demo-*"\n',
        }
    )
    audit(cache, at(0), record("demo-widget"))
    states, _ = fleet_states(all_runs(), readings=5)
    assert [item.group for item in earned(states, load_config(root))] == ["demo"]


def test_the_report_names_the_readings_it_rests_on(
    cache: Path, write_tree: WriteTree
) -> None:
    """The evidence is the point, so it is above the listing rather than implied."""
    audit(cache, at(0), record("demo"))
    audit(cache, at(30), record("demo", recipe_text=NOARCH + "# moved\n"))

    states, _ = fleet_states(all_runs(), readings=5)
    text = render_trust(states, earned(states, tree_at(write_tree)))
    assert "2 readings of the fleet over 30 hours" in text
    assert "2026-08-01 00:00" in text
    assert "EARNED A RUNG (1)" in text
    assert "in all 2" in text
    assert "config/trust.yaml" in text


def test_a_report_with_nothing_to_say_says_so(
    cache: Path, write_tree: WriteTree
) -> None:
    """An empty listing under a heading reads as a report that failed to render."""
    audit(cache, at(0), record("demo", outcome="needs-review"))
    states, _ = fleet_states(all_runs(), readings=5)
    text = render_trust(states, earned(states, tree_at(write_tree)))
    assert "NOTHING HAS EARNED A MOVE" in text
    assert "EARNED A RUNG" not in text
