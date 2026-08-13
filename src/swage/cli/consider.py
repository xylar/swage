"""Read one feedstock and decide what should happen to it (DESIGN.md 8).

Everything `scan` and `update` have in common lives here: which feedstocks a
run covers, which of a feedstock's open bot pull requests is the one to act
on, reading and planning it, and evaluating the gates. Both commands need all
of that and neither may reach a different answer -- a second implementation of
"is this pull request a version bump" would be a second thing to get wrong,
and two commands disagreeing about which bucket a feedstock is in would leave
two `run.json` that cannot be compared, which is the property DESIGN.md 8 is
protecting.

**What differs between the commands is a parameter.** `consider_feedstock`
takes what to *do* about a pull request once it has been judged, and `scan`
passes `do_nothing`. That is not a policy `scan` happens to hold -- it is the
entire difference between the two commands, said in one place, where a reader
can see that the read path and the write path are the same path.

**One feedstock's failure is that feedstock's failure.** A sweep over several
hundred repositories that aborts on the first unreadable recipe is a sweep
nobody can run unattended, so a per-feedstock error becomes a FAILED record
carrying its reason and the loop goes on. Only something wrong with the run
*itself* -- an invalid config tree, a channel that will not answer -- stops the
command, because that is not a fact about any one feedstock.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from swage.config import ConfigError, ConfigTree, FeedstockConfig, MappingLayer
from swage.forge import (
    RECIPE_V1,
    BotPullRequest,
    Fetcher,
    ForgeError,
    GitHub,
    NotFound,
    build_resolver,
    discover_feedstocks,
    download,
    fetch_upstream,
    open_bot_pull_requests,
    previous_version,
    read_ci_support,
    read_feedstock,
    upstream_location,
)
from swage.mapping import PackageIndex
from swage.plan import (
    PlanError,
    RecipePlan,
    Verdict,
    check_preconditions,
    evaluate_gates,
    plan_recipe,
    planned_blocks,
    resolve_python_min,
)
from swage.recipe import Recipe, RecipeError, read_recipe, render_recipe
from swage.report import FeedstockRecord, Outcome, build_record
from swage.upstream import UpstreamError, UpstreamMetadata

__all__ = [
    "Act",
    "Acted",
    "NameSources",
    "PlannedRecipe",
    "consider_feedstock",
    "do_nothing",
    "outcome_for",
    "plan_at",
    "plan_pull",
    "select_feedstocks",
]

#: conda-forge's bot stops opening new pull requests once this many of its
#: previous ones sit unmerged (DESIGN.md 3.4.1).
BOT_BACKLOG_CAP = 4


@dataclass(frozen=True)
class NameSources:
    """The two name-resolution layers nobody writes by hand (DESIGN.md 3.2).

    Loaded once for a whole run rather than per feedstock: they are 24 MB
    between them and they say the same thing about every feedstock in a sweep.
    """

    index: PackageIndex
    grayskull: MappingLayer[str]


@dataclass(frozen=True)
class PlannedRecipe:
    """One recipe, read and planned, with the file swage would write.

    `rendered` is the whole recipe rather than the plan's lines, because that
    is what G7 is a claim about (DESIGN.md 5.3): swage owns the comments inside
    a requirements block as much as the dependencies, so "no modification
    needed" is byte identity or it is nothing.
    """

    recipe: Recipe
    upstream: UpstreamMetadata
    plan: RecipePlan
    #: The recipe exactly as swage would push it.
    rendered: str

    @property
    def unchanged(self) -> bool:
        """Whether swage would leave the pull request's recipe alone."""
        return self.rendered == self.recipe.text


@dataclass(frozen=True)
class Acted:
    """What a command did about one pull request, where it did anything.

    Empty from `scan`, which is the whole of what `scan` does. Every field
    overrides or extends what the record would otherwise have said, because
    only the command knows what became of the plan -- `report.build_record`
    takes the outcome as a parameter for the same reason.
    """

    #: Replaces `outcome_for`'s answer where what happened differs from what
    #: was decided: a push that failed, or a label that did not land.
    outcome: Outcome | None = None
    detail: str = ""
    notes: tuple[str, ...] = ()
    stopped: str = ""
    #: The commit swage pushed, where it pushed one.
    pushed: str = ""


#: What a command does about a pull request it has read, planned and judged.
Act = Callable[[FeedstockConfig, BotPullRequest, PlannedRecipe, Verdict], Acted]


def do_nothing(
    config: FeedstockConfig,
    pull: BotPullRequest,
    planned: PlannedRecipe,
    verdict: Verdict,
) -> Acted:
    """`scan`'s action (DESIGN.md 8), and it is the whole of `scan`."""
    return Acted()


def select_feedstocks(
    github: GitHub,
    tree: ConfigTree,
    family: str | None = None,
    feedstock: str | None = None,
    everything: bool = False,
) -> tuple[str, ...]:
    """Which feedstocks this run covers (DESIGN.md 8).

    Naming one feedstock skips discovery entirely, which is what makes
    scanning a single feedstock a two-call operation rather than a sweep. It
    is also not checked against the discovered list: a feedstock swage is
    pointed at directly is one somebody has a reason to look at, and refusing
    it because a team listing does not mention it would be swage second
    guessing the person running it.

    A family is named rather than matched loosely, because scanning nothing
    looks exactly like a clean run -- a typo in `--family` would report zero
    problems across zero feedstocks and mean nothing at all.
    """
    if feedstock is not None:
        return (feedstock,)
    if not everything and family not in tree.families:
        known = ", ".join(sorted(tree.families)) or "none"
        raise ConfigError(tree.root, f"no such family '{family}'; known: {known}")
    found = discover_feedstocks(github)
    if everything:
        return found
    return tuple(name for name in found if _family_of(tree, name) == family)


def _family_of(tree: ConfigTree, feedstock: str) -> str | None:
    """The family owning ``feedstock``, or None where that is ambiguous.

    An ambiguity is a real error and gets reported as one -- but per feedstock,
    when it is scanned, rather than by taking down the selection of every
    other feedstock in the run.
    """
    try:
        family = tree.family_for(feedstock)
    except ConfigError:
        return None
    return family.family if family is not None else None


def consider_feedstock(
    github: GitHub,
    tree: ConfigTree,
    feedstock: str,
    names: NameSources,
    fetch: Fetcher = download,
    act: Act = do_nothing,
) -> FeedstockRecord:
    """Read one feedstock, judge it, and do ``act`` about it."""
    try:
        config = tree.for_feedstock(feedstock)
    except ConfigError as exc:
        # A feedstock with no file of its own can still match two family
        # globs, which load-time validation cannot catch because it only knows
        # the feedstocks that have files (DESIGN.md 4).
        return build_record(feedstock, "failed", stopped=str(exc))
    layers = _config_layers(tree, feedstock, config)

    try:
        pulls = open_bot_pull_requests(github, feedstock)
    except NotFound:
        # A team with no repository behind it. `all-members` is org-wide and
        # nothing in the team object says so, which is one 404 in 487 and
        # cheaper than an exclusion list that would go stale in silence
        # (DESIGN.md 3.4).
        return build_record(
            feedstock,
            "unchanged",
            detail="no feedstock repository",
            config_layers=layers,
        )
    except ForgeError as exc:
        return build_record(feedstock, "failed", stopped=str(exc), config_layers=layers)

    if not pulls:
        return build_record(feedstock, "unchanged", config_layers=layers)

    # Newest first: superseded bumps pile up, and only the newest describes a
    # release anyone wants (DESIGN.md 3.4.1).
    for pull in reversed(pulls):
        record = _consider(github, config, pull, names, layers, len(pulls), fetch, act)
        if record is not None:
            return record

    # Every one of them was a migration -- a rebuild for a new Python, which
    # changes no version and so leaves nothing upstream to reconcile
    # (DESIGN.md 3.4.1). Said out loud rather than reported as a bare
    # UNCHANGED, because a maintainer should not have to wonder whether swage
    # looked at four open pull requests or never saw them.
    return build_record(
        feedstock,
        "unchanged",
        detail=_none_acted_on(len(pulls)),
        config_layers=layers,
        pull_requests=len(pulls),
    )


def plan_pull(
    github: GitHub,
    config: FeedstockConfig,
    pull: BotPullRequest,
    recipe_text: str,
    conda_build_config: str | None,
    names: NameSources,
    fetch: Fetcher = download,
) -> PlannedRecipe:
    """Read one bot pull request's recipe and compute what swage would write.

    The pull request contributes exactly two things over `plan_at`: the ref its
    `.ci_support` is read from, and the previous version's metadata that tells
    an upstream-dropped dependency from a never-upstream one (DESIGN.md 3.3.7).
    """
    return plan_at(
        github,
        config,
        pull.head_sha,
        recipe_text,
        conda_build_config,
        names,
        fetch,
        previous=_previous_upstream(github, config, pull, fetch),
    )


def plan_at(
    github: GitHub,
    config: FeedstockConfig,
    ref: str,
    recipe_text: str,
    conda_build_config: str | None,
    names: NameSources,
    fetch: Fetcher = download,
    previous: UpstreamMetadata | None = None,
) -> PlannedRecipe:
    """Read a recipe at any ref and compute what swage would write for it.

    Keyed on a ref rather than a pull request, because the highest-volume
    comparison available does not involve one. The bespoke tools swage replaces
    act only on open bot pull requests, and a feedstock that still has one is
    usually blocked -- so the live head-to-head samples the pathological tail.
    Their *output*, though, is already published: the `recipe.yaml` on each
    feedstock's default branch is what one of them produced. Rendering that ref
    and diffing needs no pull request and no tool run, and reaches the healthy
    majority the live sample cannot (DESIGN.md 10).

    **`previous` is optional and its absence is not a gap.** It exists to tell
    an upstream-dropped dependency from a never-upstream one; with no pull
    request there is no previous version to compare against, so every removal
    comes back *unclassified* and is therefore kept (DESIGN.md 3.3.7). That is
    the safe direction by construction -- swage does not delete on a guess --
    and it means a main-based rendering can differ from the published recipe by
    *adding* and *changing* lines but never by dropping one it cannot justify.

    One code path with the scan, so what this renders is what swage would push.
    A second implementation would answer "what would swage write" with something
    swage would not write, and would diverge exactly when a precondition or a
    fetch rule changed -- which is when a comparison is trusted most.

    Callers handle `ForgeError`, `PlanError`, `RecipeError` and `UpstreamError`:
    every one of them is a fact about one feedstock, which is why a sweep turns
    them into a FAILED record rather than letting them stop the run.
    """
    # Checked before anything is parsed, because the point is not to start: a
    # feedstock building two artifacts from one recipe would be collapsed into
    # a single wrong answer (DESIGN.md 3.3.5).
    check_preconditions(recipe_text, conda_build_config)
    recipe = read_recipe(recipe_text)
    # Only the recipe can say whether the build floor has to be fetched, and
    # 55 of 60 noarch recipes do not set their own (DESIGN.md 3.5).
    ci_support = (
        ()
        if "python_min" in recipe.context
        else read_ci_support(github, config.feedstock, ref)
    )
    python_min = resolve_python_min(recipe, ci_support)
    upstream = fetch_upstream(recipe, config, github, fetch)
    plan = plan_recipe(
        recipe,
        upstream,
        config,
        build_resolver(config, names.index, names.grayskull),
        python_min,
        previous=previous,
    )
    return PlannedRecipe(
        recipe, upstream, plan, render_recipe(recipe, planned_blocks(plan))
    )


def outcome_for(verdict: Verdict, unchanged: bool) -> Outcome:
    """Which bucket a planned feedstock belongs in, before anything is written.

    The distinction `merge-ready` cannot carry is path B (DESIGN.md 2.1, 5.2):
    with no commit to push there is no CI run, so conda-forge's automerge can
    never be dispatched for that pull request, and swage merging it directly is
    the only thing that ever will. Calling such a feedstock `merge-ready` --
    whose bucket reads "pushed + labeled automerge, awaiting CI" -- states the
    one course of action that is guaranteed not to happen, so it goes to
    `awaiting-ci`, the bucket for a path B candidate swage has not merged yet.
    """
    if verdict.failures:
        return "needs-review"
    return "awaiting-ci" if unchanged else "merge-ready"


def _consider(
    github: GitHub,
    config: FeedstockConfig,
    pull: BotPullRequest,
    names: NameSources,
    layers: Sequence[str],
    open_pulls: int,
    fetch: Fetcher,
    act: Act,
) -> FeedstockRecord | None:
    """One pull request, or None where it is not one swage acts on."""
    feedstock = config.feedstock
    record = _recorder(feedstock, pull, layers, open_pulls)

    try:
        files = read_feedstock(github, feedstock, pull.head_sha)
        if files.recipe is None:
            # v0 is routed, not parsed. It is the single most common condition
            # in the fleet, and surfacing it as a broken file would be the
            # worst available answer (DESIGN.md 3.1).
            return record("needs-migration")

        # A migration -- a rebuild for a new Python -- changes no version, so
        # there is nothing to reconcile that the recipe does not already have.
        previous = previous_version(github, pull, files.recipe)
        if previous is None:
            return None

        planned = plan_pull(
            github, config, pull, files.recipe, files.conda_build_config, names, fetch
        )
    except (ForgeError, PlanError, RecipeError, UpstreamError) as exc:
        return record("failed", stopped=str(exc))

    recipe, upstream, plan = planned.recipe, planned.upstream, planned.plan
    unchanged = planned.unchanged
    verdict = evaluate_gates(
        plan,
        config,
        upstream,
        # Path B is the case where swage changes nothing and merges the pull
        # request itself, and it is the only path G7 applies to.
        path_b=unchanged,
        unchanged=unchanged,
        output_names=[output.name or "" for output in recipe.outputs],
    )

    # Last, and only once the gates have spoken. Nothing above this line writes
    # anywhere, which is what makes `scan` structurally read-only rather than
    # read-only by having remembered not to.
    acted = act(config, pull, planned, verdict)

    return record(
        acted.outcome or outcome_for(verdict, unchanged),
        plan=plan,
        verdict=verdict,
        recipe=recipe,
        upstream=upstream,
        previous=previous,
        upstream_source=upstream_location(recipe, config),
        # Kept out of run.json and written beside it, so a sweep leaves every
        # rendering on disk for DESIGN.md 10's differential validation.
        rendered_recipe=planned.rendered,
        current_recipe=recipe.text,
        detail=acted.detail,
        notes=acted.notes,
        stopped=acted.stopped,
        pushed=acted.pushed,
    )


def _recorder(
    feedstock: str,
    pull: BotPullRequest,
    layers: Sequence[str],
    open_pulls: int,
) -> Callable[..., FeedstockRecord]:
    """Every record about this pull request carries how it was reached."""

    def record(outcome: Outcome, **rest: Any) -> FeedstockRecord:
        return build_record(
            feedstock,
            outcome,
            pull_request=pull.number,
            head=pull.head_sha,
            config_layers=layers,
            pull_requests=open_pulls,
            **rest,
        )

    return record


def _previous_upstream(
    github: GitHub,
    config: FeedstockConfig,
    pull: BotPullRequest,
    fetch: Fetcher,
) -> UpstreamMetadata | None:
    """The metadata for the version the recipe reflected before this bump.

    This is the second fetch DESIGN.md 3.3.7 says telling the two kinds of
    removal apart costs. It comes from the recipe on the branch the pull
    request targets, so it describes the release the recipe currently
    reflects rather than whatever upstream has published since.

    Failure is an answer rather than an error. A yanked release or a deleted
    tag leaves a removal *unclassified*, which is treated as never-upstream
    and therefore kept -- swage does not delete on a guess, so the direction
    the failure falls in is the safe one.
    """
    try:
        base = read_recipe(github.file(pull.repo, RECIPE_V1, pull.base_ref))
        return fetch_upstream(base, config, github, fetch)
    except (ForgeError, RecipeError, UpstreamError):
        return None


def _config_layers(
    tree: ConfigTree, feedstock: str, config: FeedstockConfig
) -> tuple[str, ...]:
    """The files that decided this feedstock's quirks, most specific first."""
    layers = []
    if feedstock in tree.feedstocks:
        layers.append(f"config/feedstocks/{feedstock}.yaml")
    if config.family is not None:
        layers.append(f"config/families/{config.family}.yaml")
    layers.append("config/defaults.yaml")
    return tuple(layers)


def _none_acted_on(count: int) -> str:
    """Why a feedstock with open bot pull requests got none of swage's attention.

    Naming the count is the load-bearing half (DESIGN.md 3.4.1): acting on
    none of four without saying so is how a maintainer discovers months later
    that swage has been ignoring all of them. Four in particular is a signal
    rather than a number -- it is where conda-forge's bot stops filing new
    ones, so the feedstock has stopped receiving updates until somebody clears
    the backlog.
    """
    plural = "" if count == 1 else "s"
    backlog = (
        "; the bot files no more until they clear" if count >= BOT_BACKLOG_CAP else ""
    )
    return f"{count} open bot pull request{plural}, none a version update{backlog}"
