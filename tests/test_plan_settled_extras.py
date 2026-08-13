"""An `embedded_extras` entry that is empty on purpose (DESIGN.md 4, 6).

Absent and empty are different claims. Absent means nobody has looked at the
extra, and G2 stops the feedstock over it. Empty means somebody did and
conda-forge needs nothing beyond the bare dependency -- a decision, and the
recipe says so on the line it is about.

The prior tools recorded the same decision as a `# start` / `# end` pair
around no lines at all, which is 13 of the 22 marker pairs in the fleet's
checkouts. These pin the replacement, and in particular that the caption is
recognized as swage's own: a wording the emitter writes and `authored.py` does
not know is a comment that gets duplicated on every affected feedstock at once.
"""

from __future__ import annotations

from swage.config import ConfigTree, load_config
from swage.mapping import NameResolver, StaticPackageIndex
from swage.plan import PythonMin, plan_section
from swage.plan.authored import is_swage_authored
from swage.recipe import read_recipe
from swage.upstream import parse_pyproject

from .conftest import WriteTree

PYTHON_MIN = PythonMin("3.10", "recipe")

INDEX = StaticPackageIndex.of("celery", "flower", "redis", "kombu", "python", "psycopg")

UPSTREAM = """\
[project]
name = "demo"
version = "2.0.0"
dependencies = ["celery[redis] >=5.5.0,<6", "flower >=1.0.0"]
"""

RECIPE = """\
schema_version: 1

package:
  name: demo
  version: 2.0.0

requirements:
  run:
    - python >=${{ python_min }}
    - celery >=5.5.0,<6
    - flower >=1.0.0
"""

DEFAULTS = "trust: manual\nrecipe_owned:\n  names: [python, pip]\n"


def _lines(write_tree: WriteTree, feedstock: str) -> list[str]:
    """Every rendered line of the `run` block, comments included, in order."""
    tree: ConfigTree = load_config(
        write_tree({"defaults.yaml": DEFAULTS, "feedstocks/demo.yaml": feedstock})
    )
    config = tree.for_feedstock("demo")
    section = plan_section(
        read_recipe(RECIPE).blocks["/requirements/run"],
        parse_pyproject(UPSTREAM),
        config,
        NameResolver(config.name_map, INDEX),
        PYTHON_MIN,
    )
    rendered: list[str] = []
    for requirement in section.requirements:
        rendered.extend(requirement.comments)
        rendered.append(requirement.text)
    rendered.extend(section.trailing_comments)
    return rendered


EMPTY = 'feedstock: demo\nembedded_extras:\n  "celery[redis]": []\n'
FILLED = (
    "feedstock: demo\nembedded_extras:\n"
    '  "celery[redis]":\n    - redis >=4.5.2\n    - kombu >=5.3.0\n'
)


def test_an_empty_entry_captions_the_line_it_belongs_to(
    write_tree: WriteTree,
) -> None:
    assert _lines(write_tree, EMPTY) == [
        "python >=${{ python_min }}",
        "# celery[redis] needs nothing extra on conda-forge",
        "celery >=5.5.0,<6",
        "flower >=1.0.0",
    ]


def test_an_empty_entry_renders_no_marker_pair(write_tree: WriteTree) -> None:
    """The pair delimits a span of lines, and there are none to delimit."""
    rendered = _lines(write_tree, EMPTY)
    assert not [line for line in rendered if line.startswith(("# start", "# end"))]


def test_a_filled_entry_still_renders_the_markers_and_no_caption(
    write_tree: WriteTree,
) -> None:
    """The two shapes are alternatives, never both."""
    assert _lines(write_tree, FILLED) == [
        "python >=${{ python_min }}",
        "celery >=5.5.0,<6",
        "# start celery[redis]",
        "redis >=4.5.2",
        "kombu >=5.3.0",
        "# end celery[redis]",
        "flower >=1.0.0",
    ]


def test_an_absent_entry_renders_neither(write_tree: WriteTree) -> None:
    """Silence is not a decision, so nothing is written down as though it were."""
    assert _lines(write_tree, "feedstock: demo\n") == [
        "python >=${{ python_min }}",
        "celery >=5.5.0,<6",
        "flower >=1.0.0",
    ]


def test_the_caption_is_recognized_as_swage_authored() -> None:
    """Otherwise a rerun preserves it and writes a second one beside it.

    The failure is fleet-wide and lands all at once, which is why this is
    checked against the emitter's exact wording rather than a hand-typed one.
    """
    assert is_swage_authored("# celery[redis] needs nothing extra on conda-forge")
    assert is_swage_authored(
        "# hdfs[avro,dataframe] needs nothing extra on conda-forge"
    )


def test_a_maintainer_sentence_ending_the_same_way_is_preserved() -> None:
    """The `name[extra]` shape is what keeps the pattern off a human's note."""
    assert not is_swage_authored("# this package needs nothing extra on conda-forge")


def test_the_caption_survives_a_rerun_without_duplicating(
    write_tree: WriteTree,
) -> None:
    """Plan a recipe swage already wrote: the caption appears once, not twice."""
    tree = load_config(
        write_tree({"defaults.yaml": DEFAULTS, "feedstocks/demo.yaml": EMPTY})
    )
    config = tree.for_feedstock("demo")
    already = RECIPE.replace(
        "    - celery >=5.5.0,<6",
        "    # celery[redis] needs nothing extra on conda-forge\n"
        "    - celery >=5.5.0,<6",
    )
    section = plan_section(
        read_recipe(already).blocks["/requirements/run"],
        parse_pyproject(UPSTREAM),
        config,
        NameResolver(config.name_map, INDEX),
        PYTHON_MIN,
    )
    captions = [
        comment
        for requirement in section.requirements
        for comment in requirement.comments
        if "needs nothing extra" in comment
    ]
    assert captions == ["# celery[redis] needs nothing extra on conda-forge"]
