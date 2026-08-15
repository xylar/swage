"""Tests for `swage audit` (DESIGN.md 8.2).

The read harness is `test_cli_scan`'s, so a difference between an audit and a
scan of the same feedstock is a real difference and not a difference in what
the two were handed. That matters more here than anywhere else, because the
claim audit makes is that it holds what a scan would hold.

Two properties carry the weight. **It reads the feedstock rather than a pull
request** -- that is the whole reason the command exists, and it is what lets
it see the 479 of 487 feedstocks a scan reports as having no open bot pull
request. And **`trust: manual` lands in its own bucket**: it is the default
that most of the fleet sits at, so collapsing it into "a decision is needed"
would bury the feedstocks where one genuinely is.
"""

from __future__ import annotations

import importlib
import json
import shutil
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from swage.cli import main
from swage.cli.audit import AUDIT_DESCRIPTIONS, readiness, run_audit
from swage.cli.consider import NameSources
from swage.config import MappingLayer, load_config
from swage.forge import GitHub, NotFound
from swage.mapping import StaticPackageIndex
from swage.plan import GateResult, Verdict
from swage.report import render_summary

from .conftest import CONFIG_ROOT
from .test_cli_scan import RECIPE, STALE_RECIPE, FakeGitHub, fetcher

CLI = importlib.import_module("swage.cli.main")


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
        if path == "repos/conda-forge/demo-feedstock":
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


def test_it_never_lists_pull_requests(tmp_path: Path, names: NameSources) -> None:
    """The subject is the feedstock, which is what lets audit see the fleet."""
    runner = AuditGitHub(files={"recipe/recipe.yaml": STALE_RECIPE})
    audit(runner, tree_at(tmp_path, "auto"), names)
    assert not [
        argv for argv in runner.argvs if any(p.endswith("/pulls") for p in argv)
    ]


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


def test_an_unblessed_feedstock_lands_in_proposed_end_to_end(
    tmp_path: Path, names: NameSources
) -> None:
    runner = AuditGitHub(files={"recipe/recipe.yaml": STALE_RECIPE})
    record = audit(runner, tree_at(tmp_path, "manual"), names)
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
