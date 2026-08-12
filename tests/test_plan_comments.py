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
dependencies = [
  "requests>=2.31.0",
  "numpy>=1.24.0; python_version < '3.13'",
  "numpy>=1.26.0; python_version >= '3.13'",
]

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

INDEX = StaticPackageIndex.of("requests", "celery", "pandas", "redis", "kombu", "numpy")


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


# --- comments swage did *not* write (DESIGN.md 6.1) ------------------------


def test_a_maintainer_comment_survives_above_an_upstream_line(
    write_tree: WriteTree,
) -> None:
    """The case that went unnoticed until a corpus recipe carried one.

    `google-cloud-bigquery` explains its `google-auth` line with a note about
    the `pyopenssl` extra. That line is upstream-derived, and the planner used
    to build those from the plan alone -- so the note was destroyed while an
    identical note above a line swage *could not* explain would have survived.
    """
    recipe = """\
requirements:
  run:
    - python
    # conda-forge package includes google-auth[pyopenssl] extra
    - requests >=2.31.0
"""
    lines, _ = _plan(write_tree, recipe, (), core=True)

    assert lines[:3] == [
        "python",
        "# conda-forge package includes google-auth[pyopenssl] extra",
        "requests >=2.31.0",
    ]


def test_a_maintainer_comment_moves_with_its_dependency(
    write_tree: WriteTree,
) -> None:
    """Anchored to the requirement, not to a position in the list.

    Upstream declares `requests` before `numpy`, so planning reorders this
    section. A comment that stayed where it was would end up describing
    `numpy` -- valid YAML and silently false, which is what ruled out
    conda-recipe-manager (DESIGN.md 3.1).
    """
    recipe = """\
requirements:
  run:
    - python
    - numpy >=1.26.0
    # pinned high because the old wheels are broken on aarch64
    - requests >=2.31.0
"""
    lines, _ = _plan(write_tree, recipe, (), core=True)

    note = "# pinned high because the old wheels are broken on aarch64"
    assert lines.index(note) == lines.index("requests >=2.31.0") - 1
    assert lines.index("requests >=2.31.0") < lines.index("numpy >=1.26.0")


def test_a_retired_marker_wording_is_replaced_rather_than_preserved(
    write_tree: WriteTree,
) -> None:
    """The expensive half of DESIGN.md 6.1, and the reason `_RETIRED` exists.

    A recipe in the wild carries the wording swage used to emit. It is still
    swage's comment -- a tool wrote it, no human chose it -- so it has to be
    replaced by its successor rather than preserved beside it. Treating it as
    maintainer prose would leave two notes above one dependency saying the same
    thing differently, on all 53 fleet recipes carrying one, on the first run.
    """
    recipe = """\
requirements:
  run:
    - python
    # more restrictive constraint for python >=3.13
    - numpy >=1.26.0
"""
    lines, _ = _plan(write_tree, recipe, (), core=True)

    assert "# tightest of upstream's floors (python >=3.13)" in lines
    assert not [line for line in lines if "more restrictive" in line]
    assert len([line for line in lines if line.startswith("# tightest")]) == 1


def test_a_generated_note_precedes_a_preserved_one(write_tree: WriteTree) -> None:
    """Structure first, then the maintainer's remark closest to its line."""
    recipe = """\
requirements:
  run:
    - python
    # more restrictive constraint for python >=3.13
    # and we cannot drop it until the 3.12 builds are retired
    - numpy >=1.26.0
"""
    lines, _ = _plan(write_tree, recipe, (), core=True)

    assert lines[-3:] == [
        "# tightest of upstream's floors (python >=3.13)",
        "# and we cannot drop it until the 3.12 builds are retired",
        "numpy >=1.26.0",
    ]


def test_a_current_header_is_not_duplicated(write_tree: WriteTree) -> None:
    """swage regenerates its own headers, so finding one must not add a second."""
    recipe = """\
requirements:
  run:
    - python
    # from the pandas extra
    - pandas >=2.1.0
"""
    lines, _ = _plan(write_tree, recipe, ("pandas", "redis"), core=False)

    assert lines.count("# from the pandas extra") == 1
