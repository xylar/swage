"""Tests for `swage update` (design-v1.md 5.1, 5.4, 5.5, 8).

These are the highest-stakes tests in the suite after the gates, because this
is the command that writes to other people's repositories. They are written
against **one** fake runner serving reads, `gh pr` writes and git alike, so
what they can assert is the order of the calls a feedstock provoked. That is
the property design-v1.md 5.5 is about: push strictly before label, and a label
that did not land reported rather than swallowed.

The read half of the harness is `test_cli_scan`'s, unchanged, so a difference
between the two commands here is a real difference and not a difference in
what they were handed.
"""

from __future__ import annotations

import functools
import hashlib
import json
import shutil
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from swage.cli import ExitCode, main
from swage.cli.pipeline import HELD_BACK, NOT_PUSHED, NameSources
from swage.cli.update import (
    DRY_RUN_DESCRIPTIONS,
    NO_COMMENT,
    NO_RECORD,
    RERENDER_REQUEST,
    SWAGE_URL,
    TRAILER,
    UPDATE_DESCRIPTIONS,
    Reading,
    already_read,
    migration_comment,
    refusal_comment,
    run_update,
    unread,
)
from swage.config import MappingLayer, load_config
from swage.forge import ForgeError, Git, GitHub
from swage.mapping import StaticPackageIndex
from swage.plan import Finding
from swage.run import Outcome, Run, all_runs, record, render_summary, write_run

from .conftest import CONFIG_ROOT
from .test_cli_scan import (
    PREVIOUS_SDIST,
    PYPROJECT,
    RECIPE,
    RUN_MATCHING,
    RUN_STALE,
    SHA256,
    STALE_RECIPE,
    URL,
    FakeGitHub,
    fetcher,
    pull,
    recipe_text,
    sdist,
)

#: What the fake's `rev-parse` answers with once a commit has been made, so a
#: test can tell the commit swage created from the one it planned against.
NEW_SHA = "1f0cafe0000000000000000000000000000000ab"


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
        if argv[:3] == ["gh", "pr", "comment"]:
            # Kept where the reads will find it, so a second run over the same
            # pull request sees what the first one left (DESIGN.md §3.1).
            self.reads.comments.append(argv[argv.index("--body") + 1])
            return ""
        if argv[:2] == ["gh", "pr"]:
            return ""
        return self.reads(argv)

    def wrote(self, *tokens: str) -> list[list[str]]:
        return [call for call in self.calls if all(token in call for token in tokens)]

    @property
    def comments(self) -> list[str]:
        """The body of every comment swage posted, in order."""
        return [call[call.index("--body") + 1] for call in self.wrote("comment")]

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
    write: bool = True,
    current: bytes | None = None,
) -> Any:
    github = GitHub(run=forge)
    archives: dict[str, bytes] = {"previous": PREVIOUS_SDIST}
    if current is not None:
        archives["current"] = current
    run = run_update(
        github,
        Git(run=forge, root=tmp_path / "clones"),
        tree,
        ["demo"],
        names,
        write=write,
        fetch=fetcher(**archives),
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
    """The one ordering design-v1.md 2.2 makes mandatory.

    Labeling first guarantees conda-forge strips the label, because swage's
    commit then lands after the `labeled` event.
    """
    forge = FakeForge(stale())
    record = update(forge, tree_at(tmp_path, "auto"), names, tmp_path)

    assert forge.order == ["clone", "commit", "push", "unlabel", "label", "comment"]
    assert record.outcome == "automerge"
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


def test_a_label_that_will_not_land_needs_review_rather_than_automerge(
    tmp_path: Path, names: NameSources
) -> None:
    """The hazard design-v1.md 5.5 exists for.

    swage's commit has already broken conda-forge's own path B for this pull
    request -- every commit is no longer a bot's -- so a push without its
    label leaves the pull request less automated than swage found it. That
    needs a human, and it must not be buried in a success list.
    """
    forge = FakeForge(stale(), fail=["--add-label"])
    record = update(forge, tree_at(tmp_path, "auto"), names, tmp_path)

    assert record.outcome == "needs-review"
    assert record.pushed == NEW_SHA
    assert "the label did not land" in record.reason
    assert any("labeling failed" in note for note in record.notes)
    assert record.needs_review is True


def test_a_push_that_fails_is_not_degraded(tmp_path: Path, names: NameSources) -> None:
    """Nothing landed, so there is no automation to repair -- just no update."""
    forge = FakeForge(stale(), fail=["push"])
    record = update(forge, tree_at(tmp_path, "auto"), names, tmp_path)

    assert record.outcome == "failed"
    assert "push failed" in record.reason
    assert record.pushed == ""
    assert forge.wrote("--add-label") == []


def test_a_proposed_feedstock_is_pushed_and_explained_but_not_labeled(
    tmp_path: Path, names: NameSources
) -> None:
    """`trust: propose` is "push, never auto-label" (design-v1.md 5.4)."""
    forge = FakeForge(stale())
    record = update(forge, tree_at(tmp_path, "propose"), names, tmp_path)

    assert forge.order == ["clone", "commit", "push", "comment"]
    assert record.outcome == "needs-review"
    assert record.findings == ()
    # The comment on the pull request says why there was no label. The report
    # line says how much changed: every feedstock in this bucket is unlabeled
    # for the same reason, which the bucket's heading already gives.
    assert "trust:" not in record.reason
    assert record.reason.endswith("in the recipe")
    body = forge.wrote("comment")[0][-1]
    # The rung's sentence says what swage did about the label, and it is the
    # only sentence in the lead that does.
    assert "swage is set to leave the label to a person on this feedstock" in body
    assert "not approved for automatic merging" not in body
    # Neither an identifier nor a config key: this is published to a
    # repository swage does not own, read by people who have never seen the
    # design and cannot open the file that decided this (DESIGN.md §3.1).
    assert not any(f"G{n}" in body for n in range(1, 12))
    assert "trust" not in body


#: A recipe carrying a line no upstream version declares and conda-forge does
#: publish. The line is kept -- swage never deletes one it cannot account for
#: -- so the change around it is complete and only the line itself is a
#: question. That is the shape the advisory checks are about (design-v1.md 5.4).
RUN_UNEXPLAINED = RUN_STALE + "    - conda-only >=1.0\n"


@pytest.fixture
def names_with_extra() -> NameSources:
    return NameSources(
        StaticPackageIndex.of(
            "requests", "pandas", "flit-core", "leftover", "conda-only"
        ),
        MappingLayer("grayskull pypi mapping", {}),
    )


def test_a_decision_outstanding_is_pushed_and_explained(
    tmp_path: Path, names_with_extra: NameSources
) -> None:
    """The update lands; what needs an answer is asked on the pull request.

    Holding it back meant a feedstock owing somebody an answer about one line
    never got the others updated either -- and the answer was asked for by a
    check that had nothing to say about the rest of the diff (design-v1.md 5.4).
    """
    forge = FakeForge(
        FakeGitHub(
            pulls=[pull()],
            files={
                "recipe/recipe.yaml": recipe_text("2.0.0", URL, SHA256, RUN_UNEXPLAINED)
            },
        )
    )
    record = update(forge, tree_at(tmp_path, "propose"), names_with_extra, tmp_path)

    assert forge.order == ["clone", "commit", "push", "comment"]
    assert record.pushed == NEW_SHA
    assert HELD_BACK not in record.notes
    # The bucket still says a decision is needed, because one is.
    assert record.outcome == "needs-review"
    assert forge.wrote("--add-label") == []
    body = forge.wrote("comment")[0][-1]
    assert "conda-only" in body
    # What makes the list read as questions rather than as defects.
    assert "none of them a problem with the change itself" in body


def test_a_rendering_in_question_is_still_not_pushed(
    tmp_path: Path, names_with_extra: NameSources
) -> None:
    """The two kinds of failure part company here.

    The same recipe as the test above, so the outstanding decision about
    `conda-only` is still outstanding. What is added is a second failure of the
    other kind: `requests` is a dependency upstream declares and the channel no
    longer has, so swage cannot say which package that line should name. One
    check asks a question about a sound diff and the other says the diff may be
    wrong, and only the second withholds the push.
    """
    unresolvable = NameSources(
        StaticPackageIndex.of("pandas", "flit-core", "leftover", "conda-only"),
        MappingLayer("grayskull pypi mapping", {}),
    )
    forge = FakeForge(
        FakeGitHub(
            pulls=[pull()],
            files={
                "recipe/recipe.yaml": recipe_text("2.0.0", URL, SHA256, RUN_UNEXPLAINED)
            },
        )
    )
    record = update(forge, tree_at(tmp_path, "propose"), unresolvable, tmp_path)

    assert forge.order == []
    assert record.pushed == ""
    assert HELD_BACK in record.notes


@pytest.mark.parametrize("trust", ["propose", "auto"])
def test_a_failing_check_is_not_pushed_at_any_rung(
    trust: str, tmp_path: Path, names: NameSources
) -> None:
    """What the rungs decide is labeling; what decides pushing is the checks.

    swage used to push here and comment naming what failed, on the argument
    that the work should not be thrown away. It puts a change swage itself
    cannot account for into somebody else's pull request, and the reasoning is
    already in the report and in `swage draft` -- which is where a maintainer
    who has to answer it is looking (design-v1.md 5.4).
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


def test_the_comment_gives_each_finding_its_own_bullet(tmp_path: Path) -> None:
    """What a reader of somebody else's pull request has to act on.

    Built directly rather than through a feedstock, because what is under test
    is the rendering and the fleet has no plan producing two findings of one
    kind on a feedstock that also pushes.
    """
    findings = (
        Finding("recheck", "a !=1", "", "`a !=1` is temporary -- one.", "Re-check"),
        Finding("recheck", "b !=2", "", "`b !=2` is temporary -- two.", "Re-check"),
    )
    config = tree_at(tmp_path, "propose").for_feedstock("demo")
    body = refusal_comment("demo 2.0.0", findings, config)

    # The rung is a sentence of the body, not a bullet: the list is what was
    # found, and the rung is not a finding (DESIGN.md §11.3).
    assert (
        "swage is set to leave the label to a person on this feedstock. Still "
        "outstanding, none of them a problem with the change "
        "itself:\n"
        "\n"
        "- `a !=1` is temporary -- one.\n"
        "- `b !=2` is temporary -- two.\n"
    ) in body
    # Neither the joined form nor swage's advice about its own config reaches
    # a repository swage does not own.
    assert "Re-check" not in body
    assert "; `b !=2`" not in body


def test_the_comment_ends_with_the_trailer(tmp_path: Path) -> None:
    """The reader has no other way to find out what wrote this.

    The comment arrives on somebody else's pull request under the account of
    whoever ran swage, and names a tool that account has said nothing about.
    The trailer is the one place it is linked (DESIGN.md §3.1).
    """
    config = tree_at(tmp_path, "propose").for_feedstock("demo")
    body = refusal_comment("demo 2.0.0", (), config)

    assert body.startswith("swage updated")
    assert body.endswith(TRAILER)
    assert body.count(SWAGE_URL) == 1
    # No list and no heading over an empty one: the rung is the whole reason.
    assert "outstanding" not in body
    assert "- " not in body.replace("---", "")


def test_a_comment_that_will_not_post_does_not_change_the_verdict(
    tmp_path: Path, names: NameSources
) -> None:
    """The gates decided it; the comment only explains it."""
    forge = FakeForge(stale(), fail=["comment"])
    record = update(forge, tree_at(tmp_path, "propose"), names, tmp_path)

    assert record.outcome == "needs-review"
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


def test_a_recipe_already_matching_upstream_is_labeled_and_not_pushed_to(
    tmp_path: Path, names: NameSources
) -> None:
    """There is no commit to make, and CI is still running: the label is what
    `trust: auto` grants, and what would merge is what the checks passed on.
    """
    forge = FakeForge(FakeGitHub(pulls=[pull()], files={"recipe/recipe.yaml": RECIPE}))
    record = update(forge, tree_at(tmp_path, "auto"), names, tmp_path)

    assert forge.order == ["unlabel", "label", "comment"]
    assert record.outcome == "labeled"
    assert record.pushed == ""
    assert record.notes == ()


def test_a_recipe_already_matching_upstream_is_left_alone_below_auto(
    tmp_path: Path, names: NameSources
) -> None:
    forge = FakeForge(FakeGitHub(pulls=[pull()], files={"recipe/recipe.yaml": RECIPE}))
    record = update(forge, tree_at(tmp_path, "propose"), names, tmp_path)

    # The comment is the only thing swage leaves: the label is a person's.
    assert forge.order == ["comment"]
    assert record.outcome == "awaiting-ci"


def test_a_label_that_will_not_land_with_nothing_pushed_hands_back_the_window(
    tmp_path: Path, names: NameSources
) -> None:
    """Not the hazard of design-v1.md 5.5: no commit of swage's is on the pull
    request, so the pull request is exactly as conda-forge left it and the one
    thing outstanding is the label. That is what AWAITING CI already asks for.
    """
    forge = FakeForge(
        FakeGitHub(pulls=[pull()], files={"recipe/recipe.yaml": RECIPE}),
        fail=["--add-label"],
    )
    record = update(forge, tree_at(tmp_path, "auto"), names, tmp_path)

    assert record.outcome == "awaiting-ci"
    assert record.pushed == ""
    assert any("labeling failed" in note for note in record.notes)


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
    """The end of path B, and the end swage settled for (design-v1.md 5.2).

    A writing run on a blessed feedstock whose recipe needs no change touches
    neither the branch nor the label. GitHub will not let swage merge a pull
    request that re-renders a workflow file, which is most of them, so the
    pull request is reported as ready and a person presses the button; the
    comment is what tells them the recipe was checked.
    """
    forge = FakeForge(green())
    record = update(forge, tree_at(tmp_path, "auto"), names, tmp_path)

    assert forge.order == ["comment"]
    assert record.outcome == "ready-to-merge"
    assert record.merge_check is not None and record.merge_check.verified


def test_the_comment_records_the_release_and_the_file_it_was_read_from(
    tmp_path: Path, names: NameSources
) -> None:
    """The whole of the record on a pull request with no commit on it.

    A maintainer merging on the strength of it has no diff to read, so the
    comment names what swage read rather than only what it concluded.
    """
    forge = FakeForge(green())
    record = update(forge, tree_at(tmp_path, "auto"), names, tmp_path)

    assert record.outcome == "ready-to-merge"
    body = forge.comments[0]
    assert record.upstream is not None
    assert f"against {record.upstream.name} {record.upstream.version}" in body
    assert f"as declared in its `{record.upstream.declared_in}`" in body
    assert "every requirement already matches. Nothing to change." in body
    assert "CI has passed. A maintainer needs to merge this." in body
    # Nothing was generated but the reading, and the trailer says which.
    assert "The reconciliation above was generated" in body


def test_a_second_run_does_not_say_the_same_thing_twice(
    tmp_path: Path, names: NameSources
) -> None:
    """The pull request is read again on every run, and nothing about it has
    moved, so the second comment would repeat the first word for word."""
    forge = FakeForge(green())
    for _ in range(3):
        update(forge, tree_at(tmp_path, "auto"), names, tmp_path)

    assert forge.order == ["comment"]


def test_a_pull_request_whose_situation_moved_gets_the_comment_that_now_applies(
    tmp_path: Path, names: NameSources
) -> None:
    """Comparing bodies rather than looking for swage's trailer is what keeps
    this working: the first comment is no reason to withhold a different one.
    """
    forge = FakeForge(green(comments=["swage said something else earlier."]))
    update(forge, tree_at(tmp_path, "auto"), names, tmp_path)

    assert forge.order == ["comment"]


def test_the_comment_after_a_failed_label_asks_for_the_label(
    tmp_path: Path, names: NameSources
) -> None:
    """Which sentence is true depends on whether the label landed, so it is
    chosen after the attempt rather than from the decision."""
    forge = FakeForge(
        FakeGitHub(pulls=[pull()], files={"recipe/recipe.yaml": RECIPE}),
        fail=["--add-label"],
    )
    record = update(forge, tree_at(tmp_path, "auto"), names, tmp_path)

    assert record.outcome == "awaiting-ci"
    body = forge.comments[0]
    assert "CI is still running. A maintainer merges this, or adds the label." in body
    assert "swage set the" not in body


def test_a_comment_that_will_not_post_is_noted_and_changes_no_outcome(
    tmp_path: Path, names: NameSources
) -> None:
    """Losing the record is worth saying and is not worth a person's morning:
    the pull request is exactly as swage found it."""
    forge = FakeForge(green(), fail=["comment"])
    record = update(forge, tree_at(tmp_path, "auto"), names, tmp_path)

    assert record.outcome == "ready-to-merge"
    assert NO_RECORD in record.notes


def test_a_dry_run_leaves_no_comment(tmp_path: Path, names: NameSources) -> None:
    forge = FakeForge(green())
    record = update(forge, tree_at(tmp_path, "auto"), names, tmp_path, write=False)

    assert forge.order == []
    assert record.outcome == "ready-to-merge"


def test_a_matching_recipe_held_by_a_finding_says_what_holds_it(
    tmp_path: Path, names_with_extra: NameSources
) -> None:
    """The pull request with the most to say, and until now nothing said.

    Nothing is pushed and nothing is armed, so without this comment the only
    record that swage looked is in a terminal the feedstock's other
    maintainers never see -- on the one pull request whose recipe deviates
    from upstream on purpose.
    """
    matching = recipe_text(
        "2.0.0", URL, SHA256, RUN_MATCHING + "    - conda-only >=1.0\n"
    )
    forge = FakeForge(
        FakeGitHub(pulls=[pull()], files={"recipe/recipe.yaml": matching})
    )
    record = update(forge, tree_at(tmp_path, "auto"), names_with_extra, tmp_path)

    assert record.outcome == "needs-review"
    assert record.pushed == ""
    assert forge.order == ["comment"]
    body = forge.comments[0]
    assert "every requirement already matches. Nothing to change." in body
    assert "Still outstanding, none of them a problem with this pull request:" in body
    assert "conda-only" in body
    # swage does not read CI where a finding holds it, so the comment claims
    # nothing about CI (v1 §5.1).
    assert record.merge_check is None
    assert "CI" not in body


def test_a_change_swage_will_not_push_is_not_commented_on(
    tmp_path: Path, names: NameSources
) -> None:
    """What a comment records is a reading, and there is none to record where
    the recipe and the release disagree and swage is keeping the difference to
    itself. `trust: never` and a withholding finding both land here."""
    forge = FakeForge(stale())
    record = update(forge, tree_at(tmp_path, "never"), names, tmp_path)

    assert forge.order == []
    assert record.outcome == "needs-review"
    assert NOT_PUSHED in record.notes


def test_never_writes_nothing_at_all_not_even_a_comment(
    tmp_path: Path, names: NameSources
) -> None:
    """The rung is on a feedstock whose recipe is somebody else's to maintain
    -- `gdal`, because it is more recipe than swage should be writing to --
    so a note under the maintainer's name is a write like any other.

    It is read before the recipe is, so a recipe that already matches does
    not reach the comment the other rungs get. What swage read still reaches
    whoever ran it.
    """
    forge = FakeForge(green())
    record = update(forge, tree_at(tmp_path, "never"), names, tmp_path)

    assert forge.order == []
    assert record.outcome == "ready-to-merge"
    # Read and planned all the same: the rung decides what is written, not
    # whether swage looks.
    assert record.upstream is not None and record.upstream.version
    assert record.sections


#: Upstream declares `leftover` only for whoever asks for its `speed` extra.
WITH_EXTRA = sdist(
    PYPROJECT.replace(
        'dependencies = ["requests>=2.31.0", "pandas>=2.1.0"]',
        'dependencies = ["requests>=2.31.0", "pandas>=2.1.0"]\n\n'
        "[project.optional-dependencies]\n"
        'speed = ["leftover>=1.0"]',
    )
)


def test_a_constraint_transcribed_from_an_extra_gets_its_own_comment(
    tmp_path: Path, names: NameSources
) -> None:
    """Its own comment, after whatever the run itself had to say.

    It is about the recipe rather than about this release, so it reads the
    same on every run and under every outcome; carrying it beside the change
    would make one comment about two unrelated things.
    """
    sha = hashlib.sha256(WITH_EXTRA).hexdigest()
    recipe = recipe_text("2.0.0", URL, sha, RUN_STALE)
    recipe = recipe.replace(
        "  run:\n", "  run_constraints:\n    - leftover >=1.0\n  run:\n", 1
    )
    forge = FakeForge(FakeGitHub(pulls=[pull()], files={"recipe/recipe.yaml": recipe}))
    record = update(
        forge, tree_at(tmp_path, "auto"), names, tmp_path, current=WITH_EXTRA
    )

    # The change comment and then this one, which is about the recipe.
    assert forge.order[-1] == "comment"
    body = forge.comments[-1]
    assert "entries that upstream declares only under an extra" in body
    assert "- `leftover`, under its `speed` extra" in body
    assert "https://xylar.github.io/swage/config/names/#run_constraints" in body
    # And the record carries it, for whoever is reading the report instead.
    assert any("run_constrained" in note for note in record.notes)


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

    assert "merge" not in forge.order
    assert record.outcome == "ready-to-merge"
    # Two of them record the reading; `never` writes nothing at all.
    assert forge.order == ([] if trust == "never" else ["comment"])


@pytest.mark.parametrize(
    ("trust", "outcome"),
    [("auto", "automerge"), ("propose", "needs-review"), ("never", "needs-review")],
)
def test_a_dry_run_writes_nothing_and_reaches_the_same_bucket(
    trust: str, outcome: str, tmp_path: Path, names: NameSources
) -> None:
    """The default, and not a rehearsal (design-v1.md 8).

    An outcome is a statement about the gates rather than about what was
    written, so the same invocation buckets a feedstock identically either
    way -- which is what makes the dry run's report worth reading.

    Every rung of the ladder, because `propose` is where this was wrong: it
    fails G6 exactly as `never` does, so a rule reading only the verdict put
    it in NEEDS REVIEW on a dry run and PROPOSED on a run that wrote.
    """
    dry = FakeForge(stale())
    wet = FakeForge(stale())
    dry_record = update(dry, tree_at(tmp_path, trust), names, tmp_path, write=False)
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
        write=False,
        fetch=fetcher(previous=PREVIOUS_SDIST),
    )

    dry = render_summary(run, descriptions=DRY_RUN_DESCRIPTIONS, color=False)
    wrote = render_summary(run, descriptions=UPDATE_DESCRIPTIONS, color=False)

    assert "would push + label automerge" in dry
    assert "pushed +" not in dry
    assert "pushed + labeled automerge" in wrote


def test_the_report_says_would_label_where_nothing_was_labeled(
    tmp_path: Path, names: NameSources
) -> None:
    """The one bucket a dry run reaches with no commit in it: saying "would
    push" of a feedstock swage would only label names a commit never made.
    """
    github = GitHub(
        run=FakeForge(FakeGitHub(pulls=[pull()], files={"recipe/recipe.yaml": RECIPE}))
    )
    run = run_update(
        github,
        Git(root=tmp_path / "clones"),
        tree_at(tmp_path, "auto"),
        ["demo"],
        names,
        write=False,
        fetch=fetcher(previous=PREVIOUS_SDIST),
    )

    dry = render_summary(run, descriptions=DRY_RUN_DESCRIPTIONS, color=False)
    wrote = render_summary(run, descriptions=UPDATE_DESCRIPTIONS, color=False)

    assert "would label automerge -- drop `--dry-run` to do it" in dry
    assert "push" not in dry
    assert "nothing to push; labeled automerge" in wrote


def test_a_v0_feedstock_is_pointed_at_the_flag_not_at_swage_migrate(
    tmp_path: Path, names: NameSources
) -> None:
    """`swage migrate` previews a conversion and writes nothing.

    The heading once sent a maintainer there, which is a second command that
    prints a preview and ends where the first one did. The default heading
    already names the flag, and the override that replaced it was the defect.
    """
    github = GitHub(run=FakeForge(v0()))
    run = run_update(
        github,
        Git(root=tmp_path / "clones"),
        tree_at(tmp_path, "propose"),
        ["demo"],
        names,
        write=False,
        fetch=fetcher(),
    )

    for descriptions in (DRY_RUN_DESCRIPTIONS, UPDATE_DESCRIPTIONS):
        rendered = render_summary(run, descriptions=descriptions, color=False)
        assert "NEEDS MIGRATION (1)  v0 meta.yaml -- rerun with `--migrate`" in rendered
        assert "swage migrate" not in rendered


def test_a_run_that_pushed_says_so_and_names_the_commit(
    tmp_path: Path, names: NameSources
) -> None:
    """The mirror of `trust: never -- swage never pushes to this feedstock`.

    A run that wrote reported a feedstock held for review in exactly the
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
        write=True,
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
    MERGE-READY nor PROPOSED, so a dry run and a run that wrote, of the same
    invocation, printed the same bytes -- and nothing in the report said whether
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
    monkeypatch.setattr("swage.forge.GitHub", lambda: GitHub(run=forge))
    monkeypatch.setattr("swage.forge.Git", lambda root: Git(run=forge, root=root))
    monkeypatch.setattr("swage.forge.load_package_index", lambda: names.index)
    monkeypatch.setattr("swage.forge.load_grayskull_layer", lambda: names.grayskull)
    monkeypatch.setattr(
        "swage.cli.update.run_update",
        functools.partial(run_update, fetch=fetcher(previous=PREVIOUS_SDIST)),
    )

    code = main(
        [
            "--config-root",
            str(root),
            "update",
            "--feedstock",
            "demo",
            "--dry-run",
            "--quiet",
        ]
    )

    assert code == ExitCode.NEEDS_REVIEW
    out = capsys.readouterr().out
    assert "NEEDS REVIEW (1)" in out
    assert "DRY RUN -- nothing was written; drop --dry-run to push" in out
    # And the header says which run this was, for the same reason the banner
    # does: a `run.json` read months later has nothing else to go on.
    assert "swage update --feedstock demo --dry-run" in out
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
    monkeypatch.setattr("swage.forge.GitHub", lambda: GitHub(run=forge))
    monkeypatch.setattr("swage.forge.Git", lambda root: Git(run=forge, root=root))
    monkeypatch.setattr("swage.forge.load_package_index", lambda: names.index)
    monkeypatch.setattr("swage.forge.load_grayskull_layer", lambda: names.grayskull)
    monkeypatch.setattr(
        "swage.cli.update.run_update",
        functools.partial(run_update, fetch=fetcher(previous=PREVIOUS_SDIST)),
    )

    code = main(
        ["--config-root", str(root), "update", "--feedstock", "demo", "--quiet"]
    )

    assert code == ExitCode.OK
    assert forge.order == ["clone", "commit", "push", "unlabel", "label", "comment"]
    out = capsys.readouterr().out
    assert "AUTOMERGE (1)" in out
    assert "pushed + labeled automerge" in out
    # The other half of the banner: on a run that wrote, it would be a lie.
    assert "DRY RUN" not in out
    assert "swage update --feedstock demo" in out
    runs = sorted((tmp_path / "cache" / "swage" / "runs").iterdir())
    assert (runs[-1] / "clones" / "demo-7" / "recipe" / "recipe.yaml").is_file()


def test_execute_is_an_unrecognized_argument(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`--execute` is dropped, not accepted and ignored (DESIGN.md §12.1)."""
    with pytest.raises(SystemExit):
        main(["update", "--feedstock", "demo", "--execute"])
    assert "unrecognized arguments: --execute" in capsys.readouterr().err


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
    """A bot version update against a feedstock still on the old format."""
    return FakeGitHub(
        pulls=[pull()],
        files={"recipe/meta.yaml": META_YAML, "conda-forge.yml": FORGE_YML},
        base_files={"recipe/meta.yaml": META_YAML.replace('"2.0.0"', '"1.0.0"')},
        **rest,
    )


def v0_rebuild() -> FakeGitHub:
    """A bot migration against a feedstock still on the old format: the
    version is the same on both sides."""
    return FakeGitHub(
        pulls=[pull()],
        files={"recipe/meta.yaml": META_YAML, "conda-forge.yml": FORGE_YML},
        base_files={"recipe/meta.yaml": META_YAML},
    )


def migrating(forge: FakeForge, tree: Any, names: NameSources, tmp_path: Path) -> Any:
    run = run_update(
        GitHub(run=forge),
        Git(run=forge, root=tmp_path / "clones"),
        tree,
        ["demo"],
        names,
        write=True,
        fetch=fetcher(previous=PREVIOUS_SDIST),
        migrate=True,
    )
    return run.feedstocks[0]


def test_without_the_flag_a_v0_feedstock_is_only_reported(
    tmp_path: Path, names: NameSources
) -> None:
    """The default stays "tell me" (design-v1.md 7.1).

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
    """The shape design-v1.md 7.1 asks for, reached through the whole command."""
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


def test_a_migration_gets_its_own_comment_and_asks_for_a_rerender(
    tmp_path: Path, names: NameSources
) -> None:
    """The comment says what was pushed, and ends with the rerender request.

    The first converted feedstock got the ordinary refusal comment, which
    said `recipe/recipe.yaml` had been updated to match the release and
    nothing about the file having been created by a conversion -- and the
    pull request then could not build until its maintainer worked out that
    the generated CI configuration needed regenerating and asked conda-forge
    for it by hand. The request is the last line, exactly as conda-forge
    spells it, because the webservice reads it off the comment.
    """
    forge = FakeForge(v0())

    migrating(forge, tree_at(tmp_path, "propose"), names, tmp_path)

    (comment,) = forge.wrote("gh", "pr", "comment")
    body = comment[-1]
    assert body.startswith("swage converted `recipe/meta.yaml`")
    assert "switched `conda-forge.yml` to rattler-build" in body
    assert body.endswith(f"\n\n{RERENDER_REQUEST}\n{TRAILER}")


def test_an_ordinary_update_does_not_ask_for_a_rerender(
    tmp_path: Path, names: NameSources
) -> None:
    """A rerender is a commit conda-forge pushes to the pull request.

    On a migration nothing is labeled, so that commit costs nothing. On an
    ordinary proposal it would land after swage's own commit for no reason --
    a dependency change does not touch what the CI configuration is
    generated from -- and a maintainer who then adds the label is labeling a
    pull request with an extra commit on it they did not ask for.
    """
    forge = FakeForge(stale())

    update(forge, tree_at(tmp_path, "propose"), names, tmp_path)

    (comment,) = forge.wrote("gh", "pr", "comment")
    assert RERENDER_REQUEST not in comment[-1]
    assert "converted" not in comment[-1]


def test_the_migration_comment_reads_true_whatever_the_checks_found() -> None:
    """Both sentences that depend on the run are written only when true.

    A feedstock whose `conda-forge.yml` already named rattler-build gets no
    claim that swage set it, and a clean verdict gets "nothing outstanding"
    rather than a heading over an empty list. The label sentence does not
    depend on either: a migration is capped at proposing whatever the checks
    said (design-v1.md 7), and the comment says so instead of presenting the
    findings as the reason. The rung goes unsaid, for the same reason.
    """
    finding = Finding("recheck", "a !=1", "", "`a !=1` is temporary -- one.", "")
    clean = migration_comment("demo 2.0.0", (), ())
    flagged = migration_comment("demo 2.0.0", (finding,), ("conda_build_tool",))

    assert "The checks found nothing outstanding." in clean
    assert "conda-forge.yml" not in clean
    assert "whatever the checks found:\n\n- `a !=1` is temporary" in flagged
    assert "switched `conda-forge.yml` to rattler-build" in flagged
    for body in (clean, flagged):
        assert "`trust`" not in body
        assert body.count(SWAGE_URL) == 1
        assert body.endswith(f"{RERENDER_REQUEST}\n{TRAILER}")


SCRIPTED_PYPROJECT = PYPROJECT + '\n[project.scripts]\ndemo = "demo.cli:main"\n'
SCRIPTED_SDIST = sdist(SCRIPTED_PYPROJECT)
SCRIPTED_SHA256 = hashlib.sha256(SCRIPTED_SDIST).hexdigest()
#: `RECIPE` with an entry point that upstream has since moved.
SCRIPTED_RECIPE = recipe_text("2.0.0", URL, SCRIPTED_SHA256, RUN_MATCHING).replace(
    "build:\n  noarch: python\n",
    "build:\n  noarch: python\n  python:\n    entry_points:\n"
    "      - demo = demo:main\n",
)


def test_an_entry_point_upstream_moved_is_rewritten_and_pushed(
    tmp_path: Path, names: NameSources
) -> None:
    """m2r2, through the whole command (design-v1.md 3.3.15).

    The recipe's dependencies already match, so the only edit is the entry
    point -- which is what makes this the test that the byte comparison sees
    the third kind of edit: without it, the recipe would read as unchanged
    and nothing would be pushed.
    """
    forge = FakeForge(
        FakeGitHub(pulls=[pull()], files={"recipe/recipe.yaml": SCRIPTED_RECIPE})
    )
    run = run_update(
        GitHub(run=forge),
        Git(root=tmp_path / "clones", run=forge),
        tree_at(tmp_path, "auto"),
        ["demo"],
        names,
        write=True,
        fetch=fetcher(current=SCRIPTED_SDIST, previous=PREVIOUS_SDIST),
    )
    record = run.feedstocks[0]

    assert record.outcome == "automerge"
    assert "      - demo = demo.cli:main\n" in record.rendered_recipe
    assert "demo = demo:main" not in record.rendered_recipe
    assert (
        "entry point `demo` now runs `demo.cli:main`, which is what upstream "
        "declares; it ran `demo:main`"
    ) in record.notes


def test_a_manual_entry_point_list_is_left_as_written(
    tmp_path: Path, names: NameSources
) -> None:
    """`entry_points: manual` says the list is conda-forge's own.

    The recipe then matches what swage would write, so nothing is pushed and
    nothing is said -- not even the note, since a list swage was told not to
    look at is not one it can remark on.
    """
    tree_at(tmp_path, "auto")  # the shipped config, copied; `demo` rewritten below
    (tmp_path / "config-auto" / "feedstocks" / "demo.yaml").write_text(
        "feedstock: demo\ntrust: auto\nentry_points: manual\n", encoding="utf-8"
    )
    tree = load_config(tmp_path / "config-auto")
    forge = FakeForge(
        FakeGitHub(pulls=[pull()], files={"recipe/recipe.yaml": SCRIPTED_RECIPE})
    )
    run = run_update(
        GitHub(run=forge),
        Git(root=tmp_path / "clones", run=forge),
        tree,
        ["demo"],
        names,
        write=True,
        fetch=fetcher(current=SCRIPTED_SDIST, previous=PREVIOUS_SDIST),
    )
    record = run.feedstocks[0]

    assert record.rendered_recipe == SCRIPTED_RECIPE
    assert "push" not in forge.order
    assert not any("entry point" in note for note in record.notes)


def test_a_migration_is_never_labeled_even_at_trust_auto(
    tmp_path: Path, names: NameSources
) -> None:
    """The ceiling (design-v1.md 7): a migration proposes, whatever the gates say.

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

    A conversion does not override it: design-v1.md 7's ceiling caps what a
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
    """The ladder decides labeling, and there is nothing here to label.

    swage cannot merge a no-change pull request on any rung (design-v1.md 5.2.2)
    and a label on one whose CI has finished is inert (design-v1.md 2.1), so what
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
    assert "CI failed" in record.reason
    assert "trust" not in record.reason


# --- update --all: what an earlier update already read (DESIGN.md §12.1) ------


def _ran(root: Path, stamp: str, command: str, *records: Any) -> None:
    """Write one run under ``root``, as swage would have."""
    write_run(
        Run(
            command=command, started=f"{stamp[:10]}T00:00:00+00:00", feedstocks=records
        ),
        root / "runs" / f"{stamp}T00-00-00",
    )


def test_a_reading_counts_at_the_commit_read_and_the_commit_pushed(
    tmp_path: Path,
) -> None:
    _ran(
        tmp_path,
        "2026-09-24",
        "swage update --feedstock demo",
        record("demo", "automerge", pull_request=7, head="sha7", pushed=NEW_SHA),
    )

    readings = already_read(all_runs(tmp_path))

    assert set(readings) == {("demo", 7, "sha7"), ("demo", 7, NEW_SHA)}
    assert readings["demo", 7, "sha7"] == Reading(
        "2026-09-24T00:00:00+00:00", "automerge"
    )


def test_only_a_run_that_could_have_written_counts_as_a_reading(
    tmp_path: Path,
) -> None:
    """A dry run and a scan read the pull request too, and left nothing on it:
    the comment an update would post is still unposted."""
    seen = record("demo", "ready-to-merge", pull_request=7, head="sha7")
    _ran(tmp_path, "2026-09-22", "swage update --feedstock demo --dry-run", seen)
    _ran(tmp_path, "2026-09-23", "swage scan --all", seen)
    _ran(tmp_path, "2026-09-24", "swage status --since 7d", seen)

    assert already_read(all_runs(tmp_path)) == {}


def test_a_v1_update_is_not_a_reading(tmp_path: Path) -> None:
    """Until `--dry-run` existed, `swage update` without `--execute` wrote
    nothing, and a v1 record cannot say which it was."""
    directory = tmp_path / "runs" / "2026-09-01T00-00-00"
    directory.mkdir(parents=True)
    (directory / "run.json").write_text(
        json.dumps(
            {
                "schema": 4,
                "command": "swage update --feedstock demo",
                "started": "2026-09-01T00:00:00+00:00",
                "feedstocks": [
                    {
                        "feedstock": "demo",
                        "outcome": "unchanged",
                        "pull_request": 7,
                        "head": "sha7",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    assert already_read(all_runs(tmp_path)) == {}


@pytest.mark.parametrize("outcome", ["failed", "needs-migration"])
def test_an_outcome_that_may_not_recur_is_read_again(
    tmp_path: Path, outcome: Outcome
) -> None:
    _ran(
        tmp_path,
        "2026-09-24",
        "swage update --feedstock demo",
        record("demo", outcome, pull_request=7, head="sha7"),
    )

    assert already_read(all_runs(tmp_path)) == {}


def test_a_pull_request_read_at_its_current_commit_is_left_alone(
    tmp_path: Path, names: NameSources
) -> None:
    forge = FakeForge(green())
    readings = {
        ("demo", 7, "sha7"): Reading("2026-09-24T07:21:05+00:00", "ready-to-merge")
    }
    run = run_update(
        GitHub(run=forge),
        Git(run=forge, root=tmp_path / "clones"),
        tree_at(tmp_path, "auto"),
        ["demo"],
        names,
        write=True,
        fetch=fetcher(previous=PREVIOUS_SDIST),
        skip=unread(readings),
    )

    left = run.feedstocks[0]
    assert forge.order == []
    assert left.outcome == "unchanged"
    assert left.reason == (
        "read at sha7 on 2026-09-24 (ready to merge), and nothing pushed since"
    )
    assert left.pull_request == 7
    # Not a reading, so the next run finds the one that was.
    assert left.head == ""


def test_a_pull_request_that_moved_since_it_was_read_is_read_again(
    tmp_path: Path, names: NameSources
) -> None:
    forge = FakeForge(green())
    readings = {("demo", 7, "older"): Reading("2026-09-24T07:21:05+00:00", "unchanged")}
    run = run_update(
        GitHub(run=forge),
        Git(run=forge, root=tmp_path / "clones"),
        tree_at(tmp_path, "auto"),
        ["demo"],
        names,
        write=True,
        fetch=fetcher(previous=PREVIOUS_SDIST),
        skip=unread(readings),
    )

    assert run.feedstocks[0].outcome == "ready-to-merge"
    assert forge.order == ["comment"]


def test_update_all_reads_a_pull_request_once_until_it_moves(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    names: NameSources,
) -> None:
    """The second run finds the first one's reading and writes nothing, not
    even the comment whose closing sentence has changed since."""
    forge = FakeForge(stale(teams=["demo"]))
    root = tmp_path / "config"
    shutil.copytree(CONFIG_ROOT, root)
    (root / "feedstocks" / "demo.yaml").write_text(
        "feedstock: demo\ntrust: auto\n", encoding="utf-8"
    )
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setattr("swage.forge.GitHub", lambda: GitHub(run=forge))
    monkeypatch.setattr("swage.forge.Git", lambda root: Git(run=forge, root=root))
    monkeypatch.setattr("swage.forge.load_package_index", lambda: names.index)
    monkeypatch.setattr("swage.forge.load_grayskull_layer", lambda: names.grayskull)
    monkeypatch.setattr(
        "swage.cli.update.run_update",
        functools.partial(run_update, fetch=fetcher(previous=PREVIOUS_SDIST)),
    )
    argv = ["--config-root", str(root), "update", "--all", "--quiet"]

    assert main(argv) == ExitCode.OK
    assert forge.order == ["clone", "commit", "push", "unlabel", "label", "comment"]
    capsys.readouterr()
    # The fake's pull request still points at the commit swage read, and the
    # second run needs a directory of its own.
    monkeypatch.setattr(
        "swage.run.run_directory",
        lambda: tmp_path / "cache" / "swage" / "runs" / "2099-01-01T00-00-00",
    )

    assert main(argv) == ExitCode.OK
    assert len(forge.order) == 6
    out = capsys.readouterr().out
    assert "UNCHANGED (1)" in out
    assert "nothing new since swage read it" in out
    assert "read at sha7 on " in out


def test_a_v0_version_update_is_listed_with_its_pull_request(
    tmp_path: Path, names: NameSources
) -> None:
    """The version it waits on and the address to rerun against, so the
    bucket is never a count with nothing under it."""
    forge = FakeForge(v0())
    record = update(forge, tree_at(tmp_path, "auto"), names, tmp_path)

    assert record.outcome == "needs-migration"
    assert record.reason == "version update to 2.0.0"
    rendered = render_summary(
        Run(command="swage update --all", feedstocks=(record,)), color=False
    )
    assert "demo  version update to 2.0.0" in rendered
    assert "https://github.com/conda-forge/demo-feedstock/pull/7" in rendered


def test_a_v0_migration_is_left_alone_and_never_converted(
    tmp_path: Path, names: NameSources
) -> None:
    """A conversion rides along with a version update and nothing else
    (design-v1.md 7): a rebuild on a v0 feedstock is left like any other."""
    forge = FakeForge(v0_rebuild())
    record = migrating(forge, tree_at(tmp_path, "auto"), names, tmp_path)

    assert forge.order == []
    assert record.outcome == "unchanged"
    assert record.reason == ""


def test_a_v0_feedstock_set_to_never_says_so(
    tmp_path: Path, names: NameSources
) -> None:
    forge = FakeForge(v0())
    record = update(forge, tree_at(tmp_path, "never"), names, tmp_path)

    assert record.outcome == "needs-migration"
    assert NOT_PUSHED in record.notes
    assert forge.order == []
