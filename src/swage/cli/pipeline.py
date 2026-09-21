"""One pipeline for `scan`, `update`, `status` and `audit` (DESIGN.md §12.2).

``` select -> which feedstocks locate -> the pull request to act on, or the
default branch read -> recipe, .ci_support, config, previous version declare ->
the release, current and previous plan -> Plan decide -> Decision act -> push,
label, comment record -> Record ```

Two switches: whether to write, which is ``act``, and whether the subject is a
pull request or a feedstock, which is the `Subject`. One feedstock's failure is
that feedstock's failure, a `failed` record; only something wrong with the run
itself stops the command.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from swage.config import (
    ConfigError,
    ConfigTree,
    FeedstockConfig,
    ManualUpstream,
    MappingLayer,
    NoUpstream,
)
from swage.forge import (
    RECIPE_V1,
    BotPullRequest,
    CiStatus,
    CiSupport,
    Fetcher,
    ForgeError,
    GitHub,
    NotFound,
    build_resolver,
    correct_source_versions,
    discover_feedstocks,
    download,
    fetch_upstream,
    open_bot_pull_requests,
    previous_version,
    read_ci_support,
    read_feedstock,
    upstream_location,
    verify_ci,
)
from swage.mapping import PackageIndex
from swage.migrate import Migration, MigrationError, plan_migration
from swage.plan import (
    Decision,
    Plan,
    PlanError,
    builds_per_python,
    check_preconditions,
    decide,
    needs_python_min,
    plan_recipe,
    resolve_python_min,
    withheld,
)
from swage.recipe import RecipeError, read_recipe
from swage.run import Outcome, Record, record
from swage.upstream import NothingToReconcile, RecipeUpstream, UpstreamError

from .complete import FEEDSTOCKS, remember
from .unread import declaration_record

__all__ = [
    "BOT_BACKLOG_CAP",
    "DAMAGED_CONVERSION",
    "HELD_BACK",
    "NOT_PUSHED",
    "NO_DISTRIBUTION",
    "PLANNED_AGAINST_CONVERSION",
    "UNMAINTAINED",
    "Act",
    "Acted",
    "NameSources",
    "Subject",
    "config_layers",
    "consider",
    "consider_feedstock",
    "do_nothing",
    "failure_reason",
    "plan_at",
    "plan_pull",
    "pushed_note",
    "select_feedstocks",
]

#: conda-forge's bot stops opening new pull requests once this many of its
#: previous ones sit unmerged (design-v1.md 3.4.1).
BOT_BACKLOG_CAP = 4

#: Said of a pull request swage has a change ready for and will not push,
#: because of the rung: a fact about the config, not the run.
NOT_PUSHED = "trust: never -- swage never pushes to this feedstock"

#: Said of a pull request swage has a change for and will not offer, because a
#: finding withholds it (DESIGN.md §9.7). The same in a dry run and a run that
#: wrote.
HELD_BACK = "swage pushes nothing while a check says the change itself may be wrong"

#: The line beside a feedstock whose config says it packages no python
#: distribution, or that nobody maintains it. The config's own paragraph is the
#: stop.
NO_DISTRIBUTION = "packages no python distribution"
UNMAINTAINED = "config says nobody maintains this feedstock"

#: Said of a v0 feedstock audited on its default branch, whose recipe swage read
#: by converting one.
PLANNED_AGAINST_CONVERSION = (
    "this feedstock is still on the old recipe format, so everything below is "
    "about the recipe `swage update --migrate` would convert it into"
)

#: Said beside it where the conversion is one swage read back and found wrong,
#: so the plan's findings are not mistaken for a sound recipe's.
DAMAGED_CONVERSION = (
    "the conversion everything below is about is one swage found wrong: "
    "`swage migrate {feedstock}` says where, and it has to be fixed by hand "
    "before any of it can be written"
)


def pushed_note(sha: str) -> str:
    """Said of a pull request swage has just written to; the mirror of
    `NOT_PUSHED`.
    """
    return f"pushed {sha[:7]} to the pull request"


@dataclass(frozen=True)
class NameSources:
    """The two name-resolution layers nobody writes by hand (v1 §3.2), loaded
    once per run.
    """

    index: PackageIndex
    grayskull: MappingLayer[str]


@dataclass(frozen=True)
class Subject:
    """What one pass of the pipeline is about (DESIGN.md §12.2): a bot pull
    request's head, or a feedstock's default branch.
    """

    ref: str
    pull: BotPullRequest | None = None
    #: How many open bot pull requests the feedstock had where swage looked, and
    #: 0 where it did not.
    open_pulls: int = 0
    #: Whether a v0 recipe is converted and planned against rather than
    #: reported as needing migration: `update --migrate`, and every audit.
    convert: bool = False
    #: What was noticed about the feedstock before anything was planned, said
    #: on every record about it (audit's hygiene).
    notes: tuple[str, ...] = ()

    @classmethod
    def pull_request(
        cls, pull: BotPullRequest, open_pulls: int = 0, convert: bool = False
    ) -> Subject:
        return cls(pull.head_sha, pull, open_pulls, convert)

    @classmethod
    def default_branch(cls, ref: str, notes: tuple[str, ...] = ()) -> Subject:
        return cls(ref, convert=True, notes=notes)


@dataclass(frozen=True)
class Acted:
    """What a command did about one pull request, where it did anything. Empty
    from `scan`.
    """

    #: Replaces the decision's outcome where what happened differs from what
    #: was decided: a push that failed, or a label that did not land.
    outcome: Outcome | None = None
    reason: str = ""
    notes: tuple[str, ...] = ()
    stopped: str = ""
    #: The commit swage pushed, where it pushed one.
    pushed: str = ""


class Act(Protocol):
    """What a command does about a pull request it has read, planned and
    decided about: the write switch of DESIGN.md §12.2. ``migration`` is the
    conversion to push underneath the dependency edit.
    """

    def __call__(
        self,
        config: FeedstockConfig,
        pull: BotPullRequest,
        plan: Plan,
        decision: Decision,
        migration: Migration | None,
    ) -> Acted: ...


def do_nothing(
    config: FeedstockConfig,
    pull: BotPullRequest,
    plan: Plan,
    decision: Decision,
    migration: Migration | None,
) -> Acted:
    """Record what would happen: every command but `update` (design-v1.md 8)."""
    return Acted()


# --- select --------------------------------------------------------------------


def select_feedstocks(
    github: GitHub,
    tree: ConfigTree,
    family: str | None = None,
    # The concrete containers rather than `Sequence[str]`, which a bare `str`
    # satisfies one character at a time.
    feedstock: list[str] | tuple[str, ...] | None = None,
    everything: bool = False,
) -> tuple[str, ...]:
    """Which feedstocks this run covers (v1 §8).

    Named feedstocks skip discovery and are not checked against it. Order is
    the order given, duplicates dropped. A family is named exactly, because
    scanning nothing looks like a clean run.
    """
    if feedstock:
        return tuple(dict.fromkeys(feedstock))
    if not everything and family not in tree.families:
        known = ", ".join(sorted(tree.families)) or "none"
        raise ConfigError(tree.root, f"no such family '{family}'; known: {known}")
    found = discover_feedstocks(github)
    # Everything discovered, because completion needs every feedstock that
    # exists (v1 §8.3).
    remember(FEEDSTOCKS, found)
    if everything:
        return found
    return tuple(name for name in found if _family_of(tree, name) == family)


def _family_of(tree: ConfigTree, feedstock: str) -> str | None:
    """The family owning ``feedstock``, or None where that is ambiguous, which
    is reported per feedstock when it is scanned.
    """
    try:
        family = tree.family_for(feedstock)
    except ConfigError:
        return None
    return family.family if family is not None else None


def config_layers(
    tree: ConfigTree, feedstock: str, config: FeedstockConfig
) -> tuple[str, ...]:
    """The files that decided this feedstock's quirks, most specific first."""
    layers = []
    if feedstock in tree.feedstocks:
        layers.append(f"config/feedstocks/{feedstock}.yaml")
    # Only where it decided something.
    if feedstock in tree.listed_rungs:
        layers.append("config/trust.yaml")
    if config.family is not None:
        layers.append(f"config/families/{config.family}.yaml")
    layers.append("config/defaults.yaml")
    return tuple(layers)


# --- locate: the pull request --------------------------------------------------


def consider_feedstock(
    github: GitHub,
    tree: ConfigTree,
    feedstock: str,
    names: NameSources,
    fetch: Fetcher = download,
    act: Act = do_nothing,
    migrate: bool = False,
) -> Record:
    """Locate one feedstock's bot pull request and run the pipeline on it: the
    newest open one that bumps a version (v1 §8).
    """
    try:
        config = tree.for_feedstock(feedstock)
    except ConfigError as exc:
        # A feedstock with no file of its own can still match two family globs,
        # which load-time validation cannot see (v1 §4).
        return record(feedstock, "failed", stopped=str(exc))
    layers = config_layers(tree, feedstock, config)

    if config.unmaintained:
        # Before the pull request listing, because this is the path that writes;
        # the listing drops an archived feedstock and nothing carries this one.
        return record(
            feedstock,
            "skipped",
            reason=UNMAINTAINED,
            stopped=config.unmaintained,
            config_layers=layers,
        )

    try:
        pulls = open_bot_pull_requests(github, feedstock)
    except NotFound:
        # A team with no repository behind it (v1 §3.4).
        return record(
            feedstock,
            "unchanged",
            reason="no feedstock repository",
            config_layers=layers,
        )
    except ForgeError as exc:
        return record(feedstock, "failed", stopped=str(exc), config_layers=layers)

    if not pulls:
        return record(feedstock, "unchanged", config_layers=layers)

    # Newest first: superseded bumps pile up, and only the newest describes a
    # release anyone wants (design-v1.md 3.4.1).
    for pull in reversed(pulls):
        subject = Subject.pull_request(pull, len(pulls), convert=migrate)
        considered = consider(github, config, subject, names, layers, fetch, act)
        if considered is not None:
            return considered

    # Every one of them was a migration, which changes no version (v1 §3.4.1).
    # Said out loud rather than reported as a bare UNCHANGED.
    return record(
        feedstock,
        "unchanged",
        reason=_none_acted_on(len(pulls)),
        config_layers=layers,
        pull_requests=len(pulls),
    )


def _none_acted_on(count: int) -> str:
    """Why a feedstock with open bot pull requests got none of swage's attention
    (v1 §3.4.1). Four is where conda-forge's bot stops filing.
    """
    plural = "" if count == 1 else "s"
    backlog = "; the bot files no more" if count >= BOT_BACKLOG_CAP else ""
    return f"{count} open bot pull request{plural}, none a version update{backlog}"


# --- read, declare, plan, decide, act, record ----------------------------------


def consider(
    github: GitHub,
    config: FeedstockConfig,
    subject: Subject,
    names: NameSources,
    layers: tuple[str, ...],
    fetch: Fetcher = download,
    act: Act = do_nothing,
) -> Record | None:
    """The pipeline from `read` on, for one subject.

    None where the subject is a pull request that is not a version update
    (v1 §3.4.1); the caller decides what that means.
    """
    feedstock = config.feedstock
    pull = subject.pull
    about = _recorder(feedstock, subject, layers)

    # --- read
    try:
        files = read_feedstock(github, feedstock, subject.ref)
    except ForgeError as exc:
        return about("failed", stopped=failure_reason(exc))

    recipe_text = files.recipe
    conversion: Migration | None = None
    # Said on every record about a plan against a conversion, and empty on
    # every other path, so what follows carries it unconditionally.
    converted_notes: tuple[str, ...] = ()
    previous: str | None = None
    if recipe_text is None:
        if not subject.convert:
            # v0 is routed, not parsed (v1 §3.1).
            return about("needs-migration")
        # Converted first, then planned against (v1 §7). No `previous_version`:
        # a conversion is worth pushing whether or not the version moved.
        try:
            conversion = plan_migration(github, feedstock, subject.ref)
        except MigrationError as exc:
            return about("needs-migration", stopped=str(exc))
        recipe_text = conversion.recipe_text
        if pull is None:
            converted_notes = (PLANNED_AGAINST_CONVERSION,)
            if conversion.review.damage:
                converted_notes = (
                    *converted_notes,
                    DAMAGED_CONVERSION.format(feedstock=feedstock),
                )
    elif pull is not None:
        # A migration -- a rebuild for a new Python -- changes no version, so
        # there is nothing to reconcile the recipe does not already have.
        try:
            previous = previous_version(github, pull, recipe_text)
        except ForgeError as exc:
            return about("failed", stopped=str(exc))
        if previous is None:
            return None

    # --- declare and plan
    try:
        plan = (
            plan_pull(github, config, pull, recipe_text, names, fetch)
            if pull is not None
            else plan_at(github, config, subject.ref, recipe_text, names, fetch)
        )
    except NothingToReconcile:
        declaration = config.upstream
        if isinstance(declaration, ManualUpstream):
            return declaration_record(
                about,
                github,
                config,
                declaration,
                pull,
                recipe_text,
                fetch,
                notes=converted_notes,
            )
        assert isinstance(declaration, NoUpstream)
        return about(
            "not-read",
            reason=NO_DISTRIBUTION,
            stopped=declaration.reason,
            notes=converted_notes,
        )
    except (ForgeError, PlanError, RecipeError, UpstreamError) as exc:
        return about("failed", stopped=str(exc), notes=converted_notes)

    # --- decide
    converted = conversion is not None
    ci = (
        _merge_check(github, pull, plan) if pull is not None and not converted else None
    )
    # A converted recipe gets human eyes whatever the findings say (v1 §7); they
    # are still reported.
    decision = decide(
        plan.findings,
        plan.unchanged,
        config,
        ci,
        converted=converted,
        pull_request=pull is not None,
    )
    if decision.outcome == "needs-migration":
        # The bucket's own heading already says v0; the note earns its place
        # only under another verdict.
        converted_notes = ()

    # --- act
    # Last, and only once the decision is made. Nothing above this line writes.
    acted = (
        act(config, pull, plan, decision, conversion) if pull is not None else Acted()
    )

    notes = (*converted_notes, *acted.notes)
    if pull is not None:
        # Facts about what this run did with the pull request, and about the
        # change it has in hand; an audit has neither.
        if config.trust == "never" and (converted or not plan.unchanged):
            notes = (NOT_PUSHED, *notes)
        elif acted.pushed:
            notes = (pushed_note(acted.pushed), *notes)
        elif not plan.unchanged and not converted and withheld(plan.findings):
            notes = (HELD_BACK, *notes)

    # --- record
    return about(
        acted.outcome or decision.outcome,
        ci=ci,
        plan=plan,
        decision=decision,
        previous=previous,
        upstream_source=upstream_location(plan.recipe, config),
        reason=acted.reason,
        notes=notes,
        stopped=acted.stopped,
        pushed=acted.pushed,
    )


def _recorder(
    feedstock: str, subject: Subject, layers: tuple[str, ...]
) -> Callable[..., Record]:
    """Every record about this subject carries how it was reached."""

    def about(outcome: Outcome, notes: tuple[str, ...] = (), **rest: Any) -> Record:
        return record(
            feedstock,
            outcome,
            pull_request=subject.pull.number if subject.pull is not None else None,
            head=subject.ref,
            pull_requests=subject.open_pulls,
            config_layers=layers,
            notes=(*subject.notes, *notes),
            **rest,
        )

    return about


def _merge_check(github: GitHub, pull: BotPullRequest, plan: Plan) -> CiStatus | None:
    """Whether CI clears this pull request for the merge only a person can make.

    Asked only where the answer would change something: a change to push is
    conda-forge's to merge (v1 §5.1), and a held one is nobody's. Merging a
    no-change pull request is a person's job on every rung (v1 §5.2.2). A
    read that fails comes back unverified, carrying the reason.
    """
    if not plan.unchanged or plan.findings:
        return None
    try:
        return verify_ci(github, pull)
    except ForgeError as exc:
        return CiStatus(reason=f"CI could not be checked: {failure_reason(exc)}")


def failure_reason(exc: ForgeError) -> str:
    """The half of a command failure worth putting on a report line: the
    program's stderr, carried on the error rather than parsed out of the
    message.
    """
    return " ".join(exc.said.split()) or str(exc).partition("\n")[0]


# --- declare and plan, for one ref ---------------------------------------------


def plan_pull(
    github: GitHub,
    config: FeedstockConfig,
    pull: BotPullRequest,
    recipe_text: str,
    names: NameSources,
    fetch: Fetcher = download,
) -> Plan:
    """Read one bot pull request's recipe and compute what swage would write.

    The pull request contributes the ref `.ci_support` is read from and the
    previous version's metadata (v1 §3.3.7).
    """
    return plan_at(
        github,
        config,
        pull.head_sha,
        recipe_text,
        names,
        fetch,
        previous=_previous_upstream(github, config, pull, fetch),
    )


def plan_at(
    github: GitHub,
    config: FeedstockConfig,
    ref: str,
    recipe_text: str,
    names: NameSources,
    fetch: Fetcher = download,
    previous: RecipeUpstream | None = None,
) -> Plan:
    """Read a recipe at any ref and compute what swage would write for it.

    ``previous`` is optional: without it every removal is unclassified and
    kept (v1 §3.3.7). Callers handle `ForgeError`, `PlanError`,
    `RecipeError` and `UpstreamError`, each a fact about one feedstock.
    """
    # Checked before anything is parsed (v1 §3.3.5).
    check_preconditions(recipe_text)
    recipe = read_recipe(recipe_text)
    # Only the recipe can say whether `.ci_support` has to be fetched: a noarch
    # output needs the floor and the platform axis, an arch output its pythons
    # (DESIGN.md §9.1).
    ci_support = (
        read_ci_support(github, config.feedstock, ref)
        if needs_python_min(recipe) or builds_per_python(recipe)
        else CiSupport()
    )
    python_min = resolve_python_min(recipe, ci_support.files)
    upstream = fetch_upstream(recipe, config, github, fetch, ref)

    # A second source's version, corrected before planning because it changes
    # which release an output is reconciled against (v1 §3.6.5).
    if config.source_versions == "auto":
        corrected, source_edits = correct_source_versions(
            recipe, upstream, config, fetch
        )
        if source_edits:
            recipe = read_recipe(corrected)
            upstream = fetch_upstream(recipe, config, github, fetch, ref)

    return plan_recipe(
        recipe,
        upstream,
        config,
        build_resolver(config, names.index, names.grayskull),
        python_min,
        previous=previous,
        pythons=ci_support.pythons,
        platforms=ci_support.platforms,
        pinned=ci_support.pinned,
    )


def _previous_upstream(
    github: GitHub,
    config: FeedstockConfig,
    pull: BotPullRequest,
    fetch: Fetcher,
) -> RecipeUpstream | None:
    """The metadata for the version the recipe reflected before this bump, read
        from the recipe on the branch the pull request targets (v1 §3.3.7).

    Failure is an answer: the removal is unclassified and kept.
    """
    try:
        base = read_recipe(github.file(pull.repo, RECIPE_V1, pull.base_ref))
        return fetch_upstream(base, config, github, fetch, pull.base_ref)
    except (ForgeError, RecipeError, UpstreamError):
        return None
