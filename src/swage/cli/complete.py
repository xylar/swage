"""`swage completion` -- the commands, and the names, on the TAB key.

**The shell asks swage, on every TAB.** This is the design `gh` and `pip`
use, through argcomplete: the shell calls the tool back with the line so far
and the tool answers with candidates, so the commands and options a TAB
offers are read from the parser that will parse them and can never be an old
snapshot of it. v1 could not afford it -- importing the CLI cost a third of a
second, which is a completion a maintainer turns off within a day -- and
generated a 600-line script instead. v2's CLI imports nothing but argparse
until a command runs (DESIGN.md 12.3), and the callback is the reason.

**Names come from a cache, because the authoritative answer is a network
call.** Which feedstocks the maintainer has is one paginated GitHub read over
~490 teams (`discover_feedstocks`) -- fine once a run, impossible on a
keystroke. Any run that discovers writes what it found under the cache root,
`swage completion --refresh` writes it on demand, and the completer reads
the file. A file that is not there completes nothing rather than failing,
which is the same answer swage gives for a cache it cannot write.

argcomplete is imported here and nowhere else, and only on the two paths
that need it: answering a TAB, and printing the hook that makes the shell
ask.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any

from swage.cache import cache_root

__all__ = [
    "FAMILIES",
    "FEEDSTOCKS",
    "SHELLS",
    "autocomplete",
    "hook",
    "install",
    "names_directory",
    "recall",
    "remember",
]

#: The shells `swage completion` prints a hook for.
SHELLS = ("bash", "zsh")

#: The two name files. One name per line and sorted, so that the file is
#: worth reading with `grep` as well as with TAB.
FEEDSTOCKS = "feedstocks"
FAMILIES = "families"

_NAMES = "names"


def names_directory(root: Path | None = None) -> Path:
    """Where the names completion offers live."""
    return (root or cache_root()) / _NAMES


def remember(kind: str, names: Iterable[str], root: Path | None = None) -> None:
    """Record the names completion should offer for ``kind``.

    Called from whatever has just worked them out, and never on its own
    account: discovery is expensive enough that running it to fill a completion
    cache would be the tail wagging the dog.
    """
    path = names_directory(root) / kind
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "".join(f"{name}\n" for name in sorted(set(names))), encoding="utf-8"
        )
    except OSError:
        # A cache swage cannot write is a completion that offers less, not a
        # command that failed -- the run this was a side effect of did its job.
        pass


def recall(kind: str, root: Path | None = None) -> tuple[str, ...]:
    """The names completion currently offers for ``kind``."""
    try:
        text = (names_directory(root) / kind).read_text(encoding="utf-8")
    except OSError:
        return ()
    return tuple(line for line in text.splitlines() if line)


def hook(shell: str) -> str:
    """The shell code that makes ``shell`` ask swage to complete `swage`.

    argcomplete's own, as `register-python-argcomplete swage` would print it,
    and printed by swage so that installing completion is one command of the
    tool being completed rather than a second tool to know about.

    Without readline's default completion behind it. Where swage offers
    nothing -- `--since 7d`, a value it cannot enumerate -- bash would
    otherwise offer filenames, and a completion that answers a window with
    the working directory teaches you to distrust it.
    """
    from argcomplete.shell_integration import shellcode

    return shellcode(["swage"], shell=shell, use_defaults=False)


def autocomplete(parser: argparse.ArgumentParser) -> None:
    """Answer the shell's TAB from ``parser`` and exit.

    Only ever reached with `_ARGCOMPLETE` in the environment, which the hook
    sets; `main` checks for it before importing anything, because the import
    is the cost the callback design has to stay under.
    """
    import argcomplete

    install(parser)
    argcomplete.autocomplete(parser)


def install(parser: argparse.ArgumentParser) -> None:
    """Attach a completer to every argument of ``parser`` that wants one.

    Keyed on `dest` rather than on the flag or the command, because the same
    value means the same thing under every command: `--feedstock` names
    feedstocks whether it is `scan`'s or `update`'s, and `explain`'s
    positional is a feedstock too. A table keyed on flags would be a table
    with six ways to disagree with itself.
    """
    for action in _arguments(parser):
        completer = _completer(action)
        if completer is not None:
            action.completer = completer  # type: ignore[attr-defined]


def _arguments(parser: argparse.ArgumentParser) -> Iterator[argparse.Action]:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for subparser in action.choices.values():
                yield from _arguments(subparser)
        else:
            yield action


Completer = Callable[..., Iterable[str]]


def _completer(action: argparse.Action) -> Completer | None:
    """What completes where ``action``'s value goes, or `None` to leave it.

    A flag takes no value, and a fixed set of choices argcomplete offers on
    its own. Everything else is one of three kinds of name or a value swage
    cannot enumerate, such as `--since 7d`, which completes to nothing:
    offering filenames for one of those would be worse than offering nothing.
    """
    if action.nargs == 0 or action.choices:
        return None
    if action.dest == "feedstock":
        return _feedstocks
    if action.dest == "family":
        return _families
    if action.type is Path:
        return _directories
    return _nothing


def _feedstocks(**_: Any) -> tuple[str, ...]:
    return recall(FEEDSTOCKS)


def _families(**_: Any) -> tuple[str, ...]:
    return recall(FAMILIES)


def _directories(prefix: str, **_: Any) -> Iterable[str]:
    from argcomplete.completers import DirectoriesCompleter

    return DirectoriesCompleter()(prefix=prefix)


def _nothing(**_: Any) -> tuple[str, ...]:
    return ()


def completing() -> bool:
    """Whether this process was started by the shell to answer a TAB."""
    return "_ARGCOMPLETE" in os.environ
