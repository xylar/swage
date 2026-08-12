"""Tests for `swage explain` (DESIGN.md 9.2).

Built against the example in DESIGN.md 9.2, and against the rules stated
beneath it -- the action is the first token, every source is a file path or a
named layer, gates and verdict come last, and a feedstock that stopped before
planning still explains itself.
"""

from __future__ import annotations

import pytest

from swage.report import (
    FeedstockRecord,
    GateRecord,
    PlannedLine,
    SectionRecord,
    UpstreamRecord,
    render_explain,
)

RECORD = FeedstockRecord(
    feedstock="google-cloud-bigquery",
    outcome="needs-review",
    label="swage:needs-review",
    recipe="v1, 2 outputs, 4 requirements blocks",
    pull_request=187,
    head="4a2f1c8",
    upstream=UpstreamRecord(
        name="google-cloud-bigquery",
        version="3.44.0",
        source="sdist METADATA",
        previous="3.43.0",
    ),
    python_min="3.9",
    python_min_source=".ci_support/linux_64_.yaml",
    config_layers=(
        "config/feedstocks/google-cloud-bigquery.yaml",
        "config/families/google-cloud.yaml",
        "config/defaults.yaml",
    ),
    sections=(
        SectionRecord(
            path="/outputs/1/requirements/run",
            section="run",
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
    gates=(
        GateRecord(name="G1", passed=True),
        GateRecord(name="G3", passed=None),
        GateRecord(name="G8", passed=False, detail="drops grpcio-status (3.3.8)"),
        GateRecord(
            name="G9", passed=False, detail="run_constrained `protobuf` (3.3.9)"
        ),
    ),
)


def sections(rendered: str) -> list[str]:
    return [line for line in rendered.splitlines() if line and not line.startswith(" ")]


def test_the_four_sections_come_in_the_order_the_questions_are_asked() -> None:
    """Gates and verdict last: "why did this not merge" is why someone ran it."""
    headings = sections(render_explain(RECORD))
    assert headings[0].startswith("swage explain google-cloud-bigquery")
    assert headings[1:] == [
        "INPUTS",
        "PLAN  /outputs/1/requirements/run",
        "GATES",
        "VERDICT  swage:needs-review   (G8, G9)",
    ]


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
    assert "sdist METADATA" in rendered
    # The previous version is what classifies a removal (DESIGN.md 3.3.7), so
    # a report that omits it cannot explain a drop.
    assert "previous 3.43.0" in rendered
    assert "3.9" in rendered
    assert ".ci_support/linux_64_.yaml" in rendered


def test_config_layers_are_listed_most_specific_first() -> None:
    rendered = render_explain(RECORD).splitlines()
    layers = [line.split()[-1] for line in rendered if "config/" in line]
    assert layers == [
        "config/feedstocks/google-cloud-bigquery.yaml",
        "config/families/google-cloud.yaml",
        "config/defaults.yaml",
    ]


def test_gates_that_settled_share_a_line_and_failures_get_their_own() -> None:
    """A failure with nothing beside it is a failure nobody can act on."""
    rendered = render_explain(RECORD).splitlines()
    assert "  G1 pass   G3 n/a" in rendered
    assert "  G8 FAIL   drops grpcio-status (3.3.8)" in rendered
    assert "  G9 FAIL   run_constrained `protobuf` (3.3.9)" in rendered


def test_every_failure_starts_its_reason_in_the_same_column() -> None:
    """`G10 FAIL` is a character wider than `G1 FAIL`, and was printed as such.

    The offset is inherited by `textwrap` for every wrapped continuation line,
    so the gate whose reason runs longest was the one aligned with nothing.
    """
    record = FeedstockRecord(
        feedstock="demo",
        outcome="needs-review",
        gates=(
            GateRecord(
                name="G1",
                passed=False,
                detail="a requirement swage cannot account for",
            ),
            GateRecord(
                name="G10",
                passed=False,
                detail="upstream computed its dependency list",
            ),
        ),
    )

    failures = [line for line in render_explain(record).splitlines() if "FAIL" in line]
    reasons = [
        line.index("a requirement") for line in failures if "a requirement" in line
    ]
    reasons += [
        line.index("upstream computed") for line in failures if "upstream" in line
    ]

    assert len(reasons) == 2
    assert reasons[0] == reasons[1]


def test_a_gate_that_did_not_apply_is_not_a_gate_that_passed() -> None:
    """Not asked and asked-and-satisfied are different claims (DESIGN.md 5.4)."""
    record = FeedstockRecord(
        feedstock="demo",
        outcome="merge-ready",
        gates=(GateRecord(name="G3", passed=None), GateRecord(name="G4", passed=True)),
    )
    assert "  G3 n/a   G4 pass" in render_explain(record)


def test_a_feedstock_that_stopped_explains_itself_anyway() -> None:
    """An empty plan is the least helpful possible answer to "what happened"."""
    record = FeedstockRecord(
        feedstock="markupsafe",
        outcome="failed",
        recipe="v1, 1 output",
        stopped=(
            "unsupported build-variant switch: use_noarch\n"
            "  recipe/conda_build_config.yaml defines use_noarch and the recipe "
            "uses it"
        ),
    )
    rendered = render_explain(record)
    assert sections(rendered)[1:] == ["INPUTS", "STOPPED"]
    assert "unsupported build-variant switch: use_noarch" in rendered
    # It still says what it read before stopping.
    assert "v1, 1 output" in rendered


def test_an_inexact_resolution_is_said_out_loud() -> None:
    """The failure hardest to notice by eye should not need G2 to be inferred."""
    record = FeedstockRecord(
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


def test_a_verdict_with_no_failures_names_no_gates() -> None:
    record = FeedstockRecord(
        feedstock="demo",
        outcome="merge-ready",
        label="automerge",
        gates=(GateRecord(name="G1", passed=True),),
    )
    assert render_explain(record).splitlines()[-1] == "VERDICT  automerge"


@pytest.mark.parametrize("run", ["", "2026-08-12T03-14"])
def test_the_header_names_the_run_it_is_rendering(run: str) -> None:
    header = render_explain(RECORD, run=run).splitlines()[0]
    assert header.startswith("swage explain google-cloud-bigquery")
    assert (f"run {run}" in header) is bool(run)
