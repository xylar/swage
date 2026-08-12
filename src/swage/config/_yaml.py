"""YAML loading that can map a pydantic error location back to a source line."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .errors import ConfigError

__all__ = ["load_yaml_document"]


class YamlDocument:
    """A parsed YAML file plus enough of its node tree to locate keys."""

    def __init__(self, path: Path, data: Any, root: yaml.Node | None) -> None:
        self.path = path
        self.data = data
        self._root = root

    def line_for(self, loc: tuple[int | str, ...]) -> int | None:
        """Line number of the deepest node reachable along ``loc``, 1-based.

        pydantic reports the path to the offending key; walking as far down
        that path as the document actually goes puts the error on the most
        specific line available.
        """
        node = self._root
        if node is None:
            return None
        line = node.start_mark.line + 1
        for key in loc:
            if isinstance(node, yaml.MappingNode) and isinstance(key, str):
                for key_node, value_node in node.value:
                    if key_node.value == key:
                        line = key_node.start_mark.line + 1
                        node = value_node
                        break
                else:
                    return line
            elif isinstance(node, yaml.SequenceNode) and isinstance(key, int):
                if key >= len(node.value):
                    return line
                node = node.value[key]
                line = node.start_mark.line + 1
            else:
                return line
        return line


def load_yaml_document(path: Path) -> YamlDocument:
    """Parse ``path`` as YAML, raising :class:`ConfigError` on bad syntax.

    The file is parsed twice -- once for values, once for node positions.
    Quirk files are small and this runs once per process.
    """
    text = path.read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(text)
        root = yaml.compose(text)
    except yaml.YAMLError as exc:
        line = None
        mark = getattr(exc, "problem_mark", None)
        if mark is not None:
            line = mark.line + 1
        raise ConfigError(path, f"invalid YAML: {exc}", line) from exc
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ConfigError(path, "expected a YAML mapping at the top level")
    return YamlDocument(path, data, root)
