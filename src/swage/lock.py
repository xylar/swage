"""One swage run at a time writes to a feedstock (v1 §9.1; DESIGN.md §12.1).

An advisory lock per feedstock under the cache root, held by `update` from
reading the pull request to its last write. A second run waits, and then
plans against whatever the first one pushed, as it would have run after it.
The operating system releases the lock when the process ends however it
ends, so a crashed run leaves nothing to clean up.
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from swage.cache import cache_root

__all__ = ["LOCKS", "POLL", "feedstock_lock"]

#: Where the lock files live under the cache root. Empty files; the lock is
#: the operating system's, not the file's existence.
LOCKS = "locks"

#: How long to wait between attempts on a lock another run holds, in seconds.
POLL = 2.0

if sys.platform == "win32":
    import msvcrt

    def _acquire(fd: int) -> bool:
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True

    def _release(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _acquire(fd: int) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True

    def _release(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


@contextmanager
def feedstock_lock(
    feedstock: str, waiting: Callable[[str], None] | None = None
) -> Iterator[None]:
    """Hold ``feedstock``'s lock for the body, waiting while another run
    holds it. ``waiting`` is told once, the first time the lock is found
    held.
    """
    root = cache_root() / LOCKS
    root.mkdir(parents=True, exist_ok=True)
    fd = os.open(root / f"{feedstock}.lock", os.O_RDWR | os.O_CREAT)
    try:
        told = False
        while not _acquire(fd):
            if waiting is not None and not told:
                waiting(feedstock)
                told = True
            time.sleep(POLL)
        try:
            yield
        finally:
            _release(fd)
    finally:
        os.close(fd)
