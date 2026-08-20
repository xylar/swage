"""Whether a converted recipe's `about.license` is a valid SPDX expression.

**A v1 recipe's license is one SPDX *expression*, not a list.** `MIT AND
Apache-2.0` stays exactly that -- a single scalar joining two identifiers with
an operator -- and every v1 recipe in conda-forge that needs two licenses
writes it that way: `airflow` says
`MIT AND BSD-3-Clause AND BSD-2-Clause AND Apache-2.0` in one line. Splitting
such a license into several entries would be inventing a shape the schema does
not have.

**The converter never loses one.** Across the maintainer's 121 convertible v0
recipes the license comes through byte-identical 120 times, and the one
exception is a correction it reported: `BSD 3-Clause` to `BSD-3-Clause`. So
its "Could not patch unrecognized license" is not a warning that something was
dropped -- it is conda-recipe-manager saying its own table did not recognize
the string, having left it exactly as written.

That message is therefore useless as a signal on its own: it fires on
`MIT AND Apache-2.0`, which is impeccable, and on `Apache Software`, which is
not a license identifier at all. swage answers the question the message only
gestures at, by reading the license out of the converted recipe and checking
it -- the artifact rather than the commentary, which is this module's whole
reason to exist.
"""

from __future__ import annotations

import re

import yaml
from conda_recipe_manager.licenses.spdx_utils import SpdxUtils

__all__ = ["license_problems", "spdx_problems"]

#: SPDX's operators, which are **case-sensitive and upper-case**. A recipe
#: writing `PSF-2.0 and MIT` has not written an expression joining two
#: licenses; it has written one identifier that does not exist. Two feedstocks
#: do exactly that, so this is worth catching rather than assuming away.
_OPERATORS = frozenset({"AND", "OR", "WITH"})

#: SPDX's escape hatch for a license not on its list: `LicenseRef-<idstring>`,
#: where the idstring is letters, digits, dots and hyphens. conda-forge uses it
#: -- `pyspharm` ships `LicenseRef-spherepack` -- and CRM's table does not know
#: it, which is one more reason its verdict cannot be taken as the answer.
_LICENSE_REF = re.compile(
    r"^(?:DocumentRef-[A-Za-z0-9.\-]+:)?LicenseRef-[A-Za-z0-9.\-]+$"
)

_SPDX = SpdxUtils()


def spdx_problems(expression: str) -> str:
    """What is wrong with ``expression``, in one sentence, or empty if nothing.

    Empty for a valid expression, which is the common case: of the twelve
    licenses CRM could not patch, seven are valid compound expressions it
    simply cannot parse. Over every distinct license in the maintainer's
    checkouts this flags 8 of 35, and all 8 really are unusable.

    **One sentence per license rather than one per token**, because the tokens
    are rarely independent findings: `Apache Software` is not two unknown
    licenses, it is one license written in prose.

    Deliberately shallow. Operator precedence and whether the parentheses
    balance are not checked, because nothing swage does depends on the
    expression's *structure* -- only on whether each license named in it is one
    anybody can look up. A reader handed "`Apache Software` is not a license
    identifier" can act on it; one handed a parse tree cannot.

    **An exact match or nothing, and never a suggested correction.** CRM's
    lookup is a fuzzy one, so it answers for prose as readily as for an
    identifier: asked about `Apache 2.0` it offers `Apache-2.0` for the first
    word and `ZPL-2.0` for the second. Repeating that would be inventing a
    license for somebody. The cost is that a deprecated-but-real identifier --
    `GPL-2.0`, which SPDX renamed `GPL-2.0-only` -- is reported as
    unrecognized rather than as outdated. No recipe in the fleet writes one,
    and a maintainer sent to look at a license loses nothing by being told the
    wrong reason to look.
    """
    if not expression.strip():
        return ""
    tokens = expression.replace("(", " ").replace(")", " ").split()

    # Checked before anything else, because it turns every other token into
    # nonsense: SPDX's operators are upper-case, so `PSF-2.0 and MIT` is not
    # two licenses joined by `and` -- it is one identifier nobody can look up.
    # Three feedstocks write it that way.
    lowered = sorted(
        {
            token
            for token in tokens
            if token not in _OPERATORS and token.upper() in _OPERATORS
        }
    )
    if lowered:
        spelled = ", ".join(f"`{token}`" for token in lowered)
        return (
            f"`{expression}` joins licenses with {spelled}, and SPDX's "
            "operators are upper-case -- `AND`, `OR`, `WITH`"
        )

    unknown = []
    for previous, token in zip(["", *tokens], tokens, strict=False):
        # `WITH` takes a license on the left and an *exception* on the right,
        # and exceptions live in a different SPDX table than licenses do. So
        # `Apache-2.0 WITH LLVM-exception` is correct and looking the right
        # half up as a license finds nothing. No recipe in the fleet writes
        # one; the rule is here so that the first that does is not stopped by
        # swage for being right.
        if token in _OPERATORS or previous == "WITH" or _LICENSE_REF.match(token):
            continue
        # A trailing `+` means "or later" and is part of the grammar rather
        # than of the identifier: `GPL-2.0+` is `GPL-2.0` with it.
        identifier = token.removesuffix("+")
        if _SPDX.find_closest_license_match(identifier) != identifier:
            unknown.append(token)
    if unknown:
        spelled = ", ".join(f"`{token}`" for token in unknown)
        return f"`{expression}` is not a license expression SPDX recognizes: {spelled}"
    return ""


def license_problems(recipe_text: str) -> tuple[str, ...]:
    """What is wrong with the licenses in ``recipe_text``, said in sentences.

    Reads every `about.license` the recipe has -- the top-level one and one per
    output -- because a multi-output recipe can name a different license per
    package and `google-api-core` does.

    Silent where the recipe names no license at all. That is conda-forge's
    linter's business rather than swage's, and a conversion is not the moment
    to start enforcing a rule the recipe was already breaking.
    """
    try:
        document = yaml.safe_load(recipe_text)
    except yaml.YAMLError:
        # Unreadable YAML is caught by the reader with a better message than
        # anything this could add (DESIGN.md 7.1).
        return ()
    if not isinstance(document, dict):
        return ()

    return tuple(
        f"{where}: {problem}"
        for where, expression in _licenses(document)
        if (problem := spdx_problems(expression))
    )


def _licenses(document: dict[str, object]) -> list[tuple[str, str]]:
    """Every license in the recipe, with where it was found."""
    found = []
    about = document.get("about")
    if isinstance(about, dict) and isinstance(about.get("license"), str):
        found.append(("about.license", about["license"]))
    outputs = document.get("outputs")
    if isinstance(outputs, list):
        for index, output in enumerate(outputs):
            if not isinstance(output, dict):
                continue
            inner = output.get("about")
            if isinstance(inner, dict) and isinstance(inner.get("license"), str):
                found.append((f"outputs[{index}].about.license", inner["license"]))
    return found
