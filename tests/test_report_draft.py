"""Tests for the workbench `swage draft` assembles (DESIGN.md 8.1).

The one that matters most asserts what the draft *does not* contain. `draft`
exists to make a config decision cheap, and the cheapest thing it could do is
guess -- which for an unexplained line means `add_requirements`, the answer
that is wrong for the whole temporary-constraint class.
"""

from __future__ import annotations

from pathlib import Path

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
    gate = GateResult("G6", False, "not approved for automatic merging (trust: manual)")

    findings = findings_markdown("demo", _plan(), _verdict(gate), UPSTREAM, {})

    assert "- not approved for automatic merging (trust: manual)" in findings
    assert "approved for automatic merging**" not in findings


def test_an_entirely_commented_extras_block_comments_its_own_key() -> None:
    """A key over a commented-out body is a key with no value.

    `extras_as_outputs:` left uncommented above nothing loads as null, says
    nothing, and stays in the file forever if the maintainer never returns to
    it. The whole block is commented so it is uncommented as a unit.
    """
    draft = config_draft("demo", read_recipe(RECIPE), UPSTREAM)

    assert "\n# extras_as_outputs:" in draft
    assert "\nextras_as_outputs:" not in draft
    assert "#     - pandas" in draft


def test_an_output_named_for_an_extra_is_drafted_as_supported() -> None:
    """The one thing derivable without judgement: what the recipe publishes."""
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

    assert "extras_as_outputs:\n  supported:\n    - pandas" in draft
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
    rendered = render_workbench(Workbench(Path("/tmp/drafts/demo"), ()), None)

    assert "/tmp/drafts/demo" in rendered
    assert "FINDINGS.md" in rendered
    assert "--apply" in rendered
