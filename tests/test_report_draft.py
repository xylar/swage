"""Tests for the workbench `swage draft` assembles (DESIGN.md 8.1).

The one that matters most asserts what the draft *does not* contain. `draft`
exists to make a config decision cheap, and the cheapest thing it could do is
guess -- which for an unexplained line means `add_requirements`, the answer
that is wrong for the whole temporary-constraint class.
"""

from __future__ import annotations

from pathlib import Path
from types import UnionType
from typing import Any, get_args, get_origin

import yaml
from pydantic import BaseModel

from swage.config import Feedstock, Quirks
from swage.plan import (
    PlannedRequirement,
    PlannedSection,
    Provenance,
    RecipePlan,
    Unexplained,
)
from swage.plan.gates import GateResult, Verdict
from swage.recipe import read_recipe
from swage.report.draft import (
    ANSWERED_WITH,
    Workbench,
    config_draft,
    findings_markdown,
    render_workbench,
    write_workbench,
)
from swage.upstream import parse_pyproject

RECIPE = """context:
  python_min: '3.10'

package:
  name: demo
  version: '2.0'

build:
  noarch: python

requirements:
  run:
    - python >=${{ python_min }}
    - grpcio-gcp >=0.2.2
"""

PYPROJECT = """[build-system]
requires = ["setuptools"]

[project]
name = "demo"
version = "2.0"
dependencies = ["requests >=2"]

[project.optional-dependencies]
pandas = ["pandas >=1"]
tests = ["pytest"]
"""

UPSTREAM = parse_pyproject(PYPROJECT)


def _validated(text: str) -> Feedstock:
    """The drafted file as `swage config` would read it after `draft --execute`."""
    return Feedstock.model_validate(yaml.safe_load(text))


def _uncommented(draft: str, key: str) -> str:
    """The commented block a maintainer would uncomment, and nothing else.

    Uncommenting the whole file would take the prose note at the top with it.
    The block runs from its own key to the blank line after it, which is what
    makes it uncommentable as a unit in the first place.
    """
    lines = draft.splitlines()
    start = lines.index(f"# {key}:")
    end = lines.index("", start)
    return "\n".join(["feedstock: demo", *(line[2:] for line in lines[start:end])])


def _plan(*unexplained: Unexplained) -> RecipePlan:
    return RecipePlan(
        sections=(
            PlannedSection(
                path="/requirements/run",
                section="run",
                entries=(
                    PlannedRequirement(
                        "requests >=2", Provenance("upstream-core", "upstream")
                    ),
                ),
                unexplained=unexplained,
            ),
        )
    )


def _verdict(*gates: GateResult) -> Verdict:
    return Verdict(gates=gates)


#: A recipe whose maintainer expanded an extra by hand and said so, which is
#: the shape `microsoft-kiota-http` has and the case the recipe quote is for.
RECIPE_WITH_COMMENT = """\
context:
  version: '1.0.0'

package:
  name: demo
  version: ${{ version }}

requirements:
  run:
    - httpx >=0.25,<1.0.0
    # httpx[http2] extra:
    - h2 >=3,<5
"""


def test_the_metadata_is_quoted_beside_the_name_it_decides() -> None:
    """Most of the value, and the reason `draft` re-fetches the raw text.

    The remedy says what the options are. What decides between them is what
    upstream says about the disputed name, and a maintainer should not have to
    go and extract an sdist to find out.
    """
    plan = _plan(
        Unexplained("nowhere", "setuptools", "`setuptools` is in the recipe and...")
    )

    findings = findings_markdown(
        "demo", plan, _verdict(), UPSTREAM, {"pyproject.toml": PYPROJECT}
    )

    assert "Every mention of `setuptools` in pyproject.toml:" in findings
    assert '2: requires = ["setuptools"]' in findings


def test_a_name_upstream_never_mentions_says_so_rather_than_showing_nothing() -> None:
    """The blank is the answer -- it is the whole case for dropping the line.

    Empty space where the evidence should be reads as "not checked", which is
    a different claim from "checked, and upstream has never heard of it".
    """
    plan = _plan(Unexplained("nowhere", "grpcio-gcp >=0.2.2", "in no upstream version"))

    findings = findings_markdown(
        "demo", plan, _verdict(), UPSTREAM, {"pyproject.toml": PYPROJECT}
    )

    assert "Every mention of `grpcio-gcp` in pyproject.toml:" in findings
    assert "(none)" in findings


def test_a_mention_is_found_through_the_other_separator() -> None:
    """`-` and `_` are the same character in a distribution name.

    Telling a maintainer a name appears nowhere, in a file that spells it with
    the other separator, is a tool lying with the evidence open in front of it.
    """
    pyproject = '[project]\nname = "demo"\ndependencies = ["ruamel_yaml >=0.17"]\n'
    plan = _plan(Unexplained("nowhere", "ruamel-yaml >=0.17", "unexplained"))

    findings = findings_markdown(
        "demo", plan, _verdict(), parse_pyproject(pyproject), {"p.toml": pyproject}
    )

    assert "ruamel_yaml >=0.17" in findings
    assert "(none)" not in findings


def test_the_findings_never_offer_add_requirements_as_the_next_step() -> None:
    """The answer that is wrong for the whole temporary-constraint class.

    Five of the first eight findings in the fleet were in it. A skeleton
    naming `add_requirements` as the obvious thing to fill in is a machine
    nudging the maintainer toward the harmful choice, so the draft names none
    of the three answers and `FINDINGS.md` presents them as the remedy already
    words them.
    """
    draft = config_draft("demo", read_recipe(RECIPE), UPSTREAM)

    assert "add_requirements" not in draft


def test_what_is_holding_it_says_what_is_wrong_not_what_was_checked() -> None:
    """A check's title states the property that ought to hold.

    Listing titles under a heading that promises what is *holding* the
    feedstock prints the opposite of the truth: the first version of this file
    said `- **this feedstock is approved for automatic merging**` about a
    feedstock that is not.
    """
    gate = GateResult("G6", False, "not approved for automatic merging (trust: never)")

    findings = findings_markdown("demo", _plan(), _verdict(gate), UPSTREAM, {})

    assert "- not approved for automatic merging (trust: never)" in findings
    assert "approved for automatic merging**" not in findings


def _shape(annotation: Any) -> str:
    """Whether a config field holds a mapping, a sequence, or a scalar."""
    if get_origin(annotation) is UnionType:
        annotation = next(arg for arg in get_args(annotation) if arg is not type(None))
    origin = get_origin(annotation) or annotation
    if origin is dict or (isinstance(origin, type) and issubclass(origin, BaseModel)):
        return "mapping"
    if origin in {list, tuple, set}:
        return "sequence"
    return "scalar"


def test_every_stub_is_written_the_shape_its_key_holds() -> None:
    """The stubs are guidance swage prints, so they have to be loadable.

    `run_constraints` is a mapping of package name to what its entry tracks,
    and the stub offered a list of `requirement:`/`extra:` pairs -- which the
    loader rejects with `Input should be a valid dictionary`. A maintainer
    following it verbatim gets a config file that stops `swage config`, having
    done exactly what they were told.

    Shape rather than content, because the stubs are placeholders by design
    (DESIGN.md 8.1) and `<the upstream extra it tracks>` is not a real extra.
    """
    for gate, (_keys, stub) in ANSWERED_WITH.items():
        drafted = yaml.safe_load(stub)
        for key, value in drafted.items():
            assert key in Quirks.model_fields, f"{gate} names {key}, which is not a key"
            expected = _shape(Quirks.model_fields[key].annotation)
            assert _shape(type(value)) == expected, (
                f"{gate} drafts {key} as a {_shape(type(value))}, "
                f"and the schema holds a {expected}"
            )


def test_an_entirely_commented_extras_block_comments_its_own_key() -> None:
    """A key over a commented-out body is a key with no value.

    `outputs:` left uncommented above nothing loads as null, says nothing, and
    stays in the file forever if the maintainer never returns to it. The whole
    block is commented so it is uncommented as a unit.
    """
    draft = config_draft("demo", read_recipe(RECIPE), UPSTREAM)

    assert "\n# outputs:" in draft
    assert "\noutputs:" not in draft
    assert "#         - pandas" in draft


def test_a_feedstock_publishing_no_extras_as_outputs_is_drafted_the_other_shape() -> (
    None
):
    """`extras_as_outputs.skip` is the wrong key for a recipe with no such output.

    It also could not load: `suffix` is required and the draft had none to
    give, having nothing to read one off. Both faults reached `pyjwt`'s
    workbench, where the decision belongs in `outputs[].run.skip`.
    """
    draft = config_draft("demo", read_recipe(RECIPE), UPSTREAM)

    assert "extras_as_outputs" not in draft
    # The output they would be folded into is the recipe's only one.
    uncommented = _validated(_uncommented(draft, "outputs"))
    assert uncommented.outputs["demo"].run.skip == ("pandas", "tests")
    assert uncommented.outputs["demo"].run.core is True


def test_a_drafted_extras_as_outputs_block_carries_the_suffix_it_needs() -> None:
    """`suffix` is required, and the recipe has already spelled it out.

    Without it the drafted file failed validation on the one key a maintainer
    could not have supplied, never having been told it was wanted.
    """
    recipe = read_recipe(
        """package:
  name: demo
  version: '2.0'

outputs:
  - package:
      name: demo-with-pandas
    requirements:
      run:
        - pandas >=1
"""
    )

    draft = config_draft("demo", recipe, UPSTREAM)

    assert 'suffix: "{name}-with-{extra}"' in draft
    assert _validated(draft).extras_as_outputs is not None


def test_an_output_named_for_an_extra_is_drafted_as_supported() -> None:
    """The one thing derivable without judgment: what the recipe publishes."""
    recipe = read_recipe(
        """package:
  name: demo
  version: '2.0'

outputs:
  - package:
      name: demo-pandas
    requirements:
      run:
        - pandas >=1
"""
    )

    draft = config_draft("demo", recipe, UPSTREAM)

    assert (
        'extras_as_outputs:\n  suffix: "{name}-{extra}"\n  supported:\n    - pandas'
    ) in draft
    # Still a candidate rather than a decision: no output is named for it.
    assert "  # skip:\n  #   - tests" in draft


def test_the_workbench_holds_both_recipes_the_diff_and_the_metadata(
    tmp_path: Path,
) -> None:
    recipe = read_recipe(RECIPE)
    rendered = RECIPE.replace("grpcio-gcp >=0.2.2", "requests >=2")

    workbench = write_workbench(
        tmp_path / "demo",
        "demo",
        recipe,
        rendered,
        _plan(),
        _verdict(),
        UPSTREAM,
        {"pyproject.toml": PYPROJECT},
    )

    written = {path.name for path in workbench.files}
    assert written == {
        "recipe.yaml",
        "recipe.swage.yaml",
        "recipe.diff",
        "FINDINGS.md",
        "config.yaml",
        "pyproject.toml",
    }
    assert (
        workbench.directory / "upstream" / "pyproject.toml"
    ).read_text() == PYPROJECT
    assert (
        "-    - grpcio-gcp >=0.2.2" in (workbench.directory / "recipe.diff").read_text()
    )


def test_nothing_holding_it_is_said_rather_than_left_blank() -> None:
    """`draft` is asked about feedstocks that turn out to be fine, too."""
    findings = findings_markdown("demo", _plan(), _verdict(), UPSTREAM, {})

    assert "Nothing is holding this feedstock" in findings


def test_the_terminal_says_where_the_workbench_is_and_what_to_open() -> None:
    # Spelled through `Path` rather than as a literal: a path is written with
    # the separator of the platform printing it, and asserting the POSIX
    # spelling failed on Windows against output that was perfectly correct.
    directory = Path("/tmp/drafts/demo")

    rendered = render_workbench(Workbench(directory, ()), None)

    assert str(directory) in rendered
    assert "FINDINGS.md" in rendered
    assert "--execute" in rendered


def test_a_finding_says_which_key_answers_it_and_what_it_looks_like() -> None:
    """Naming a key without its shape sends a maintainer hunting for an example.

    `microsoft-kiota-http` named `embedded_extras`, said nothing about how to
    write one, and the only worked example in the repository was in another
    family's config file.
    """
    verdict = Verdict(
        gates=(
            GateResult(name="G2", passed=False, detail="`httpx[http2]` resolved to..."),
        )
    )
    text = findings_markdown("demo", RecipePlan(), verdict, UPSTREAM, {})
    assert "## Where to write it down" in text
    assert "`name_map` or `embedded_extras`" in text
    assert "config/feedstocks/demo.yaml" in text
    assert "docs/configuration.md" in text


def test_the_stub_leaves_the_decision_blank() -> None:
    """Shape is not a decision; the draft still refuses to make one."""
    verdict = Verdict(
        gates=(GateResult(name="G1", passed=False, detail="`h2` is in the recipe"),)
    )
    text = findings_markdown("demo", RecipePlan(), verdict, UPSTREAM, {})
    assert "add_requirements:" in text
    assert "<the requirement, exactly as the recipe spells it>" in text


def test_a_check_no_config_key_answers_gets_no_stub() -> None:
    """G13 is a judgment about the recipe with nowhere to record it."""
    verdict = Verdict(
        gates=(GateResult(name="G13", passed=False, detail="cross-compiled"),)
    )
    text = findings_markdown("demo", RecipePlan(), verdict, UPSTREAM, {})
    assert "## Where to write it down" not in text


def test_the_recipe_line_and_its_comment_are_quoted() -> None:
    """For a hand-expanded extra the recipe *is* the evidence.

    `microsoft-kiota-http` lists `h2 >=3,<5` under a `# httpx[http2] extra:`
    comment somebody wrote when they expanded the extra by hand. That comment
    is the whole answer, and the workbench used to quote every upstream
    mention of `h2` -- correctly reporting `(none)` for each -- while never
    showing the one line that explained it.
    """
    recipe = read_recipe(RECIPE_WITH_COMMENT)
    plan = RecipePlan(
        sections=(
            PlannedSection(
                path="/requirements/run",
                section="run",
                unexplained=(Unexplained("nowhere", "h2 >=3,<5", "nowhere"),),
            ),
        )
    )
    verdict = Verdict(
        gates=(GateResult(name="G1", passed=False, detail="`h2` is in the recipe"),)
    )
    text = findings_markdown("demo", plan, verdict, UPSTREAM, {}, recipe)
    assert "What the recipe says, with any comment above it:" in text
    assert "# httpx[http2] extra:" in text
    assert "- h2 >=3,<5" in text
