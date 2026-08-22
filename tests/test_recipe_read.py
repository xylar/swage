"""Tests for the recipe reader (DESIGN.md 3.1).

The corpus does most of the work here: if the reader can take apart 27 real
recipes and say exactly which lines each requirements block occupies, the
splicing that swage's write path depends on has something solid underneath it.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from swage.recipe import (
    Conditional,
    Entry,
    RecipeError,
    Requirement,
    read_recipe,
    resolve_expression,
)

from .conftest import REPO_ROOT

CORPUS = REPO_ROOT / "tests" / "corpus"
AIRFLOW = CORPUS / "airflow-providers"
GOOGLE = CORPUS / "google-cloud"

RECIPES = sorted(CORPUS.rglob("*recipe.yaml"))


@pytest.mark.parametrize("path", RECIPES, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_every_corpus_recipe_reads(path: Path) -> None:
    recipe = read_recipe(path.read_text(encoding="utf-8"), str(path))
    assert recipe.outputs
    # Every recipe in the corpus declares dependencies somewhere.
    assert recipe.blocks


def written_items(entries: Sequence[Entry]) -> int:
    """List items these entries occupy, a conditional's branches included."""
    total = 0
    for entry in entries:
        total += 1
        if isinstance(entry, Conditional):
            # An inline `then: pywin32` is not a list item of its own.
            for branch, inline in (
                (entry.then, entry.then_inline),
                (entry.otherwise or (), entry.otherwise_inline),
            ):
                total += 0 if inline else written_items(branch)
    return total


@pytest.mark.parametrize("path", RECIPES, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_block_line_ranges_land_on_requirements(path: Path) -> None:
    """Each block's range must cover its own items and nothing else."""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    recipe = read_recipe(text, str(path))
    for block in recipe.blocks.values():
        body = lines[block.first_line : block.end_line]
        items = [line for line in body if line.strip().startswith("- ")]
        assert len(items) == written_items(block.content.entries)
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
    with pytest.raises(RecipeError, match="could be read from the source lines"):
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


FLUSH = """\
requirements:
  host:
  - python
  - pip
  run:
  - python
"""


def test_a_list_level_with_its_key_is_read() -> None:
    """`shelved-cache` writes its items at the indentation of `host:` itself."""
    recipe = read_recipe(FLUSH)
    blocks = recipe.outputs[0].blocks
    assert blocks["host"].content.texts() == ("python", "pip")
    assert blocks["run"].content.texts() == ("python",)


def test_a_flush_list_records_the_indentation_it_was_written_at() -> None:
    """The writer splices at this, so a flush section stays flush."""
    assert read_recipe(FLUSH).outputs[0].blocks["host"].item_indent == 2


def test_a_key_level_with_a_flush_list_ends_the_block() -> None:
    """What stops the body is the next line at that level that is not an item."""
    host = read_recipe(FLUSH).outputs[0].blocks["host"]
    assert (host.first_line, host.end_line) == (2, 4)


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
        # The forms a source URL adds: PyPI's first-letter path segment, and
        # the underscored sdist filename.
        ("${{ name[0] }}", "D"),
        ("${{ name|replace('-', '_') }}", "Demo_Thing"),
        ("${{ name | replace('-', '_') }}", "Demo_Thing"),
        ('${{ name | replace("-", "_") | lower }}', "demo_thing"),
        # Unresolved is None rather than a half-substituted string, so a caller
        # cannot mistake "swage does not know the name" for the name.
        ("${{ unknown }}", None),
        ("${{ name }}-${{ unknown }}", None),
        ("${{ name[99] }}", None),
        # Outside the set of forms the fleet uses, so None rather than a guess.
        ("${{ name.split('-') }}", None),
        ("${{ name | title }}", None),
        ("${{ name", None),
    ],
)
def test_resolve_expression(expr: str, expected: str | None) -> None:
    context = {"name": "Demo-Thing", "extra": "pandas"}
    assert resolve_expression(expr, context) == expected


def test_a_pypi_source_url_resolves_against_the_context() -> None:
    """The whole point: the recipe already names the archive and pins its hash."""
    recipe = read_recipe(
        "context:\n"
        "  name: google-cloud-bigquery\n"
        '  version: "3.43.0"\n'
        "source:\n"
        "  url: https://pypi.org/packages/source/${{ name[0] }}/${{ name }}/"
        "${{ name|replace('-', '_') }}-${{ version }}.tar.gz\n"
        "  sha256: e3dc25ab9ac8b2b089408493177d4d4508b098c80c3931786fbc20b075298fe6\n"
        "requirements:\n  run:\n    - python\n"
    )
    assert len(recipe.sources) == 1
    source = recipe.sources[0]
    assert source.url == (
        "https://pypi.org/packages/source/g/google-cloud-bigquery/"
        "google_cloud_bigquery-3.43.0.tar.gz"
    )
    assert source.sha256 == (
        "e3dc25ab9ac8b2b089408493177d4d4508b098c80c3931786fbc20b075298fe6"
    )
    assert source.target_directory is None


def test_several_sources_keep_their_order_and_target_directories() -> None:
    """`airflow-feedstock` builds from three sdists with two versions."""
    recipe = read_recipe(
        "context:\n"
        '  version: "3.3.0"\n'
        '  task_sdk_version: "1.3.0"\n'
        "source:\n"
        "  - url: https://example.invalid/apache_airflow-${{ version }}.tar.gz\n"
        "    sha256: aa\n"
        "    target_directory: airflow\n"
        "  - url: https://example.invalid/"
        "apache_airflow_task_sdk-${{ task_sdk_version }}.tar.gz\n"
        "    sha256: bb\n"
        "    target_directory: airflow-task-sdk\n"
        "requirements:\n  run:\n    - python\n"
    )
    assert [source.target_directory for source in recipe.sources] == [
        "airflow",
        "airflow-task-sdk",
    ]
    assert recipe.sources[1].url == (
        "https://example.invalid/apache_airflow_task_sdk-1.3.0.tar.gz"
    )


def test_a_url_the_context_cannot_resolve_is_none_but_keeps_its_expression() -> None:
    recipe = read_recipe(
        "source:\n"
        "  url: https://example.invalid/${{ mystery }}.tar.gz\n"
        "  sha256: aa\n"
        "requirements:\n  run:\n    - python\n"
    )
    assert recipe.sources[0].url is None
    assert recipe.sources[0].url_expr == "https://example.invalid/${{ mystery }}.tar.gz"


def test_a_source_that_is_not_a_url_still_holds_its_place() -> None:
    """Dropping it would shift every later source out from under its index."""
    recipe = read_recipe(
        "source:\n"
        "  - git: https://example.invalid/thing.git\n"
        "    rev: v1\n"
        "  - url: https://example.invalid/thing-1.tar.gz\n"
        "    sha256: aa\n"
        "requirements:\n  run:\n    - python\n"
    )
    assert len(recipe.sources) == 2
    assert recipe.sources[0].url_expr is None
    assert recipe.sources[1].url == "https://example.invalid/thing-1.tar.gz"


def test_a_recipe_with_no_source_has_none() -> None:
    recipe = read_recipe("requirements:\n  run:\n    - python\n")
    assert recipe.sources == ()


CONDITIONAL = """\
requirements:
  build:
    - if: build_platform != target_platform
      then:
        - python
        - cross-python_${{ target_platform }}
    - ${{ compiler('c') }}
  host:
    # needs the mpi library itself
    - if: mpi != "nompi"
      then: ${{ mpi }}
    - if: win
      then: m2-unzip
      else:
        - unzip
        - m4
"""


def branch_texts(entries: tuple[Entry, ...]) -> list[str]:
    """The plain requirements of a branch, so a test can assert on them."""
    return [entry.text for entry in entries if isinstance(entry, Requirement)]


def test_a_conditional_entry_is_read_as_structure() -> None:
    """The v1 grammar for "this requirement belongs to some builds only"."""
    recipe = read_recipe(CONDITIONAL)
    build = recipe.outputs[0].blocks["build"].content
    conditional = build.conditionals[0]
    assert conditional.condition == "build_platform != target_platform"
    assert branch_texts(conditional.then) == [
        "python",
        "cross-python_${{ target_platform }}",
    ]
    assert conditional.otherwise is None
    # The plain entry beside it is still a plain entry.
    assert build.texts() == ("${{ compiler('c') }}",)


def test_an_inline_branch_reads_the_same_as_a_list_one() -> None:
    host = read_recipe(CONDITIONAL).outputs[0].blocks["host"].content
    inline = host.conditionals[0]
    assert inline.then_inline is True
    assert branch_texts(inline.then) == ["${{ mpi }}"]
    assert inline.comments == ("# needs the mpi library itself",)


def test_an_else_branch_is_read() -> None:
    host = read_recipe(CONDITIONAL).outputs[0].blocks["host"].content
    both = host.conditionals[1]
    assert both.then_inline is True
    assert both.otherwise is not None
    assert branch_texts(both.otherwise) == ["unzip", "m4"]


def test_a_conditional_inside_a_branch_is_read() -> None:
    """`apache-beam` nests a python check inside its cross-compilation block."""
    recipe = read_recipe(
        "requirements:\n  build:\n"
        "    - if: build_platform != target_platform\n"
        "      then:\n"
        "        - cython\n"
        '        - if: python < "3.13"\n'
        "          then: grpcio-tools ==1.62.1\n"
    )
    outer = recipe.outputs[0].blocks["build"].content.conditionals[0]
    assert isinstance(outer.then[1], Conditional)
    assert outer.then[1].condition == 'python < "3.13"'


def test_requirements_reports_the_unconditional_entries_only() -> None:
    """A caller with nothing to say about a conditional must not see one.

    The planner is checked for conditionals separately and refuses while it
    cannot reconcile them, so this cannot quietly drop anything.
    """
    host = read_recipe(CONDITIONAL).outputs[0].blocks["host"].content
    assert host.requirements == ()
    assert len(host.entries) == 2


def test_an_if_with_no_condition_is_refused() -> None:
    with pytest.raises(RecipeError, match="`if:` with no condition"):
        read_recipe("requirements:\n  run:\n    - if:\n        win\n      then: a\n")


def test_a_conditional_with_no_then_is_refused() -> None:
    with pytest.raises(RecipeError, match="no `then:`"):
        read_recipe("requirements:\n  run:\n    - if: win\n      else: a\n")


def test_a_branch_with_both_a_value_and_a_list_is_refused() -> None:
    with pytest.raises(RecipeError, match="both a value and a list"):
        read_recipe(
            "requirements:\n  run:\n    - if: win\n      then: a\n        - b\n"
        )


def test_a_key_that_is_not_then_or_else_is_refused() -> None:
    with pytest.raises(RecipeError, match="neither `then:` nor `else:`"):
        read_recipe("requirements:\n  run:\n    - if: win\n      maybe: a\n")


# --- a context entry written in terms of another ---------------------------


def test_a_context_entry_is_resolved_against_the_ones_above_it() -> None:
    """rattler-build evaluates these top to bottom, and so must swage.

    `parallelio` derives the underscored version its GitHub tag needs from the
    version above it. Storing that unevaluated left `${{ ver_underscores }}` in
    the source URL resolving to a string that still contained `${{`, which
    `resolve_expression` refuses -- so the feedstock reported as having no URL
    with a sha256 rather than as one swage could not expand.
    """
    recipe = read_recipe(
        'context:\n  version: "2.6.9"\n'
        '  ver_underscores: ${{ version | replace(".", "_") }}\n'
        "source:\n"
        "  url: https://x.invalid/pio${{ ver_underscores }}.tar.gz\n"
        "  sha256: " + "0" * 64 + "\n"
        "requirements:\n  run:\n    - python\n"
    )
    assert recipe.context["ver_underscores"] == "2_6_9"
    assert recipe.sources[0].url == "https://x.invalid/pio2_6_9.tar.gz"


def test_an_entry_swage_cannot_evaluate_is_dropped_rather_than_kept_verbatim() -> None:
    """The variant axis, and the one place this must not resolve.

    Eight recipes in the fleet write `mpi: ${{ mpi or "nompi" }}`, where `mpi`
    is a build variant rather than context (DESIGN.md 3.3.4). There is no value
    to resolve it to, and inventing `nompi` would silently pick one build out
    of three. Keeping the text verbatim is the same answer by a longer route --
    anything referring to it was refused for still containing `${{`.
    """
    recipe = read_recipe(
        'context:\n  version: "1.0"\n  mpi: ${{ mpi or "nompi" }}\n'
        "requirements:\n  run:\n    - python\n"
    )
    assert "mpi" not in recipe.context
    assert recipe.context["version"] == "1.0"


def test_a_forward_reference_does_not_resolve() -> None:
    """Top to bottom, so an entry cannot be written in terms of a later one."""
    recipe = read_recipe(
        "context:\n  early: ${{ late }}\n  late: settled\n"
        "requirements:\n  run:\n    - python\n"
    )
    assert "early" not in recipe.context
    assert recipe.context["late"] == "settled"
