"""The workbench `swage draft` assembles (v1 §8.1; DESIGN.md §11.3).

`FINDINGS.md` quotes the upstream metadata back beside each finding and names
the config key that answers it; `config.yaml` drafts only what swage can derive
without judgment, and pre-fills nothing a person has to decide.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from swage.plan import (
    CHECKS,
    Finding,
    Plan,
    PlannedSection,
    Unexplained,
    by_kind,
    summarize,
)
from swage.plan.lines import parse_line
from swage.plan.prose import section_phrase
from swage.recipe import Recipe
from swage.upstream import UpstreamMetadata

from .record import declaration_diff

__all__ = [
    "FAMILIES_DIR",
    "FamilyQuestion",
    "Workbench",
    "config_draft",
    "family_summary",
    "findings_markdown",
    "group_questions",
    "render_family",
    "render_workbench",
    "write_declaration_workbench",
    "write_workbench",
]

#: Where the workbench for one feedstock lives, under the cache root.
DRAFTS_DIR = "drafts"


@dataclass(frozen=True)
class Workbench:
    """What `draft` wrote, and where."""

    directory: Path
    files: tuple[Path, ...]


def write_declaration_workbench(
    directory: Path,
    feedstock: str,
    recipe: Recipe,
    reason: str,
    texts: Mapping[str, str],
    previous: Mapping[str, str] | None = None,
) -> Workbench:
    """The workbench for a feedstock swage does not read (v1 §3.6.8).

    No rendering, no diff and no config draft, because reaching here means
    the config already exists. `upstream.diff` and `upstream.before/` are the
    useful half. ``previous`` is the release the recipe is moving from, or
    None.
    """
    directory.mkdir(parents=True, exist_ok=True)
    moved = (
        None
        if previous is None
        else tuple(name for name, text in texts.items() if previous.get(name) != text)
    )
    written = [
        _write(directory / "recipe.yaml", recipe.text),
        _write(
            directory / "FINDINGS.md",
            _declaration_findings(feedstock, recipe, reason, texts, moved),
        ),
    ]
    for name, text in texts.items():
        written.append(_write(directory / "upstream" / name, text))
    if moved:
        for name in moved:
            was = (previous or {}).get(name)
            if was is not None:
                written.append(_write(directory / "upstream.before" / name, was))
        written.append(
            _write(
                directory / "upstream.diff",
                declaration_diff(texts, previous or {}, moved),
            )
        )
    return Workbench(directory, tuple(written))


def _declaration_findings(
    feedstock: str,
    recipe: Recipe,
    reason: str,
    texts: Mapping[str, str],
    moved: Sequence[str] | None,
) -> str:
    """What to say when there are no findings, because nothing was read: the
    files, which moved, and that none was reconciled.
    """
    version = recipe.context.get("version")
    lines = [f"# {feedstock}", ""]
    lines.append(
        f"Upstream {feedstock} {version}." if version else f"Upstream {feedstock}."
    )
    lines += ["", "## What swage did not do", "", reason, ""]
    lines += [
        "swage reconciled nothing here, so no line in this recipe has been",
        "checked against upstream. The files below are what upstream states",
        "its dependencies in; they are in `upstream/` at their own paths.",
        "",
        "## Where upstream declares",
        "",
    ]
    for name in texts:
        mark = "  **changed in this release**" if name in (moved or ()) else ""
        lines.append(f"- `{name}`{mark}")
    lines.append("")
    if moved is None:
        # The default-branch case: the recipe and upstream name the same
        # release.
        lines += [
            "swage has no other release to compare these against here. Where",
            "it does -- a bot pull request bumping the version -- it writes",
            "`upstream.diff`, which is every change the new release made to",
            "the files above.",
        ]
    elif moved:
        lines += [
            "**`upstream.diff` has the changes**, one unified diff per file,",
            "against the release this recipe is moving from. Their previous",
            "contents are in `upstream.before/` if the diff needs more context",
            "than it carries. swage does not read any of it -- what it can say",
            "is which lines moved, and that is usually enough to see whether a",
            "dependency did.",
        ]
    else:
        lines += [
            "None of them differ from the release this recipe is moving from,",
            "so upstream restated its dependencies in exactly the same words.",
        ]
    return "\n".join(lines) + "\n"


def write_workbench(
    directory: Path,
    feedstock: str,
    plan: Plan,
    findings: Sequence[Finding],
    upstream: UpstreamMetadata,
    texts: Mapping[str, str],
    rung: str = "",
) -> Workbench:
    """Assemble the workbench for one feedstock into ``directory``.

    Read-only against everything but itself; `--apply` is a separate
    function. ``rung`` is the sentence about the trust rung where it is not
    `auto`, listed beside the findings.
    """
    directory.mkdir(parents=True, exist_ok=True)
    recipe = plan.recipe
    written = [
        # As read, so a corrected source version shows in the diff.
        _write(directory / "recipe.yaml", plan.read),
        _write(directory / "recipe.swage.yaml", plan.rendered),
        _write(directory / "recipe.diff", _diff(plan.read, plan.rendered)),
        _write(
            directory / "FINDINGS.md",
            findings_markdown(feedstock, plan, findings, upstream, texts, recipe, rung),
        ),
        _write(directory / "config.yaml", config_draft(feedstock, recipe, upstream)),
    ]
    for name, text in texts.items():
        written.append(_write(directory / "upstream" / name, text))
    return Workbench(directory, tuple(written))


def render_workbench(workbench: Workbench, applied: Path | None) -> str:
    """What the terminal says about a workbench that has just been written:
    the two files worth opening first.
    """
    out = [
        f"  workbench: {_short(workbench.directory)}",
        "    FINDINGS.md    what is undecided, and what upstream says about it",
        "    recipe.diff    what swage would change",
    ]
    if applied is not None:
        out.append(f"  drafted config: {_short(applied)}")
        if applied.name.endswith(".yaml.draft"):
            out.append(
                "    this feedstock already had a config, so the draft is "
                "beside it rather than over it"
            )
    else:
        out.append("  copy the config in with --apply once you have decided")
    return "\n".join(out) + "\n"


def _short(path: Path) -> str:
    """`~`-relative where it helps, absolute where it does not, in the
    platform's own separators.
    """
    try:
        return str(Path("~") / path.relative_to(Path.home()))
    except ValueError:
        return str(path)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _diff(before: str, after: str) -> str:
    """The two recipes, unified. Empty where swage would change nothing."""
    lines = difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile="recipe.yaml",
        tofile="recipe.swage.yaml",
    )
    return "".join(lines)


def findings_markdown(
    feedstock: str,
    plan: Plan,
    findings: Sequence[Finding],
    upstream: UpstreamMetadata,
    texts: Mapping[str, str],
    recipe: Recipe | None = None,
    rung: str = "",
) -> str:
    """Each thing holding this feedstock, with the evidence for deciding it."""
    rows = _rows(findings, rung)
    version = upstream.version or "unknown version"
    out = [
        f"# {feedstock}",
        "",
        f"Upstream {upstream.name or feedstock} {version}.",
        "",
    ]

    if not rows:
        out += [
            "Nothing is holding this feedstock. swage can account for every",
            "requirement in the recipe, so there is no decision to write down.",
            "",
        ]
    else:
        out += ["## What is holding it", ""]
        out += [f"- {said}" for _, _, each in rows for said in each]
        out += [""]

        out += _where_to_write(feedstock, rows)

    unexplained = plan.unexplained
    if unexplained:
        out += [
            "## The names to decide about",
            "",
            "Each of these is a line swage cannot attribute to anything "
            "upstream declares.",
            "Every mention of the name in the metadata swage read is quoted "
            "beneath it, so",
            "the decision can be made from this file.",
            "",
        ]
        for section in plan.sections:
            for item in section.unexplained:
                out += _finding(_where(section), item, texts, recipe)

    extras = _unaccounted(upstream, plan)
    if extras:
        out += [
            "## Extras nothing draws on",
            "",
            "Upstream declares these and no output in the recipe uses them. "
            "Recording a",
            "decision means listing each as supported or skipped in `config.yaml`.",
            "",
        ]
        out += [f"- `{extra}`" for extra in extras]
        out += [""]

    return "\n".join(out).rstrip() + "\n"


#: The config key each check is answered with, and the shape of the answer.
#: Shape, never content (v1 §8.1): every stub has a placeholder where the
#: decision goes. A check with no entry is one no config key answers.
ANSWERED_WITH: dict[str, tuple[tuple[str, ...], str]] = {
    "G1": (
        ("add_requirements", "temporary_requirements"),
        "add_requirements:\n"
        "  run:\n"
        "    - line: <the requirement, exactly as the recipe spells it>\n"
        "      reason: <why conda-forge needs it -- `TODO` is refused>\n"
        "# `add_requirements` is for a dependency conda-forge needs for good.\n"
        "# Use the same shape under `temporary_requirements` for a line working\n"
        "# around another package's metadata: swage keeps it and asks again at\n"
        "# every version bump instead of letting it become permanent.",
    ),
    "G2": (
        ("name_map", "embedded_extras"),
        "# If conda-forge publishes a package for it:\n"
        "name_map:\n"
        "  <upstream name>: <conda-forge name>\n"
        "\n"
        "# If it does not, write out what the extra pulls in:\n"
        "embedded_extras:\n"
        '  "<upstream requirement, with its extra>":\n'
        "    - <conda-forge package the extra pulls in>",
    ),
    "G3": (
        ("extras_as_outputs",),
        "extras_as_outputs:\n"
        "  suffix: <how an extra's output is named>\n"
        "  supported: [<extras published as outputs>]\n"
        "  skip: [<extras deliberately not published>]\n"
        "# Both lists together must name every extra upstream declares.",
    ),
    "G6": (
        ("trust",),
        "trust: propose   # or `auto` to let conda-forge merge it unattended",
    ),
    "G8": (
        ("retire", "removals"),
        "# If the line is an artifact of a tool swage replaces:\n"
        "retire:\n"
        "  - <the package name>\n"
        "\n"
        "# If upstream genuinely dropped it and that is routine here:\n"
        "removals: auto",
    ),
    "G9": (
        ("run_constraints",),
        "run_constraints:\n"
        "  <the package the entry constrains>:\n"
        "    extra: <the upstream extra it tracks>\n"
        "# `extra: null` is a real answer: it says the bound is deliberate\n"
        "# and tracks nothing upstream.",
    ),
    "G10": (
        ("dynamic_dependencies",),
        "dynamic_dependencies: auto"
        "   # upstream computes the list; accept it as complete",
    ),
    "G11": (
        ("constraints", "temporary_constraints", "add_requirements"),
        "constraints:\n"
        "  <package>:\n"
        "    bound: <the bound upstream does not ask for>\n"
        "    reason: <why this feedstock states it -- `TODO` is refused>\n"
        "# `constraints` says the bound outlives the reason it was added for.\n"
        "# Move it to `temporary_constraints` to be asked again every update,\n"
        "# or drop the entry and let swage reconcile the line.\n"
        "#\n"
        "# For a whole line rather than a bound, the pair is\n"
        "# `add_requirements` and `temporary_requirements`, and it reads the\n"
        "# same way: move the entry to say the recipe means to keep it.",
    ),
    "G12": (
        ("test_matrix",),
        "test_matrix: auto   # let swage complete the python test matrix unattended",
    ),
    "G15": (
        ("entry_points",),
        "entry_points: manual   # the recipe's entry points are conda-forge's own;\n"
        "                       # swage leaves the list as written",
    ),
}


def _where_to_write(feedstock: str, rows: Sequence[_Row]) -> list[str]:
    """The key each failure is answered with, and the shape of the answer."""
    answerable = [
        (title, ANSWERED_WITH[name]) for name, title, _ in rows if name in ANSWERED_WITH
    ]
    if not answerable:
        return []
    out = [
        "## Where to write it down",
        "",
        f"Each of these goes in `config/feedstocks/{feedstock}.yaml`, or in the",
        "family file if the same answer is right for every feedstock in it.",
        "Placeholders mark the decision; swage does not make it.",
        "",
    ]
    for title, (keys, stub) in answerable:
        named = " or ".join(f"`{key}`" for key in keys)
        out += [f"### {title}", "", f"Answered with {named}:", "", "```yaml"]
        out += stub.splitlines()
        out += ["```", ""]
    out += [
        "Every key is described in `docs/configuration.md`, with worked",
        "examples from the feedstocks already using it.",
        "",
    ]
    return out


#: One check's findings as `FINDINGS.md` lists them: the v1 name the config
#: table is keyed by, what the check says when it fails, and one sentence per
#: thing found.
_Row = tuple[str, str, tuple[str, ...]]

#: The check the rung's row follows. v1 listed the rung as a check between the
#: fourth and the eighth, so a workbench lists it where it always did.
_RUNG_AFTER = "orphaned-output"

#: What the rung's row is headed, which is what v1's check said when it failed.
_RUNG_TITLE = "this feedstock's trust setting does not allow automatic merging"


def _rows(findings: Sequence[Finding], rung: str) -> tuple[_Row, ...]:
    """One row per check with findings, and one for the rung where there is one.

    One sentence per finding; what to do about the whole set is `Where to
    write it down`.
    """
    rows: list[_Row] = []
    grouped = by_kind(findings)
    for row in CHECKS:
        if row.kind in grouped:
            rows.append((row.v1, row.failure, tuple(f.said for f in grouped[row.kind])))
        if row.kind == _RUNG_AFTER and rung:
            rows.append(("G6", _RUNG_TITLE, (rung,)))
    return tuple(rows)


def _where(section: PlannedSection) -> str:
    """Where a finding is, never as a position in the parsed document
    (`plan.prose`).
    """
    return section.where or section_phrase(section.section)


def _finding(
    where: str,
    item: Unexplained,
    texts: Mapping[str, str],
    recipe: Recipe | None = None,
) -> list[str]:
    name = parse_line(item.text).name
    out = [
        f"### `{item.text}`",
        "",
        f"In {where}, {item.kind}.",
        "",
        f"{item.message}",
        "",
    ]
    out += _in_the_recipe(name, recipe)
    for filename, text in texts.items():
        mentions = _mentions(name, text)
        out.append(f"Every mention of `{name}` in {filename}:")
        out.append("")
        if mentions:
            out += [f"    {number}: {line}" for number, line in mentions]
        else:
            # A real answer rather than an omission: a name upstream never
            # mentions is the whole case for dropping the line.
            out.append("    (none)")
        out.append("")
    return out


def _in_the_recipe(name: str, recipe: Recipe | None) -> list[str]:
    """The recipe's own line for this name, with whatever comment sits above it:
    the evidence for a hand-expanded extra is in the recipe, not upstream.
    """
    if recipe is None:
        return []
    wanted = _comparable(name)
    lines = recipe.text.splitlines()
    quoted: list[str] = []
    for number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped.startswith("-") or wanted not in _comparable(stripped):
            continue
        above = lines[number - 2].strip() if number >= 2 else ""
        if above.startswith("#"):
            quoted.append(f"    {number - 1}: {above}")
        quoted.append(f"    {number}: {stripped}")
    if not quoted:
        return []
    return [
        "What the recipe says, with any comment above it:",
        "",
        *quoted,
        "",
    ]


def _mentions(name: str, text: str) -> list[tuple[int, str]]:
    """Every line quoting ``name``, matched the way a package name compares:
    `-` and `_` alike, case ignored.
    """
    wanted = _comparable(name)
    if not wanted:
        return []
    return [
        (number, line.strip())
        for number, line in enumerate(text.splitlines(), start=1)
        if wanted in _comparable(line)
    ]


def _comparable(text: str) -> str:
    return text.replace("_", "-").replace(".", "-").lower()


def _unaccounted(upstream: UpstreamMetadata, plan: Plan) -> tuple[str, ...]:
    """Upstream extras the plan could not tie to anything."""
    return plan.unaccounted_extras or tuple(upstream.extras)


def config_draft(feedstock: str, recipe: Recipe, upstream: UpstreamMetadata) -> str:
    """The config file this feedstock would need, minus every judgment call.

    Derivable: which outputs the recipe publishes, which upstream extras they
    correspond to, and how those names are spelled. A recipe that publishes
    an output per extra takes `extras_as_outputs`; one that does not takes
    `outputs[].run.skip` (v1 §4).
    """
    supported, candidates = _extras_by_output(recipe, upstream)
    out = [
        f"# Drafted by `swage draft {feedstock}`. Nothing here is a decision;",
        "# read FINDINGS.md, then say what each name means and delete this note.",
        "",
        f"feedstock: {feedstock}",
    ]
    if supported:
        # `suffix` is required, and is read off the output names.
        suffix = _suffix(feedstock, recipe, supported[0])
        out += ["", "extras_as_outputs:", f'  suffix: "{suffix}"', "  supported:"]
        out += [f"    - {extra}" for extra in supported]
        if candidates:
            out += ["  # skip:"] + [f"  #   - {extra}" for extra in candidates]
    elif candidates:
        # The whole block is commented, its key included; a key with no value
        # would load as nothing and stay in the file.
        out += ["", "# outputs:", f"#   {_folding_output(feedstock, recipe)}:"]
        # `core: true` decides nothing, so `skip` is the only new claim.
        out += ["#     run:", "#       core: true", "#       skip:"]
        out += [f"#         - {extra}" for extra in candidates]
    if candidates:
        out += [
            "",
            "# Upstream declares the extras above and no output is named for",
            "# them. Listing one under `skip` records a decision not to publish",
            "# it; leaving it out means nobody has looked yet.",
        ]
    return "\n".join(out) + "\n"


def _suffix(feedstock: str, recipe: Recipe, extra: str) -> str:
    """How this recipe names an output built from ``extra``, as a template
    derived from an output it already has. A stem that is not the package
    name is written out literally.
    """
    package = recipe.context.get("name", feedstock)
    for output in recipe.outputs:
        name = output.name
        if name is None or not _comparable(name).endswith(f"-{_comparable(extra)}"):
            continue
        stem = name[: len(name) - len(extra) - 1]
        if _comparable(stem).startswith(_comparable(package)):
            return "{name}" + stem[len(package) :] + "-{extra}"
        return stem + "-{extra}"
    return "{name}-{extra}"


def _folding_output(feedstock: str, recipe: Recipe) -> str:
    """Which output an extra would be folded into, where that is unambiguous;
    several make it a decision, and the placeholder says so.
    """
    if len(recipe.outputs) == 1:
        return recipe.outputs[0].name or feedstock
    return "<the output to fold them into>"


def _extras_by_output(
    recipe: Recipe, upstream: UpstreamMetadata
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Which upstream extras the recipe already publishes an output for."""
    names = [output.name for output in recipe.outputs if output.name]
    supported = []
    candidates = []
    for extra in upstream.extras:
        if _published_as(extra, names):
            supported.append(extra)
        else:
            candidates.append(extra)
    return tuple(supported), tuple(candidates)


def _published_as(extra: str, outputs: Sequence[str]) -> bool:
    wanted = _comparable(extra)
    return any(_comparable(name).endswith(f"-{wanted}") for name in outputs)


#: Where a family's workbenches and their summary live, under the cache root.
FAMILIES_DIR = "families"


@dataclass(frozen=True)
class FamilyQuestion:
    """One question, and every feedstock in the family that asks it."""

    #: The gate's identifier. A stable key for the artifact and for the code,
    #: and never printed -- renderers use `title` (CLAUDE.md).
    gate: str
    #: What the check asks, in words that need no design document.
    title: str
    #: The distinct wordings behind this question, most common first. Usually
    #: one; several where the same question is asked about different names.
    details: tuple[str, ...]
    feedstocks: tuple[str, ...]


def group_questions(
    held: Mapping[str, Sequence[Finding]],
) -> tuple[FamilyQuestion, ...]:
    """Collapse a family's gate failures into the questions they represent
    (v1 §8.1).

    Two failures are the same question when they come from the same check and
    their wording matches once names and versions are taken out. The concrete
    wordings are kept underneath. The trust rung is not a question.
    """
    by_question: dict[tuple[str, str], dict[str, list[str]]] = {}
    titles: dict[tuple[str, str], str] = {}
    for feedstock, findings in held.items():
        for found_here in by_kind(findings).values():
            row = found_here[0].check
            said = summarize(found_here)
            key = (row.v1, _shape(said))
            titles[key] = row.failure
            found = by_question.setdefault(key, {})
            found.setdefault(said or row.failure, []).append(feedstock)

    questions = [
        FamilyQuestion(
            gate=key[0],
            title=titles[key],
            details=tuple(
                detail
                for detail, _ in sorted(
                    details.items(), key=lambda item: (-len(item[1]), item[0])
                )
            ),
            feedstocks=tuple(sorted({f for names in details.values() for f in names})),
        )
        for key, details in by_question.items()
    ]
    return tuple(sorted(questions, key=lambda q: (-len(q.feedstocks), q.gate)))


def _shape(detail: str) -> str:
    """A gate detail with the particulars taken out, for grouping: the fenced
    spans go, and the punctuation between them.
    """
    without_names = re.sub(r"`[^`]*`", " ", detail)
    return re.sub(r"[^a-z]+", " ", without_names.lower()).strip()


#: How many feedstocks a question names before the rest are counted, and how
#: many wordings it quotes.
_NAMED = 8
_QUOTED = 3


def family_summary(
    family: str,
    config_file: str | None,
    questions: Sequence[FamilyQuestion],
    settled: Sequence[str],
    refused: Mapping[str, str],
) -> str:
    """What a set of workbenches say when read together: the handful of
    decisions they represent, and where each can be written down once.

    ``config_file`` is the one file that could answer a shared question, and
    None for feedstocks named on the command line rather than by a family.
    """
    total = len(questions)
    held = {feedstock for q in questions for feedstock in q.feedstocks}
    drafted = len(held) + len(settled) + len(refused)
    out = [
        f"# {family}",
        "",
        f"{drafted} feedstocks drafted. "
        f"{total} question{'' if total == 1 else 's'} between them.",
        "",
    ]
    if not questions:
        scope = "this family" if config_file is not None else "here"
        out += [
            f"Nothing in {scope} is waiting on a decision. Every feedstock's",
            "requirements are accounted for, so there is nothing to write down.",
            "",
        ]

    for index, question in enumerate(questions, start=1):
        count = len(question.feedstocks)
        out += [
            f"## {index}. {question.title}",
            "",
            f"Asked by {count} feedstock{'' if count == 1 else 's'}:",
            "",
        ]
        named = ", ".join(question.feedstocks[:_NAMED])
        rest = count - min(count, _NAMED)
        out += [f"    {named}" + (f", and {rest} more" if rest else ""), ""]
        out += ["What they report:", ""]
        for detail in question.details[:_QUOTED]:
            out.append(f"    {detail}")
        if len(question.details) > _QUOTED:
            out.append(f"    ... and {len(question.details) - _QUOTED} more wordings")
        out += [""]
        # Where, never what (v1 §8.1).
        out += [
            _where_to_answer(question, config_file),
            "",
            f"Evidence is in each feedstock's `FINDINGS.md`, starting with"
            f" `{question.feedstocks[0]}/FINDINGS.md`.",
            "",
        ]

    if settled:
        out += [
            "## Waiting on nothing",
            "",
            f"{len(settled)} feedstock{'' if len(settled) == 1 else 's'} whose"
            " requirements are already accounted for:",
            "",
            f"    {', '.join(settled[:_NAMED])}"
            + (f", and {len(settled) - _NAMED} more" if len(settled) > _NAMED else ""),
            "",
        ]

    if refused:
        out += [
            "## Not drafted",
            "",
            "swage could not assemble a workbench for these, and each says why:",
            "",
        ]
        out += [f"    {name}  --  {reason}" for name, reason in sorted(refused.items())]
        out += [""]
    return "\n".join(out)


def _where_to_answer(question: FamilyQuestion, config_file: str | None) -> str:
    """Which file answers this question, for however many feedstocks ask it.

    Where, never what (v1 §8.1). A family file writes an `add_requirements`
    line into every feedstock the family matches, so feedstocks that merely
    ask the same question share the reasoning and not the entry.
    """
    count = len(question.feedstocks)
    if count == 1:
        return f"Answer it in `config/feedstocks/{question.feedstocks[0]}.yaml`."
    if config_file is None:
        return (
            f"Answer it in `config/feedstocks/<name>.yaml`, once for each of the"
            f" {count}. A family file shares an answer only where it is true of"
            " every feedstock the family matches."
        )
    return (
        f"Answer it per feedstock in `config/feedstocks/<name>.yaml`, or once"
        f" for all {count} in `{config_file}`."
    )


def render_family(
    directory: Path, questions: Sequence[FamilyQuestion], scope: str = "this family"
) -> str:
    """What the terminal says after several feedstocks have been drafted: the
    counts and one path.
    """
    out = [f"  workbenches: {_short(directory)}"]
    if not questions:
        out.append(f"    nothing in {scope} is waiting on a decision")
        return "\n".join(out) + "\n"
    out.append("    SUMMARY.md     the questions they ask, and where to answer each")
    for index, question in enumerate(questions, start=1):
        count = len(question.feedstocks)
        out.append(
            f"  {index}. {question.title}"
            f"  ({count} feedstock{'' if count == 1 else 's'})"
        )
    return "\n".join(out) + "\n"
