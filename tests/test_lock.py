"""Tests for the per-feedstock lock (v1 §9.1; DESIGN.md §12.1).

A second hold of one lock from the same process conflicts like one from
another process -- `flock` locks an open file, and Windows locks a handle --
so a held lock and a waiter can both live in one test. The waiter's sleep is
where the holder lets go.
"""

from __future__ import annotations

from contextlib import ExitStack

import pytest

from swage.lock import POLL, feedstock_lock


def test_a_held_lock_is_waited_for_and_the_wait_is_said_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    holder = ExitStack()
    holder.enter_context(feedstock_lock("demo"))
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        # Twice round the loop before the other run lets go.
        if len(sleeps) == 2:
            holder.close()

    monkeypatch.setattr("swage.lock.time.sleep", sleep)
    told: list[str] = []

    with feedstock_lock("demo", told.append):
        pass

    assert told == ["demo"]
    assert sleeps == [POLL, POLL]


def test_another_feedstock_s_lock_is_no_reason_to_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def sleep(seconds: float) -> None:
        raise AssertionError("waited on a lock nobody holds")

    monkeypatch.setattr("swage.lock.time.sleep", sleep)

    with feedstock_lock("demo"), feedstock_lock("other"):
        pass


def test_the_lock_is_released_when_the_body_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def sleep(seconds: float) -> None:
        raise AssertionError("the lock outlived the body that raised")

    monkeypatch.setattr("swage.lock.time.sleep", sleep)

    with pytest.raises(RuntimeError), feedstock_lock("demo"):
        raise RuntimeError

    with feedstock_lock("demo"):
        pass
