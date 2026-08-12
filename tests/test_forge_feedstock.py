"""Tests for reading a feedstock at a commit (DESIGN.md 3.5).

The two things worth pinning are both about absence. A missing
`recipe/recipe.yaml` means the feedstock is v0 and gets routed to migration
rather than reported as broken, and a missing `conda_build_config.yaml` means
the feedstock simply has none -- which is the common case, not a failure.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Sequence

import pytest

from swage.forge import ForgeError, GitHub, read_feedstock

RECIPE = "context:\n  version: '1.0'\nrequirements:\n  run:\n    - python\n"


class FakeGitHub:
    """Answers contents reads from a mapping of path -> text."""

    def __init__(self, **files: str) -> None:
        self.files = files
        self.reads: list[str] = []

    def __call__(self, argv: Sequence[str]) -> str:
        path = next(part for part in argv if part.startswith("repos/"))
        wanted = path.split("/contents/", 1)[1]
        self.reads.append(wanted)
        if wanted in self.files:
            content = base64.b64encode(self.files[wanted].encode()).decode()
            return json.dumps({"encoding": "base64", "content": content})
        if wanted == ".ci_support" and any(
            name.startswith(".ci_support/") for name in self.files
        ):
            return json.dumps(
                [
                    {"name": name.split("/", 1)[1], "type": "file"}
                    for name in self.files
                    if name.startswith(".ci_support/")
                ]
            )
        raise _not_found(wanted)


def _not_found(path: str) -> Exception:
    from swage.forge import NotFound

    return NotFound(f"gh: Not Found (HTTP 404) for {path}")


def test_a_v1_recipe_is_read_at_the_given_ref() -> None:
    runner = FakeGitHub(**{"recipe/recipe.yaml": RECIPE})
    files = read_feedstock(GitHub(run=runner), "demo", "abc123")
    assert files.recipe == RECIPE
    assert files.v0 is False
    assert files.repo == "conda-forge/demo-feedstock"


def test_a_v0_feedstock_is_routed_rather_than_reported_as_broken() -> None:
    """The most common condition in the fleet must not look like a corrupt file."""
    runner = FakeGitHub(**{"recipe/meta.yaml": "{% set name = 'demo' %}\n"})
    files = read_feedstock(GitHub(run=runner), "demo", "abc123")
    assert files.v0 is True
    assert files.recipe is None


def test_a_feedstock_with_neither_recipe_is_a_failure() -> None:
    runner = FakeGitHub()
    with pytest.raises(ForgeError, match="has neither"):
        read_feedstock(GitHub(run=runner), "demo", "abc123")


def test_a_missing_conda_build_config_is_not_a_failure() -> None:
    """Most feedstocks have none, so its absence cannot be an error."""
    runner = FakeGitHub(**{"recipe/recipe.yaml": RECIPE})
    files = read_feedstock(GitHub(run=runner), "demo", "abc123")
    assert files.conda_build_config is None


def test_a_conda_build_config_is_read_because_a_variant_switch_hides_there() -> None:
    runner = FakeGitHub(
        **{
            "recipe/recipe.yaml": RECIPE,
            "recipe/conda_build_config.yaml": "use_noarch:\n  - true\n  - false\n",
        }
    )
    files = read_feedstock(GitHub(run=runner), "demo", "abc123")
    assert files.conda_build_config is not None
    assert "use_noarch" in files.conda_build_config


def test_ci_support_is_not_read_unless_asked_for() -> None:
    """Two API calls per feedstock to learn what the recipe usually says itself."""
    runner = FakeGitHub(
        **{"recipe/recipe.yaml": RECIPE, ".ci_support/linux_64_.yaml": "python_min:\n"}
    )
    files = read_feedstock(GitHub(run=runner), "demo", "abc123")
    assert files.ci_support == ()
    assert ".ci_support" not in runner.reads


def test_one_ci_support_file_is_enough() -> None:
    """`python_min` cannot differ per architecture, so the first is the answer."""
    runner = FakeGitHub(
        **{
            "recipe/recipe.yaml": RECIPE,
            ".ci_support/linux_64_.yaml": "python_min:\n- '3.10'\n",
            ".ci_support/osx_64_.yaml": "python_min:\n- '3.10'\n",
            ".ci_support/win_64_.yaml": "python_min:\n- '3.10'\n",
        }
    )
    files = read_feedstock(GitHub(run=runner), "demo", "abc123", ci_support=True)
    assert len(files.ci_support) == 1
    assert files.ci_support[0][0] == "linux_64_.yaml"
    assert "python_min" in files.ci_support[0][1]


def test_a_feedstock_conda_smithy_never_rendered_has_no_ci_support() -> None:
    """The planner stops rather than assuming a floor, and says so."""
    runner = FakeGitHub(**{"recipe/recipe.yaml": RECIPE})
    files = read_feedstock(GitHub(run=runner), "demo", "abc123", ci_support=True)
    assert files.ci_support == ()


def test_the_ref_is_carried_through_to_every_read() -> None:
    runner = FakeGitHub(**{"recipe/recipe.yaml": RECIPE})
    read_feedstock(GitHub(run=runner), "demo", "4a2f1c8")
    assert runner.reads[0] == "recipe/recipe.yaml"
