"""`swage update` -- `scan` plus writes (design-v1.md 8, 5.1, 5.2, 5.5).

Everything up to the decision is the pipeline's and is shared with `scan`
(DESIGN.md §12.2), so what lives here is only what happens *after* it. There are four
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
from swage.plan import Decision, Finding, Plan, rung_sentence
from swage.run import Run, condition_rows
from swage.upstream import UpstreamMetadata

from .pipeline import (
    Act,
    Acted,
    NameSources,
    consider_feedstock,
    do_nothing,
    failure_reason,
)

__all__ = [
    "DRY_RUN_BANNER",
    "DRY_RUN_DESCRIPTIONS",
    "RERENDER_REQUEST",
    "SWAGE_URL",
    "TRAILER",
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
    "automerge": "would push + label automerge -- drop `--dry-run` to do it",
}

#: Said where the push landed and the explanation did not. The verdict is
#: unaffected -- the gates decided it and the report still names them -- but
#: the pull request itself is now carrying a swage commit with nothing on it
#: saying why, so somebody should know.
NO_COMMENT = "pushed, but the comment explaining the verdict could not be left"

SWAGE_URL = "https://github.com/xylar/swage"

#: What every comment ends with (DESIGN.md §3.1). It lands on a repository
#: swage does not own, under an account whose owner is the only person there
#: who knows what wrote it.
TRAILER = (
    "\n---\n\n"
    f"*Posted by [swage]({SWAGE_URL}) on @xylar's behalf. The change above was "
    "generated, not reviewed; please check it accordingly.*\n"
)


def refusal_comment(
    release: str, findings: Sequence[Finding], config: FeedstockConfig
) -> str:
    """What swage says on a pull request it pushed to and would not arm.

    The rung and the `said` halves, and nothing a reader has to research
    (DESIGN.md §3.1, §11.3). There is no `swage:needs-review` label on any
    feedstock and swage creates none (v1 §5.4), so the comment is where the
    reasons go, and it ends by saying what to do rather than how conda-forge
    works.
    """
    rung = rung_sentence(config)
    lead = (
        f"swage updated `recipe/recipe.yaml` to match {release} and pushed it "
        "without the `automerge` label."
    )
    if rung:
        lead = f"{lead} {rung}."
    if findings:
        lead = (
            f"{lead} Still outstanding, none of them a problem with the change "
            f"itself:\n\n{_bullets(findings)}"
        )
    return (
        f"{lead}\n\n"
        "Nothing merges this pull request on its own: a maintainer merges it, "
        f"or adds the label.\n{TRAILER}"
    )


def _bullets(findings: Sequence[Finding]) -> str:
    """One bullet per finding, the `said` half only (DESIGN.md §11.3)."""
    return "\n".join(f"- {finding.said}" for finding in findings)


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
) -> Run:
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
    return Run(command=command, started=started, feedstocks=tuple(records))


def migration_comment(
    release: str, findings: Sequence[Finding], forge_config_added: Sequence[str]
) -> str:
    """What swage says on a pull request it converted, and asks of it.

    A conversion is pushed whatever the checks found and is never labeled
    (v1 §7), so the rung goes unsaid. The rerender request is on a line of
    its own, spelled as conda-forge documents it, since the webservice reads
    it off the comment; the trailer may follow it.
    """
    tools = (
        ", switched `conda-forge.yml` to rattler-build," if forge_config_added else ""
    )
    found = (
        f" whatever the checks found:\n\n{_bullets(findings)}"
        if findings
        else ". The checks found nothing outstanding."
    )
    return (
        f"swage converted `recipe/meta.yaml` to `recipe/recipe.yaml`{tools} and "
        f"updated it to match {release}, in two commits. A conversion is "
        f"reviewed by hand, so the `automerge` label is not added{found}\n"
        "\n"
        "A maintainer merges this, or adds the label. The new format needs a "
        "rerender:\n"
        "\n"
        f"{RERENDER_REQUEST}\n{TRAILER}"
    )


def _writer(github: GitHub, git: Git) -> Act:
    """The action `--execute` supplies, closed over what it writes through."""

    def write(
        config: FeedstockConfig,
        pull: BotPullRequest,
        plan: Plan,
        decision: Decision,
        migration: Migration | None,
    ) -> Acted:
        if not decision.pushes:
            # Nothing to push, `trust: never`, or a finding that says the
            # rendering itself may be wrong (DESIGN.md §9.8). The reasoning
            # stays in the report and in what `swage draft` assembles; a
            # `never` feedstock gets a note from the pipeline in every command,
            # because it is a fact about the config rather than this run.
            return Acted()

        release = _release(plan.upstream.primary)
        source = upstream_location(plan.recipe, config)
        try:
            pushed = (
                git.push_recipe(pull, plan.rendered, commit_message(release, source))
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
                    recipe=plan.rendered,
                    recipe_note=commit_message(release, source),
                )
            )
        except ForgeError as exc:
            # Nothing landed, so nothing is degraded -- this feedstock simply
            # did not get its update, and the next run will try again. Reported
            # as a `detail` rather than as `stopped`, because a plan does exist
            # and `explain` prints one or the other: the reader wants to see
            # the change that failed to land, not only that it failed.
            return Acted(outcome="failed", reason=f"push failed: {failure_reason(exc)}")

        # A migration is never automerged (design-v1.md 7): the decision says
        # so, and it takes the comment path -- the ceiling, applied at the one
        # place that could have labeled it.
        return _arm(
            github,
            pull,
            decision,
            plan.findings,
            config,
            release,
            pushed.sha,
            migration,
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
                outcome="needs-review",
                reason=(
                    f"pushed {sha[:7]}, but labeling failed: {failure_reason(exc)} "
                    "-- merge it yourself"
                ),
                pushed=sha,
            )
        return Acted(pushed=sha)

    notes: tuple[str, ...] = ()
    comment = (
        refusal_comment(release, findings, config)
        if migration is None
        else migration_comment(release, findings, migration.forge_config_added)
    )
    try:
        github.comment(pull.repo, pull.number, comment)
    except ForgeError:
        notes = (NO_COMMENT,)
    # No outcome: the decision's stands, and it stands for a dry run too.
    return Acted(notes=notes, pushed=sha)


def _release(upstream: UpstreamMetadata) -> str:
    """`name version`, or just the name where the version could not be read."""
    return f"{upstream.name} {upstream.version}" if upstream.version else upstream.name
