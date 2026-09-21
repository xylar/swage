"""`swage update`: `scan` plus writes (v1 §8, §5; DESIGN.md §9.8).

Everything up to the decision is the pipeline's (§12.2). What lives here is the
write: push, then label as the very next call (v1 §5.5), or push and comment
where a finding holds; nothing where the recipe already matches, the rung is
`never`, or a finding withholds. Writing is the default, and a dry run reaches
the same outcomes.
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

#: The line conda-forge's webservice answers by pushing a rerender, spelled as
#: conda-forge documents it (v1 §7.1).
RERENDER_REQUEST = "@conda-forge-admin, please rerender"

#: What the buckets mean for a run that wrote (v1 §9): the defaults.
UPDATE_DESCRIPTIONS: dict[str, str] = {}

#: Said above every bucket of a run that did not write, because most feedstocks
#: land in buckets whose wording does not say.
DRY_RUN_BANNER = "DRY RUN -- nothing was written; drop --dry-run to push"

#: And for a run that did not write: the same outcomes, subjunctive sentences
#: (v1 §8).
DRY_RUN_DESCRIPTIONS = {
    "automerge": "would push + label automerge -- drop `--dry-run` to do it",
}

#: Said where the push landed and the explanation did not.
NO_COMMENT = "pushed, but the comment explaining the verdict could not be left"

SWAGE_URL = "https://github.com/xylar/swage"

#: What every comment ends with (DESIGN.md §3.1).
TRAILER = (
    "\n---\n\n"
    f"*Posted by [swage]({SWAGE_URL}) on @xylar's behalf. The change above was "
    "generated, not reviewed; please check it accordingly.*\n"
)


def refusal_comment(
    release: str, findings: Sequence[Finding], config: FeedstockConfig
) -> str:
    """What swage says on a pull request it pushed to and would not arm: the
    rung and the `said` halves, and the trailer (DESIGN.md §3.1, §11.3).
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
    write: bool = False,
    command: str = "swage update",
    fetch: Fetcher = download,
    progress: Callable[[str], None] | None = None,
    migrate: bool = False,
) -> Run:
    """Update every feedstock in ``feedstocks``, writing only if ``write``.
    ``migrate`` converts a v0 feedstock before reconciling it (v1 §7.1).
    """
    started = datetime.now(UTC).isoformat(timespec="seconds")
    act = _writer(github, git) if write else do_nothing
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
    (v1 §7). The rerender request is on a line of its own, since the
    webservice reads it off the comment.
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
    """The action an `update` that writes supplies, closed over its clients."""

    def write(
        config: FeedstockConfig,
        pull: BotPullRequest,
        plan: Plan,
        decision: Decision,
        migration: Migration | None,
    ) -> Acted:
        if not decision.pushes:
            # Nothing to push, `trust: never`, or a withholding finding
            # (DESIGN.md §9.8); a `never` feedstock gets its note from the
            # pipeline.
            return Acted()

        release = _release(plan.upstream.primary)
        source = upstream_location(plan.recipe, config)
        moved = plan.correction.moved if plan.correction is not None else ()
        try:
            pushed = (
                git.push_recipe(
                    pull, plan.rendered, commit_message(release, source, moved)
                )
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
                    recipe_note=commit_message(release, source, moved),
                )
            )
        except ForgeError as exc:
            # Nothing landed, so nothing is degraded; a `reason` and a note
            # rather than a `stopped`, because a plan exists.
            return Acted(
                outcome="failed", reason="the push failed", notes=(failure_reason(exc),)
            )

        # A migration is never automerged (v1 §7), so it takes the comment path.
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
    """Label or explain, as the very next call after the push (v1 §5.5).
    ``migration`` is the conversion just pushed, which gets a comment of its
    own.
    """
    if decision.labels:
        try:
            arm_automerge(github, pull)
        except ForgeError as exc:
            # The hazard v1 §5.5 is named for: swage's commit has broken
            # conda-forge's own path for this pull request.
            return Acted(
                outcome="needs-review",
                reason=(
                    f"pushed {sha[:7]}, but the label did not land -- merge it yourself"
                ),
                notes=(f"labeling failed: {failure_reason(exc)}",),
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
