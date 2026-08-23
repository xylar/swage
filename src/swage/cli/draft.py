"""`swage draft` -- assemble what a config decision needs (DESIGN.md 8.1).

swage's product is a curated list of pull requests worth looking at, and that
list is exactly as long as config coverage allows. Nine of the thirteen
feedstocks with an open bot pull request have no config at all, and fleet-wide
it is 13 files plus two family globs out of ~490. What makes coverage
expensive is not writing the YAML -- it is the archaeology behind each line of
it: find the sdist, extract it, find the right metadata file, diff it against a
recipe on a branch somewhere. `draft` is what makes that cheap.

**It reads the pull request where there is one, and the default branch where
there is not.** A feedstock with no open bot pull request is the ordinary case
-- 476 of 487 in the last sweep -- and it is exactly the case where somebody
sits down to write a config for the first time. Refusing those would leave the
command unavailable for most of the work it exists to do.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Sequence
from pathlib import Path

from swage.cache import cache_root
from swage.config import ConfigError, ConfigTree, FeedstockConfig, ManualUpstream
from swage.forge import (
    RECIPE_V1,
    BotPullRequest,
    Fetcher,
    ForgeError,
    GitHub,
    download,
    fetch_upstream_texts,
    open_bot_pull_requests,
    read_declaration,
    read_feedstock,
    repository,
)
from swage.plan import PlanError, Verdict, evaluate_gates
from swage.plan.gates import GateResult
from swage.recipe import RecipeError, read_recipe
from swage.report.draft import (
    DRAFTS_DIR,
    FAMILIES_DIR,
    FamilyQuestion,
    Workbench,
    family_summary,
    group_questions,
    write_declaration_workbench,
    write_workbench,
)
from swage.upstream import NothingToReconcile, UpstreamError

from .consider import NameSources, plan_at, plan_pull

__all__ = [
    "draft_directory",
    "family_directory",
    "run_draft",
    "run_family_draft",
    "run_selected_draft",
]


def draft_directory(feedstock: str, root: Path | None = None) -> Path:
    """Where this feedstock's workbench lives."""
    return (root or cache_root()) / DRAFTS_DIR / feedstock


def run_draft(
    github: GitHub,
    tree: ConfigTree,
    feedstock: str,
    names: NameSources,
    execute: bool = False,
    root: Path | None = None,
    fetch: Fetcher = download,
) -> tuple[Workbench, Path | None]:
    """Write the workbench for one feedstock, and optionally the config.

    Returns the workbench and the config file `--execute` wrote, where it wrote
    one. A `ForgeError`, `PlanError`, `RecipeError` or `UpstreamError` is the
    caller's to report: unlike a sweep, this is one feedstock and a failure to
    read it is the whole answer.
    """
    workbench, _ = _draft_one(
        github, tree, feedstock, names, draft_directory(feedstock, root), fetch
    )
    return workbench, _apply(tree, feedstock, workbench) if execute else None


def _draft_one(
    github: GitHub,
    tree: ConfigTree,
    feedstock: str,
    names: NameSources,
    directory: Path,
    fetch: Fetcher,
) -> tuple[Workbench, Verdict]:
    """Assemble one feedstock's workbench into ``directory``.

    Shared by the single-feedstock command and the family sweep, so a
    workbench means the same thing either way and the verdict a family is
    grouped by is the verdict the feedstock's own `FINDINGS.md` explains.
    """
    config = tree.for_feedstock(feedstock)
    # Newest first, as everywhere: superseded bumps pile up and only the
    # newest describes a release anyone wants (DESIGN.md 3.4.1).
    pulls = open_bot_pull_requests(github, feedstock)
    pull = pulls[-1] if pulls else None
    # Asked rather than assumed. This used to read `main`, which is right for
    # almost every conda-forge feedstock and silently wrong for the ones still
    # on `master`: they came back as having no recipe at all, which reads as
    # "this feedstock is v0" and is the one answer a maintainer would act on.
    if pull is None:
        repo = repository(github, feedstock)
        if repo.archived:
            raise ForgeError(
                f"{feedstock}-feedstock is archived on GitHub\n"
                "  it is read-only, so nothing drafted against it could ever "
                "be pushed -- un-archive it first if it is still wanted"
            )
        ref = repo.default_branch
    else:
        ref = pull.head_sha

    files = read_feedstock(github, feedstock, ref)
    if files.recipe is None:
        raise ForgeError(
            f"{feedstock}: has no {ref} recipe.yaml -- it is still a v0 "
            "meta.yaml, and there is nothing here for a v1 config to describe\n"
            f"  `swage migrate {feedstock}` converts it first"
        )

    upstream = config.upstream
    if isinstance(upstream, ManualUpstream):
        # Before planning, because there is no plan: `fetch_upstream` refuses
        # this feedstock outright, and the files are the whole workbench
        # (DESIGN.md 3.6.8).
        return _declaration_workbench(
            github, config, upstream, pull, files.recipe, directory, fetch
        )

    planned = (
        plan_pull(github, config, pull, files.recipe, names, fetch)
        if pull is not None
        else plan_at(github, config, ref, files.recipe, names, fetch)
    )

    verdict = evaluate_gates(
        planned.plan,
        config,
        planned.upstream,
        output_names=[output.name or "" for output in planned.recipe.outputs],
    )
    texts = fetch_upstream_texts(planned.recipe, config, github, fetch, ref)
    workbench = write_workbench(
        directory,
        feedstock,
        planned.recipe,
        planned.rendered,
        planned.plan,
        verdict,
        planned.upstream.primary,
        texts,
    )
    return workbench, verdict


def _declaration_workbench(
    github: GitHub,
    config: FeedstockConfig,
    upstream: ManualUpstream,
    pull: BotPullRequest | None,
    recipe_text: str,
    directory: Path,
    fetch: Fetcher,
) -> tuple[Workbench, Verdict]:
    """The files, and an empty verdict, because no gate was ever evaluated.

    An empty `Verdict` rather than a fabricated pass: the caller reads
    `verdict.failures` to decide whether the feedstock is settled or held, and
    a feedstock swage does not reconcile is neither. Nothing here is waiting on
    a decision -- the decision was already made and is what the config says.
    """
    recipe = read_recipe(recipe_text)
    texts = read_declaration(recipe, config, upstream, fetch)
    previous: dict[str, str] | None = None
    if pull is not None:
        with contextlib.suppress(ForgeError, RecipeError):
            base = read_recipe(github.file(pull.repo, RECIPE_V1, pull.base_ref))
            previous = read_declaration(base, config, upstream, fetch)
    workbench = write_declaration_workbench(
        directory, config.feedstock, recipe, upstream.reason, texts, previous
    )
    return workbench, Verdict(gates=())


def _apply(tree: ConfigTree, feedstock: str, workbench: Workbench) -> Path:
    """Copy the draft into the config tree, refusing to overwrite one.

    Persistence is git, and there is no copy-back protocol: in the
    maintainer's checkout this is an ordinary modified file to read and
    commit. Where the file already exists it gets `.yaml.draft` beside it
    instead -- a config file is hand-written prose as much as data, and
    overwriting one to save a diff is a bad trade.
    """
    draft = (workbench.directory / "config.yaml").read_text(encoding="utf-8")
    target = tree.root / "feedstocks" / f"{feedstock}.yaml"
    if target.exists():
        target = target.with_suffix(".yaml.draft")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(draft, encoding="utf-8")
    return target


def family_directory(family: str, root: Path | None = None) -> Path:
    """Where a family's workbenches and their summary live."""
    return (root or cache_root()) / DRAFTS_DIR / FAMILIES_DIR / family


def run_selected_draft(
    github: GitHub,
    tree: ConfigTree,
    feedstocks: Sequence[str],
    names: NameSources,
    root: Path | None = None,
    fetch: Fetcher = download,
    progress: Callable[[str], None] | None = None,
) -> tuple[Path, tuple[FamilyQuestion, ...]]:
    """Draft several named feedstocks, and say what they ask together.

    The grouping is what a family draft has and a loop over `draft` does not,
    and feedstocks that share a question do not have to share a family to want
    it: six of the maintainer's own -- `esmpy`, `e3sm_diags`, `mpas-analysis`,
    `pyremap`, `zppy-interfaces`, `e3sm-tools` -- hold four package names
    between them and match no glob at all.

    They also have no file in common, and that is a fact about `config/` rather
    than an accident of how they were selected: a family's `add_requirements`
    entry *writes that line into every feedstock the family matches*, so a
    family is a way to share an answer only where the answer is true of all of
    them. The summary says so instead of pointing at a file that would be
    wrong.

    Workbenches land where a single draft puts them, one directory per
    feedstock, with the shared summary above them.
    """
    directory = (root or cache_root()) / DRAFTS_DIR
    return _draft_together(
        github,
        tree,
        feedstocks,
        names,
        directory=directory,
        label=_selection_label(feedstocks),
        config_file=None,
        workbench_root=directory,
        fetch=fetch,
        progress=progress,
    )


def _selection_label(feedstocks: Sequence[str]) -> str:
    """What the summary calls a set of feedstocks nobody has named.

    Their own names while they fit on a line, because that is what a maintainer
    typed and what tells one run's summary from the last one's.
    """
    if len(feedstocks) <= _LABELLED:
        return ", ".join(feedstocks)
    return f"{len(feedstocks)} feedstocks"


#: How many feedstocks a selection names in its summary heading before it
#: gives up and counts them instead.
_LABELLED = 6


def run_family_draft(
    github: GitHub,
    tree: ConfigTree,
    family: str,
    feedstocks: Sequence[str],
    names: NameSources,
    root: Path | None = None,
    fetch: Fetcher = download,
    progress: Callable[[str], None] | None = None,
) -> tuple[Path, tuple[FamilyQuestion, ...]]:
    """Draft every feedstock in ``family``, and say what they ask together.

    **The summary is the reason this exists**, not the saved typing. Across the
    fleet, 174 held feedstocks ask 8 kinds of question between them, and inside
    one family it is usually one or two -- so a maintainer looking at 49
    workbenches is looking at a decision they can take once, in one file. A
    command that only ran `draft` in a loop would leave them to notice that.

    One feedstock's failure is that feedstock's failure, as in every sweep: it
    is named in the summary with its reason and the rest are still assembled.
    """
    directory = family_directory(family, root)
    return _draft_together(
        github,
        tree,
        feedstocks,
        names,
        directory=directory,
        label=family,
        config_file=f"config/families/{family}.yaml",
        workbench_root=directory,
        fetch=fetch,
        progress=progress,
    )


def _draft_together(
    github: GitHub,
    tree: ConfigTree,
    feedstocks: Sequence[str],
    names: NameSources,
    *,
    directory: Path,
    label: str,
    config_file: str | None,
    workbench_root: Path,
    fetch: Fetcher,
    progress: Callable[[str], None] | None,
) -> tuple[Path, tuple[FamilyQuestion, ...]]:
    """Draft each of ``feedstocks`` and write the summary they share."""
    held: dict[str, Sequence[GateResult]] = {}
    settled: list[str] = []
    refused: dict[str, str] = {}

    for feedstock in feedstocks:
        if progress is not None:
            progress(feedstock)
        try:
            _, verdict = _draft_one(
                github, tree, feedstock, names, workbench_root / feedstock, fetch
            )
        except (
            ConfigError,
            ForgeError,
            NothingToReconcile,
            PlanError,
            RecipeError,
            UpstreamError,
        ) as exc:
            refused[feedstock] = failure_reason_of(exc)
            continue
        blocking = [gate for gate in verdict.failures if gate.name != "G6"]
        if blocking:
            held[feedstock] = blocking
        else:
            settled.append(feedstock)

    questions = group_questions(held)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SUMMARY.md").write_text(
        family_summary(label, config_file, questions, sorted(settled), refused),
        encoding="utf-8",
    )
    return directory, questions


def failure_reason_of(exc: Exception) -> str:
    """The first line of why one feedstock could not be drafted."""
    return str(exc).partition("\n")[0]
