"""Read a feedstock out of a local checkout rather than GitHub (DESIGN.md
§12.1).

`Checkout` is a `GitHub` that answers every read of the feedstock's own
repository from the working tree and passes every other read through, so the
planner reads an upstream tag or a sibling feedstock exactly as it does
anywhere else. The tree must be clean: what is on disk is then the commit the
plan is recorded against.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .errors import ForgeError, NotFound
from .github import GitHub, Runner, run_gh

__all__ = ["Checkout"]

#: A remote's URL naming a feedstock, over https or ssh, from any owner: a
#: checkout of a fork names the same feedstock as one of conda-forge's.
_FEEDSTOCK_URL = re.compile(r"[/:][^/\s]+/(?P<name>[^/\s]+?)-feedstock(?:\.git)?/?$")

#: Between the fields and between the commits of `git log`, which cannot occur
#: in a hash or a message.
_FIELD = "\x1f"
_ENTRY = "\x1e"


class Checkout(GitHub):
    """One feedstock's working tree, standing in for its repository."""

    def __init__(
        self,
        root: Path,
        feedstock: str,
        head: str,
        branch: str,
        run: Runner = run_gh,
    ) -> None:
        super().__init__(run)
        self.root = root
        self.feedstock = feedstock
        #: The commit the tree is clean against, which is the ref every read
        #: of the feedstock is recorded at.
        self.head = head
        #: What the commits land on: a branch name, or `HEAD` where detached.
        self.branch = branch
        self.repo = f"conda-forge/{feedstock}-feedstock"

    @classmethod
    def open(cls, directory: Path, run: Runner = run_gh) -> Checkout:
        """The checkout ``directory`` is in, refused where its tree is dirty
        or no remote says which feedstock it is.
        """

        def git(*argv: str) -> str:
            return run(["git", "-C", str(directory), *argv]).strip()

        try:
            root = Path(git("rev-parse", "--show-toplevel"))
        except ForgeError as exc:
            raise ForgeError(f"{directory}: is not a git checkout", exc.said) from exc
        names = {
            match["name"]
            for line in git("remote", "-v").splitlines()
            if (fields := line.split())
            and len(fields) >= 2
            and (match := _FEEDSTOCK_URL.search(fields[1]))
        }
        if len(names) != 1:
            said = ", ".join(sorted(names)) if names else "none"
            raise ForgeError(
                f"{root}: swage cannot tell which feedstock this is\n"
                f"  its remotes name {said}; it needs exactly one "
                "`<name>-feedstock` repository among them"
            )
        if git("status", "--porcelain", "--untracked-files=no"):
            raise ForgeError(
                f"{root}: has uncommitted changes\n"
                "  swage commits its own changes on top of the branch, so "
                "commit or stash yours first"
            )
        head = git("rev-parse", "HEAD")
        return cls(
            root, names.pop(), head, git("rev-parse", "--abbrev-ref", "HEAD"), run
        )

    def file(self, repo: str, path: str, ref: str) -> str:
        if repo != self.repo:
            return super().file(repo, path, ref)
        target = self.root / path
        if not target.is_file():
            raise NotFound(f"{path}: not in the checkout at {self.root}")
        return target.read_text(encoding="utf-8")

    def api(self, path: str, params: Mapping[str, str] | None = None) -> Any:
        own = f"repos/{self.repo}"
        if not path.startswith(f"{own}/"):
            return super().api(path, params)
        contents = f"{own}/contents/"
        if not path.startswith(contents):
            raise self._unanswerable(path)
        target = self.root / path.removeprefix(contents)
        if not target.is_dir():
            raise NotFound(f"{target}: not a directory in the checkout")
        return [
            {"name": child.name, "type": "dir" if child.is_dir() else "file"}
            for child in sorted(target.iterdir())
        ]

    def paginated(
        self, path: str, params: Mapping[str, str] | None = None
    ) -> list[Any]:
        own = f"repos/{self.repo}"
        if not path.startswith(f"{own}/"):
            return super().paginated(path, params)
        if path != f"{own}/commits":
            raise self._unanswerable(path)
        wanted = params or {}
        log = self._run(
            [
                "git",
                "-C",
                str(self.root),
                "log",
                f"--format=%H{_FIELD}%B{_ENTRY}",
                wanted.get("sha", self.head),
                "--",
                wanted.get("path", "."),
            ]
        )
        entries = []
        for entry in log.split(_ENTRY):
            sha, _, message = entry.strip().partition(_FIELD)
            if sha:
                entries.append({"sha": sha, "commit": {"message": message}})
        return entries

    # A checkout is read from and committed to, never published from: nothing
    # that writes to GitHub can be reached through one.

    def label(self, repo: str, number: int, name: str) -> None:
        raise self._unwritable()

    def unlabel(self, repo: str, number: int, name: str) -> None:
        raise self._unwritable()

    def comment(self, repo: str, number: int, body: str) -> None:
        raise self._unwritable()

    def _unanswerable(self, path: str) -> ForgeError:
        """A read of the feedstock the working tree has no answer to, refused
        rather than sent to GitHub, whose copy is not the one being planned.
        """
        return ForgeError(f"{path}: not something a checkout at {self.root} can answer")

    def _unwritable(self) -> ForgeError:
        return ForgeError(
            f"{self.root}: swage writes nothing to GitHub from a checkout"
        )
