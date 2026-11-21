"""Who counts as another maintainer (DESIGN.md §16)."""

from __future__ import annotations

from swage.forge import Author, GitHub, others_comments
from swage.forge.authorship import written_by_others

ME = "xylar"


def test_the_person_running_swage_is_not_another_maintainer() -> None:
    assert written_by_others((Author(ME, "Xylar"),), ME) == ()


def test_swages_own_trailer_resolves_to_github_and_does_not_count() -> None:
    """`Co-Authored-By: swage <noreply@github.com>` is GitHub's `web-flow`."""
    authors = (Author(ME, "Xylar"), Author("web-flow", "swage"))
    assert written_by_others(authors, ME) == ()


def test_conda_forge_bots_do_not_count() -> None:
    for author in (
        Author("regro-cf-autotick-bot", "regro-cf-autotick-bot"),
        Author("conda-forge-admin", "conda-forge-admin"),
        Author(None, "github-actions[bot]"),
        Author("pre-commit-ci[bot]", "pre-commit-ci[bot]"),
    ):
        assert written_by_others((author,), ME) == (), author


def test_a_co_author_without_an_account_is_a_tool() -> None:
    authors = (Author(ME, "Xylar"), Author(None, "Some Script"))
    assert written_by_others(authors, ME) == ()


def test_an_agent_co_author_is_a_tool_though_it_has_an_account() -> None:
    """`noreply@anthropic.com` is the `claude` account's, so the agent that
    co-wrote google-cloud-bigquery's extra headers resolves to a login.
    """
    authors = (Author(ME, "Xylar"), Author("claude", "Claude Opus 5"))
    assert written_by_others(authors, ME) == ()


def test_the_tools_swage_replaces_wrote_for_whoever_ran_them() -> None:
    """The airflow updater committed under a name of its own and no account."""
    assert written_by_others((Author(None, "airflow-providers-updater"),), ME) == ()


def test_an_author_without_an_account_is_still_somebody_else() -> None:
    assert written_by_others((Author(None, "A Stranger"),), ME) == ("A Stranger",)


def test_another_maintainer_counts_as_author_or_co_author() -> None:
    assert written_by_others((Author("ocefpaf", "Filipe"),), ME) == ("ocefpaf",)
    authors = (Author(ME, "Xylar"), Author("ocefpaf", "Filipe"))
    assert written_by_others(authors, ME) == ("ocefpaf",)


class Blamed(GitHub):
    """Answers blame and who is logged in, and counts the blames asked."""

    def __init__(self, *lines: tuple[Author, ...]) -> None:
        super().__init__(run=self._refuse)
        self.lines = lines
        self.asked = 0
        self._login = ME

    @staticmethod
    def _refuse(argv: object) -> str:
        raise AssertionError(f"unexpected call: {argv}")

    def blame(self, repo: str, path: str, ref: str) -> tuple[tuple[Author, ...], ...]:
        self.asked += 1
        return self.lines


RECIPE = """\
requirements:
  host:
    # python-sbom[hatch]
    - hatchling
    # mine
"""


def test_each_comment_another_maintainer_wrote_is_named_with_its_author() -> None:
    github = Blamed(
        (Author(ME, "X"),),
        (Author(ME, "X"),),
        (Author("ocefpaf", "Filipe"),),
        (Author("ocefpaf", "Filipe"),),
        (Author(ME, "X"),),
    )
    found = others_comments(github, "virtualenv", "abc", RECIPE)
    assert dict(found) == {"# python-sbom[hatch]": ("ocefpaf",)}


def test_a_recipe_without_comments_is_not_blamed() -> None:
    github = Blamed()
    assert others_comments(github, "demo", "abc", "requirements: {}\n") == {}
    assert github.asked == 0
