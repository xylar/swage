"""Compare a replayed sweep against the reference run (DESIGN.md §14.2).

    scripts/compare_replay.py REFERENCE_RUN CANDIDATE_RUN

Both are run directories under a snapshot's `runs/`, each holding `run.json`
and `recipes/`. Every feedstock whose outcome or rendered recipe differs is
named, with a unified diff of the recipe where that is what changed. Exit
status 1 where anything differed, so a sweep can gate on it.

Outcomes are compared under DESIGN.md §11.2's mapping: a reference run that
says `proposed` and a candidate that says `needs-review` agree.
"""

from __future__ import annotations

import difflib
import json
import sys
from pathlib import Path

#: DESIGN.md §11.2, second column to first.
V1_OUTCOMES = {
    "merge-ready": "automerge",
    "proposed": "needs-review",
    "degraded": "needs-review",
    "archived": "skipped",
    "unmaintained": "skipped",
    "not-reconciled": "not-read",
}


def _outcomes(run: Path) -> dict[str, str]:
    record = json.loads((run / "run.json").read_text(encoding="utf-8"))
    return {
        entry["feedstock"]: V1_OUTCOMES.get(entry["outcome"], entry["outcome"])
        for entry in record["feedstocks"]
    }


def _recipes(run: Path) -> dict[str, str]:
    found: dict[str, str] = {}
    for path in sorted((run / "recipes").rglob("*")):
        if path.is_file():
            found[str(path.relative_to(run / "recipes"))] = path.read_text(
                encoding="utf-8"
            )
    return found


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    reference, candidate = Path(argv[1]), Path(argv[2])
    differences = 0

    before, after = _outcomes(reference), _outcomes(candidate)
    for feedstock in sorted(before.keys() | after.keys()):
        if before.get(feedstock) != after.get(feedstock):
            differences += 1
            print(f"{feedstock}: {before.get(feedstock)} -> {after.get(feedstock)}")

    old, new = _recipes(reference), _recipes(candidate)
    for name in sorted(old.keys() | new.keys()):
        if old.get(name) == new.get(name):
            continue
        differences += 1
        print(f"--- {name}")
        sys.stdout.writelines(
            difflib.unified_diff(
                old.get(name, "").splitlines(keepends=True),
                new.get(name, "").splitlines(keepends=True),
                fromfile=f"reference/{name}",
                tofile=f"candidate/{name}",
            )
        )

    total = len(before.keys() | after.keys())
    print(
        f"{total - len({f for f in before if before.get(f) != after.get(f)})} of "
        f"{total} feedstocks in the same outcome; "
        f"{len(old) - sum(1 for n in old if old[n] != new.get(n))} of {len(old)} "
        f"rendered recipes identical"
    )
    return 1 if differences else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
