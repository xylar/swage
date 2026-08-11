#!/usr/bin/env python
"""Phase 1 round-trip spike: can conda-recipe-manager carry a real recipe?

DESIGN.md 3.1 makes swage's whole `recipe` layer conditional on this question,
so it gets answered before any code depends on the answer. For every recipe
handed to it, this script checks four things:

1. **Read/render fidelity.** Parse and immediately render. Anything other than
   a byte-identical result is formatting swage did not ask for.
2. **Comment survival and placement.** Comments must come out with their text
   *and their indentation* intact -- swage's marker convention (DESIGN.md 6)
   puts `# start X` / `# end X` around a block of dependencies, so a comment
   that drifts to another indentation level has changed which block it marks.
3. **Edit fidelity.** Patch one dependency the way swage would, and check that
   the render differs only in that dependency.
4. **Edit safety.** Replace, append, and remove a dependency, then confirm the
   result still parses as YAML and still has the structure it started with.
   This is the check that matters most: a silently corrupted document is worse
   than a failed edit.

Usage:

    python spikes/crm_roundtrip.py [PATH ...] [--diffs DIR]

With no PATH, the vendored corpus under tests/corpus/ is used.
"""

from __future__ import annotations

import argparse
import difflib
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from conda_recipe_manager.parser.recipe_parser import RecipeParser
from conda_recipe_manager.parser.recipe_reader import RecipeReader

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOTS = [REPO_ROOT / "tests" / "corpus"]

COMMENT_RE = re.compile(r"^(\s*)#\s?(.*?)\s*$")


@dataclass
class EditOutcome:
    op: str
    list_path: str = ""
    applied: bool = False
    error: str | None = None
    #: Lines that changed other than the one the edit targeted.
    stray_lines: list[str] = field(default_factory=list)
    #: Did the rendered result still parse as YAML at all?
    parses: bool = False
    #: Did the document keep the shape it had, apart from the intended change?
    structure_intact: bool = False
    diff: str = ""


@dataclass
class Result:
    path: Path
    schema_version: str = "?"
    parse_error: str | None = None
    identical: bool = False
    comments_in: int = 0
    comments_out: int = 0
    moved_comments: list[str] = field(default_factory=list)
    diff: str = ""
    edits: list[EditOutcome] = field(default_factory=list)


def comment_census(text: str) -> list[tuple[int, str]]:
    """Every whole-line comment as (indentation, text).

    Trailing comments on a value line are ignored: they belong to the value,
    and swage's markers are always whole-line.
    """
    found: list[tuple[int, str]] = []
    for line in text.splitlines():
        match = COMMENT_RE.match(line)
        if match:
            found.append((len(match.group(1)), match.group(2)))
    return found


def unified(before: str, after: str, label: str, n: int = 2) -> str:
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"{label} (before)",
            tofile=f"{label} (after)",
            n=n,
        )
    )


def changed_lines(before: str, after: str) -> tuple[list[str], list[str]]:
    before_lines = before.splitlines()
    after_lines = after.splitlines()
    matcher = difflib.SequenceMatcher(None, before_lines, after_lines, autojunk=False)
    removed: list[str] = []
    added: list[str] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in {"replace", "delete"}:
            removed.extend(before_lines[i1:i2])
        if tag in {"replace", "insert"}:
            added.extend(after_lines[j1:j2])
    return removed, added


def at_path(data: Any, path: str) -> Any:
    node = data
    for part in path.strip("/").split("/"):
        node = node[int(part)] if isinstance(node, list) else node[part]
    return node


def replace_at_path(data: Any, path: str, value: Any) -> Any:
    """Return ``data`` with ``path`` set to ``value``, without mutating it."""
    parts = path.strip("/").split("/")
    node = data
    for part in parts[:-1]:
        node = node[int(part)] if isinstance(node, list) else node[part]
    last = parts[-1]
    if isinstance(node, list):
        node[int(last)] = value
    else:
        node[last] = value
    return data


def check_structure(
    baseline_text: str, edited_text: str, list_path: str, expected: list[Any]
) -> tuple[bool, bool]:
    """Did the edited render keep the document's shape?

    Returns ``(parses, structure_intact)``. The edited list must have become
    exactly ``expected``; putting the original list back must then reproduce
    the baseline document, so anything left over is a change the edit was not
    asked to make.
    """
    try:
        edited = yaml.safe_load(edited_text)
        baseline = yaml.safe_load(baseline_text)
    except yaml.YAMLError:
        return False, False
    if edited is None or baseline is None:
        return False, False
    try:
        if at_path(edited, list_path) != expected:
            return True, False
        undone = replace_at_path(edited, list_path, at_path(baseline, list_path))
    except (KeyError, IndexError, TypeError):
        return True, False
    return True, undone == baseline


def run_edit(
    original: str, baseline: str, list_path: str, op_name: str, label: str
) -> EditOutcome:
    outcome = EditOutcome(op=op_name, list_path=list_path)
    parser = RecipeParser(original)
    try:
        current = parser.get_value(list_path)
    except Exception as exc:  # noqa: BLE001 - the spike reports, it does not handle
        outcome.error = f"{type(exc).__name__}: {exc}"
        return outcome
    if not isinstance(current, list) or not current:
        outcome.error = f"{list_path} is not a non-empty list"
        return outcome

    spiked = "swage-spiked-dependency >=1.0"
    # The lines the edit is entitled to change; anything else that moves is the
    # renderer rewriting something swage did not ask about.
    touched: set[str] = {spiked}
    if op_name == "replace":
        patch = {"op": "replace", "path": f"{list_path}/0", "value": spiked}
        expected = [spiked, *current[1:]]
        touched.add(str(current[0]))
    elif op_name == "add":
        patch = {"op": "add", "path": f"{list_path}/-", "value": spiked}
        expected = [*current, spiked]
    else:
        patch = {"op": "remove", "path": f"{list_path}/{len(current) - 1}"}
        expected = list(current[:-1])
        touched.add(str(current[-1]))

    try:
        outcome.applied = parser.patch(patch)
    except Exception as exc:  # noqa: BLE001 - the spike reports, it does not handle
        outcome.error = f"{type(exc).__name__}: {exc}"
        return outcome
    if not outcome.applied:
        outcome.error = "patch returned False"
        return outcome

    rendered = parser.render()
    outcome.diff = unified(baseline, rendered, label)
    removed, added = changed_lines(baseline, rendered)
    outcome.stray_lines = [
        line
        for line in removed + added
        if not any(value in line for value in touched)
    ]
    outcome.parses, outcome.structure_intact = check_structure(
        baseline, rendered, list_path, expected
    )
    return outcome


def dependency_lists(reader: RecipeReader) -> list[str]:
    """Every requirements list swage might rewrite, in document order.

    All of them, not just the first: the failure this spike is looking for
    depends on what surrounds a particular list, so probing one list per recipe
    would miss it.
    """
    seen: list[str] = []
    for path in reader.get_dependency_paths():
        parent = path.rsplit("/", 1)[0]
        if parent.endswith(("/run", "/host", "/build")) and parent not in seen:
            seen.append(parent)
    return seen


def check(path: Path) -> Result:
    original = path.read_text(encoding="utf-8")
    census_in = comment_census(original)
    result = Result(path=path, comments_in=len(census_in))

    try:
        reader = RecipeReader(original)
    except Exception as exc:  # noqa: BLE001 - the spike reports, it does not handle
        result.parse_error = f"{type(exc).__name__}: {exc}"
        return result

    result.schema_version = str(reader.get_schema_version())
    rendered = reader.render()
    census_out = comment_census(rendered)
    result.comments_out = len(census_out)
    result.identical = rendered == original
    if not result.identical:
        result.diff = unified(original, rendered, _rel(path))

    # A comment whose text survives but whose indentation changed has moved to
    # a different part of the document.
    out_by_text: dict[str, list[int]] = {}
    for indent, text in census_out:
        out_by_text.setdefault(text, []).append(indent)
    for indent, text in census_in:
        indents = out_by_text.get(text)
        if indents and indent not in indents:
            result.moved_comments.append(f"{text!r}: {indent} -> {indents[0]}")

    for list_path in dependency_lists(reader):
        for op_name in ("replace", "add", "remove"):
            result.edits.append(
                run_edit(original, rendered, list_path, op_name, _rel(path))
            )
    return result


def collect(roots: list[Path]) -> list[Path]:
    found: list[Path] = []
    for root in roots:
        if root.is_file():
            found.append(root)
        else:
            found.extend(sorted(root.rglob("*recipe.yaml")))
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path, default=None)
    parser.add_argument(
        "--diffs", type=Path, default=None, help="write full diffs to this directory"
    )
    parser.add_argument(
        "--show", type=int, default=0, help="how many sample render diffs to print"
    )
    args = parser.parse_args(argv)

    recipes = collect(args.paths or DEFAULT_ROOTS)
    if not recipes:
        print("no recipes found", file=sys.stderr)
        return 2

    results = [check(path) for path in recipes]

    failed_parse = [r for r in results if r.parse_error]
    identical = [r for r in results if r.identical]
    differed = [r for r in results if not r.identical and not r.parse_error]
    lost = [r for r in results if r.comments_out < r.comments_in]
    moved = [r for r in results if r.moved_comments]

    print(f"recipes checked:          {len(results)}")
    print(f"  parse failed:           {len(failed_parse)}")
    print(f"  rendered identical:     {len(identical)}")
    print(f"  rendered differently:   {len(differed)}")
    print(f"  comments lost:          {len(lost)}")
    print(f"  comments moved:         {len(moved)}")

    versions: dict[str, int] = {}
    for result in results:
        versions[result.schema_version] = versions.get(result.schema_version, 0) + 1
    print(f"  schema detected:        {versions}")

    print("\nedit safety, per operation:")
    for op_name in ("replace", "add", "remove"):
        outcomes = [e for r in results for e in r.edits if e.op == op_name]
        clean = [e for e in outcomes if e.structure_intact and not e.stray_lines]
        stray = [e for e in outcomes if e.structure_intact and e.stray_lines]
        broken = [e for e in outcomes if e.applied and not e.structure_intact]
        errors = [e for e in outcomes if e.error]
        print(
            f"  {op_name:<8} attempted={len(outcomes):<4} clean={len(clean):<4} "
            f"stray-lines={len(stray):<4} STRUCTURE BROKEN={len(broken):<4} "
            f"errors={len(errors)}"
        )

    broken_by_file = [
        (r, e) for r in results for e in r.edits if e.applied and not e.structure_intact
    ]
    if broken_by_file:
        print("\n--- edits that changed the document's structure ---")
        for result, outcome in broken_by_file:
            note = "does not parse as YAML" if not outcome.parses else "shape changed"
            print(f"{_rel(result.path)} [{outcome.op} {outcome.list_path}]: {note}")

    stray_by_file = [
        (r, e) for r in results for e in r.edits if e.structure_intact and e.stray_lines
    ]
    if stray_by_file:
        print("\n--- edits that also rewrote lines they were not asked to ---")
        for result, outcome in stray_by_file:
            sample = "; ".join(line.strip() for line in outcome.stray_lines[:3])
            print(f"{_rel(result.path)} [{outcome.op} {outcome.list_path}]: {sample}")

    if moved:
        print("\n--- comments that changed indentation ---")
        for result in moved:
            for note in result.moved_comments:
                print(f"{_rel(result.path)}: {note}")

    if failed_parse:
        print("\n--- parse failures ---")
        for result in failed_parse:
            print(f"{_rel(result.path)}: {result.parse_error}")

    if differed and args.show:
        print(f"\n--- sample render diffs ({min(args.show, len(differed))}) ---")
        for result in differed[: args.show]:
            print(f"\n### {_rel(result.path)}")
            print(result.diff)

    if args.diffs:
        args.diffs.mkdir(parents=True, exist_ok=True)
        for result in results:
            stem = _rel(result.path).replace("/", "__")
            if result.diff:
                (args.diffs / f"{stem}.render.diff").write_text(
                    result.diff, encoding="utf-8"
                )
            for outcome in result.edits:
                if outcome.diff:
                    (args.diffs / f"{stem}.{outcome.op}.diff").write_text(
                        outcome.diff, encoding="utf-8"
                    )
        print(f"\nfull diffs written to {args.diffs}")

    return 0 if not differed and not failed_parse and not broken_by_file else 1


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


if __name__ == "__main__":
    sys.exit(main())
