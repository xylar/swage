"""Reading a conversion against the recipe it was made from (DESIGN.md 7).

Four of the corpus's v0 recipes are compiled, and between them they carry every
answer the review can give:

| fixture | what it proves |
|---|---|
| `tiledb` | twenty conditions, all landing -- the review stays quiet |
| `igraph` | a condition that lands nowhere, taking a value with it |
| `aiohttp` | the same, on `{% set %}` statements rather than on a key |
| `fiona` | a value the converter truncated, and both spellings of a condition |

Every one of them was found by running the converter over the fleet's 148 v0
recipes, so these are outcomes that occur rather than ones imagined here.
"""

from __future__ import annotations

from pathlib import Path

from swage.migrate import Review, convert_recipe, review_conversion

CORPUS = Path(__file__).resolve().parent / "corpus" / "v0"


def meta_yaml(feedstock: str) -> str:
    return (CORPUS / feedstock / "meta.yaml").read_text(encoding="utf-8")


def review(feedstock: str) -> Review:
    """The conversion of ``feedstock``, read back against its `meta.yaml`."""
    return convert_recipe(meta_yaml(feedstock), feedstock).review


def test_a_recipe_with_no_conditions_has_nothing_to_review() -> None:
    """`calver` is 104 of the fleet's 105 noarch v0 recipes.

    Nothing conditional in it at all, so the ledger is empty rather than
    uninteresting -- which is what makes this review free on the noarch half.
    """
    reviewed = review("calver")

    assert reviewed.conditions == ()
    assert reviewed.damage == ()


def test_a_compiled_recipe_that_converts_faithfully_says_nothing_is_wrong() -> None:
    """`tiledb`: twenty selector comments, three conditions, all landing.

    The ledger is per condition rather than per line, because `# [win]` nine
    times over is one thing to check. This is the fixture that keeps the review
    from crying wolf: a check that fired on an ordinary compiled feedstock
    would be worse than no check, the whole point being to say where to look.
    """
    reviewed = review("tiledb")

    assert reviewed.damage == ()
    assert {condition.selector for condition in reviewed.conditions} == {
        "win",
        "not win",
        "unix",
    }
    assert all(
        condition.landed == ("if",) * len(condition.guarded)
        for condition in reviewed.conditions
    )


def test_a_condition_the_conversion_dropped_is_damage() -> None:
    """`igraph` conditions an entry of `build.script_env` on `arm64`.

    CRM emits `script: {}` -- the environment variable is gone and the package
    would be built without it. It does say something, and what it says is that
    the selector needs replacing with a `cmp()` call, which reads like a
    nuance rather than like a deletion.
    """
    reviewed = review("igraph")

    lost = [condition for condition in reviewed.conditions if condition.lost]
    assert [condition.selector for condition in lost] == ["arm64"]
    assert lost[0].guarded == (
        "- F2C_EXTERNAL_ARITH_HEADER={{ RECIPE_DIR }}/arith_arm64.h",
    )
    assert reviewed.damage == (
        "the `arm64` condition is not in the converted recipe at all, and "
        "neither is what it applied to:\n"
        "  meta.yaml    - F2C_EXTERNAL_ARITH_HEADER="
        "{{ RECIPE_DIR }}/arith_arm64.h",
    )


def test_the_conditions_around_a_lost_one_are_reported_as_landing() -> None:
    """`igraph`'s other four conditions convert perfectly, and say so.

    The reason `arm64` is worth reporting is that `osx and arm64` beside it is
    not -- one selector on a list member, which converts, and one on a scalar,
    which does not. A review that flagged the whole recipe would point at
    nothing.
    """
    reviewed = review("igraph")

    landed = {
        condition.selector: set(condition.landed)
        for condition in reviewed.conditions
        if not condition.lost
    }
    assert landed == {
        "osx and arm64": {"if"},
        "not win": {"if"},
        "win": {"if"},
        "unix": {"if"},
    }


def test_conditions_on_jinja_statements_are_lost_the_same_way() -> None:
    """`aiohttp` builds a test-skip list out of conditional `{% set %}` lines.

    v1 has no `{% set %}`, so all four go. CRM reports that `tests_to_skip` is
    "defined multiple times", which says the conversion hit something it could
    not do and not that four platform-specific test skips were dropped.
    """
    reviewed = review("aiohttp")

    lost = {condition.selector for condition in reviewed.conditions if condition.lost}
    assert lost == {"py>=311", "py>=313", "linux", "aarch64 or ppc64le"}
    assert len(reviewed.damage) == 4


def test_a_truncated_value_is_reported_with_what_the_recipe_said() -> None:
    """`fiona` conditions `build.script` on `# [unix]`.

    Folding the condition in, CRM strips three characters off each end of the
    value, meaning to unwrap a scalar that is wholly a `{{ ... }}` expression.
    This one only starts with one, so `--no-build-isolation` loses its last two
    characters and the `${{` is never closed.

    Both lines are quoted, on their own lines and in the same column, because
    the converted one alone looks like an ordinary conditional and the
    difference between the two is two characters in the middle of a long
    command.
    """
    reviewed = review("fiona")

    assert reviewed.damage == (
        "the converter cut this value short while folding a condition into "
        "it, leaving a `${{` it never closes:\n"
        "  meta.yaml    script: {{ PYTHON }} -m pip install . -vv --no-deps "
        "--no-build-isolation  # [unix]\n"
        "  recipe.yaml  content: ${{ PYTHON }} -m pip install . -vv --no-deps "
        "--no-build-isolati if unix else '' }}",
    )


def test_a_python_selector_is_matched_the_way_the_converter_spells_it() -> None:
    """`fiona`'s `py<310` becomes `match(python, "<3.10")` and still lands.

    Platform conditions come through a conversion unchanged and Python ones do
    not, so a ledger comparing the two literally would report every Python
    condition in the fleet as lost.
    """
    reviewed = review("fiona")

    condition = next(c for c in reviewed.conditions if c.selector == "py<310")
    assert condition.expression == 'match(python, "<3.10")'
    assert condition.landed == ("if",)


def test_a_condition_is_not_found_inside_its_own_negation() -> None:
    """`skip` joins conditions into one expression, so matching is by substring.

    The trap that walks into is `not win`, which contains `win`. Reporting a
    condition as landed when it has not is the expensive direction -- it hides
    a dropped value, which is the one thing here nothing else catches.
    """
    reviewed = review_conversion(
        "build:\n  skip: true  # [not win]\n  number: 0  # [win]\n",
        "build:\n  skip: not win\n",
    )

    landed = {c.selector: c.landed for c in reviewed.conditions}
    assert landed == {"not win": ("skip",), "win": ()}


def test_a_condition_that_is_itself_compound_lands_whole() -> None:
    """`# [win and vc<14]` becomes `skip: win and vc<14`, unchanged.

    Found by running this over the fleet rather than here: an earlier reading
    split the skip expression on `and` and `or` before looking for the
    condition inside the pieces, which no condition holding an `and` or an `or`
    of its own can survive. Seven feedstocks convert perfectly and every one of
    them was reported as having lost everything it stated -- a review that
    cries wolf on ordinary recipes is worse than no review.

    No corpus recipe reaches it: none of the eight writes a `build.skip` at
    all, which is why this fixture is written out rather than vendored.
    """
    reviewed = review_conversion(
        "build:\n  skip: true  # [win and vc<14]\n",
        "build:\n  skip: win and vc<14\n",
    )

    assert reviewed.damage == ()
    assert [c.landed for c in reviewed.conditions] == [("skip",)]
