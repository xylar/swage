"""Tests for the grouped terminal summary (DESIGN.md 9).

The example in DESIGN.md 9 is the specification, so the first test builds the
run it depicts and checks the rendering against it. That is a claim about the
spec rather than about my expectations, which is the same reason the planner
is tested against the corpus.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from swage.report import (
    DECLARATIONS_DIR,
    FeedstockRecord,
    RunRecord,
    render_summary,
    supports_color,
)


def _run(*records: FeedstockRecord, command: str = "", started: str = "") -> RunRecord:
    return RunRecord(command=command, started=started, feedstocks=records)


def _many(outcome: str, count: int, prefix: str = "f") -> list[FeedstockRecord]:
    return [
        FeedstockRecord(feedstock=f"{prefix}{i}", outcome=outcome) for i in range(count)
    ]


def test_the_summary_matches_the_example_in_the_design() -> None:
    run = _run(
        *_many("ready-to-merge", 28, "m"),
        *_many("merge-ready", 41, "r"),
        *_many("awaiting-ci", 13, "a"),
        *_many("proposed", 12, "p"),
        FeedstockRecord(
            feedstock="google-cloud-aiplatform",
            outcome="needs-review",
            detail="upstream extra 'evaluation' is in neither list",
        ),
        FeedstockRecord(
            feedstock="google-cloud-bigquery",
            outcome="needs-review",
            detail="no conda-forge package found for 'db-dtypes'",
        ),
        FeedstockRecord(
            feedstock="google-cloud-spanner",
            outcome="degraded",
            detail="label API call failed after 3 attempts",
        ),
        *_many("migrated", 3, "g"),
        *_many("needs-migration", 18, "n"),
        *_many("unchanged", 206, "u"),
        FeedstockRecord(
            feedstock="markupsafe",
            outcome="failed",
            detail="`markupsafe` chooses whether it is noarch rather than stating it",
        ),
        command="swage update --family google-cloud",
        started="2026-08-11T14:02:00Z",
    )
    lines = render_summary(run, width=88, color=False).splitlines()

    header = lines[0]
    assert header.startswith("swage update --family google-cloud    2026-08-11 14:02")
    assert header.endswith("(325 scanned)")
    assert len(header) == 88
    assert lines[1] == ""

    assert lines[2:] == [
        "  READY TO MERGE (28)  nothing to change and CI is green -- merge these yourself",  # noqa: E501
        "  MERGE-READY (41)     pushed + labeled automerge; conda-forge merges it on green CI",  # noqa: E501
        "  AWAITING CI (13)     no changes needed; `automerge` is yours to add while CI runs",  # noqa: E501
        "  PROPOSED (12)        pushed, needs your review before labeling",
        "  NEEDS REVIEW (2)",
        "    google-cloud-aiplatform  upstream extra 'evaluation' is in neither list",
        "    google-cloud-bigquery    no conda-forge package found for 'db-dtypes'",
        "  DEGRADED (1)         pushed but NOT labeled -- merge it yourself",
        "    google-cloud-spanner     label API call failed after 3 attempts",
        "  MIGRATED (3)         v0 -> v1 converted and updated -- review both commits",
        "  NEEDS MIGRATION (18) v0 meta.yaml -- rerun with `--migrate` to convert in place",  # noqa: E501
        "  UNCHANGED (206)      no open bot PR",
        "  FAILED (1)",
        "    markupsafe               `markupsafe` chooses whether it is noarch rather than",  # noqa: E501
        "                             stating it",
    ]


def test_an_empty_bucket_is_not_printed() -> None:
    """A run over one family should not list eight outcomes it cannot produce."""
    rendered = render_summary(
        _run(FeedstockRecord(feedstock="x", outcome="unchanged")), color=False
    )
    assert "UNCHANGED (1)" in rendered
    assert "MERGED" not in rendered
    assert "FAILED" not in rendered


def test_only_feedstocks_with_something_to_say_are_listed() -> None:
    """206 lines of "no open bot PR" would bury the nine that need reading."""
    rendered = render_summary(
        _run(*_many("unchanged", 206, "u")), width=88, color=False
    )
    assert "UNCHANGED (206)" in rendered
    assert "u0" not in rendered


def test_a_long_detail_wraps_under_itself_rather_than_beside() -> None:
    run = _run(
        FeedstockRecord(
            feedstock="google-cloud-kms",
            outcome="needs-review",
            detail=(
                "G1: 'grpcio-gcp' in recipe, in no upstream version -- declare "
                "in add_requirements or drop"
            ),
        )
    )
    body = render_summary(run, width=76, color=False).splitlines()[2:]
    detail = [line for line in body if line.startswith("    ")]
    assert len(detail) == 2
    assert detail[0].startswith("    google-cloud-kms  G1:")
    # The continuation lines up under the detail, not under the name.
    assert detail[1].startswith(" " * detail[0].index("G1:"))
    assert all(len(line) <= 76 for line in detail)


def test_the_counts_are_of_feedstocks_scanned() -> None:
    rendered = render_summary(
        _run(*_many("unchanged", 3), started="2026-08-11T14:02:00Z"), color=False
    )
    assert "(3 scanned)" in rendered.splitlines()[0]


def test_the_run_directory_is_printed_when_there_is_one(tmp_path: Path) -> None:
    """Outside the home directory it prints in full, separators and all.

    Anchored at the drive root rather than under `tmp_path`, because on
    GitHub's Windows runners the temp directory *is* inside the home
    directory and would be abbreviated.
    """
    directory = Path(tmp_path.anchor) / "swage-runs" / "2026-08-11T14-02"
    rendered = render_summary(
        _run(FeedstockRecord(feedstock="x", outcome="unchanged")),
        run_directory=directory,
        color=False,
    )
    assert rendered.splitlines()[-1] == f"  run: {directory}{os.sep}"


def test_a_directory_under_home_is_abbreviated_in_native_separators(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    r"""`~/AppData\Local\Temp` is a path in two conventions and pastes nowhere."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    rendered = render_summary(
        _run(FeedstockRecord(feedstock="x", outcome="unchanged")),
        run_directory=tmp_path / "cache" / "swage" / "runs" / "2026-08-11T14-02",
        color=False,
    )
    expected = Path("~") / "cache" / "swage" / "runs" / "2026-08-11T14-02"
    assert rendered.splitlines()[-1] == f"  run: {expected}{os.sep}"


def test_colour_wraps_only_the_heading() -> None:
    run = _run(FeedstockRecord(feedstock="markupsafe", outcome="failed", detail="boom"))
    rendered = render_summary(run, width=88, color=True)
    assert "\033[1;31mFAILED (1)\033[0m" in rendered
    # The feedstock line stays plain, so piping to grep keeps working.
    assert "    markupsafe  boom" in rendered


def test_colour_is_off_when_asked_not_to() -> None:
    run = _run(FeedstockRecord(feedstock="x", outcome="failed", detail="boom"))
    assert "\033[" not in render_summary(run, color=False)


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({"NO_COLOR": "1", "CLICOLOR_FORCE": "1"}, False),
        ({"CLICOLOR_FORCE": "1"}, True),
        ({"CLICOLOR_FORCE": "0"}, False),
        ({}, False),
    ],
)
def test_colour_detection_follows_the_usual_environment_rules(
    monkeypatch: pytest.MonkeyPatch, env: dict[str, str], expected: bool
) -> None:
    """NO_COLOR wins over everything, which is what the standard says."""
    for name in ("NO_COLOR", "CLICOLOR_FORCE", "TERM"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    assert supports_color(stream=None) is expected


def test_a_note_names_a_feedstock_that_has_no_detail() -> None:
    """DESIGN.md 4's promise: reported and not gated.

    A merge-ready feedstock has no failing gate and so no `detail`, which is
    exactly the case the note exists for -- and exactly the case that would
    print nothing at all if listing keyed on `detail` alone.
    """
    run = _run(
        FeedstockRecord(
            feedstock="google-cloud-storage",
            outcome="merge-ready",
            notes=(
                "upstream 2.19.0 declares extra 'tracing', which no output draws on",
            ),
        ),
        *_many("unchanged", 3),
    )
    rendered = render_summary(run, width=100, color=False)
    assert "google-cloud-storage" in rendered
    assert "note: upstream 2.19.0 declares extra 'tracing'" in rendered
    # The three unchanged feedstocks say nothing and stay unlisted.
    assert "f0" not in rendered


def test_a_note_sits_under_the_detail_rather_than_beside_the_name() -> None:
    """The two are different claims, so they must not share a column."""
    run = _run(
        FeedstockRecord(
            feedstock="demo",
            outcome="needs-review",
            detail="not approved for automatic merging (trust: never)",
            notes=("upstream 1.2.3 declares extra 'docs', which no output draws on",),
        )
    )
    lines = render_summary(run, width=100, color=False).splitlines()
    detail = next(i for i, line in enumerate(lines) if "not approved" in line)
    note = next(i for i, line in enumerate(lines) if "note:" in line)
    assert note == detail + 1
    assert "demo" in lines[detail]
    assert "demo" not in lines[note]


def test_a_pull_request_that_needs_a_person_is_one_click_away() -> None:
    """swage cannot merge, so getting somebody there is what it can do.

    Printed for the buckets whose content is "go and do something on GitHub"
    and for no others: a run over several hundred feedstocks should not put a
    URL under every line it prints.
    """
    run = _run(
        FeedstockRecord(
            feedstock="google-ads",
            outcome="ready-to-merge",
            detail="CI passed: linter, github-actions",
            pull_request=55,
        ),
        FeedstockRecord(feedstock="quiet", outcome="unchanged", pull_request=3),
    )

    rendered = render_summary(run, width=88, color=False)

    assert "https://github.com/conda-forge/google-ads-feedstock/pull/55" in rendered
    assert "quiet-feedstock" not in rendered


def test_a_link_is_never_wrapped() -> None:
    """A URL split across two lines is one nobody can click or paste."""
    run = _run(
        FeedstockRecord(
            feedstock="apache-airflow-providers-microsoft-azure",
            outcome="ready-to-merge",
            detail="CI passed: linter, azure",
            pull_request=68,
        )
    )

    lines = render_summary(run, width=60, color=False).splitlines()
    links = [line for line in lines if "https://" in line]

    assert len(links) == 1
    assert links[0].strip().endswith("/pull/68")


def test_a_merged_pull_request_is_named_and_linked() -> None:
    """The morning-after report's good news, and it names what landed.

    Linked for a different reason than the buckets asking for action: nothing
    is wanted from the reader, but "what did swage do while I was asleep" is
    only worth answering if the pull request it names can be opened.
    """
    rendered = render_summary(
        _run(
            FeedstockRecord(
                feedstock="google-ads",
                outcome="merged",
                detail="merged since the run that pushed it",
                pull_request=55,
            )
        ),
        width=88,
        color=False,
    )
    assert "MERGED (1)" in rendered
    assert "landed since the run that made it" in rendered
    assert "https://github.com/conda-forge/google-ads-feedstock/pull/55" in rendered


def test_a_closed_pull_request_says_the_work_was_not_taken() -> None:
    rendered = render_summary(
        _run(
            FeedstockRecord(
                feedstock="demo",
                outcome="closed",
                detail="closed without merging",
                pull_request=7,
            )
        ),
        width=88,
        color=False,
    )
    assert "CLOSED (1)" in rendered
    assert "swage's work was not taken" in rendered
    assert "https://github.com/conda-forge/demo-feedstock/pull/7" in rendered


def test_awaiting_ci_hands_the_label_over_while_it_still_works() -> None:
    """The one window in which `automerge` is not inert (DESIGN.md 2.1, 5.2).

    swage pushed nothing, so it labels nothing -- but CI has not finished, and
    the status events still to come would dispatch conda-forge's automerge job
    for whoever labels the pull request first. The line says so, and the URL
    is under it because an instruction with a deadline should not also be an
    errand.
    """
    rendered = render_summary(
        _run(
            FeedstockRecord(
                feedstock="google-resumable-media",
                outcome="awaiting-ci",
                detail="CI has not finished: linter, github-actions",
                pull_request=42,
            )
        ),
        width=88,
        color=False,
    )
    assert "`automerge` is yours to add while CI runs" in rendered
    assert (
        "https://github.com/conda-forge/google-resumable-media-feedstock/pull/42"
        in rendered
    )


def test_degraded_does_not_send_the_reader_back_to_status() -> None:
    """Labeling it later summons nothing (DESIGN.md 2.1), so a person merges it."""
    rendered = render_summary(
        _run(FeedstockRecord(feedstock="demo", outcome="degraded")), color=False
    )
    assert "merge it yourself" in rendered
    assert "swage status" not in rendered


def test_the_header_says_what_the_command_actually_did() -> None:
    """`status` followed pull requests up; it scanned no feedstocks."""
    run = _run(FeedstockRecord(feedstock="demo", outcome="merged"))
    assert "(1 scanned)" in render_summary(run, width=88, color=False)
    assert "(1 followed up)" in render_summary(
        run, width=88, color=False, counted="followed up"
    )


def test_a_feedstock_with_many_notes_does_not_bury_the_rest_of_the_run() -> None:
    """`google-cloud-aiplatform` declares 35 extras no output draws on.

    Printing every one filled half a fifty-feedstock audit with one feedstock's
    advisories and buried the line naming the decision it actually needs. Same
    rule a gate's detail already follows: name the first, count the rest, and
    `explain` is where all of them live.
    """
    rendered = render_summary(
        _run(
            FeedstockRecord(
                feedstock="google-cloud-aiplatform",
                outcome="needs-review",
                detail="would remove `google-api-core`",
                notes=tuple(f"upstream declares extra {n!r}" for n in range(35)),
            )
        ),
        width=88,
        color=False,
    )
    assert rendered.count("note:") == 4, "three notes and one line counting the rest"
    assert "note: and 32 more" in rendered
    assert (
        "for the full explanation, run: swage explain google-cloud-aiplatform"
        in rendered
    )
    assert "would remove `google-api-core`" in rendered


def test_a_feedstock_with_few_notes_still_prints_all_of_them() -> None:
    rendered = render_summary(
        _run(
            FeedstockRecord(
                feedstock="demo", outcome="merge-ready", notes=("one", "two")
            )
        ),
        width=88,
        color=False,
    )
    assert "note: one" in rendered
    assert "note: two" in rendered
    assert "more --" not in rendered


def test_an_outcome_this_swage_lacks_is_printed_rather_than_dropped() -> None:
    """A record present in `run.json` and absent from the report is the worst case.

    The bucket loop walks the outcomes this swage knows, so without a bucket of
    its own a feedstock a newer swage classified would vanish from what a
    person reads, with nothing anywhere saying one had gone missing. A crash
    gets investigated; a silent omission does not.
    """
    rendered = render_summary(
        _run(
            FeedstockRecord(feedstock="known", outcome="unchanged"),
            FeedstockRecord(feedstock="strange", outcome="half-merged"),
        ),
        width=88,
        color=False,
    )
    assert "UNRECOGNIZED (1)" in rendered
    assert "half-merged -- written by a newer swage than this one" in rendered
    assert "UNCHANGED (1)" in rendered


def test_every_unrecognized_outcome_is_named_once() -> None:
    rendered = render_summary(
        _run(*_many("from-the-future", 3), *_many("also-new", 2, "g")),
        width=88,
        color=False,
    )
    assert "UNRECOGNIZED (5)" in rendered
    assert "also-new, from-the-future" in rendered


def test_a_run_of_known_outcomes_prints_no_unrecognized_bucket() -> None:
    rendered = render_summary(_run(*_many("unchanged", 2)), width=88, color=False)
    assert "UNRECOGNIZED" not in rendered


# --- the declaration diff (DESIGN.md 3.6.8) ---------------------------------

MOVED = FeedstockRecord(
    feedstock="ncview",
    outcome="declaration-moved",
    detail="m4macros/netcdf.m4 changed from 2.1.8 to 2.1.9",
    declaration_diff=(
        "--- 2.1.8/m4macros/netcdf.m4\n"
        "+++ 2.1.9/m4macros/netcdf.m4\n"
        "@@ -1,3 +1,3 @@\n"
        " AC_DEFUN([AC_PATH_NETCDF], [\n"
        "-  NETCDF_MIN=4.7\n"
        "+  NETCDF_MIN=4.9\n"
        " ])\n"
    ),
)


def test_a_declaration_that_moved_prints_its_diff() -> None:
    """On a feedstock with no reader this is the whole answer swage has.

    Naming the file says where to look; these lines are what to look at, and
    sending the reader to a second command for them cost two fetches of both
    archives to see something swage had already compared.
    """
    rendered = render_summary(_run(MOVED), width=88, color=False)

    assert "--- 2.1.8/m4macros/netcdf.m4" in rendered
    assert "-  NETCDF_MIN=4.7" in rendered
    assert "+  NETCDF_MIN=4.9" in rendered
    assert "more lines" not in rendered


def test_a_diff_line_is_never_wrapped() -> None:
    """A diff folded to the terminal width is not a diff.

    The same rule the URL follows: overflowing the column is the smaller cost
    against a line nobody can read or paste.
    """
    long_line = "+  " + "x" * 120
    rendered = render_summary(
        _run(
            FeedstockRecord(
                feedstock="demo",
                outcome="declaration-moved",
                detail="configure.ac changed from 1 to 2",
                declaration_diff=f"{long_line}\n",
            )
        ),
        width=88,
        color=False,
    )

    assert long_line in rendered


def test_a_long_diff_is_capped_and_says_where_the_rest_is(tmp_path: Path) -> None:
    """One rewritten `configure.ac` must not become the whole report."""
    rendered = render_summary(
        _run(
            FeedstockRecord(
                feedstock="demo",
                outcome="declaration-moved",
                detail="configure.ac changed from 1 to 2",
                declaration_diff="".join(f"+line {i}\n" for i in range(60)),
            )
        ),
        run_directory=tmp_path,
        width=88,
        color=False,
    )

    assert "+line 39" in rendered
    assert "+line 40" not in rendered
    # Matched on the tail rather than on `tmp_path` itself: the report
    # abbreviates a path under the home directory, and on Windows the pytest
    # temporary directory is one -- so the line names `~\AppData\...` there
    # and the full path everywhere else.
    named = next(line for line in rendered.splitlines() if "more lines" in line)
    assert named.strip().startswith("... and 20 more lines: ")
    assert named.endswith(str(Path(DECLARATIONS_DIR) / "demo.diff"))


def test_a_capped_diff_with_no_run_directory_still_counts_the_rest() -> None:
    rendered = render_summary(
        _run(
            FeedstockRecord(
                feedstock="demo",
                outcome="declaration-moved",
                detail="configure.ac changed from 1 to 2",
                declaration_diff="".join(f"+line {i}\n" for i in range(60)),
            )
        ),
        width=88,
        color=False,
    )

    assert "... and 20 more lines" in rendered


def test_a_diff_at_the_end_of_the_report_is_not_padded(tmp_path: Path) -> None:
    """The blank line under a diff separates it from the next bucket.

    The last diff in a report has nothing after it but the run directory, and
    a gap there reads as a section that failed to render.
    """
    rendered = render_summary(_run(MOVED), run_directory=tmp_path, width=88)

    assert "\n\n\n" not in rendered
    assert rendered.splitlines()[-3].strip() == "])"
