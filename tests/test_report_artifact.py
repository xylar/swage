"""Tests for the run record and its artifact (DESIGN.md 9, 9.1).

`run.json` is a contract rather than a debug dump: `swage explain --from-run`
reads one back, possibly one written by a different version of swage. So the
tests that matter are about what happens at the read -- a record that has
drifted must fail loudly, naming what it saw, rather than half-rendering.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import get_args

import pytest

from swage.cache import cache_root
from swage.report import (
    DECLARATIONS_DIR,
    OUTCOMES,
    RECIPES_DIR,
    SCHEMA_VERSION,
    FeedstockRecord,
    GateRecord,
    Outcome,
    PlannedLine,
    ReportError,
    RunRecord,
    SectionRecord,
    UpstreamRecord,
    read_run,
    run_directory,
    write_declarations,
    write_recipes,
    write_run,
)

RECORD = RunRecord(
    command="swage scan --family google-cloud",
    started="2026-08-12T14:02:00Z",
    feedstocks=(
        FeedstockRecord(
            feedstock="google-cloud-bigquery",
            outcome="needs-review",
            detail="G9: run_constrained 'protobuf' not associated",
            recipe="v1, 2 outputs, 4 requirements blocks",
            pull_request=187,
            head="4a2f1c8",
            upstream=UpstreamRecord(
                name="google-cloud-bigquery",
                version="3.44.0",
                source="sdist PKG-INFO",
                previous="3.43.0",
            ),
            python_min="3.10",
            python_min_source=".ci_support/linux_64_.yaml",
            config_layers=("config/feedstocks/google-cloud-bigquery.yaml",),
            sections=(
                SectionRecord(
                    path="/outputs/1/requirements/run",
                    section="run",
                    lines=(
                        PlannedLine(
                            action="add",
                            text="proto-plus >=1.26.1",
                            origin="upstream-extra",
                            source="extra:bigquery-v2",
                            exact=True,
                        ),
                    ),
                ),
            ),
            gates=(GateRecord(name="G9", passed=False, detail="protobuf"),),
            decision="needs-review",
        ),
        FeedstockRecord(feedstock="google-cloud-storage", outcome="unchanged"),
    ),
)


#: The opening of a `run.json` at the schema this swage reads. Spelled out of
#: `SCHEMA_VERSION` rather than pinned, so that bumping the schema does not
#: turn every "refused for some other reason" test below into a test that the
#: version was wrong.
_HEADER = f'{{"schema": {SCHEMA_VERSION}, '


def test_a_record_round_trips_through_the_artifact(tmp_path: Path) -> None:
    write_run(RECORD, tmp_path)
    assert read_run(tmp_path) == RECORD


def test_the_schema_version_is_written_as_schema(tmp_path: Path) -> None:
    """`schema` is the field DESIGN.md 9.1 names; `schema_version` is python."""
    import json

    path = write_run(RECORD, tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema"] == SCHEMA_VERSION


def test_a_record_from_a_schema_this_swage_does_not_read_is_refused(
    tmp_path: Path,
) -> None:
    """A version nobody checks is decoration."""
    (tmp_path / "run.json").write_text('{"schema": 99, "feedstocks": []}')
    with pytest.raises(ReportError, match="schema 99"):
        read_run(tmp_path)


def test_a_missing_artifact_names_the_path(tmp_path: Path) -> None:
    with pytest.raises(ReportError, match="no run artifact"):
        read_run(tmp_path)


def test_a_truncated_artifact_is_refused(tmp_path: Path) -> None:
    (tmp_path / "run.json").write_text(_HEADER + '"feedsto')
    with pytest.raises(ReportError, match="not valid JSON"):
        read_run(tmp_path)


def test_a_record_missing_a_required_field_is_refused(tmp_path: Path) -> None:
    (tmp_path / "run.json").write_text(
        _HEADER + '"feedstocks": [{"outcome": "unchanged"}]}'
    )
    with pytest.raises(ReportError, match="not a run record swage can read"):
        read_run(tmp_path)


def test_an_outcome_this_swage_lacks_does_not_fail_the_whole_file(
    tmp_path: Path,
) -> None:
    """One feedstock must not take `explain` down for the rest of the run.

    This used to be refused, on the reasoning that the buckets are the
    report's vocabulary and an unknown outcome renders nowhere. Rendering
    nowhere was the part worth fixing: the record is readable, it is one of
    several hundred, and refusing the value costs the other several hundred
    their `explain`.
    """
    (tmp_path / "run.json").write_text(
        _HEADER + '"feedstocks": [{"feedstock": "x", "outcome": "unchanged"},'
        ' {"feedstock": "y", "outcome": "spectacularly-merged"}]}'
    )
    run = read_run(tmp_path)
    assert [record.outcome for record in run.feedstocks] == [
        "unchanged",
        "spectacularly-merged",
    ]


def test_an_outcome_this_swage_lacks_is_kept_verbatim(tmp_path: Path) -> None:
    """Not folded into a sentinel: what the other swage said is the evidence."""
    (tmp_path / "run.json").write_text(
        _HEADER + '"feedstocks": [{"feedstock": "y", "outcome": "half-merged"}]}'
    )
    assert read_run(tmp_path).feedstocks[0].outcome == "half-merged"


def test_a_run_naming_a_retired_outcome_still_reads(tmp_path: Path) -> None:
    """`nothing-to-reconcile` is what `not-reconciled` used to be called.

    Not a hypothesis about some future swage: renaming it left `run.json`
    files on disk that a matching `schema` version says are readable and that
    the record refused, so `swage explain` died on every feedstock in them.
    Renaming a value is invisible to a version that describes the shape.
    """
    (tmp_path / "run.json").write_text(
        _HEADER + '"feedstocks": [{"feedstock": "esmf",'
        ' "outcome": "nothing-to-reconcile"}]}'
    )
    record = read_run(tmp_path).feedstocks[0]
    assert record.outcome == "nothing-to-reconcile"
    assert record.needs_review is True


def test_an_outcome_this_swage_lacks_still_wants_a_human() -> None:
    """Exit code 0 claims nothing needs you, and swage has no basis for it."""
    record = FeedstockRecord(feedstock="y", outcome="something-new")
    assert record.needs_review is True
    assert RunRecord(feedstocks=(record,)).needs_review is True


def test_the_outcomes_swage_writes_all_have_a_bucket() -> None:
    """Two hand-kept lists of the same thirteen strings, held to each other.

    This is what the `Literal` on the record used to be doing by accident, and
    it does it better: a value in `Outcome` with no row in `OUTCOMES` is a
    feedstock that renders nowhere, which is the same defect from swage's own
    side rather than a newer swage's.
    """
    assert set(get_args(Outcome)) == {outcome for outcome, _, _ in OUTCOMES}


def test_a_field_a_newer_swage_added_does_not_break_the_read(tmp_path: Path) -> None:
    """Forward compatibility is the half a version number cannot give you."""
    (tmp_path / "run.json").write_text(
        _HEADER + '"feedstocks": [{"feedstock": "x", "outcome": "unchanged",'
        ' "something_new": 5}]}'
    )
    assert read_run(tmp_path).feedstocks[0].feedstock == "x"


def test_reading_accepts_the_file_as_well_as_its_directory(tmp_path: Path) -> None:
    path = write_run(RECORD, tmp_path)
    assert read_run(path) == RECORD


def test_lookups_by_outcome_and_name() -> None:
    assert [r.feedstock for r in RECORD.by_outcome("unchanged")] == [
        "google-cloud-storage"
    ]
    found = RECORD.find("google-cloud-bigquery")
    assert found is not None
    assert [gate.name for gate in found.failures] == ["G9"]
    assert RECORD.find("nothing-here") is None


def test_needs_review_is_what_exit_code_1_reads() -> None:
    assert RECORD.needs_review is True
    quiet = RunRecord(feedstocks=(FeedstockRecord(feedstock="x", outcome="unchanged"),))
    assert quiet.needs_review is False


def test_the_run_directory_is_named_for_when_the_run_started(tmp_path: Path) -> None:
    when = datetime(2026, 8, 11, 14, 2, tzinfo=UTC)
    assert run_directory(when, tmp_path) == tmp_path / "runs" / "2026-08-11T14-02-00"


def test_the_cache_root_honours_xdg(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert run_directory().is_relative_to(tmp_path / "swage" / "runs")


def test_the_suite_caches_somewhere_other_than_home() -> None:
    """Nothing under test may write the cache the maintainer's swage reads.

    A test that leaves a real cache behind reports nothing: the suite passes,
    and the damage shows up later as swage completing two feedstock names or
    re-downloading an archive it already had. `cache_elsewhere` in `conftest`
    is what keeps that from happening, and this is what says so out loud.
    """
    assert not cache_root().is_relative_to(Path.home() / ".cache")


def test_write_recipes_leaves_both_sides_on_disk(tmp_path: Path) -> None:
    """DESIGN.md 10's differential validation, as a by-product of scanning."""
    run = RunRecord(
        command="swage scan --all",
        started="2026-08-13T07:00:00+00:00",
        feedstocks=(
            FeedstockRecord(
                feedstock="demo",
                outcome="merge-ready",
                rendered_recipe="requirements:\n  run:\n    - requests >=2\n",
                current_recipe="requirements:\n  run:\n    - requests\n",
            ),
            # Never reached a plan, so there is nothing to write for it.
            FeedstockRecord(feedstock="quiet", outcome="unchanged"),
        ),
    )
    written = write_recipes(run, tmp_path)

    assert [path.name for path in written] == ["recipe.yaml", "recipe.before.yaml"]
    root = tmp_path / RECIPES_DIR / "demo"
    assert (
        root / "recipe.yaml"
    ).read_text() == "requirements:\n  run:\n    - requests >=2\n"
    assert (
        root / "recipe.before.yaml"
    ).read_text() == "requirements:\n  run:\n    - requests\n"
    assert not (tmp_path / RECIPES_DIR / "quiet").exists()


DIFF = """\
--- 1.0.0/m4/netcdf.m4
+++ 2.0.0/m4/netcdf.m4
@@ -1 +1 @@
-AC_DEFUN([ACX_NETCDF], [])
+AC_DEFUN([ACX_NETCDF], [4.9])
"""


def test_write_declarations_leaves_the_diff_on_disk(tmp_path: Path) -> None:
    """The whole answer swage has about a feedstock it cannot read (3.6.8).

    Both releases' copies were in hand to decide the outcome, so keeping the
    comparison costs nothing already fetched -- and it is what stops the
    summary's capped excerpt from being all there is.
    """
    run = RunRecord(
        command="swage update --feedstock ncview",
        started="2026-08-28T07:00:00+00:00",
        feedstocks=(
            FeedstockRecord(
                feedstock="ncview",
                outcome="declaration-moved",
                declaration_diff=DIFF,
            ),
            # Compared and unchanged, so there is no diff to write for it.
            FeedstockRecord(feedstock="quiet", outcome="not-read"),
        ),
    )

    written = write_declarations(run, tmp_path)

    assert [path.name for path in written] == ["ncview.diff"]
    assert (tmp_path / DECLARATIONS_DIR / "ncview.diff").read_text() == DIFF
    assert not (tmp_path / DECLARATIONS_DIR / "quiet.diff").exists()


def test_the_declaration_diff_stays_out_of_run_json(tmp_path: Path) -> None:
    """A `configure.ac` is long, and `run.json` is parsed by other things."""
    run = RunRecord(
        started="2026-08-28T07:00:00+00:00",
        feedstocks=(
            FeedstockRecord(
                feedstock="ncview",
                outcome="declaration-moved",
                declaration_diff=DIFF,
            ),
        ),
    )
    write_run(run, tmp_path)

    assert "ACX_NETCDF" not in (tmp_path / "run.json").read_text()
    assert read_run(tmp_path).feedstocks[0].declaration_diff == ""


def test_the_recipes_stay_out_of_run_json(tmp_path: Path) -> None:
    """`run.json` is a contract other things read; two recipes per feedstock
    would bloat it, and a file is the right shape for something to be diffed."""
    run = RunRecord(
        command="swage scan",
        started="2026-08-13T07:00:00+00:00",
        feedstocks=(
            FeedstockRecord(
                feedstock="demo",
                outcome="merge-ready",
                rendered_recipe="- requests >=2\n",
                current_recipe="- requests\n",
            ),
        ),
    )
    path = write_run(run, tmp_path)
    written = path.read_text()
    # The texts themselves, not a substring of them: "requests" also occurs in
    # `pull_requests`, which is how the first version of this test passed for
    # the wrong reason.
    assert run.feedstocks[0].rendered_recipe not in written
    assert run.feedstocks[0].current_recipe not in written
    # And it still round-trips, with the excluded fields simply absent.
    assert read_run(tmp_path).feedstocks[0].rendered_recipe == ""


def test_a_moved_declaration_wants_a_human_and_an_unread_one_does_not() -> None:
    """The whole reason they are two buckets rather than one.

    Nothing is wrong with a feedstock swage does not read -- the config says
    so on purpose. A declaration that moved is a different claim: the file the
    recipe was last reconciled against is not the file upstream now ships, and
    only a person can say what that means.
    """
    moved = FeedstockRecord(feedstock="ncview", outcome="declaration-moved")
    unread = FeedstockRecord(feedstock="ncview", outcome="not-read")
    assert moved.needs_review is True
    assert unread.needs_review is False
