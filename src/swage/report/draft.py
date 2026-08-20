"""The workbench `swage draft` assembles (DESIGN.md 8.1).

Every gate that stops a feedstock hands the maintainer a question, and
answering it means having three things open at once: what upstream declares,
what the recipe says, and somewhere to write the answer down. Nothing here is
new work except the upstream file and the draft -- `scan` already renders both
recipes and every verdict already carries its remedy. What was missing was the
artifact.

**Quoting the metadata back is most of the value.** The remedy says what the
options are; what decides between them is what upstream says about the
disputed name, and a maintainer should not have to go and look. One real
finding from the first six feedstocks this was done by hand for::

    ### `setuptools` -- in /requirements/host, nowhere
    Every mention of `setuptools` in pyproject.toml:
        7: requires = ["setuptools"]

That one was not a decision at all. It was a swage defect, and it took seconds
to see with the file open beside the finding and considerably longer without.

**The draft must not pre-fill `add_requirements` for an unexplained line.**
That is the answer that is wrong for the whole temporary-constraint class, and
the class is not small: five of the first eight findings in the fleet were in
it. A skeleton offering it as the obvious next step is a machine nudging the
maintainer toward the harmful choice. `FINDINGS.md` presents the options;
`config.yaml` drafts only what swage can derive without judgment.

The same reasoning puts the `skip` candidates in as comments. `skip` is how a
maintainer records "considered and declined", and a file arriving with that
already written would record a decision nobody made -- which is the one thing
the exhaustiveness rule exists to detect.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from swage.plan import RecipePlan, Unexplained, Verdict
from swage.plan.gates import GateResult
from swage.plan.lines import parse_line
from swage.recipe import Recipe
from swage.upstream import UpstreamMetadata

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
    "write_workbench",
]

#: Where the workbench for one feedstock lives, under the cache root.
DRAFTS_DIR = "drafts"


@dataclass(frozen=True)
class Workbench:
    """What `draft` wrote, and where."""

    directory: Path
    files: tuple[Path, ...]


def write_workbench(
    directory: Path,
    feedstock: str,
    recipe: Recipe,
    rendered: str,
    plan: RecipePlan,
    verdict: Verdict,
    upstream: UpstreamMetadata,
    texts: Mapping[str, str],
) -> Workbench:
    """Assemble the workbench for one feedstock into ``directory``.

    Read-only against everything but itself. Nothing here touches the config
    tree -- `--execute` is a separate gesture and a separate function -- and
    nothing touches the feedstock at all.
    """
    directory.mkdir(parents=True, exist_ok=True)
    written = [
        _write(directory / "recipe.yaml", recipe.text),
        _write(directory / "recipe.swage.yaml", rendered),
        _write(directory / "recipe.diff", _diff(recipe.text, rendered)),
        _write(
            directory / "FINDINGS.md",
            findings_markdown(feedstock, plan, verdict, upstream, texts, recipe),
        ),
        _write(directory / "config.yaml", config_draft(feedstock, recipe, upstream)),
    ]
    for name, text in texts.items():
        written.append(_write(directory / "upstream" / name, text))
    return Workbench(directory, tuple(written))


def render_workbench(workbench: Workbench, applied: Path | None) -> str:
    """What the terminal says about a workbench that has just been written.

    Names the two files worth opening first rather than listing all six. The
    reader asked a question about one feedstock and is about to go and read
    prose; a manifest is not what they need back.
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
        out.append("  copy the config in with --execute once you have decided")
    return "\n".join(out) + "\n"


def _short(path: Path) -> str:
    """`~`-relative where it helps, absolute where it does not.

    Joined through `Path` rather than interpolated after a literal `~/`, so
    the separator is the one the reader's platform uses throughout instead of
    a forward slash followed by whatever `Path` prints.
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
    plan: RecipePlan,
    verdict: Verdict,
    upstream: UpstreamMetadata,
    texts: Mapping[str, str],
    recipe: Recipe | None = None,
) -> str:
    """Each thing holding this feedstock, with the evidence for deciding it."""
    version = upstream.version or "unknown version"
    out = [
        f"# {feedstock}",
        "",
        f"Upstream {upstream.name or feedstock} {version}.",
        "",
    ]

    if not verdict.failures:
        out += [
            "Nothing is holding this feedstock. swage can account for every",
            "requirement in the recipe, so there is no decision to write down.",
            "",
        ]
    else:
        out += ["## What is holding it", ""]
        out += [f"- {_holding(gate)}" for gate in verdict.failures]
        out += [""]

        out += _where_to_write(feedstock, verdict)

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
                out += _finding(section.path, item, texts, recipe)

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
#:
#: **Shape, never content.** The draft has always refused to choose an answer
#: (DESIGN.md 8.1) and that refusal stands: every stub below has a placeholder
#: where the decision goes. What it stops refusing is *syntax*, which is not a
#: decision -- a maintainer who has decided what `httpx[http2]` expands to
#: should not then have to find another feedstock's config to learn how to
#: write it down.
#:
#: A check with no entry is one no config key answers. G13 is the case: whether
#: a cross-compilation block repeats what changed is a judgment about the
#: recipe, and there is nowhere to record it.
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
        "dynamic_dependencies: trust"
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
}


def _where_to_write(feedstock: str, verdict: Verdict) -> list[str]:
    """The key each failure is answered with, and the shape of the answer.

    The gap this closes was demonstrated on `microsoft-kiota-http`: the remedy
    named `embedded_extras`, the workbench said nothing about how to write one,
    and the only worked example in the repository was in another family's file.
    Naming a key without its shape leaves a maintainer to go and find one.
    """
    answerable = [
        (gate, ANSWERED_WITH[gate.name])
        for gate in verdict.failures
        if gate.name in ANSWERED_WITH
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
    for gate, (keys, stub) in answerable:
        named = " or ".join(f"`{key}`" for key in keys)
        out += [f"### {gate.title}", "", f"Answered with {named}:", "", "```yaml"]
        out += stub.splitlines()
        out += ["```", ""]
    out += [
        "Every key is described in `docs/configuration.md`, with worked",
        "examples from the feedstocks already using it.",
        "",
    ]
    return out


def _holding(gate: GateResult) -> str:
    """One failure, said as what is wrong rather than as what was checked.

    A check's title states the property that ought to hold -- "this feedstock
    is approved for automatic merging" -- so listing titles under a heading
    that promises what is *holding* the feedstock prints the opposite of the
    truth. The first draft did exactly that, and read as though the feedstock
    were approved.
    """
    if gate.detail:
        return gate.detail
    return f"swage could not confirm that {gate.title}"


def _finding(
    path: str,
    item: Unexplained,
    texts: Mapping[str, str],
    recipe: Recipe | None = None,
) -> list[str]:
    name = parse_line(item.text).name
    out = [
        f"### `{item.text}`",
        "",
        f"In `{path}`, {item.kind}.",
        "",
        f"{item.reason}",
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
            # mentions is the whole case for dropping the line, and a blank
            # space where the evidence should be reads as "not checked".
            out.append("    (none)")
        out.append("")
    return out


def _in_the_recipe(name: str, recipe: Recipe | None) -> list[str]:
    """The recipe's own line for this name, with whatever comment sits above it.

    **The evidence for a hand-expanded extra is in the recipe, not upstream.**
    `microsoft-kiota-http` lists `h2 >=3,<5` under a `# httpx[http2] extra:`
    comment somebody wrote when they expanded the extra by hand -- which is the
    entire answer to why the line is there and what to record. This file quoted
    every upstream mention of `h2`, correctly reported `(none)` for all of
    them, and never showed the one line that explained it.
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
    """Every line quoting ``name``, matched the way a package name compares.

    `-` and `_` are the same character in a distribution name and case does
    not count, so a search for `ruamel-yaml` has to find `ruamel_yaml`. A
    maintainer reading a finding that says a name is mentioned nowhere, in a
    file that spells it with the other separator, is being told something
    false by a tool that had the file open.
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


def _unaccounted(upstream: UpstreamMetadata, plan: RecipePlan) -> tuple[str, ...]:
    """Upstream extras the plan could not tie to anything."""
    return plan.unaccounted_extras or tuple(upstream.extras)


def config_draft(feedstock: str, recipe: Recipe, upstream: UpstreamMetadata) -> str:
    """The config file this feedstock would need, minus every judgment call.

    What is derivable is which outputs the recipe already publishes, which
    upstream extras their names correspond to, and how those names are spelled.
    Everything else -- what a disputed name means, whether a bound is
    deliberate, whether an extra is worth publishing -- is what the maintainer
    is here to decide, and drafting a guess at it would be a machine putting
    words in their mouth.

    **Which of the two extras shapes a feedstock uses is not a decision
    either**, and drafting the wrong one made this file unloadable. A recipe
    that already publishes an output per extra takes `extras_as_outputs`; one
    that does not has nowhere to put such a list, and its decision belongs in
    `outputs[].run.skip` (DESIGN.md 4). The first draft wrote
    `extras_as_outputs.skip` for both, without the `suffix` that key requires,
    so `swage draft --apply` would copy in a file that stops `swage config`.
    """
    supported, candidates = _extras_by_output(recipe, upstream)
    out = [
        f"# Drafted by `swage draft {feedstock}`. Nothing here is a decision;",
        "# read FINDINGS.md, then say what each name means and delete this note.",
        "",
        f"feedstock: {feedstock}",
    ]
    if supported:
        # `suffix` is required, and it is read off the output names rather than
        # asked for: the recipe has already spelled it, in every output the
        # `supported` list beneath was derived from.
        suffix = _suffix(feedstock, recipe, supported[0])
        out += ["", "extras_as_outputs:", f'  suffix: "{suffix}"', "  supported:"]
        out += [f"    - {extra}" for extra in supported]
        if candidates:
            out += ["  # skip:"] + [f"  #   - {extra}" for extra in candidates]
    elif candidates:
        # The whole block is commented, its key included. Leaving that key
        # uncommented over an entirely commented body is a key with no value --
        # it loads as nothing, says nothing, and stays in the file forever if
        # the maintainer never comes back to it.
        out += ["", "# outputs:", f"#   {_folding_output(feedstock, recipe)}:"]
        # `core: true` decides nothing: an output with no config entry already
        # takes upstream's own dependencies, so this restates what the recipe
        # does today and leaves `skip` as the only new claim.
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
    """How this recipe names an output built from ``extra``, as a template.

    Derived from an output the recipe already has, so the drafted `suffix`
    describes the feedstock rather than imposing a convention on it:
    `apache-airflow-providers-amazon-with-google` against the extra `google`
    yields `{name}-with-{extra}`. Where the stem is not the package name --
    which no feedstock in the fleet does today -- it is written out literally,
    because a template that expands wrongly is worse than a name that cannot.
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
    """Which output an extra would be folded into, where that is unambiguous.

    One output leaves no choice, so it is named. Several make it a decision
    about which package the extra's dependencies belong in, and the placeholder
    says so rather than picking the first.
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
    held: Mapping[str, Sequence[GateResult]],
) -> tuple[FamilyQuestion, ...]:
    """Collapse a family's gate failures into the questions they represent.

    The point of drafting a family at once rather than one feedstock at a time
    (DESIGN.md 8.1). Across the fleet, 174 held feedstocks ask 8 kinds of
    question between them, and within one family it is usually one or two --
    so a maintainer facing 49 workbenches is really facing a decision they can
    take once. Presenting them as 49 separate archaeologies is what makes
    config coverage feel like 49 pieces of work.

    Two failures are the same question when they come from the same gate and
    their wording matches once names and versions are taken out. That is what
    collapses `would remove google-api-core >=2.17.1,<3.0.0` and the same line
    at `>=2.24.2` into one; the concrete wordings are kept and printed
    underneath, because whether a question is about one name or forty is
    exactly what decides where it gets answered.

    The trust ladder is not a question. It is what PROPOSED means, it is
    answered by a `trust` line rather than by any archaeology, and including
    it would put every unblessed feedstock in the family under a heading that
    reads as a decision needing evidence.
    """
    by_question: dict[tuple[str, str], dict[str, list[str]]] = {}
    titles: dict[tuple[str, str], str] = {}
    for feedstock, gates in held.items():
        for gate in gates:
            if gate.name == "G6":
                continue
            key = (gate.name, _shape(gate.detail))
            titles[key] = gate.title
            found = by_question.setdefault(key, {})
            found.setdefault(gate.detail or gate.title, []).append(feedstock)

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
    """A gate detail with the particulars taken out, for grouping.

    Names are fenced in every detail swage writes, so removing the fenced
    spans leaves the sentence -- which is the question -- and drops what it is
    being asked about.

    **Punctuation goes too, and that is not tidying.** A detail listing two
    names keeps the comma between them once the names are gone, so
    "upstream computed `requires-dist`" and "upstream computed
    `provides-extra`, `requires-dist`" came out as two questions when they are
    one gate asking one thing. The first real family draft split its 49
    feedstocks into 41 and 8 that way -- which is precisely the arithmetic
    this summary exists to stop a maintainer doing in their head.
    """
    without_names = re.sub(r"`[^`]*`", " ", detail)
    return re.sub(r"[^a-z]+", " ", without_names.lower()).strip()


#: How many feedstocks a question names before the rest are counted, and how
#: many wordings it quotes. The list is evidence for where an answer belongs,
#: not a manifest -- the directory beside this file is the manifest.
_NAMED = 8
_QUOTED = 3


def family_summary(
    family: str,
    config_file: str | None,
    questions: Sequence[FamilyQuestion],
    settled: Sequence[str],
    refused: Mapping[str, str],
) -> str:
    """What a set of workbenches say when read together.

    The file a maintainer opens first, and the reason drafting several
    feedstocks at once exists: it turns a directory of N archaeologies into the
    handful of decisions they actually represent, and says where each one can
    be written down once.

    ``config_file`` is the one file that could answer a shared question, and is
    None for feedstocks named on the command line rather than selected by a
    family -- they have no file in common, so what the summary can say is where
    an answer goes and what it would take to share one.
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
        # Where, never what. Which file an answer belongs in is a fact about
        # how config resolves; what to write in it is the decision, and a
        # machine proposing one is what DESIGN.md 8.1 refuses to do.
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

    Where, never what. Which file an answer belongs in is a fact about how
    config resolves; what to write in it is the decision, and a machine
    proposing one is what DESIGN.md 8.1 refuses to do.

    **A family file is not a way to share every answer**, which is why the
    sentence changes when there is no family. A `add_requirements` entry in a
    family file *writes that line into every feedstock the family matches*, so
    sharing one is only right where the line belongs in all of them. Feedstocks
    that merely ask the same question -- six of the maintainer's own share four
    package names between them -- share the reasoning and not the entry.
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
    """What the terminal says after several feedstocks have been drafted.

    The counts and one path. A family sweep writes several hundred files and
    listing them would bury the finding, which is how few questions they come
    to between them.
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
