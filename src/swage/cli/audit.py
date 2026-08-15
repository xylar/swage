"""`swage audit` -- what the fleet would do if the bot filed tomorrow (8.2).

Every other command is driven by an open bot pull request, because that is what
there is to act on. The consequence is that swage has never looked at most of
what it maintains: a `scan --all` over 487 feedstocks plans 8 of them and
reports the other 479 as having no open bot pull request, which is true and
says nothing about them.

This one reads each feedstock's default branch and plans it there, so the
question it answers is **readiness**: if the bot filed a pull request for this
feedstock tomorrow, what would swage do with it? Answering that early is the
point, because the config decision that would hold a pull request can be made
before the pull request exists.

**Almost none of this is new.** `plan_at` is keyed on a ref rather than a pull
request precisely so a rendering can be produced without one, and the gates are
the gates. What audit adds is the sweep, and the one place its verdict is read
differently from `update`'s -- see `readiness`.

**It writes nothing**, to a feedstock or to `config/`. Audit produces the list;
`swage draft <feedstock> --apply` writes a config file, one at a time and
deliberately. An audit that filled in the quirks database would be exactly the
failure a required `reason` exists to prevent, at fleet scale.
"""

from __future__ import annotations

import difflib
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from swage.config import ConfigError, ConfigTree
from swage.forge import (
    Fetcher,
    ForgeError,
    GitHub,
    NotFound,
    default_branch,
    download,
    read_feedstock,
    upstream_location,
)
from swage.plan import PlanError, Verdict, evaluate_gates
from swage.recipe import RecipeError
from swage.report import (
    FeedstockRecord,
    Outcome,
    RunRecord,
    build_record,
    compact,
)
from swage.upstream import UpstreamError

from .consider import (
    NameSources,
    PlannedRecipe,
    config_layers,
    failure_reason,
    plan_at,
)

__all__ = ["AUDIT_DESCRIPTIONS", "readiness", "run_audit"]

#: What the buckets mean when the subject is a feedstock rather than a pull
#: request. The vocabulary is unchanged on purpose -- an outcome is a statement
#: about the gates rather than about what was written, so a feedstock audit
#: holds is one a later `scan` must hold too, and two vocabularies would be two
#: things to keep in step.
#:
#: Only the sentences move, and every one of them goes subjunctive: audit has
#: no pull request in front of it and pushes nothing.
AUDIT_DESCRIPTIONS = {
    "merge-ready": "a bot pull request would be pushed and labeled, unattended",
    "proposed": "ready except that it is not blessed -- set `trust` in config/",
    "needs-review": "a decision is needed -- `swage draft <feedstock>` assembles it",
    "unchanged": "the recipe already matches the release it names",
    "needs-migration": "v0 meta.yaml -- `swage migrate` converts it",
}


def readiness(verdict: Verdict, unchanged: bool = False) -> Outcome:
    """Which bucket a planned feedstock is in, asked of a feedstock.

    This is the one place audit reads the gates differently from `update`, and
    the difference is the trust ladder. `outcome_for` distinguishes `propose`
    from `manual` because they mean opposite things about *what happened*: a
    `propose` feedstock is pushed and left for a human to label, and a `manual`
    one is not pushed at all, so calling the second PROPOSED would claim an
    action that did not take place.

    Audit pushes to nothing, for any feedstock, so that reason does not apply
    here -- and collapsing `manual` into NEEDS REVIEW would actively destroy
    what this command is for. `manual` is the default that 333 of 487
    feedstocks sit at, so it would put nearly the whole fleet in the bucket
    that means "a config decision is needed" and bury the feedstocks where one
    genuinely is. The two answers are different work: bless it, or decide
    something about it.

    **A gate that is not the trust ladder outranks having nothing to change.**
    A recipe can match its release exactly and still be held the moment the bot
    files, because what holds it is an unanswered question about the feedstock
    rather than anything about the current text. Reporting that as UNCHANGED
    would hide the one thing this command is for, so `unchanged` only wins once
    nothing but a blessing is outstanding.
    """
    blocking = [gate.name for gate in verdict.failures if gate.name != "G6"]
    if blocking:
        return "needs-review"
    if unchanged:
        # Nothing to push and nothing holding it. Whether it is blessed does
        # not arise, because a blessing decides what happens to a change and
        # there is no change.
        return "unchanged"
    return "proposed" if verdict.failures else "merge-ready"


def _reason(verdict: Verdict) -> str:
    """Which failing gate a held feedstock is named for.

    The first *blocking* one rather than simply the first, and the difference
    is the trust ladder. Gates are evaluated in order and the ladder is
    somewhere in the middle, so a feedstock held by a later gate had the ladder
    printed beside it instead: `google-cloud-redis` is held because swage would
    drop a requirement it cannot account for, and the first real audit reported
    it as "not approved for automatic merging (trust: propose)".

    That is wrong here in a way it is not wrong for `update`. This bucket says
    a decision is needed, and being unblessed is the one failure audit has
    already decided is *not* that decision -- it is what PROPOSED is for. So
    the line names the gate that put the feedstock in this bucket.
    """
    blocking = [gate for gate in verdict.failures if gate.name != "G6"]
    if not blocking:  # pragma: no cover - readiness routes these elsewhere
        return ""
    return compact(blocking[0].detail) if blocking[0].detail else blocking[0].title


def _would_change(current: str, rendered: str) -> str:
    """How much of the recipe an update would touch.

    Every feedstock in `MERGE-READY` and `PROPOSED` is there for the same
    reason, so the reason is in the bucket's heading and repeating it per
    feedstock says nothing -- the first real audit printed "not approved for
    automatic merging (trust: propose)" thirty times in a row. What differs
    between them is how much would change, which is also what says whether a
    feedstock is worth opening first.
    """
    changed = [
        line
        for line in difflib.unified_diff(
            current.splitlines(), rendered.splitlines(), n=0
        )
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
    ]
    added = sum(1 for line in changed if line.startswith("+"))
    return f"+{added} -{len(changed) - added} in the recipe"


def _detail_for(outcome: Outcome, verdict: Verdict, planned: PlannedRecipe) -> str:
    """The one line the summary prints beside this feedstock's name.

    Three buckets, three different things worth saying. A held feedstock is
    named for what holds it; the two that would go through are all there for
    the same reason, so what distinguishes them is the size of the change; and
    a feedstock with nothing to change and nothing holding it says nothing at
    all, which is what keeps a fleet audit from printing several hundred lines
    that mean "fine".
    """
    if outcome == "needs-review":
        return _reason(verdict)
    if outcome == "unchanged":
        return ""
    return _would_change(planned.recipe.text, planned.rendered)


def run_audit(
    github: GitHub,
    tree: ConfigTree,
    feedstocks: Sequence[str],
    names: NameSources,
    command: str = "swage audit",
    fetch: Fetcher = download,
    progress: Callable[[str], None] | None = None,
) -> RunRecord:
    """Plan every feedstock in ``feedstocks`` on its own default branch."""
    started = datetime.now(UTC).isoformat(timespec="seconds")
    records = []
    for feedstock in feedstocks:
        if progress is not None:
            progress(feedstock)
        records.append(_audit(github, tree, feedstock, names, fetch))
    return RunRecord(command=command, started=started, feedstocks=tuple(records))


def _audit(
    github: GitHub,
    tree: ConfigTree,
    feedstock: str,
    names: NameSources,
    fetch: Fetcher,
) -> FeedstockRecord:
    """One feedstock, read where it lives rather than on a pull request."""
    try:
        config = tree.for_feedstock(feedstock)
    except ConfigError as exc:
        return build_record(feedstock, "failed", stopped=str(exc))
    layers = config_layers(tree, feedstock, config)

    try:
        ref = default_branch(github, feedstock)
        files = read_feedstock(github, feedstock, ref)
    except NotFound:
        # A team with no repository behind it -- `all-members` is org-wide and
        # nothing in the team object says so.
        return build_record(
            feedstock,
            "unchanged",
            detail="no feedstock repository",
            config_layers=layers,
        )
    except ForgeError as exc:
        return build_record(
            feedstock, "failed", stopped=failure_reason(exc), config_layers=layers
        )

    if files.recipe is None:
        return build_record(
            feedstock, "needs-migration", head=ref, config_layers=layers
        )

    try:
        # No `previous`: with no pull request there is no version this recipe
        # is moving from, so every removal comes back unclassified and is
        # therefore kept. That is the safe direction by construction -- an
        # audit can report a feedstock as adding or changing lines, never as
        # dropping one it cannot justify.
        planned = plan_at(github, config, ref, files.recipe, names, fetch)
    except (ForgeError, PlanError, RecipeError, UpstreamError) as exc:
        return build_record(
            feedstock, "failed", stopped=str(exc), head=ref, config_layers=layers
        )

    verdict = evaluate_gates(
        planned.plan,
        config,
        planned.upstream,
        # Path B is a pull request swage changed nothing in and a person
        # merges. There is no pull request here, so the byte-identity gate is
        # not asked -- what it would claim is reported as UNCHANGED instead.
        path_b=False,
        unchanged=planned.unchanged,
        output_names=[output.name or "" for output in planned.recipe.outputs],
    )
    outcome = readiness(verdict, planned.unchanged)
    return build_record(
        feedstock,
        outcome,
        plan=planned.plan,
        # Withheld where the only thing the gates have left to say is that this
        # feedstock is not blessed. That is true, and beside a feedstock with
        # nothing to change it reads as the reason it is being reported, which
        # it is not -- and it would print on several hundred lines of a fleet
        # audit that otherwise needs none of them.
        verdict=None if outcome == "unchanged" else verdict,
        recipe=planned.recipe,
        upstream=planned.upstream,
        upstream_source=upstream_location(planned.recipe, config),
        head=ref,
        config_layers=layers,
        detail=_detail_for(outcome, verdict, planned),
        rendered_recipe=planned.rendered,
        current_recipe=planned.recipe.text,
    )
