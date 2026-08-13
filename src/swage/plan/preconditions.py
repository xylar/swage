"""The one recipe shape swage refuses before planning starts (DESIGN.md 3.3.5).

A recipe that builds **both an architecture-specific and a noarch package out
of one output**, switched by a variable the feedstock invents for itself.
[`markupsafe`](https://github.com/conda-forge/markupsafe-feedstock) is the
example, and the reason is specific to it:

```yaml
build:
  noarch: python             # [use_noarch]
requirements:
  run:
    - python >={{ python_min }}  # [use_noarch]
    - python                     # [not use_noarch]
```

One `run` list holds two mutually exclusive alternatives of the same
dependency. swage keeps one list of requirements per output and has no way to
say "these are alternatives, pick by variant", so rewriting the list would
collapse them into a single wrong answer. That is the hazard, and it is about
`noarch` being *chosen* rather than *stated*.

**Build variants in general are not a reason to refuse anything.** A feedstock
that builds three mpi variants, or one artifact per Python, is an ordinary
conda-forge feedstock. Its variants differ in lines swage keeps verbatim
anyway -- compilers, `${{ mpi }}`, build strings -- and a v1 recipe says which
requirement belongs to which variant in `if:`/`then:` structure a reader can
see, rather than in the v0 selector comments that make `markupsafe`
unreadable.

swage did refuse a feedstock for defining a multi-valued key in
`recipe/conda_build_config.yaml` and mentioning it in the recipe. That caught
five feedstocks in the fleet, every one for `mpi` and none of them harmful,
while `libnetcdf`, `moab` and `libpnetcdf` built the same three mpi variants
off conda-forge's global pinning and passed. It selected for where a variant
happened to be written down.
"""

from __future__ import annotations

import re
from typing import Any

import yaml

from .errors import PlanError

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

    swage normally routes v0 by filename, before reading anything, precisely so
    that the most common condition in the fleet is reported as NEEDS MIGRATION
    rather than as a corrupt file (DESIGN.md 3.1). A feedstock part-way through
    conversion defeats that -- `apache-beam` had v0 Jinja under the v1 name --
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
    """A `noarch` that is chosen rather than stated means two packages in one.

    This catches `markupsafe` on its own, which is the whole of DESIGN.md
    3.3.5. A recipe that says `noarch: python`, says `noarch: generic`, or says
    nothing at all has settled the question, however many variants it then
    builds.
    """
    for where, build in _build_sections(document):
        if not isinstance(build, dict):
            # An `if:`/`then:` list in place of the build mapping can put
            # `noarch` on one branch and not the other, which is the same
            # claim by another spelling.
            raise PlanError(
                f"unsupported conditional build section at {where}\n"
                "  swage cannot tell whether this output builds a noarch "
                "package, an architecture-specific one, or both\n"
                "  update this feedstock by hand"
            )
        if "noarch" not in build:
            continue
        noarch = build["noarch"]
        if isinstance(noarch, str) and "${{" in noarch:
            raise PlanError(
                f"unsupported conditional noarch in {where}/noarch\n"
                f"    noarch: {noarch}\n"
                "  the recipe chooses whether this output is noarch rather "
                "than stating it, so one output builds both an "
                "architecture-specific and a noarch package, with different "
                "requirements\n"
                "  swage keeps one list of requirements per output and would "
                "collapse those into a single wrong answer -- update this "
                "feedstock by hand"
            )
        if not isinstance(noarch, str | bool):
            raise PlanError(
                f"unsupported conditional noarch in {where}: {noarch!r}\n"
                "  swage keeps one list of requirements per output -- update "
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
