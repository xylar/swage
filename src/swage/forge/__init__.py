"""Reading GitHub and upstream archives (DESIGN.md 3.5)."""

from __future__ import annotations

from .archive import Fetcher, download, parse_archive, read_archive
from .errors import ForgeError
from .github import GitHub, Runner, run_gh
from .upstream import fetch_upstream, sole_source

__all__ = [
    "Fetcher",
    "ForgeError",
    "GitHub",
    "Runner",
    "download",
    "fetch_upstream",
    "parse_archive",
    "read_archive",
    "run_gh",
    "sole_source",
]
