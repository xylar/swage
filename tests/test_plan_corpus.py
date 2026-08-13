"""Golden tests: plan a corpus recipe and compare against the real one.

The strongest test available, and the one DESIGN.md 11 puts first. Each corpus
entry is a real project's upstream metadata beside the `recipe.yaml` the tool
swage replaces actually published. Planning the first and comparing against the
second exercises every layer at once -- marker reconciliation, name resolution,
attribution, embedded extras, ordering and rendering -- and compares the result
against output that shipped rather than against expectations swage wrote for
itself.

It is also the property gate G7 rests on. A section swage would render
differently is a section it would rewrite, so "no changes needed" is only ever
true where this test would pass.

**Two families, and they do not agree with each other.** The airflow providers
come from a `pyproject.toml` in a monorepo tag; the google-cloud feedstocks
come from a sdist's `PKG-INFO`, and 10 of the 11 ship no `pyproject.toml` at
all, which makes them the corpus's only coverage of the core-metadata path and
of `default_build_requires` (DESIGN.md 3.6.2, 3.6.4). The two bespoke tools
that produced these recipes also disagreed about formatting, so where a
convention had to be chosen `CONVENTIONS` records it once rather than
exempting every file it touches.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from pathlib import Path

import pytest

from swage.config import ConfigTree, load_config
from swage.mapping import NameResolver, StaticPackageIndex
from swage.plan import PythonMin, RecipePlan, plan_recipe, planned_blocks
from swage.recipe import Recipe, read_recipe, render_recipe
from swage.upstream import UpstreamMetadata, parse_metadata, parse_pyproject

from .conftest import CONFIG_ROOT, REPO_ROOT

CORPUS = REPO_ROOT / "tests" / "corpus"

#: Every recipe in the corpus is built against this floor. The providers are
#: rendered with it, and `.ci_support/linux_64_.yaml` says 3.10 for the
#: google-cloud feedstocks too.
PYTHON_MIN = PythonMin("3.10", ".ci_support/linux_64_.yaml")


@dataclass(frozen=True)
class Case:
    """One corpus entry: upstream metadata beside its published recipe."""

    name: str
    directory: Path

    @property
    def recipe_text(self) -> str:
        return (self.directory / "recipe.yaml").read_text(encoding="utf-8")

    def upstream(self) -> UpstreamMetadata:
        """Read the metadata the way `forge.archive` would.

        `pyproject.toml` wins where it is there, because only it carries
        `[build-system] requires`; `PKG-INFO` is what the other ten
        google-cloud sdists state their dependencies in (DESIGN.md 3.6.2).
        """
        pyproject = self.directory / "pyproject.toml"
        if pyproject.is_file():
            return parse_pyproject(
                pyproject.read_text(encoding="utf-8"), str(pyproject)
            )
        pkg_info = self.directory / "PKG-INFO"
        return parse_metadata(pkg_info.read_text(encoding="utf-8"), str(pkg_info))


def _cases() -> list[Case]:
    return [
        Case(directory.name, directory)
        for family in ("airflow-providers", "google-cloud")
        for directory in sorted((CORPUS / family).iterdir())
        if (directory / "recipe.yaml").is_file()
    ]


CASES = _cases()


def _feedstock(case: Case, upstream: UpstreamMetadata) -> str:
    """The feedstock name config is keyed on.

    The provider triples are named `providers-<slug>_<version>`, so the
    feedstock comes from the metadata; the google-cloud directories are named
    for the feedstock itself.
    """
    if case.directory.parent.name == "google-cloud":
        return case.name
    return upstream.name


def _package_index(
    recipe: Recipe, tree: ConfigTree, feedstock: str
) -> StaticPackageIndex:
    """Stand in for conda-forge's package list, built from the published recipe.

    The names conda-forge actually publishes for these projects are the ones
    the published recipe depends on, so that is what the index holds -- plus
    everything config maps to or supplies. A missing package is G2's business
    rather than this test's, and every name that ought to render is in the
    recipe by construction, since the recipe is the expected output.

    **Seeding this from upstream's spellings instead invents failures.** The
    `google-cloud-bigquery` geopandas extra declares `Shapely`; conda-forge
    publishes `shapely` and the recipe says so. An index built from upstream
    contains `Shapely`, identity-resolves to it, and swage renders a second
    line beside the real one -- a harness bug that looks exactly like a planner
    bug. Resolving against what the channel really has is also what exercises
    DESIGN.md 3.6.1's rule that both spellings are indexed and the exact one is
    tried first.
    """
    config = tree.for_feedstock(feedstock)
    names: set[str] = set()
    for block in recipe.blocks.values():
        for text in block.content.texts():
            head = text.split()[0]
            # `${{ pin_subpackage(...) }}` and friends are recipe-owned and
            # never reach the resolver (DESIGN.md 3.3.6).
            if not head.startswith("${{"):
                names.add(head)
    for mapped in config.name_map.layers:
        names |= set(mapped.entries.values())
    for expansions in config.embedded_extras.layers:
        for lines in expansions.entries.values():
            names |= {line.split()[0] for line in lines}
    return StaticPackageIndex(frozenset(names))


def _plan(case: Case) -> tuple[Recipe, RecipePlan]:
    upstream = case.upstream()
    recipe = read_recipe(case.recipe_text)
    tree = load_config(CONFIG_ROOT)
    feedstock = _feedstock(case, upstream)
    config = tree.for_feedstock(feedstock)
    resolver = NameResolver(config.name_map, _package_index(recipe, tree, feedstock))
    return recipe, plan_recipe(recipe, upstream, config, resolver, PYTHON_MIN)


#: Formatting swage renders differently from *both* tools, on purpose, applied
#: to the published text before comparing. A convention that diverges from the
#: prior art everywhere belongs here rather than in `KNOWN_DIFFERENCES`:
#: listing every file it touches would exempt most of the corpus from
#: byte-comparison in order to record one decision.
#:
#: The marker comment of DESIGN.md 3.3.1 is the only entry. The airflow tool
#: wrote `# more restrictive for python >=3.14` and the google-cloud tool
#: `# more restrictive constraint for python >=3.14`; swage writes neither,
#: because both read as though the constraint applied only from 3.14 up when
#: in fact it binds on every python (`plan.reconcile._note`).
CONVENTIONS = (
    (
        re.compile(r"# more restrictive(?: constraint)? for (python [^\n]*)"),
        r"# tightest of upstream's floors (\1)",
    ),
)


def _as_swage_writes_it(text: str) -> str:
    for pattern, replacement in CONVENTIONS:
        text = pattern.sub(replacement, text)
    return text


def test_the_conventions_actually_fire() -> None:
    """Guard against a rewrite that has quietly become a no-op.

    If the corpus stopped carrying the prior art's wording, this rewrite would
    pass every recipe through untouched and the byte comparison below would
    look like it had verified a convention it never saw.
    """
    matched = [
        case.name
        for case in CASES
        if _as_swage_writes_it(case.recipe_text) != case.recipe_text
    ]
    assert len(matched) >= 12, matched


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
def test_planning_reproduces_the_published_recipe(case: Case) -> None:
    """Every host and run section, line for line and in order.

    Weaker than the byte comparison below and kept for the failure it prints:
    a list of dependency lines side by side says what moved, where a diff of
    two whole recipes says only that they differ. Where `KNOWN_DIFFERENCES`
    names a subject, its lines come out of both sides -- everything else in
    the section still has to match exactly, so an allowance made for one
    dependency cannot hide a second.
    """
    recipe, plan = _plan(case)
    allowance = KNOWN_DIFFERENCES.get(case.name)
    subject = allowance[1] if allowance is not None else None

    def comparable(texts: list[str]) -> list[str]:
        if subject is None:
            return texts
        return [text for text in texts if text.split()[0] != subject]

    assert plan.sections, "planned nothing at all"
    for section in plan.sections:
        expected = list(recipe.blocks[section.path].content.texts())
        assert comparable([r.text for r in section.requirements]) == comparable(
            expected
        ), section.path


def test_the_corpus_covers_both_families() -> None:
    """Guard against the parametrized tests silently covering nothing."""
    assert len(CASES) >= 19
    assert sum(case.directory.parent.name == "google-cloud" for case in CASES) >= 11


#: Corpus recipes swage does not reproduce byte for byte, and why. Each is a
#: statement about `config/` or about DESIGN.md, never about the renderer --
#: which is the only reason an entry here is acceptable rather than a bug.
#: Anything not listed must round-trip exactly, because a section swage would
#: render differently is a section it would rewrite (G7, DESIGN.md 5.3).
#:
#: The value is a marker that must appear in the rendered or published text,
#: plus the one dependency whose line is allowed to differ -- where that is
#: None, only comments may.
KNOWN_DIFFERENCES: dict[str, tuple[str, str | None]] = {
    # DESIGN.md 6 writes the marker's extra PEP 685-normalized, so swage says
    # `pyhive[hive-pure-sasl]` where this recipe says `pyhive[hive_pure_sasl]`.
    # Deliberate: the spelling must not depend on which metadata file was read.
    "providers-apache-hive_9.6.1": ("# start pyhive[hive-pure-sasl]", None),
    # No `embedded_extras` entry for celery yet, so swage does not know the
    # lines inside its markers are an expansion; it fails G1 on exactly those
    # lines, so nothing merges while it is unwritten.
    "providers-celery_3.23.1": ("# start celery[redis]", None),
    # `psycopg[binary]` expands to nothing on purpose, so swage writes the
    # caption of DESIGN.md 6 where this recipe has an empty `# start`/`# end`
    # pair. Comments only.
    "providers-postgres_7.0.0": ("# psycopg[binary] needs nothing extra", None),
    # The corpus's one multi-clause constraint, and the one place the two
    # tools' output cannot both be reproduced. The airflow tool passed a
    # constraint through as upstream wrote it; the google-cloud tool
    # canonicalized bounds before exclusions. swage canonicalizes (DESIGN.md
    # 6), so this one line reformats and every google-cloud recipe stops
    # reformatting.
    "providers-cncf-kubernetes_10.21.0": (
        "python-kubernetes >=35.0.0,<37.0.0,!=36.0.0",
        "python-kubernetes",
    ),
    # Two things, both deliberate. The same PEP 685 normalization as
    # apache-hive above, on an extra header: this sdist's pyproject.toml says
    # `bigquery_v2` where its PKG-INFO says `bigquery-v2`. And DESIGN.md 6
    # rule 2 puts `python` first in a section, ahead of the `pin_subpackage`
    # line this recipe leads with.
    #
    # It also carries the corpus's only hand-written note *inside* a
    # requirements block, which swage renders and therefore does not preserve.
    # `exclude` records a deliberate omission (DESIGN.md 3.3.13); nothing yet
    # records a remark about a line that is present.
    "google-cloud-bigquery": ("# from the bigquery-v2 extra", "python"),
    # The grayskull workaround, and swage retiring it (DESIGN.md 3.2).
    # grayskull drops the extra from `google-api-core[grpc]<3.0.0,>=2.25.0`, so
    # this recipe carries the requirement as two lines: a constrained
    # `google-api-core`, which is what grayskull would regenerate anyway,
    # beside a deliberately bare `google-api-core-grpc`, so that the two tools
    # would not overwrite each other. swage resolves the requirement properly
    # and constrains the second line; the first then appears in no upstream
    # version, comes out `kept, unexplained`, and G1 stops the feedstock naming
    # it -- so a human deletes it once and the workaround retires.
    #
    # `google-cloud-storage` below has the identical shape legitimately: it
    # declares plain `google-api-core` among its core dependencies *and*
    # `google-api-core[grpc]` under its grpc extra. That is why the two are
    # told apart by attribution and never by recognizing the pattern.
    "google-cloud-logging": (
        "google-api-core-grpc >=2.25.0,<3.0.0",
        "google-api-core-grpc",
    ),
    # DESIGN.md 6 rule 2 again: this recipe's host section is alphabetized, so
    # `pip` precedes `python`. The fleet agrees with the rule 159 sections to 2.
    "google-cloud-storage": ("python ${{ python_min }}.*", "python"),
}


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
def test_rendering_reproduces_the_published_recipe_byte_for_byte(case: Case) -> None:
    """The stronger claim, and the one G7 actually rests on.

    Comparing dependency *lines* leaves swage's own comments unverified --
    the `# from the X extra` headers and the `# start`/`# end` marker pairs of
    DESIGN.md 6, which swage regenerates rather than preserves. Both were
    wrong when this test was written and neither showed up above: swage
    annotated every line of an `extras_as_outputs` output with the extra its
    own name already carries, and dropped the marker pairs entirely, so the
    first thing it would have done to four of these recipes is delete the
    round-trip markers that make a rerun idempotent.
    """
    recipe, plan = _plan(case)
    rendered = render_recipe(recipe, planned_blocks(plan))
    expected = _as_swage_writes_it(case.recipe_text)

    allowance = KNOWN_DIFFERENCES.get(case.name)
    if allowance is None:
        assert rendered == expected
        return
    marker, subject = allowance
    # A listed difference still has to be the one that was signed off on, and
    # still has to be confined to what the allowance describes -- a dependency
    # that changed here would be hiding behind an allowance made for a comment.
    assert rendered != expected, f"{case.name} now matches; drop its allowance"
    assert marker in rendered or marker in expected
    changed = [
        line
        for line in difflib.unified_diff(
            expected.splitlines(), rendered.splitlines(), n=0
        )
        if line[:1] in "+-" and not line.startswith(("---", "+++"))
    ]
    assert all(
        "#" in line or (subject is not None and subject in line) for line in changed
    ), changed


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
def test_planning_never_authors_a_run_constraints_entry(case: Case) -> None:
    """The hardest guard in DESIGN.md 11's list.

    "upstream declares an extra, so emit a constraint" is exactly the
    plausible-looking behaviour DESIGN.md 3.3.9 exists to prevent, so it is
    asserted against every real recipe rather than argued about in a
    docstring.
    """
    _, plan = _plan(case)

    assert {section.section for section in plan.sections} <= {"host", "run"}


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
def test_planning_is_idempotent(case: Case) -> None:
    """`plan(apply(plan(r))) == no-op` (DESIGN.md 6).

    Since planning already reproduces the published recipe, planning it a
    second time has to produce the same thing again -- which is what makes the
    tool safe to run on a schedule.
    """
    _, first = _plan(case)
    _, second = _plan(case)
    assert [[r.text for r in section.requirements] for section in first.sections] == [
        [r.text for r in section.requirements] for section in second.sections
    ]
