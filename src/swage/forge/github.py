"""Every GitHub call swage makes, through one choke point (DESIGN.md 3.5).

Two things are centralized here, and both exist because of scale.

**Retry with backoff.** At ~490 feedstocks, a transient 5xx or a secondary
rate limit is a certainty rather than a risk, and a single blip part-way
through a sweep would otherwise abort the whole run. `run_gh_api` in the
google-cloud tool is the prior art; this generalizes it. A primary rate limit
resets on the hour and no amount of backing off will outlast it, but it is
retried anyway: telling it apart from a secondary limit means parsing
`gh`'s stderr more closely than is worth doing, and the cost of being wrong is
fourteen seconds.

**Authentication.** swage has no credentials of its own -- it shells out to the
GitHub CLI, exactly as both tools it replaces do. Nothing here reads a token,
and nothing here should: a token swage handled would be a token swage could
leak into a run artifact or a log.

The runner is injectable so that tests exercise this without `gh` installed
and without a network (DESIGN.md 11).
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

#: Failures worth trying again. `gh` reports the status in its stderr, so this
#: matches on the message rather than on an exit code, which is always 1.
#: `timed out` is swage's own wording from the timeout below, and belongs here
#: because a call that hung is the most retryable failure there is: the usual
#: cause is a connection that died under a suspended laptop, and the next
#: attempt opens a new one.
_TRANSIENT = re.compile(
    r"HTTP (?:429|5\d\d)\b|secondary rate limit|rate limit exceeded|timed out",
    re.IGNORECASE,
)

#: How long any one `gh` or `git` call may take before it is abandoned.
#:
#: **Without this a fleet sweep can hang forever, and twice it did.** `gh` sets
#: no deadline of its own, so a request whose connection dies underneath it --
#: closing a laptop is enough -- waits on a socket nobody will ever answer.
#: swage waits on `gh`, so a 487-feedstock sweep stops dead: not failing, not
#: retrying, not reporting. One was found at 0.0% CPU two hours in, holding a
#: child that had been alive for 1h49m on a single contents request.
#:
#: Generous on purpose. This bounds `git clone` and a `--paginate` read of
#: every team the maintainer belongs to as well as the ordinary request, and
#: abandoning work that was going to finish would be a worse failure than the
#: one being fixed. An ordinary API call takes well under a second, so this is
#: two to three orders of magnitude of headroom and still bounded.
_TIMEOUT = 300.0

#: A read of something that is not there. Never transient, and usually not an
#: error either -- see `NotFound`.
_NOT_FOUND = re.compile(r"HTTP 404\b|Not Found", re.IGNORECASE)


#: Why swage needs each program it shells out to, said where somebody who does
#: not have it will read it. Both are hard requirements rather than optional
#: integrations: swage has no credentials of its own and no git of its own.
_MISSING = {
    "gh": (
        "swage authenticates with the GitHub CLI's credentials rather than a "
        "token of its own -- install gh and run `gh auth login`"
    ),
    "git": ("swage clones a pull request's branch to write to it -- install git"),
}


def run_gh(argv: Sequence[str]) -> str:
    """Run a command, raising `ForgeError` with its stderr if it fails.

    Named for `gh` because that is what it began as and what almost every
    caller passes, but the write path shells out to `git` through the same
    runner (DESIGN.md 3.5). One runner rather than two is what lets a test
    inject a single fake and see the whole sequence of calls a feedstock
    provoked, in the order swage made them -- which for push-then-label is
    the property under test rather than an incidental convenience.
    """
    try:
        completed = subprocess.run(
            argv, check=True, text=True, capture_output=True, timeout=_TIMEOUT
        )
    except subprocess.TimeoutExpired as exc:
        # Deliberately a `ForgeError` whose wording `_TRANSIENT` matches, so
        # the retry above tries again on a fresh connection rather than losing
        # the feedstock to a socket that died while the machine was asleep.
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
        # A 404 is usually not a failure at all -- a feedstock whose recipe is
        # still `meta.yaml` is the common case -- so it gets a type callers can
        # act on rather than a message they have to re-parse.
        if _NOT_FOUND.search(detail):
            raise NotFound(message, detail) from exc
        raise ForgeError(message, detail) from exc
    return completed.stdout


#: The exact argv prefix `api` and `paginated` build, and the only shape
#: `ReadRecorder` will keep anything for. A write does not begin this way and
#: cannot be made to: `label`, `unlabel` and `comment` are `gh pr` subcommands,
#: so the read/write split this file already draws is the same split the cache
#: is keyed on rather than a second one that could drift from it.
_READ = ("gh", "api", "--method", "GET")

#: What an entry recording "it does not exist" is called, beside the entry that
#: would have held the answer. A suffix rather than a marker inside the file,
#: so the two can never be confused for one another by a truncated write.
_ABSENT = ".not-found"


@dataclass
class ReadRecorder:
    """A `Runner` that keeps what read-only calls answered, and can replay it.

    **What this is for is verifying a change to swage, not saving time.** A
    fleet audit reads ~490 default branches, recipes and pull request lists
    through `gh`, which is a subprocess apiece and around a quarter of an hour
    -- and the run it is compared against read them a quarter of an hour ago,
    off a fleet that may have moved in between. So the sweep is slow *and* the
    experiment is not controlled: a feedstock whose recipe changed on its own
    default branch shows up as a difference somebody then has to attribute to
    the code.

    Replaying pins both. The second audit reads the same bytes the first one
    did, so every difference between the two renderings is the code's, and it
    costs no `gh` calls at all.

    **A cached fleet is deliberately out of date**, which is the whole point
    and also the one way to misuse this: a replayed audit reports the fleet as
    it was when the cache was recorded. `replayed` and `fetched` are counted
    so the caller can say so, and nothing that writes to a feedstock is ever
    given one of these -- `audit` writes nothing (DESIGN.md 8.2), and it is the
    only command that asks for one.

    **Only a read is ever kept.** The argv has to start with `_READ`, which is
    what `api` and `paginated` build and what a `gh pr edit` cannot produce. So
    a replayed run cannot serve a label or a comment from disk, and a recorded
    one cannot have kept one.

    Recording happens whether or not ``replay`` is set, so an ordinary audit
    leaves the cache a later one can be pinned against.

    **A `NotFound` is kept too, and no other failure is.** "It does not exist"
    is an answer rather than a failure, and it is the answer for a third of the
    fleet: 148 feedstocks are still `meta.yaml`, so reading `recipe/recipe.yaml`
    on them 404s. Leaving those out would mean a replayed audit still made a
    live call for each, and worse, that their outcome was the only one in the
    run *not* pinned -- a feedstock that gained a `recipe.yaml` in between would
    move, and the difference would not be the code's. Nothing else is kept:
    a 5xx or a secondary rate limit is retried by `_attempt` above, and a cache
    that remembered one would turn a blip into a permanent wrong answer.
    """

    #: The underlying runner, which is `run_gh` outside tests.
    run: Runner
    #: Where entries live. One file per argv, disposable like everything else
    #: under the cache root.
    root: Path
    #: Whether to answer from the cache. Off by default, so recording never
    #: changes what a run sees.
    replay: bool = False
    #: Reads answered from disk, and reads that still went to GitHub. A replay
    #: whose cache is mostly empty is a slow live audit, and the difference is
    #: only visible in these.
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
        """Write through a temporary file in the same directory and rename.

        Two swage runs racing on one entry cannot leave a half-written one
        behind, which matters more here than for an archive: an archive is
        checked against the hash the recipe pins every time it is read, and a
        truncated API response would just be unparseable JSON.
        """
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(f"{path.name}.{os.getpid()}")
            temporary.write_text(payload, encoding="utf-8")
            temporary.replace(path)
        except OSError:
            # A cache that cannot be written is a slow swage, not a broken one.
            pass


def _key(argv: Sequence[str]) -> str:
    """A filename for one read, keeping the path it read visible.

    Hashed because an API path has slashes and the parameters have to be part
    of the key -- two reads of one file at two refs are two entries -- and
    suffixed with the endpoint so somebody looking in the cache directory can
    tell what is in it.
    """
    digest = hashlib.sha256("\x00".join(argv).encode("utf-8")).hexdigest()[:16]
    rest = argv[len(_READ) :]
    name = rest[0].strip("/").replace("/", "-") if rest else ""
    return f"{digest}-{name}"[:120] if name else digest


def _at(repo: str) -> list[str]:
    """Name the repository explicitly on every `gh pr` call.

    Without it `gh` infers one from the working directory, which for swage is
    whatever the maintainer happened to be standing in -- so a run started in
    a feedstock checkout could label a pull request on a different feedstock
    than the one it just pushed to.
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

        ``--method GET`` is not decoration and must never be dropped: ``gh``
        infers POST from the presence of an ``-f`` field, so the same argv
        without it would *create* against the endpoint rather than read it.
        Against ``/pulls`` that means opening a pull request on somebody's
        feedstock, which is exactly the class of accident this whole tool is
        supposed to be incapable of.
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
        """GET every page of a paginated endpoint, flattened into one list.

        ``--slurp`` makes `gh` return the pages as a JSON array rather than
        concatenated documents, which is the only form that can be parsed
        without knowing how many pages there were.
        """
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
        """Read one file at one ref, without cloning anything.

        Reading recipes and upstream metadata for several hundred feedstocks
        by `git clone` is untenable, so reads go through the contents API and
        only a feedstock that needs a commit is ever cloned (DESIGN.md 3.5).
        """
        payload = self.api(f"repos/{repo}/contents/{path}", {"ref": ref})
        where = f"{repo}/{path}@{ref}"
        if not isinstance(payload, Mapping):
            raise ForgeError(f"{where}: is a directory, not a file")
        encoding = payload.get("encoding")
        if encoding != "base64":
            # GitHub answers `"encoding": "none"` with empty content for a file
            # over 1 MB, which would otherwise arrive looking like a file that
            # says nothing -- the one reading of silence DESIGN.md 3.6.2 rules
            # out. No metadata file is anywhere near that, so say so plainly
            # rather than reaching for the blob API on a case that means
            # something has gone wrong.
            raise ForgeError(
                f"{where}: contents API returned encoding {encoding!r} rather "
                "than base64, which means the file is over 1 MB"
            )
        try:
            return base64.b64decode(payload.get("content", "")).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError) as exc:
            raise ForgeError(f"{where}: contents are not UTF-8 text: {exc}") from exc

    # Everything above reads. Everything below writes, and there is nothing
    # else: these three are every change swage makes through GitHub's API.
    # Merging is not among them and cannot be added back casually -- see
    # DESIGN.md 5.2 for what stopped it.
    #
    # They are `gh pr` subcommands rather than `api()` with a different method,
    # and that is the point. The accident `api()` is shaped to prevent is a
    # read becoming a write by omitting `--method GET` (DESIGN.md 3.5); a write
    # that has to be spelled `gh pr edit` cannot be reached by forgetting an
    # argument. They still go through `_attempt`, because DESIGN.md 5.5 asks
    # for the label to be retried before a pull request is called DEGRADED.

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
