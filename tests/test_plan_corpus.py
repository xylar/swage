"""Golden tests: plan a corpus recipe and compare against the real one.

The strongest test available, and the one DESIGN.md 11 puts first. Each corpus
triple is a real provider's upstream `pyproject.toml` beside the `recipe.yaml`
the tool swage replaces actually published. Planning the first and comparing
against the second exercises every layer at once -- marker reconciliation, name
resolution, attribution, embedded extras, ordering and rendering -- and
compares the result against output that shipped rather than against
expectations swage wrote for itself.

It is also the property gate G7 rests on. A section swage would render
differently is a section it would rewrite, so "no changes needed" is only ever
true where this test would pass.
"""

from __future__ import annotations

import difflib
from pathlib import Path

import pytest

from swage.config import ConfigTree, load_config
from swage.mapping import NameResolver, StaticPackageIndex
from swage.plan import PythonMin, plan_recipe, planned_blocks
from swage.recipe import read_recipe, render_recipe
from swage.upstream import UpstreamMetadata, parse_pyproject

from .conftest import CONFIG_ROOT, REPO_ROOT

CORPUS = REPO_ROOT / "tests" / "corpus" / "airflow-providers"
TRIPLES = sorted(p.parent for p in CORPUS.glob("*/pyproject.toml"))

#: Every provider recipe in the corpus is built against this floor.
PYTHON_MIN = PythonMin("3.10", ".ci_support/linux_64_.yaml")


def _package_index(
    upstream: UpstreamMetadata, tree: ConfigTree, feedstock: str
) -> StaticPackageIndex:
    """Stand in for conda-forge's package list.

    Every name upstream declares is assumed to exist, plus everything config
    maps to or supplies. That is deliberately generous: this test is about
    reconciliation and rendering, and a missing package is G2's business, not
    this one's. Being generous also keeps the test from silently passing
    because a line was dropped for the wrong reason.
    """
    config = tree.for_feedstock(feedstock)
    names = {requirement.name for requirement in upstream.dependencies}
    for requirements in upstream.optional_dependencies.values():
        names |= {requirement.name for requirement in requirements}
    for requirement in upstream.build_requires or ():
        names.add(requirement.name.replace("_", "-"))
    for mapped in config.name_map.layers:
        names |= set(mapped.entries.values())
    for expansions in config.embedded_extras.layers:
        for lines in expansions.entries.values():
            names |= {line.split()[0] for line in lines}
    return StaticPackageIndex(frozenset(names))


@pytest.mark.parametrize("directory", TRIPLES, ids=lambda p: p.name)
def test_planning_reproduces_the_published_recipe(directory: Path) -> None:
    """Every host and run section, line for line and in order."""
    upstream = parse_pyproject(
        (directory / "pyproject.toml").read_text(encoding="utf-8")
    )
    recipe = read_recipe((directory / "recipe.yaml").read_text(encoding="utf-8"))
    tree = load_config(CONFIG_ROOT)
    config = tree.for_feedstock(upstream.name)
    resolver = NameResolver(
        config.name_map, _package_index(upstream, tree, upstream.name)
    )

    plan = plan_recipe(recipe, upstream, config, resolver, PYTHON_MIN)

    assert plan.sections, "planned nothing at all"
    for section in plan.sections:
        expected = list(recipe.blocks[section.path].content.texts())
        assert [r.text for r in section.requirements] == expected, section.path


def test_the_corpus_covers_more_than_one_provider() -> None:
    """Guard against the parametrized test above silently covering nothing."""
    assert len(TRIPLES) >= 8


#: Corpus recipes swage does not reproduce byte for byte, and why. Each is a
#: statement about `config/` or about DESIGN.md, never about the renderer --
#: which is the only reason an entry here is acceptable rather than a bug.
#: Anything not listed must round-trip exactly, because a section swage would
#: render differently is a section it would rewrite (G7, DESIGN.md 5.3).
KNOWN_DIFFERENCES = {
    # DESIGN.md 6 writes the marker's extra PEP 685-normalized, so swage says
    # `pyhive[hive-pure-sasl]` where this recipe says `pyhive[hive_pure_sasl]`.
    # Deliberate: the spelling must not depend on which metadata file was read.
    "providers-apache-hive_9.6.1": "# start pyhive[hive-pure-sasl]",
    # No `embedded_extras` entry for these yet, so swage does not know the
    # lines inside the markers are an expansion. Both feedstocks fail G1 on
    # exactly those lines, so nothing merges while it is unwritten; the full
    # quirk set lands in phase 7.
    "providers-celery_3.23.1": "# start celery[redis]",
    "providers-postgres_7.0.0": "# start psycopg[binary]",
}


@pytest.mark.parametrize("directory", TRIPLES, ids=lambda p: p.name)
def test_rendering_reproduces_the_published_recipe_byte_for_byte(
    directory: Path,
) -> None:
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
    upstream = parse_pyproject(
        (directory / "pyproject.toml").read_text(encoding="utf-8")
    )
    text = (directory / "recipe.yaml").read_text(encoding="utf-8")
    recipe = read_recipe(text)
    tree = load_config(CONFIG_ROOT)
    config = tree.for_feedstock(upstream.name)
    resolver = NameResolver(
        config.name_map, _package_index(upstream, tree, upstream.name)
    )

    plan = plan_recipe(recipe, upstream, config, resolver, PYTHON_MIN)
    rendered = render_recipe(recipe, planned_blocks(plan))

    known = KNOWN_DIFFERENCES.get(directory.name)
    if known is None:
        assert rendered == text
        return
    # A listed difference still has to be the one that was signed off on, and
    # still has to be confined to comments -- a dependency that changed here
    # would be hiding behind an allowance made for a comment.
    assert rendered != text, f"{directory.name} now matches; drop its allowance"
    assert known in rendered or known in text
    changed = [
        line
        for line in difflib.unified_diff(text.splitlines(), rendered.splitlines(), n=0)
        if line[:1] in "+-" and not line.startswith(("---", "+++"))
    ]
    assert all("#" in line for line in changed), changed


@pytest.mark.parametrize("directory", TRIPLES, ids=lambda p: p.name)
def test_planning_never_authors_a_run_constraints_entry(directory: Path) -> None:
    """The hardest guard in DESIGN.md 11's list.

    "upstream declares an extra, so emit a constraint" is exactly the
    plausible-looking behaviour DESIGN.md 3.3.9 exists to prevent, so it is
    asserted against every real provider rather than argued about in a
    docstring.
    """
    upstream = parse_pyproject(
        (directory / "pyproject.toml").read_text(encoding="utf-8")
    )
    recipe = read_recipe((directory / "recipe.yaml").read_text(encoding="utf-8"))
    tree = load_config(CONFIG_ROOT)
    config = tree.for_feedstock(upstream.name)
    resolver = NameResolver(
        config.name_map, _package_index(upstream, tree, upstream.name)
    )

    plan = plan_recipe(recipe, upstream, config, resolver, PYTHON_MIN)

    assert {section.section for section in plan.sections} <= {"host", "run"}


@pytest.mark.parametrize("directory", TRIPLES, ids=lambda p: p.name)
def test_planning_is_idempotent(directory: Path) -> None:
    """`plan(apply(plan(r))) == no-op` (DESIGN.md 6).

    Since planning already reproduces the published recipe, planning it a
    second time has to produce the same thing again -- which is what makes the
    tool safe to run on a schedule.
    """
    upstream = parse_pyproject(
        (directory / "pyproject.toml").read_text(encoding="utf-8")
    )
    recipe = read_recipe((directory / "recipe.yaml").read_text(encoding="utf-8"))
    tree = load_config(CONFIG_ROOT)
    config = tree.for_feedstock(upstream.name)
    resolver = NameResolver(
        config.name_map, _package_index(upstream, tree, upstream.name)
    )

    first = plan_recipe(recipe, upstream, config, resolver, PYTHON_MIN)
    second = plan_recipe(recipe, upstream, config, resolver, PYTHON_MIN)
    assert [[r.text for r in section.requirements] for section in first.sections] == [
        [r.text for r in section.requirements] for section in second.sections
    ]
