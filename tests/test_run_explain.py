"""Tests for `swage explain` (DESIGN.md §11.3, design-v1.md 9.2).

Built against the example in design-v1.md 9.2, and against the rules stated
beneath it -- the action is the first token, every source is a file path or a
named layer, findings and verdict come last, and a feedstock that stopped
before planning still explains itself.
"""

from __future__ import annotations

import pytest

from swage.run import (
    CheckRecord,
    FindingRecord,
    MergeCheckRecord,
    OutputRecord,
    PlannedLine,
    Record,
    SectionRecord,
    UpstreamRecord,
    render_explain,
)

RECORD = Record(
    feedstock="google-cloud-bigquery",
    outcome="needs-review",
    reason="would remove grpcio-status, gone in 3.44.0",
    decision="nothing",
    pull_request=187,
    head="4a2f1c8",
    upstream=UpstreamRecord(
        name="google-cloud-bigquery",
        version="3.44.0",
        source="https://pypi.org/.../google_cloud_bigquery-3.44.0.tar.gz",
        declared_in="pyproject.toml + PKG-INFO",
        previous="3.43.0",
    ),
    outputs=(
        OutputRecord(
            name="google-cloud-bigquery",
            artifacts="one",
            pythons=">=3.9",
            floor="3.9",
            floor_source=".ci_support/linux_64_.yaml",
        ),
        OutputRecord(
            name="google-cloud-bigquery-with-pandas",
            artifacts="one",
            pythons=">=3.9",
            floor="3.9",
            floor_source=".ci_support/linux_64_.yaml",
        ),
    ),
    config_layers=(
        "config/feedstocks/google-cloud-bigquery.yaml",
        "config/families/google-cloud.yaml",
        "config/defaults.yaml",
    ),
    sections=(
        SectionRecord(
            path="/outputs/1/requirements/run",
            section="run",
            where="`google-cloud-bigquery-with-pandas`'s `run` requirements",
            lines=(
                PlannedLine(
                    action="keep",
                    text="python >=${{ python_min }}",
                    origin="recipe-kept",
                    source="recipe_owned.names",
                ),
                PlannedLine(
                    action="bump",
                    text="google-auth >=2.14.1 -> >=2.15.0",
                    origin="upstream-core",
                    source="identity",
                ),
                PlannedLine(
                    action="add",
                    text="proto-plus >=1.26.1",
                    origin="upstream-extra",
                    source="extra:bigquery-v2",
                ),
                PlannedLine(
                    action="drop",
                    text="grpcio-status >=1.33.2",
                    origin="upstream-dropped",
                    source="absent in 3.44.0",
                ),
            ),
        ),
    ),
    findings=(
        FindingRecord(
            kind="removal",
            title="a requirement would be removed without review",
            subject="grpcio-status",
            said="would remove grpcio-status, gone in 3.44.0",
            remedy="set `removals: auto` to let it go",
        ),
        FindingRecord(
            kind="unassociated-constraint",
            title="a run constraint is tied to no upstream extra",
            subject="protobuf",
            said="run_constraints `protobuf` is tied to no upstream extra",
        ),
    ),
)


def sections(rendered: str) -> list[str]:
    return [line for line in rendered.splitlines() if line and not line.startswith(" ")]


def test_the_four_sections_come_in_the_order_the_questions_are_asked() -> None:
    """Findings and verdict last: "why did this not merge" is why someone ran it."""
    headings = sections(render_explain(RECORD))
    assert headings[0].startswith("swage explain google-cloud-bigquery")
    assert headings[1:] == [
        "INPUTS",
        "PLAN  `google-cloud-bigquery-with-pandas`'s `run` requirements",
        "FINDINGS",
        "VERDICT  needs-review   (2 findings)",
    ]


def test_a_run_written_before_sections_had_a_label_still_renders() -> None:
    """`explain` reads a run artifact, which may predate the field it wants.

    `where` is what a person reads and `path` is the key beside it, so a
    record from an older run has the key and not the words. Printing nothing
    there would lose the only thing that says which section the plan is of --
    and printing the key itself puts a position in a parsed document in front
    of somebody reading about their recipe, so it is read into words instead.
    """
    older = RECORD.model_copy(
        update={
            "sections": (RECORD.sections[0].model_copy(update={"where": ""}),),
        }
    )

    headings = sections(render_explain(older))

    assert "PLAN  the `run` requirements of the recipe's output 2" in headings


def plan_lines(rendered: str) -> list[str]:
    """The lines of the PLAN block, which run until the next blank line."""
    lines = rendered.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("PLAN ")) + 1
    end = lines.index("", start)
    return lines[start:end]


def test_the_action_is_the_first_token_so_a_plan_reads_as_a_diff() -> None:
    assert [line.split()[0] for line in plan_lines(render_explain(RECORD))] == [
        "keep",
        "~bump",
        "+add",
        "-drop",
    ]


def test_every_line_names_where_it_came_from() -> None:
    """Greppability is the point: `explain X | grep upstream-core` is a question."""
    rendered = render_explain(RECORD)
    assert "recipe-kept       recipe_owned.names" in rendered
    assert "upstream-extra    extra:bigquery-v2" in rendered
    assert "upstream-dropped  absent in 3.44.0" in rendered


def test_the_inputs_name_both_versions_and_where_each_came_from() -> None:
    rendered = render_explain(RECORD)
    assert "google-cloud-bigquery 3.44.0" in rendered
    assert "google_cloud_bigquery-3.44.0.tar.gz" in rendered
    # Which release and which file in it are two steps of one lookup, and the
    # tarball URL answers only the first (design-v1.md 9.2).
    assert "declared in pyproject.toml + PKG-INFO" in rendered
    # The previous version is what classifies a removal (design-v1.md 3.3.7), so
    # a report that omits it cannot explain a drop.
    assert "previous 3.43.0" in rendered
    assert "floor 3.9" in rendered
    assert ".ci_support/linux_64_.yaml" in rendered


def test_every_output_says_which_pythons_it_was_planned_for() -> None:
    """The build model is a property of each output (DESIGN.md §1)."""
    rendered = render_explain(RECORD)
    assert (
        "output      google-cloud-bigquery  one noarch package for every python "
        "and platform  python >=3.9"
    ) in rendered
    assert "output      google-cloud-bigquery-with-pandas  one noarch" in rendered


def test_a_v1_record_counts_its_outputs() -> None:
    """v1 counted the outputs and named none, and recorded one floor."""
    older = RECORD.model_copy(
        update={
            "outputs": (
                OutputRecord(floor="3.9", floor_source="linux_64_.yaml"),
                OutputRecord(floor="3.9", floor_source="linux_64_.yaml"),
            )
        }
    )
    rendered = render_explain(older)
    assert "  outputs     2" in rendered
    assert "output      the recipe's" not in rendered
    assert rendered.count("floor 3.9") == 1


def test_config_layers_are_listed_most_specific_first() -> None:
    rendered = render_explain(RECORD).splitlines()
    layers = [line.split()[-1] for line in rendered if "config/" in line]
    assert layers == [
        "config/feedstocks/google-cloud-bigquery.yaml",
        "config/families/google-cloud.yaml",
        "config/defaults.yaml",
    ]


def test_every_check_is_named_by_what_it_says_and_never_by_its_kind() -> None:
    """`removal` is unreadable without the design; this is the whole point.

    The identifier stays in the record, because `run.json` wants a stable key
    and the code has to call each check something -- but nothing prints one.
    """
    rendered = render_explain(RECORD)
    assert "  a requirement would be removed without review" in rendered
    assert (
        "        would remove grpcio-status, gone in 3.44.0 -- set `removals: "
        "auto` to let it go"
    ) in rendered
    findings = rendered.split("FINDINGS")[1].split("VERDICT")[0]
    assert not any(
        line.strip().startswith(("removal", "unassociated-constraint"))
        for line in findings.splitlines()
    )


def test_every_failure_starts_its_reason_in_the_same_column() -> None:
    """A reason is indented under its check rather than beside its name.

    Names of different widths used to push their reasons to different
    columns, an offset `textwrap` then inherited for every continuation line.
    A fixed indent cannot drift that way whatever a check is called.
    """
    record = Record(
        feedstock="demo",
        outcome="needs-review",
        findings=(
            FindingRecord(
                kind="unaccounted",
                title="a requirement is not accounted for",
                said="a requirement swage cannot account for",
            ),
            FindingRecord(
                kind="computed-dependencies",
                title="upstream computed its dependencies rather than declaring them",
                said="upstream computed its dependency list",
            ),
        ),
    )

    indents = [
        len(line) - len(line.lstrip())
        for line in render_explain(record).splitlines()
        if line.strip().startswith(
            ("a requirement swage cannot", "upstream computed its dependency list")
        )
    ]

    assert len(indents) == 2
    assert indents[0] == indents[1]


def test_a_shared_remedy_is_said_once_after_the_findings() -> None:
    """`recheck`'s remedy is about the set, so it follows the list (§9.7)."""
    record = Record(
        feedstock="demo",
        outcome="needs-review",
        findings=(
            FindingRecord(
                kind="recheck",
                title="a temporary entry has not been re-checked",
                said="`numpy <2` is a temporary constraint from 1.0",
                remedy="re-check whether each still holds",
            ),
            FindingRecord(
                kind="recheck",
                title="a temporary entry has not been re-checked",
                said="`scipy <1.14` is a temporary constraint from 1.0",
                remedy="re-check whether each still holds",
            ),
        ),
    )
    lines = render_explain(record).splitlines()
    assert lines.index("        re-check whether each still holds") == (
        lines.index("        `scipy <1.14` is a temporary constraint from 1.0") + 1
    )
    assert "-- re-check" not in "\n".join(lines)


def test_a_finding_from_a_newer_swage_prints_by_its_title() -> None:
    """A kind this swage lacks still has the sentence the other one wrote."""
    record = Record(
        feedstock="demo",
        outcome="needs-review",
        findings=(
            FindingRecord(kind="brand-new", title="something new is wrong", said="so"),
        ),
    )
    assert "  something new is wrong" in render_explain(record)


def test_a_feedstock_that_stopped_explains_itself_anyway() -> None:
    """An empty plan is the least helpful possible answer to "what happened"."""
    record = Record(
        feedstock="markupsafe",
        outcome="failed",
        outputs=(OutputRecord(name="markupsafe", artifacts="per-cell"),),
        stopped=(
            "`markupsafe` chooses whether it is noarch rather than stating it\n"
            '    noarch: ${{ "python" if use_noarch }}'
        ),
    )
    rendered = render_explain(record)
    assert sections(rendered)[1:] == ["INPUTS", "STOPPED"]
    assert (
        "`markupsafe` chooses whether it is noarch rather than stating it" in rendered
    )
    # It still says what it read before stopping.
    assert "output      markupsafe  built once per python and platform" in rendered


def test_an_inexact_resolution_is_said_out_loud() -> None:
    """The failure hardest to notice by eye should not need G2 to be inferred."""
    record = Record(
        feedstock="demo",
        outcome="needs-review",
        sections=(
            SectionRecord(
                path="/requirements/run",
                section="run",
                lines=(
                    PlannedLine(
                        action="add",
                        text="db-dtypes >=1.0",
                        origin="upstream-extra",
                        source="grayskull",
                        exact=False,
                    ),
                ),
            ),
        ),
    )
    assert "grayskull (inexact)" in render_explain(record)


def test_a_verdict_with_no_findings_counts_none() -> None:
    record = Record(feedstock="demo", outcome="automerge", decision="push-label")
    lines = render_explain(record).splitlines()
    assert "FINDINGS" not in lines
    assert lines[-2:] == [
        "VERDICT  automerge",
        "  decision  push, then label `automerge`",
    ]


def test_a_v1_decision_still_reads_as_a_sentence() -> None:
    """v1 recorded the label it meant, not the action; both print as words."""
    record = Record(feedstock="demo", outcome="automerge", decision="automerge")
    assert "  decision  push, then label `automerge`" in render_explain(record)


@pytest.mark.parametrize("run", ["", "2026-08-12T03-14"])
def test_the_header_names_the_run_it_is_rendering(run: str) -> None:
    header = render_explain(RECORD, run=run).splitlines()[0]
    assert header.startswith("swage explain google-cloud-bigquery")
    assert (f"run {run}" in header) is bool(run)


def merge_check_record(verified: bool, reason: str = "") -> Record:
    return Record(
        feedstock="demo",
        outcome="ready-to-merge" if verified else "needs-review",
        decision="nothing",
        merge_check=MergeCheckRecord(
            verified=verified,
            reason=reason,
            checks=(
                CheckRecord(name="linter", state="passed"),
                CheckRecord(name="azure", state="passed" if verified else "pending"),
            ),
        ),
    )


def test_the_checks_behind_a_merge_are_rendered_for_the_merges_too() -> None:
    """The evidence for the one action nobody reviews (design-v1.md 5.2).

    A pull request swage merged unattended is exactly the one somebody will
    want to reconstruct months later, so the checks are in the record whether
    or not anything went wrong -- and `explain` prints them.
    """
    rendered = render_explain(merge_check_record(verified=True))

    assert "CI" in sections(rendered)
    assert "  pass  linter" in rendered
    assert "  pass  azure" in rendered


def test_ci_that_has_not_finished_says_which_check_and_why() -> None:
    rendered = render_explain(
        merge_check_record(verified=False, reason="CI has not finished: azure")
    )

    assert "  wait  azure" in rendered
    assert "        CI has not finished: azure" in rendered


def test_ci_sits_between_the_findings_and_the_verdict() -> None:
    """It is the last thing between the plan and a merge, and reads as such."""
    headings = sections(render_explain(merge_check_record(verified=True)))

    assert headings[-2:] == ["CI", "VERDICT  ready-to-merge"]


def test_a_record_that_never_reached_the_gates_still_says_what_happened() -> None:
    """`swage explain` on a merged pull request printed only its inputs.

    A status record carries no plan and no checks -- there was nothing left to
    decide -- so every section that normally answers "what happened" was empty
    and the report stopped after INPUTS, which answers a different question
    than the one that was asked.
    """
    rendered = render_explain(
        Record(
            feedstock="google-ads",
            outcome="merged",
            reason="merged since the run that acted on it",
            pull_request=55,
        )
    )
    assert "VERDICT  merged" in rendered
    assert "  merged since the run that acted on it" in rendered
    assert "decision" not in rendered


def test_a_stopped_feedstock_explains_itself_rather_than_naming_its_bucket() -> None:
    """STOPPED already says what happened, so VERDICT would only repeat it."""
    rendered = render_explain(
        Record(
            feedstock="markupsafe",
            outcome="failed",
            stopped="`markupsafe` chooses whether it is noarch rather than stating it",
        )
    )
    assert "STOPPED" in rendered
    assert "VERDICT" not in rendered


def test_a_record_written_before_the_file_was_carried_still_renders() -> None:
    """`declared_in` is empty in every run.json older than it."""
    record = RECORD.model_copy(
        update={"upstream": UpstreamRecord(name="demo", version="1.0", source="sdist")}
    )
    rendered = render_explain(record)
    assert "demo 1.0" in rendered
    assert "declared in" not in rendered
