"""Tests for maintaining a second source's version (DESIGN.md 3.6.4).

The shape under test is `airflow`'s and only `airflow`'s: a recipe building
several archives, one of them at a version of its own that the conda-forge bot
does not bump, and a sibling release pinning it exactly. The fixtures here are
that shape with the names changed, small enough to read.

**These are written for refusal.** swage authors a sha256 here rather than
checking one, which is the only place it does, so what matters is that every
way of reaching the wrong archive stops.
"""

from __future__ import annotations

import hashlib
import io
import tarfile

import pytest

from swage.config import ConfigTree, load_config
from swage.forge import ForgeError, correct_source_versions, fetch_upstream
from swage.recipe import read_recipe
from swage.upstream import RecipeUpstream

from .conftest import WriteTree

DEFAULTS = """
trust: propose
recipe_owned:
  functions: [pin_subpackage]
  names: [python, pip]
removals: review
dynamic_dependencies: review
test_matrix: auto
source_versions: never
"""


def sdist(name: str, version: str, dependencies: str = "") -> bytes:
    """A one-file sdist declaring ``name`` at ``version``."""
    pyproject = (
        "[project]\n"
        f'name = "{name}"\n'
        f'version = "{version}"\n'
        f"dependencies = [{dependencies}]\n"
    )
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        payload = pyproject.encode("utf-8")
        info = tarfile.TarInfo(f"{name}-{version}/pyproject.toml")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


#: The library, which pins the helper at 1.3.1.
LIB = sdist("demo", "3.3.1", '"demo-helper==1.3.1"')
#: The helper the recipe actually builds, a version behind. It declares a
#: dependency of its own because a release declaring none sends `fetch_upstream`
#: to the wheel on PyPI, which is a real behavior and not this test's subject.
OLD_HELPER = sdist("demo-helper", "1.3.0", '"attrs>=22"')
#: The one it should be building.
NEW_HELPER = sdist("demo-helper", "1.3.1", '"attrs>=22"')

URLS = {
    "https://example.invalid/demo-3.3.1.tar.gz": LIB,
    "https://example.invalid/demo-helper-1.3.0.tar.gz": OLD_HELPER,
    "https://example.invalid/demo-helper-1.3.1.tar.gz": NEW_HELPER,
}

RECIPE = f"""\
schema_version: 1

context:
  version: "3.3.1"
  helper_version: "1.3.0"

recipe:
  name: demo-split
  version: ${{{{ version }}}}

source:
  - url: https://example.invalid/demo-${{{{ version }}}}.tar.gz
    sha256: {hashlib.sha256(LIB).hexdigest()}
    target_directory: demo
  - url: https://example.invalid/demo-helper-${{{{ helper_version }}}}.tar.gz
    sha256: {hashlib.sha256(OLD_HELPER).hexdigest()}
    target_directory: helper

outputs:
  - package:
      name: demo
    build:
      noarch: python
    requirements:
      run:
        - python >=3.10
        - demo-helper ==${{{{ helper_version }}}}
  - package:
      name: demo-helper
      version: ${{{{ helper_version }}}}
    build:
      noarch: python
    requirements:
      run:
        - python >=3.10
"""


def fetcher(urls: dict[str, bytes] | None = None):  # type: ignore[no-untyped-def]
    table = URLS if urls is None else urls

    def fetch(url: str, timeout: float = 60.0) -> bytes:
        if url not in table:
            raise ForgeError(f"nothing at {url}")
        return table[url]

    return fetch


@pytest.fixture
def tree(write_tree: WriteTree) -> ConfigTree:
    root = write_tree(
        {
            "defaults.yaml": DEFAULTS,
            "feedstocks/demo.yaml": "feedstock: demo\nsource_versions: auto\n",
        }
    )
    return load_config(root)


def correct(tree: ConfigTree, recipe_text: str = RECIPE, **rest):  # type: ignore[no-untyped-def]
    recipe = read_recipe(recipe_text)
    config = tree.for_feedstock("demo")
    upstream = fetch_upstream(recipe, config, fetch=fetcher(**rest))
    return correct_source_versions(recipe, upstream, config, fetch=fetcher(**rest))


def test_the_second_source_moves_to_what_the_first_requires(tree: ConfigTree) -> None:
    """The whole point: the bot bumps one version and swage bumps the other."""
    text, edits = correct(tree)

    assert [edit.summary for edit in edits] == [
        "demo-helper 1.3.0 to 1.3.1, which demo requires"
    ]
    assert 'helper_version: "1.3.1"' in text
    assert hashlib.sha256(NEW_HELPER).hexdigest() in text
    assert hashlib.sha256(OLD_HELPER).hexdigest() not in text


def test_the_recipes_own_version_is_left_alone(tree: ConfigTree) -> None:
    """`version` is the bot's, and it drives every other output.

    Nothing here may touch it, which is why `_naming_variable` excludes it by
    name as well as by the shared-reference rule.
    """
    text, _ = correct(tree)
    assert 'version: "3.3.1"' in text
    assert hashlib.sha256(LIB).hexdigest() in text


def test_only_the_two_lines_change(tree: ConfigTree) -> None:
    """The edit swage makes outside a requirements block is exactly two lines."""
    text, _ = correct(tree)
    changed = [
        (before, after)
        for before, after in zip(RECIPE.splitlines(), text.splitlines(), strict=True)
        if before != after
    ]
    assert len(changed) == 2
    assert all("helper" in before or "sha256" in before for before, _ in changed)


def test_a_feedstock_that_did_not_opt_in_is_untouched(write_tree: WriteTree) -> None:
    """`source_versions` is off everywhere but where somebody turned it on."""
    root = write_tree(
        {"defaults.yaml": DEFAULTS, "feedstocks/demo.yaml": "feedstock: demo\n"}
    )
    assert load_config(root).for_feedstock("demo").source_versions == "never"


def test_a_single_source_recipe_has_nothing_to_correct(tree: ConfigTree) -> None:
    """There is no sibling to read the answer from, so there is no answer."""
    helper_source = "\n".join(
        (
            "  - url: https://example.invalid/demo-helper-${{ helper_version }}.tar.gz",
            f"    sha256: {hashlib.sha256(OLD_HELPER).hexdigest()}",
            "    target_directory: helper",
            "",
        )
    )
    recipe = read_recipe(RECIPE.replace(helper_source, ""))
    config = tree.for_feedstock("demo")
    text, edits = correct_source_versions(
        recipe,
        RecipeUpstream.of(fetch_upstream(recipe, config, fetch=fetcher()).primary),
        config,
        fetch=fetcher(),
    )
    assert edits == ()
    assert text == recipe.text


def test_a_range_is_not_an_instruction_to_move(tree: ConfigTree) -> None:
    """`>=1.3.0,<2` says what will work, not what to build."""
    lib = sdist("demo", "3.3.1", '"demo-helper>=1.3.0,<2"')
    urls = dict(URLS, **{"https://example.invalid/demo-3.3.1.tar.gz": lib})
    recipe_text = RECIPE.replace(
        hashlib.sha256(LIB).hexdigest(), hashlib.sha256(lib).hexdigest()
    )
    _, edits = correct(tree, recipe_text, urls=urls)
    assert edits == ()


def test_an_archive_declaring_another_project_is_refused(tree: ConfigTree) -> None:
    """The check that stands in for the hash swage is not verifying.

    A URL built from a template and a version can reach the wrong archive, and
    the metadata inside is the only thing that can say so.
    """
    urls = dict(
        URLS,
        **{
            "https://example.invalid/demo-helper-1.3.1.tar.gz": sdist(
                "other", "1.3.1", '"attrs>=22"'
            )
        },
    )
    with pytest.raises(ForgeError, match="declares itself to be other"):
        correct(tree, urls=urls)


def test_an_archive_at_another_version_is_refused(tree: ConfigTree) -> None:
    """A project serving its latest release at a versioned URL, say."""
    urls = dict(
        URLS,
        **{
            "https://example.invalid/demo-helper-1.3.1.tar.gz": sdist(
                "demo-helper", "1.4.0", '"attrs>=22"'
            )
        },
    )
    with pytest.raises(ForgeError, match=r"declares version 1\.4\.0"):
        correct(tree, urls=urls)


def test_two_releases_asking_for_different_versions_is_refused(
    tree: ConfigTree,
) -> None:
    """Nothing decides between them, so swage decides nothing."""
    lib = sdist("demo", "3.3.1", '"demo-helper==1.3.1"')
    third = sdist("demo-extra", "3.3.1", '"demo-helper==1.3.2"')
    recipe_text = RECIPE.replace(
        hashlib.sha256(LIB).hexdigest(), hashlib.sha256(lib).hexdigest()
    ).replace(
        "outputs:",
        f"""  - url: https://example.invalid/demo-extra-${{{{ version }}}}.tar.gz
    sha256: {hashlib.sha256(third).hexdigest()}
    target_directory: extra

outputs:""",
        1,
    )
    urls = dict(
        URLS,
        **{
            "https://example.invalid/demo-3.3.1.tar.gz": lib,
            "https://example.invalid/demo-extra-3.3.1.tar.gz": third,
        },
    )
    with pytest.raises(ForgeError, match="cannot all be built"):
        correct(tree, recipe_text, urls=urls)


def test_a_version_no_context_entry_holds_is_refused(tree: ConfigTree) -> None:
    """The url has to be built from an entry that *is* the version.

    Spelled out in the recipe, the helper's version is unreachable by any
    single `context` entry, so there is nothing for swage to move.
    """
    recipe_text = RECIPE.replace(
        "https://example.invalid/demo-helper-${{ helper_version }}.tar.gz",
        "https://example.invalid/demo-helper-1.3.0.tar.gz",
    )
    with pytest.raises(ForgeError, match="cannot tell which context entry"):
        correct(tree, recipe_text)


def test_the_template_the_recipe_wrote_survives_the_correction(
    tree: ConfigTree,
) -> None:
    """The other half of maintaining the entry (DESIGN.md 3.6.4).

    The recipe writes the helper's version once and reads it three times: the
    source url, the built package's version, and this requirement. Rendering
    `demo-helper ==1.3.1` there would be correct and would still replace a
    maintainer's single point of truth with copies of a number -- in a recipe
    swage does not own, on every feedstock that uses the idiom.
    """
    from swage.config import MappingLayer
    from swage.forge import build_resolver
    from swage.mapping import StaticPackageIndex
    from swage.plan import plan_recipe, planned_blocks, planned_matrices
    from swage.plan.python_min import PythonMin
    from swage.recipe import render_recipe

    text, edits = correct(tree)
    assert edits, "the fixture is meant to need a correction"

    recipe = read_recipe(text)
    config = tree.for_feedstock("demo")
    upstream = fetch_upstream(recipe, config, fetch=fetcher())
    plan = plan_recipe(
        recipe,
        upstream,
        config,
        build_resolver(
            config,
            StaticPackageIndex.of("demo-helper", "attrs"),
            MappingLayer("grayskull pypi mapping", {}),
        ),
        PythonMin("3.10", ".ci_support/linux_64_.yaml"),
    )
    rendered = render_recipe(recipe, planned_blocks(plan), planned_matrices(plan))

    assert "- demo-helper ==${{ helper_version }}" in rendered
    assert "- demo-helper ==1.3.1" not in rendered


def test_a_template_written_with_a_space_survives_too(tree: ConfigTree) -> None:
    """`== ${{ version }}` is as ordinary a spelling as `==${{ version }}`.

    Comparing the rendered forms directly missed it: the first resolves to
    `demo-helper == 1.3.1` against a planned `demo-helper ==1.3.1`. `airflow`
    showed the gap by having one such line preserved and an identical one
    flattened in the same recipe.
    """
    from swage.config import MappingLayer
    from swage.forge import build_resolver
    from swage.mapping import StaticPackageIndex
    from swage.plan import plan_recipe, planned_blocks, planned_matrices
    from swage.plan.python_min import PythonMin
    from swage.recipe import render_recipe

    spaced = RECIPE.replace(
        "- demo-helper ==${{ helper_version }}",
        "- demo-helper == ${{ helper_version }}",
    )
    text, edits = correct(tree, spaced)
    assert edits, "the fixture is meant to need a correction"

    recipe = read_recipe(text)
    config = tree.for_feedstock("demo")
    plan = plan_recipe(
        recipe,
        fetch_upstream(recipe, config, fetch=fetcher()),
        config,
        build_resolver(
            config,
            StaticPackageIndex.of("demo-helper", "attrs"),
            MappingLayer("grayskull pypi mapping", {}),
        ),
        PythonMin("3.10", ".ci_support/linux_64_.yaml"),
    )
    rendered = render_recipe(recipe, planned_blocks(plan), planned_matrices(plan))

    assert "- demo-helper == ${{ helper_version }}" in rendered
    assert "- demo-helper ==1.3.1" not in rendered
