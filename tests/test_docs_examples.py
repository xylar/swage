"""Every YAML example in `docs/` is config the loader would accept.

The documentation exists because swage names a config key in the message that
holds a feedstock, and a maintainer arrives at the page holding that sentence.
An example that does not load sends them away with a file that stops
`swage config` -- which is worse than no example, because they have no reason
to doubt it.

This is not hypothetical. The stub `swage draft` printed for `run_constraints`
offered a list where the schema holds a mapping, and it was found by writing
the documentation rather than by any test.

Blocks are validated against a model built from every field of every config
model, with each made optional, so a fragment showing one key is checked as
strictly as a whole file: unknown keys are refused, and the nested models still
run their own validators -- `extras_as_outputs` without its required `suffix`
fails here exactly as it would on load.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

import pytest
import yaml
from pydantic import BaseModel, ConfigDict, TypeAdapter, create_model

from swage.config import Defaults, Family, Feedstock, Quirks

DOCS = Path(__file__).resolve().parent.parent / "docs"

#: A fenced block, with the info string that says what language it is.
_FENCE = re.compile(r"^```(\w*)\n(.*?)^```", re.MULTILINE | re.DOTALL)

#: The two global maps are bare name-to-name mappings rather than quirks
#: documents, so a block quoting one is validated the way the loader reads it.
_NAME_MAPS = ("config/name-map.yaml", "config/link-map.yaml")

_NAME_MAP_ADAPTER = TypeAdapter(dict[str, str])

_FIELDS = {
    **Defaults.model_fields,
    **Quirks.model_fields,
    **Family.model_fields,
    **Feedstock.model_fields,
}

#: Every config key, all optional, so a documented fragment validates as itself.
_OPTIONAL: dict[str, Any] = {
    name: (Optional[field.annotation], None)  # noqa: UP045 -- runtime annotation
    for name, field in _FIELDS.items()
}

Fragment: type[BaseModel] = create_model(
    "Fragment",
    __config__=ConfigDict(extra="forbid"),
    **_OPTIONAL,
)


def _blocks() -> list[tuple[str, str]]:
    """Every YAML example in the documentation, with the page it is on."""
    found = []
    for page in sorted(DOCS.rglob("*.md")):
        for language, body in _FENCE.findall(page.read_text(encoding="utf-8")):
            if language == "yaml":
                found.append((str(page.relative_to(DOCS)), body))
    return found


def _validate(data: Any) -> None:
    if set(data) <= set(Quirks.model_fields):
        # Quirks itself where it can be, since it carries validators of its own
        # -- an `embedded_extras` key that names no extra is caught only here.
        Quirks.model_validate(data)
    else:
        Fragment.model_validate(data)


@pytest.mark.parametrize(("page", "block"), _blocks())
def test_a_documented_example_is_config_the_loader_accepts(
    page: str, block: str
) -> None:
    data = yaml.safe_load(block)
    assert isinstance(data, dict), f"{page}: example is not a YAML mapping"
    if any(name in block.splitlines()[0] for name in _NAME_MAPS):
        _NAME_MAP_ADAPTER.validate_python(data)
        return
    _validate(data)


def test_the_documentation_has_examples_to_check() -> None:
    """A regex that matched nothing would make every test above vacuous."""
    assert len(_blocks()) > 10
