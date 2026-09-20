"""The decision (DESIGN.md §9.8): what swage does, and which bucket that is.

A pure function of the findings, the fact `unchanged`, the trust rung and CI,
and the order of its rules is the precedence. The tests of the rung's two
sentences were v1's G6 tests; the rung stopped being a check and became a
parameter here, and the sentences are unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from swage.config import ConfigTree, FeedstockConfig, load_config
from swage.plan import Finding, decide, rung_sentence

from .conftest import WriteTree

HOLDS = Finding("unaccounted", "leftpad", "the `run` requirements", "no source", "drop")
WITHHOLDS = Finding(
    "unresolved-name", "leftpad", "the `run` requirements", "no package found"
)


@dataclass(frozen=True)
class _Ci:
    """`forge.checks.CiStatus`, as the decision sees it."""

    reason: str = ""
    pending: bool = False

    @property
    def verified(self) -> bool:
        return not self.reason


def _tree(write_tree: WriteTree, feedstock: str = "") -> ConfigTree:
    files = {"defaults.yaml": "trust: never\nrecipe_owned:\n  names: [python, pip]\n"}
    if feedstock:
        files["feedstocks/demo.yaml"] = feedstock
    return load_config(write_tree(files))


def _config(write_tree: WriteTree, trust: str) -> FeedstockConfig:
    return _tree(write_tree, f"feedstock: demo\ntrust: {trust}\n").for_feedstock("demo")


# --- a change to push --------------------------------------------------------


def test_a_blessed_feedstock_with_no_findings_is_pushed_and_labeled(
    write_tree: WriteTree,
) -> None:
    """The one acceptance case; everything below is a refusal."""
    decision = decide((), False, _config(write_tree, "auto"))
    assert (decision.action, decision.outcome) == ("push-label", "merge-ready")


def test_propose_pushes_and_leaves_the_label_to_a_person(write_tree: WriteTree) -> None:
    decision = decide((), False, _config(write_tree, "propose"))
    assert (decision.action, decision.outcome) == ("push", "proposed")


def test_a_holding_finding_pushes_and_asks(write_tree: WriteTree) -> None:
    """The change is complete as far as it goes; the question rides with it."""
    for trust in ("auto", "propose"):
        decision = decide((HOLDS,), False, _config(write_tree, trust))
        assert (decision.action, decision.outcome) == ("push", "needs-review")


def test_a_withholding_finding_pushes_nothing(write_tree: WriteTree) -> None:
    """A diff swage cannot vouch for is not offered (v1 §5.4)."""
    decision = decide((HOLDS, WITHHOLDS), False, _config(write_tree, "auto"))
    assert (decision.action, decision.outcome) == ("nothing", "needs-review")


def test_never_pushes_nothing_whatever_the_findings(write_tree: WriteTree) -> None:
    for findings in ((), (HOLDS,), (WITHHOLDS,)):
        decision = decide(findings, False, _config(write_tree, "never"))
        assert (decision.action, decision.outcome) == ("nothing", "needs-review")


def test_a_feedstock_with_no_config_at_all_is_never(write_tree: WriteTree) -> None:
    """New feedstocks start at the bottom rung, so silence is a refusal."""
    config = _tree(write_tree).for_feedstock("demo")
    assert decide((), False, config).action == "nothing"


# --- the two rungs' sentences -------------------------------------------------


def test_the_two_unblessed_rungs_do_not_say_the_same_thing(
    write_tree: WriteTree,
) -> None:
    """They mean opposite things about whether anything was written.

    `propose` pushed the commit and left the label; `never` wrote nothing at
    all. Saying "not approved for automatic merging" of a `never` feedstock
    answers a question nobody asked -- which is what a maintainer read off a
    writing run they had asked for by hand, and could not account for.

    Neither says it by negating a check, which is how the `propose` sentence
    used to read: what the reader did not already have is what the rung *is*.
    """
    held = decide((), False, _config(write_tree, "never"))
    pushed = _config(write_tree, "propose")

    assert "`trust` is `never`" in held.reason
    # Where to change it, since the rung is a fact about config.
    assert "config/feedstocks/demo.yaml" in held.reason
    assert "automatic merging" not in held.reason
    assert rung_sentence(pushed) == (
        "`trust` is `propose` for this feedstock, which is the setting that "
        "pushes the change and leaves the label to a person"
    )
    # The remedy names a file only swage's own repository has, so it stays out
    # of what a feedstock's pull request is told (CLAUDE.md).
    assert "config/" not in rung_sentence(pushed)
    assert rung_sentence(_config(write_tree, "auto")) == ""


def test_a_never_feedstock_with_nothing_stated_names_the_fleet_default(
    write_tree: WriteTree,
) -> None:
    config = _tree(write_tree).for_feedstock("demo")
    assert "config/defaults.yaml" in decide((), False, config).reason


def test_the_reason_is_empty_wherever_a_finding_or_the_record_speaks(
    write_tree: WriteTree,
) -> None:
    assert decide((HOLDS,), False, _config(write_tree, "auto")).reason == ""
    assert decide((), False, _config(write_tree, "propose")).reason == ""


# --- nothing to push ------------------------------------------------------------


def test_unchanged_is_decided_before_the_rung(write_tree: WriteTree) -> None:
    """swage cannot merge on any rung, so the rung changes nothing a reader does."""
    for trust in ("never", "propose", "auto"):
        decision = decide((), True, _config(write_tree, trust), _Ci())
        assert (decision.action, decision.outcome) == ("nothing", "ready-to-merge")


def test_unchanged_with_a_finding_needs_a_person(write_tree: WriteTree) -> None:
    """Held by an unanswered question, not by the text, which already matches."""
    decision = decide((HOLDS,), True, _config(write_tree, "auto"), _Ci())
    assert (decision.action, decision.outcome) == ("nothing", "needs-review")


def test_unchanged_waits_on_ci_that_has_not_finished(write_tree: WriteTree) -> None:
    config = _config(write_tree, "auto")
    assert decide((), True, config, _Ci("azure pending", pending=True)).outcome == (
        "awaiting-ci"
    )
    # Not asked is not finished either: an unverified claim is not a verified one.
    assert decide((), True, config, None).outcome == "awaiting-ci"


def test_unchanged_with_ci_failed_needs_a_person(write_tree: WriteTree) -> None:
    decision = decide((), True, _config(write_tree, "auto"), _Ci("CI failed: azure"))
    assert (decision.action, decision.outcome) == ("nothing", "needs-review")


# --- a conversion ---------------------------------------------------------------


@pytest.mark.parametrize("findings", [(), (HOLDS,), (WITHHOLDS,)])
def test_a_conversion_is_pushed_whatever_the_findings_and_reviewed_whatever_the_rung(
    write_tree: WriteTree, findings: tuple[Finding, ...]
) -> None:
    """v1 §7: its diff touches every line, so the findings do not decide it."""
    for trust in ("propose", "auto"):
        decision = decide(findings, False, _config(write_tree, trust), converted=True)
        assert (decision.action, decision.outcome) == ("push", "needs-review")


def test_a_conversion_needing_no_dependency_edit_is_still_a_change(
    write_tree: WriteTree,
) -> None:
    """`unchanged` is against the converted recipe, which exists nowhere yet."""
    decision = decide((), True, _config(write_tree, "auto"), converted=True)
    assert decision.action == "push"


def test_a_conversion_does_not_override_never(write_tree: WriteTree) -> None:
    decision = decide((), False, _config(write_tree, "never"), converted=True)
    assert decision.action == "nothing"
