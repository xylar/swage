"""Tests for the switch from update-grayskull to hint-grayskull (DESIGN.md
§5.3, §9.8).

The edit is one line of a file other people wrote, so what is pinned here is
that nothing else in it moves, and that a shape the edit cannot be shown to
handle is left for a person rather than guessed at.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Sequence

import pytest

from swage.forge import (
    SWITCH_SUBJECT,
    BotPullRequest,
    GitHub,
    NotFound,
    plan_inspection,
)
from swage.forge.inspection import UNEDITABLE, switched

HEAD = "caf01ea7e0d0cf996fa2e28b224a1977652395fc"

FLEET = "bot:\n  inspection: update-grayskull\nconda_build:\n  pkg_format: '2'\n"


def test_the_one_line_is_switched_and_nothing_else_moves() -> None:
    assert switched(FLEET) == FLEET.replace("update-grayskull", "hint-grayskull")


def test_a_trailing_comment_and_space_survive() -> None:
    text = "bot:\n  automerge: true\n  inspection: update-grayskull  # see #12 \n"
    assert switched(text) == text.replace("update-grayskull", "hint-grayskull")


@pytest.mark.parametrize(
    "text",
    [
        "bot:\n  inspection: hint-grayskull\n",
        "bot:\n  automerge: true\n",
        "conda_build:\n  pkg_format: '2'\n",
        "",
        "{not yaml",
    ],
)
def test_a_file_not_updating_from_grayskull_is_not_switched(text: str) -> None:
    assert switched(text) is None


def test_a_shape_the_line_edit_cannot_reach_is_left_for_a_person() -> None:
    """Flow style parses to the same setting and has no line to edit."""
    with pytest.raises(ValueError, match="does not edit"):
        switched("bot: {inspection: update-grayskull}\n")


def test_an_edit_that_would_move_another_setting_is_refused() -> None:
    """The same words under another key are not the setting."""
    text = (
        "bot:\n  inspection: update-grayskull\nother:\n  inspection: update-grayskull\n"
    )
    with pytest.raises(ValueError, match="does not edit"):
        switched(text)


class Reads:
    """`conda-forge.yml` at the pull request's head, and its history."""

    def __init__(self, text: str | None, history: Sequence[str] = ()) -> None:
        self.text = text
        self.history = list(history)
        self.paths: list[str] = []

    def __call__(self, argv: Sequence[str]) -> str:
        assert argv[argv.index("--method") + 1] == "GET"
        path = next(part for part in argv if "/" in part and not part.startswith("-"))
        self.paths.append(path)
        if path.endswith("/commits"):
            assert "sha=" + HEAD in argv
            return json.dumps(
                [[{"sha": "ab" * 20, "commit": {"message": m}} for m in self.history]]
            )
        assert "ref=" + HEAD in argv, "read at the pull request's head"
        if self.text is None:
            raise NotFound("gh: Not Found (HTTP 404)")
        content = base64.b64encode(self.text.encode()).decode()
        return json.dumps({"encoding": "base64", "content": content})


def pull() -> BotPullRequest:
    return BotPullRequest(
        feedstock="demo",
        number=7,
        title="demo v2.0.0",
        head_sha=HEAD,
        head_ref="2.0.0_hbeef",
        head_repo="regro-cf-autotick-bot/demo-feedstock",
        base_ref="main",
        created_at="2026-08-12T00:00:00Z",
    )


def test_the_switch_is_planned_from_the_file_at_the_head() -> None:
    reads = Reads(FLEET, history=["Update conda-forge.yml\n"])
    planned = plan_inspection(GitHub(run=reads), pull())
    assert planned.text == FLEET.replace("update-grayskull", "hint-grayskull")
    assert planned.note == ""


def test_a_feedstock_with_no_file_has_nothing_to_switch() -> None:
    reads = Reads(None)
    planned = plan_inspection(GitHub(run=reads), pull())
    assert planned.text is None and planned.note == ""


def test_the_history_is_read_only_where_there_is_something_to_switch() -> None:
    reads = Reads("bot:\n  inspection: hint-grayskull\n")
    plan_inspection(GitHub(run=reads), pull())
    assert not any(path.endswith("/commits") for path in reads.paths)


def test_a_switch_already_undone_once_is_not_made_again() -> None:
    reads = Reads(FLEET, history=["Revert something", f"{SWITCH_SUBJECT}\n\nbody"])
    planned = plan_inspection(GitHub(run=reads), pull())
    assert planned.text is None
    assert "back on update-grayskull since swage switched it in abababa" in (
        planned.note
    )


def test_an_uneditable_file_says_so_rather_than_failing() -> None:
    reads = Reads("bot: {inspection: update-grayskull}\n")
    planned = plan_inspection(GitHub(run=reads), pull())
    assert planned.text is None
    assert planned.note == UNEDITABLE


def test_a_conversion_s_text_is_switched_without_a_read() -> None:
    reads = Reads(None, history=[])
    planned = plan_inspection(GitHub(run=reads), pull(), FLEET)
    assert planned.text is not None
    assert not any("/contents/" in path for path in reads.paths)
