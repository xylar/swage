"""Tests for the run record and its artifact (DESIGN.md 9, 9.1).

`run.json` is a contract rather than a debug dump: `swage explain --from-run`
reads one back, possibly one written by a different version of swage. So the
tests that matter are about what happens at the read -- a record that has
drifted must fail loudly, naming what it saw, rather than half-rendering.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from swage.report import (
    SCHEMA_VERSION,
    FeedstockRecord,
    GateRecord,
    PlannedLine,
    ReportError,
    RunRecord,
    SectionRecord,
    UpstreamRecord,
    read_run,
    run_directory,
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
            label="swage:needs-review",
        ),
        FeedstockRecord(feedstock="google-cloud-storage", outcome="unchanged"),
    ),
)


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
    (tmp_path / "run.json").write_text('{"schema": 1, "feedsto')
    with pytest.raises(ReportError, match="not valid JSON"):
        read_run(tmp_path)


def test_a_record_missing_a_required_field_is_refused(tmp_path: Path) -> None:
    (tmp_path / "run.json").write_text(
        '{"schema": 1, "feedstocks": [{"outcome": "unchanged"}]}'
    )
    with pytest.raises(ReportError, match="not a run record swage can read"):
        read_run(tmp_path)


def test_an_unknown_outcome_is_refused(tmp_path: Path) -> None:
    """The buckets are the report's vocabulary; an unknown one renders nowhere."""
    (tmp_path / "run.json").write_text(
        '{"schema": 1, "feedstocks": [{"feedstock": "x", "outcome": "banana"}]}'
    )
    with pytest.raises(ReportError, match="not a run record swage can read"):
        read_run(tmp_path)


def test_a_field_a_newer_swage_added_does_not_break_the_read(tmp_path: Path) -> None:
    """Forward compatibility is the half a version number cannot give you."""
    (tmp_path / "run.json").write_text(
        '{"schema": 1, "feedstocks": [{"feedstock": "x", "outcome": "unchanged",'
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
