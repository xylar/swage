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
from swage.cli.audit import AUDIT_DESCRIPTIONS, readiness, run_audit
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
    SHA256,
    STALE_RECIPE,
    URL,
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

    def __init__(
        self, branch: str = "master", archived: bool = False, **rest: Any
    ) -> None:
        super().__init__(**rest)
        self.branch = branch
        self.archived = archived

    def __call__(self, argv: Sequence[str]) -> str:
        path = next(part for part in argv if "/" in part and not part.startswith("-"))
        if re.fullmatch(r"repos/conda-forge/[^/]+-feedstock", path):
            return json.dumps(
                {"default_branch": self.branch, "archived": self.archived}
            )
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


def unmaintained(tmp_path: Path, reason: str = "upstream deleted it") -> Any:
    """A config tree whose one feedstock is marked as nobody's to maintain."""
    root = tmp_path / "unmaintained"
    shutil.copytree(CONFIG_ROOT, root)
    (root / "feedstocks" / "demo.yaml").write_text(
        f"feedstock: demo\ntrust: auto\nunmaintained: {reason}\n", encoding="utf-8"
    )
    return load_config(root)


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


def test_an_archived_feedstock_is_reported_and_never_planned(
    tmp_path: Path, names: NameSources
) -> None:
    """Nothing swage does could land on a read-only repository.

    `apache-airflow-task-sdk` is why this exists rather than being left to the
    pull-request path: it has no open bot pull request, so nothing carried the
    fact, and an audit reported it PROPOSED -- a push swage would have made to
    a feedstock that refuses writes.
    """
    runner = AuditGitHub(archived=True, files={"recipe/recipe.yaml": STALE_RECIPE})
    record = audit(runner, tree_at(tmp_path, "auto"), names)
    assert record.outcome == "archived"
    assert "archived on GitHub" in (record.detail or "")


def test_an_archived_feedstock_costs_nothing_past_the_first_call(
    tmp_path: Path, names: NameSources
) -> None:
    """The recipe is never read and the archive is never fetched.

    Not an optimization: a fleet audit fetches an sdist per feedstock, and
    reading one to plan a proposal nobody can push is the whole of what this
    stops. Pinned on the reads, because `outcome == "archived"` alone would go
    on passing if the work happened first and the answer were thrown away.
    """
    runner = AuditGitHub(archived=True, files={"recipe/recipe.yaml": STALE_RECIPE})

    def refuse(url: str) -> bytes:
        raise AssertionError(f"an archived feedstock fetched {url}")

    record = run_audit(
        GitHub(run=runner), tree_at(tmp_path, "auto"), ["demo"], names, fetch=refuse
    ).feedstocks[0]
    assert record.outcome == "archived"
    assert not any("/contents/" in argv for argv in runner.argvs)


def test_a_live_feedstock_is_not_mistaken_for_an_archived_one(
    tmp_path: Path, names: NameSources
) -> None:
    """The same fixture with the one field flipped, so the test above is not
    passing on something else the fake does."""
    runner = AuditGitHub(archived=False, files={"recipe/recipe.yaml": STALE_RECIPE})
    record = audit(runner, tree_at(tmp_path, "auto"), names)
    assert record.outcome != "archived"


def test_a_feedstock_config_calls_unmaintained_is_reported_and_never_planned(
    tmp_path: Path, names: NameSources
) -> None:
    """The gap before GitHub carries the decision.

    Archiving a conda-forge feedstock is a request somebody else merges, so
    between deciding and the archiving landing the repository still accepts
    writes and looks exactly like a live one.
    """
    tree = tree_at(tmp_path, "auto")
    runner = AuditGitHub(files={"recipe/recipe.yaml": STALE_RECIPE})

    def refuse(url: str) -> bytes:
        raise AssertionError(f"an unmaintained feedstock fetched {url}")

    record = run_audit(
        GitHub(run=runner), unmaintained(tmp_path), ["demo"], names, fetch=refuse
    ).feedstocks[0]
    assert record.outcome == "unmaintained"
    assert record.detail == "upstream deleted it"
    assert not any("/contents/" in argv for argv in runner.argvs)
    # The same fixture without the entry, so the assertion above is not
    # passing on something else the fake does.
    assert audit(runner, tree, names).outcome != "unmaintained"


def test_an_unmaintained_feedstock_that_is_now_archived_says_to_drop_the_entry(
    tmp_path: Path, names: NameSources
) -> None:
    """GitHub's answer wins, and the config entry has done its job.

    Saying so is what keeps the list from rotting: without it every entry
    survives its own reason, and the file that records "this is not
    maintained" quietly becomes a second copy of something GitHub already
    carries.
    """
    runner = AuditGitHub(archived=True, files={"recipe/recipe.yaml": STALE_RECIPE})
    record = run_audit(
        GitHub(run=runner), unmaintained(tmp_path), ["demo"], names, fetch=fetcher()
    ).feedstocks[0]
    assert record.outcome == "archived"
    assert any("can be dropped" in note for note in record.notes)


def test_an_archived_feedstock_with_no_entry_is_not_told_to_drop_one(
    tmp_path: Path, names: NameSources
) -> None:
    runner = AuditGitHub(archived=True, files={"recipe/recipe.yaml": STALE_RECIPE})
    record = audit(runner, tree_at(tmp_path, "auto"), names)
    assert record.outcome == "archived"
    assert not any("can be dropped" in note for note in record.notes)


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


#: The v0 spelling of `STALE_RECIPE`, so that auditing one and auditing the
#: other are the same question asked of the same feedstock.
#:
#: `python_min` is a `{% set %}` here because the fake serves no `.ci_support`
#: and a v0 recipe otherwise takes its floor from the build variant. Three of
#: the fleet's 148 write it exactly this way; the rest are covered by the
#: sweep over real feedstocks rather than by this fixture.
V0_RECIPE = f"""\
{{% set version = "2.0.0" %}}
{{% set python_min = "3.10" %}}

package:
  name: demo
  version: {{{{ version }}}}

source:
  url: {URL}
  sha256: {SHA256}

build:
  noarch: python
  number: 0
  script: {{{{ PYTHON }}}} -m pip install . -vv

requirements:
  host:
    - python {{{{ python_min }}}}
    - pip
    - flit-core ==3.12.0
  run:
    - python >={{{{ python_min }}}}
    - requests >=2.30.0
    - pandas >=2.1.0

about:
  home: https://example.invalid
  summary: demo
  license: MIT
  license_file: LICENSE
"""


def test_a_v0_feedstock_is_converted_and_then_planned_against(
    tmp_path: Path, names: NameSources
) -> None:
    """Two jobs, not one, and the audit now asks about both.

    The recipe is `STALE_RECIPE` written the v0 way, so the dependency half
    has something real to say: `requests >=2.30.0` against upstream's
    `>=2.31.0`. Reporting only "this is v0" left that unasked.
    """
    runner = AuditGitHub(files={"recipe/meta.yaml": V0_RECIPE})
    record = audit(runner, tree_at(tmp_path, "auto"), names)

    assert record.outcome == "needs-migration"
    assert "requests >=2.31.0" in record.rendered_recipe
    # What the second of the two commits would change, which is the half a
    # whole-file conversion diff hides.
    assert record.detail == "+1 -1 in the recipe"
    # And no note saying this is v0: the bucket it lands in already does.
    assert not any("old recipe format" in note for note in record.notes)


def test_a_conversion_that_is_refused_says_why_rather_than_only_that_it_is_v0(
    tmp_path: Path, names: NameSources
) -> None:
    """The first of the two steps blocked, which six of the fleet's 148 are."""
    refused = V0_RECIPE.replace(
        "  script: {{ PYTHON }} -m pip install . -vv",
        "  script: build.sh  # [unix]\n  script: build.bat  # [win]",
        1,
    )
    runner = AuditGitHub(files={"recipe/meta.yaml": refused})
    record = audit(runner, tree_at(tmp_path, "auto"), names)

    assert record.outcome == "needs-migration"
    assert "one key twice under different selectors" in record.detail


def test_a_conversion_whose_plan_is_blocked_reports_the_plan(
    tmp_path: Path, names: NameSources
) -> None:
    """The second step blocked, which only converting first can find.

    A name nothing resolves is the ordinary way a plan is held, and on a v0
    feedstock it was invisible: the audit stopped at the file name.
    """
    unresolvable = V0_RECIPE.replace(
        "    - requests >=2.30.0", "    - requests >=2.30.0\n    - nowhere-at-all", 1
    )
    runner = AuditGitHub(files={"recipe/meta.yaml": unresolvable})
    record = audit(runner, tree_at(tmp_path, "auto"), names)

    assert record.outcome == "needs-review"
    assert any("old recipe format" in note for note in record.notes)


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


def test_a_feedstock_that_packages_no_distribution_is_not_a_failure(
    tmp_path: Path, names: NameSources
) -> None:
    """Nothing has gone wrong, and reporting it as a failure would say it had.

    `e3sm-tools` installs Fortran binaries and two scripts. Its source archive
    carries `pyscream`'s metadata, so before this outcome existed swage read
    that, planned confidently against the wrong project, and proposed `mpi4py`
    for a recipe with no use for it.
    """
    root = tmp_path / "config"
    shutil.copytree(CONFIG_ROOT, root)
    (root / "feedstocks" / "demo.yaml").write_text(
        "feedstock: demo\nupstream:\n  source: none\n"
        "  reason: Fortran binaries and two scripts, whose deps are their imports\n",
        encoding="utf-8",
    )
    runner = AuditGitHub(files={"recipe/recipe.yaml": STALE_RECIPE})

    record = audit(runner, load_config(root), names)

    assert record.outcome == "not-reconciled"
    assert "demo packages no python distribution" in record.detail
    assert "their imports" in record.detail
    assert not record.sections, "it planned nothing"


def test_a_declaration_that_could_not_be_checked_says_so(
    tmp_path: Path, names: NameSources
) -> None:
    """A checked pointer and an unchecked one must not read alike.

    `r-proj4` builds from a list of CRAN mirrors written against a variable
    conda-build supplies, so there is no URL to fetch and no archive to look
    the paths up in. Naming the files is still worth more than failing, and
    the note is what keeps that from claiming they were verified.
    """
    root = tmp_path / "config"
    shutil.copytree(CONFIG_ROOT, root)
    (root / "feedstocks" / "demo.yaml").write_text(
        "feedstock: demo\nupstream:\n  source: manual\n"
        "  declares:\n    - DESCRIPTION\n"
        "  reason: DESCRIPTION states the system library as an English sentence\n",
        encoding="utf-8",
    )
    unfetchable = STALE_RECIPE.replace(
        f"  url: {URL}\n", "  url:\n    - ${{ cran_mirror }}/demo_2.0.0.tar.gz\n"
    )
    runner = AuditGitHub(files={"recipe/recipe.yaml": unfetchable})

    record = audit(runner, load_config(root), names)

    assert record.outcome == "not-read"
    assert record.upstream is not None
    assert record.upstream.declared_in == "DESCRIPTION"
    assert any("DESCRIPTION could not be checked" in note for note in record.notes)


def test_a_declaration_read_out_of_the_archive_carries_no_such_note(
    tmp_path: Path, names: NameSources
) -> None:
    """The mutation the note is worth having: the ordinary case stays quiet."""
    root = tmp_path / "config"
    shutil.copytree(CONFIG_ROOT, root)
    (root / "feedstocks" / "demo.yaml").write_text(
        "feedstock: demo\nupstream:\n  source: manual\n"
        "  declares:\n    - pyproject.toml\n"
        "  reason: demo declares through an m4 macro of its own\n",
        encoding="utf-8",
    )
    runner = AuditGitHub(files={"recipe/recipe.yaml": STALE_RECIPE})

    record = audit(runner, load_config(root), names)

    assert record.outcome == "not-read"
    assert record.upstream is not None
    assert record.upstream.declared_in == "pyproject.toml"
    assert not [note for note in record.notes if "could not be checked" in note]


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
    """Facts about the repository, gathered whatever the two steps come to."""
    runner = AuditGitHub(
        pulls=[pull(7, base=ARCHIVED_BASE)],
        files={"recipe/meta.yaml": V0_RECIPE},
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
