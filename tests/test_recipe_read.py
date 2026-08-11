"""Tests for the recipe reader (DESIGN.md 3.1).

The corpus does most of the work here: if the reader can take apart 27 real
recipes and say exactly which lines each requirements block occupies, the
splicing that swage's write path depends on has something solid underneath it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from swage.recipe import RecipeError, read_recipe, resolve_expression

from .conftest import REPO_ROOT

CORPUS = REPO_ROOT / "tests" / "corpus"
RECIPES = sorted(CORPUS.rglob("*recipe.yaml"))
AIRFLOW = CORPUS / "airflow-providers"
GOOGLE = CORPUS / "google-cloud"


@pytest.mark.parametrize("path", RECIPES, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_every_corpus_recipe_reads(path: Path) -> None:
    recipe = read_recipe(path.read_text(encoding="utf-8"), str(path))
    assert recipe.outputs
    # Every recipe in the corpus declares dependencies somewhere.
    assert recipe.blocks


@pytest.mark.parametrize("path", RECIPES, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_block_line_ranges_land_on_requirements(path: Path) -> None:
    """Each block's range must cover its own items and nothing else."""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    recipe = read_recipe(text, str(path))
    for block in recipe.blocks.values():
        body = lines[block.first_line : block.end_line]
        items = [line for line in body if line.strip().startswith("- ")]
        assert len(items) == len(block.content.requirements)
        # The line above the block is the section key that owns it.
        assert lines[block.first_line - 1].strip() == f"{block.section}:"
        # Nothing in the range is shallower than the items.
        for line in body:
            if line.strip():
                assert len(line) - len(line.lstrip()) >= block.item_indent


def test_a_single_output_recipe_has_one_unindexed_output() -> None:
    path = AIRFLOW / "providers-databricks_7.18.1" / "recipe.yaml"
    recipe = read_recipe(path.read_text(encoding="utf-8"), str(path))
    assert len(recipe.outputs) == 1
    output = recipe.outputs[0]
    assert output.index is None
    assert output.name == "apache-airflow-providers-databricks"
    assert set(output.blocks) == {"host", "run"}
    assert output.blocks["run"].path == "/requirements/run"


def test_a_comment_is_read_onto_the_dependency_below_it() -> None:
    path = AIRFLOW / "providers-databricks_7.18.1" / "recipe.yaml"
    recipe = read_recipe(path.read_text(encoding="utf-8"), str(path))
    run = recipe.outputs[0].blocks["run"].content
    by_text = {requirement.text: requirement for requirement in run.requirements}
    assert by_text["pandas >=2.3.3"].comments == (
        "# more restrictive for python >=3.14",
    )
    assert by_text["pyarrow >=22.0.0"].comments == (
        "# more restrictive for python >=3.14",
    )
    assert by_text["aiohttp >=3.14.0,<4"].comments == ()


def test_a_multi_output_recipe_resolves_every_output_name() -> None:
    path = AIRFLOW / "providers-common-sql_2.1.0" / "recipe.yaml"
    recipe = read_recipe(path.read_text(encoding="utf-8"), str(path))
    assert [output.name for output in recipe.outputs] == [
        "apache-airflow-providers-common-sql",
        "apache-airflow-providers-common-sql-with-openlineage",
        "apache-airflow-providers-common-sql-with-pandas",
        "apache-airflow-providers-common-sql-with-polars",
    ]
    assert [output.index for output in recipe.outputs] == [0, 1, 2, 3]


def test_embedded_extra_markers_are_read_as_comments_and_a_trailer() -> None:
    """The `# end` marker is what a position-based model loses."""
    path = AIRFLOW / "providers-common-sql_2.1.0" / "recipe.yaml"
    recipe = read_recipe(path.read_text(encoding="utf-8"), str(path))
    run = recipe.outputs[2].blocks["run"].content
    by_text = {requirement.text: requirement for requirement in run.requirements}
    assert by_text["sqlalchemy >=2.0.36"].comments == ("# start pandas[sql-other]",)
    assert run.trailing_comments == ("# end pandas[sql-other]",)


def test_a_split_feedstock_names_both_outputs() -> None:
    path = GOOGLE / "google-cloud-bigquery" / "recipe.yaml"
    recipe = read_recipe(path.read_text(encoding="utf-8"), str(path))
    assert [output.name for output in recipe.outputs] == [
        "google-cloud-bigquery",
        "google-cloud-bigquery-core",
    ]
    assert recipe.blocks["/outputs/1/requirements/run"].section == "run"


def test_context_is_read() -> None:
    path = GOOGLE / "google-cloud-bigquery" / "recipe.yaml"
    recipe = read_recipe(path.read_text(encoding="utf-8"), str(path))
    assert recipe.context["name"] == "google-cloud-bigquery"
    assert recipe.context["version"] == "3.43.0"


def test_pin_subpackage_is_kept_verbatim() -> None:
    """swage does not interpret requirements at this layer, it carries them."""
    path = AIRFLOW / "providers-common-sql_2.1.0" / "recipe.yaml"
    recipe = read_recipe(path.read_text(encoding="utf-8"), str(path))
    texts = recipe.outputs[1].blocks["run"].content.texts()
    assert "${{ pin_subpackage(name, exact=True) }}" in texts


SINGLE = """\
package:
  name: demo
requirements:
  run:
    - python
"""


def test_a_flow_style_list_is_refused() -> None:
    """It parses fine, but there is no line range to splice."""
    with pytest.raises(RecipeError, match="one plain requirement per line"):
        read_recipe("requirements:\n  run: [python, pandas]\n")


def test_a_quoted_requirement_is_refused() -> None:
    """Read text and parsed value disagree, so swage would rewrite it wrongly."""
    with pytest.raises(RecipeError, match="cannot rewrite safely"):
        read_recipe("requirements:\n  run:\n    - 'python >=3.10'\n")


def test_an_inline_comment_on_a_requirement_is_refused() -> None:
    with pytest.raises(RecipeError, match="cannot rewrite safely"):
        read_recipe("requirements:\n  run:\n    - python  # why\n")


def test_a_requirements_section_that_is_not_a_list_is_refused() -> None:
    with pytest.raises(RecipeError, match="is not a list"):
        read_recipe("requirements:\n  run:\n    python: yes\n")


def test_an_empty_section_is_skipped_rather_than_guessed_at() -> None:
    recipe = read_recipe("requirements:\n  run:\n  host:\n    - python\n")
    assert set(recipe.outputs[0].blocks) == {"host"}


def test_invalid_yaml_is_an_error() -> None:
    with pytest.raises(RecipeError, match="invalid YAML"):
        read_recipe("requirements:\n  run:\n   - a\n  - b\n")


def test_a_non_mapping_document_is_an_error() -> None:
    with pytest.raises(RecipeError, match="mapping at the top level"):
        read_recipe("- just\n- a\n- list\n")


def test_run_exports_is_left_alone() -> None:
    """A packaging decision, not a dependency swage reconciles."""
    recipe = read_recipe(
        "requirements:\n  run:\n    - python\n  run_exports:\n    - demo\n"
    )
    assert set(recipe.outputs[0].blocks) == {"run"}


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        ("${{ name }}", "Demo-Thing"),
        ("${{ name|lower }}", "demo-thing"),
        ("${{ name }}-core", "Demo-Thing-core"),
        ("${{ name }}-with-${{ extra }}", "Demo-Thing-with-pandas"),
        ("literal", "literal"),
        # Unresolved is None rather than a half-substituted string, so a caller
        # cannot mistake "swage does not know the name" for the name.
        ("${{ unknown }}", None),
        ("${{ name[0] }}", None),
        ("${{ name }}-${{ unknown }}", None),
    ],
)
def test_resolve_expression(expr: str, expected: str | None) -> None:
    context = {"name": "Demo-Thing", "extra": "pandas"}
    assert resolve_expression(expr, context) == expected
