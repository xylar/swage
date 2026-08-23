"""Tests for reading a feedstock at a commit (DESIGN.md 3.5).

The thing worth pinning is about absence: a missing `recipe/recipe.yaml` means
the feedstock is v0 and gets routed to migration rather than reported as
broken.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Sequence

import pytest

from swage.forge import (
    CiSupport,
    ForgeError,
    GitHub,
    read_ci_support,
    read_feedstock,
)

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


def test_reading_a_recipe_reads_nothing_else() -> None:
    """One file per feedstock, at several hundred feedstocks per sweep.

    `recipe/conda_build_config.yaml` used to be fetched here for a
    build-variant refusal that has since been narrowed to what the recipe
    itself says (DESIGN.md 3.3.5), so nothing reads it any more.
    """
    runner = FakeGitHub(
        **{
            "recipe/recipe.yaml": RECIPE,
            "recipe/conda_build_config.yaml": "mpi:\n  - nompi\n  - mpich\n",
        }
    )
    read_feedstock(GitHub(run=runner), "demo", "abc123")
    assert runner.reads == ["recipe/recipe.yaml"]


def test_reading_the_recipe_does_not_read_ci_support() -> None:
    """They are separate reads because a caller learns it needs the floor late.

    55 of the 60 noarch feedstocks in the maintainer's checkouts refer to
    `${{ python_min }}` without setting it, so the floor almost always comes
    from `.ci_support` -- but only the recipe can say so, and a flag on this
    call would mean reading the recipe twice to find out.
    """
    runner = FakeGitHub(
        **{"recipe/recipe.yaml": RECIPE, ".ci_support/linux_64_.yaml": "python_min:\n"}
    )
    read_feedstock(GitHub(run=runner), "demo", "abc123")
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
    found = read_ci_support(GitHub(run=runner), "demo", "abc123")
    assert len(found.files) == 1
    assert found.files[0][0] == "linux_64_.yaml"
    assert "python_min" in found.files[0][1]


def test_the_variant_names_say_which_pythons_are_built() -> None:
    """The matrix an architecture-specific output is built over.

    Nothing in the recipe states it, and conda-smithy writes `python_min` only
    for a feedstock that builds a noarch python package -- so for a compiled
    one the variant names are the whole answer. `cp314t` is the free-threaded
    build of 3.14 rather than a release of its own, and `migrations/` is not a
    variant at all.
    """
    runner = FakeGitHub(
        **{
            "recipe/recipe.yaml": RECIPE,
            ".ci_support/linux_64_python3.10.____cpython.yaml": "python_min: '3.10'\n",
            ".ci_support/linux_aarch64_python3.12.____cpython.yaml": "python:\n",
            ".ci_support/osx_arm64_python3.14.____cp314.yaml": "python:\n",
            ".ci_support/win_64_python3.14.____cp314t.yaml": "python:\n",
        }
    )

    found = read_ci_support(GitHub(run=runner), "demo", "abc123")

    assert found.pythons == (10, 12, 14)


def test_a_feedstock_conda_smithy_never_rendered_has_no_ci_support() -> None:
    """The planner stops rather than assuming a floor, and says so."""
    runner = FakeGitHub(**{"recipe/recipe.yaml": RECIPE})
    assert read_ci_support(GitHub(run=runner), "demo", "abc123") == CiSupport()


def test_an_ordinary_noarch_feedstock_renders_one_platform() -> None:
    """One artifact, and `linux_64` is the only thing conda-smithy renders.

    This is the whole fleet bar three, and it is the reading that makes more
    than one platform mean something.
    """
    runner = FakeGitHub(
        **{
            "recipe/recipe.yaml": RECIPE,
            ".ci_support/linux_64_.yaml": "python_min:\n- '3.10'\n",
        }
    )

    assert read_ci_support(GitHub(run=runner), "demo", "abc123").platforms == ("linux",)


def test_noarch_platforms_renders_one_variant_per_platform() -> None:
    """The fourth build model, as conda-smithy leaves it on disk.

    `colorlog` and `click` render exactly these two names and `poetry` adds
    `osx_64_.yaml`; the recipes say `if: win` and `if: unix` accordingly. The
    listing is read rather than `conda-forge.yml` because it is what was
    actually rendered from it.
    """
    runner = FakeGitHub(
        **{
            "recipe/recipe.yaml": RECIPE,
            ".ci_support/linux_64_.yaml": "python_min:\n- '3.10'\n",
            ".ci_support/win_64_.yaml": "python_min:\n- '3.10'\n",
        }
    )

    found = read_ci_support(GitHub(run=runner), "demo", "abc123")

    assert found.platforms == ("linux", "win")


def test_platforms_are_ordered_as_a_recipe_writes_them() -> None:
    """`osx` before `win`, which alphabetical order would not give."""
    runner = FakeGitHub(
        **{
            "recipe/recipe.yaml": RECIPE,
            ".ci_support/win_64_python3.12.____cpython.yaml": "python:\n",
            ".ci_support/osx_arm64_python3.12.____cpython.yaml": "python:\n",
            ".ci_support/linux_ppc64le_python3.12.____cpython.yaml": "python:\n",
        }
    )

    found = read_ci_support(GitHub(run=runner), "demo", "abc123")

    assert found.platforms == ("linux", "osx", "win")


def test_the_build_targets_carry_the_machine_as_well_as_the_platform() -> None:
    """The platform axis the planner reasons over, which `platforms` throws
    away: `osx-64` and `osx-arm64` are one platform and two targets, and a
    marker naming a machine can only be answered against the second.
    """
    runner = FakeGitHub(
        **{
            "recipe/recipe.yaml": RECIPE,
            ".ci_support/win_64_python3.12.____cpython.yaml": "python:\n",
            ".ci_support/osx_arm64_python3.12.____cpython.yaml": "python:\n",
            ".ci_support/osx_64_python3.12.____cpython.yaml": "python:\n",
            ".ci_support/linux_ppc64le_python3.12.____cpython.yaml": "python:\n",
        }
    )

    found = read_ci_support(GitHub(run=runner), "demo", "abc123")

    assert found.targets == ("linux-ppc64le", "osx-64", "osx-arm64", "win-64")
    assert found.platforms == ("linux", "osx", "win")


def test_a_noarch_feedstock_renders_one_target() -> None:
    """conda-smithy writes `linux_64_.yaml` with no variant key after it, so
    the machine has to be read off a name that ends right there."""
    runner = FakeGitHub(
        **{"recipe/recipe.yaml": RECIPE, ".ci_support/linux_64_.yaml": "python:\n"}
    )

    assert read_ci_support(GitHub(run=runner), "demo", "abc123").targets == (
        "linux-64",
    )


def test_the_ref_is_carried_through_to_every_read() -> None:
    runner = FakeGitHub(**{"recipe/recipe.yaml": RECIPE})
    read_feedstock(GitHub(run=runner), "demo", "4a2f1c8")
    assert runner.reads[0] == "recipe/recipe.yaml"
