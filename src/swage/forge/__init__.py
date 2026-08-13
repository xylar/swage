"""Reading GitHub and upstream archives (DESIGN.md 3.5)."""

from __future__ import annotations

from .archive import Fetcher, download, parse_archive, read_archive
from .discover import (
    BOT_AUTHORS,
    BotPullRequest,
    discover_feedstocks,
    newest,
    open_bot_pull_requests,
    previous_version,
)
from .errors import ForgeError, NotFound
from .feedstock import RECIPE_V1, FeedstockFiles, read_ci_support, read_feedstock
from .github import GitHub, Runner, run_gh
from .index import (
    CHANNELDATA_URL,
    GRAYSKULL_SOURCE,
    GRAYSKULL_URL,
    build_resolver,
    load_grayskull_layer,
    load_package_index,
)
from .repo import CO_AUTHOR, COMMIT_SUBJECT, Git, Pushed, commit_message
from .upstream import fetch_upstream, sole_source, upstream_location

__all__ = [
    "BOT_AUTHORS",
    "CHANNELDATA_URL",
    "COMMIT_SUBJECT",
    "CO_AUTHOR",
    "GRAYSKULL_SOURCE",
    "GRAYSKULL_URL",
    "RECIPE_V1",
    "BotPullRequest",
    "FeedstockFiles",
    "Fetcher",
    "ForgeError",
    "Git",
    "GitHub",
    "NotFound",
    "Pushed",
    "Runner",
    "build_resolver",
    "commit_message",
    "discover_feedstocks",
    "download",
    "fetch_upstream",
    "load_grayskull_layer",
    "load_package_index",
    "newest",
    "open_bot_pull_requests",
    "parse_archive",
    "previous_version",
    "read_archive",
    "read_ci_support",
    "read_feedstock",
    "run_gh",
    "sole_source",
    "upstream_location",
]
