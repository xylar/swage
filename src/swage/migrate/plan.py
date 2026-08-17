"""One feedstock's whole migration, read but not written (DESIGN.md 7).

Both halves of a conversion in one place: the recipe, converted and proved
readable, and `conda-forge.yml`, told to build it with rattler-build. Neither
is written anywhere -- what this produces is the two files a caller would
commit, so `swage migrate` can report a conversion without performing one and
`swage update --migrate` can push the same bytes it showed.

**A feedstock that is already v1 is not an error.** `swage update --migrate`
runs over whatever it is pointed at, and a family part-way through conversion
is the ordinary case rather than a mistake worth stopping for.
"""

from __future__ import annotations

from dataclasses import dataclass

from swage.forge import CONDA_FORGE_YML, GitHub, NotFound
from swage.forge.feedstock import RECIPE_V0, RECIPE_V1
from swage.recipe import Recipe

from .convert import convert_recipe
from .errors import MigrationError
from .forge_config import set_build_tools
from .review import Review

__all__ = ["Migration", "plan_migration"]


@dataclass(frozen=True)
class Migration:
    """What converting one feedstock would write, and what to read first."""

    feedstock: str
    ref: str
    #: The converted `recipe/recipe.yaml`, already read back by swage.
    recipe_text: str
    #: That reading, so a caller planning dependencies against the conversion
    #: does not parse it a second time.
    recipe: Recipe
    #: The new `conda-forge.yml`, identical to the old where it already said
    #: what a v1 feedstock has to say.
    forge_config_text: str
    #: Which settings that added, empty where it needed none.
    forge_config_added: tuple[str, ...]
    #: What the converter could not carry over, and what swage found it had
    #: got wrong. Every one is something the person reviewing this has to look
    #: at -- and somebody always does, a migration being capped at proposing
    #: however its gates come out (DESIGN.md 7).
    concerns: tuple[str, ...]
    #: Everything else the converter said, kept and not worth reading.
    notes: tuple[str, ...]
    #: What became of each condition the v0 recipe stated (`review`) -- the
    #: review a compiled conversion needs, its diff being the whole file.
    review: Review

    @property
    def files(self) -> dict[str, str]:
        """Path to text, for a caller about to commit them."""
        return {RECIPE_V1: self.recipe_text, CONDA_FORGE_YML: self.forge_config_text}


def plan_migration(github: GitHub, feedstock: str, ref: str) -> Migration:
    """Convert ``feedstock`` at ``ref``, without writing anything.

    **``ref`` has no default**, because every other command that reads a
    feedstock without a pull request to hand it one asks `default_branch`
    (DESIGN.md 8.2), and a `migrate` that assumed `main` instead would be the
    one command answering "which ref?" differently from the rest. Every
    conda-forge feedstock worth converting is on `main`, so this buys
    consistency rather than coverage, at one request per feedstock.

    Raises `MigrationError` where the feedstock has no v0 recipe to convert,
    where the converter refuses it, or where what it produced is not something
    swage can read.
    """
    repo = f"conda-forge/{feedstock}-feedstock"
    try:
        meta_yaml = github.file(repo, RECIPE_V0, ref)
    except NotFound as exc:
        raise MigrationError(
            f"{feedstock}: has no {RECIPE_V0} at {ref}, so there is nothing to "
            "convert -- this feedstock is already v1"
        ) from exc

    converted = convert_recipe(meta_yaml, feedstock)

    # Read after the conversion rather than before it. A feedstock the
    # converter refuses is one nothing will be written to, and asking for a
    # second file first would spend a request on every one of those.
    try:
        forge_config = github.file(repo, CONDA_FORGE_YML, ref)
    except NotFound:
        # conda-smithy puts one in every feedstock, so this is a feedstock
        # shaped wrongly rather than one making a choice. Treated as empty:
        # the two settings still have to be made, and refusing here would stop
        # a conversion over the easier half of it.
        forge_config = ""
    edit = set_build_tools(forge_config, feedstock)

    return Migration(
        feedstock=feedstock,
        ref=ref,
        recipe_text=converted.text,
        recipe=converted.recipe,
        forge_config_text=edit.text,
        forge_config_added=edit.added,
        concerns=converted.concerns,
        notes=converted.notes,
        review=converted.review,
    )
