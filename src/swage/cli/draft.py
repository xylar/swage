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

from pathlib import Path

from swage.cache import cache_root
from swage.config import ConfigTree
from swage.forge import (
    Fetcher,
    ForgeError,
    GitHub,
    download,
    fetch_upstream_texts,
    open_bot_pull_requests,
    read_feedstock,
)
from swage.plan import evaluate_gates
from swage.report.draft import DRAFTS_DIR, Workbench, write_workbench

from .consider import NameSources, plan_at, plan_pull

#: The branch `plan_at` reads where there is no pull request. conda-forge
#: renders every feedstock from `main`, and `compare_published` has read the
#: fleet this way for two releases.
DEFAULT_BRANCH = "main"

__all__ = ["draft_directory", "run_draft"]


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
    caller's to report: unlike a sweep, this is one feedstock and a failure to
    read it is the whole answer.
    """
    config = tree.for_feedstock(feedstock)
    # Newest first, as everywhere: superseded bumps pile up and only the
    # newest describes a release anyone wants (DESIGN.md 3.4.1).
    pulls = open_bot_pull_requests(github, feedstock)
    pull = pulls[-1] if pulls else None
    ref = pull.head_sha if pull is not None else DEFAULT_BRANCH

    files = read_feedstock(github, feedstock, ref)
    if files.recipe is None:
        raise ForgeError(
            f"{feedstock}: has no {ref} recipe.yaml -- it is still a v0 "
            "meta.yaml, and there is nothing here for a v1 config to describe\n"
            "  `swage migrate` is the command for that, once it exists"
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
    texts = fetch_upstream_texts(planned.recipe, config, github, fetch)
    workbench = write_workbench(
        draft_directory(feedstock, root),
        feedstock,
        planned.recipe,
        planned.rendered,
        planned.plan,
        verdict,
        planned.upstream,
        texts,
    )
    return workbench, _apply(tree, feedstock, workbench) if apply else None


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
