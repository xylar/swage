"""`swage update` -- `scan` plus writes (DESIGN.md 8, 5.1, 5.2, 5.5).

Everything up to the verdict is `consider`'s and is shared with `scan`, so what
lives here is only what happens *after* the gates have spoken. There are five
answers and each one is a rule from DESIGN.md rather than a preference:

**Path B pushes nothing and merges instead.** The recipe already matches
upstream, so there is no commit to make -- and with no commit there is no CI
run, so nothing will ever dispatch conda-forge's automerge for that pull
request and it would sit open forever (DESIGN.md 2.1). swage merging it is the
only thing that closes it, so on this path alone swage merges, pinned to the
commit whose CI it checked, and comments afterwards to say why (DESIGN.md 5.2).
Until CI is finished and green there is nothing to do and the pull request
waits.

**`trust: manual` pushes nothing either.** A gate failure does not stop a push
(DESIGN.md 5.4) but the bottom of the trust ladder does, and it is the state
every feedstock starts in. That is the one case where swage has a change ready
and deliberately does not make it, so the record says so out loud rather than
leaving a reader to infer it from a gate name.

**Push, then label, as one unit.** Labelling first guarantees the label is
stripped; pushing without labelling leaves a `[bot-automerge]` pull request
*less* automated than swage found it, because swage's commit is not a bot's
and breaks conda-forge's all-commits-from-a-bot test forever (DESIGN.md 2.2).
So the label goes on as the very next call after a successful push, and a
label that will not land after retries is DEGRADED -- reported at the top of
the run rather than buried in a success list, because it is the one state that
needs a human to repair automation swage removed.

**A failing gate still pushes, and explains itself on the pull request.** The
work is not thrown away. What swage does not do is arm automerge, and the
comment says which gates stopped it -- there is no `swage:needs-review` label
on any feedstock and swage creates none (DESIGN.md 5.4).

**Dry run is the default and it is not a rehearsal.** With no `--execute` this
command is exactly `scan` with different wording, down to reaching the same
outcome for every feedstock, so what the report says it would do is what the
same invocation with `--execute` does.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from swage.config import ConfigTree, FeedstockConfig
from swage.forge import (
    BotPullRequest,
    CiStatus,
    Fetcher,
    ForgeError,
    Git,
    GitHub,
    arm_automerge,
    commit_message,
    download,
    merge_pull,
    upstream_location,
)
from swage.plan import Verdict
from swage.report import RunRecord
from swage.upstream import UpstreamMetadata

from .consider import (
    Act,
    Acted,
    NameSources,
    PlannedRecipe,
    consider_feedstock,
    do_nothing,
    failure_reason,
)

__all__ = [
    "DRY_RUN_DESCRIPTIONS",
    "UPDATE_DESCRIPTIONS",
    "merge_comment",
    "refusal_comment",
    "run_update",
]

#: What the buckets mean for a run that wrote (DESIGN.md 9). The defaults are
#: already `update`'s -- "pushed + labeled automerge" is what MERGE-READY means
#: -- so only the two buckets naming a command that does not exist yet, or an
#: action this command took rather than declined, are said differently.
UPDATE_DESCRIPTIONS = {
    "awaiting-ci": "no changes needed; CI has not finished -- swage checks again",
    "needs-migration": "v0 meta.yaml -- `swage migrate` converts it",
}

#: And for a run that did not write. Same outcomes, subjunctive sentences: an
#: outcome is a statement about the gates rather than about what was written,
#: so a dry run and an `--execute` run of the same invocation put every
#: feedstock in the same bucket (DESIGN.md 8).
DRY_RUN_DESCRIPTIONS = {
    "merge-ready": "would push + label automerge -- `--execute` to do it",
    "would-merge": "no changes needed and CI is green -- `--execute` merges these",
    "proposed": "would push; needs your review before labeling",
    "awaiting-ci": "no changes needed; CI has not finished -- swage checks again",
    "needs-migration": "v0 meta.yaml -- `swage migrate` converts it",
}

#: Said where the push landed and the explanation did not. The verdict is
#: unaffected -- the gates decided it and the report still names them -- but
#: the pull request itself is now carrying a swage commit with nothing on it
#: saying why, so somebody should know.
NO_COMMENT = "pushed, but the comment explaining the verdict could not be left"

#: And where the merge landed and its explanation did not. Worth saying for
#: the same reason and more loudly: this is the action nobody reviewed.
NO_MERGE_COMMENT = "merged, but the comment explaining why could not be left"


def refusal_comment(release: str, verdict: Verdict) -> str:
    """What swage says on a pull request it pushed to and would not arm.

    It names the reasons rather than only the fact, which is the whole reason
    DESIGN.md 5.4 settled on a comment: there is no `swage:needs-review` label
    on any conda-forge feedstock, and creating one in every feedstock swage
    ever flags would leave several hundred repositories permanently marked
    because a tool ran once.

    **This is the surface where design shorthand is least forgivable.** It is
    published to a repository swage does not own, read by whoever is looking at
    that pull request, and permanent. The first one swage ever posted said
    ``- **G6**: trust is 'propose', not 'auto'``, which is unreadable without
    a document that reader has never seen and would not know to look for. So
    every reason here is a sentence, and the only names it uses are things
    that exist outside swage: the `automerge` label, and the `trust` setting
    a maintainer would find in the config if they went looking.

    **It closes by saying what to do, not how conda-forge works.** It used to
    explain that a label is stripped by any commit landing after it, which is
    true, load-bearing for swage, and of no use to the person reading: they
    are not watching the pull request in the minutes between a push and the
    end of CI, and by the time they read this the mechanism has already had
    its effect. Two courses of action are open to them and the comment names
    both.
    """
    reasons = "\n".join(f"- {gate.detail or gate.title}" for gate in verdict.failures)
    return (
        f"swage updated `recipe/recipe.yaml` to match {release} and pushed the "
        "result. It did **not** add the `automerge` label, because:\n"
        "\n"
        f"{reasons}\n"
        "\n"
        "Nothing will merge this pull request on its own: a maintainer has "
        "to merge it, or add the `automerge` label.\n"
    )


def merge_comment(release: str, ci: CiStatus) -> str:
    """What swage says on a pull request it has just merged (DESIGN.md 5.2).

    The audit trail for the one action nobody reviewed, so it answers the
    three questions somebody finding it will have, in the order they will have
    them: what was merged, what was checked first, and why a person was not
    the one to press the button.

    **Written for a maintainer who has never heard of swage.** It names the
    checks rather than counting them, and it explains the automerge situation
    in terms of the label and the CI run rather than in swage's vocabulary --
    the reader is looking at a pull request on their own feedstock, not at a
    design document.
    """
    checks = "\n".join(f"- `{check.name}`: {check.word}" for check in ci.required)
    return (
        f"swage merged this pull request. `recipe/recipe.yaml` already matched "
        f"{release}, so there was nothing to change, and every check "
        "conda-forge requires here had finished and passed:\n"
        "\n"
        f"{checks}\n"
        "\n"
        "Because no commit was pushed, no new CI run would have started -- and "
        "conda-forge dispatches its automerge job from CI events, so an "
        "`automerge` label on this pull request would never have been acted "
        "on. Merging it was the only thing that would ever have closed it.\n"
    )


def run_update(
    github: GitHub,
    git: Git,
    tree: ConfigTree,
    feedstocks: Sequence[str],
    names: NameSources,
    execute: bool = False,
    command: str = "swage update",
    fetch: Fetcher = download,
    progress: Callable[[str], None] | None = None,
) -> RunRecord:
    """Update every feedstock in ``feedstocks``, writing only if ``execute``."""
    started = datetime.now(UTC).isoformat(timespec="seconds")
    act = _writer(github, git) if execute else do_nothing
    records = []
    for feedstock in feedstocks:
        if progress is not None:
            progress(feedstock)
        records.append(consider_feedstock(github, tree, feedstock, names, fetch, act))
    return RunRecord(command=command, started=started, feedstocks=tuple(records))


def _writer(github: GitHub, git: Git) -> Act:
    """The action `--execute` supplies, closed over what it writes through."""

    def write(
        config: FeedstockConfig,
        pull: BotPullRequest,
        planned: PlannedRecipe,
        verdict: Verdict,
        ci: CiStatus | None,
    ) -> Acted:
        if planned.unchanged:
            # Path B. There is no commit to push, so a label would be inert
            # and only swage can ever close this pull request.
            return _merge(github, pull, config, planned, ci)
        if config.trust == "manual":
            # The bottom of the trust ladder, and where every feedstock starts.
            # `consider` says so in a note, in every command, because it is a
            # fact about the config rather than about this run.
            return Acted()

        release = _release(planned.upstream)
        try:
            pushed = git.push_recipe(
                pull,
                planned.rendered,
                commit_message(release, upstream_location(planned.recipe, config)),
            )
        except ForgeError as exc:
            # Nothing landed, so nothing is degraded -- this feedstock simply
            # did not get its update, and the next run will try again. Reported
            # as a `detail` rather than as `stopped`, because a plan does exist
            # and `explain` prints one or the other: the reader wants to see
            # the change that failed to land, not only that it failed.
            return Acted(outcome="failed", detail=f"push failed: {failure_reason(exc)}")

        return _arm(github, pull, verdict, release, pushed.sha)

    return write


def _merge(
    github: GitHub,
    pull: BotPullRequest,
    config: FeedstockConfig,
    planned: PlannedRecipe,
    ci: CiStatus | None,
) -> Acted:
    """Close a pull request nothing else ever will (DESIGN.md 5.2).

    The one irreversible thing swage does that nobody reviews, so the two
    conditions are stated here rather than inferred, even though the gates
    have already established both: CI has finished and passed, and somebody
    blessed this feedstock. `ci` is only computed where every check passed --
    trust among them -- so re-reading the trust level changes no outcome
    today. It is here because the day that stops being true, the failure is a
    merge on a feedstock nobody approved.
    """
    if ci is None or not ci.verified or config.trust != "auto":
        return Acted()

    release = _release(planned.upstream)
    try:
        merge_pull(github, pull, release)
    except ForgeError as exc:
        # Includes the case the pin exists for: the bot pushed between the
        # check and the merge, GitHub refused, and the next run reads the new
        # commit and decides about that one instead.
        return Acted(outcome="failed", detail=f"merge failed: {failure_reason(exc)}")

    # Afterwards, and never before. A comment written first says a merge
    # happened, and the first time a merge then fails that sentence is
    # permanent and on a repository swage does not own (DESIGN.md 5.2).
    try:
        github.comment(pull.repo, pull.number, merge_comment(release, ci))
    except ForgeError:
        # The merge is made and is not in doubt; what is missing is the
        # explanation beside it on GitHub. The reasoning is still in the run
        # record, so this is a note rather than a verdict.
        return Acted(outcome="merged", notes=(NO_MERGE_COMMENT,))
    return Acted(outcome="merged")


def _arm(
    github: GitHub,
    pull: BotPullRequest,
    verdict: Verdict,
    release: str,
    sha: str,
) -> Acted:
    """Label or explain, as the very next call after the push (DESIGN.md 5.5)."""
    if verdict.decision == "automerge":
        try:
            arm_automerge(github, pull)
        except ForgeError as exc:
            # The hazard DESIGN.md 5.5 is named for: swage's commit has already
            # broken conda-forge's own path B for this pull request, so leaving
            # it unlabelled is strictly worse than never having run.
            return Acted(
                outcome="degraded",
                detail=f"pushed {sha[:7]}, but labeling failed: {failure_reason(exc)}",
                pushed=sha,
            )
        return Acted(pushed=sha)

    notes: tuple[str, ...] = ()
    try:
        github.comment(pull.repo, pull.number, refusal_comment(release, verdict))
    except ForgeError:
        notes = (NO_COMMENT,)
    # No outcome: PROPOSED versus NEEDS REVIEW is `outcome_for`'s to decide,
    # and it has to decide it for a dry run too.
    return Acted(notes=notes, pushed=sha)


def _release(upstream: UpstreamMetadata) -> str:
    """`name version`, or just the name where the version could not be read."""
    return f"{upstream.name} {upstream.version}" if upstream.version else upstream.name
