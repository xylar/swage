"""The ``swage`` command line (DESIGN.md 8).

Exit codes are part of the contract, because every command is meant to be safe
to run from cron (DESIGN.md 9.1): ``0`` nothing needs you, ``1`` items need
review, ``2`` swage itself failed.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from enum import IntEnum
from pathlib import Path

from swage import __version__
from swage.config import ConfigError, ConfigTree, load_config
from swage.forge import (
    ForgeError,
    GitHub,
    load_grayskull_layer,
    load_package_index,
)
from swage.report import (
    ReportError,
    render_summary,
    run_directory,
    write_run,
)

from .explain import explain_feedstock, resolve_run
from .scan import SCAN_DESCRIPTIONS, NameSources, run_scan, select_feedstocks

__all__ = ["main"]

_CONFIG_ROOT_ENV = "SWAGE_CONFIG_ROOT"

#: Commands from DESIGN.md 8 that later phases fill in, with the phase that
#: does it. Registering them now keeps ``swage --help`` honest about the shape
#: of the tool without pretending they work.
_PLANNED = {
    "update": ("render, push, and label", "3"),
    "status": ("close the loop on prior runs", "4"),
    "audit": ("read-only hygiene sweep", "5"),
    "migrate": ("convert a feedstock from v0 to v1", "6"),
}


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
        "config", help="validate the quirks database and show what it resolves to"
    )
    config_parser.add_argument(
        "--feedstock",
        metavar="NAME",
        help="show the config resolved for one feedstock instead of a summary",
    )

    scan_parser = subparsers.add_parser(
        "scan", help="read-only; report what would change"
    )
    # Exactly one, and required: `scan` with no selector would sweep every
    # feedstock the maintainer has, which is a real operation against GitHub
    # and not something to trip into by typing the command with no arguments.
    scope = scan_parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--feedstock", metavar="NAME", help="scan one feedstock")
    scope.add_argument("--family", metavar="NAME", help="scan one family's feedstocks")
    scope.add_argument(
        "--all", action="store_true", help="scan every feedstock you maintain"
    )
    scan_parser.add_argument(
        "--quiet",
        action="store_true",
        help="do not report progress while the sweep runs",
    )

    explain_parser = subparsers.add_parser("explain", help="why did swage decide that?")
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

    for name, (help_text, phase) in _PLANNED.items():
        subparsers.add_parser(name, help=f"{help_text} [phase {phase}]")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command in _PLANNED:
        phase = _PLANNED[args.command][1]
        print(
            f"swage {args.command} is not implemented yet (phase {phase}); "
            "see DESIGN.md",
            file=sys.stderr,
        )
        return ExitCode.FAILED

    # `explain` reads a run directory and nothing else -- no config, no
    # network, no recipe. Loading the quirks database first would make an
    # unrelated typo in it the answer to "why did swage do that".
    if args.command == "explain":
        return _explain(args)

    try:
        tree = load_config(_config_root(args.config_root))
    except ConfigError as exc:
        print(f"swage: {exc}", file=sys.stderr)
        return ExitCode.FAILED

    if args.command == "scan":
        return _scan(tree, args)

    if args.feedstock:
        _print_feedstock(tree, args.feedstock)
    else:
        _print_summary(tree)
    return ExitCode.OK


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
        progress=_progress if live else None,
    )

    directory = run_directory()
    write_run(run, directory)
    if live:
        # Erase the progress line rather than leaving it above the report.
        print("\r\033[K", end="", file=sys.stderr)
    print(render_summary(run, directory, descriptions=SCAN_DESCRIPTIONS), end="")
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


def _progress(feedstock: str) -> None:
    """One rewritten line on stderr, so the report on stdout stays pipeable."""
    print(f"\r\033[K  scanning {feedstock}", end="", file=sys.stderr, flush=True)


def _nothing_selected(args: argparse.Namespace) -> str:
    if args.family is not None:
        return f"no feedstock you maintain belongs to the family '{args.family}'"
    return "you maintain no conda-forge feedstocks"  # pragma: no cover


def _command_line(args: argparse.Namespace) -> str:
    """The invocation, as the report's header prints it back."""
    if args.feedstock is not None:
        return f"swage scan --feedstock {args.feedstock}"
    if args.family is not None:
        return f"swage scan --family {args.family}"
    return "swage scan --all"


def _config_root(explicit: Path | None) -> Path | None:
    if explicit is not None:
        return explicit
    from_env = os.environ.get(_CONFIG_ROOT_ENV)
    return Path(from_env) if from_env else None


def _print_summary(tree: ConfigTree) -> None:
    print(f"config root:  {tree.root}")
    print(f"trust floor:  {tree.defaults.trust}")
    if tree.defaults.requires_python is not None:
        print(f"python floor: {tree.defaults.requires_python.min}")
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
    resolved = tree.for_feedstock(feedstock)
    print(f"feedstock:         {resolved.feedstock}")
    print(f"family:            {resolved.family or '-'}")
    print(f"trust:             {resolved.trust}")
    print(f"upstream:          {resolved.upstream or '-'}")
    if resolved.requires_python is not None:
        print(f"requires python:   {resolved.requires_python.min}")
    if resolved.extras_as_outputs is not None:
        extras = resolved.extras_as_outputs
        print(f"extras as outputs: suffix={extras.suffix}")
        print(f"  supported:       {', '.join(extras.supported) or '-'}")
        print(f"  skip:            {', '.join(extras.skip) or '-'}")
    for name, output in resolved.outputs.items():
        print(f"output {name}:")
        print(f"  core:            {output.run.core}")
        print(f"  extras:          {', '.join(output.run.extras) or '-'}")
    print("name map layers:")
    for layer in resolved.name_map.layers:
        print(f"  {layer.source} ({len(layer.entries)} entries)")
    print("embedded extras layers:")
    for extras_layer in resolved.embedded_extras.layers:
        print(f"  {extras_layer.source} ({len(extras_layer.entries)} entries)")
