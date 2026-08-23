"""No tracked file carries an unresolved merge conflict.

`main` once did, and for two weeks. #137 narrowed a rule in `DESIGN.md`'s gate
table and the merge that followed left both wordings in the file with the
markers between them, plus a row stranded on the far side.

**git had already said its piece.** It raised the conflict at merge time, as it
always does; what reached `main` was a bad resolution -- the file staged with
the markers still in it -- and after that there was nothing left for git to
complain about. So the gap this closes is not in git, it is here: `pixi run
check` was green on that tree, because the suite reads the design's fenced YAML
blocks to prove they are config the loader accepts and never looks at the prose
around them. The one file every instruction in `CLAUDE.md` says to read first
was the file nothing checked.

Every tracked file, therefore, and not only the ones that parse as something.
Prose is where this happened and prose is what no other check reads.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO = Path(__file__).parents[1]

#: Built rather than written, so this file does not match itself.
START = "<" * 7
BASE = "|" * 7
MIDDLE = "=" * 7
END = ">" * 7


def _tracked() -> list[Path]:
    """Every file git tracks, which is the population that can reach `main`.

    `git ls-files` rather than a walk: the pixi environment, the run artifacts
    and the caches are all untracked, all enormous, and none of them can carry
    a conflict into a commit.
    """
    listing = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    )
    return [REPO / name for name in listing.stdout.split("\0") if name]


def _markers(text: str) -> list[tuple[int, str]]:
    """The conflict markers in one file's text, with the lines they are on.

    A marker is seven characters at the start of a line, followed by end of
    line or a space -- git's own shape, `>>>>>>> theirs` and `=======` alone.

    **The middle marker only counts beside an outer one.** Seven equals is also
    how reStructuredText underlines a heading, and ten `PKG-INFO` files in
    `tests/corpus/` open with a longer run of them. Requiring exactly seven
    already separates those, and requiring an angle bracket somewhere in the
    same file costs nothing real: a resolution that dropped the opening marker
    and kept the middle one is not a thing that happens.
    """
    found = []
    for number, line in enumerate(text.splitlines(), 1):
        for marker in (START, BASE, END):
            if line.startswith(marker) and line[7:8] in ("", " "):
                found.append((number, marker))
        if line == MIDDLE:
            found.append((number, MIDDLE))
    if not any(marker in (START, BASE, END) for _, marker in found):
        return []
    return found


def test_no_tracked_file_has_an_unresolved_merge_conflict() -> None:
    tracked = _tracked()
    assert len(tracked) > 300, "git ls-files should list the whole repository"
    offenders = []
    for path in tracked:
        if not path.is_file():
            continue
        for number, marker in _markers(path.read_text(encoding="utf-8")):
            offenders.append(f"{path.relative_to(REPO)}:{number}: {marker}")
    assert not offenders, "unresolved merge conflict:\n" + "\n".join(offenders)


def test_a_conflict_in_prose_is_what_this_catches() -> None:
    """The shape that reached `main`: markdown, nothing that parses, and a
    real edit on both sides of it."""
    conflicted = "\n".join(
        [
            "| G13 | a rule |",
            f"{START} HEAD",
            "| G14 | the narrowed wording |",
            MIDDLE,
            "| G14 | the wording it replaced |",
            f"{END} main",
        ]
    )
    assert [marker for _, marker in _markers(conflicted)] == [START, MIDDLE, END]


def test_a_heading_underline_is_not_a_conflict() -> None:
    """`tests/corpus/google-cloud/*/PKG-INFO` open with one of these."""
    assert _markers("Description\n" + "=" * 40 + "\n") == []
    assert _markers("Description\n" + MIDDLE + "\n") == []
