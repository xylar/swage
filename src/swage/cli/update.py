"""`swage update` -- `scan` plus writes (DESIGN.md 8, 5.1, 5.5).

Everything up to the verdict is `consider`'s and is shared with `scan`, so what
lives here is only what happens *after* the gates have spoken. There are four
answers and each one is a rule from DESIGN.md rather than a preference:

**Path B pushes nothing.** The recipe already matches upstream, so there is no
commit to make. Merging such a pull request is the only thing that will ever
close it (DESIGN.md 5.2), and that is phase 3.5 -- this command leaves it in
AWAITING CI and says so.

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
    Fetcher,
    ForgeError,
    Git,
    GitHub,
    arm_automerge,
    commit_message,
    download,
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
)

__all__ = [
    "DRY_RUN_DESCRIPTIONS",
    "UPDATE_DESCRIPTIONS",
    "refusal_comment",
    "run_update",
]

#: What the buckets mean for a run that wrote (DESIGN.md 9). The defaults are
#: already `update`'s -- "pushed + labeled automerge" is what MERGE-READY means
#: -- so only the two buckets naming a phase that does not exist yet are said
#: differently.
UPDATE_DESCRIPTIONS = {
    "awaiting-ci": "no changes needed -- swage will merge these once it can",
    "needs-migration": "v0 meta.yaml -- `swage migrate` converts it",
}

#: And for a run that did not write. Same outcomes, subjunctive sentences: an
#: outcome is a statement about the gates rather than about what was written,
#: so a dry run and an `--execute` run of the same invocation put every
#: feedstock in the same bucket (DESIGN.md 8).
DRY_RUN_DESCRIPTIONS = {
    "merge-ready": "would push + label automerge -- `--execute` to do it",
    "proposed": "would push; needs your review before labeling",
    "awaiting-ci": "no changes needed -- swage will merge these once it can",
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
    """
    reasons = "\n".join(f"- {gate.detail or gate.title}" for gate in verdict.failures)
    return (
        f"swage updated `recipe/recipe.yaml` to match {release} and pushed the "
        "result. It did **not** add the `automerge` label, because:\n"
        "\n"
        f"{reasons}\n"
        "\n"
        "conda-forge removes an `automerge` label as soon as a commit lands "
        "after it, so nothing will merge this pull request until a maintainer "
        "reviews the change and adds the label.\n"
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
    ) -> Acted:
        if planned.unchanged:
            # Path B. There is no commit to push, so a label would be inert and
            # only swage can ever merge this -- which is phase 3.5.
            return Acted()
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
            return Acted(outcome="failed", detail=f"push failed: {_reason(exc)}")

        return _arm(github, pull, verdict, release, pushed.sha)

    return write


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
                detail=f"pushed {sha[:7]}, but labeling failed: {_reason(exc)}",
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


def _reason(exc: ForgeError) -> str:
    """The half of a command failure worth putting on a report line.

    `run_gh` builds its message as the argv it ran, then the program's stderr.
    The argv is the half a reader could reconstruct; the stderr is the half
    only that run knows, so it is what the one line gets.
    """
    head, _, rest = str(exc).partition("\n")
    body = " ".join(rest.split())
    return body or head
