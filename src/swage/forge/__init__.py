"""Reading GitHub and upstream archives (DESIGN.md 3.5)."""

from __future__ import annotations

from .archive import Fetcher, caching, download, parse_archive, read_archive
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
    PullOutcome,
    discover_feedstocks,
    newest,
    open_bot_pull_requests,
    previous_version,
    read_pull_request,
)
from .errors import ForgeError, NotFound
from .feedstock import (
    RECIPE_V1,
    CiSupport,
    FeedstockFiles,
    default_branch,
    read_ci_support,
    read_feedstock,
)
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
from .repo import (
    CLONES,
    CO_AUTHOR,
    COMMIT_SUBJECT,
    CONVERSION_SUBJECT,
    Git,
    Pushed,
    commit_message,
    conversion_message,
)
from .source_versions import SourceVersionEdit, correct_source_versions
from .upstream import (
    archive_sources,
    fetch_upstream,
    fetch_upstream_texts,
    upstream_location,
)

__all__ = [
    "AUTOMERGE",
    "BOT_AUTHORS",
    "CHANNELDATA_URL",
    "CLONES",
    "COMMIT_SUBJECT",
    "CONDA_FORGE_YML",
    "CONVERSION_SUBJECT",
    "CO_AUTHOR",
    "GRAYSKULL_SOURCE",
    "GRAYSKULL_URL",
    "RECIPE_V1",
    "BotPullRequest",
    "CheckState",
    "CiStatus",
    "CiSupport",
    "FeedstockFiles",
    "Fetcher",
    "ForgeError",
    "Git",
    "GitHub",
    "NotFound",
    "PullOutcome",
    "Pushed",
    "Reader",
    "Runner",
    "SourceVersionEdit",
    "archive_sources",
    "arm_automerge",
    "build_resolver",
    "caching",
    "commit_message",
    "conversion_message",
    "correct_source_versions",
    "default_branch",
    "discover_feedstocks",
    "download",
    "fetch_upstream",
    "fetch_upstream_texts",
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
    "read_pull_request",
    "required_checks",
    "resolve_states",
    "run_gh",
    "upstream_location",
    "verify_ci",
]
