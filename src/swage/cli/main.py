"""The ``swage`` command line (v1 §8; DESIGN.md §12).

Exit codes are part of the contract (v1 §9.1): ``0`` nothing needs you, ``1``
items need review, ``2`` swage itself failed. Nothing but argparse is imported
until a command runs (§12.3); each command function imports what it runs.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable, Sequence
from enum import IntEnum
from pathlib import Path
from typing import TYPE_CHECKING

from swage import __version__

from .complete import SHELLS, completing

if TYPE_CHECKING:
    from swage.config import AddedRequirement, ConfigTree
    from swage.forge import ReadRecorder

__all__ = ["main"]

_CONFIG_ROOT_ENV = "SWAGE_CONFIG_ROOT"

#: Commands registered so `--help` names them before they work. Empty, and kept
#: as the way to add the next one.
_PLANNED: dict[str, tuple[str, str]] = {}

#: Where `audit` keeps the archives it fetched, so a second audit pays for the
#: recipes that changed rather than for all 490 again (design-v1.md 8.2).
ARCHIVES = "archives"

#: Where `audit` keeps what GitHub answered its read-only calls with, so a
#: later audit can be replayed against the same fleet (design-v1.md 8.2).
READS = "reads"


#: How far back `status` reads swage's own runs when nobody says (v1 §8).
DEFAULT_SINCE = "7d"

#: How many readings of the fleet `trust` requires agreement from, by default.
#: Readings rather than days, because the fleet moves between sweeps (v1 §8.4).
TRUST_READINGS = 3


class ExitCode(IntEnum):
    OK = 0
    NEEDS_REVIEW = 1
    FAILED = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="swage",
        description="Maintenance automation for conda-forge feedstocks at scale.",
    )
    parser.add_argument("--version", action="version", version=f"swage {__version__}")
    parser.add_argument(
        "--config-root",
        type=Path,
        default=None,
        metavar="DIR",
        help=(
            f"quirks database directory (default: ${_CONFIG_ROOT_ENV}, else the "
            "nearest config/ directory at or above the working directory)"
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    config_parser = subparsers.add_parser(
        "config",
        help="validate the quirks database and show what it resolves to",
        description=(
            "Read config/ and print what it resolves to. Writes nothing and "
            "touches no feedstock, so it is the cheapest way to check a config "
            "change parses and lands where you meant it to."
        ),
        epilog="example:  swage config --feedstock microsoft-kiota-http",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    config_parser.add_argument(
        "-f",
        "--feedstock",
        metavar="NAME",
        action="extend",
        nargs="+",
        help="show the config resolved for these feedstocks instead of a summary",
    )

    scan_parser = subparsers.add_parser(
        "scan",
        help="read-only; report what would change",
        description=(
            "Plan the feedstocks that have an open bot pull request and report "
            "what swage would do with each. Writes nothing. Most feedstocks "
            "have no bot pull request at any given moment -- `swage audit` is "
            "the one that looks at a feedstock regardless."
        ),
        epilog="example:  swage scan --family google-cloud",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # Exactly one, and required (v1 §8). `-m` rather than `-F` for the family,
    # so the two selectors are not a shift key apart on the command that writes;
    # `-a` is left free for `--all`.
    scope = scan_parser.add_mutually_exclusive_group(required=True)
    scope.add_argument(
        "-f",
        "--feedstock",
        metavar="NAME",
        action="extend",
        nargs="+",
        help="scan these feedstocks",
    )
    scope.add_argument(
        "-m", "--family", metavar="NAME", help="scan one family's feedstocks"
    )
    scope.add_argument(
        "--all", action="store_true", help="scan every feedstock you maintain"
    )
    scan_parser.add_argument(
        "--quiet",
        action="store_true",
        help="do not report progress while the sweep runs",
    )

    audit_parser = subparsers.add_parser(
        "audit",
        help="read-only; what the fleet would do if the bot filed tomorrow",
        description=(
            "Read each feedstock where it lives and ask what swage would do if "
            "the bot filed a pull request for it tomorrow. Writes nothing. "
            "NEEDS REVIEW is the config backlog: `swage draft <feedstock>` "
            "assembles what deciding one needs. Fetches an sdist per feedstock, "
            "so the whole fleet takes about twenty minutes."
        ),
        epilog="example:  swage audit --family microsoft-kiota",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # Required, as for `scan`: audit plans every feedstock it is given.
    audit_scope = audit_parser.add_mutually_exclusive_group(required=True)
    audit_scope.add_argument(
        "-f",
        "--feedstock",
        metavar="NAME",
        action="extend",
        nargs="+",
        help="audit these feedstocks",
    )
    audit_scope.add_argument(
        "-m", "--family", metavar="NAME", help="audit one family's feedstocks"
    )
    audit_scope.add_argument(
        "--all", action="store_true", help="audit every feedstock you maintain"
    )
    audit_parser.add_argument(
        "--quiet",
        action="store_true",
        help="do not report progress while the sweep runs",
    )
    # Not offered on any command that writes: a stale read is harmless to a
    # report and not to a push.
    audit_parser.add_argument(
        "--cached",
        action="store_true",
        help=(
            "read the fleet from the last audit's cache instead of GitHub, "
            "which reports it as it was then rather than as it is now"
        ),
    )

    update_parser = subparsers.add_parser(
        "update",
        help="render, push, and label; --dry-run to rehearse",
        description=(
            "The only command that writes to a feedstock. --dry-run makes it "
            "`scan` with different wording, reaching the same verdict for "
            "every feedstock -- so what the dry run says it would do is what "
            "the same command without it does."
        ),
        epilog="example:  swage update --feedstock globus-cli",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # `--all` reads only what no earlier update read at its current commit
    # (DESIGN.md §12.1, §16); naming a feedstock reads it whatever came before.
    update_scope = update_parser.add_mutually_exclusive_group(required=True)
    update_scope.add_argument(
        "-f",
        "--feedstock",
        metavar="NAME",
        action="extend",
        nargs="+",
        help="update these feedstocks",
    )
    update_scope.add_argument(
        "-m", "--family", metavar="NAME", help="update one family's feedstocks"
    )
    update_scope.add_argument(
        "--all",
        action="store_true",
        help=(
            "update every feedstock you maintain whose bot pull request has "
            "something new since an earlier update read it"
        ),
    )
    update_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would be pushed and labeled, and write nothing",
    )
    update_parser.add_argument(
        "--migrate",
        action="store_true",
        help=(
            "also convert feedstocks still on the old recipe format, as a "
            "separate commit before the dependency update"
        ),
    )
    update_parser.add_argument(
        "--quiet",
        action="store_true",
        help="do not report progress while the run proceeds",
    )

    explain_parser = subparsers.add_parser(
        "explain",
        help="why did swage decide that?",
        description=(
            "Render one feedstock out of a run that already happened, rather "
            "than recomputing it. The question is almost never what swage "
            "would do now but why it did that, at 03:00, against upstream that "
            "has since moved."
        ),
        epilog="example:  swage explain google-ads --json",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    explain_parser.add_argument("feedstock", metavar="FEEDSTOCK")
    explain_parser.add_argument(
        "--from-run",
        type=Path,
        default=None,
        metavar="DIR",
        help="explain out of an older run instead of the most recent",
    )
    explain_parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="print the stored record verbatim, as run.json holds it",
    )

    status_parser = subparsers.add_parser(
        "status",
        help="read-only; what became of the pull requests swage acted on",
        description=(
            "Read swage's own earlier runs and ask GitHub what became of every "
            "pull request they pushed to or left waiting. Writes nothing. This "
            "is the report to read the morning after."
        ),
        epilog="example:  swage status --since 36h",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # No selector: the subject is the pull requests swage's own earlier runs
    # touched, and the window is the only dial.
    status_parser.add_argument(
        "--since",
        default=DEFAULT_SINCE,
        metavar="WINDOW",
        help=(
            "how far back to read swage's own runs, as 7d or 36h "
            f"(default: {DEFAULT_SINCE})"
        ),
    )
    status_parser.add_argument(
        "--quiet",
        action="store_true",
        help="do not report progress while the run proceeds",
    )

    trust_parser = subparsers.add_parser(
        "trust",
        help="read-only; which feedstocks the recorded audits say have earned a rung",
        description=(
            "Read swage's own fleet audits and report which feedstocks had "
            "approval outstanding and nothing else in every one of them. "
            "Writes nothing, and reads nothing but the runs already on disk. "
            "Grouped by what one argument could cover, because a batch in "
            "config/trust.yaml is promoted on one reason."
        ),
        epilog="example:  swage trust --since 30d",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # Readings rather than a window (v1 §8.4).
    trust_parser.add_argument(
        "--readings",
        type=int,
        default=TRUST_READINGS,
        metavar="N",
        help=(
            "how many of the most recent readings of the fleet must agree "
            f"(default: {TRUST_READINGS})"
        ),
    )

    draft_parser = subparsers.add_parser(
        "draft",
        help="assemble what a config decision for a feedstock needs",
        description=(
            "Assemble everything deciding a feedstock's config needs into one "
            "directory: the recipe, what swage would write, the diff, the "
            "upstream metadata, and FINDINGS.md -- which names what is "
            "undecided, quotes the evidence, and shows which config key "
            "answers it. Writes only under the cache directory. Name several "
            "feedstocks, or a whole family with --family, and swage reports "
            "the questions they ask between them; both refuse --apply, "
            "because what several feedstocks share is usually one decision and "
            "not one config file each."
        ),
        epilog=(
            "examples:\n"
            "  swage draft microsoft-kiota-http\n"
            "  swage draft esmpy pyremap mpas-analysis\n"
            "  swage draft --family google-cloud\n"
            "\n"
            "config keys are described in docs/configuration.md"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # One or the other. A family drafts every feedstock in it and reports the
    # questions they share, which is a different gesture from asking about one.
    draft_scope = draft_parser.add_mutually_exclusive_group(required=True)
    draft_scope.add_argument(
        "feedstock",
        metavar="FEEDSTOCK",
        nargs="*",
        default=[],
        help="draft these feedstocks, and report the questions they share",
    )
    draft_scope.add_argument(
        "-m", "--family", metavar="NAME", help="draft every feedstock in one family"
    )
    draft_parser.add_argument(
        "--apply",
        action="store_true",
        help="also copy the drafted config into your config directory",
    )
    draft_parser.add_argument(
        "--quiet",
        action="store_true",
        help="do not report progress while several feedstocks are drafted",
    )

    migrate_parser = subparsers.add_parser(
        "migrate",
        help="convert a feedstock's recipe from the old format to the new one",
        description=(
            "Convert recipe/meta.yaml into recipe/recipe.yaml and set "
            "conda-forge.yml to build it, reporting what that would produce "
            "and writing nothing. A converted recipe is always reviewed by "
            "hand: conversion is imperfect, and swage checks that it can read "
            "the result back but cannot check that the result is right."
        ),
        epilog="example:  swage migrate calver",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    migrate_parser.add_argument(
        "feedstock",
        metavar="FEEDSTOCK",
        action="extend",
        nargs="+",
        help="the feedstocks to convert",
    )

    completion_parser = subparsers.add_parser(
        "completion",
        help="print the hook that completes swage in your shell, or refresh its names",
        description=(
            "Print the code that makes your shell ask swage what comes next on "
            "every TAB: swage's commands and options, the feedstocks you "
            "maintain, and the families in your config. The names come from "
            "files swage caches: any run that reads the fleet fills them in, "
            "and --refresh fills them in on demand without running anything "
            "else."
        ),
        epilog=(
            "examples:\n"
            "  swage completion bash > ~/.local/share/bash-completion/"
            "completions/swage\n"
            "  swage completion zsh > ~/.zfunc/_swage\n"
            "  swage completion --refresh\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # One or the other: a run that did both would write shell code to the same
    # stdout it reported the refresh on.
    completion_scope = completion_parser.add_mutually_exclusive_group(required=True)
    completion_scope.add_argument(
        "shell",
        metavar="SHELL",
        nargs="?",
        choices=SHELLS,
        help=f"print the hook for this shell ({', '.join(SHELLS)})",
    )
    completion_scope.add_argument(
        "--refresh",
        action="store_true",
        help="ask GitHub which feedstocks you maintain, so completion offers them",
    )

    for name, (help_text, phase) in _PLANNED.items():
        subparsers.add_parser(name, help=f"{help_text} [phase {phase}]")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    # The shell asking what comes next, checked before argcomplete is imported
    # (DESIGN.md §12.3).
    if completing():
        from .complete import autocomplete

        autocomplete(parser)
    args = parser.parse_args(argv)

    if args.command in _PLANNED:
        phase = _PLANNED[args.command][1]
        print(
            f"swage {args.command} is not implemented yet (planned for phase {phase})",
            file=sys.stderr,
        )
        return ExitCode.FAILED

    # `explain` reads a run directory and nothing else, so a config typo cannot
    # be the answer to "why did swage do that".
    if args.command == "explain":
        return _explain(args)

    # Printing the hook reads nothing, so it works outside a config tree;
    # `--refresh` wants the tree and falls through.
    if args.command == "completion" and not args.refresh:
        from .complete import hook

        print(hook(args.shell), end="")
        return ExitCode.OK

    from swage.config import ConfigError, load_config

    from .complete import FAMILIES, remember

    try:
        tree = load_config(_config_root(args.config_root))
    except ConfigError as exc:
        print(f"swage: {exc}", file=sys.stderr)
        return ExitCode.FAILED
    for note in tree.notes:
        print(f"swage: {note}", file=sys.stderr)

    # Every command loads the tree, so every command keeps completion's family
    # names current.
    remember(FAMILIES, tree.families)

    if args.command == "scan":
        return _scan(tree, args)

    if args.command == "update":
        return _update(tree, args)

    if args.command == "audit":
        return _audit(tree, args)

    if args.command == "status":
        return _status(tree, args)

    if args.command == "trust":
        return _trust(tree, args)

    if args.command == "draft":
        if args.family:
            return _draft_family(tree, args)
        # One feedstock answers with its workbench; several is a question about
        # what they share (v1 §8.1).
        return (
            _draft(tree, args)
            if len(args.feedstock) == 1
            else _draft_several(tree, args)
        )

    if args.command == "migrate":
        return _migrate(args)

    if args.command == "completion":
        return _refresh_names(tree)

    if args.feedstock:
        for name in dict.fromkeys(args.feedstock):
            _print_feedstock(tree, name)
    else:
        _print_summary(tree)
    return ExitCode.OK


def _audit(tree: ConfigTree, args: argparse.Namespace) -> int:
    """`swage audit` (v1 §8.2; DESIGN.md §12.2), which plans every feedstock it
    is given and writes nothing.
    """
    from swage.cache import cache_root
    from swage.config import ConfigError
    from swage.forge import (
        ForgeError,
        GitHub,
        ReadRecorder,
        caching,
        download,
        load_grayskull_layer,
        load_package_index,
        run_gh,
    )
    from swage.run import (
        render_summary,
        run_directory,
        write_declarations,
        write_recipes,
        write_run,
    )

    from .audit import AUDIT_DESCRIPTIONS, run_audit
    from .pipeline import NameSources, select_feedstocks

    # Every read GitHub answers is kept, whether or not this run replays one,
    # so an ordinary audit leaves a cache the next one can be pinned against.
    reads = ReadRecorder(run_gh, cache_root() / READS, replay=args.cached)
    github = GitHub(reads)
    try:
        names = NameSources(load_package_index(), load_grayskull_layer())
        feedstocks = select_feedstocks(
            github, tree, args.family, args.feedstock, args.all
        )
    except (ConfigError, ForgeError) as exc:
        print(f"swage: {exc}", file=sys.stderr)
        return ExitCode.FAILED

    if not feedstocks:
        print(f"swage: {_nothing_selected(args)}", file=sys.stderr)
        return ExitCode.FAILED

    live = not args.quiet and sys.stderr.isatty()
    run = run_audit(
        github,
        tree,
        feedstocks,
        names,
        command=_command_line(args),
        fetch=caching(download, cache_root() / ARCHIVES),
        progress=_progress("auditing") if live else None,
        # A config file for a feedstock the sweep did not cover only means
        # something when the sweep was the whole fleet.
        complete=args.all,
    )

    directory = run_directory()
    write_run(run, directory)
    write_recipes(run, directory)
    write_declarations(run, directory)
    if live:
        print("\r\033[K", end="", file=sys.stderr)
    print(
        render_summary(
            run,
            directory,
            descriptions=AUDIT_DESCRIPTIONS,
            counted="audited",
            named=args.feedstock or (),
        ),
        end="",
    )
    if args.cached:
        print(_from_cache(reads))
    return ExitCode.NEEDS_REVIEW if run.needs_review else ExitCode.OK


def _from_cache(reads: ReadRecorder) -> str:
    """Say that this run read a stored fleet, and how much of it was stored,
    because a replayed audit reports the fleet as it was.
    """
    total = reads.replayed + reads.fetched
    if not total:
        return "  read nothing from GitHub"
    if not reads.fetched:
        return (
            f"  read {reads.replayed} GitHub responses from the cache, and "
            "none from GitHub -- this is the fleet as it was when the cache "
            "was recorded"
        )
    return (
        f"  read {reads.replayed} of {total} GitHub responses from the cache "
        f"and {reads.fetched} from GitHub -- the cached part is the fleet as "
        "it was when the cache was recorded"
    )


def _scan(tree: ConfigTree, args: argparse.Namespace) -> int:
    """`swage scan` (v1 §8), which reads and reports and writes nothing.

    A feedstock swage could not read is a `1`; a channel that will not
    answer is a `2` (v1 §9.1).
    """
    from swage.config import ConfigError
    from swage.forge import ForgeError, GitHub, load_grayskull_layer, load_package_index
    from swage.run import (
        render_summary,
        run_directory,
        write_declarations,
        write_recipes,
        write_run,
    )

    from .pipeline import NameSources, select_feedstocks
    from .scan import SCAN_DESCRIPTIONS, run_scan

    github = GitHub()
    try:
        names = NameSources(load_package_index(), load_grayskull_layer())
        feedstocks = select_feedstocks(
            github, tree, args.family, args.feedstock, args.all
        )
    except (ConfigError, ForgeError) as exc:
        print(f"swage: {exc}", file=sys.stderr)
        return ExitCode.FAILED

    if not feedstocks:
        print(f"swage: {_nothing_selected(args)}", file=sys.stderr)
        return ExitCode.FAILED

    # Progress is one line rewritten in place, so only a terminal gets it.
    live = not args.quiet and sys.stderr.isatty()
    run = run_scan(
        github,
        tree,
        feedstocks,
        names,
        command=_command_line(args),
        progress=_progress("scanning") if live else None,
    )

    directory = run_directory()
    write_run(run, directory)
    # Every recipe swage planned, beside the one it would replace (v1 §9).
    write_recipes(run, directory)
    write_declarations(run, directory)
    if live:
        # Erase the progress line rather than leaving it above the report.
        print("\r\033[K", end="", file=sys.stderr)
    print(
        render_summary(
            run,
            directory,
            descriptions=SCAN_DESCRIPTIONS,
            named=args.feedstock or (),
        ),
        end="",
    )
    return ExitCode.NEEDS_REVIEW if run.needs_review else ExitCode.OK


def _draft_family(tree: ConfigTree, args: argparse.Namespace) -> int:
    """`swage draft --family` (design-v1.md 8.1), which assembles and groups.

    `--apply` is refused: applying is a per-feedstock gesture, taken once a
    decision exists, and a family's answer is usually one family file.
    """
    from swage.cache import cache_root
    from swage.config import ConfigError
    from swage.forge import (
        ForgeError,
        GitHub,
        caching,
        download,
        load_grayskull_layer,
        load_package_index,
    )
    from swage.run import render_family

    from .draft import run_family_draft
    from .pipeline import NameSources, select_feedstocks

    if args.apply:
        print(
            "swage: --apply drafts one feedstock at a time\n"
            "  a family's answer usually belongs in one family file rather "
            "than in a config file per feedstock -- read SUMMARY.md first",
            file=sys.stderr,
        )
        return ExitCode.FAILED

    github = GitHub()
    try:
        names = NameSources(load_package_index(), load_grayskull_layer())
        feedstocks = select_feedstocks(github, tree, args.family)
    except (ConfigError, ForgeError) as exc:
        print(f"swage: {exc}", file=sys.stderr)
        return ExitCode.FAILED

    if not feedstocks:
        print(f"swage: {_nothing_selected(args)}", file=sys.stderr)
        return ExitCode.FAILED

    live = not args.quiet and sys.stderr.isatty()
    directory, questions = run_family_draft(
        github,
        tree,
        args.family,
        feedstocks,
        names,
        fetch=caching(download, cache_root() / ARCHIVES),
        progress=_progress("drafting") if live else None,
    )
    if live:
        print("\r\033[K", end="", file=sys.stderr)
    print(render_family(directory, questions), end="")
    return ExitCode.OK


def _draft_several(tree: ConfigTree, args: argparse.Namespace) -> int:
    """`swage draft A B C` (design-v1.md 8.1), which groups what they ask.

    `--apply` is refused for the reason `--family` refuses it.
    """
    from swage.cache import cache_root
    from swage.config import ConfigError
    from swage.forge import (
        ForgeError,
        GitHub,
        caching,
        download,
        load_grayskull_layer,
        load_package_index,
    )
    from swage.run import render_family

    from .draft import run_selected_draft
    from .pipeline import NameSources

    if args.apply:
        print(
            "swage: --apply drafts one feedstock at a time\n"
            "  what several feedstocks share is usually one decision rather "
            "than a config file each -- read SUMMARY.md first",
            file=sys.stderr,
        )
        return ExitCode.FAILED

    github = GitHub()
    try:
        names = NameSources(load_package_index(), load_grayskull_layer())
    except (ConfigError, ForgeError) as exc:
        print(f"swage: {exc}", file=sys.stderr)
        return ExitCode.FAILED

    feedstocks = list(dict.fromkeys(args.feedstock))
    live = not args.quiet and sys.stderr.isatty()
    directory, questions = run_selected_draft(
        github,
        tree,
        feedstocks,
        names,
        fetch=caching(download, cache_root() / ARCHIVES),
        progress=_progress("drafting") if live else None,
    )
    if live:
        print("\r\033[K", end="", file=sys.stderr)
    print(render_family(directory, questions, scope="any of them"), end="")
    return ExitCode.OK


def _draft(tree: ConfigTree, args: argparse.Namespace) -> int:
    """`swage draft` (v1 §8.1), which reads and writes a workbench.

    Exit `0` even where the feedstock is held, since something is known to
    be undecided; `2` means the workbench could not be assembled.
    """
    from swage.config import ConfigError
    from swage.forge import ForgeError, GitHub, load_grayskull_layer, load_package_index
    from swage.plan import PlanError
    from swage.recipe import RecipeError
    from swage.run import render_workbench
    from swage.upstream import NothingToReconcile, UpstreamError

    from .draft import run_draft
    from .pipeline import NameSources

    github = GitHub()
    try:
        names = NameSources(load_package_index(), load_grayskull_layer())
        workbench, applied = run_draft(
            github, tree, args.feedstock[0], names, apply=args.apply
        )
    except (
        ConfigError,
        ForgeError,
        NothingToReconcile,
        PlanError,
        RecipeError,
        UpstreamError,
    ) as exc:
        print(f"swage: {exc}", file=sys.stderr)
        return ExitCode.FAILED

    print(render_workbench(workbench, applied), end="")
    return ExitCode.OK


def _migrate(args: argparse.Namespace) -> int:
    """`swage migrate` (v1 §7), which converts and writes nothing.

    No config is consulted: a conversion is about the recipe's format.
    Pushing one is `swage update --migrate`. Exit `1` where any feedstock was
    refused.
    """
    from swage.forge import (
        ForgeError,
        GitHub,
        open_bot_pull_requests,
        read_feedstock,
        repository,
    )
    from swage.migrate import MigrationError, plan_migration
    from swage.run import render_migration, render_refusal

    github = GitHub()
    refused = False
    for index, feedstock in enumerate(dict.fromkeys(args.feedstock)):
        if index:
            print()
        try:
            repo = repository(github, feedstock)
            if repo.archived:
                raise MigrationError(
                    f"{feedstock}-feedstock is archived on GitHub\n"
                    "  it is read-only, so a conversion could never be pushed "
                    "to it -- un-archive it first if it is still wanted"
                )
            migration = plan_migration(github, feedstock, repo.default_branch)
            # Looked up after the conversion, because the pull request only
            # decides the report's last line (v1 §7).
            pulls = open_bot_pull_requests(github, feedstock)
            converted = bool(
                pulls
                and read_feedstock(github, feedstock, pulls[-1].head_sha).recipe
                is not None
            )
        except MigrationError as exc:
            print(render_refusal(feedstock, str(exc)), end="")
            refused = True
        except ForgeError as exc:
            print(f"swage: {exc}", file=sys.stderr)
            return ExitCode.FAILED
        else:
            print(render_migration(migration, pulls, converted), end="")
    return ExitCode.NEEDS_REVIEW if refused else ExitCode.OK


def _trust(tree: ConfigTree, args: argparse.Namespace) -> int:
    """`swage trust` (v1 §8.4), which reads only what earlier runs left. A window
    with nothing in it is not a failure.
    """
    from swage.run import all_runs, earned, fleet_states, render_trust

    if args.readings < 1:
        print("swage: --readings takes a whole number of readings", file=sys.stderr)
        return ExitCode.FAILED

    states, skipped = fleet_states(all_runs(), args.readings)
    if not states:
        print(
            "swage: no fleet audits on this machine -- "
            "`swage audit --all` records what this reads"
        )
        return ExitCode.OK

    print(render_trust(states, earned(states, tree), skipped, readings=args.readings))
    return ExitCode.OK


def _status(tree: ConfigTree, args: argparse.Namespace) -> int:
    """`swage status` (v1 §8), which closes the loop and writes nothing.

    A window with no runs in it is said rather than printed as an empty
    summary. Its own run is recorded like any other.
    """
    from datetime import UTC, datetime

    from swage.forge import ForgeError, GitHub, load_grayskull_layer, load_package_index
    from swage.run import (
        render_summary,
        run_directory,
        runs_since,
        write_declarations,
        write_recipes,
        write_run,
    )

    from .pipeline import NameSources
    from .status import (
        STATUS_DESCRIPTIONS,
        followed,
        parse_since,
        read_runs,
        run_status,
    )

    try:
        window = parse_since(args.since)
    except ValueError as exc:
        print(f"swage: --since {exc}", file=sys.stderr)
        return ExitCode.FAILED

    cutoff = datetime.now(UTC) - window
    runs, skipped = read_runs(runs_since(cutoff))
    if skipped:
        # Counted, never silent and never one line each.
        plural = "" if skipped == 1 else "s"
        print(
            f"swage: skipped {skipped} run{plural} in this window that this "
            "swage cannot read; rerun the command that made them to replace them",
            file=sys.stderr,
        )
    if not runs:
        print(f"swage: no runs in the last {args.since} to follow up on")
        return ExitCode.OK

    # Checked before anything is loaded: a fleet with nothing in flight is the
    # common case, and an empty summary would read as a failed render.
    if not followed(runs):
        print(
            f"swage: nothing to follow up on -- the runs in the last {args.since} "
            "pushed to no pull request and left none waiting"
        )
        return ExitCode.OK

    github = GitHub()
    try:
        names = NameSources(load_package_index(), load_grayskull_layer())
    except ForgeError as exc:
        print(f"swage: {exc}", file=sys.stderr)
        return ExitCode.FAILED

    directory = run_directory()
    live = not args.quiet and sys.stderr.isatty()
    run = run_status(
        github,
        tree,
        runs,
        names,
        command=_command_line(args),
        progress=_progress("following") if live else None,
    )

    write_run(run, directory)
    write_recipes(run, directory)
    write_declarations(run, directory)
    if live:
        print("\r\033[K", end="", file=sys.stderr)
    print(
        render_summary(
            run, directory, descriptions=STATUS_DESCRIPTIONS, counted="followed up"
        ),
        end="",
    )
    return ExitCode.NEEDS_REVIEW if run.needs_review else ExitCode.OK


def _update(tree: ConfigTree, args: argparse.Namespace) -> int:
    """`swage update` (v1 §8), which is `scan` plus writes.

    A dry run reaches the same outcome for every feedstock. Clones live under
    this run's directory, beside the record of why swage pushed.
    """
    from swage.config import ConfigError
    from swage.forge import (
        CLONES,
        ForgeError,
        Git,
        GitHub,
        load_grayskull_layer,
        load_package_index,
    )
    from swage.run import (
        all_runs,
        render_summary,
        run_directory,
        write_declarations,
        write_recipes,
        write_run,
    )

    from .pipeline import NameSources, select_feedstocks
    from .update import (
        ALL_DESCRIPTIONS,
        DRY_RUN_BANNER,
        DRY_RUN_DESCRIPTIONS,
        UPDATE_DESCRIPTIONS,
        already_read,
        run_update,
        unread,
    )

    github = GitHub()
    try:
        names = NameSources(load_package_index(), load_grayskull_layer())
        feedstocks = select_feedstocks(
            github, tree, args.family, args.feedstock, args.all
        )
    except (ConfigError, ForgeError) as exc:
        print(f"swage: {exc}", file=sys.stderr)
        return ExitCode.FAILED

    if not feedstocks:
        print(f"swage: {_nothing_selected(args)}", file=sys.stderr)
        return ExitCode.FAILED

    directory = run_directory()
    live = not args.quiet and sys.stderr.isatty()
    run = run_update(
        github,
        Git(root=directory / CLONES),
        tree,
        feedstocks,
        names,
        write=not args.dry_run,
        command=_command_line(args),
        progress=_progress("updating") if live else None,
        migrate=args.migrate,
        skip=unread(already_read(all_runs())) if args.all else None,
    )

    write_run(run, directory)
    write_recipes(run, directory)
    write_declarations(run, directory)
    if live:
        print("\r\033[K", end="", file=sys.stderr)
    print(
        render_summary(
            run,
            directory,
            descriptions={
                **(DRY_RUN_DESCRIPTIONS if args.dry_run else UPDATE_DESCRIPTIONS),
                **(ALL_DESCRIPTIONS if args.all else {}),
            },
            banner=DRY_RUN_BANNER if args.dry_run else "",
            named=args.feedstock or (),
        ),
        end="",
    )
    return ExitCode.NEEDS_REVIEW if run.needs_review else ExitCode.OK


def _explain(args: argparse.Namespace) -> int:
    """`swage explain` (v1 §9.2), rendered from the record. The exit code is the
    one the run gave this feedstock.
    """
    from swage.run import ReportError

    from .explain import explain_feedstock, resolve_run

    try:
        directory = resolve_run(args.from_run)
        rendered, record = explain_feedstock(args.feedstock, directory, args.as_json)
    except ReportError as exc:
        print(f"swage: {exc}", file=sys.stderr)
        return ExitCode.FAILED

    print(rendered)
    return ExitCode.NEEDS_REVIEW if record.needs_review else ExitCode.OK


def _refresh_names(tree: ConfigTree) -> int:
    """`swage completion --refresh`, which fills in what completion offers; the
    families were written when the tree loaded, so this adds the GitHub call.
    """
    from swage.forge import ForgeError, GitHub, discover_feedstocks

    from .complete import FEEDSTOCKS, names_directory, remember

    github = GitHub()
    try:
        feedstocks = discover_feedstocks(github)
    except ForgeError as exc:
        print(f"swage: {exc}", file=sys.stderr)
        return ExitCode.FAILED

    remember(FEEDSTOCKS, feedstocks)
    print(
        f"completion now offers {len(feedstocks)} feedstocks and "
        f"{len(tree.families)} families, from {names_directory()}"
    )
    return ExitCode.OK


def _progress(verb: str) -> Callable[[str], None]:
    """One rewritten line on stderr, so the report on stdout stays pipeable."""

    def report(feedstock: str) -> None:
        print(f"\r\033[K  {verb} {feedstock}", end="", file=sys.stderr, flush=True)

    return report


def _nothing_selected(args: argparse.Namespace) -> str:
    if args.family is not None:
        return f"no feedstock you maintain belongs to the family '{args.family}'"
    return "you maintain no conda-forge feedstocks"  # pragma: no cover


def _command_line(args: argparse.Namespace) -> str:
    """The invocation, as the report's header prints it back, every named
    feedstock included.
    """
    parts = [f"swage {args.command}"]
    # `status` has no selector to print. Its subject is the pull requests
    # earlier runs touched, and the window is what narrows it.
    if args.command == "status":
        return f"swage status --since {args.since}"
    if args.command == "trust":
        return f"swage trust --readings {args.readings}"
    if args.feedstock is not None:
        named = " ".join(dict.fromkeys(args.feedstock))
        parts.append(f"--feedstock {named}")
    elif args.family is not None:
        parts.append(f"--family {args.family}")
    else:
        parts.append("--all")
    # `--cached` is recorded because a replayed audit read a stored fleet;
    # `--quiet` is not, because it changed the display and not the run.
    if args.command == "audit" and args.cached:
        parts.append("--cached")
    # Both change what the run did, so both belong in the header.
    if args.command == "update" and args.migrate:
        parts.append("--migrate")
    if args.command == "update" and args.dry_run:
        parts.append("--dry-run")
    return " ".join(parts)


def _config_root(explicit: Path | None) -> Path | None:
    if explicit is not None:
        return explicit
    from_env = os.environ.get(_CONFIG_ROOT_ENV)
    return Path(from_env) if from_env else None


def _print_summary(tree: ConfigTree) -> None:
    print(f"config root:  {tree.root}")
    print(f"trust floor:  {tree.defaults.trust}")
    print(f"name map:     {len(tree.name_map)} entries")
    print(f"families:     {len(tree.families)}")
    for family in tree.families.values():
        print(f"  {family.family:<24} {family.match.feedstock}")
    print(f"feedstocks:   {len(tree.feedstocks)}")
    for name in sorted(tree.feedstocks):
        resolved = tree.for_feedstock(name)
        owner = resolved.family or "-"
        print(f"  {name:<40} family={owner:<20} trust={resolved.trust}")


def _print_additions(label: str, added: Sequence[AddedRequirement]) -> None:
    """One line per conda-forge-only requirement, with why it is there
    (v1 §4).
    """
    for requirement in added:
        print(f"{label}:".ljust(19) + f"{requirement.text}  ({requirement.source})")
        if requirement.reason:
            print(f"{'':19}{requirement.reason}")


def _print_feedstock(tree: ConfigTree, feedstock: str) -> None:
    """Every key that applies to one feedstock, as the layers resolved it."""
    resolved = tree.for_feedstock(feedstock)
    print(f"feedstock:         {resolved.feedstock}")
    print(f"family:            {resolved.family or '-'}")
    print(f"trust:             {resolved.trust}")
    print(f"upstream:          {resolved.upstream or '-'}")
    print(f"removals:          {resolved.removals}")
    print(f"dynamic deps:      {resolved.dynamic_dependencies}")
    print(f"test matrix:       {resolved.test_matrix}")
    if resolved.extras_as_outputs is not None:
        extras = resolved.extras_as_outputs
        print(f"extras as outputs: suffix={extras.suffix}")
        print(f"  supported:       {', '.join(extras.supported) or '-'}")
        print(f"  skip:            {', '.join(extras.skip) or '-'}")
    for name, output in resolved.outputs.items():
        print(f"output {name}:")
        if output.upstream is not None:
            print(f"  upstream:        {output.upstream}")
        print(f"  core:            {output.run.core}")
        print(f"  extras:          {', '.join(output.run.extras) or '-'}")
        print(f"  skip:            {', '.join(output.run.skip) or '-'}")
    for section, added in resolved.add_requirements.every.items():
        _print_additions(f"add to {section}", added)
    for named, sections in resolved.add_requirements.per_output.items():
        for section, added in sections.items():
            _print_additions(f"add to {named} {section}", added)
    for name, override in resolved.constraints.items():
        print(f"constraint:        {name} {override.bound}")
        print(f"{'':19}{override.reason}")
    for name, override in resolved.temporary_constraints.items():
        print(f"temporary:         {name} {override.bound}")
        print(f"{'':19}{override.reason}")
    for name, entry in resolved.run_constraints.items():
        print(f"run constraint:    {name} tracks extra {entry.extra or '-'}")
    for variant in resolved.variant_conditions:
        print(f"build variant:     if: {variant.condition}")
        print(f"{'':19}around {', '.join(variant.packages)}")
        print(f"{'':19}{variant.reason}")
    if resolved.retire:
        print(f"retire:            {', '.join(sorted(resolved.retire))}")
    print(f"recipe owned:      {', '.join(resolved.recipe_owned.names) or '-'}")
    print(f"  functions:       {', '.join(resolved.recipe_owned.functions) or '-'}")
    print(f"  variables:       {', '.join(resolved.recipe_owned.variables) or '-'}")
    print(f"default build:     {', '.join(resolved.default_build_requires) or '-'}")
    if resolved.link_map:
        print(f"link map:          {len(resolved.link_map)} libraries")
    if resolved.cmake_map:
        print(f"cmake map:         {len(resolved.cmake_map)} package names")
    print("name map layers:")
    for layer in resolved.name_map.layers:
        print(f"  {layer.source} ({len(layer.entries)} entries)")
    print("embedded extras layers:")
    for extras_layer in resolved.embedded_extras.layers:
        print(f"  {extras_layer.source} ({len(extras_layer.entries)} entries)")
