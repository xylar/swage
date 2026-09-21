"""The one recipe shape swage refuses before planning starts (v1 §3.3.5).

One output that builds both an arch and a noarch package, switched by a variable
the feedstock invents, holds two alternatives of the same dependency in one
list. Build variants in general are not refused.
"""

from __future__ import annotations

import re
from typing import Any

import yaml

from .errors import PlanError
from .prose import fenced, output_phrase

#: Written once, because it is fenced and appears in a message.
_BUILD = fenced("build")

__all__ = ["check_preconditions"]


def check_preconditions(recipe_text: str, source: str = "recipe.yaml") -> None:
    """Raise `PlanError` where swage must not plan this recipe at all."""
    try:
        document = yaml.safe_load(recipe_text)
    except yaml.YAMLError as exc:
        _refuse_v0_in_disguise(recipe_text, source)
        raise PlanError(f"{source}: invalid YAML: {exc}") from exc
    if not isinstance(document, dict):
        raise PlanError(f"{source}: is not a mapping")

    _refuse_conditional_noarch(document, source)


#: A v0 recipe's Jinja, which is not YAML and never parses as it.
_V0_JINJA = re.compile(r"^\s*\{%\s*(set|if|for)\b", re.MULTILINE)


def _refuse_v0_in_disguise(recipe_text: str, source: str) -> None:
    """A v0 recipe living in a file named `recipe.yaml`.

    Routing by filename cannot see it (v1 §3.1), and "invalid YAML" would send
    the maintainer looking for a syntax error.
    """
    if _V0_JINJA.search(recipe_text):
        raise PlanError(
            f"{source}: this is a v0 recipe despite the v1 filename\n"
            "  it opens with Jinja that is not YAML, so it cannot be parsed as "
            "a v1 recipe\n"
            "  finish the conversion, or rename it to meta.yaml so swage "
            "reports it as needing migration"
        )


def _refuse_conditional_noarch(document: dict[str, Any], source: str) -> None:
    """A `noarch` that is chosen rather than stated means two packages in one
    (v1 §3.3.5).
    """
    for where, build in _build_sections(document):
        if not isinstance(build, dict):
            # An `if:`/`then:` list in place of the build mapping can put
            # `noarch` on one branch and not the other.
            raise PlanError(
                f"{where} states its {_BUILD} section as a condition\n"
                "  swage cannot tell whether this output builds a noarch "
                "package, an architecture-specific one, or both\n"
                "  update this feedstock by hand"
            )
        if "noarch" not in build:
            continue
        noarch = build["noarch"]
        if isinstance(noarch, str) and "${{" in noarch:
            raise PlanError(
                f"{where} chooses whether it is noarch rather than stating it\n"
                f"    noarch: {noarch}\n"
                "  so one output builds both an architecture-specific and a "
                "noarch package, with different requirements\n"
                "  swage keeps one list of requirements per output and would "
                "collapse those into a single wrong answer -- update this "
                "feedstock by hand"
            )
        if not isinstance(noarch, str | bool):
            raise PlanError(
                f"{where} states its noarch as {noarch!r}, which swage cannot "
                "read\n"
                "  swage keeps one list of requirements per output -- update "
                "this feedstock by hand"
            )


def _build_sections(document: dict[str, Any]) -> list[tuple[str, Any]]:
    """Every `build` section, with the package whose section it is, read out of
    the raw document because this runs before the recipe is.
    """
    found: list[tuple[str, Any]] = []
    if "build" in document:
        found.append((output_phrase(), document["build"]))
    outputs = document.get("outputs")
    if isinstance(outputs, list):
        for index, output in enumerate(outputs):
            if isinstance(output, dict) and "build" in output:
                found.append(
                    (output_phrase(_output_name(output), index), output["build"])
                )
    return found


def _output_name(output: dict[str, Any]) -> str:
    """What this output builds, or what it stages, or nothing."""
    for key in ("package", "staging"):
        node = output.get(key)
        if isinstance(node, dict):
            name = node.get("name")
            if isinstance(name, str):
                return name
    return ""
