"""`swage update` -- `scan` plus writes (DESIGN.md 8, 5.1, 5.2, 5.5).

Everything up to the verdict is `consider`'s and is shared with `scan`, so what
lives here is only what happens *after* the gates have spoken. There are four
answers and each one is a rule from DESIGN.md rather than a preference:

**Path B writes nothing at all.** The recipe already matches upstream, so
there is no commit to make -- and with no commit there is no CI run, so
nothing will ever dispatch conda-forge's automerge for that pull request
(DESIGN.md 2.1). swage cannot close it either: GitHub refuses a merge that
writes a workflow file unless the credential swage borrows carries the
`workflow` scope, and conda-smithy re-renders one into most bot pull requests
(DESIGN.md 5.2). So swage checks that CI is green, says the pull request is
ready, and leaves it to a person -- who is one click away in the report.

**A change the gates hold is not pushed either** (DESIGN.md 5.4), and neither
is anything at all on a `trust: never` feedstock. Those are the two cases where
swage has a change ready and deliberately does not make it, so the record says
so out loud rather than leaving a reader to infer it from a gate name.

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
    conversion_message,
    download,
    upstream_location,
)
from swage.plan import Verdict
from swage.report import RunRecord, condition_rows
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
    "DRY_RUN_BANNER",
    "DRY_RUN_DESCRIPTIONS",
    "UPDATE_DESCRIPTIONS",
    "refusal_comment",
    "run_update",
]

#: What the buckets mean for a run that wrote (DESIGN.md 9). The defaults are
#: already `update`'s -- "pushed + labeled automerge" is what MERGE-READY means
#: -- so only the two buckets naming another command, or an action this command
#: took rather than declined, are said differently.
UPDATE_DESCRIPTIONS = {
    "awaiting-ci": "no changes needed; CI has not finished -- swage checks again",
    "needs-migration": "v0 meta.yaml -- `swage migrate` converts it",
}

#: Said above every bucket of a run that did not write.
#:
#: The subjunctive descriptions below were the only thing telling a dry run
#: apart from an `--execute` run, and they speak for two outcomes out of
#: twelve. A feedstock held for review lands in neither -- which is the fleet's
#: default state and most of what `update` reports -- so the two runs printed
#: identical bytes. Whether swage wrote to somebody else's repository is not
#: something a reader should have to infer from which buckets are populated.
DRY_RUN_BANNER = "DRY RUN -- nothing was written; add --execute to push"

#: And for a run that did not write. Same outcomes, subjunctive sentences: an
#: outcome is a statement about the gates rather than about what was written,
#: so a dry run and an `--execute` run of the same invocation put every
#: feedstock in the same bucket (DESIGN.md 8).
DRY_RUN_DESCRIPTIONS = {
    "merge-ready": "would push + label automerge -- `--execute` to do it",
    "proposed": "would push; needs your review before labeling",
    "awaiting-ci": "no changes needed; CI has not finished -- swage checks again",
    "needs-migration": "v0 meta.yaml -- `swage migrate` converts it",
}

#: Said where the push landed and the explanation did not. The verdict is
#: unaffected -- the gates decided it and the report still names them -- but
#: the pull request itself is now carrying a swage commit with nothing on it
#: saying why, so somebody should know.
NO_COMMENT = "pushed, but the comment explaining the verdict could not be left"


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

    **It says whether the change itself is in question**, because that is what
    decides whether the reader has to re-check the diff or only answer what is
    listed (DESIGN.md 5.4). Ordinarily nothing here is about the change --
    swage does not push one it cannot vouch for -- and saying so is what makes
    the list read as questions rather than as defects. The exception is a
    migration, which is pushed whatever the gates found, so the sentence is
    written only when it is true.
    """
    reasons = "\n".join(f"- {gate.detail or gate.title}" for gate in verdict.failures)
    sound = (
        ""
        if verdict.withheld
        else (
            "Each of those is a decision about the recipe rather than a "
            "problem with the change above.\n\n"
        )
    )
    return (
        f"swage updated `recipe/recipe.yaml` to match {release} and pushed the "
        "result. It did **not** add the `automerge` label, because:\n"
        "\n"
        f"{reasons}\n"
        "\n"
        f"{sound}"
        "Nothing will merge this pull request on its own: a maintainer has "
        "to merge it, or add the `automerge` label.\n"
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
    migrate: bool = False,
) -> RunRecord:
    """Update every feedstock in ``feedstocks``, writing only if ``execute``.

    ``migrate`` converts a v0 feedstock before reconciling it, rather than
    reporting it as needing migration and moving on (DESIGN.md 7.1). Off by
    default, because turning 148 feedstocks into pull requests is not
    something to trip into.
    """
    started = datetime.now(UTC).isoformat(timespec="seconds")
    act = _writer(github, git) if execute else do_nothing
    records = []
    for feedstock in feedstocks:
        if progress is not None:
            progress(feedstock)
        records.append(
            consider_feedstock(github, tree, feedstock, names, fetch, act, migrate)
        )
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
        if planned.unchanged and planned.migration is None:
            # Path B. There is no commit to push, a label would be inert, and
            # swage cannot merge it either (DESIGN.md 5.2) -- so the pull
            # request is reported for a human and nothing is written.
            #
            # Asked together with the conversion, because a conversion needing
            # no dependency edit is `unchanged` against the converted recipe
            # while having the most of any case to push (DESIGN.md 7.1).
            return Acted()
        if config.trust == "never":
            # The one rung that is about the feedstock rather than about the
            # change: somebody said swage does not write here. `consider` says
            # so in a note, in every command, because it is a fact about the
            # config rather than about this run.
            #
            # A conversion does not override it. DESIGN.md 7's ceiling caps
            # what a migration may do; it does not license writing to a
            # feedstock whose maintainer said not to.
            return Acted()
        if planned.migration is None and verdict.withheld:
            # **Only a check about the rendering itself withholds the push**
            # (DESIGN.md 5.4). A diff swage cannot vouch for is one a reviewer
            # would have to check line by line, in a repository swage does not
            # own, which is the one review nobody has time for -- so it is not
            # offered, and the reasoning stays in the report and in what
            # `swage draft` assembles.
            #
            # A check that says a *decision* is outstanding does not reach
            # here. The change is complete and correct as far as it goes, and
            # holding it would mean a feedstock that owes somebody an answer
            # about four lines never gets the other forty updated either.
            #
            # A migration is exempt, and by construction rather than by
            # exception: its diff touches every line of the recipe, so the
            # gates have nothing to say about it and DESIGN.md 7 already sends
            # it to a person whatever they said.
            return Acted()

        release = _release(planned.upstream.primary)
        source = upstream_location(planned.recipe, config)
        migration = planned.migration
        try:
            pushed = (
                git.push_recipe(pull, planned.rendered, commit_message(release, source))
                if migration is None
                else git.push_migration(
                    pull,
                    forge_config=migration.forge_config_text,
                    conversion=migration.recipe_text,
                    conversion_note=conversion_message(
                        migration.forge_config_added,
                        migration.reported_concerns,
                        migration.review.damage,
                        condition_rows(migration.review.conditions),
                    ),
                    recipe=planned.rendered,
                    recipe_note=commit_message(release, source),
                )
            )
        except ForgeError as exc:
            # Nothing landed, so nothing is degraded -- this feedstock simply
            # did not get its update, and the next run will try again. Reported
            # as a `detail` rather than as `stopped`, because a plan does exist
            # and `explain` prints one or the other: the reader wants to see
            # the change that failed to land, not only that it failed.
            return Acted(outcome="failed", detail=f"push failed: {failure_reason(exc)}")

        # A migration is never automerged (DESIGN.md 7), so it takes the
        # comment path whatever the gates decided -- the ceiling, applied at
        # the one place that could have labelled it.
        return _arm(
            github, pull, verdict, release, pushed.sha, automerge=migration is None
        )

    return write


def _arm(
    github: GitHub,
    pull: BotPullRequest,
    verdict: Verdict,
    release: str,
    sha: str,
    automerge: bool = True,
) -> Acted:
    """Label or explain, as the very next call after the push (DESIGN.md 5.5).

    ``automerge`` is False for a migration, which is capped at proposing
    however its gates came out (DESIGN.md 7). It is a parameter rather than
    something read off the verdict because the verdict is about the
    dependencies and this is about the conversion underneath them.
    """
    if automerge and verdict.decision == "automerge":
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
