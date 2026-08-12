"""The comments swage writes into a requirements block (DESIGN.md 6).

These are swage's own lines: it regenerates them from the plan rather than
preserving what was there, so getting them wrong is not cosmetic. A missing
`# start`/`# end` pair means a rerun cannot tell an expansion from something a
maintainer typed, and a header on every line of an output named for its extra
is the redundancy the one-comment-per-extra rule exists to avoid.

Both rules were wrong until a corpus recipe was compared byte for byte
(`test_plan_corpus.py`); these pin them as rules rather than as a property of
four fixtures.
"""

from __future__ import annotations

from swage.config import ConfigTree, load_config
from swage.mapping import NameResolver, StaticPackageIndex
from swage.plan import PythonMin, plan_section
from swage.recipe import read_recipe
from swage.upstream import parse_pyproject

from .conftest import WriteTree

PYTHON_MIN = PythonMin("3.10", "recipe")

UPSTREAM = """\
[project]
name = "demo"
version = "2.0.0"
dependencies = ["requests>=2.31.0"]

[project.optional-dependencies]
redis = ["celery[redis]>=5.3.0"]
pandas = ["pandas>=2.1.0"]
"""

DEFAULTS = """\
trust: manual
recipe_owned:
  names: [python, pip]
"""

FEEDSTOCK = """\
feedstock: demo
embedded_extras:
  "celery[redis]":
    - redis >=4.5.2
    - kombu >=5.3.0
"""

INDEX = StaticPackageIndex.of("requests", "celery", "pandas", "redis", "kombu")


def _config(write_tree: WriteTree) -> ConfigTree:
    return load_config(
        write_tree({"defaults.yaml": DEFAULTS, "feedstocks/demo.yaml": FEEDSTOCK})
    )


def _plan(
    write_tree: WriteTree, recipe_text: str, extras: tuple[str, ...], core: bool
) -> tuple[list[str], tuple[str, ...]]:
    """Plan the recipe's only `run` block; return its lines and trailing comments."""
    recipe = read_recipe(recipe_text)
    config = _config(write_tree).for_feedstock("demo")
    section = plan_section(
        recipe.blocks["/requirements/run"],
        parse_pyproject(UPSTREAM),
        config,
        NameResolver(config.name_map, INDEX),
        PYTHON_MIN,
        listed_extras=extras,
        core=core,
    )
    lines: list[str] = []
    for requirement in section.requirements:
        lines.extend(requirement.comments)
        lines.append(requirement.text)
    return lines, section.trailing_comments


ONE_LINE = """\
requirements:
  run:
    - python
"""


def test_an_expansion_is_wrapped_in_its_start_and_end_markers(
    write_tree: WriteTree,
) -> None:
    """The pair is what makes a rerun idempotent rather than additive."""
    lines, trailing = _plan(write_tree, ONE_LINE, ("redis",), core=False)

    assert lines == [
        "python",
        "celery >=5.3.0",
        "# start celery[redis]",
        "redis >=4.5.2",
        "kombu >=5.3.0",
    ]
    # Nothing follows the expansion, so the closing marker is the block's
    # trailing comment -- it has no requirement to sit above.
    assert trailing == ("# end celery[redis]",)


def test_a_closing_marker_sits_above_whatever_follows_the_expansion(
    write_tree: WriteTree,
) -> None:
    """`# end` belongs below the last expanded line, wherever that lands.

    `apache-hive` and `celery` both carry a dependency after the block, so the
    marker becomes that line's leading comment rather than the section's
    trailing one.
    """
    lines, trailing = _plan(write_tree, ONE_LINE, ("redis", "pandas"), core=False)

    assert lines[-4:] == [
        "kombu >=5.3.0",
        "# end celery[redis]",
        "# from the pandas extra",
        "pandas >=2.1.0",
    ]
    assert trailing == ()


def test_an_output_named_for_its_extra_gets_no_header(
    write_tree: WriteTree,
) -> None:
    """The `extras_as_outputs` shape: one extra, no core, name says which.

    `apache-airflow-providers-common-sql-with-pandas` would otherwise carry
    `# from the pandas extra` above every line it has, which is why none of
    the published provider recipes do.
    """
    lines, _ = _plan(write_tree, ONE_LINE, ("pandas",), core=False)

    assert lines == ["python", "pandas >=2.1.0"]


def test_several_extras_folded_into_one_output_each_get_a_header(
    write_tree: WriteTree,
) -> None:
    """The `outputs[].run.extras` shape, where the question is real.

    `google-cloud-bigquery` folds in nine extras, so a line's origin is not
    recoverable from anything else in the section.
    """
    lines, _ = _plan(write_tree, ONE_LINE, ("pandas", "redis"), core=False)

    assert "# from the pandas extra" in lines
    assert "# from the redis extra" in lines
