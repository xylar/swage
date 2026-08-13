"""Diff what swage would write against what the tools it replaces published.

DESIGN.md 10's differential validation, at the scale the fleet actually
offers. The bespoke tools act only on open bot pull requests, and a feedstock
that still has one is usually blocked on something -- 12 of 487 have one and 3
of those are renderable by a bespoke tool, every one stuck. So the live
head-to-head samples the pathological tail and nothing else.

Their *output* is already published. The `recipe.yaml` on each feedstock's
default branch is what one of them produced, so rendering that same ref with
swage and diffing needs no pull request, no tool run, and reaches the healthy
majority: roughly 99 airflow providers and the google-cloud family.

**A difference is not automatically a defect, and the split is the point.**
Some are conventions swage diverges from deliberately and
`tests/test_plan_corpus.py` already records; those are recognised here and
counted apart, so what is left is the set worth reading. Counting failures
without categorising them is the mistake CLAUDE.md names.

**Reads only, and it renders through `plan_at`** -- the same code path `scan`
uses -- so what it compares is what swage would push rather than a second
implementation's opinion about it.

    pixi run python scripts/compare_published.py
    pixi run python scripts/compare_published.py --family google-cloud --show 5
"""

from __future__ import annotations

import argparse
import difflib
import re
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from swage.cli.scan import NameSources, plan_at
from swage.config import ConfigError, ConfigTree, FeedstockConfig, load_config
from swage.forge import (
    ForgeError,
    GitHub,
    discover_feedstocks,
    load_grayskull_layer,
    load_package_index,
    read_feedstock,
)
from swage.plan import PlanError
from swage.recipe import RecipeError
from swage.upstream import UpstreamError

#: Differences swage makes on purpose, each already recorded as a decision
#: rather than a defect. Matched against the *pair* of lines a change produces,
#: so a rewording counts once rather than as one removal and one addition.
DELIBERATE: tuple[tuple[str, re.Pattern[str]], ...] = (
    # DESIGN.md 3.3.1: both tools' wording read as though the constraint applied
    # only above the named version, when it binds on every python.
    ("marker wording", re.compile(r"#\s*(more restrictive|tightest of upstream)")),
    # DESIGN.md 6 rule 2 puts `python` first in a section.
    ("python first", re.compile(r"^[+-]\s*-\s*python[ $]")),
    # DESIGN.md 6: the extra-block header swage renders for itself.
    ("extra header", re.compile(r"#\s*(from the \S+ extra|\S+ extra$)")),
    # DESIGN.md 6: `# start`/`# end` markers, and the caption that replaced an
    # empty pair.
    ("expansion markers", re.compile(r"#\s*(start|end) \S+|needs nothing extra")),
    # DESIGN.md 3.2, and the single commonest difference in the fleet -- 38 of
    # the 50 google-cloud feedstocks. grayskull drops the extra from
    # `google-api-core[grpc]`, so recipes maintained beside it carry a bare
    # `google-api-core-grpc` whose missing constraint was deliberate: it kept
    # the two tools from overwriting each other. swage resolves the requirement
    # properly and constrains the line, retiring the workaround.
    ("grayskull workaround retired", re.compile(r"^[+-]\s*-\s*\S+-grpc(\s|$)")),
)


@dataclass
class Outcome:
    """What comparing one feedstock came to."""

    feedstock: str
    family: str
    verdict: str
    changed: list[str] = field(default_factory=list)
    detail: str = ""

    @property
    def deliberate_only(self) -> bool:
        return bool(self.changed) and all(
            any(pattern.search(line) for _, pattern in DELIBERATE)
            for line in self.changed
        )


def _targets(
    github: GitHub, tree: ConfigTree, families: set[str]
) -> Iterator[tuple[str, str, FeedstockConfig]]:
    for feedstock in discover_feedstocks(github):
        try:
            family = tree.family_for(feedstock)
            if family is None or family.family not in families:
                continue
            yield feedstock, family.family, tree.for_feedstock(feedstock)
        except ConfigError:
            continue


def compare(
    github: GitHub,
    feedstock: str,
    family: str,
    config: FeedstockConfig,
    names: NameSources,
) -> Outcome:
    """Render this feedstock's default branch and diff it against itself."""
    try:
        files = read_feedstock(github, feedstock, "main")
    except ForgeError as exc:
        return Outcome(feedstock, family, "unreadable", detail=str(exc))
    if files.recipe is None:
        # v0 is out of scope here: swage routes it to migration rather than
        # rendering it (DESIGN.md 3.1).
        return Outcome(feedstock, family, "v0 meta.yaml")

    try:
        planned = plan_at(
            github, config, "main", files.recipe, files.conda_build_config, names
        )
    except (ForgeError, PlanError, RecipeError, UpstreamError) as exc:
        return Outcome(feedstock, family, "refused", detail=str(exc).splitlines()[0])

    if planned.rendered == files.recipe:
        return Outcome(feedstock, family, "identical")
    changed = [
        line
        for line in difflib.unified_diff(
            files.recipe.splitlines(), planned.rendered.splitlines(), n=0
        )
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
    ]
    return Outcome(feedstock, family, "differs", changed=changed)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--family",
        action="append",
        help="repeatable; defaults to every family with a config file",
    )
    parser.add_argument("--limit", type=int, help="stop after this many feedstocks")
    parser.add_argument(
        "--show", type=int, default=6, help="feedstocks whose diff to print in full"
    )
    parser.add_argument("--config-root", type=Path, default=Path("config"))
    args = parser.parse_args(argv)

    tree = load_config(args.config_root)
    families = set(args.family or tree.families)
    github = GitHub()
    names = NameSources(load_package_index(), load_grayskull_layer())

    targets = list(_targets(github, tree, families))
    if args.limit:
        targets = targets[: args.limit]
    print(f"{len(targets)} feedstocks in {', '.join(sorted(families))}", flush=True)

    outcomes: list[Outcome] = []
    for index, (feedstock, family, config) in enumerate(targets, start=1):
        print(
            f"\r\033[K  [{index}/{len(targets)}] {feedstock}", end="", file=sys.stderr
        )
        outcomes.append(compare(github, feedstock, family, config, names))
    print("\r\033[K", end="", file=sys.stderr)

    identical = [o for o in outcomes if o.verdict == "identical"]
    differs = [o for o in outcomes if o.verdict == "differs"]
    deliberate = [o for o in differs if o.deliberate_only]
    substantive = [o for o in differs if not o.deliberate_only]
    other = [o for o in outcomes if o.verdict not in {"identical", "differs"}]

    print("\n=== summary ===")
    print(f"  {len(identical):5d}  identical to the published recipe")
    print(f"  {len(deliberate):5d}  differ only by conventions swage chose on purpose")
    print(f"  {len(substantive):5d}  differ in ways worth reading")
    for verdict in sorted({o.verdict for o in other}):
        named = [o for o in other if o.verdict == verdict]
        print(f"  {len(named):5d}  {verdict}")

    for outcome in other:
        if outcome.detail:
            print(f"\n  {outcome.verdict}: {outcome.feedstock}")
            print(f"      {outcome.detail[:110]}")

    if substantive:
        print(f"\n=== {len(substantive)} to read ===")
        for outcome in substantive[: args.show]:
            print(f"\n  {outcome.feedstock}  ({outcome.family})")
            for line in outcome.changed[:20]:
                print(f"    {line}")
        if len(substantive) > args.show:
            rest = ", ".join(o.feedstock for o in substantive[args.show :])
            print(f"\n  ...and {len(substantive) - args.show} more: {rest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
