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
import json
import re
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from .errors import ForgeError, NotFound

__all__ = ["GitHub", "Runner", "run_gh"]

#: Takes an argv and returns stdout, raising `ForgeError` if the command fails.
Runner = Callable[[Sequence[str]], str]

#: Failures worth trying again. `gh` reports the status in its stderr, so this
#: matches on the message rather than on an exit code, which is always 1.
_TRANSIENT = re.compile(
    r"HTTP (?:429|5\d\d)\b|secondary rate limit|rate limit exceeded",
    re.IGNORECASE,
)

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
        completed = subprocess.run(argv, check=True, text=True, capture_output=True)
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
            raise NotFound(message) from exc
        raise ForgeError(message) from exc
    return completed.stdout


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
