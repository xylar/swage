"""The two `conda-forge.yml` settings a v0 -> v1 migration must make (DESIGN.md 7).

**This is the one place swage touches `conda-forge.yml` at all.** Everywhere
else the file is off-limits, because every other setting in it is a judgment
about how a feedstock is built and nothing swage reads can make that judgment
(§7). A converted recipe is the exception and not an optional one: the file
names `recipe.yaml`, and conda-forge builds it with `conda-build` unless told
otherwise, so a migration that converts the recipe and leaves this alone has
produced a feedstock that does not build.

The edit is made in the text rather than through a round-trip. Two keys are
added to a mapping and nothing else moves -- which makes the diff two lines
whatever the file's key order, and no reviewer has to check whether a
serializer reflowed something on the way past. None of the 159 `conda-forge.yml`
in the maintainer's checkouts carries a comment, so nothing is being preserved
here that a round-trip would have lost; it is the *unchanged* lines that are
worth guaranteeing.
"""

from __future__ import annotations

from dataclasses import dataclass

import yaml

from .errors import MigrationError

__all__ = ["SETTINGS", "ForgeConfigEdit", "set_build_tools"]

#: What a v1 feedstock has to say, and the only values the fleet uses. 51 of
#: the maintainer's 159 checkouts carry both, five carry `conda_build_tool`
#: alone, and 103 carry neither -- and no feedstock anywhere gives either key
#: a different value, which is why a different one is a refusal below rather
#: than a case to handle.
SETTINGS = {
    "conda_build_tool": "rattler-build",
    "conda_install_tool": "pixi",
}


@dataclass(frozen=True)
class ForgeConfigEdit:
    """What setting the build tools did, or would do."""

    #: The file's new text. Identical to the old where `added` is empty.
    text: str
    #: The keys this added, in the order written. Empty for a feedstock that
    #: was already saying both -- which is not an error and not a no-op worth
    #: reporting as a change.
    added: tuple[str, ...] = ()


def set_build_tools(conda_forge_yml: str, feedstock: str) -> ForgeConfigEdit:
    """Ensure `conda-forge.yml` names rattler-build and pixi.

    Raises `MigrationError` where the file is not a mapping, or where it
    already sets one of the two to something else. Overwriting that would be
    swage reversing a decision somebody made in the one file §7 otherwise
    keeps it out of, and no feedstock in the fleet has made it.
    """
    try:
        document = yaml.safe_load(conda_forge_yml)
    except yaml.YAMLError as exc:
        raise MigrationError(
            f"{feedstock}: conda-forge.yml is not valid YAML: {exc}"
        ) from exc
    if document is None:
        document = {}
    if not isinstance(document, dict):
        raise MigrationError(f"{feedstock}: conda-forge.yml is not a mapping")

    missing = []
    for key, value in SETTINGS.items():
        current = document.get(key)
        if current is None:
            missing.append(key)
        elif current != value:
            raise MigrationError(
                f"{feedstock}: conda-forge.yml already sets {key} to "
                f"{current!r}, and a migration would set it to {value!r}\n"
                "  swage does not overwrite this file's settings -- change it "
                "by hand, or convert this feedstock by hand"
            )

    if not missing:
        return ForgeConfigEdit(text=conda_forge_yml)

    # Appended rather than placed. These files are not sorted -- among the
    # checkouts `conda_build_tool` turns up first, third and last -- so there
    # is no position that would look more at home than the end, and appending
    # is the only one that cannot disturb a line it did not write.
    body = conda_forge_yml if conda_forge_yml.endswith("\n") else conda_forge_yml + "\n"
    if not conda_forge_yml.strip():
        body = ""
    added = "".join(f"{key}: {SETTINGS[key]}\n" for key in missing)
    return ForgeConfigEdit(text=body + added, added=tuple(missing))
