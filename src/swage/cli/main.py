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

__all__ = ["main"]

_CONFIG_ROOT_ENV = "SWAGE_CONFIG_ROOT"

#: Commands from DESIGN.md 8 that later phases fill in, with the phase that
#: does it. Registering them now keeps ``swage --help`` honest about the shape
#: of the tool without pretending they work.
_PLANNED = {
    "scan": ("read-only; report what would change", "1"),
    "update": ("render, push, and label", "3"),
    "status": ("close the loop on prior runs", "4"),
    "audit": ("read-only hygiene sweep", "5"),
    "migrate": ("convert a feedstock from v0 to v1", "6"),
    "explain": ("why did swage decide that?", "1"),
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

    try:
        tree = load_config(_config_root(args.config_root))
    except ConfigError as exc:
        print(f"swage: {exc}", file=sys.stderr)
        return ExitCode.FAILED

    if args.feedstock:
        _print_feedstock(tree, args.feedstock)
    else:
        _print_summary(tree)
    return ExitCode.OK


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
