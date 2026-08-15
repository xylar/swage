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
from swage.report import FeedstockRecord, Outcome, RunRecord, build_record
from swage.upstream import UpstreamError

from .consider import NameSources, config_layers, failure_reason, plan_at

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


def readiness(verdict: Verdict) -> Outcome:
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
    """
    if not verdict.failures:
        return "merge-ready"
    if [gate.name for gate in verdict.failures] == ["G6"]:
        return "proposed"
    return "needs-review"


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
    return build_record(
        feedstock,
        "unchanged" if planned.unchanged else readiness(verdict),
        plan=planned.plan,
        verdict=verdict,
        recipe=planned.recipe,
        upstream=planned.upstream,
        upstream_source=upstream_location(planned.recipe, config),
        head=ref,
        config_layers=layers,
        rendered_recipe=planned.rendered,
        current_recipe=planned.recipe.text,
    )
