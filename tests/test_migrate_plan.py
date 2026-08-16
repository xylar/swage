"""Both halves of one feedstock's migration, read and not written.

The fixtures are the same four real recipes `test_migrate_convert` uses, so
what is tested here is the joining rather than the conversion: which files are
read, in what order, what happens when one of them is missing, and that
nothing is written anywhere.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Sequence
from pathlib import Path

import pytest
import yaml

from swage.forge import CONDA_FORGE_YML, GitHub, NotFound
from swage.forge.feedstock import RECIPE_V0, RECIPE_V1
from swage.migrate import MigrationError, plan_migration

CORPUS = Path(__file__).resolve().parent / "corpus" / "v0"

FORGE_YML = """\
conda_forge_output_validation: true
github:
  branch_name: main
"""


def meta_yaml(feedstock: str) -> str:
    return (CORPUS / feedstock / "meta.yaml").read_text(encoding="utf-8")


class FakeGitHub:
    """Answers contents reads from a mapping of path -> text."""

    def __init__(self, **files: str) -> None:
        self.files = files
        self.reads: list[str] = []

    def __call__(self, argv: Sequence[str]) -> str:
        path = next(part for part in argv if part.startswith("repos/"))
        wanted = path.split("/contents/", 1)[1]
        self.reads.append(wanted)
        if wanted not in self.files:
            raise NotFound(f"{wanted}: not found")
        content = base64.b64encode(self.files[wanted].encode()).decode()
        return json.dumps({"encoding": "base64", "content": content})


def github_for(**files: str) -> tuple[GitHub, FakeGitHub]:
    fake = FakeGitHub(**files)
    return GitHub(run=fake), fake


def test_a_v0_feedstock_yields_both_files() -> None:
    github, _ = github_for(
        **{RECIPE_V0: meta_yaml("calver"), CONDA_FORGE_YML: FORGE_YML}
    )

    migration = plan_migration(github, "calver")

    assert set(migration.files) == {RECIPE_V1, CONDA_FORGE_YML}
    assert "schema_version: 1" in migration.files[RECIPE_V1]
    assert yaml.safe_load(migration.files[CONDA_FORGE_YML])["conda_build_tool"] == (
        "rattler-build"
    )
    assert migration.forge_config_added == (
        "conda_build_tool",
        "conda_install_tool",
    )


def test_a_feedstock_already_v1_says_so_rather_than_failing_obscurely() -> None:
    """`--migrate` runs over whatever it is pointed at.

    A family part-way through conversion is the ordinary case, so the one
    thing this must not do is report a missing file as a broken feedstock.
    """
    github, _ = github_for(**{CONDA_FORGE_YML: FORGE_YML})

    with pytest.raises(MigrationError) as raised:
        plan_migration(github, "calver")

    assert "already v1" in str(raised.value)


def test_a_refused_conversion_never_asks_for_the_second_file() -> None:
    """One request saved on every feedstock nothing will be written to.

    Six of the fleet's 148 are refused before conversion starts, and reading
    `conda-forge.yml` for them would be a request spent on a file whose
    contents cannot matter.
    """
    github, fake = github_for(
        **{RECIPE_V0: meta_yaml("libspatialite"), CONDA_FORGE_YML: FORGE_YML}
    )

    with pytest.raises(MigrationError):
        plan_migration(github, "libspatialite")

    assert fake.reads == [RECIPE_V0]


def test_a_feedstock_with_no_conda_forge_yml_still_converts() -> None:
    """conda-smithy puts one in every feedstock, so this is a malformed one.

    Refusing here would stop a conversion over the half of it that has no
    judgement in it at all, so the settings are simply made from nothing.
    """
    github, _ = github_for(**{RECIPE_V0: meta_yaml("calver")})

    migration = plan_migration(github, "calver")

    assert migration.files[CONDA_FORGE_YML] == (
        "conda_build_tool: rattler-build\nconda_install_tool: pixi\n"
    )


def test_a_conversion_swage_cannot_read_stops_before_the_second_file() -> None:
    """The verification step, reached through the whole path.

    `apache-airflow-providers-common-sql` converts into a file that is not
    valid YAML while the converter reports nothing wrong.
    """
    github, fake = github_for(
        **{
            RECIPE_V0: meta_yaml("apache-airflow-providers-common-sql"),
            CONDA_FORGE_YML: FORGE_YML,
        }
    )

    with pytest.raises(MigrationError) as raised:
        plan_migration(github, "apache-airflow-providers-common-sql")

    assert "not one swage can read" in str(raised.value)
    assert fake.reads == [RECIPE_V0]


def test_the_ref_is_carried_through_to_what_is_read() -> None:
    github, _ = github_for(
        **{RECIPE_V0: meta_yaml("calver"), CONDA_FORGE_YML: FORGE_YML}
    )

    migration = plan_migration(github, "calver", ref="1.2.x")

    assert migration.ref == "1.2.x"
