"""`swage draft`: assemble what a config decision needs (v1 §8.1).

It reads the pull request where there is one and the default branch where there
is not, which is the ordinary case and the one where a config is first written.
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
from swage.plan import Finding, PlanError, rung_sentence
from swage.recipe import RecipeError, read_recipe
from swage.run.draft import (
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

from .pipeline import NameSources, plan_at, plan_pull

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
    apply: bool = False,
    root: Path | None = None,
    fetch: Fetcher = download,
) -> tuple[Workbench, Path | None]:
    """Write the workbench for one feedstock, and optionally the config.

    Returns the workbench and the config file `--apply` wrote, where it wrote
    one. A `ForgeError`, `PlanError`, `RecipeError` or `UpstreamError` is the
    caller's to report.
    """
    workbench, _ = _draft_one(
        github, tree, feedstock, names, draft_directory(feedstock, root), fetch
    )
    return workbench, _apply(tree, feedstock, workbench) if apply else None


def _draft_one(
    github: GitHub,
    tree: ConfigTree,
    feedstock: str,
    names: NameSources,
    directory: Path,
    fetch: Fetcher,
) -> tuple[Workbench, tuple[Finding, ...]]:
    """Assemble one feedstock's workbench into ``directory``, for the single
    command and the family sweep alike.
    """
    config = tree.for_feedstock(feedstock)
    if config.unmaintained:
        raise ForgeError(
            f"{feedstock}-feedstock is not maintained\n"
            f"  config/feedstocks/{feedstock}.yaml says: {config.unmaintained}\n"
            "  a workbench is for deciding what a recipe should say, and "
            "nobody is going to act on this one"
        )
    # Newest first, as everywhere: superseded bumps pile up and only the
    # newest describes a release anyone wants (design-v1.md 3.4.1).
    pulls = open_bot_pull_requests(github, feedstock)
    pull = pulls[-1] if pulls else None
    # Asked rather than assumed: a feedstock still on `master` came back as
    # having no recipe.
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
            f"  `swage update --migrate --feedstock {feedstock}` converts it "
            "first, on its open version pull request"
        )

    upstream = config.upstream
    if isinstance(upstream, ManualUpstream):
        # Before planning, because there is no plan: the files are the whole
        # workbench (v1 §3.6.8).
        return _declaration_workbench(
            github, config, upstream, pull, files.recipe, directory, fetch
        )

    plan = (
        plan_pull(github, config, pull, files.recipe, names, fetch)
        if pull is not None
        else plan_at(github, config, ref, files.recipe, names, fetch)
    )

    texts = fetch_upstream_texts(plan.recipe, config, github, fetch, ref)
    workbench = write_workbench(
        directory,
        feedstock,
        plan,
        plan.findings,
        plan.upstream.primary,
        texts,
        rung=rung_sentence(config),
    )
    return workbench, plan.findings


def _declaration_workbench(
    github: GitHub,
    config: FeedstockConfig,
    upstream: ManualUpstream,
    pull: BotPullRequest | None,
    recipe_text: str,
    directory: Path,
    fetch: Fetcher,
) -> tuple[Workbench, tuple[Finding, ...]]:
    """The files, and no findings, because no check was ever evaluated: the
    decision is what the config says.
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
    return workbench, ()


def _apply(tree: ConfigTree, feedstock: str, workbench: Workbench) -> Path:
    """Copy the draft into the config tree, refusing to overwrite one: an
    existing file gets `.yaml.draft` beside it.
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

    They have no file in common, and the summary says so: a family's
    `add_requirements` entry writes that line into every member. Workbenches
    land one directory per feedstock, the summary above them.
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
    """What the summary calls a set of feedstocks nobody has named: their own
    names while they fit on a line.
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
    """Draft every feedstock in ``family``, and say what they ask together
    (v1 §8.1). One feedstock's failure is named in the summary and the rest
    are still assembled.
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
    held: dict[str, Sequence[Finding]] = {}
    settled: list[str] = []
    refused: dict[str, str] = {}

    for feedstock in feedstocks:
        if progress is not None:
            progress(feedstock)
        try:
            _, findings = _draft_one(
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
        if findings:
            held[feedstock] = findings
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
