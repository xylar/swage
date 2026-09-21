"""Is this pull request's CI finished, and did it pass (v1 §5.2; DESIGN.md §10)?

A port of conda-forge's own `automerge.py` rules (docs/conda-forge.md), with two
departures toward refusing: anything failing stops the merge, required or not,
and an empty required set is a refusal. Everything is read through the contents
API.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import yaml

from .discover import BotPullRequest
from .errors import ForgeError, NotFound
from .github import GitHub

__all__ = [
    "CONDA_FORGE_YML",
    "CheckState",
    "CiStatus",
    "Reader",
    "ignored_statuses",
    "read_at",
    "required_checks",
    "resolve_states",
    "verify_ci",
]

CONDA_FORGE_YML = "conda-forge.yml"

#: Reads one path at one commit, answering None where the file is not there. A
#: callable, so the rules can be run against a directory on disk.
Reader = Callable[[str], str | None]

#: What conda-forge requires of every feedstock, whatever its CI.
LINTER = "linter"

#: `path -> the provider whose check becomes required`. Two of them need more
#: than their own existence and are handled below.
_PROVIDER_FILES = {
    "appveyor.yml": "appveyor",
    ".appveyor.yml": "appveyor",
    ".drone.yml": "drone",
    ".travis.yml": "travis",
    "azure-pipelines.yml": "azure",
}

_GITHUB_WORKFLOW = ".github/workflows/conda-build.yml"
_CIRCLE_CONFIG = ".circleci/config.yml"

#: conda-smithy writes a disabled GitHub Actions workflow rather than deleting
#: it, and these three lines together are what "disabled" looks like.
_GITHUB_DISABLED = ("name: Disabled build", "- run: exit 0", "if: false")

#: Circle is the other one conda-smithy leaves behind: the config exists even
#: where the provider is off. Either of these scripts means it is really on.
_CIRCLE_SCRIPTS = (
    ".circleci/checkout_merge_commit.sh",
    ".circleci/fast_finish_ci_pr_build.sh",
)

#: And failing those, a `filters:` block that ignores every branch means the
#: config is inert.
_CIRCLE_DISABLED = ("filters:", "branches:", "ignore:", "- /.*/")

#: A commit status in one of these is pending rather than decided; one in any
#: of the rest is a pass. conda-forge's own two lists, kept as its two lists.
_PENDING_STATES = frozenset({"pending"})
_BAD_STATES = frozenset(
    {
        # statuses
        "failure",
        "error",
        # check suites
        "action_required",
        "canceled",
        "timed_out",
        "failed",
        "neutral",
    }
)

#: The GitHub Actions run that is conda-forge's automerge job; a suite holding
#: it never counts as a passing build.
_AUTOMERGE_RUN = "automerge"


@dataclass(frozen=True)
class CheckState:
    """One thing CI said about a commit, and whether it is a pass. `state` is
    True passed, False failed, and None not finished.
    """

    name: str
    state: bool | None

    @property
    def word(self) -> str:
        """`passed`, `failed` or `pending`, for anything that prints this."""
        if self.state is None:
            return "pending"
        return "passed" if self.state else "failed"


@dataclass(frozen=True)
class CiStatus:
    """Whether swage may merge this pull request, and what it looked at."""

    #: One per required provider, in the order conda-forge requires them.
    required: tuple[CheckState, ...] = ()
    #: Why swage will not merge, empty where it would.
    reason: str = ""
    #: True where the reason is only that CI has not finished.
    pending: bool = False

    @property
    def verified(self) -> bool:
        """Whether every check swage asks about has finished and passed."""
        return not self.reason


def verify_ci(github: GitHub, pull: BotPullRequest) -> CiStatus:
    """Establish whether ``pull`` is green, mergeable, and swage's to merge.

    Every refusal is a `CiStatus` carrying its reason; a read that fails
    raises `ForgeError`.
    """
    if pull.draft:
        return CiStatus(reason="the pull request is a draft, so nothing may merge it")

    config = _conda_forge_yml(github, pull)
    required = required_checks(read_at(github, _ci_repo(pull), pull.head_sha), config)
    if not required:
        # conda-forge refuses here too, and for the better reason: with no
        # provider identified there is nothing that passing could mean.
        return CiStatus(
            reason="no CI provider identified, so nothing could confirm a pass"
        )

    observed = _observed(github, pull)
    states = resolve_states(required, observed)
    ignored = ignored_statuses(config)
    return _verdict(github, pull, states, observed, ignored)


def read_at(github: GitHub, repo: str, ref: str) -> Reader:
    """A `Reader` over one commit of one repository."""

    def read(path: str) -> str | None:
        try:
            return github.file(repo, path, ref)
        except NotFound:
            return None

    return read


def ignored_statuses(config: Mapping[str, Any]) -> tuple[str, ...]:
    """`bot.automerge_options.ignored_statuses` out of `conda-forge.yml`, read
    defensively.
    """
    bot = config.get("bot")
    options = bot.get("automerge_options") if isinstance(bot, Mapping) else None
    listed = options.get("ignored_statuses") if isinstance(options, Mapping) else None
    if not isinstance(listed, Sequence) or isinstance(listed, str):
        return ()
    return tuple(str(entry).lower() for entry in listed)


def required_checks(read: Reader, config: Mapping[str, Any]) -> tuple[str, ...]:
    """Which CI providers must pass before this feedstock may merge
    (docs/conda-forge.md).

    The files at the pull request's head are the evidence. The
    `ignored_statuses` filter compares in conda-forge's direction: a required
    name is dropped when it appears inside an ignored entry.
    """
    required = [LINTER]
    for path, provider in _PROVIDER_FILES.items():
        if read(path) is not None and provider not in required:
            required.append(provider)
    if _github_actions_active(read):
        required.append("github-actions")
    if _circle_active(read):
        required.append("circle")

    ignored = ignored_statuses(config)
    return tuple(
        name for name in required if not any(name in entry for entry in ignored)
    )


def resolve_states(
    required: Sequence[str], observed: Sequence[CheckState]
) -> tuple[CheckState, ...]:
    """Match each required provider to what CI actually reported.

    The match is a substring, so one provider can match several reports, and
    a provider matching nothing is not finished rather than passed.
    """
    resolved = []
    for name in required:
        found = [check for check in observed if name in check.name.lower()]
        resolved.append(CheckState(name, _combined(found)))
    return tuple(resolved)


def _combined(found: Sequence[CheckState]) -> bool | None:
    """One answer out of every report matching a provider: a failure anywhere,
    then not-yet-finished, then pass.
    """
    if not found:
        return None
    if any(check.state is False for check in found):
        return False
    if any(check.state is None for check in found):
        return None
    return True


def _verdict(
    github: GitHub,
    pull: BotPullRequest,
    states: Sequence[CheckState],
    observed: Sequence[CheckState],
    ignored: Sequence[str],
) -> CiStatus:
    """Everything swage checks, in the order that makes the reason useful."""
    required = tuple(states)
    failed = [check.name for check in states if check.state is False]
    if failed:
        return CiStatus(required, reason=f"CI failed: {', '.join(failed)}")

    # swage's own addition (design-v1.md 5.2): a check nobody made required is
    # still somebody's evidence that this build is wrong.
    broken = [
        check.name
        for check in observed
        if check.state is False and not _is_ignored(check.name, ignored)
    ]
    if broken:
        return CiStatus(
            required,
            reason=f"not required, but failing: {', '.join(sorted(set(broken)))}",
        )

    waiting = [check.name for check in states if check.state is None]
    if waiting:
        return CiStatus(
            required,
            reason=f"CI has not finished: {', '.join(waiting)}",
            pending=True,
        )

    return _mergeable(github, pull, required)


def _mergeable(
    github: GitHub, pull: BotPullRequest, required: tuple[CheckState, ...]
) -> CiStatus:
    """The last thing between green CI and a merge (v1 §5.2), read last because
    GitHub computes `mergeable` lazily.
    """
    payload = github.api(f"repos/{pull.repo}/pulls/{pull.number}")
    if not isinstance(payload, Mapping):
        raise ForgeError(f"{pull.repo}#{pull.number}: pull request was not an object")
    if payload.get("merged"):
        return CiStatus(required, reason="the pull request has already been merged")
    mergeable = payload.get("mergeable")
    if mergeable is None:
        # GitHub answers null while it works the merge out: pending, not a
        # refusal.
        return CiStatus(
            required,
            reason="GitHub has not yet worked out whether this merges cleanly",
            pending=True,
        )
    if not mergeable:
        return CiStatus(
            required,
            reason="the pull request does not merge cleanly and needs a rebase",
        )
    return CiStatus(required)


def _is_ignored(name: str, ignored: Sequence[str]) -> bool:
    """Whether the feedstock has said not to wait for this check, matched in
    either direction, since entries and derived names differ in length.
    """
    lowered = name.lower()
    return any(entry in lowered or lowered in entry for entry in ignored)


def _ci_repo(pull: BotPullRequest) -> str:
    """Where the CI configuration is read from: the head repository, which is
    what CI ran on, falling back to the feedstock where the fork is gone.
    """
    return pull.head_repo or pull.repo


def _conda_forge_yml(github: GitHub, pull: BotPullRequest) -> Mapping[str, Any]:
    """The feedstock's own settings, read from the base branch, as conda-forge
    does. Absent or unreadable is an empty mapping.
    """
    try:
        text = github.file(pull.repo, CONDA_FORGE_YML, pull.base_ref)
    except NotFound:
        return {}
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError:
        return {}
    return loaded if isinstance(loaded, Mapping) else {}


def _github_actions_active(read: Reader) -> bool:
    """Whether the GitHub Actions build is configured and switched on;
    conda-smithy leaves a disabled workflow file in place.
    """
    text = read(_GITHUB_WORKFLOW)
    if text is None:
        return False
    present = {line.strip() for line in text.splitlines()}
    return not all(sentinel in present for sentinel in _GITHUB_DISABLED)


def _circle_active(read: Reader) -> bool:
    """Whether the Circle build is configured and switched on; a `filters:`
    block ignoring every branch is the disabled marker.
    """
    if any(read(path) is not None for path in _CIRCLE_SCRIPTS):
        return True
    text = read(_CIRCLE_CONFIG)
    if text is None:
        return False
    lines = [line.strip() for line in text.splitlines()]
    for index, line in enumerate(lines):
        if line == _CIRCLE_DISABLED[0]:
            window = lines[index : index + len(_CIRCLE_DISABLED)]
            if window == list(_CIRCLE_DISABLED):
                return False
    return True


def _observed(github: GitHub, pull: BotPullRequest) -> tuple[CheckState, ...]:
    """Everything CI has said about the head commit, statuses and checks alike,
    read from the feedstock, where CI posts for a pull request.
    """
    return _statuses(github, pull) + _check_suites(github, pull)


def _statuses(github: GitHub, pull: BotPullRequest) -> tuple[CheckState, ...]:
    """The latest commit status per context, by `updated_at`."""
    payload = github.paginated(f"repos/{pull.repo}/commits/{pull.head_sha}/statuses")
    latest: dict[str, tuple[str, bool | None]] = {}
    for entry in payload:
        if not isinstance(entry, Mapping):
            continue
        context = str(entry.get("context", ""))
        stamp = str(entry.get("updated_at", ""))
        if context not in latest or stamp >= latest[context][0]:
            latest[context] = (stamp, _state(str(entry.get("state", ""))))
    return tuple(CheckState(context, state) for context, (_, state) in latest.items())


def _check_suites(github: GitHub, pull: BotPullRequest) -> tuple[CheckState, ...]:
    """One state per app that ran a check suite on the head commit."""
    payload = github.api(
        f"repos/{pull.repo}/commits/{pull.head_sha}/check-suites",
        {"per_page": "100"},
    )
    suites = payload.get("check_suites") if isinstance(payload, Mapping) else None
    if not isinstance(suites, Sequence):
        raise ForgeError(f"{pull.repo}@{pull.head_sha}: check suites were not a list")
    return tuple(
        CheckState(_app(suite), _suite_state(github, pull, suite))
        for suite in suites
        if isinstance(suite, Mapping)
    )


def _suite_state(
    github: GitHub, pull: BotPullRequest, suite: Mapping[str, Any]
) -> bool | None:
    """Whether one check suite counts as a pass; a suite holding the `automerge`
    run is that job reporting on itself.
    """
    if str(suite.get("status", "")) != "completed":
        return None
    passed = str(suite.get("conclusion", "")) == "success"
    if _app(suite) != "github-actions" or not passed:
        return passed
    runs = _run_names(github, pull, suite)
    return bool(runs) and not any(name == _AUTOMERGE_RUN for name in runs)


def _run_names(
    github: GitHub, pull: BotPullRequest, suite: Mapping[str, Any]
) -> tuple[str, ...]:
    identifier = suite.get("id")
    if identifier is None:
        return ()
    payload = github.api(f"repos/{pull.repo}/check-suites/{identifier}/check-runs")
    runs = payload.get("check_runs") if isinstance(payload, Mapping) else None
    if not isinstance(runs, Sequence):
        return ()
    return tuple(str(run.get("name", "")) for run in runs if isinstance(run, Mapping))


def _app(suite: Mapping[str, Any]) -> str:
    app = suite.get("app")
    return str(app.get("slug", "")) if isinstance(app, Mapping) else ""


def _state(state: str) -> bool | None:
    if state in _PENDING_STATES:
        return None
    return state not in _BAD_STATES
