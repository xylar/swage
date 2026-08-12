"""Reading GitHub and upstream archives (DESIGN.md 3.5)."""

from __future__ import annotations

from .archive import Fetcher, download, parse_archive, read_archive
from .discover import (
    BOT_AUTHORS,
    BotPullRequest,
    discover_feedstocks,
    newest,
    open_bot_pull_requests,
)
from .errors import ForgeError
from .github import GitHub, Runner, run_gh
from .upstream import fetch_upstream, sole_source

__all__ = [
    "BOT_AUTHORS",
    "BotPullRequest",
    "Fetcher",
    "ForgeError",
    "GitHub",
    "Runner",
    "discover_feedstocks",
    "download",
    "fetch_upstream",
    "newest",
    "open_bot_pull_requests",
    "parse_archive",
    "read_archive",
    "run_gh",
    "sole_source",
]
