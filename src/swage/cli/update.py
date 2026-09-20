"""`swage update` -- `scan` plus writes (design-v1.md 8, 5.1, 5.2, 5.5).

Everything up to the verdict is `consider`'s and is shared with `scan`, so what
lives here is only what happens *after* the gates have spoken. There are four
answers and each one is a rule from design-v1.md rather than a preference:

**Path B writes nothing at all.** The recipe already matches upstream, so
there is no commit to make -- and with no commit there is no CI run, so
nothing will ever dispatch conda-forge's automerge for that pull request
(design-v1.md 2.1). swage cannot close it either: GitHub refuses a merge that
writes a workflow file unless the credential swage borrows carries the
`workflow` scope, and conda-smithy re-renders one into most bot pull requests
(design-v1.md 5.2). So swage checks that CI is green, says the pull request is
ready, and leaves it to a person -- who is one click away in the report.

**A change the gates hold is not pushed either** (design-v1.md 5.4), and neither
is anything at all on a `trust: never` feedstock. Those are the two cases where
swage has a change ready and deliberately does not make it, so the record says
so out loud rather than leaving a reader to infer it from a gate name.

**Push, then label, as one unit.** Labeling first guarantees the label is
stripped; pushing without labeling leaves a `[bot-automerge]` pull request
*less* automated than swage found it, because swage's commit is not a bot's
and breaks conda-forge's all-commits-from-a-bot test forever (design-v1.md 2.2).
So the label goes on as the very next call after a successful push, and a
label that will not land after retries is DEGRADED -- reported at the top of
the run rather than buried in a success list, because it is the one state that
needs a human to repair automation swage removed.

**A failing gate still pushes, and explains itself on the pull request.** The
work is not thrown away. What swage does not do is arm automerge, and the
comment says which gates stopped it -- there is no `swage:needs-review` label
on any feedstock and swage creates none (design-v1.md 5.4).

**Writing is the default, and the dry run is not a rehearsal.** With
`--dry-run` this command is exactly `scan` with different wording, down to
reaching the same outcome for every feedstock, so what the report says it would
do is what the same invocation without the flag does.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from swage.config import ConfigTree, FeedstockConfig
from swage.forge import (
    BotPullRequest,
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
from swage.migrate import Migration
from swage.plan import CHECKS, Decision, Finding, Kind, rung_sentence, withheld
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
    "RERENDER_REQUEST",
    "SWAGE_URL",
    "UPDATE_DESCRIPTIONS",
    "migration_comment",
    "refusal_comment",
    "run_update",
]

#: The line conda-forge's webservice answers by pushing a rerender to the
#: pull request, exactly as conda-forge documents it. A conversion changes
#: the build tool in `conda-forge.yml`, and the CI configuration that reads
#: it is generated rather than written, so the pull request cannot build until
#: somebody asks for this -- which was a step the first converted feedstock's
#: maintainer had to work out for themselves (design-v1.md 7.1).
RERENDER_REQUEST = "@conda-forge-admin, please rerender"

#: What the buckets mean for a run that wrote (design-v1.md 9). The defaults are
#: already `update`'s -- "pushed + labeled automerge" is what MERGE-READY
#: means, and NEEDS MIGRATION says to rerun with `--migrate` -- so nothing is
#: said differently. It is kept as a name so the two runs are chosen between
#: in one expression. There was an override once, and it was worse than the
#: default it replaced: it named `swage migrate`, which converts nothing --
#: that command previews a conversion, and the one that pushes it is this
#: one with `--migrate` (design-v1.md 7.1).
UPDATE_DESCRIPTIONS: dict[str, str] = {}

#: Said above every bucket of a run that did not write.
#:
#: The subjunctive descriptions below were the only thing telling a dry run
#: apart from a run that wrote, and they speak for two outcomes out of
#: twelve. A feedstock held for review lands in neither -- which is the fleet's
#: default state and most of what `update` reports -- so the two runs printed
#: identical bytes. Whether swage wrote to somebody else's repository is not
#: something a reader should have to infer from which buckets are populated.
DRY_RUN_BANNER = "DRY RUN -- nothing was written; drop --dry-run to push"

#: And for a run that did not write. Same outcomes, subjunctive sentences: an
#: outcome is a statement about the gates rather than about what was written,
#: so a dry run and a writing run of the same invocation put every feedstock in
#: the same bucket (design-v1.md 8).
DRY_RUN_DESCRIPTIONS = {
    "merge-ready": "would push + label automerge -- drop `--dry-run` to do it",
    "proposed": "would push; needs your review before labeling",
}

#: Said where the push landed and the explanation did not. The verdict is
#: unaffected -- the gates decided it and the report still names them -- but
#: the pull request itself is now carrying a swage commit with nothing on it
#: saying why, so somebody should know.
NO_COMMENT = "pushed, but the comment explaining the verdict could not be left"

#: Where the comment sends a reader who has never heard of swage. It lands on
#: a repository swage does not own, under an account whose owner is the only
#: person there who knows what wrote it, so the first mention is a link.
SWAGE_URL = "https://github.com/xylar/swage"


def refusal_comment(
    release: str, findings: Sequence[Finding], config: FeedstockConfig
) -> str:
    """What swage says on a pull request it pushed to and would not arm.

    It names the reasons rather than only the fact, which is the whole reason
    design-v1.md 5.4 settled on a comment: there is no `swage:needs-review` label
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

    **The first mention of swage is a link to it**, for the same reason. The
    name means nothing to a conda-forge maintainer reading a comment on their
    own pull request: what posted it is an account they know, belonging to
    somebody who has not said what they are running, and the rest of this --
    what was reconciled, why the label is absent -- is easier to weigh once
    they can go and look.

    **It closes by saying what to do, not how conda-forge works.** It used to
    explain that a label is stripped by any commit landing after it, which is
    true, load-bearing for swage, and of no use to the person reading: they
    are not watching the pull request in the minutes between a push and the
    end of CI, and by the time they read this the mechanism has already had
    its effect. Two courses of action are open to them and the comment names
    both.

    **One bullet per finding, not per check.** A check that found two things
    used to render both in one bullet, joined with `; ` and with a doubled full
    stop wherever the first ended in one -- and it carried swage's advice about
    its own config keys into a comment on a repository swage does not own. The
    findings are what the reader has to act on; what to do about the set of
    them is said in swage's own output (design-v1.md 5.4, CLAUDE.md).

    **It says whether the change itself is in question**, because that is what
    decides whether the reader has to re-check the diff or only answer what is
    listed (design-v1.md 5.4). "A decision outstanding" rather than "a decision
    about the recipe", because the commonest comment swage will ever post has
    one bullet and it is the trust rung, which is a decision about the
    feedstock and not about its recipe at all. Ordinarily nothing here is about
    the change -- swage does not push one it cannot vouch for -- and saying so
    is what makes the list read as questions rather than as defects. The
    exception is a migration, which is pushed whatever the gates found, so the
    sentence is written only when it is true.
    """
    reasons = _bullets(findings, config)
    sound = (
        ""
        if withheld(findings)
        else (
            "Each of those is a decision outstanding rather than a "
            "problem with the change above.\n\n"
        )
    )
    return (
        f"[swage]({SWAGE_URL}) updated `recipe/recipe.yaml` to match "
        f"{release} and pushed the result. It did **not** add the "
        "`automerge` label, because:\n"
        "\n"
        f"{reasons}\n"
        "\n"
        f"{sound}"
        "Nothing will merge this pull request on its own: a maintainer has "
        "to merge it, or add the `automerge` label.\n"
    )


#: The check the rung's sentence follows in a comment. v1 listed the rung as
#: a check between the fourth and the eighth, so a comment lists it where it
#: always did, and one about the same feedstock reads the same before and
#: after the rung stopped being a check.
_RUNG_AFTER: Kind = "orphaned-output"


def _bullets(findings: Sequence[Finding], config: FeedstockConfig) -> str:
    """One bullet per finding, and one for the rung where there is one to say.

    The `said` halves only: what the reader has to act on. What to do about
    the set of them names swage's own config keys and is said in swage's own
    output (v1 §5.4, CLAUDE.md).
    """
    kinds = [row.kind for row in CHECKS]
    slot = kinds.index(_RUNG_AFTER)
    rung = rung_sentence(config)
    lines: list[str] = []
    for finding in findings:
        if rung and kinds.index(finding.kind) > slot:
            lines.append(rung)
            rung = ""
        lines.append(finding.said)
    if rung:
        lines.append(rung)
    return "\n".join(f"- {line}" for line in lines)


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
    reporting it as needing migration and moving on (design-v1.md 7.1). Off by
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


def migration_comment(
    release: str,
    findings: Sequence[Finding],
    config: FeedstockConfig,
    forge_config_added: Sequence[str],
) -> str:
    """What swage says on a pull request it converted, and asks of it.

    The refusal comment's rules hold -- swage is linked where it is first
    named, every reason is a sentence, and nothing in it needs the design --
    and three things are different because a conversion is a different thing
    to have pushed.

    **It says what the two commits are**, because the reader is looking at a
    diff that touches every line of the recipe and deletes the file they knew,
    and the dependency change they could have judged is the second commit
    rather than the diff (design-v1.md 7.1).

    **It says the label would not have been added whatever the checks found**,
    rather than presenting the checks as the reason. On a migration they are
    not: the ceiling is (design-v1.md 7), and a comment listing two findings as
    the reason invites fixing those two and adding the label to a recipe
    nobody has read.

    **It ends by asking conda-forge for a rerender**, on a line of its own,
    because the CI configuration is generated from the recipe and
    `conda-forge.yml`, and the recipe's format has just changed -- with the
    build tool beside it, on every feedstock but the handful whose
    `conda-forge.yml` already named rattler-build. The pull request cannot
    build until that happens, and the webservice reads the request off any
    comment on the pull request -- so swage's own comment is where it goes,
    and the sentence above it says why, since to the maintainer it reads as
    swage asking a bot to push to their pull request.
    """
    tools = (
        " and set `conda-forge.yml` to build it with rattler-build"
        if forge_config_added
        else ""
    )
    bullets = _bullets(findings, config)
    checks = (
        "They found nothing outstanding."
        if not bullets
        else f"They found:\n\n{bullets}"
    )
    return (
        f"[swage]({SWAGE_URL}) converted `recipe/meta.yaml` to "
        f"`recipe/recipe.yaml`{tools}, then updated the converted recipe to "
        f"match {release} -- as two commits, so the dependency change can be "
        "read on its own. The conversion's commit message says what the "
        "converter reported and what swage changed in its output.\n"
        "\n"
        "A converted recipe is reviewed by hand: conversion is imperfect, and "
        "swage's checks vouch for the dependency change rather than for the "
        "rest of the file. So swage did **not** add the `automerge` label, "
        f"and would not have whatever they found. {checks}\n"
        "\n"
        "Nothing will merge this pull request on its own: a maintainer has "
        "to merge it, or add the `automerge` label.\n"
        "\n"
        "The CI configuration is generated from the recipe and "
        "`conda-forge.yml` rather than written, so a recipe in a new format "
        "needs it regenerated before this can build:\n"
        "\n"
        f"{RERENDER_REQUEST}\n"
    )


def _writer(github: GitHub, git: Git) -> Act:
    """The action `--execute` supplies, closed over what it writes through."""

    def write(
        config: FeedstockConfig,
        pull: BotPullRequest,
        planned: PlannedRecipe,
        decision: Decision,
        findings: Sequence[Finding],
    ) -> Acted:
        if not decision.pushes:
            # Nothing to push, `trust: never`, or a finding that says the
            # rendering itself may be wrong (DESIGN.md §9.8). The reasoning
            # stays in the report and in what `swage draft` assembles; a
            # `never` feedstock gets a note from `consider` in every command,
            # because it is a fact about the config rather than this run.
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

        # A migration is never automerged (design-v1.md 7): the decision says
        # so, and it takes the comment path -- the ceiling, applied at the one
        # place that could have labeled it.
        return _arm(
            github, pull, decision, findings, config, release, pushed.sha, migration
        )

    return write


def _arm(
    github: GitHub,
    pull: BotPullRequest,
    decision: Decision,
    findings: Sequence[Finding],
    config: FeedstockConfig,
    release: str,
    sha: str,
    migration: Migration | None = None,
) -> Acted:
    """Label or explain, as the very next call after the push (design-v1.md 5.5).

    ``migration`` is the conversion just pushed. The decision already caps it
    at proposing (design-v1.md 7); it is passed because a conversion gets a
    comment of its own, since what was pushed and what it needs next are both
    different.
    """
    if decision.labels:
        try:
            arm_automerge(github, pull)
        except ForgeError as exc:
            # The hazard design-v1.md 5.5 is named for: swage's commit has already
            # broken conda-forge's own path B for this pull request, so leaving
            # it unlabeled is strictly worse than never having run.
            return Acted(
                outcome="degraded",
                detail=f"pushed {sha[:7]}, but labeling failed: {failure_reason(exc)}",
                pushed=sha,
            )
        return Acted(pushed=sha)

    notes: tuple[str, ...] = ()
    comment = (
        refusal_comment(release, findings, config)
        if migration is None
        else migration_comment(release, findings, config, migration.forge_config_added)
    )
    try:
        github.comment(pull.repo, pull.number, comment)
    except ForgeError:
        notes = (NO_COMMENT,)
    # No outcome: PROPOSED versus NEEDS REVIEW is the decision's, and it
    # decides it for a dry run too.
    return Acted(notes=notes, pushed=sha)


def _release(upstream: UpstreamMetadata) -> str:
    """`name version`, or just the name where the version could not be read."""
    return f"{upstream.name} {upstream.version}" if upstream.version else upstream.name
