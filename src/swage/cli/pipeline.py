"""One pipeline for `scan`, `update`, `status` and `audit` (DESIGN.md §12.2).

```
select    -> which feedstocks
locate    -> the pull request to act on, or the default branch
read      -> recipe, .ci_support, config, previous version
declare   -> the release, current and previous
plan      -> Plan
decide    -> Decision
act       -> push, label, comment
record    -> Record
```

The four commands are this function with two switches: whether to write,
which is ``act``, and whether the subject is a pull request or a feedstock,
which is the `Subject`. `scan` and `update` locate the newest bot pull
request that bumps a version; `status` is handed the one an earlier run
acted on; `audit` locates the default branch and plans there, converting a
v0 recipe first (v1 §8.2). Everything after `locate` is the same code, so
no two commands can reach different answers about one feedstock, and a
`run.json` from any of them compares with a `run.json` from any other --
the property v1 §8 protects.

**One feedstock's failure is that feedstock's failure.** A sweep over
several hundred repositories that aborts on the first unreadable recipe is
a sweep nobody can run unattended, so a per-feedstock error becomes a
`failed` record carrying its reason and the loop goes on. Only something
wrong with the run *itself* -- an invalid config tree, a channel that will
not answer -- stops the command, because that is not a fact about any one
feedstock.
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
    "PLANNED_AGAINST_CONVERSION",
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
#: because it is a fact about the config rather than about what a particular
#: run did. Neither the bucket nor a finding can say it: NEEDS REVIEW also
#: holds feedstocks that *were* pushed, and the rung is only the line beside
#: the name where nothing else is.
NOT_PUSHED = "trust: never -- swage never pushes to this feedstock"

#: Said of a pull request swage has a change for and will not offer, because
#: a check says the change itself may be wrong. Not said of one held only for
#: a decision -- that one is pushed, and carries `pushed_note` instead
#: (design-v1.md 5.4). A fact about the change rather than about the run, so
#: it reads the same in a dry run and in a run that wrote: what a reader
#: wants to know is that answering those checks is what releases it.
HELD_BACK = "swage pushes nothing while a check says the change itself may be wrong"

#: Said of a v0 feedstock audited on its default branch, whose recipe swage
#: read by converting one. Without it a `failed` verdict names a
#: `recipe.yaml` the feedstock does not have, and somebody goes looking for a
#: file that exists nowhere yet.
PLANNED_AGAINST_CONVERSION = (
    "this feedstock is still on the old recipe format, so everything below is "
    "about the recipe `swage update --migrate` would convert it into"
)

#: Said beside it where the conversion is one swage read back and found wrong.
#: Everything the plan says is then about a recipe nobody would write, and the
#: note above alone reads as though the conversion were sound: `fiona` comes
#: back with three findings a maintainer could act on, out of a recipe whose
#: build script the converter truncated.
DAMAGED_CONVERSION = (
    "the conversion everything below is about is one swage found wrong: "
    "`swage migrate {feedstock}` says where, and it has to be fixed by hand "
    "before any of it can be written"
)


def pushed_note(sha: str) -> str:
    """Said of a pull request swage has just written to.

    The mirror of `NOT_PUSHED`: whether swage wrote to somebody else's
    repository should not be a thing a maintainer reconstructs from a file.
    """
    return f"pushed {sha[:7]} to the pull request"


@dataclass(frozen=True)
class NameSources:
    """The two name-resolution layers nobody writes by hand (design-v1.md 3.2).

    Loaded once for a whole run rather than per feedstock: they are 24 MB
    between them and they say the same thing about every feedstock in a sweep.
    """

    index: PackageIndex
    grayskull: MappingLayer[str]


@dataclass(frozen=True)
class Subject:
    """What one pass of the pipeline is about (DESIGN.md §12.2).

    A bot pull request's head, or a feedstock's default branch. The second
    has no pull request: nothing waits on CI, nothing is pushed, and a v0
    recipe is converted so it can be planned rather than reported (v1 §8.2).
    """

    ref: str
    pull: BotPullRequest | None = None
    #: How many open bot pull requests the feedstock had where swage looked,
    #: and 0 where it did not look -- `status` is handed one pull request
    #: rather than a listing, and that is the honest answer.
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
    """What a command did about one pull request, where it did anything.

    Empty from `scan`, which is the whole of what `scan` does. Every field
    overrides or extends what the record would otherwise have said, because
    only the command knows what became of the plan.
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
    decided about: the write switch of DESIGN.md §12.2.

    It is handed the decision, which says whether to push and whether to
    label, and the plan, whose findings are what the comment says where it
    does not label (DESIGN.md §9.8). ``migration`` is the conversion to push
    underneath the dependency edit, where the recipe is one swage converted.
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
    # Spelled as the concrete containers rather than `Sequence[str]`, because a
    # bare `str` satisfies `Sequence[str]` and would be taken apart into one
    # "feedstock" per character. This way the type checker refuses it.
    feedstock: list[str] | tuple[str, ...] | None = None,
    everything: bool = False,
) -> tuple[str, ...]:
    """Which feedstocks this run covers (design-v1.md 8).

    Naming feedstocks skips discovery entirely, which is what makes scanning a
    handful a two-call operation rather than a sweep. They are also not checked
    against the discovered list: a feedstock swage is pointed at directly is
    one somebody has a reason to look at, and refusing it because a team
    listing does not mention it would be swage second guessing the person
    running it.

    **Every name given is covered.** `--feedstock` took a single value, so
    argparse kept the last one and dropped the rest in silence: asking for two
    feedstocks acted on one, reported `(1 scanned)`, and never mentioned the
    other -- on `update`, the command that writes to feedstocks swage does not
    own.

    Order is the order the names were given, and duplicates are dropped. A
    sorted list would be tidier and would stop the report reading back the way
    the command was typed, which is what makes a long run easy to follow.

    A family is named rather than matched loosely, because scanning nothing
    looks exactly like a clean run -- a typo in `--family` would report zero
    problems across zero feedstocks and mean nothing at all.
    """
    if feedstock:
        return tuple(dict.fromkeys(feedstock))
    if not everything and family not in tree.families:
        known = ", ".join(sorted(tree.families)) or "none"
        raise ConfigError(tree.root, f"no such family '{family}'; known: {known}")
    found = discover_feedstocks(github)
    # Everything discovered, rather than the subset this run covers: what
    # completion needs to know is which feedstocks exist, and a `--family` run
    # has the whole answer in hand while acting on part of it (design-v1.md 8.3).
    remember(FEEDSTOCKS, found)
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


def config_layers(
    tree: ConfigTree, feedstock: str, config: FeedstockConfig
) -> tuple[str, ...]:
    """The files that decided this feedstock's quirks, most specific first."""
    layers = []
    if feedstock in tree.feedstocks:
        layers.append(f"config/feedstocks/{feedstock}.yaml")
    # Only where it decided something: the file names a few hundred feedstocks
    # and listing it against the rest would say it had a hand in every plan
    # swage makes.
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
    """Locate one feedstock's bot pull request and run the pipeline on it.

    `scan` and `update`: the newest open bot pull request that bumps a
    version is the one acted on, and the difference between the two commands
    is ``act`` (design-v1.md 8).
    """
    try:
        config = tree.for_feedstock(feedstock)
    except ConfigError as exc:
        # A feedstock with no file of its own can still match two family
        # globs, which load-time validation cannot catch because it only knows
        # the feedstocks that have files (design-v1.md 4).
        return record(feedstock, "failed", stopped=str(exc))
    layers = config_layers(tree, feedstock, config)

    if config.unmaintained:
        # Before the pull request listing, because this is the path that
        # writes. An archived feedstock never reaches here -- the listing
        # drops one, since a pull request carries its base repository -- and
        # nothing carries this, so a feedstock waiting on an archiving request
        # would otherwise be updated and pushed to like any other.
        return record(
            feedstock,
            "skipped",
            reason=config.unmaintained,
            config_layers=layers,
        )

    try:
        pulls = open_bot_pull_requests(github, feedstock)
    except NotFound:
        # A team with no repository behind it. `all-members` is org-wide and
        # nothing in the team object says so, which is one 404 in 487 and
        # cheaper than an exclusion list that would go stale in silence
        # (design-v1.md 3.4).
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

    # Every one of them was a migration -- a rebuild for a new Python, which
    # changes no version and so leaves nothing upstream to reconcile
    # (design-v1.md 3.4.1). Said out loud rather than reported as a bare
    # UNCHANGED, because a maintainer should not have to wonder whether swage
    # looked at four open pull requests or never saw them.
    return record(
        feedstock,
        "unchanged",
        reason=_none_acted_on(len(pulls)),
        config_layers=layers,
        pull_requests=len(pulls),
    )


def _none_acted_on(count: int) -> str:
    """Why a feedstock with open bot pull requests got none of swage's attention.

    Naming the count is the load-bearing half (design-v1.md 3.4.1): acting on
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

    None where the subject is a pull request that is not a version update --
    a migration, a rebuild for a new Python -- which changes no version and
    so leaves nothing upstream to reconcile (design-v1.md 3.4.1). The caller
    decides what that means: `scan` moves to the next pull request, `status`
    says the branch has caught up with the one it is following.
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
            # v0 is routed, not parsed. It is the single most common condition
            # in the fleet, and surfacing it as a broken file would be the
            # worst available answer (design-v1.md 3.1).
            return about("needs-migration")
        # Converted first, then planned against -- the recipe the rest of this
        # function reads is one that exists nowhere yet (design-v1.md 7). No
        # `previous_version` for it: that check exists to skip a rebuild which
        # changes no version and so has nothing to reconcile, and a conversion
        # is worth pushing whether or not the version moved.
        try:
            conversion = plan_migration(github, feedstock, subject.ref)
        except MigrationError as exc:
            # `summary` rather than the message's first line, which names the
            # feedstock this report has already named and would spend the one
            # line a sweep gives saying nothing.
            return about("needs-migration", reason=exc.summary, stopped=str(exc))
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
    except NothingToReconcile as exc:
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
        return about("not-read", reason=str(exc), notes=converted_notes)
    except (ForgeError, PlanError, RecipeError, UpstreamError) as exc:
        return about("failed", stopped=str(exc), notes=converted_notes)

    # --- decide
    converted = conversion is not None
    ci = (
        _merge_check(github, pull, plan) if pull is not None and not converted else None
    )
    # A converted recipe gets human eyes, whatever the findings say about its
    # dependencies (design-v1.md 7). They are still found and reported, because
    # the person reviewing the conversion should see what swage made of the
    # dependencies too -- they simply do not decide this.
    decision = decide(
        plan.findings,
        plan.unchanged,
        config,
        ci,
        converted=converted,
        pull_request=pull is not None,
    )
    if decision.outcome == "needs-migration":
        # The bucket's own heading says this feedstock is v0, so repeating it
        # per feedstock would print the same three wrapped lines under 148 of
        # them. It earns its place only where the verdict is something else,
        # and a reader would otherwise go looking for a `recipe.yaml` that
        # does not exist yet.
        converted_notes = ()

    # --- act
    # Last, and only once the decision is made. Nothing above this line writes
    # anywhere, which is what makes every command but `update` structurally
    # read-only rather than read-only by having remembered not to.
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

    **Asked only where the answer would change something.** A feedstock swage
    has a change to push is conda-forge's to merge, not swage's (design-v1.md
    5.1), and one a check has already stopped is nobody's -- so in both cases
    the dozen reads this costs would buy an answer nothing acts on. That is
    also what keeps a sweep over several hundred feedstocks affordable: the
    ones that reach here are the handful with nothing left to decide.

    The trust ladder is not one of those checks. Merging a no-change pull
    request is a person's job on every rung (design-v1.md 5.2.2), so a
    `propose` feedstock with nothing to change is exactly as ready as an
    `auto` one.

    A read that fails is not this feedstock failing. The plan is sound and only
    the merge precondition could not be established, so it comes back as an
    unverified status carrying the reason -- which lands the feedstock in front
    of a human rather than in front of nobody.
    """
    if not plan.unchanged or plan.findings:
        return None
    try:
        return verify_ci(github, pull)
    except ForgeError as exc:
        return CiStatus(reason=f"CI could not be checked: {failure_reason(exc)}")


def failure_reason(exc: ForgeError) -> str:
    """The half of a command failure worth putting on a report line.

    `run_gh` builds its message as the argv it ran, then the program's stderr.
    The argv is the half a reader could reconstruct; the stderr is the half
    only that run knows, so it is what the one line gets -- and it is carried
    on the error rather than parsed back out of the message, because the argv
    stopped being one line the day swage passed a commit body to `gh`.
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

    The pull request contributes exactly two things over `plan_at`: the ref its
    `.ci_support` is read from, and the previous version's metadata that tells
    an upstream-dropped dependency from a never-upstream one (design-v1.md 3.3.7).
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

    Keyed on a ref rather than a pull request, because the highest-volume
    comparison available does not involve one: the `recipe.yaml` on each
    feedstock's default branch is what the tools swage replaces produced, and
    rendering that ref and diffing needs no pull request (design-v1.md 10).
    `audit` and `draft` plan there for the same reason.

    **`previous` is optional and its absence is not a gap.** It exists to tell
    an upstream-dropped dependency from a never-upstream one; with no pull
    request there is no previous version to compare against, so every removal
    comes back *unclassified* and is therefore kept (design-v1.md 3.3.7). That is
    the safe direction by construction -- swage does not delete on a guess --
    and it means a main-based rendering can differ from the published recipe by
    *adding* and *changing* lines but never by dropping one it cannot justify.

    Callers handle `ForgeError`, `PlanError`, `RecipeError` and `UpstreamError`:
    every one of them is a fact about one feedstock, which is why a sweep turns
    them into a `failed` record rather than letting them stop the run.
    """
    # Checked before anything is parsed, because the point is not to start: an
    # output building both an arch and a noarch package would be collapsed into
    # a single wrong answer (design-v1.md 3.3.5).
    check_preconditions(recipe_text)
    recipe = read_recipe(recipe_text)
    # Only the recipe can say whether `.ci_support` has to be fetched, and both
    # kinds of output want something from it. A noarch output needs the build
    # floor, which 55 of 60 noarch recipes do not set themselves (design-v1.md
    # 3.5); an architecture-specific one needs the set of pythons it is built
    # for, because that set is its matrix and nothing in the recipe states it
    # (design-v1.md 3.3.1.1).
    #
    # A noarch output is asked for even where the recipe states its own floor,
    # because the *platform* axis is only in `.ci_support` and nothing in the
    # recipe hints at it: a feedstock built once per platform looks exactly
    # like one built once, right up until a dependency carries a platform
    # marker. Skipping the fetch there would refuse the feedstock with the old
    # message and no way to tell why.
    ci_support = (
        read_ci_support(github, config.feedstock, ref)
        if needs_python_min(recipe) or builds_per_python(recipe)
        else CiSupport()
    )
    python_min = resolve_python_min(recipe, ci_support.files)
    upstream = fetch_upstream(recipe, config, github, fetch, ref)

    # A second source's version, where the rest of the recipe requires one it
    # does not build (design-v1.md 3.6.5). This happens before planning and the
    # metadata is then read again, because the correction changes *which
    # release* an output is reconciled against -- planning first and patching
    # afterwards would leave a plan built against the archive swage had just
    # replaced.
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
    """The metadata for the version the recipe reflected before this bump.

    This is the second fetch design-v1.md 3.3.7 says telling the two kinds of
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
        return fetch_upstream(base, config, github, fetch, pull.base_ref)
    except (ForgeError, RecipeError, UpstreamError):
        return None
