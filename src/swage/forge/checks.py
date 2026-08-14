"""Is this pull request's CI finished, and did it pass (DESIGN.md 5.2)?

The one question swage has to answer for itself. Everywhere else conda-forge
decides whether a pull request may merge and swage only decides whether its own
change is routine -- but where swage changes nothing there is no commit, so no
CI run, so nothing ever dispatches conda-forge's automerge job and only swage
can close that pull request (DESIGN.md 2.1). Deciding to merge means deciding
that CI is green, and that decision has to be made here.

**This is a port of conda-forge's own rules, not a second opinion about them.**
`_get_required_checks_and_statuses`, `_get_github_checks`, `_get_github_statuses`
and `_all_statuses_and_checks_ok` in
`conda_forge_webservices/github_actions_integration/automerge.py` are the
authority, and the parts that look arbitrary are the parts most worth copying
exactly: which files make a CI provider required, that a provider is matched to
a check by *substring*, and that a GitHub Actions suite containing a run called
`automerge` does not count as a passing build. Reading that file is how these
were learned; guessing at them would have produced something that merges pull
requests conda-forge would not have.

Two deliberate departures, both toward refusing:

- **Anything failing stops the merge, required or not.** conda-forge asks "did
  the required checks pass?"; swage also asks "is anything else broken?". A
  check nobody made required is still somebody's evidence that this build is
  wrong.
- **An empty required set is a refusal**, as it is for conda-forge -- if no
  provider can be identified there is nothing to have passed, and "all zero
  required checks passed" is the most dangerous sentence available.

Everything is read through the contents API rather than by cloning, because
conda-forge's version of this runs in a job per pull request and swage's runs
in a sweep (DESIGN.md 3.5).
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

#: Reads one path at one commit, answering None where the file is not there.
#: A callable rather than a repository and a ref, so that the rules below can
#: be run against a directory on disk -- which is what makes it possible to
#: check them against a port of conda-forge's own code over the whole fleet
#: instead of against a fixture written from the same reading of it.
Reader = Callable[[str], str | None]

#: What conda-forge requires of every feedstock, whatever its CI. `linter` is
#: the conda-forge-linter status, and it is the reason a feedstock with no CI
#: provider at all still has a non-empty required set.
LINTER = "linter"

#: `path -> the provider whose check becomes required`. conda-smithy writes a
#: file per provider it has configured, so the file is the evidence. Two of
#: them need more than their own existence and are handled below.
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
#: config is inert. The four lines are consecutive, which is the whole of how
#: conda-forge recognizes it.
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

#: The GitHub Actions run that *is* conda-forge's automerge job. A suite
#: holding it is that job reporting on itself rather than the build, so it
#: never counts as a passing build.
_AUTOMERGE_RUN = "automerge"


@dataclass(frozen=True)
class CheckState:
    """One thing CI said about a commit, and whether it is a pass.

    `state` is three-valued on purpose: True passed, False failed, and None
    *not finished* -- which is neither, and is the ordinary condition of a
    pull request the bot opened a minute ago.
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
    #: Why swage will not merge, empty where it would. A sentence that stands
    #: on its own: it reaches a terminal report and, once merging is enabled,
    #: a comment on somebody else's pull request.
    reason: str = ""
    #: True where the reason is only that CI has not finished. The difference
    #: decides whether a human is owed a look now or whether swage should
    #: simply come back later, so it is recorded rather than re-derived from
    #: the wording.
    pending: bool = False

    @property
    def verified(self) -> bool:
        """Whether every check swage asks about has finished and passed."""
        return not self.reason


def verify_ci(github: GitHub, pull: BotPullRequest) -> CiStatus:
    """Establish whether ``pull`` is green, mergeable, and swage's to merge.

    Every refusal is a `CiStatus` carrying its reason rather than an
    exception: "CI has not finished" is the ordinary answer for a fresh pull
    request and the commonest outcome of all, and a caller that had to tell an
    expected refusal from a broken read by catching it would get that wrong
    eventually. A read that genuinely fails still raises `ForgeError`.
    """
    if pull.draft:
        return CiStatus(reason="the pull request is a draft, so nothing may merge it")

    config = _conda_forge_yml(github, pull)
    required = required_checks(read_at(github, _ci_repo(pull), pull.head_sha), config)
    if not required:
        # conda-forge refuses here too, and for the better reason: with no
        # provider identified there is nothing that passing could mean.
        return CiStatus(
            reason=(
                "no CI provider could be identified for this feedstock, so "
                "there is no check swage could confirm passed"
            )
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
    """`bot.automerge_options.ignored_statuses` out of `conda-forge.yml`.

    A feedstock's own list of checks it has decided not to wait for, which
    swage honours because it is the maintainer's decision recorded in the
    maintainer's file. Read defensively: this is a file on somebody's
    feedstock rather than swage's own config.
    """
    bot = config.get("bot")
    options = bot.get("automerge_options") if isinstance(bot, Mapping) else None
    listed = options.get("ignored_statuses") if isinstance(options, Mapping) else None
    if not isinstance(listed, Sequence) or isinstance(listed, str):
        return ()
    return tuple(str(entry).lower() for entry in listed)


def required_checks(read: Reader, config: Mapping[str, Any]) -> tuple[str, ...]:
    """Which CI providers must pass before this feedstock may merge.

    conda-smithy writes a configuration file per provider it has set up, so
    the files at the pull request's head are the evidence -- there is no API
    that answers this. Two providers are configured by a file that outlives
    them being turned off, which is why those two are read rather than merely
    looked for.

    The `ignored_statuses` filter is conda-forge's, including the direction it
    compares in: a required name is dropped when it appears *inside* one of
    the ignored entries, so a feedstock ignoring `azure-pipelines` also drops
    `azure`.
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

    **The match is a substring**, because the names do not line up otherwise:
    the provider is `azure` and the status context conda-forge's Azure
    pipeline posts is `conda-forge/azure-pipelines`. That also means one
    provider can match several reports -- an Azure build per platform -- and a
    provider matching nothing at all is *not finished* rather than passed,
    which is what makes "CI has not started yet" refuse rather than merge.
    """
    resolved = []
    for name in required:
        found = [check for check in observed if name in check.name.lower()]
        resolved.append(CheckState(name, _combined(found)))
    return tuple(resolved)


def _combined(found: Sequence[CheckState]) -> bool | None:
    """One answer out of every report matching a provider.

    A failure anywhere wins, then not-yet-finished, then pass. conda-forge
    folds these with `and`, which is order-dependent where `None` is involved;
    the difference only ever shows up in the wording of a refusal, and this
    way a provider whose four builds are three passes and a failure reads as
    failed rather than as pending.
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

    # swage's own addition (DESIGN.md 5.2): a check nobody made required is
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
    """The last thing between green CI and a merge (DESIGN.md 5.2).

    Read last rather than first because GitHub computes `mergeable` lazily, on
    being asked -- so asking about a pull request swage was never going to
    merge is a background job started for nothing, several hundred times a
    sweep.
    """
    payload = github.api(f"repos/{pull.repo}/pulls/{pull.number}")
    if not isinstance(payload, Mapping):
        raise ForgeError(f"{pull.repo}#{pull.number}: pull request was not an object")
    if payload.get("merged"):
        return CiStatus(required, reason="the pull request has already been merged")
    mergeable = payload.get("mergeable")
    if mergeable is None:
        # GitHub answers null while it works the merge out, and starts the job
        # on being asked. Pending rather than a refusal: the next run gets an
        # answer, and nothing about this pull request is wrong.
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
    """Whether the feedstock has said not to wait for this check.

    Matched in either direction, because the two things being compared are
    written at different lengths: an entry is written to match a status
    *context* -- `conda-forge-linter` -- while the names swage compares
    against include the short provider names it derived itself. Requiring one
    direction would silently honour the list in half the places it appears.
    """
    lowered = name.lower()
    return any(entry in lowered or lowered in entry for entry in ignored)


def _ci_repo(pull: BotPullRequest) -> str:
    """Where the CI configuration is read from.

    The head repository, which is the bot's fork: the pull request's own
    commit is what CI ran on, and a feedstock that gained or lost a provider
    in this very pull request would otherwise be judged against the wrong set.
    conda-forge clones exactly this. Falling back to the feedstock is for the
    pull request whose fork has been deleted, where the commit survives only
    in the base repository.
    """
    return pull.head_repo or pull.repo


def _conda_forge_yml(github: GitHub, pull: BotPullRequest) -> Mapping[str, Any]:
    """The feedstock's own settings, read from the branch being merged into.

    From the base rather than from the head, deliberately and for the reason
    conda-forge gives: these are the maintainer's settings, and a fork can say
    anything it likes. Absent or unreadable is an empty mapping -- the only
    thing read out of it is a list of checks to ignore, and ignoring nothing
    is the strict direction.
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
    """Whether the GitHub Actions build is configured and switched on.

    conda-smithy leaves the workflow file in place when the provider is turned
    off and writes a disabled one, so the file existing proves nothing on its
    own.
    """
    text = read(_GITHUB_WORKFLOW)
    if text is None:
        return False
    present = {line.strip() for line in text.splitlines()}
    return not all(sentinel in present for sentinel in _GITHUB_DISABLED)


def _circle_active(read: Reader) -> bool:
    """Whether the Circle build is configured and switched on.

    Same shape as GitHub Actions and a different disabled marker: a `filters:`
    block ignoring every branch. The four sentinel lines are consecutive from
    the `filters:` line, which is what conda-forge's scan comes to.
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
    """Everything CI has said about the head commit, statuses and checks alike.

    Both, because conda-forge's providers are split across the two APIs and
    always have been: Azure and the linter post commit *statuses*, while
    GitHub Actions reports *check suites*. Read from the feedstock rather than
    from the fork, which is where CI posts for a pull request.
    """
    return _statuses(github, pull) + _check_suites(github, pull)


def _statuses(github: GitHub, pull: BotPullRequest) -> tuple[CheckState, ...]:
    """The latest commit status per context.

    GitHub keeps every status ever posted for a context, so a build that went
    red and was re-run has two -- and reading the wrong one would refuse a
    pull request that is green, or worse. The newest by `updated_at` is the
    current one.
    """
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
    """Whether one check suite counts as a pass.

    The GitHub Actions case is conda-forge's and is not obvious: a suite whose
    runs include one called `automerge` is the automerge job reporting on
    itself, and counting it would let a feedstock's automerge workflow stand in
    for the build that was supposed to have passed.
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
