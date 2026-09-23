"""Every GitHub call swage makes, through one choke point (v1 §3.5; DESIGN.md
§10).

Retry with backoff, because at ~490 feedstocks a transient failure is a
certainty. No credentials of its own: swage shells out to the GitHub CLI. The
runner is injectable so tests run without `gh` or a network.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import ForgeError, NotFound

__all__ = ["GitHub", "ReadRecorder", "Runner", "run_gh"]

#: Takes an argv and returns stdout, raising `ForgeError` if the command fails.
Runner = Callable[[Sequence[str]], str]

#: Failures worth trying again, matched on `gh`'s stderr since its exit code is
#: always 1. `timed out` is swage's own wording from the timeout below.
_TRANSIENT = re.compile(
    r"HTTP (?:429|5\d\d)\b|secondary rate limit|rate limit exceeded|timed out",
    re.IGNORECASE,
)

#: How long any one `gh` or `git` call may take before it is abandoned. Without
#: it a sweep hangs forever on a socket nobody will answer; generous because it
#: also bounds `git clone` and a paginated read.
_TIMEOUT = 300.0

#: A read of something that is not there. Never transient, and usually not an
#: error either -- see `NotFound`.
_NOT_FOUND = re.compile(r"HTTP 404\b|Not Found", re.IGNORECASE)


#: Why swage needs each program it shells out to, said where somebody who does
#: not have it will read it.
_MISSING = {
    "gh": (
        "swage authenticates with the GitHub CLI's credentials rather than a "
        "token of its own -- install gh and run `gh auth login`"
    ),
    "git": ("swage clones a pull request's branch to write to it -- install git"),
}


def run_gh(argv: Sequence[str]) -> str:
    """Run a command, raising `ForgeError` with its stderr if it fails. One
    runner for `gh` and `git`, so a test sees the whole sequence of calls.
    """
    try:
        completed = subprocess.run(
            argv, check=True, text=True, capture_output=True, timeout=_TIMEOUT
        )
    except subprocess.TimeoutExpired as exc:
        # A `ForgeError` whose wording `_TRANSIENT` matches, so the retry tries
        # again on a fresh connection.
        raise ForgeError(f"{' '.join(argv)} timed out after {_TIMEOUT:.0f}s") from exc
    except FileNotFoundError as exc:
        program = argv[0] if argv else ""
        hint = _MISSING.get(program)
        raise ForgeError(
            f"the `{program}` command is not on PATH" + (f"\n  {hint}" if hint else "")
        ) from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        message = f"{' '.join(argv)} failed:\n{detail}"
        # A 404 is usually not a failure at all, so it gets a type callers can
        # act on.
        if _NOT_FOUND.search(detail):
            raise NotFound(message, detail) from exc
        raise ForgeError(message, detail) from exc
    return completed.stdout


#: The exact argv prefix `api` and `paginated` build, and the only shape
#: `ReadRecorder` will keep anything for; a write cannot be made to begin this
#: way.
_READ = ("gh", "api", "--method", "GET")

#: What an entry recording "it does not exist" is called: a suffix, so a
#: truncated write cannot confuse the two.
_ABSENT = ".not-found"


@dataclass
class ReadRecorder:
    """A `Runner` that keeps what read-only calls answered, and can replay it
    (DESIGN.md §14.1).

    For verifying a change to swage: a replayed audit reads the same bytes
    the recorded one did, so every difference is the code's. A cached fleet
    is out of date on purpose, and `replayed` and `fetched` are counted so
    the caller can say so. Only a read is ever kept, and a `NotFound` is
    kept too, because it is an answer rather than a failure; no other
    failure is.
    """

    #: The underlying runner, which is `run_gh` outside tests.
    run: Runner
    #: Where entries live. One file per argv, disposable like everything else
    #: under the cache root.
    root: Path
    #: Whether to answer from the cache. Off by default, so recording never
    #: changes what a run sees.
    replay: bool = False
    #: Reads answered from disk, and reads that still went to GitHub.
    replayed: int = field(default=0, init=False)
    fetched: int = field(default=0, init=False)

    def __call__(self, argv: Sequence[str]) -> str:
        if tuple(argv[: len(_READ)]) != _READ:
            # A write, or a `git` call. Neither is cached, in either direction.
            return self.run(argv)
        path = self.root / _key(argv)
        absent = path.with_name(f"{path.name}{_ABSENT}")
        if self.replay:
            try:
                answer = path.read_text(encoding="utf-8")
            except OSError:
                # Missing, unreadable, or a directory somebody put there. All
                # of them mean the same thing to a cache: fetch it.
                pass
            else:
                self.replayed += 1
                return answer
            try:
                said = absent.read_text(encoding="utf-8")
            except OSError:
                pass
            else:
                self.replayed += 1
                raise NotFound(f"{' '.join(argv)} failed:\n{said}", said)
        try:
            payload = self.run(argv)
        except NotFound as exc:
            self.fetched += 1
            self._keep(absent, exc.said)
            raise
        self.fetched += 1
        self._keep(path, payload)
        return payload

    def _keep(self, path: Path, payload: str) -> None:
        """Write through a temporary file in the same directory and rename."""
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(f"{path.name}.{os.getpid()}")
            temporary.write_text(payload, encoding="utf-8")
            temporary.replace(path)
        except OSError:
            # A cache that cannot be written is a slow swage, not a broken one.
            pass


def _key(argv: Sequence[str]) -> str:
    """A filename for one read, keeping the path it read visible."""
    digest = hashlib.sha256("\x00".join(argv).encode("utf-8")).hexdigest()[:16]
    rest = argv[len(_READ) :]
    name = rest[0].strip("/").replace("/", "-") if rest else ""
    return f"{digest}-{name}"[:120] if name else digest


def _at(repo: str) -> list[str]:
    """Name the repository explicitly on every `gh pr` call, so the working
    directory decides nothing.
    """
    return ["--repo", repo]


class GitHub:
    """The GitHub API, with retries."""

    def __init__(
        self,
        run: Runner = run_gh,
        sleep: Callable[[float], None] = time.sleep,
        max_attempts: int = 4,
        base_delay: float = 2.0,
    ) -> None:
        self._run = run
        self._sleep = sleep
        self._max_attempts = max_attempts
        self._base_delay = base_delay

    def api(self, path: str, params: Mapping[str, str] | None = None) -> Any:
        """GET an API path and parse the JSON it answers with.

        ``--method GET`` must never be dropped: ``gh`` infers POST from an
        ``-f`` field, and the same argv without it would create against the
        endpoint (v1 §3.5).
        """
        argv = ["gh", "api", "--method", "GET", path]
        for key, value in (params or {}).items():
            argv.extend(["-f", f"{key}={value}"])
        payload = self._attempt(argv)
        try:
            return json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ForgeError(f"{path}: GitHub did not answer with JSON: {exc}") from exc

    def paginated(
        self, path: str, params: Mapping[str, str] | None = None
    ) -> list[Any]:
        """GET every page of a paginated endpoint, flattened into one list."""
        argv = ["gh", "api", "--method", "GET", "--paginate", "--slurp", path]
        for key, value in (params or {}).items():
            argv.extend(["-f", f"{key}={value}"])
        payload = self._attempt(argv)
        try:
            pages = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ForgeError(f"{path}: GitHub did not answer with JSON: {exc}") from exc
        if not isinstance(pages, list):
            raise ForgeError(f"{path}: paginated read did not return a list")
        items: list[Any] = []
        for page in pages:
            items.extend(page if isinstance(page, list) else [page])
        return items

    def file(self, repo: str, path: str, ref: str) -> str:
        """Read one file at one ref, without cloning anything (v1 §3.5)."""
        payload = self.api(f"repos/{repo}/contents/{path}", {"ref": ref})
        where = f"{repo}/{path}@{ref}"
        if not isinstance(payload, Mapping):
            raise ForgeError(f"{where}: is a directory, not a file")
        encoding = payload.get("encoding")
        if encoding != "base64":
            # GitHub answers `"encoding": "none"` with empty content for a file
            # over 1 MB; no metadata file is near that, so say so rather than
            # reach for the blob API.
            raise ForgeError(
                f"{where}: contents API returned encoding {encoding!r} rather "
                "than base64, which means the file is over 1 MB"
            )
        try:
            return base64.b64decode(payload.get("content", "")).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError) as exc:
            raise ForgeError(f"{where}: contents are not UTF-8 text: {exc}") from exc

    def comments(self, repo: str, number: int) -> list[str]:
        """Every comment body on a pull request, oldest first.

        Read so that a second run does not repeat a comment the first one
        left (DESIGN.md §3.1).
        """
        payload = self.paginated(f"repos/{repo}/issues/{number}/comments")
        return [
            item["body"]
            for item in payload
            if isinstance(item, Mapping) and isinstance(item.get("body"), str)
        ]

    # Everything above reads. Everything below writes, and there is nothing
    # else: these three are every change swage makes through GitHub's API, and
    # merging is not among them (v1 §5.2). They are `gh pr` subcommands rather
    # than `api()` with a different method, so a write cannot be reached by
    # forgetting an argument. They still go through `_attempt` (v1 §5.5).

    def label(self, repo: str, number: int, name: str) -> None:
        """Add a label to a pull request."""
        self._attempt(
            ["gh", "pr", "edit", str(number), *_at(repo), "--add-label", name]
        )

    def unlabel(self, repo: str, number: int, name: str) -> None:
        """Remove a label from a pull request, which need not carry it."""
        self._attempt(
            ["gh", "pr", "edit", str(number), *_at(repo), "--remove-label", name]
        )

    def comment(self, repo: str, number: int, body: str) -> None:
        """Leave a comment on a pull request."""
        self._attempt(["gh", "pr", "comment", str(number), *_at(repo), "--body", body])

    def _attempt(self, argv: Sequence[str]) -> str:
        for attempt in range(1, self._max_attempts + 1):
            try:
                return self._run(argv)
            except ForgeError as exc:
                if attempt >= self._max_attempts or not _TRANSIENT.search(str(exc)):
                    raise
                self._sleep(self._base_delay * 2 ** (attempt - 1))
        raise AssertionError("unreachable: the loop above either returns or raises")
