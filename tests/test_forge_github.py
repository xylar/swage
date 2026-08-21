"""Tests for the GitHub choke point (DESIGN.md 3.5).

No network and no `gh`: the runner is injected, which is the seam DESIGN.md 11
asks for. What matters here is which failures are retried and which are not --
retrying a 404 wastes fourteen seconds per feedstock across the fleet, and
*not* retrying a secondary rate limit aborts a run that would have succeeded.
"""

from __future__ import annotations

import base64
import json
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from swage.forge import ForgeError, GitHub, NotFound, ReadRecorder
from swage.forge.github import _TIMEOUT, run_gh


class FakeRunner:
    """Answers each call from a queued list, recording the argv it was given."""

    def __init__(self, *answers: str | ForgeError) -> None:
        self.answers = list(answers)
        self.calls: list[list[str]] = []

    def __call__(self, argv: Sequence[str]) -> str:
        self.calls.append(list(argv))
        answer = self.answers.pop(0)
        if isinstance(answer, ForgeError):
            raise answer
        return answer


class FakeClock:
    def __init__(self) -> None:
        self.slept: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.slept.append(seconds)


def contents_payload(text: str) -> str:
    return json.dumps(
        {
            "encoding": "base64",
            "content": base64.b64encode(text.encode("utf-8")).decode("ascii"),
        }
    )


def test_api_builds_a_get_with_query_parameters() -> None:
    runner = FakeRunner('{"tag_name": "v1"}')
    assert GitHub(run=runner).api("repos/a/b/releases", {"per_page": "1"}) == {
        "tag_name": "v1"
    }
    assert runner.calls == [
        [
            "gh",
            "api",
            "--method",
            "GET",
            "repos/a/b/releases",
            "-f",
            "per_page=1",
        ]
    ]


def test_file_decodes_the_contents_api_at_a_ref() -> None:
    runner = FakeRunner(contents_payload("[project]\nname = 'demo'\n"))
    text = GitHub(run=runner).file(
        "apache/airflow",
        "providers/apache/hive/pyproject.toml",
        "providers-apache-hive/9.6.1",
    )
    assert text == "[project]\nname = 'demo'\n"
    assert runner.calls[0][-2:] == ["-f", "ref=providers-apache-hive/9.6.1"]


@pytest.mark.parametrize(
    "message",
    [
        "gh: HTTP 502 Bad Gateway",
        "gh: HTTP 429 Too Many Requests",
        "You have exceeded a secondary rate limit",
        "API rate limit exceeded for user ID 1",
    ],
)
def test_a_transient_failure_is_retried_with_growing_delays(message: str) -> None:
    runner = FakeRunner(ForgeError(message), ForgeError(message), '{"ok": true}')
    clock = FakeClock()
    assert GitHub(run=runner, sleep=clock).api("rate_limit") == {"ok": True}
    assert clock.slept == [2.0, 4.0]


@pytest.mark.parametrize(
    "message",
    [
        "gh: HTTP 404 Not Found",
        "gh: HTTP 401 Bad credentials",
        "gh: HTTP 422 Unprocessable Entity",
    ],
)
def test_a_permanent_failure_is_not_retried(message: str) -> None:
    """A 404 per feedstock at four attempts each is a sweep nobody waits for."""
    runner = FakeRunner(ForgeError(message))
    clock = FakeClock()
    with pytest.raises(ForgeError, match="HTTP 4"):
        GitHub(run=runner, sleep=clock).api("repos/a/b/contents/nope")
    assert len(runner.calls) == 1
    assert clock.slept == []


def test_retries_give_up_and_raise_the_last_failure() -> None:
    runner = FakeRunner(*[ForgeError("gh: HTTP 503") for _ in range(4)])
    clock = FakeClock()
    with pytest.raises(ForgeError, match="503"):
        GitHub(run=runner, sleep=clock).api("rate_limit")
    assert len(runner.calls) == 4
    assert clock.slept == [2.0, 4.0, 8.0]


def test_a_file_over_the_contents_api_limit_is_named_not_read_as_empty() -> None:
    """An empty string here would arrive looking like metadata that says nothing."""
    runner = FakeRunner(json.dumps({"encoding": "none", "content": ""}))
    with pytest.raises(ForgeError, match="over 1 MB"):
        GitHub(run=runner).file("a/b", "big.toml", "main")


def test_a_directory_is_not_a_file() -> None:
    runner = FakeRunner(json.dumps([{"name": "pyproject.toml"}]))
    with pytest.raises(ForgeError, match="is a directory"):
        GitHub(run=runner).file("a/b", "providers", "main")


def test_a_non_json_answer_names_the_path() -> None:
    runner = FakeRunner("<html>proxy error</html>")
    with pytest.raises(ForgeError, match="did not answer with JSON"):
        GitHub(run=runner).api("repos/a/b")


def test_a_call_that_hangs_is_abandoned_rather_than_waited_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`gh` sets no deadline, so without one here a sweep hangs forever.

    Twice it did. A request whose connection died under a suspended laptop
    waits on a socket nobody will answer; swage waits on `gh`; the sweep stops
    dead without failing, retrying or reporting. One was found at 0.0% CPU two
    hours in, holding a child alive for 1h49m on a single contents request.

    The timeout is asserted as *passed to* `subprocess.run` rather than by
    sleeping through it, so this test costs nothing and still fails if the
    argument is ever dropped.
    """
    seen: dict[str, object] = {}

    def fake_run(argv: Sequence[str], **kwargs: object) -> object:
        seen.update(kwargs)
        raise subprocess.TimeoutExpired(cmd=list(argv), timeout=_TIMEOUT)

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(ForgeError, match="timed out after 300s") as caught:
        run_gh(["gh", "api", "--method", "GET", "repos/conda-forge/demo"])

    assert seen["timeout"] == _TIMEOUT
    # The argv is named, because a sweep's log is where this will be read.
    assert "repos/conda-forge/demo" in str(caught.value)


def test_a_timed_out_call_is_retried_on_a_fresh_connection() -> None:
    """The most retryable failure there is: the next attempt opens a socket.

    Classified transient deliberately. A hung call is almost always a
    connection that died underneath `gh` rather than anything wrong with the
    request, so losing the feedstock over it would be the wrong answer.
    """
    message = "gh api ... timed out after 300s"
    runner = FakeRunner(ForgeError(message), ForgeError(message), '{"ok": true}')
    clock = FakeClock()

    assert GitHub(run=runner, sleep=clock).api("rate_limit") == {"ok": True}
    assert clock.slept == [2.0, 4.0]


# --- recording and replaying read-only calls (DESIGN.md 8.2) ---------------


def recorder(
    root: Path, *answers: str | ForgeError, replay: bool = False
) -> tuple[ReadRecorder, FakeRunner]:
    """A recorder over a fake runner, and the fake, so calls can be asserted on."""
    fake = FakeRunner(*answers)
    return ReadRecorder(fake, root, replay=replay), fake


def test_a_read_is_kept_and_answered_from_disk_next_time(tmp_path: Path) -> None:
    """The whole point: the second run reads the same bytes and calls nothing."""
    first, _ = recorder(tmp_path, contents_payload("recipe one"))
    assert GitHub(first).file("conda-forge/x-feedstock", "recipe.yaml", "main") == (
        "recipe one"
    )
    assert (first.replayed, first.fetched) == (0, 1)

    # A runner with no answers queued at all, so any call to it raises.
    second, ran = recorder(tmp_path, replay=True)
    assert GitHub(second).file("conda-forge/x-feedstock", "recipe.yaml", "main") == (
        "recipe one"
    )
    assert (second.replayed, second.fetched) == (1, 0)
    assert ran.calls == []


def test_recording_happens_without_replaying(tmp_path: Path) -> None:
    """An ordinary audit leaves a cache the next one can be pinned against.

    Without this the first `--cached` run after any change would find nothing,
    and the option would only ever work if somebody had remembered to ask for
    it the time before.
    """
    live, _ = recorder(tmp_path, contents_payload("recipe one"))
    GitHub(live).file("conda-forge/x-feedstock", "recipe.yaml", "main")
    assert list(tmp_path.iterdir())


def test_a_replay_does_not_serve_a_read_the_fleet_has_since_changed(
    tmp_path: Path,
) -> None:
    """Stated as the property it is, because it is the risk rather than a bug.

    A replayed audit reports the fleet as it was. That is what pins the
    experiment, and it is why nothing that writes to a feedstock is given one
    of these.
    """
    GitHub(recorder(tmp_path, contents_payload("recipe one"))[0]).file(
        "conda-forge/x-feedstock", "recipe.yaml", "main"
    )
    moved, _ = recorder(tmp_path, contents_payload("recipe two"), replay=True)
    assert GitHub(moved).file("conda-forge/x-feedstock", "recipe.yaml", "main") == (
        "recipe one"
    )


def test_a_write_is_never_kept(tmp_path: Path) -> None:
    """`gh pr edit` cannot match the read prefix, so nothing can record one."""
    keeping, ran = recorder(tmp_path, "")
    GitHub(keeping).label("conda-forge/x-feedstock", 7, "automerge")
    assert list(tmp_path.iterdir()) == []
    assert (keeping.replayed, keeping.fetched) == (0, 0)
    assert ran.calls[0][:3] == ["gh", "pr", "edit"]


def test_a_write_is_never_replayed(tmp_path: Path) -> None:
    """Even with the cache turned on, a label goes to GitHub or nowhere."""
    replaying, ran = recorder(tmp_path, "", replay=True)
    GitHub(replaying).comment("conda-forge/x-feedstock", 7, "hello")
    assert ran.calls[0][:3] == ["gh", "pr", "comment"]
    assert (replaying.replayed, replaying.fetched) == (0, 0)


def test_two_reads_of_one_file_at_two_refs_are_two_entries(tmp_path: Path) -> None:
    """The parameters are part of the key, not just the path.

    A recipe read at a pull request's head and at the default branch is the
    same contents path and two different files, which is the case `audit` and
    `scan` disagree on by design.
    """
    keeping, _ = recorder(tmp_path, contents_payload("head"), contents_payload("main"))
    client = GitHub(keeping)
    assert client.file("conda-forge/x-feedstock", "recipe.yaml", "pr-head") == "head"
    assert client.file("conda-forge/x-feedstock", "recipe.yaml", "main") == "main"
    assert len(list(tmp_path.iterdir())) == 2


def test_a_replay_with_nothing_stored_falls_through_and_says_so(
    tmp_path: Path,
) -> None:
    """A replay whose cache is empty is a live audit, and the counts show it."""
    empty, _ = recorder(tmp_path, contents_payload("recipe one"), replay=True)
    GitHub(empty).file("conda-forge/x-feedstock", "recipe.yaml", "main")
    assert (empty.replayed, empty.fetched) == (0, 1)


def test_an_unwritable_cache_is_a_slow_swage_rather_than_a_broken_one(
    tmp_path: Path,
) -> None:
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("")
    stuck, _ = recorder(blocked, contents_payload("recipe one"))
    assert GitHub(stuck).file("conda-forge/x-feedstock", "recipe.yaml", "main") == (
        "recipe one"
    )


def test_it_does_not_exist_is_an_answer_and_is_kept(tmp_path: Path) -> None:
    """148 feedstocks are still `meta.yaml`, so this is a third of the fleet.

    Not caching it would leave those the only outcomes in a replayed run that
    were not pinned, which is worse than the live call it also costs.
    """
    live, _ = recorder(tmp_path, NotFound("gh api ... failed:\nHTTP 404", "HTTP 404"))
    with pytest.raises(NotFound):
        GitHub(live).file("conda-forge/x-feedstock", "recipe/recipe.yaml", "main")

    replayed, ran = recorder(tmp_path, replay=True)
    with pytest.raises(NotFound) as caught:
        GitHub(replayed).file("conda-forge/x-feedstock", "recipe/recipe.yaml", "main")
    assert caught.value.said == "HTTP 404"
    assert ran.calls == []
    assert (replayed.replayed, replayed.fetched) == (1, 0)


def test_a_transient_failure_is_never_kept(tmp_path: Path) -> None:
    """A cache that remembered a blip would make it permanent.

    `_attempt` retries these, so the recorder sees each attempt; keeping one
    would turn a rate limit into a feedstock that fails for as long as the
    cache lives.
    """
    flaky, _ = recorder(
        tmp_path,
        ForgeError("gh api ... failed:\nHTTP 502"),
        ForgeError("gh api ... failed:\nHTTP 502"),
        ForgeError("gh api ... failed:\nHTTP 502"),
        ForgeError("gh api ... failed:\nHTTP 502"),
    )
    with pytest.raises(ForgeError):
        GitHub(flaky, sleep=FakeClock()).file(
            "conda-forge/x-feedstock", "recipe.yaml", "main"
        )
    assert list(tmp_path.iterdir()) == []
