"""Reading GitHub and upstream archives (DESIGN.md 3.5)."""

from __future__ import annotations

from .archive import Fetcher, download, parse_archive, read_archive
from .checks import (
    CONDA_FORGE_YML,
    CheckState,
    CiStatus,
    Reader,
    ignored_statuses,
    read_at,
    required_checks,
    resolve_states,
    verify_ci,
)
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
from .pulls import AUTOMERGE, arm_automerge
from .repo import CLONES, CO_AUTHOR, COMMIT_SUBJECT, Git, Pushed, commit_message
from .upstream import fetch_upstream, sole_source, upstream_location

__all__ = [
    "AUTOMERGE",
    "BOT_AUTHORS",
    "CHANNELDATA_URL",
    "CLONES",
    "COMMIT_SUBJECT",
    "CONDA_FORGE_YML",
    "CO_AUTHOR",
    "GRAYSKULL_SOURCE",
    "GRAYSKULL_URL",
    "RECIPE_V1",
    "BotPullRequest",
    "CheckState",
    "CiStatus",
    "FeedstockFiles",
    "Fetcher",
    "ForgeError",
    "Git",
    "GitHub",
    "NotFound",
    "Pushed",
    "Reader",
    "Runner",
    "arm_automerge",
    "build_resolver",
    "commit_message",
    "discover_feedstocks",
    "download",
    "fetch_upstream",
    "ignored_statuses",
    "load_grayskull_layer",
    "load_package_index",
    "newest",
    "open_bot_pull_requests",
    "parse_archive",
    "previous_version",
    "read_archive",
    "read_at",
    "read_ci_support",
    "read_feedstock",
    "required_checks",
    "resolve_states",
    "run_gh",
    "sole_source",
    "upstream_location",
    "verify_ci",
]
