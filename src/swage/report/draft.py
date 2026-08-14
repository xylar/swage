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
`config.yaml` drafts only what swage can derive without judgement.

The same reasoning puts the `skip` candidates in as comments. `skip` is how a
maintainer records "considered and declined", and a file arriving with that
already written would record a decision nobody made -- which is the one thing
the exhaustiveness rule exists to detect.
"""

from __future__ import annotations

import difflib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from swage.plan import RecipePlan, Unexplained, Verdict
from swage.plan.gates import GateResult
from swage.plan.lines import parse_line
from swage.recipe import Recipe
from swage.upstream import UpstreamMetadata

__all__ = [
    "Workbench",
    "config_draft",
    "findings_markdown",
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
    tree -- `--apply` is a separate gesture and a separate function -- and
    nothing touches the feedstock at all.
    """
    directory.mkdir(parents=True, exist_ok=True)
    written = [
        _write(directory / "recipe.yaml", recipe.text),
        _write(directory / "recipe.swage.yaml", rendered),
        _write(directory / "recipe.diff", _diff(recipe.text, rendered)),
        _write(
            directory / "FINDINGS.md",
            findings_markdown(feedstock, plan, verdict, upstream, texts),
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
        out.append("  copy the config in with --apply once you have decided")
    return "\n".join(out) + "\n"


def _short(path: Path) -> str:
    """`~`-relative where it helps, absolute where it does not."""
    try:
        return f"~/{path.relative_to(Path.home())}"
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
                out += _finding(section.path, item, texts)

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


def _finding(path: str, item: Unexplained, texts: Mapping[str, str]) -> list[str]:
    name = parse_line(item.text).name
    out = [
        f"### `{item.text}`",
        "",
        f"In `{path}`, {item.kind}.",
        "",
        f"{item.reason}",
        "",
    ]
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
    """The config file this feedstock would need, minus every judgement call.

    What is derivable is which outputs the recipe already publishes and which
    upstream extras their names correspond to. Everything else -- what a
    disputed name means, whether a bound is deliberate, whether an extra is
    worth publishing -- is what the maintainer is here to decide, and drafting
    a guess at it would be a machine putting words in their mouth.
    """
    supported, candidates = _extras_by_output(recipe, upstream)
    out = [
        f"# Drafted by `swage draft {feedstock}`. Nothing here is a decision;",
        "# read FINDINGS.md, then say what each name means and delete this note.",
        "",
        f"feedstock: {feedstock}",
    ]
    if supported:
        out += ["", "extras_as_outputs:", "  supported:"]
        out += [f"    - {extra}" for extra in supported]
        if candidates:
            out += ["  # skip:"] + [f"  #   - {extra}" for extra in candidates]
    elif candidates:
        # The whole block is commented, `extras_as_outputs:` included. Leaving
        # that key uncommented over an entirely commented body is a key with
        # no value -- it loads as nothing, says nothing, and stays in the file
        # forever if the maintainer never comes back to it.
        out += ["", "# extras_as_outputs:", "#   skip:"]
        out += [f"#     - {extra}" for extra in candidates]
    if candidates:
        out += [
            "",
            "# Upstream declares the extras above and no output is named for",
            "# them. Listing one under `skip` records a decision not to publish",
            "# it; leaving it out means nobody has looked yet.",
        ]
    return "\n".join(out) + "\n"


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
