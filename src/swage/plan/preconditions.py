"""Feedstocks swage refuses before planning starts (DESIGN.md 3.3.5).

A few feedstocks build both an arch-specific and a `noarch` package from one
recipe, switched by a variable the feedstock invents for itself.
[`markupsafe`](https://github.com/conda-forge/markupsafe-feedstock) is the
example:

```yaml
# recipe/conda_build_config.yaml
use_noarch:
  - true    # [linux64]
  - false
```

**This breaks the assumption every reconciliation rule rests on.** DESIGN.md
3.3.1 intersects constraints across the whole Python range *because there is
one artifact*. Here there are two, with genuinely different Python
requirements, and intersecting them produces a `run` section wrong for both.
Worse, the requirements sections hold mutually exclusive alternatives of the
*same* dependency, selected by something that is neither a platform nor a
Python version -- swage's model has one list per output and no way to say
"these two lines are alternatives, pick by variant". Rewriting the list would
silently collapse them into one.

So the feedstock is refused **by name, before planning starts**. This is a
feedstock-level precondition rather than a recipe-parsing one: the point is to
not start, so it is checked by whatever reads the feedstock's files rather than
discovered part-way through building a plan.

Supporting this properly would mean modelling a requirements section as several
variant-conditioned lists and producing a plan per variant -- a real change to
the core model, worth making only if enough feedstocks need it. Until then the
failure is loud and specific, which is the whole requirement.
"""

from __future__ import annotations

import re
from typing import Any

import yaml

from .errors import PlanError

__all__ = ["check_preconditions"]

#: Keys conda-smithy and rattler-build own, which a recipe using one of them in
#: a selector is not thereby inventing a build variant over.
_NOT_VARIANTS = frozenset(
    {
        "python",
        "python_min",
        "numpy",
        "channel_sources",
        "channel_targets",
        "target_platform",
        "zip_keys",
        "pin_run_as_build",
        "c_compiler",
        "cxx_compiler",
        "MACOSX_DEPLOYMENT_TARGET",
    }
)


def check_preconditions(
    recipe_text: str,
    conda_build_config: str | None = None,
    source: str = "recipe.yaml",
) -> None:
    """Raise `PlanError` where swage must not plan this feedstock at all."""
    try:
        document = yaml.safe_load(recipe_text)
    except yaml.YAMLError as exc:
        _refuse_v0_in_disguise(recipe_text, source)
        raise PlanError(f"{source}: invalid YAML: {exc}") from exc
    if not isinstance(document, dict):
        raise PlanError(f"{source}: is not a mapping")

    _refuse_conditional_noarch(document, source)
    _refuse_recipe_variants(recipe_text, conda_build_config)


#: A v0 recipe's Jinja, which is not YAML and never parses as it.
_V0_JINJA = re.compile(r"^\s*\{%\s*(set|if|for)\b", re.MULTILINE)


def _refuse_v0_in_disguise(recipe_text: str, source: str) -> None:
    """A v0 recipe living in a file named `recipe.yaml`.

    swage normally routes v0 by filename, before reading anything, precisely so
    that the most common condition in the fleet is reported as NEEDS MIGRATION
    rather than as a corrupt file (DESIGN.md 3.1). A feedstock part-way through
    conversion defeats that -- `apache-beam` has v0 Jinja under the v1 name --
    and the filename check cannot see it. Saying "invalid YAML" there would
    send the maintainer looking for a syntax error that is not the problem.
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
    """A `noarch` that is chosen rather than stated means two artifacts.

    Catches `markupsafe` on its own, which is why DESIGN.md 3.3.5 calls it the
    first of the two checks.
    """
    for where, build in _build_sections(document):
        if not isinstance(build, dict):
            # An `if:`/`then:` list in place of the build mapping is the same
            # claim -- something about this build is conditional.
            raise PlanError(
                f"unsupported conditional build section at {where}\n"
                "  swage reconciles one noarch artifact at a time and cannot "
                "tell which variant a requirement belongs to\n"
                "  update this feedstock by hand"
            )
        if "noarch" not in build:
            continue
        noarch = build["noarch"]
        if isinstance(noarch, str) and "${{" in noarch:
            raise PlanError(
                f"unsupported build-variant switch in {where}/noarch\n"
                f"    noarch: {noarch}\n"
                "  the recipe chooses whether to build noarch rather than "
                "stating it, so it produces more than one artifact with "
                "different requirements\n"
                "  swage reconciles one noarch artifact at a time and would "
                "collapse those into a single wrong answer -- update this "
                "feedstock by hand"
            )
        if not isinstance(noarch, str | bool):
            raise PlanError(
                f"unsupported conditional noarch in {where}: {noarch!r}\n"
                "  swage reconciles one noarch artifact at a time -- update "
                "this feedstock by hand"
            )


def _build_sections(document: dict[str, Any]) -> list[tuple[str, Any]]:
    found: list[tuple[str, Any]] = []
    if "build" in document:
        found.append(("/build", document["build"]))
    outputs = document.get("outputs")
    if isinstance(outputs, list):
        for index, output in enumerate(outputs):
            if isinstance(output, dict) and "build" in output:
                found.append((f"/outputs/{index}/build", output["build"]))
    return found


def _refuse_recipe_variants(recipe_text: str, conda_build_config: str | None) -> None:
    """A feedstock-local variant the recipe then branches on.

    `recipe/conda_build_config.yaml` legitimately pins things -- a `python_min`
    or a compiler -- without inventing a variant. What matters is a key the
    feedstock made up *and* refers to, which is the shape that multiplies the
    artifacts a single recipe produces.
    """
    if not conda_build_config:
        return
    try:
        document = yaml.safe_load(conda_build_config)
    except yaml.YAMLError as exc:
        raise PlanError(f"conda_build_config.yaml: invalid YAML: {exc}") from exc
    if not isinstance(document, dict):
        return

    for key, values in document.items():
        if key in _NOT_VARIANTS or not isinstance(values, list) or len(values) < 2:
            continue
        if re.search(rf"\b{re.escape(str(key))}\b", recipe_text):
            raise PlanError(
                f"unsupported build-variant switch: {key}\n"
                f"  recipe/conda_build_config.yaml defines {key} with "
                f"{len(values)} values and the recipe uses it, so one recipe "
                "builds several packages with different requirements\n"
                "  swage reconciles one noarch artifact at a time and would "
                "collapse those into a single wrong answer -- update this "
                "feedstock by hand"
            )
