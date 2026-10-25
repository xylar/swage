"""`swage apply`: what `update` would push, committed to a local checkout
instead (DESIGN.md §12.1).

For a feedstock with no bot pull request to carry the change: a recipe whose
dependencies are wrong for the release it already builds, often one still on
the old format. The pipeline is `audit`'s, with the checkout for its subject;
the act commits `update`'s commits to whatever branch the checkout has out.
Nothing is pushed, labeled, commented or rerendered.
"""

from __future__ import annotations

from datetime import UTC, datetime

from swage.config import ConfigError, ConfigTree, FeedstockConfig
from swage.forge import (
    COMMIT_SUBJECT,
    CONVERSION_SUBJECT,
    HINT,
    SWITCH_SUBJECT,
    BotPullRequest,
    Checkout,
    Commit,
    Fetcher,
    ForgeError,
    Git,
    Inspection,
    download,
)
from swage.migrate import Migration
from swage.plan import Decision, Plan, withheld
from swage.run import Outcome, Run, record

from .pipeline import (
    Act,
    Acted,
    NameSources,
    Subject,
    config_layers,
    consider,
    failure_reason,
    held_back,
)
from .update import planned_commits

__all__ = [
    "APPLY_DESCRIPTIONS",
    "DRY_RUN_BANNER",
    "DRY_RUN_DESCRIPTIONS",
    "NEVER",
    "run_apply",
]

#: What the buckets mean for a checkout, where they differ from a pull
#: request's.
APPLY_DESCRIPTIONS = {
    "unchanged": "the recipe already matches the release it names -- nothing to commit",
    "needs-migration": "v0 meta.yaml swage could not convert -- see each line",
}

DRY_RUN_DESCRIPTIONS = {
    **APPLY_DESCRIPTIONS,
    "committed": "would be committed to the checkout -- drop --dry-run to commit",
}

DRY_RUN_BANNER = "DRY RUN -- nothing was committed; drop --dry-run to commit"

#: Said of a feedstock whose rung keeps swage out of it, on a checkout too:
#: the reasons for it are about swage editing the recipe, not about pushing.
NEVER = (
    "`trust` is `never` for this feedstock, so swage commits nothing to it -- "
    "edit the recipe by hand"
)

#: The commits made, and the ones a dry run would make.
COMMITTED = "committed to {branch}: {commits}; nothing pushed"
WOULD_COMMIT = "would commit to {branch}: {commits}"
_KINDS = {
    CONVERSION_SUBJECT: "the conversion",
    COMMIT_SUBJECT: "the dependency update",
    SWITCH_SUBJECT: f"the switch to {HINT}",
}

#: Said where swage committed something the next build needs a rerender for,
#: which `apply` leaves to whoever pushes.
RERENDER = "rerender before this merges: {why}"
NEW_FORMAT = "the recipe is in the new format"
SKIP_RAISED = "`build.skip` now leaves out a Python upstream dropped"

#: Said where the conversion is one swage read back and found wrong.
DAMAGED = (
    "the conversion is wrong in places and has to be fixed by hand before "
    "this is pushed -- its commit message says where"
)


def run_apply(
    checkout: Checkout,
    git: Git,
    tree: ConfigTree,
    names: NameSources,
    write: bool = True,
    command: str = "swage apply",
    fetch: Fetcher = download,
) -> Run:
    """Plan the feedstock ``checkout`` holds and commit the change to it,
    or say what would be committed where ``write`` is False.
    """
    started = datetime.now(UTC).isoformat(timespec="seconds")
    feedstock = checkout.feedstock
    try:
        config = tree.for_feedstock(feedstock)
    except ConfigError as exc:
        item = record(feedstock, "failed", stopped=str(exc))
    else:
        layers = config_layers(tree, feedstock, config)
        act = _committer(git, checkout) if write else _rehearsal(checkout)
        considered = consider(
            checkout, config, Subject.checkout(checkout.head), names, layers, fetch, act
        )
        # None is a pull request that is not a version update.
        assert considered is not None
        item = considered
    return Run(command=command, started=started, feedstocks=(item,))


def _committer(git: Git, checkout: Checkout) -> Act:
    """The act that commits, closed over the checkout it commits to."""

    def commit(
        config: FeedstockConfig,
        pull: BotPullRequest | None,
        plan: Plan,
        decision: Decision,
        migration: Migration | None,
        inspection: Inspection,
    ) -> Acted:
        commits, refused = _commits(config, plan, migration, inspection)
        if refused is not None or not commits:
            return refused or Acted()
        try:
            shas = git.commit(checkout.root, commits)
        except ForgeError as exc:
            # A commit that did land stays on the branch, where `git log`
            # shows it; the plan is still in the run directory.
            return Acted(
                outcome="failed",
                reason="the commit failed",
                notes=(failure_reason(exc),),
            )
        listed = ", ".join(
            f"{sha[:7]} {_kind(change)}"
            for sha, change in zip(shas, commits, strict=True)
        )
        said = COMMITTED.format(branch=checkout.branch, commits=listed)
        return _committed(plan, migration, said)

    return commit


def _rehearsal(checkout: Checkout) -> Act:
    """The act for `--dry-run`: the commits `_committer` would make, named."""

    def rehearse(
        config: FeedstockConfig,
        pull: BotPullRequest | None,
        plan: Plan,
        decision: Decision,
        migration: Migration | None,
        inspection: Inspection,
    ) -> Acted:
        commits, refused = _commits(config, plan, migration, inspection)
        if refused is not None or not commits:
            return refused or Acted()
        listed = ", ".join(_kind(change) for change in commits)
        said = WOULD_COMMIT.format(branch=checkout.branch, commits=listed)
        return _committed(plan, migration, said)

    return rehearse


def _commits(
    config: FeedstockConfig,
    plan: Plan,
    migration: Migration | None,
    inspection: Inspection,
) -> tuple[list[Commit], Acted | None]:
    """`update`'s commits for this plan, or why there are none.

    `update` writes nothing on `trust: never`, and pushes no dependency
    edit a check says may be wrong; a conversion goes ahead whatever the
    checks found (v1 §7). `apply` refuses the same things, because each is
    about the recipe swage would leave behind rather than about publishing it.
    """
    if config.trust == "never":
        return [], Acted(outcome="needs-review", reason=NEVER)
    held = withheld(plan.findings)
    if held and migration is None:
        return [], Acted(notes=(held_back(held),))
    return planned_commits(config, plan, migration, inspection), None


def _committed(plan: Plan, migration: Migration | None, said: str) -> Acted:
    """The record of commits made, or rehearsed: ``said`` names them."""
    notes = [said]
    why = []
    if migration is not None:
        why.append(NEW_FORMAT)
    if plan.raised_skips:
        why.append(SKIP_RAISED)
    if why:
        notes.append(RERENDER.format(why="; ".join(why)))
    damaged = migration is not None and bool(migration.review.damage)
    if damaged:
        notes.append(DAMAGED)
    # Where something is left to look at, the findings name the look, as
    # they do on a pull request; the commits are a note beneath them.
    outcome: Outcome = "needs-review" if plan.findings or damaged else "committed"
    return Acted(outcome=outcome, notes=tuple(notes))


def _kind(commit: Commit) -> str:
    """What one of `update`'s commits is, in a few words."""
    subject = commit.message.partition("\n")[0]
    return _KINDS.get(subject, subject)
