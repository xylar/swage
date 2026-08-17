"""The ``swage`` command line (DESIGN.md 8).

Exit codes are part of the contract, because every command is meant to be safe
to run from cron (DESIGN.md 9.1): ``0`` nothing needs you, ``1`` items need
review, ``2`` swage itself failed.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from enum import IntEnum
from pathlib import Path

from swage import __version__
from swage.cache import cache_root
from swage.config import ConfigError, ConfigTree, load_config
from swage.forge import (
    CLONES,
    ForgeError,
    Git,
    GitHub,
    caching,
    default_branch,
    discover_feedstocks,
    download,
    load_grayskull_layer,
    load_package_index,
)
from swage.migrate import MigrationError, plan_migration
from swage.plan import PlanError
from swage.recipe import RecipeError
from swage.report import (
    ReportError,
    render_family,
    render_migration,
    render_refusal,
    render_summary,
    render_workbench,
    run_directory,
    runs_since,
    write_recipes,
    write_run,
)
from swage.upstream import UpstreamError

from .audit import AUDIT_DESCRIPTIONS, run_audit
from .complete import (
    FAMILIES,
    FEEDSTOCKS,
    SHELLS,
    completion_script,
    names_directory,
    remember,
)
from .consider import NameSources, select_feedstocks
from .draft import run_draft, run_family_draft
from .explain import explain_feedstock, resolve_run
from .scan import SCAN_DESCRIPTIONS, run_scan
from .status import (
    DEFAULT_SINCE,
    STATUS_DESCRIPTIONS,
    followed,
    parse_since,
    read_runs,
    run_status,
)
from .update import (
    DRY_RUN_BANNER,
    DRY_RUN_DESCRIPTIONS,
    UPDATE_DESCRIPTIONS,
    run_update,
)

__all__ = ["main"]

_CONFIG_ROOT_ENV = "SWAGE_CONFIG_ROOT"

#: Commands from DESIGN.md 8 that later phases fill in, with the phase that
#: does it. Registering them now keeps ``swage --help`` honest about the shape
#: of the tool without pretending they work. Empty now that `migrate` is real,
#: and kept because the mechanism is the honest way to add the next one.
_PLANNED: dict[str, tuple[str, str]] = {}

#: Where `audit` keeps the archives it fetched, so a second audit pays for the
#: recipes that changed rather than for all 490 again (DESIGN.md 8.2).
ARCHIVES = "archives"


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
    # Exactly one, and required: `scan` with no selector would sweep every
    # feedstock the maintainer has, which is a real operation against GitHub
    # and not something to trip into by typing the command with no arguments.
    scope = scan_parser.add_mutually_exclusive_group(required=True)
    scope.add_argument(
        "--feedstock",
        metavar="NAME",
        action="extend",
        nargs="+",
        help="scan these feedstocks",
    )
    scope.add_argument("--family", metavar="NAME", help="scan one family's feedstocks")
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
    # The same required selector `scan` has, and for a stronger reason: audit
    # plans every feedstock it is given rather than only the ones with an open
    # bot pull request, so a bare `swage audit` would be an unintended sweep an
    # order of magnitude slower than the one that rule already prevents.
    audit_scope = audit_parser.add_mutually_exclusive_group(required=True)
    audit_scope.add_argument(
        "--feedstock",
        metavar="NAME",
        action="extend",
        nargs="+",
        help="audit these feedstocks",
    )
    audit_scope.add_argument(
        "--family", metavar="NAME", help="audit one family's feedstocks"
    )
    audit_scope.add_argument(
        "--all", action="store_true", help="audit every feedstock you maintain"
    )
    audit_parser.add_argument(
        "--quiet",
        action="store_true",
        help="do not report progress while the sweep runs",
    )

    update_parser = subparsers.add_parser(
        "update",
        help="render, push, and label; dry run without --execute",
        description=(
            "The only command that writes to a feedstock, and only with "
            "--execute. Without it this is `scan` with different wording, "
            "reaching the same verdict for every feedstock -- so what the dry "
            "run says it would do is what --execute does."
        ),
        epilog="example:  swage update --feedstock globus-cli --execute",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # No `--all`, deliberately, and DESIGN.md 8's synopsis says so: `scan` and
    # `audit` read, and sweeping every feedstock is what reading is for. A
    # fleet-wide *write* is not a gesture that should have a spelling this
    # short. The volume control for a large family is `--execute`.
    update_scope = update_parser.add_mutually_exclusive_group(required=True)
    update_scope.add_argument(
        "--feedstock",
        metavar="NAME",
        action="extend",
        nargs="+",
        help="update these feedstocks",
    )
    update_scope.add_argument(
        "--family", metavar="NAME", help="update one family's feedstocks"
    )
    update_parser.add_argument(
        "--execute",
        action="store_true",
        help="actually push and label; without it nothing is written",
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
    # No selector, unlike `scan` and `update`. There is nothing here to sweep:
    # the subject is the pull requests swage's own earlier runs touched, which
    # is a handful whatever the fleet is, and the window is the only dial.
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

    draft_parser = subparsers.add_parser(
        "draft",
        help="assemble what a config decision for a feedstock needs",
        description=(
            "Assemble everything deciding a feedstock's config needs into one "
            "directory: the recipe, what swage would write, the diff, the "
            "upstream metadata, and FINDINGS.md -- which names what is "
            "undecided, quotes the evidence, and shows which config key "
            "answers it. Writes only under the cache directory. --family "
            "drafts a whole family and reports the questions they share; it "
            "refuses --execute, because a family's answer usually belongs in one "
            "family file rather than in a config file per feedstock."
        ),
        epilog=(
            "examples:\n"
            "  swage draft microsoft-kiota-http\n"
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
        "feedstock", metavar="FEEDSTOCK", nargs="?", help="draft one feedstock"
    )
    draft_scope.add_argument(
        "--family", metavar="NAME", help="draft every feedstock in one family"
    )
    # `--execute` is the spelling every command that writes uses, and this one
    # writes -- into your own config tree rather than into a feedstock, but a
    # maintainer moving between `draft` and `update` should not have to
    # remember which word each one wanted. `--apply` still works, and is what
    # earlier runs and any notes taken from them will say.
    draft_parser.add_argument(
        "--execute",
        "--apply",
        dest="execute",
        action="store_true",
        help="also copy the drafted config into your config directory",
    )
    draft_parser.add_argument(
        "--quiet",
        action="store_true",
        help="do not report progress while a family is drafted",
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
        help="print a shell completion script, or refresh the names it offers",
        description=(
            "Print a completion script for your shell, which completes swage's "
            "commands and options, the feedstocks you maintain, and the "
            "families in your config. The names come from files swage caches: "
            "any run that reads the fleet fills them in, and --refresh fills "
            "them in on demand without running anything else."
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
    # One or the other: printing a script and going to GitHub for names are
    # different gestures, and a run that did both would write a script to the
    # same stdout it reported the refresh on.
    completion_scope = completion_parser.add_mutually_exclusive_group(required=True)
    completion_scope.add_argument(
        "shell",
        metavar="SHELL",
        nargs="?",
        choices=SHELLS,
        help=f"print the completion script for this shell ({', '.join(SHELLS)})",
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
    args = parser.parse_args(argv)

    if args.command in _PLANNED:
        phase = _PLANNED[args.command][1]
        print(
            f"swage {args.command} is not implemented yet (planned for phase {phase})",
            file=sys.stderr,
        )
        return ExitCode.FAILED

    # `explain` reads a run directory and nothing else -- no config, no
    # network, no recipe. Loading the quirks database first would make an
    # unrelated typo in it the answer to "why did swage do that".
    if args.command == "explain":
        return _explain(args)

    # Printing a completion script reads nothing at all, and must not: a
    # maintainer installing completion is standing wherever they were, and a
    # script that will not print outside a config tree is one they conclude is
    # broken. `--refresh` does want the tree, and falls through.
    if args.command == "completion" and not args.refresh:
        print(completion_script(args.shell, parser), end="")
        return ExitCode.OK

    try:
        tree = load_config(_config_root(args.config_root))
    except ConfigError as exc:
        print(f"swage: {exc}", file=sys.stderr)
        return ExitCode.FAILED

    # Every command loads the tree, so every command can keep completion's
    # family names current for free. The last tree swage read is the one it
    # completes against, which is what a `--config-root` somewhere else means.
    remember(FAMILIES, tree.families)

    if args.command == "scan":
        return _scan(tree, args)

    if args.command == "update":
        return _update(tree, args)

    if args.command == "audit":
        return _audit(tree, args)

    if args.command == "status":
        return _status(tree, args)

    if args.command == "draft":
        return _draft_family(tree, args) if args.family else _draft(tree, args)

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
    """`swage audit` (DESIGN.md 8.2), which reads the fleet and writes nothing.

    Unlike `scan`, this plans every feedstock it is given rather than only the
    ones with an open bot pull request, so it fetches an sdist per feedstock
    and takes an hour or two over the whole fleet. That is what the archive
    cache is for: a second audit pays for the recipes that changed.
    """
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
    if live:
        print("\r\033[K", end="", file=sys.stderr)
    print(
        render_summary(
            run, directory, descriptions=AUDIT_DESCRIPTIONS, counted="audited"
        ),
        end="",
    )
    return ExitCode.NEEDS_REVIEW if run.needs_review else ExitCode.OK


def _scan(tree: ConfigTree, args: argparse.Namespace) -> int:
    """`swage scan` (DESIGN.md 8), which reads and reports and writes nothing.

    Exit codes are the contract a cron wrapper reads (DESIGN.md 9.1): `0`
    nothing needs you, `1` items need review, `2` swage itself failed. The
    distinction that matters is the last one -- a feedstock swage could not
    read is a `1`, because the run did its job and is telling you about it,
    while a channel that will not answer is a `2`, because the run did not
    happen.
    """
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

    # Progress is one line rewritten in place, so it is only ever emitted to a
    # terminal that can rewrite it. Piped or redirected, those escapes would be
    # 487 lines of `\r\033[K` in whatever collected them.
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
    # Every recipe swage planned, beside the one it would replace. Costs a
    # dozen small files on a fleet sweep and is what makes DESIGN.md 10's
    # differential validation a by-product of scanning rather than a second
    # tool (DESIGN.md 9).
    write_recipes(run, directory)
    if live:
        # Erase the progress line rather than leaving it above the report.
        print("\r\033[K", end="", file=sys.stderr)
    print(render_summary(run, directory, descriptions=SCAN_DESCRIPTIONS), end="")
    return ExitCode.NEEDS_REVIEW if run.needs_review else ExitCode.OK


def _draft_family(tree: ConfigTree, args: argparse.Namespace) -> int:
    """`swage draft --family` (DESIGN.md 8.1), which assembles and groups.

    **`--execute` is refused here**, and that is the point rather than a gap. The
    per-feedstock draft holds only what swage can derive without judgement, and
    writing fifty of them into `config/` at once would put fifty files in front
    of a reviewer that nobody has decided anything about -- while the summary's
    whole finding is usually that one *family* file answers them all. Applying
    stays a per-feedstock gesture, taken once a decision exists.
    """
    if args.execute:
        print(
            "swage: --execute drafts one feedstock at a time\n"
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


def _draft(tree: ConfigTree, args: argparse.Namespace) -> int:
    """`swage draft` (DESIGN.md 8.1), which reads and writes a workbench.

    Exit `0` even where the feedstock is held: `draft` is asked at a moment
    when something is known to be undecided, so reporting that as needing
    review would make the successful case indistinguishable from the failure.
    A `2` here means swage could not assemble the workbench at all.
    """
    github = GitHub()
    try:
        names = NameSources(load_package_index(), load_grayskull_layer())
        workbench, applied = run_draft(
            github, tree, args.feedstock, names, execute=args.execute
        )
    except (ConfigError, ForgeError, PlanError, RecipeError, UpstreamError) as exc:
        print(f"swage: {exc}", file=sys.stderr)
        return ExitCode.FAILED

    print(render_workbench(workbench, applied), end="")
    return ExitCode.OK


def _migrate(args: argparse.Namespace) -> int:
    """`swage migrate` (DESIGN.md 7), which reads and writes nothing yet.

    **No config is consulted and none is needed.** A conversion is a statement
    about the recipe's *format* rather than about its dependencies, so the
    quirks database has nothing to say about it -- which is also why this is
    the one bucket no config helps with, and the reason it is the largest.

    Exit `1` where any feedstock was refused: a refusal is a real answer that
    a person now has to act on, which is what that code means everywhere else.
    A `2` is swage failing to ask the question at all.
    """
    github = GitHub()
    refused = False
    for index, feedstock in enumerate(dict.fromkeys(args.feedstock)):
        if index:
            print()
        try:
            ref = default_branch(github, feedstock)
            migration = plan_migration(github, feedstock, ref)
        except MigrationError as exc:
            print(render_refusal(feedstock, str(exc)), end="")
            refused = True
        except ForgeError as exc:
            print(f"swage: {exc}", file=sys.stderr)
            return ExitCode.FAILED
        else:
            print(render_migration(migration), end="")
    return ExitCode.NEEDS_REVIEW if refused else ExitCode.OK


def _status(tree: ConfigTree, args: argparse.Namespace) -> int:
    """`swage status` (DESIGN.md 8), which closes the loop and writes nothing.

    A window with no runs in it is not a failure and not a clean report either
    -- swage has nothing to say, and says that rather than printing an empty
    summary that would read as "everything landed".

    Its own run is recorded like any other, so `swage explain` answers out of a
    status run exactly as it does out of a scan.
    """
    try:
        window = parse_since(args.since)
    except ValueError as exc:
        print(f"swage: --since {exc}", file=sys.stderr)
        return ExitCode.FAILED

    cutoff = datetime.now(UTC) - window
    runs, skipped = read_runs(runs_since(cutoff))
    if skipped:
        # Never silent, and never one line each: a window quietly covering less
        # than it claims is how a report comes back clean by having looked at
        # less, and 48 lines saying so would bury the report either way.
        plural = "" if skipped == 1 else "s"
        print(
            f"swage: skipped {skipped} run{plural} in this window that this "
            "swage cannot read; rerun the command that made them to replace them",
            file=sys.stderr,
        )
    if not runs:
        print(f"swage: no runs in the last {args.since} to follow up on")
        return ExitCode.OK

    # Checked before anything is loaded or fetched, because this is the common
    # case rather than an edge one: a fleet with nothing in flight is what most
    # mornings look like, and an empty summary under a header would read as a
    # report that failed to render rather than as good news.
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
    """`swage update` (DESIGN.md 8), which is `scan` plus writes.

    Dry run unless `--execute`, and the dry run is not a rehearsal: the same
    invocation reaches the same outcome for every feedstock either way, so what
    the report says it would do is what it does.

    Clones live under this run's directory, which means the tree swage pushed
    is still on disk beside the record of why it pushed it -- and that a run
    directory somebody keeps is a complete account of one write.
    """
    github = GitHub()
    try:
        names = NameSources(load_package_index(), load_grayskull_layer())
        feedstocks = select_feedstocks(github, tree, args.family, args.feedstock)
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
        execute=args.execute,
        command=_command_line(args),
        progress=_progress("updating") if live else None,
        migrate=args.migrate,
    )

    write_run(run, directory)
    write_recipes(run, directory)
    if live:
        print("\r\033[K", end="", file=sys.stderr)
    print(
        render_summary(
            run,
            directory,
            descriptions=UPDATE_DESCRIPTIONS if args.execute else DRY_RUN_DESCRIPTIONS,
            banner="" if args.execute else DRY_RUN_BANNER,
        ),
        end="",
    )
    return ExitCode.NEEDS_REVIEW if run.needs_review else ExitCode.OK


def _explain(args: argparse.Namespace) -> int:
    """`swage explain` (DESIGN.md 9.2), rendered from the record.

    The exit code is the one the run itself gave this feedstock, so asking
    about a feedstock that needs review says so in the same way the sweep did.
    """
    try:
        directory = resolve_run(args.from_run)
        rendered, record = explain_feedstock(args.feedstock, directory, args.as_json)
    except ReportError as exc:
        print(f"swage: {exc}", file=sys.stderr)
        return ExitCode.FAILED

    print(rendered)
    return ExitCode.NEEDS_REVIEW if record.needs_review else ExitCode.OK


def _refresh_names(tree: ConfigTree) -> int:
    """`swage completion --refresh`, which fills in what completion offers.

    The only command whose whole purpose is that cache. Every other run fills
    it as a side effect of work it was doing anyway -- but a maintainer who
    always names feedstocks explicitly never causes a discovery, and would
    otherwise have a completion that offers nothing and no way to see why.

    The families were written when the tree loaded, like any other run, so
    what this adds is the one GitHub call.
    """
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
    """The invocation, as the report's header prints it back.

    Every named feedstock is printed, not the last one. The header is how a
    reader checks that swage understood the command, so a header naming one
    feedstock above a run that covered two is worse than no header at all --
    that is exactly how `--feedstock` dropping all but the last went unnoticed.
    """
    parts = [f"swage {args.command}"]
    # `status` has no selector to print. Its subject is the pull requests
    # earlier runs touched, and the window is what narrows it.
    if args.command == "status":
        return f"swage status --since {args.since}"
    if args.feedstock is not None:
        named = " ".join(dict.fromkeys(args.feedstock))
        parts.append(f"--feedstock {named}")
    elif args.family is not None:
        parts.append(f"--family {args.family}")
    else:
        parts.append("--all")
    # Before `--execute`, in the order they are typed. Both belong in the
    # header because both change what the run did: a `run.json` that does not
    # say a conversion was in scope cannot be told from one where every v0
    # feedstock was simply reported and skipped.
    if args.command == "update" and args.migrate:
        parts.append("--migrate")
    if args.command == "update" and args.execute:
        parts.append("--execute")
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


def _print_feedstock(tree: ConfigTree, feedstock: str) -> None:
    """Every key that applies to one feedstock, as the layers resolved it.

    **Every** key, because this is the command the documentation sends a
    maintainer to after a config edit, and it printed seven of the fifteen: a
    freshly written `add_requirements` or `constraints` resolved perfectly and
    showed up nowhere, which reads exactly like an edit that did not land.
    """
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
        print(f"  core:            {output.run.core}")
        print(f"  extras:          {', '.join(output.run.extras) or '-'}")
        print(f"  skip:            {', '.join(output.run.skip) or '-'}")
    for section, added in resolved.add_requirements.items():
        # The file that asked, per line: an added requirement is only as good
        # as its stated reason, and which layer stated it is half of that.
        for requirement in added:
            print(f"add to {section}:       {requirement.text}  ({requirement.source})")
    for name, constraint in resolved.constraints.items():
        print(f"constraint:        {name} {constraint}")
    for name, entry in resolved.run_constraints.items():
        print(f"run constraint:    {name} tracks extra {entry.extra or '-'}")
    if resolved.retire:
        print(f"retire:            {', '.join(sorted(resolved.retire))}")
    print(f"recipe owned:      {', '.join(resolved.recipe_owned.names) or '-'}")
    print(f"  functions:       {', '.join(resolved.recipe_owned.functions) or '-'}")
    print(f"default build:     {', '.join(resolved.default_build_requires) or '-'}")
    print("name map layers:")
    for layer in resolved.name_map.layers:
        print(f"  {layer.source} ({len(layer.entries)} entries)")
    print("embedded extras layers:")
    for extras_layer in resolved.embedded_extras.layers:
        print(f"  {extras_layer.source} ({len(extras_layer.entries)} entries)")
