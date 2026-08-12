"""Tests for the GitHub choke point (DESIGN.md 3.5).

No network and no `gh`: the runner is injected, which is the seam DESIGN.md 11
asks for. What matters here is which failures are retried and which are not --
retrying a 404 wastes fourteen seconds per feedstock across the fleet, and
*not* retrying a secondary rate limit aborts a run that would have succeeded.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Sequence

import pytest

from swage.forge import ForgeError, GitHub


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
