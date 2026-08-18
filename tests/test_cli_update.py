"""Tests for `swage update` (DESIGN.md 5.1, 5.4, 5.5, 8).

These are the highest-stakes tests in the suite after the gates, because this
is the command that writes to other people's repositories. They are written
against **one** fake runner serving reads, `gh pr` writes and git alike, so
what they can assert is the order of the calls a feedstock provoked. That is
the property DESIGN.md 5.5 is about: push strictly before label, and a label
that did not land reported rather than swallowed.

The read half of the harness is `test_cli_scan`'s, unchanged, so a difference
between the two commands here is a real difference and not a difference in
what they were handed.
"""

from __future__ import annotations

import functools
import importlib
import shutil
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from swage.cli import ExitCode, main
from swage.cli.consider import HELD_BACK, NOT_PUSHED, NameSources
from swage.cli.update import (
    DRY_RUN_DESCRIPTIONS,
    NO_COMMENT,
    UPDATE_DESCRIPTIONS,
    run_update,
)
from swage.config import MappingLayer, load_config
from swage.forge import ForgeError, Git, GitHub
from swage.mapping import StaticPackageIndex
from swage.report import render_summary

from .conftest import CONFIG_ROOT
from .test_cli_scan import (
    PREVIOUS_SDIST,
    RECIPE,
    SHA256,
    STALE_RECIPE,
    URL,
    FakeGitHub,
    fetcher,
    pull,
)

#: What the fake's `rev-parse` answers with once a commit has been made, so a
#: test can tell the commit swage created from the one it planned against.
NEW_SHA = "1f0cafe0000000000000000000000000000000ab"

#: `swage.cli` re-exports a function called `main`, which shadows the module of
#: that name as an attribute -- so the module has to be fetched rather than
#: reached through the package.
CLI = importlib.import_module("swage.cli.main")


class FakeForge:
    """Reads, `gh pr` writes and git, through one runner that records order.

    Delegating reads to `test_cli_scan`'s fake keeps its assertion that every
    read passes `--method GET`, so a write accidentally spelled as a read
    still fails the way it does in `scan`.
    """

    def __init__(self, reads: FakeGitHub, fail: Sequence[str] = ()) -> None:
        self.reads = reads
        self.fail = tuple(fail)
        self.calls: list[list[str]] = []
        self.committed = False

    def __call__(self, argv: Sequence[str]) -> str:
        self.calls.append(list(argv))
        if any(token in argv for token in self.fail):
            raise ForgeError(f"{' '.join(argv)} failed:\nGitHub said no")
        if argv[:3] == ["gh", "repo", "clone"]:
            # A real clone leaves a working tree behind, and the recipe swage
            # is about to write goes into it. `meta.yaml` is there because the
            # clone is pinned to the commit swage read it at, so a migration
            # always finds one to delete -- a fake without it would let
            # `push_migration` pass a check no real clone imposes.
            (Path(argv[4]) / "recipe").mkdir(parents=True)
            (Path(argv[4]) / "recipe" / "meta.yaml").write_text("package:\n")
            return ""
        if argv[0] == "git":
            if "commit" in argv:
                self.committed = True
            if "rev-parse" in argv:
                return f"{NEW_SHA}\n" if self.committed else "sha7\n"
            return ""
        if argv[:2] == ["gh", "pr"]:
            return ""
        return self.reads(argv)

    def wrote(self, *tokens: str) -> list[list[str]]:
        return [call for call in self.calls if all(token in call for token in tokens)]

    @property
    def order(self) -> list[str]:
        """The write calls, as verbs, in the order swage made them."""
        verbs = []
        for call in self.calls:
            if call[:3] == ["gh", "repo", "clone"]:
                verbs.append("clone")
            elif call[0] == "git" and call[3] in {"commit", "push"}:
                verbs.append(call[3])
            elif "--remove-label" in call:
                verbs.append("unlabel")
            elif "--add-label" in call:
                verbs.append("label")
            elif call[:3] == ["gh", "pr", "comment"]:
                verbs.append("comment")
            elif call[:3] == ["gh", "pr", "merge"]:
                verbs.append("merge")
        return verbs


@pytest.fixture
def names() -> NameSources:
    return NameSources(
        StaticPackageIndex.of("requests", "pandas", "flit-core", "leftover"),
        MappingLayer("grayskull pypi mapping", {}),
    )


def tree_at(tmp_path: Path, trust: str) -> Any:
    """The shipped quirks database, with `demo` at one rung of the ladder.

    The real `config/` rather than a hand-written one: a harness more
    permissive than reality hides bugs as readily as it invents them.
    """
    root = tmp_path / f"config-{trust}"
    if root.exists():
        shutil.rmtree(root)
    shutil.copytree(CONFIG_ROOT, root)
    (root / "feedstocks" / "demo.yaml").write_text(
        f"feedstock: demo\ntrust: {trust}\n", encoding="utf-8"
    )
    return load_config(root)


def update(
    forge: FakeForge,
    tree: Any,
    names: NameSources,
    tmp_path: Path,
    execute: bool = True,
) -> Any:
    github = GitHub(run=forge)
    run = run_update(
        github,
        Git(run=forge, root=tmp_path / "clones"),
        tree,
        ["demo"],
        names,
        execute=execute,
        fetch=fetcher(previous=PREVIOUS_SDIST),
    )
    return run.feedstocks[0]


def stale(**rest: Any) -> FakeGitHub:
    """A pull request whose recipe swage would change."""
    return FakeGitHub(
        pulls=[pull()], files={"recipe/recipe.yaml": STALE_RECIPE}, **rest
    )


def test_the_label_goes_on_after_the_push_and_never_before(
    tmp_path: Path, names: NameSources
) -> None:
    """The one ordering DESIGN.md 2.2 makes mandatory.

    Labelling first guarantees conda-forge strips the label, because swage's
    commit then lands after the `labeled` event.
    """
    forge = FakeForge(stale())
    record = update(forge, tree_at(tmp_path, "auto"), names, tmp_path)

    assert forge.order == ["clone", "commit", "push", "unlabel", "label"]
    assert record.outcome == "merge-ready"
    assert record.pushed == NEW_SHA
    assert record.head == "sha7"


def test_the_recipe_that_was_pushed_is_the_one_swage_planned(
    tmp_path: Path, names: NameSources
) -> None:
    forge = FakeForge(stale())
    record = update(forge, tree_at(tmp_path, "auto"), names, tmp_path)

    written = tmp_path / "clones" / "demo-7" / "recipe" / "recipe.yaml"
    assert written.read_text(encoding="utf-8") == record.rendered_recipe
    assert record.rendered_recipe != STALE_RECIPE


def test_a_label_that_will_not_land_is_degraded_rather_than_merge_ready(
    tmp_path: Path, names: NameSources
) -> None:
    """The hazard DESIGN.md 5.5 exists for.

    swage's commit has already broken conda-forge's own path B for this pull
    request -- every commit is no longer a bot's -- so a push without its
    label leaves the pull request less automated than swage found it. That
    needs a human, and it must not be buried in a success list.
    """
    forge = FakeForge(stale(), fail=["--add-label"])
    record = update(forge, tree_at(tmp_path, "auto"), names, tmp_path)

    assert record.outcome == "degraded"
    assert record.pushed == NEW_SHA
    assert "labeling failed" in record.detail
    assert record.needs_review is True


def test_a_push_that_fails_is_not_degraded(tmp_path: Path, names: NameSources) -> None:
    """Nothing landed, so there is no automation to repair -- just no update."""
    forge = FakeForge(stale(), fail=["push"])
    record = update(forge, tree_at(tmp_path, "auto"), names, tmp_path)

    assert record.outcome == "failed"
    assert "push failed" in record.detail
    assert record.pushed == ""
    assert forge.wrote("--add-label") == []


def test_a_proposed_feedstock_is_pushed_and_explained_but_not_labeled(
    tmp_path: Path, names: NameSources
) -> None:
    """`trust: propose` is "push, never auto-label" (DESIGN.md 5.4)."""
    forge = FakeForge(stale())
    record = update(forge, tree_at(tmp_path, "propose"), names, tmp_path)

    assert forge.order == ["clone", "commit", "push", "comment"]
    assert record.outcome == "proposed"
    # The comment on the pull request says why there was no label. The report
    # line says how much changed: every feedstock in this bucket is unlabeled
    # for the same reason, which the bucket's heading already gives.
    assert "trust:" not in record.detail
    assert record.detail.endswith("in the recipe")
    body = forge.wrote("comment")[0][-1]
    assert "not approved for automatic merging (trust: propose)" in body
    assert "did **not** add the `automerge` label" in body
    # Never an identifier: this is published to a repository swage does not
    # own, and read by people who have never seen the design.
    assert not any(f"G{n}" in body for n in range(1, 12))


@pytest.mark.parametrize("trust", ["propose", "auto"])
def test_a_failing_check_is_not_pushed_at_any_rung(
    trust: str, tmp_path: Path, names: NameSources
) -> None:
    """What the rungs decide is labelling; what decides pushing is the checks.

    swage used to push here and comment naming what failed, on the argument
    that the work should not be thrown away. It puts a change swage itself
    cannot account for into somebody else's pull request, and the reasoning is
    already in the report and in `swage draft` -- which is where a maintainer
    who has to answer it is looking (DESIGN.md 5.4).
    """
    unresolvable = NameSources(
        StaticPackageIndex.of("pandas", "flit-core", "leftover"),
        MappingLayer("grayskull pypi mapping", {}),
    )
    forge = FakeForge(stale())
    record = update(forge, tree_at(tmp_path, trust), unresolvable, tmp_path)

    assert forge.order == []
    assert record.outcome == "needs-review"
    assert record.pushed == ""
    assert HELD_BACK in record.notes


def test_a_comment_that_will_not_post_does_not_change_the_verdict(
    tmp_path: Path, names: NameSources
) -> None:
    """The gates decided it; the comment only explains it."""
    forge = FakeForge(stale(), fail=["comment"])
    record = update(forge, tree_at(tmp_path, "propose"), names, tmp_path)

    assert record.outcome == "proposed"
    assert NO_COMMENT in record.notes


def test_a_feedstock_set_to_never_is_not_pushed_to(
    tmp_path: Path, names: NameSources
) -> None:
    """The one rung about the feedstock rather than about the change.

    Everything here passes its checks, so `propose` would push it. `never` is
    somebody having said not this one.
    """
    forge = FakeForge(stale())
    record = update(forge, tree_at(tmp_path, "never"), names, tmp_path)

    assert forge.order == []
    assert record.outcome == "needs-review"
    # The bucket cannot say it: NEEDS REVIEW also holds a pushed conversion.
    assert NOT_PUSHED in record.notes


def test_a_recipe_already_matching_upstream_is_not_pushed_to(
    tmp_path: Path, names: NameSources
) -> None:
    """Path B. There is no commit to make, and a label would be inert (5.2)."""
    forge = FakeForge(FakeGitHub(pulls=[pull()], files={"recipe/recipe.yaml": RECIPE}))
    record = update(forge, tree_at(tmp_path, "auto"), names, tmp_path)

    assert forge.order == []
    assert record.outcome == "awaiting-ci"


def green(**rest: Any) -> FakeGitHub:
    """A path B pull request -- nothing to change -- whose CI has passed."""
    return FakeGitHub(
        pulls=[pull()],
        files={"recipe/recipe.yaml": RECIPE},
        statuses=[
            {
                "context": "conda-forge-linter",
                "state": "success",
                "updated_at": "2026-08-12T00:00:00Z",
            }
        ],
        **rest,
    )


def test_a_green_path_b_pull_request_is_reported_and_never_written_to(
    tmp_path: Path, names: NameSources
) -> None:
    """The end of path B, and the end swage settled for (DESIGN.md 5.2).

    `--execute` on a blessed feedstock whose recipe needs no change writes
    nothing whatsoever. GitHub will not let swage merge a pull request that
    re-renders a workflow file, which is most of them, so the pull request is
    reported as ready and a person presses the button.
    """
    forge = FakeForge(green())
    record = update(forge, tree_at(tmp_path, "auto"), names, tmp_path)

    assert forge.order == []
    assert record.outcome == "ready-to-merge"
    assert record.merge_check is not None and record.merge_check.verified


def test_nothing_in_the_write_path_can_merge(
    tmp_path: Path, names: NameSources
) -> None:
    """Asserted against the calls rather than the outcome.

    swage merging is not switched off behind a flag -- there is no merge in
    it. The runner sees every `gh` call the run makes, so this is the whole
    claim rather than a sample of it.
    """
    forge = FakeForge(green())
    update(forge, tree_at(tmp_path, "auto"), names, tmp_path)

    assert not [call for call in forge.calls if "merge" in call]


@pytest.mark.parametrize("trust", ["auto", "propose", "never"])
def test_no_rung_of_the_ladder_merges_anything(
    trust: str, tmp_path: Path, names: NameSources
) -> None:
    """`auto` means push and label; it has never meant merge since GitHub
    refused the first one swage attempted.

    And every rung reads the same here, because on this path the ladder has no
    bearing: there is nothing to push, swage cannot merge whatever it says, and
    a label on a pull request whose CI has finished is inert. The report says
    what a person can do about it, which is the same sentence on all three.
    """
    forge = FakeForge(green())
    record = update(forge, tree_at(tmp_path, trust), names, tmp_path)

    assert forge.order == []
    assert record.outcome == "ready-to-merge"


@pytest.mark.parametrize(
    ("trust", "outcome"),
    [("auto", "merge-ready"), ("propose", "proposed"), ("never", "needs-review")],
)
def test_a_dry_run_writes_nothing_and_reaches_the_same_bucket(
    trust: str, outcome: str, tmp_path: Path, names: NameSources
) -> None:
    """The default, and not a rehearsal (DESIGN.md 8).

    An outcome is a statement about the gates rather than about what was
    written, so the same invocation buckets a feedstock identically either
    way -- which is what makes the dry run's report worth reading.

    Every rung of the ladder, because `propose` is where this was wrong: it
    fails G6 exactly as `never` does, so a rule reading only the verdict put
    it in NEEDS REVIEW on a dry run and PROPOSED with `--execute`.
    """
    dry = FakeForge(stale())
    wet = FakeForge(stale())
    dry_record = update(dry, tree_at(tmp_path, trust), names, tmp_path, execute=False)
    wet_record = update(wet, tree_at(tmp_path, trust), names, tmp_path)

    assert dry.order == []
    assert dry_record.outcome == wet_record.outcome == outcome
    assert dry_record.rendered_recipe == wet_record.rendered_recipe
    assert dry_record.pushed == ""


def test_the_report_says_would_where_nothing_was_written(
    tmp_path: Path, names: NameSources
) -> None:
    github = GitHub(run=FakeForge(stale()))
    run = run_update(
        github,
        Git(root=tmp_path / "clones"),
        tree_at(tmp_path, "auto"),
        ["demo"],
        names,
        execute=False,
        fetch=fetcher(previous=PREVIOUS_SDIST),
    )

    dry = render_summary(run, descriptions=DRY_RUN_DESCRIPTIONS, color=False)
    wrote = render_summary(run, descriptions=UPDATE_DESCRIPTIONS, color=False)

    assert "would push + label automerge" in dry
    assert "pushed +" not in dry
    assert "pushed + labeled automerge" in wrote


def test_a_run_that_pushed_says_so_and_names_the_commit(
    tmp_path: Path, names: NameSources
) -> None:
    """The mirror of `trust: never -- swage never pushes to this feedstock`.

    An `--execute` run reported a feedstock held for review in exactly the
    words its dry run used, while `run.json` recorded the commit just pushed to
    somebody else's pull request.
    """
    forge = FakeForge(stale())
    run = run_update(
        GitHub(run=forge),
        Git(run=forge, root=tmp_path / "clones"),
        tree_at(tmp_path, "propose"),
        ["demo"],
        names,
        execute=True,
        fetch=fetcher(previous=PREVIOUS_SDIST),
    )

    record = run.feedstocks[0]
    assert record.pushed
    assert record.notes[0] == f"pushed {record.pushed[:7]} to the pull request"


def test_a_dry_run_says_so_whatever_bucket_the_feedstock_lands_in(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    names: NameSources,
) -> None:
    """The subjunctive wording covers two outcomes; the fleet's default is not one.

    A feedstock at `trust: propose` is held for review, which is neither
    MERGE-READY nor PROPOSED, so a dry run and an `--execute` run of the same
    invocation printed the same bytes -- and nothing in the report said whether
    swage had written to somebody else's repository.

    Through `main`, because the defect was in what the command passes to the
    renderer rather than in the renderer.
    """
    forge = FakeForge(stale())
    root = tmp_path / "config"
    shutil.copytree(CONFIG_ROOT, root)
    (root / "feedstocks" / "demo.yaml").write_text(
        "feedstock: demo\ntrust: never\n", encoding="utf-8"
    )
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setattr(CLI, "GitHub", lambda: GitHub(run=forge))
    monkeypatch.setattr(CLI, "Git", lambda root: Git(run=forge, root=root))
    monkeypatch.setattr(CLI, "load_package_index", lambda: names.index)
    monkeypatch.setattr(CLI, "load_grayskull_layer", lambda: names.grayskull)
    monkeypatch.setattr(
        CLI,
        "run_update",
        functools.partial(run_update, fetch=fetcher(previous=PREVIOUS_SDIST)),
    )

    code = main(
        ["--config-root", str(root), "update", "--feedstock", "demo", "--quiet"]
    )

    assert code == ExitCode.NEEDS_REVIEW
    out = capsys.readouterr().out
    assert "NEEDS REVIEW (1)" in out
    assert "DRY RUN -- nothing was written; add --execute to push" in out
    # And the banner's claim is true: nothing reached the forge.
    assert forge.order == []


def test_the_command_pushes_labels_and_leaves_the_clone_in_the_run_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    names: NameSources,
) -> None:
    """End to end through `main`, because the wiring is where this can go wrong.

    The clone lands under the run directory rather than in a cache of its own,
    so the tree swage pushed is still on disk beside the `run.json` saying why
    -- one directory is the whole account of one write.
    """
    forge = FakeForge(stale())
    root = tmp_path / "config"
    shutil.copytree(CONFIG_ROOT, root)
    (root / "feedstocks" / "demo.yaml").write_text(
        "feedstock: demo\ntrust: auto\n", encoding="utf-8"
    )
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setattr(CLI, "GitHub", lambda: GitHub(run=forge))
    monkeypatch.setattr(CLI, "Git", lambda root: Git(run=forge, root=root))
    monkeypatch.setattr(CLI, "load_package_index", lambda: names.index)
    monkeypatch.setattr(CLI, "load_grayskull_layer", lambda: names.grayskull)
    monkeypatch.setattr(
        CLI,
        "run_update",
        functools.partial(run_update, fetch=fetcher(previous=PREVIOUS_SDIST)),
    )

    code = main(
        [
            "--config-root",
            str(root),
            "update",
            "--feedstock",
            "demo",
            "--execute",
            "--quiet",
        ]
    )

    assert code == ExitCode.OK
    assert forge.order == ["clone", "commit", "push", "unlabel", "label"]
    out = capsys.readouterr().out
    assert "MERGE-READY (1)" in out
    assert "pushed + labeled automerge" in out
    # The other half of the banner: on a run that wrote, it would be a lie.
    assert "DRY RUN" not in out
    assert "swage update --feedstock demo --execute" in out
    runs = sorted((tmp_path / "cache" / "swage" / "runs").iterdir())
    assert (runs[-1] / "clones" / "demo-7" / "recipe" / "recipe.yaml").is_file()


#: A v0 feedstock whose conversion is the same recipe `stale` serves, so what
#: these tests vary is the migration rather than the dependency plan.
META_YAML = f"""\
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
  script: {{{{ PYTHON }}}} -m pip install . --no-deps -vv

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
  license: BSD-3-Clause
  summary: demo
"""

FORGE_YML = "conda_forge_output_validation: true\n"


def v0(**rest: Any) -> FakeGitHub:
    """A bot pull request against a feedstock still on the old format."""
    return FakeGitHub(
        pulls=[pull()],
        files={"recipe/meta.yaml": META_YAML, "conda-forge.yml": FORGE_YML},
        **rest,
    )


def migrating(forge: FakeForge, tree: Any, names: NameSources, tmp_path: Path) -> Any:
    run = run_update(
        GitHub(run=forge),
        Git(run=forge, root=tmp_path / "clones"),
        tree,
        ["demo"],
        names,
        execute=True,
        fetch=fetcher(previous=PREVIOUS_SDIST),
        migrate=True,
    )
    return run.feedstocks[0]


def test_without_the_flag_a_v0_feedstock_is_only_reported(
    tmp_path: Path, names: NameSources
) -> None:
    """The default stays "tell me" (DESIGN.md 7.1).

    Converting several hundred feedstocks is not something to trip into, so
    `update` alone reports the same NEEDS MIGRATION every read-only command
    does and writes nothing.
    """
    forge = FakeForge(v0())

    record = update(forge, tree_at(tmp_path, "propose"), names, tmp_path)

    assert record.outcome == "needs-migration"
    assert forge.order == []


def test_with_the_flag_the_conversion_and_the_update_are_two_commits(
    tmp_path: Path, names: NameSources
) -> None:
    """The shape DESIGN.md 7.1 asks for, reached through the whole command."""
    forge = FakeForge(v0())

    record = migrating(forge, tree_at(tmp_path, "propose"), names, tmp_path)

    assert forge.order == ["clone", "commit", "commit", "push", "comment"]
    assert record.pushed == NEW_SHA


def test_the_conversion_commit_comes_before_the_dependency_commit(
    tmp_path: Path, names: NameSources
) -> None:
    """Order is what makes the second commit reviewable."""
    forge = FakeForge(v0())

    migrating(forge, tree_at(tmp_path, "propose"), names, tmp_path)

    messages = [call[-1] for call in forge.calls if "commit" in call]
    assert messages[0].startswith("Convert the recipe to the new format")
    assert messages[1].startswith("Reconcile recipe dependencies")


def test_a_migration_is_never_labeled_even_at_trust_auto(
    tmp_path: Path, names: NameSources
) -> None:
    """The ceiling (DESIGN.md 7): a migration proposes, whatever the gates say.

    `demo` at `trust: auto` with a clean plan is the case that would otherwise
    be labeled and merged unattended, which is exactly what a converted recipe
    must never be.
    """
    forge = FakeForge(v0())

    record = migrating(forge, tree_at(tmp_path, "auto"), names, tmp_path)

    assert "label" not in forge.order
    assert "merge" not in forge.order
    assert "comment" in forge.order
    assert record.outcome == "needs-review"


def test_a_feedstock_set_to_never_is_not_converted_either(
    tmp_path: Path, names: NameSources
) -> None:
    """`trust: never` is the maintainer saying "not this feedstock".

    A conversion does not override it: DESIGN.md 7's ceiling caps what a
    migration may do rather than licensing it to write where it was told not
    to.
    """
    forge = FakeForge(v0())

    record = migrating(forge, tree_at(tmp_path, "never"), names, tmp_path)

    assert forge.order == []
    assert record.pushed == ""
    assert NOT_PUSHED in record.notes


def test_a_no_change_pull_request_is_ready_at_any_rung(
    tmp_path: Path, names: NameSources
) -> None:
    """The ladder decides labelling, and there is nothing here to label.

    swage cannot merge a no-change pull request on any rung (DESIGN.md 5.2.2)
    and a label on one whose CI has finished is inert (DESIGN.md 2.1), so what
    a reader does about it is the same sentence whatever the feedstock is set
    to. Reading the ladder here put a feedstock with nothing to change in the
    bucket that means a decision is needed, over a decision with no bearing on
    it.
    """
    forge = FakeForge(green())
    record = update(forge, tree_at(tmp_path, "propose"), names, tmp_path)

    assert record.outcome == "ready-to-merge"
    assert record.merge_check is not None
    assert record.merge_check.verified


def test_a_no_change_pull_request_names_the_ci_that_held_it(
    tmp_path: Path, names: NameSources
) -> None:
    """Not the rung, which is what the report used to print beside it."""
    forge = FakeForge(
        FakeGitHub(
            pulls=[pull()],
            files={"recipe/recipe.yaml": RECIPE},
            statuses=[
                {
                    "context": "conda-forge-linter",
                    "state": "failure",
                    "updated_at": "2026-08-12T00:00:00Z",
                }
            ],
        )
    )
    record = update(forge, tree_at(tmp_path, "propose"), names, tmp_path)

    assert record.outcome == "needs-review"
    assert "CI failed" in record.detail
    assert "trust" not in record.detail
