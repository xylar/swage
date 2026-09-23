"""`swage update`: `scan` plus writes (v1 §8, §5; DESIGN.md §9.8).

Everything up to the decision is the pipeline's (§12.2). What lives here is the
write: push, then label as the very next call (v1 §5.5), or push and comment
where a finding holds; the label alone where the recipe already matches and CI
is still running; nothing where the rung is `never`, a finding withholds, or
there is no window left to label into. Every one of those that read a release
and found the recipe already matching says so on the pull request, because the
check leaves no other trace. Writing is the default, and a dry run reaches the
same outcomes.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace
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
from swage.plan import Decision, ExtraConstraint, Finding, Plan, rung_sentence
from swage.plan.prose import fenced
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
    "NO_CHANGE",
    "RERENDER_REQUEST",
    "SWAGE_URL",
    "TRAILER",
    "UPDATE_DESCRIPTIONS",
    "automerge_comment",
    "migration_comment",
    "no_change_comment",
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
    "labeled": "would label automerge -- drop `--dry-run` to do it",
}

#: Said where the push landed and the explanation did not.
NO_COMMENT = "pushed, but the comment explaining the verdict could not be left"

#: And where nothing was pushed, so the comment was the whole of the record.
NO_RECORD = "the comment recording the check could not be left"

SWAGE_URL = "https://github.com/xylar/swage"

#: Where swage argues that a run constraint and an extra are different things,
#: and what to do instead. Published, so it is a page a reader can open rather
#: than a key in a file they cannot.
RUN_CONSTRAINTS_URL = "https://xylar.github.io/swage/config/names/#run_constraints"


def _trailer(what: str) -> str:
    """The one line every comment ends with, naming what was generated
    (DESIGN.md §3.1).
    """
    return (
        "\n---\n\n"
        f"*Posted by [swage]({SWAGE_URL}) on @xylar's behalf. The {what} above "
        "was generated, not reviewed; please check it accordingly.*\n"
    )


#: What a comment carrying a change ends with, and what one carrying only a
#: reading ends with.
TRAILER = _trailer("change")
CHECK_TRAILER = _trailer("reconciliation")

#: The outcomes a pull request needing no change reaches, which is what the
#: comment's closing sentence is chosen by (DESIGN.md §9.8). `needs-review`
#: is among them: a recipe that already matches can still be held by a
#: question about the feedstock.
NO_CHANGE = frozenset({"ready-to-merge", "labeled", "awaiting-ci", "needs-review"})


def refusal_comment(
    release: str, findings: Sequence[Finding], config: FeedstockConfig
) -> str:
    """What swage says on a pull request it pushed to and would not arm: the
    rung and the `said` halves, and the trailer (DESIGN.md §3.1, §11.3).
    """
    # The rung says why the label is off; without one, say only that it is.
    # Both, and the closing line, said it three times in sixty words.
    rung = rung_sentence(config)
    label = f"{rung}." if rung else "The `automerge` label is not on it."
    lead = (
        f"swage updated `recipe/recipe.yaml` to match {release} and pushed it. {label}"
    )
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


def no_change_comment(
    release: str,
    declared_in: str,
    outcome: str,
    config: FeedstockConfig,
    findings: Sequence[Finding] = (),
) -> str:
    """What swage says on a pull request it read and found nothing to change in
    (DESIGN.md §3.1, §9.8).

    The release and the file that declared it are named because the comment is
    the whole of the record: a maintainer merging on the strength of it has no
    commit to read. ``outcome`` says what is left to happen, and ``findings``
    are the questions the recipe carries whatever this release did -- published
    as their `said` halves, like a refusal's (§11.3).
    """
    if outcome == "needs-review":
        # swage does not read CI where a finding holds a feedstock (v1 §5.1),
        # so this is the one sentence that cannot say what CI did.
        tail = (
            "Nothing merges this pull request on its own: a maintainer merges "
            "it, or adds the label."
        )
    elif outcome == "ready-to-merge":
        tail = "CI has passed. A maintainer needs to merge this."
    elif outcome == "labeled":
        tail = "CI is still running, and swage set the `automerge` label."
    else:
        # Empty on `auto`, which reaches this sentence only where the label
        # failed to land -- there the rung is not what left it off.
        rung = rung_sentence(config)
        why = f"{rung}: a" if rung else "A"
        tail = f"CI is still running. {why} maintainer merges this, or adds the label."
    lead = (
        f"swage reconciled `recipe/recipe.yaml` against {_read(release, declared_in)}, "
        "and every requirement already matches. Nothing to change."
    )
    if findings:
        lead = (
            f"{lead}\n\nStill outstanding, none of them a problem with this "
            f"pull request:\n\n{_bullets(findings)}"
        )
    return f"{lead}\n\n{tail}\n{CHECK_TRAILER}"


def automerge_comment(release: str, declared_in: str) -> str:
    """What swage says on a pull request it pushed to and armed (DESIGN.md
    §3.1).

    The commit records what changed; this records that swage made it and that
    the merge is now conda-forge's to make.
    """
    return (
        f"swage updated `recipe/recipe.yaml` to match {_read(release, declared_in)}, "
        f"and labeled it for automatic merging.\n{TRAILER}"
    )


def _read(release: str, declared_in: str) -> str:
    """The release, and the file that declared its dependencies: the half of
    the claim a reader can go and check.
    """
    return f"{release}, as declared in its `{declared_in}`" if declared_in else release


def constraints_comment(constraints: Sequence[ExtraConstraint]) -> str:
    """What swage says about `run_constrained` entries transcribed from
    upstream extras (DESIGN.md §9.7).

    Its own comment rather than a section of the others: it is about the
    recipe rather than about this release, it is the same every time until
    somebody changes the recipe, and swage posts it whatever else it did.
    The argument is one sentence and a link, because it is the same argument
    for every entry.
    """
    bullets = "\n".join(
        f"- {fenced(entry.name)}, under its {fenced(entry.extra)} extra"
        for entry in constraints
    )
    return (
        "swage noticed `run_constrained` entries that upstream declares only "
        f"under an extra:\n\n{bullets}\n\n"
        "A run constraint binds every environment holding the package, not "
        "the ones that asked for the extra. Publishing an extra as its own "
        f"output is the usual way to express it: {RUN_CONSTRAINTS_URL}\n"
        f"{CHECK_TRAILER}"
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
        release = _release(plan.upstream.primary)
        declared_in = plan.upstream.declared_in
        if not decision.pushes:
            # Nothing to push, `trust: never`, or a withholding finding
            # (DESIGN.md §9.8); a `never` feedstock gets its note from the
            # pipeline. A blessed feedstock whose recipe already matches is
            # the one of those swage still acts on.
            acted = _label(github, pull) if decision.labels else Acted()
            if not plan.unchanged:
                # `trust: never`, or a finding withholding a change swage has
                # in hand. Neither is a reading to record: the recipe and the
                # release disagree, and saying so is the pushed comment's job.
                # What the recipe says about extras is true either way.
                return _noticed(github, pull, acted, plan)
            # After the label, not before: a label that did not land changes
            # which closing sentence is true.
            acted = _say(
                github,
                pull,
                acted,
                no_change_comment(
                    release,
                    declared_in,
                    acted.outcome or decision.outcome,
                    config,
                    plan.findings,
                ),
            )
            return _noticed(github, pull, acted, plan)

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
        acted = _arm(
            github,
            pull,
            decision,
            plan.findings,
            config,
            release,
            declared_in,
            pushed.sha,
            migration,
        )
        return _noticed(github, pull, acted, plan)

    return write


def _noticed(github: GitHub, pull: BotPullRequest, acted: Acted, plan: Plan) -> Acted:
    """Leave the constraints comment where the recipe has such an entry.

    Last, after whatever this run itself had to say, and posted once like
    any other comment: it is the same sentence every run until somebody
    changes the recipe (DESIGN.md §9.7).
    """
    if not plan.extra_constraints:
        return acted
    return _say(github, pull, acted, constraints_comment(plan.extra_constraints))


def _say(github: GitHub, pull: BotPullRequest, acted: Acted, body: str) -> Acted:
    """Leave ``body`` on ``pull`` unless it already carries it word for word.

    A pull request swage reads twice is one it would otherwise comment on
    twice, and the second comment says nothing the first did not. Comparing
    bodies rather than looking for swage's trailer keeps a pull request whose
    situation has moved -- a finding since answered, a commit since pushed --
    getting the comment that now applies.
    """
    try:
        if body in github.comments(pull.repo, pull.number):
            return acted
        github.comment(pull.repo, pull.number, body)
    except ForgeError:
        return replace(acted, notes=(*acted.notes, NO_RECORD))
    return acted


def _label(github: GitHub, pull: BotPullRequest) -> Acted:
    """Label a pull request that needs no change while its CI is still running
    (DESIGN.md §9.8).

    A failure falls back to the bucket whose line already asks the reader for
    the label, while the window it names is still open; the comment that
    follows says the same thing.
    """
    try:
        arm_automerge(github, pull)
    except ForgeError as exc:
        return Acted(
            outcome="awaiting-ci",
            notes=(f"labeling failed: {failure_reason(exc)}",),
        )
    return Acted()


def _arm(
    github: GitHub,
    pull: BotPullRequest,
    decision: Decision,
    findings: Sequence[Finding],
    config: FeedstockConfig,
    release: str,
    declared_in: str,
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
        return _say(
            github, pull, Acted(pushed=sha), automerge_comment(release, declared_in)
        )

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
