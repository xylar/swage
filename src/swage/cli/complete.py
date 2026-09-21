"""`swage completion`: the commands, and the names, on the TAB key (DESIGN.md
§12.3).

The shell calls swage back on every TAB through argcomplete, so what a TAB
offers is read from the parser that will parse it. Names come from a cache that
any discovering run writes and `--refresh` writes on demand; a file that is not
there completes nothing. argcomplete is imported here and nowhere else.
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
    """Record the names completion should offer for ``kind``, from whatever has
    just worked them out.
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
    """The shell code that makes ``shell`` ask swage to complete `swage`:
    argcomplete's own, without readline's filename fallback (DESIGN.md
    §12.3).
    """
    from argcomplete.shell_integration import shellcode

    return shellcode(["swage"], shell=shell, use_defaults=False)


def autocomplete(parser: argparse.ArgumentParser) -> None:
    """Answer the shell's TAB from ``parser`` and exit. Reached only with
    `_ARGCOMPLETE` in the environment, before anything else is imported.
    """
    import argcomplete

    install(parser)
    argcomplete.autocomplete(parser)


def install(parser: argparse.ArgumentParser) -> None:
    """Attach a completer to every argument of ``parser`` that wants one, keyed
    on `dest`, because the same value means the same thing under every
    command.
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

    A value swage cannot enumerate completes to nothing rather than to
    filenames.
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
