"""Measure recorded runs against DESIGN.md §3.2's budgets.

    scripts/budgets.py RUN_DIR [RUN_DIR ...]

Each is a run directory holding a `run.json` of schema 5 -- a v1 run has a
check's findings joined into one sentence and cannot be measured. For every
feedstock: the line beside its name, each finding's `said` half, and the
comment `update` would post, rendered with the rung `config/` gives that
feedstock. Every line over budget is printed under its surface, and the
exit status is 1 where there was one.

`tests/test_wording_budgets.py` is the same measurement over the corpus,
which plans cleanly; the recorded runs are where the findings are (§3.3).
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

from swage.cli.update import refusal_comment
from swage.config import ConfigError, load_config
from swage.plan import Finding
from swage.run.budgets import (
    COMMENT_BODY,
    FINDING_SAID,
    TERMINAL_LINE,
    comment_body,
    words,
)

CONFIG_ROOT = Path(__file__).resolve().parents[1] / "config"


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    tree = load_config(CONFIG_ROOT)
    over: dict[str, list[str]] = defaultdict(list)
    measured = 0
    for run_dir in argv[1:]:
        run = json.loads((Path(run_dir) / "run.json").read_text(encoding="utf-8"))
        if run.get("schema") != 5:
            print(f"{run_dir}: schema {run.get('schema')}, not 5; skipped")
            continue
        for entry in run["feedstocks"]:
            measured += 1
            name = entry["feedstock"]
            if words(entry["reason"]) > TERMINAL_LINE:
                over["terminal line"].append(f"{name}: {entry['reason']}")
            findings = tuple(
                Finding(f["kind"], f["subject"], f["where"], f["said"], f["remedy"])
                for f in entry["findings"]
            )
            for finding in findings:
                if words(finding.said) > FINDING_SAID:
                    over["finding"].append(f"{name}: {finding.said}")
            try:
                config = tree.for_feedstock(name)
            except ConfigError:
                continue
            upstream = entry.get("upstream") or {}
            release = f"{upstream.get('name') or name} {upstream.get('version') or ''}"
            comment = refusal_comment(release.strip(), findings, config)
            if words(comment_body(comment)) > COMMENT_BODY:
                over["comment"].append(f"{name}:\n{comment}")
    budgets = {
        "terminal line": TERMINAL_LINE,
        "finding": FINDING_SAID,
        "comment": COMMENT_BODY,
    }
    for surface, budget in budgets.items():
        lines = over[surface]
        print(f"{surface}: {len(lines)} over {budget} words")
        for line in lines:
            print(f"  {line}")
    print(f"{measured} feedstocks measured")
    return 1 if any(over.values()) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
