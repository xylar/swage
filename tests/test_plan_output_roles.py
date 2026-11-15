"""What each output draws on (design-v1.md 4, 3.3.10).

`extras_as_outputs.suffix` builds the name of a published extra's output, and
`{name}` in it is the *package's* name. A feedstock's name is not necessarily
its package's: `apache-airflow-core-split-feedstock` builds
`apache-airflow-core`. Getting that wrong produces a role key matching no
output, which nothing reports -- the roles just fail to match and every
metapackage gets planned as though it were the library it wraps.
"""

from __future__ import annotations

from swage.config import ConfigTree, FeedstockConfig, load_config
from swage.mapping import NameResolver, StaticPackageIndex
from swage.plan import Plan, PythonMin, find, output_roles, plan_recipe
from swage.recipe import read_recipe
from swage.upstream import RecipeUpstream, parse_pyproject

from .conftest import WriteTree

FAMILY = """\
family: airflow-providers
match:
  feedstock: "apache-airflow-*"
extras_as_outputs:
  suffix: "{name}-with-{extra}"
  supported: [async, graphviz]
  skip: []
"""

#: The shape `apache-airflow-core-split-feedstock` really has: a feedstock
#: named for the split recipe, building a package named for the project.
SPLIT_RECIPE = """\
context:
  name: apache-airflow-core
  version: "3.1.6"
recipe:
  name: ${{ name|lower }}-split
outputs:
  - package:
      name: ${{ name }}
    build:
      noarch: python
    requirements:
      run:
        - python
  - package:
      name: ${{ name }}-with-async
    build:
      noarch: python
    requirements:
      run:
        - python
  - package:
      name: ${{ name }}-with-graphviz
    build:
      noarch: python
    requirements:
      run:
        - python
"""


def _tree(write_tree: WriteTree) -> ConfigTree:
    return load_config(
        write_tree(
            {
                "defaults.yaml": (
                    "trust: never\nrecipe_owned:\n  names: [python, pip]\n"
                ),
                "families/airflow-providers.yaml": FAMILY,
            }
        )
    )


def test_the_suffix_is_built_from_the_package_name_not_the_feedstock_name(
    write_tree: WriteTree,
) -> None:
    """`apache-airflow-core-split` builds `apache-airflow-core`."""
    config = _tree(write_tree).for_feedstock("apache-airflow-core-split")
    roles = output_roles(read_recipe(SPLIT_RECIPE), config)
    assert "apache-airflow-core-with-async" in roles
    assert "apache-airflow-core-split-with-async" not in roles


def test_every_generated_role_matches_an_output_the_recipe_has(
    write_tree: WriteTree,
) -> None:
    """The failure this prevents is silent: a key that matches nothing.

    An unmatched role does not raise -- the output simply falls back to "core,
    no extras", so a metapackage gets planned with every runtime dependency of
    the library it only wraps.
    """
    recipe = read_recipe(SPLIT_RECIPE)
    config = _tree(write_tree).for_feedstock("apache-airflow-core-split")
    outputs = {output.name for output in recipe.outputs}
    assert set(output_roles(recipe, config)) <= outputs


def test_a_feedstock_whose_names_coincide_is_unaffected(
    write_tree: WriteTree,
) -> None:
    """The common case, and the reason this went unnoticed."""
    recipe = read_recipe(
        SPLIT_RECIPE.replace(
            "name: apache-airflow-core", "name: apache-airflow-providers-amazon"
        )
    )
    config = _tree(write_tree).for_feedstock("apache-airflow-providers-amazon")
    assert "apache-airflow-providers-amazon-with-async" in output_roles(recipe, config)


def test_a_recipe_with_no_context_name_falls_back_to_the_feedstock(
    write_tree: WriteTree,
) -> None:
    """Its outputs are named literally, so there is nothing better to go on."""
    recipe = read_recipe(
        "outputs:\n"
        "  - package:\n"
        "      name: apache-airflow-thing-with-async\n"
        "    requirements:\n"
        "      run:\n"
        "        - python\n"
    )
    config = _tree(write_tree).for_feedstock("apache-airflow-thing")
    assert "apache-airflow-thing-with-async" in output_roles(recipe, config)


def test_outputs_declared_in_config_still_win_on_their_own_terms(
    write_tree: WriteTree,
) -> None:
    """`outputs[].run` is keyed by the recipe's output name and always was."""
    tree = load_config(
        write_tree(
            {
                "defaults.yaml": "trust: never\nrecipe_owned:\n  names: [python]\n",
                "feedstocks/demo.yaml": (
                    "feedstock: demo\noutputs:\n  apache-airflow-core:\n"
                    "    run:\n      core: true\n      extras: [async]\n"
                ),
            }
        )
    )
    roles = output_roles(read_recipe(SPLIT_RECIPE), tree.for_feedstock("demo"))
    assert roles["apache-airflow-core"] == (("async",), True)


#: A project with three extras: one an output draws, one deliberately declined,
#: one nobody has said anything about.
UPSTREAM = """\
[project]
name = "demo"
version = "1.0.0"
dependencies = []

[project.optional-dependencies]
pandas = ["pandas>=2.1.0"]
docs = ["sphinx>=7.0"]
tracing = ["opentelemetry-api>=1.1.0"]
"""

ONE_OUTPUT = """\
build:
  noarch: python
requirements:
  run:
    - python
"""


def _plan_demo(write_tree: WriteTree, feedstock_yaml: str) -> Plan:
    tree = load_config(
        write_tree(
            {
                "defaults.yaml": "trust: never\nrecipe_owned:\n  names: [python]\n",
                "feedstocks/demo.yaml": feedstock_yaml,
            }
        )
    )
    config = tree.for_feedstock("demo")
    return plan_recipe(
        read_recipe(ONE_OUTPUT),
        RecipeUpstream.of(parse_pyproject(UPSTREAM)),
        config,
        NameResolver(config.name_map, StaticPackageIndex.of("pandas", "sphinx")),
        PythonMin("3.10", "recipe"),
    )


def test_an_extra_an_output_draws_is_not_reported_as_unaccounted(
    write_tree: WriteTree,
) -> None:
    """The note exists to catch what nobody has decided about (design-v1.md 4).

    This read the `outputs` *override argument* rather than the resolved
    roles, and no caller passes one -- so every extra looked undrawn, and
    `google-cloud-bigquery` reported all nine it explicitly folds into its
    metapackage. Invisible on a feedstock with no config, where "unaccounted"
    is the true and intended answer; wrong on exactly the feedstocks where the
    work had been done.
    """
    plan = _plan_demo(
        write_tree,
        "feedstock: demo\noutputs:\n  demo:\n    run:\n      core: true\n"
        "      extras: [pandas]\n",
    )

    assert plan.unaccounted_extras == ("docs", "tracing")


def test_a_declined_extra_is_accounted_for(write_tree: WriteTree) -> None:
    """`skip` is a decision on the record, so the note must not nag about it.

    Accounted for exactly as firmly as `supported` -- the point is that
    somebody considered it, not which way they went.
    """
    plan = _plan_demo(
        write_tree,
        "feedstock: demo\nextras_as_outputs:\n  suffix: '{name}-with-{extra}'\n"
        "  supported: [pandas]\n  skip: [docs]\n",
    )

    assert plan.unaccounted_extras == ("tracing",)


def test_the_gate_and_the_plan_agree_about_one_extra(write_tree: WriteTree) -> None:
    """Two spellings of "accounted for" would eventually disagree.

    The disagreement would surface as swage naming an extra in one place that
    it calls settled in the other, which is advice pointing at a decision
    already made.
    """
    yaml = (
        "feedstock: demo\nextras_as_outputs:\n  suffix: '{name}-with-{extra}'\n"
        "  supported: [pandas]\n  skip: [docs]\n"
    )
    tree = load_config(
        write_tree(
            {
                "defaults.yaml": "trust: never\nrecipe_owned:\n  names: [python]\n",
                "feedstocks/demo.yaml": yaml,
            }
        )
    )
    config = tree.for_feedstock("demo")
    upstream = parse_pyproject(UPSTREAM)
    plan = _plan_demo(write_tree, yaml)

    (finding,) = [
        finding
        for finding in find(plan, config, RecipeUpstream.of(upstream))
        if finding.kind == "unclassified-extra"
    ]
    for extra in plan.unaccounted_extras:
        assert f"`{extra}`" in finding.said


SPLIT_EXTRA_RECIPE = """\
context:
  name: demo
recipe:
  name: demo-split
  version: "1.0"
outputs:
  - package:
      name: ${{ name }}
    build:
      noarch: python
    requirements:
      run:
        - python >=${{ python_min }}
  - package:
      name: ${{ name }}-with-export-without-zarr
    build:
      noarch: python
    requirements:
      run:
        - python >=${{ python_min }}
        - pandas >=2
        - xarray >=2024.6
  - package:
      name: ${{ name }}-with-export
    build:
      noarch: python
    requirements:
      run:
        - python >=${{ python_min }}
        - zarr >=3.1
"""

SPLIT_EXTRA_UPSTREAM = """\
[project]
name = "demo"
version = "1.0"
[project.optional-dependencies]
export = ["pandas >=2", "xarray >=2024.6", "zarr >=3.1"]
"""

SPLIT_EXTRA_CONFIG = """\
feedstock: demo
outputs:
  demo-with-export-without-zarr:
    run:
      core: false
      from_extras:
        export: [pandas, xarray]
  demo-with-export:
    run:
      core: false
      from_extras:
        export: [zarr]
"""


def _split_plan(write_tree: WriteTree) -> Plan:
    root = write_tree(
        {
            "defaults.yaml": "trust: propose\nrecipe_owned:\n  names: [python, pip]\n",
            "feedstocks/demo.yaml": SPLIT_EXTRA_CONFIG,
        }
    )
    config = load_config(root).for_feedstock("demo")
    recipe = read_recipe(SPLIT_EXTRA_RECIPE)
    return plan_recipe(
        recipe,
        RecipeUpstream.of(parse_pyproject(SPLIT_EXTRA_UPSTREAM)),
        config,
        NameResolver(
            config.name_map, StaticPackageIndex.of("pandas", "xarray", "zarr", "python")
        ),
        PythonMin("3.10", ".ci_support/linux_64_.yaml"),
    )


def _lines(plan: Plan, path: str) -> list[str]:
    section = next(s for s in plan.sections if s.path == path)
    return [entry.text.split()[0] for entry in section.requirements]


def test_an_extra_split_across_outputs_puts_each_package_in_one_of_them(
    write_tree: WriteTree,
) -> None:
    """`wetterdienst` publishes upstream's `export` extra as two packages.

    zarr 3.1 needs python 3.11 and the rest of the extra does not, so the
    recipe carries the four at its own floor and zarr above it. Listing the
    extra whole in either output would write all five into it.
    """
    plan = _split_plan(write_tree)

    assert _lines(plan, "/outputs/1/requirements/run") == ["python", "pandas", "xarray"]
    assert _lines(plan, "/outputs/2/requirements/run") == ["python", "zarr"]


def test_a_split_extra_is_accounted_for(write_tree: WriteTree) -> None:
    """G3 asks whether somebody decided about the extra, not how finely.

    A feedstock that publishes an extra in pieces has decided about it as
    firmly as one folding it into a single output, so the exhaustiveness gate
    must not report it as unnoticed.
    """
    plan = _split_plan(write_tree)
    assert plan.unaccounted_extras == ()


# --- an output that pins a sibling (DESIGN.md §9.1) ----------------------------

#: `email-validator`'s shape: the package, a backward-compatible alias pinning
#: it by a literal name, and a metapackage pinning it through the context.
ALIAS_RECIPE = """\
context:
  name: demo
  version: "1.0"
recipe:
  name: ${{ name }}-meta
outputs:
  - package:
      name: ${{ name }}
    build:
      noarch: python
    requirements:
      host:
        - python ${{ python_min }}.*
        - pip
        - setuptools
      run:
        - python >=${{ python_min }}
        - requests >=2
  - package:
      name: demo_alias
    build:
      noarch: python
    requirements:
      host:
        - python ${{ python_min }}.*
      run:
        - python >=${{ python_min }}
        - ${{ pin_subpackage('demo', upper_bound="x.x.x") }}
  - package:
      name: ${{ name }}-with-fast
    build:
      noarch: python
    requirements:
      run:
        - python >=${{ python_min }}
        - ${{ pin_subpackage(name, exact=True) }}
"""

ALIAS_UPSTREAM = """\
[build-system]
requires = ["setuptools"]
[project]
name = "demo"
version = "1.0"
dependencies = ["requests>=2"]
"""


def _alias_config(
    write_tree: WriteTree, feedstock: str = "feedstock: demo\n"
) -> FeedstockConfig:
    tree = load_config(
        write_tree(
            {
                "defaults.yaml": (
                    "trust: never\nrecipe_owned:\n  names: [python, pip]\n"
                    "  functions: [pin_subpackage]\n"
                ),
                "feedstocks/demo.yaml": feedstock,
            }
        )
    )
    return tree.for_feedstock("demo")


def test_an_output_pinning_a_sibling_takes_no_core(write_tree: WriteTree) -> None:
    """It installs upstream's dependencies through the pin, so listing them again
    is redundant. Both spellings of the pinned name are read.
    """
    roles = output_roles(read_recipe(ALIAS_RECIPE), _alias_config(write_tree))

    assert roles == {"demo_alias": ((), False), "demo-with-fast": ((), False)}


def test_config_naming_a_pinning_output_still_wins(write_tree: WriteTree) -> None:
    config = _alias_config(
        write_tree,
        "feedstock: demo\noutputs:\n  demo_alias:\n    run:\n      core: true\n",
    )
    roles = output_roles(read_recipe(ALIAS_RECIPE), config)

    assert roles["demo_alias"] == ((), True)
    assert roles["demo-with-fast"] == ((), False)


def test_a_sibling_that_takes_no_core_gives_none_through_its_pin(
    write_tree: WriteTree,
) -> None:
    config = _alias_config(
        write_tree, "feedstock: demo\noutputs:\n  demo:\n    run:\n      core: false\n"
    )
    roles = output_roles(read_recipe(ALIAS_RECIPE), config)

    assert "demo_alias" not in roles
    assert "demo-with-fast" not in roles


def test_a_sibling_built_from_another_release_does_not_stand_in(
    write_tree: WriteTree,
) -> None:
    """`psycopg2`'s shape: `psycopg` pins `psycopg-c`, a different sdist whose
    dependencies are not psycopg's.
    """
    ours = parse_pyproject(ALIAS_UPSTREAM)
    theirs = parse_pyproject('[project]\nname = "other"\nversion = "1.0"\n')
    upstream = RecipeUpstream(
        releases=(theirs, ours),
        by_output={"demo": theirs, "demo_alias": ours, "demo-with-fast": theirs},
    )
    roles = output_roles(read_recipe(ALIAS_RECIPE), _alias_config(write_tree), upstream)

    assert "demo_alias" not in roles
    assert roles["demo-with-fast"] == ((), False)


def test_an_alias_is_planned_without_upstream_dependencies_or_a_backend(
    write_tree: WriteTree,
) -> None:
    """Neither `requests` in `run` nor `setuptools` in `host`, which is what
    email-validator's alias got in `run` and slack-sdk's in `host`.
    """
    config = _alias_config(write_tree)
    plan = plan_recipe(
        read_recipe(ALIAS_RECIPE),
        RecipeUpstream.of(parse_pyproject(ALIAS_UPSTREAM)),
        config,
        NameResolver(config.name_map, StaticPackageIndex.of("requests", "setuptools")),
        PythonMin("3.10", ".ci_support/linux_64_.yaml"),
    )

    assert _lines(plan, "/outputs/0/requirements/run") == ["python", "requests"]
    assert _lines(plan, "/outputs/1/requirements/host") == ["python"]
    assert _lines(plan, "/outputs/1/requirements/run") == ["python", "${{"]
    assert _lines(plan, "/outputs/2/requirements/run") == ["python", "${{"]
