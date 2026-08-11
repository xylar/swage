"""Config errors that point at a file and a line."""

from __future__ import annotations

from pathlib import Path

__all__ = ["ConfigError"]


class ConfigError(Exception):
    """A quirks-database file is malformed, inconsistent, or missing.

    Carries the offending file and, where the problem is a specific key, its
    line -- a typo in a quirk file should read like a compiler error, not like
    a stack trace (DESIGN.md 4).
    """

    def __init__(self, path: Path, message: str, line: int | None = None) -> None:
        self.path = path
        self.message = message
        self.line = line
        location = str(path) if line is None else f"{path}:{line}"
        super().__init__(f"{location}: {message}")
