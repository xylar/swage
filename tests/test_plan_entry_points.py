"""Which entry-point lists swage would rewrite (DESIGN.md 3.3.15).

Every case here is one the fleet showed. m2r2 is the retarget the feature
exists for; cartopy is the rename that has to reach a person; pyproj is the
spacing that must not become a diff; flask is the recipe with no list at all.
"""

from __future__ import annotations

from dataclasses import replace

from swage.plan import plan_entry_points
from swage.recipe import read_recipe
from swage.upstream import EntryPoint, RecipeUpstream, UpstreamMetadata

from .test_recipe_entry_points import CONDITIONAL, recipe


def upstream(*scripts: str, declared: bool = True) -> RecipeUpstream:
    points = tuple(EntryPoint(*item.split(" = ")) for item in scripts)
    return RecipeUpstream.of(
        UpstreamMetadata(
            name="m2r2", version="1.1.0", entry_points=points if declared else None
        )
    )


def test_a_moved_target_is_rewritten_and_said() -> None:
    """m2r2: the script kept its name and upstream moved what it runs."""
    parsed = read_recipe(recipe(), "m2r2")

    changes, notes = plan_entry_points(parsed, upstream("m2r2 = m2r2.cli.m2r2:main"))

    (change,) = changes
    assert change.items == ("m2r2 = m2r2.cli.m2r2:main",)
    assert change.retargeted == (("m2r2", "m2r2:main", "m2r2.cli.m2r2:main"),)
    assert change.said == (
        "entry point `m2r2` now runs `m2r2.cli.m2r2:main`, which is what "
        "upstream declares; it ran `m2r2:main`",
    )
    assert change.held == ()
    assert notes == ()


def test_a_list_that_already_matches_is_no_change() -> None:
    parsed = read_recipe(recipe(), "m2r2")

    changes, notes = plan_entry_points(parsed, upstream("m2r2 = m2r2:main"))

    assert changes == ()
    assert notes == ()


def test_spacing_alone_is_not_a_change() -> None:
    """`pyproj=pyproj.__main__:main` says what upstream says; leave it."""
    parsed = read_recipe(recipe("    entry_points:\n      - m2r2=m2r2:main\n"), "m2r2")

    changes, _ = plan_entry_points(parsed, upstream("m2r2 = m2r2:main"))

    assert changes == ()


def test_a_renamed_script_is_added_and_dropped_and_the_drop_is_held() -> None:
    """cartopy: to swage a rename, to a user a command going away."""
    parsed = read_recipe(recipe(), "m2r2")

    changes, _ = plan_entry_points(parsed, upstream("m2r2-cli = m2r2.cli:main"))

    (change,) = changes
    assert change.items == ("m2r2-cli = m2r2.cli:main",)
    assert change.added == ("m2r2-cli = m2r2.cli:main",)
    assert change.dropped == ("m2r2 = m2r2:main",)
    assert change.said == (
        "entry point `m2r2-cli = m2r2.cli:main` is added, because upstream "
        "declares it and the recipe did not list it",
    )
    (held,) = change.held
    assert held.startswith("entry point `m2r2 = m2r2:main` is dropped")
    assert "held for a person to confirm" in held


def test_the_list_is_written_in_upstream_s_order() -> None:
    """DESIGN.md 6: every list swage writes follows the source's order."""
    parsed = read_recipe(recipe(), "m2r2")

    changes, _ = plan_entry_points(
        parsed, upstream("z = z:main", "m2r2 = m2r2:main", "a = a:main")
    )

    (change,) = changes
    assert change.items == ("z = z:main", "m2r2 = m2r2:main", "a = a:main")
    assert change.added == ("z = z:main", "a = a:main")
    assert change.dropped == ()


def test_a_reorder_alone_is_not_a_change() -> None:
    """wetterdienst lists its two scripts the other way round from upstream.

    Rewriting them to upstream's order is a two-line diff for nothing; the
    order rule (DESIGN.md 6) applies where a list is being rewritten anyway.
    """
    two = "    entry_points:\n      - b = b:main\n      - a = a:main\n"
    parsed = read_recipe(recipe(two), "m2r2")

    changes, _ = plan_entry_points(parsed, upstream("a = a:main", "b = b:main"))

    assert changes == ()


def test_upstream_that_cannot_say_is_left_alone() -> None:
    """Silence, not emptiness: a `dynamic = ["scripts"]` table says nothing."""
    parsed = read_recipe(recipe(), "m2r2")

    changes, notes = plan_entry_points(parsed, upstream(declared=False))

    assert changes == ()
    assert notes == ()


def test_upstream_declaring_none_empties_the_list_and_holds_it() -> None:
    """Emptiness is a claim, and the drop it implies reaches a person."""
    parsed = read_recipe(recipe(), "m2r2")

    changes, _ = plan_entry_points(parsed, upstream())

    (change,) = changes
    assert change.items == ()
    assert change.dropped == ("m2r2 = m2r2:main",)


def test_a_recipe_with_no_list_is_not_remarked_on() -> None:
    """flask lists none, tests `flask --help`, and passes."""
    parsed = read_recipe(recipe("", ""), "m2r2")

    changes, notes = plan_entry_points(parsed, upstream("flask = flask.cli:main"))

    assert changes == ()
    assert notes == ()


def test_a_conditional_list_is_left_alone_with_a_note() -> None:
    parsed = read_recipe(recipe(CONDITIONAL), "m2r2")

    changes, notes = plan_entry_points(parsed, upstream("m2r2 = m2r2.cli:main"))

    assert changes == ()
    (note,) = notes
    assert note.startswith("the entry_points list holds an if: entry")
    assert "`m2r2 = m2r2.cli:main`" in note


def test_the_output_is_named_where_the_recipe_has_several() -> None:
    text = """context:
  version: '1.0'

recipe:
  name: demo
  version: ${{ version }}

outputs:
  - package:
      name: demo-core
    build:
      noarch: python
      python:
        entry_points:
          - demo = demo:main
    requirements:
      run:
        - python
  - package:
      name: demo
    build:
      noarch: python
    requirements:
      run:
        - ${{ pin_subpackage('demo-core', exact=True) }}
"""
    parsed = read_recipe(text, "demo")
    release = UpstreamMetadata(
        name="demo",
        version="1.0",
        entry_points=(EntryPoint("demo", "demo.cli:main"),),
    )

    changes, _ = plan_entry_points(parsed, RecipeUpstream.of(release))

    (change,) = changes
    assert change.path == "/outputs/0/build/python/entry_points"
    assert change.output == "demo-core"
    assert change.said[0].startswith("entry point `demo` for `demo-core` now runs")


def test_the_sentences_survive_markdown() -> None:
    """`module:attr` with underscores is exactly what markdown italicizes."""
    parsed = read_recipe(recipe(), "m2r2")
    changes, _ = plan_entry_points(parsed, upstream("m2r2 = m2r2.__main__:_main"))
    (change,) = changes
    assert "`m2r2.__main__:_main`" in change.said[0]
    assert replace(change, retargeted=()).said == ()
