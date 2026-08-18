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

import pytest

from swage.config import ConfigTree, load_config
from swage.mapping import NameResolver, StaticPackageIndex
from swage.plan import PythonMin, plan_section
from swage.plan.authored import is_swage_authored
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
trust: never
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


@pytest.mark.parametrize(
    "wording",
    [
        "# strictest constraint for python >=3.13",
        "# strictest lower bound for python >=3.13; upper bound from python >=3.10",
    ],
)
def test_a_third_predecessors_marker_wording_is_replaced_too(
    wording: str, write_tree: WriteTree
) -> None:
    """`google-ads` carries a spelling neither replaced tool wrote.

    It was found by reading what swage would push to that feedstock rather
    than by a test or by the fleet comparison: without these patterns the
    first run writes swage's note above each of seven requirements and leaves
    the old one underneath.
    """
    recipe = f"""\
requirements:
  run:
    - python
    {wording}
    - numpy >=1.26.0
"""
    lines, _ = _plan(write_tree, recipe, (), core=True)

    assert "# tightest of upstream's floors (python >=3.13)" in lines
    assert not [line for line in lines if "strictest" in line]


def test_a_per_range_label_is_replaced_where_the_lines_it_labels_collapse(
    write_tree: WriteTree,
) -> None:
    """`apache-airflow-providers-amazon` states one dependency per range.

    Both lines collapse into the tightest, so a label calling the survivor
    "conditional for python >=3.13" describes something that no longer exists
    -- which is why this wording is retired rather than preserved, even though
    a person probably wrote it.
    """
    recipe = """\
requirements:
  run:
    - python
    # conditional for python <3.13
    - numpy >=1.24.0
    # conditional for python >=3.13
    - numpy >=1.26.0
"""
    lines, _ = _plan(write_tree, recipe, (), core=True)

    assert "# tightest of upstream's floors (python >=3.13)" in lines
    assert not [line for line in lines if "conditional for" in line]
    assert [line for line in lines if line.startswith("numpy")] == ["numpy >=1.26.0"]


def test_swage_recognizes_every_shape_of_its_own_marker_note() -> None:
    """A note swage writes and cannot recognize is one it duplicates.

    Asserted directly rather than through a plan, because the failure is in
    the pairing of two lists that are edited at different times -- the note
    grew a second shape in `reconcile` and would have kept working until the
    *next* run on a recipe carrying one.
    """
    for note in (
        "# tightest of upstream's floors (python >=3.13)",
        "# tightest of upstream's ceilings (python >=3.13)",
        "# tightest of upstream's floors (python >=3.13) and ceilings (python >=3.10)",
    ):
        assert is_swage_authored(note), note


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


def test_a_blank_line_stays_above_the_note_rather_than_splitting_it_off(
    write_tree: WriteTree,
) -> None:
    """A blank line is spacing between groups, not a remark about a dependency.

    Ordered with the maintainer's comments it lands between swage's note and
    the line the note is about, which reads as though the two were unrelated.
    `apache-airflow-providers-google` has exactly this shape.
    """
    recipe = """\
requirements:
  run:
    - python
    - requests >=2.31.0

    # more restrictive constraint for python >=3.13
    - numpy >=1.26.0
"""
    lines, _ = _plan(write_tree, recipe, (), core=True)

    assert lines[-3:] == [
        "",
        "# tightest of upstream's floors (python >=3.13)",
        "numpy >=1.26.0",
    ]


def test_the_shorthand_extra_header_is_replaced_by_swages_own(
    write_tree: WriteTree,
) -> None:
    """`# pandas extra` is the hand-written spelling of swage's header.

    It appears in the airflow, google-auth and google-cloud-bigquery recipes
    and says exactly what `# from the pandas extra` says, so preserving it
    would put two headers above one block.
    """
    recipe = """\
requirements:
  run:
    - python
    # pandas extra
    - pandas >=2.1.0
"""
    lines, _ = _plan(write_tree, recipe, ("pandas", "redis"), core=False)

    assert "# from the pandas extra" in lines
    assert "# pandas extra" not in lines


def test_a_sentence_ending_in_extra_is_still_the_maintainers(
    write_tree: WriteTree,
) -> None:
    """The guard on the shorthand above, and why it is not `.* extra$`.

    `google-cloud-bigquery` explains its `google-auth` line with a note that
    happens to end in the word. Matching that would delete the very comment
    DESIGN.md 6.1 was written to save.
    """
    note = "# conda-forge package includes google-auth[pyopenssl] extra"
    recipe = f"""\
requirements:
  run:
    - python
    {note}
    - requests >=2.31.0
"""
    lines, _ = _plan(write_tree, recipe, (), core=True)

    assert note in lines


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


def test_the_hand_written_expansion_label_is_replaced_by_the_marker_pair(
    write_tree: WriteTree,
) -> None:
    """`# celery[redis]` is the shorthand for `# start celery[redis]`.

    `apache-airflow-providers-google`'s recipe labels its expansion block this
    way. Preserving it would leave that recipe carrying both the label swage
    does not write and the marker pair it does, which is the duplication
    `_RETIRED` exists to prevent (DESIGN.md 6.1).
    """
    recipe = """\
requirements:
  run:
    - python
    - celery >=5.3.0
    # celery[redis]
    - redis >=4.5.2
    - kombu >=5.3.0
"""
    lines, _ = _plan(write_tree, recipe, ("redis",), core=False)

    assert lines.count("# celery[redis]") == 0
    assert "# start celery[redis]" in lines


def test_the_later_of_two_lines_collapsing_into_one_supplies_the_comments(
    write_tree: WriteTree,
) -> None:
    """An expansion repeating a dependency upstream also declares.

    Both recipe lines are the same planned line, and the comments above the
    first are about the expansion -- which swage now delimits with markers of
    its own, so carrying them down to where the dependency actually renders
    re-anchors a remark to something it was never about.
    `apache-airflow-providers-google` moved one 50 lines down the section.
    """
    recipe = """\
requirements:
  run:
    - python

    # the evaluation extra, written out by hand
    - pandas >=1.0.0
    - requests >=2.31.0
    - pandas >=2.1.0
"""
    lines, _ = _plan(write_tree, recipe, ("pandas",), core=True)

    assert "# the evaluation extra, written out by hand" not in lines


TRAILING_NOTE = """\
requirements:
  run:
    - python
    # seems to work without it
    # - standard-distutils
"""


def test_a_note_at_the_end_of_a_section_is_not_deleted(
    write_tree: WriteTree,
) -> None:
    """It has no requirement below it, and swage renders the section.

    Two sections in the fleet end one this way, `pymssql`'s `host` and one
    of `parsl`'s `run` lists, each recording a dependency left out on
    purpose -- which is the decision `exclude` is specified to hold
    (DESIGN.md 3.3.13) and, until it exists, the only trace of it there is.
    On `pymssql` swage's plan puts the dependency back, so deleting the note
    would reverse a decision and remove the record of it in one edit.
    """
    lines, trailing = _plan(write_tree, TRAILING_NOTE, (), core=True)

    assert lines[0] == "python"
    assert trailing == ("# seems to work without it", "# - standard-distutils")


def test_swages_own_marker_still_comes_before_a_preserved_note(
    write_tree: WriteTree,
) -> None:
    """Generated first, preserved after -- DESIGN.md 6.1's order everywhere."""
    recipe = """\
requirements:
  run:
    - python
    # a note the maintainer left
"""
    _, trailing = _plan(write_tree, recipe, ("redis",), core=False)

    assert trailing == ("# end celery[redis]", "# a note the maintainer left")


BUILD_PINNED = """\
requirements:
  run:
    # need to list hdf5 twice to get version pinning from variants and
    # build pinning from ${{ mpi_prefix }}
    - hdf5
    - hdf5 * ${{ mpi_prefix }}_*
    - python
"""


def test_a_package_stated_twice_over_a_build_string_keeps_both_lines(
    write_tree: WriteTree,
) -> None:
    """`esmf`, `mpas_tools` and `e3sm-tools` state their mpi deps this way.

    The plain line takes the version pinning conda-forge's variants supply and
    the pinned one takes the build pinning of the mpi variant, so the pair is
    two requirements rather than two spellings of one. Filed under the package
    name alone, the second read as a constraint change to the first: swage
    rewrote `hdf5 * ${{ mpi_prefix }}_*` to `hdf5` and the mpi pin left the
    recipe, taking the maintainer's note about it along.
    """
    lines, _ = _plan(write_tree, BUILD_PINNED, (), core=True)

    assert lines == [
        "python",
        "requests >=2.31.0",
        "# tightest of upstream's floors (python >=3.13)",
        "numpy >=1.26.0",
        # Neither line is upstream's, so both land in the trailing block
        # DESIGN.md 6 puts conda-forge's own additions in -- together, and
        # with the note still above the pair it is about.
        "# need to list hdf5 twice to get version pinning from variants and",
        "# build pinning from ${{ mpi_prefix }}",
        "hdf5",
        "hdf5 * ${{ mpi_prefix }}_*",
    ]
