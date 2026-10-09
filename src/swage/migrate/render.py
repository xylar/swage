"""A converted recipe, rendered by rattler-build before anything is pushed.

swage's reader answers whether swage can plan against a conversion, not
whether conda-smithy can rerender it, and the two came apart on the first
conversions pushed: `azure-servicebus` #26 failed its rerender on a source URL
swage had resolved without complaint. Over the 59 v0 feedstocks still to
convert, 7 conversions swage read back cleanly would not render, for three
different reasons, and over the 125 older v0 recipes in the maintainer's
checkouts, 38. Each fix in `convert` answers one reason; this answers the ones
nobody has found yet.

`rattler-build build --render-only` parses the recipe, evaluates its Jinja and
selectors against a variant config and stops before solving, so it needs no
network and takes a few milliseconds. It is rendered once for each operating
system the feedstock builds on, against that platform's own `.ci_support`
file: what conda-smithy last rendered from the global pinning and the
recipe's `conda_build_config.yaml`, with the selectors already evaluated.

**This is narrower than a rerender.** conda-smithy renders every platform,
including ones the recipe skips, against the pinning itself. The pinning is
not something rattler-build reads -- its selectors are conda-smithy's to
evaluate -- so a conversion broken only on a platform the feedstock does not
build on still reaches the pull request. Every failure found so far broke
every platform alike.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from collections.abc import Sequence
from pathlib import Path

from .errors import MigrationError

__all__ = ["render_conversion"]

#: rattler-build reports a failure as `Error:`, a multiplication sign and
#: what failed, then `╰─▶ <why>` wrapped over indented lines, then the source
#: span as `╭─[path:line:col]`.
_WHAT = re.compile(r"^Error:\s*\u00d7\s*(.*)$")
_WHY = re.compile(r"^\s*╰─▶\s*(.*)$")
_WHERE = re.compile(r"╭─\[.*:(\d+):\d+\]")


def render_conversion(
    text: str, feedstock: str, configs: Sequence[tuple[str, str]]
) -> None:
    """Render ``text`` for each ``(subdir, variant config)`` in ``configs``,
    as `read_variant_configs` returns them, or raise `MigrationError` with
    what rattler-build said. With no config, it is rendered for linux-64 with
    none, which is all a feedstock conda-smithy never rendered has to offer.
    """
    with tempfile.TemporaryDirectory() as scratch:
        recipe = Path(scratch) / "recipe"
        recipe.mkdir()
        (recipe / "recipe.yaml").write_text(text, encoding="utf-8")
        argv = ["rattler-build", "build", "--render-only", "--recipe", str(recipe)]
        for target, variants in configs or (("linux-64", None),):
            options = ["--target-platform", target]
            if variants is not None:
                config = Path(scratch) / f"{target}.yaml"
                config.write_text(variants, encoding="utf-8")
                options += ["--variant-config", str(config)]
            completed = subprocess.run(
                [*argv, *options],
                capture_output=True,
                text=True,
                encoding="utf-8",
                env={**os.environ, "NO_COLOR": "1"},
                check=False,
            )
            if completed.returncode:
                reason = _reason(completed.stderr + completed.stdout)
                raise MigrationError(
                    f"{feedstock}: rattler-build cannot render the converted "
                    f"recipe for {target}\n"
                    f"    {reason}\n"
                    "  the conversion has not been written anywhere -- convert "
                    "this feedstock by hand",
                    summary=f"rattler-build cannot render the conversion: {reason}",
                )


def _reason(output: str) -> str:
    """rattler-build's error as one line: what failed, why, and where."""
    lines = output.splitlines()
    start = next((n for n, line in enumerate(lines) if _WHAT.match(line)), None)
    if start is None:
        return lines[-1].strip() if lines else "no output"
    what = _WHAT.match(lines[start])
    assert what is not None
    parts = [what.group(1).strip()]
    line = ""
    for following in lines[start + 1 :]:
        where = _WHERE.search(following)
        if where:
            line = f" (line {where.group(1)})"
            break
        why = _WHY.match(following)
        if why:
            parts.append(why.group(1).strip())
        elif len(parts) > 1 and following.strip():
            parts[-1] += f" {following.strip()}"
        elif not following.strip():
            break
    return ": ".join(parts) + line
