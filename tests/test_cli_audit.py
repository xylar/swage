"""Tests for `swage audit` (DESIGN.md 8.2).

The read harness is `test_cli_scan`'s, so a difference between an audit and a
scan of the same feedstock is a real difference and not a difference in what
the two were handed. That matters more here than anywhere else, because the
claim audit makes is that it holds what a scan would hold.

Two properties carry the weight. **It reads the feedstock rather than a pull
request** -- that is the whole reason the command exists, and it is what lets
it see the 479 of 487 feedstocks a scan reports as having no open bot pull
request. And **`trust: never` lands in its own bucket**: it is the default
that most of the fleet sits at, so collapsing it into "a decision is needed"
would bury the feedstocks where one genuinely is.
"""

from __future__ import annotations

import importlib
import json
import re
import shutil
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from swage.cli import main
from swage.cli.audit import (
    AUDIT_DESCRIPTIONS,
    _reason,
    _would_change,
    readiness,
    run_audit,
)
from swage.cli.consider import NameSources
from swage.config import MappingLayer, load_config
from swage.forge import GitHub, NotFound
from swage.mapping import StaticPackageIndex
from swage.plan import GateResult, Verdict
from swage.report import render_summary

from .conftest import CONFIG_ROOT
from .test_cli_scan import (
    GREEN,
    RECIPE,
    STALE_RECIPE,
    FakeGitHub,
    fetcher,
    pull,
)

CLI = importlib.import_module("swage.cli.main")

#: What the API sends for a pull request whose feedstock has been archived.
ARCHIVED_BASE = {"ref": "main", "repo": {"archived": True}}


class AuditGitHub(FakeGitHub):
    """`test_cli_scan`'s fake, plus the default branch an audit has to ask for.

    Deliberately answers `master`, so a test would fail if anything went back
    to assuming `main` -- which is what `compare_published.py` does and what
    this command exists partly to stop doing.
    """

    def __init__(self, branch: str = "master", **rest: Any) -> None:
        super().__init__(**rest)
        self.branch = branch

    def __call__(self, argv: Sequence[str]) -> str:
        path = next(part for part in argv if "/" in part and not part.startswith("-"))
        if re.fullmatch(r"repos/conda-forge/[^/]+-feedstock", path):
            return json.dumps({"default_branch": self.branch})
        return super().__call__(argv)

    def _contents(self, path: str, argv: Sequence[str]) -> str:
        # The base-branch special case in the scan fake keys on `main`, which
        # an audit never asks for. Here every read is at the default branch.
        wanted = path.split("/contents/", 1)[1]
        if wanted == ".ci_support":
            return json.dumps([])
        return super()._contents(path, argv)


@pytest.fixture
def names() -> NameSources:
    return NameSources(
        StaticPackageIndex.of("requests", "pandas", "flit-core", "leftover"),
        MappingLayer("grayskull pypi mapping", {}),
    )


def tree_at(tmp_path: Path, trust: str) -> Any:
    root = tmp_path / "config"
    shutil.copytree(CONFIG_ROOT, root)
    (root / "feedstocks" / "demo.yaml").write_text(
        f"feedstock: demo\ntrust: {trust}\n", encoding="utf-8"
    )
    return load_config(root)


def audit(runner: FakeGitHub, tree: Any, names: NameSources) -> Any:
    return run_audit(
        GitHub(run=runner), tree, ["demo"], names, fetch=fetcher()
    ).feedstocks[0]


def gate(name: str, passed: bool) -> GateResult:
    return GateResult(name=name, passed=passed)


# --- it reads the feedstock, not a pull request ------------------------------


def test_it_reads_the_default_branch_rather_than_main(
    tmp_path: Path, names: NameSources
) -> None:
    """A feedstock on `master` must not come back unreadable."""
    runner = AuditGitHub(branch="master", files={"recipe/recipe.yaml": STALE_RECIPE})
    record = audit(runner, tree_at(tmp_path, "auto"), names)
    assert record.outcome != "failed"
    assert record.head == "master"
    assert any("ref=master" in argv for argv in runner.argvs)


def test_a_feedstock_with_no_pull_request_at_all_is_still_planned(
    tmp_path: Path, names: NameSources
) -> None:
    """The subject is the feedstock, which is what lets audit see the fleet.

    `scan` reports 479 of 487 feedstocks as having no open bot pull request and
    never opens their recipe. This is the property that makes audit different,
    so it is pinned on a feedstock that has none whatsoever.
    """
    runner = AuditGitHub(pulls=[], files={"recipe/recipe.yaml": STALE_RECIPE})
    record = audit(runner, tree_at(tmp_path, "auto"), names)
    assert record.outcome == "merge-ready"
    assert record.sections, "it planned the recipe"


def test_every_call_an_audit_makes_is_a_read(
    tmp_path: Path, names: NameSources
) -> None:
    runner = AuditGitHub(files={"recipe/recipe.yaml": STALE_RECIPE})
    audit(runner, tree_at(tmp_path, "auto"), names)
    assert runner.argvs
    for argv in runner.argvs:
        assert argv[:4] == ["gh", "api", "--method", "GET"], argv


# --- the buckets -------------------------------------------------------------


def test_a_blessed_feedstock_whose_gates_pass_would_go_through_unattended(
    tmp_path: Path, names: NameSources
) -> None:
    runner = AuditGitHub(files={"recipe/recipe.yaml": STALE_RECIPE})
    record = audit(runner, tree_at(tmp_path, "auto"), names)
    assert record.outcome == "merge-ready"


def test_a_recipe_already_matching_upstream_is_unchanged(
    tmp_path: Path, names: NameSources
) -> None:
    """No pull request, so there is no CI to ask about and no path B to take."""
    runner = AuditGitHub(files={"recipe/recipe.yaml": RECIPE})
    record = audit(runner, tree_at(tmp_path, "auto"), names)
    assert record.outcome == "unchanged"


def test_a_v0_feedstock_is_routed_to_migration(
    tmp_path: Path, names: NameSources
) -> None:
    runner = AuditGitHub(files={"recipe/meta.yaml": "package:\n  name: demo\n"})
    record = audit(runner, tree_at(tmp_path, "auto"), names)
    assert record.outcome == "needs-migration"


def test_a_feedstock_with_no_repository_behind_it_is_not_a_failure(
    tmp_path: Path, names: NameSources
) -> None:
    """`all-members` is an org-wide team and nothing in the team object says so."""

    class Missing(AuditGitHub):
        def __call__(self, argv: Sequence[str]) -> str:
            raise NotFound("gh: Not Found (HTTP 404)")

    record = audit(Missing(), tree_at(tmp_path, "auto"), names)
    assert record.outcome == "unchanged"
    assert record.detail == "no feedstock repository"


# --- the one place it reads the gates differently ----------------------------


def test_an_unblessed_feedstock_is_not_reported_as_needing_a_decision() -> None:
    """`manual` is the default 333 of 487 feedstocks sit at (DESIGN.md 8.2).

    Collapsing it into NEEDS REVIEW would put nearly the whole fleet in the
    bucket that means "a config decision is needed" and bury the feedstocks
    where one genuinely is. Blessing it and deciding something about it are
    different work.
    """
    assert readiness(Verdict(gates=(gate("G6", False),))) == "proposed"


def test_a_gate_that_is_not_the_trust_ladder_needs_a_decision() -> None:
    assert readiness(Verdict(gates=(gate("G1", False),))) == "needs-review"
    assert readiness(Verdict(gates=(gate("G1", False), gate("G6", False)))) == (
        "needs-review"
    )


def test_all_gates_passing_is_ready() -> None:
    assert readiness(Verdict(gates=(gate("G1", True),))) == "merge-ready"


def test_nothing_to_change_and_nothing_holding_it_is_unchanged() -> None:
    """Whether it is blessed does not arise: a blessing decides what happens
    to a change, and there is no change."""
    assert readiness(Verdict(gates=(gate("G6", False),)), unchanged=True) == "unchanged"
    assert readiness(Verdict(gates=(gate("G1", True),)), unchanged=True) == "unchanged"


def test_a_held_gate_outranks_having_nothing_to_change() -> None:
    """A recipe can match its release exactly and still be held the moment the
    bot files, because what holds it is a question about the feedstock rather
    than about the current text. UNCHANGED would hide it."""
    assert readiness(Verdict(gates=(gate("G3", False),)), unchanged=True) == (
        "needs-review"
    )


def test_an_unblessed_feedstock_lands_in_proposed_end_to_end(
    tmp_path: Path, names: NameSources
) -> None:
    runner = AuditGitHub(files={"recipe/recipe.yaml": STALE_RECIPE})
    record = audit(runner, tree_at(tmp_path, "never"), names)
    assert record.outcome == "proposed"


# --- the report --------------------------------------------------------------


def test_the_report_claims_no_pull_request_and_no_push(
    tmp_path: Path, names: NameSources
) -> None:
    """Audit has no pull request in front of it and pushes nothing."""
    runner = AuditGitHub(files={"recipe/recipe.yaml": STALE_RECIPE})
    run = run_audit(
        GitHub(run=runner), tree_at(tmp_path, "auto"), ["demo"], names, fetch=fetcher()
    )
    rendered = render_summary(
        run, descriptions=AUDIT_DESCRIPTIONS, counted="audited", color=False
    )
    assert "would be pushed and labeled" in rendered
    assert "pushed + labeled automerge" not in rendered
    assert "(1 audited)" in rendered


def test_the_two_ci_buckets_have_no_wording_here() -> None:
    """Both are statements about a pull request's CI, and there is none."""
    assert "ready-to-merge" not in AUDIT_DESCRIPTIONS
    assert "awaiting-ci" not in AUDIT_DESCRIPTIONS


def test_a_bare_audit_is_refused(capsys: pytest.CaptureFixture[str]) -> None:
    """An hours-long sweep is not something to trip into by typing two words."""
    with pytest.raises(SystemExit):
        main(["audit"])
    assert "one of the arguments" in capsys.readouterr().err


def test_audit_is_no_longer_listed_as_unimplemented(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit):
        main(["--help"])
    out = capsys.readouterr().out
    assert "audit" in out
    assert "phase 5" not in out


def test_a_held_feedstock_is_named_for_what_holds_it_not_the_trust_ladder() -> None:
    """Gates run in order and the ladder sits in the middle of them.

    `google-cloud-redis` is held because swage would drop a requirement it
    cannot account for, and the first real audit printed "not approved for
    automatic merging (trust: propose)" beside it -- in a bucket whose heading
    says a decision is needed, naming the one failure audit has already decided
    is not that decision.
    """
    verdict = Verdict(
        gates=(
            GateResult(name="G6", passed=False, detail="not approved (trust: propose)"),
            GateResult(
                name="G8", passed=False, detail="would remove `google-api-core`"
            ),
        )
    )
    assert _reason(verdict) == "would remove `google-api-core`"


def test_a_bucket_whose_members_are_all_there_for_one_reason_says_the_size() -> None:
    """Repeating the heading on thirty consecutive lines says nothing."""
    assert _would_change("a\nb\nc\n", "a\nx\ny\nc\n") == "+2 -1 in the recipe"


# --- the checks that need no plan --------------------------------------------


def test_an_automerge_label_on_finished_ci_is_reported(
    tmp_path: Path, names: NameSources
) -> None:
    """It will never merge, and it looks exactly like one about to.

    conda-forge dispatches automerge from CI status events, so with CI finished
    there is no event left to dispatch on (DESIGN.md 2.1). Nothing else in
    swage reports this, because every other command is looking at a pull
    request it means to act on.
    """
    runner = AuditGitHub(
        pulls=[pull(7, labels=[{"name": "automerge"}])],
        statuses=GREEN,
        files={"recipe/recipe.yaml": STALE_RECIPE},
    )
    record = audit(runner, tree_at(tmp_path, "auto"), names)
    assert any("nothing will ever merge it" in note for note in record.notes)


def test_an_automerge_label_with_ci_still_running_is_not_reported(
    tmp_path: Path, names: NameSources
) -> None:
    """The label is doing its job -- that run's completion will dispatch it."""
    runner = AuditGitHub(
        pulls=[pull(7, labels=[{"name": "automerge"}])],
        files={"recipe/recipe.yaml": STALE_RECIPE},
    )
    record = audit(runner, tree_at(tmp_path, "auto"), names)
    assert not [note for note in record.notes if "ever merge it" in note]


def test_a_feedstock_at_the_bot_backlog_cap_is_reported(
    tmp_path: Path, names: NameSources
) -> None:
    """Four is where the bot stops filing, so no version is offered until they clear."""
    runner = AuditGitHub(
        pulls=[pull(n, created=f"2026-08-0{n}T00:00:00Z") for n in (1, 2, 3, 4)],
        files={"recipe/recipe.yaml": STALE_RECIPE},
    )
    record = audit(runner, tree_at(tmp_path, "auto"), names)
    assert any("stops filing" in note for note in record.notes)


def test_an_archived_feedstock_with_an_open_pull_request_is_reported(
    tmp_path: Path, names: NameSources
) -> None:
    """Nothing can push to it and nothing can merge into it."""
    runner = AuditGitHub(
        pulls=[pull(7, base=ARCHIVED_BASE)],
        files={"recipe/recipe.yaml": STALE_RECIPE},
    )
    record = audit(runner, tree_at(tmp_path, "auto"), names)
    assert any("archived" in note for note in record.notes)


def test_a_v0_feedstock_still_gets_its_hygiene_notes(
    tmp_path: Path, names: NameSources
) -> None:
    """These are facts about the repository, and v0 is never planned at all."""
    runner = AuditGitHub(
        pulls=[pull(7, base=ARCHIVED_BASE)],
        files={"recipe/meta.yaml": "package:\n  name: demo\n"},
    )
    record = audit(runner, tree_at(tmp_path, "auto"), names)
    assert record.outcome == "needs-migration"
    assert any("archived" in note for note in record.notes)


def test_a_config_file_for_an_unmaintained_feedstock_is_reported(
    tmp_path: Path, names: NameSources
) -> None:
    """A typo in a filename loads, validates, and is silently never applied."""
    runner = AuditGitHub(files={"recipe/recipe.yaml": STALE_RECIPE})
    run = run_audit(
        GitHub(run=runner),
        tree_at(tmp_path, "auto"),
        ["globus-cli"],
        names,
        fetch=fetcher(),
        complete=True,
    )
    orphaned = [r for r in run.feedstocks if r.feedstock == "demo"]
    assert orphaned and orphaned[0].outcome == "failed"
    assert "is ever applied" in orphaned[0].detail


def test_a_partial_sweep_reports_no_orphans(tmp_path: Path, names: NameSources) -> None:
    """Over a family every other config file is absent for the obvious reason."""
    runner = AuditGitHub(files={"recipe/recipe.yaml": STALE_RECIPE})
    run = run_audit(
        GitHub(run=runner),
        tree_at(tmp_path, "auto"),
        ["globus-cli"],
        names,
        fetch=fetcher(),
    )
    assert [r.feedstock for r in run.feedstocks] == ["globus-cli"]
