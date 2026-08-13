"""Tests for `swage scan` (DESIGN.md 8).

No network, per DESIGN.md 11: GitHub is a fake runner behind the real `GitHub`
choke point, and the upstream archive is a tar built here whose sha256 the
fixture recipe pins -- so the hash check that DESIGN.md 3.6 makes a hard stop
is exercised rather than bypassed.

What is worth pinning here is the part that is genuinely this layer's: which
pull request swage acts on, which bucket a feedstock lands in, and that a
feedstock swage cannot read stops that feedstock rather than the sweep.
"""

from __future__ import annotations

import base64
import hashlib
import importlib
import io
import json
import shutil
import tarfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from swage.cli import ExitCode, main
from swage.cli.consider import (
    NameSources,
    consider_feedstock,
    plan_at,
    select_feedstocks,
)
from swage.cli.scan import SCAN_DESCRIPTIONS, run_scan
from swage.config import ConfigError, MappingLayer, load_config
from swage.forge import ForgeError, GitHub, NotFound
from swage.mapping import StaticPackageIndex
from swage.report import SCHEMA_VERSION, render_summary

from .conftest import CONFIG_ROOT

#: `swage.cli` re-exports a function called `main`, which shadows the module of
#: that name as an attribute -- so the module has to be fetched rather than
#: reached through the package.
CLI = importlib.import_module("swage.cli.main")

PYPROJECT = """\
[build-system]
requires = ["flit_core==3.12.0"]

[project]
name = "demo"
version = "2.0.0"
dependencies = ["requests>=2.31.0", "pandas>=2.1.0"]
"""

PREVIOUS_PYPROJECT = """\
[build-system]
requires = ["flit_core==3.12.0"]

[project]
name = "demo"
version = "1.0.0"
dependencies = ["requests>=2.31.0", "pandas>=2.1.0", "leftover>=1.0"]
"""


def sdist(pyproject: str) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        payload = pyproject.encode("utf-8")
        info = tarfile.TarInfo("demo-2.0.0/pyproject.toml")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


SDIST = sdist(PYPROJECT)
PREVIOUS_SDIST = sdist(PREVIOUS_PYPROJECT)
SHA256 = hashlib.sha256(SDIST).hexdigest()
PREVIOUS_SHA256 = hashlib.sha256(PREVIOUS_SDIST).hexdigest()

URL = "https://example.invalid/demo-2.0.0.tar.gz"
PREVIOUS_URL = "https://example.invalid/demo-1.0.0.tar.gz"


def recipe_text(version: str, url: str, sha: str, run: str) -> str:
    return f"""\
context:
  version: {version!r}
  python_min: '3.10'

package:
  name: demo
  version: ${{{{ version }}}}

source:
  url: {url}
  sha256: {sha}

build:
  noarch: python

requirements:
  host:
    - python ${{{{ python_min }}}}.*
    - pip
    - flit-core ==3.12.0
  run:
{run}
"""


RUN_MATCHING = """\
    - python >=${{ python_min }}
    - requests >=2.31.0
    - pandas >=2.1.0
"""

RUN_STALE = """\
    - python >=${{ python_min }}
    - requests >=2.30.0
    - pandas >=2.1.0
"""

RECIPE = recipe_text("2.0.0", URL, SHA256, RUN_MATCHING)
STALE_RECIPE = recipe_text("2.0.0", URL, SHA256, RUN_STALE)
BASE_RECIPE = recipe_text("1.0.0", PREVIOUS_URL, PREVIOUS_SHA256, RUN_MATCHING)


class FakeGitHub:
    """A runner answering the reads scan makes, and refusing anything else."""

    def __init__(
        self,
        pulls: Sequence[dict[str, Any]] = (),
        files: dict[str, str] | None = None,
        teams: Sequence[str] = (),
    ) -> None:
        self.pulls = list(pulls)
        self.files = files if files is not None else {"recipe/recipe.yaml": RECIPE}
        self.teams = list(teams)
        self.argvs: list[list[str]] = []

    def __call__(self, argv: Sequence[str]) -> str:
        self.argvs.append(list(argv))
        assert "--method" in argv and argv[argv.index("--method") + 1] == "GET", (
            "every read must pass --method GET (DESIGN.md 3.5)"
        )
        path = next(part for part in argv if "/" in part and not part.startswith("-"))
        if path == "user/teams":
            return json.dumps(
                [
                    [
                        {"name": name, "organization": {"login": "conda-forge"}}
                        for name in self.teams
                    ]
                ]
            )
        if path.endswith("/pulls"):
            return json.dumps(self.pulls)
        return self._contents(path, argv)

    def _contents(self, path: str, argv: Sequence[str]) -> str:
        wanted = path.split("/contents/", 1)[1]
        ref = next(
            (part.split("=", 1)[1] for part in argv if part.startswith("ref=")), ""
        )
        # The base branch carries the recipe as it stands without the pull
        # request, which is what says the version moved (DESIGN.md 3.4.1).
        if ref == "main" and wanted == "recipe/recipe.yaml":
            return _file(BASE_RECIPE)
        if wanted == ".ci_support":
            return json.dumps([])
        if wanted in self.files:
            return _file(self.files[wanted])
        raise NotFound(f"gh: Not Found (HTTP 404) for {wanted}")


def _file(text: str) -> str:
    return json.dumps(
        {"encoding": "base64", "content": base64.b64encode(text.encode()).decode()}
    )


def pull(number: int = 7, created: str = "2026-08-01T00:00:00Z", **rest: Any) -> Any:
    entry = {
        "number": number,
        "title": f"demo v2.0.0 (#{number})",
        "user": {"login": "regro-cf-autotick-bot"},
        "head": {
            "sha": f"sha{number}",
            "ref": f"2.0.0_{number}",
            "repo": {"full_name": "regro-cf-autotick-bot/demo-feedstock"},
        },
        "base": {"ref": "main", "repo": {"archived": False}},
        "created_at": created,
        "labels": [],
    }
    entry.update(rest)
    return entry


def fetcher(**archives: bytes) -> Any:
    """Serve the pinned archives, and refuse anything else the way a 404 does.

    Leaving `previous` unserved is how a test says "this release was yanked",
    which is the case DESIGN.md 3.3.7 leaves unclassified rather than dropped.
    """
    by_url = {URL: archives.get("current", SDIST)}
    if "previous" in archives:
        by_url[PREVIOUS_URL] = archives["previous"]

    def fetch(url: str) -> bytes:
        if url not in by_url:
            raise ForgeError(f"{url}: download failed: HTTP 404")
        return by_url[url]

    return fetch


@pytest.fixture
def names() -> NameSources:
    return NameSources(
        StaticPackageIndex.of("requests", "pandas", "flit-core", "leftover"),
        MappingLayer("grayskull pypi mapping", {}),
    )


@pytest.fixture
def config_root(tmp_path: Path) -> Path:
    """The shipped quirks database, plus a blessed `demo` feedstock.

    The real `config/` rather than a hand-written one, because a harness more
    permissive than reality hides bugs as readily as it invents them -- the
    build floor, the recipe-owned allowlist and the removal policy all come
    from the file the fleet is actually maintained by. `trust: auto` is the
    one addition, since blessing is opt-in and a `manual` feedstock fails G6
    before any other gate is worth asserting on.
    """
    root = tmp_path / "config"
    shutil.copytree(CONFIG_ROOT, root)
    (root / "feedstocks" / "demo.yaml").write_text(
        "feedstock: demo\ntrust: auto\n", encoding="utf-8"
    )
    return root


@pytest.fixture
def tree(config_root: Path) -> Any:
    return load_config(config_root)


def scan(runner: FakeGitHub, tree: Any, names: NameSources, **archives: bytes) -> Any:
    return consider_feedstock(
        GitHub(run=runner), tree, "demo", names, fetch=fetcher(**archives)
    )


def test_a_feedstock_with_no_bot_pull_request_is_unchanged(
    tree: Any, names: NameSources
) -> None:
    record = scan(FakeGitHub(pulls=[]), tree, names)

    assert record.outcome == "unchanged"
    # No detail, because 206 lines saying "no open bot PR" is the report
    # burying the nine that need reading (DESIGN.md 9).
    assert record.detail == ""


def test_a_recipe_already_matching_upstream_is_path_b(
    tree: Any, names: NameSources
) -> None:
    """swage would change nothing, so only swage can ever merge it.

    With no commit to push there is no CI run, so conda-forge's automerge is
    never dispatched and the pull request would sit open forever
    (DESIGN.md 2.1). Calling it `merge-ready` -- "pushed + labeled automerge,
    awaiting CI" -- would name the one course of action that cannot happen.
    """
    record = scan(FakeGitHub(pulls=[pull()]), tree, names, previous=PREVIOUS_SDIST)

    assert record.outcome == "awaiting-ci"
    assert {gate.name: gate.passed for gate in record.gates}["G7"] is True


def test_a_stale_recipe_is_a_change_and_g7_does_not_apply(
    tree: Any, names: NameSources
) -> None:
    runner = FakeGitHub(pulls=[pull()], files={"recipe/recipe.yaml": STALE_RECIPE})
    record = scan(runner, tree, names, previous=PREVIOUS_SDIST)

    bumped = [
        line
        for section in record.sections
        for line in section.lines
        if line.action == "bump"
    ]
    assert [line.text for line in bumped] == ["requests >=2.30.0 -> >=2.31.0"]
    # Path A: swage would push, and conda-forge decides on green CI.
    assert {gate.name: gate.passed for gate in record.gates}["G7"] is None


def test_the_previous_version_classifies_a_removal(
    tree: Any, names: NameSources
) -> None:
    """The second fetch DESIGN.md 3.3.7 says the classification costs.

    `leftover` is in 1.0.0's metadata and not in 2.0.0's, which is the only
    evidence that lets swage call a line upstream-dropped rather than
    something a maintainer put there on purpose.
    """
    recipe = recipe_text("2.0.0", URL, SHA256, RUN_MATCHING + "    - leftover >=1.0\n")
    runner = FakeGitHub(pulls=[pull()], files={"recipe/recipe.yaml": recipe})

    record = scan(runner, tree, names, previous=PREVIOUS_SDIST)

    dropped = [
        line
        for section in record.sections
        for line in section.lines
        if line.action == "drop"
    ]
    assert [line.text for line in dropped] == ["leftover >=1.0"]
    assert record.outcome == "needs-review"
    gates = {gate.name: gate for gate in record.gates}
    assert gates["G8"].passed is False
    assert "leftover" in gates["G8"].detail


def test_an_unreadable_previous_version_keeps_the_line(
    tree: Any, names: NameSources
) -> None:
    """A yanked release leaves the removal unclassified, so the line stays.

    swage does not delete on a guess, so the direction this failure falls in
    is the safe one (DESIGN.md 3.3.7).
    """
    recipe = recipe_text("2.0.0", URL, SHA256, RUN_MATCHING + "    - leftover >=1.0\n")
    runner = FakeGitHub(pulls=[pull()], files={"recipe/recipe.yaml": recipe})

    # No `previous` archive served: fetching 1.0.0's metadata fails.
    record = scan(runner, tree, names)

    kept = [
        line
        for section in record.sections
        for line in section.lines
        if line.text.startswith("leftover")
    ]
    assert [line.action for line in kept] == ["keep"]
    assert not any(
        line.action == "drop" for section in record.sections for line in section.lines
    )


def test_the_newest_version_update_is_the_one_acted_on(
    tree: Any, names: NameSources
) -> None:
    """Superseded bumps pile up and only the newest is wanted (DESIGN.md 3.4.1)."""
    runner = FakeGitHub(
        pulls=[
            pull(number=5, created="2026-07-01T00:00:00Z"),
            pull(number=9, created="2026-08-01T00:00:00Z"),
        ]
    )
    record = scan(runner, tree, names, previous=PREVIOUS_SDIST)

    assert record.pull_request == 9
    assert record.pull_requests == 2


def test_migrations_are_left_alone_and_counted(tree: Any, names: NameSources) -> None:
    """A rebuild changes no version, so there is nothing to reconcile.

    Reporting the count is the load-bearing half: swage ignoring four pull
    requests in silence is how a maintainer finds out months later
    (DESIGN.md 3.4.1).
    """
    base = FakeGitHub(pulls=[pull(number=n) for n in (1, 2, 3, 4)])
    # Every pull request carries the recipe the base branch already has, so no
    # version moved.
    base.files = {"recipe/recipe.yaml": BASE_RECIPE}

    record = consider_feedstock(GitHub(run=base), tree, "demo", names, fetch=fetcher())

    assert record.outcome == "unchanged"
    assert "4 open bot pull requests, none a version update" in record.detail
    assert "the bot files no more" in record.detail
    assert record.pull_requests == 4


def test_a_v0_feedstock_is_routed_rather_than_parsed(
    tree: Any, names: NameSources
) -> None:
    runner = FakeGitHub(pulls=[pull()], files={"recipe/meta.yaml": "{% set x = 1 %}"})

    record = scan(runner, tree, names)

    assert record.outcome == "needs-migration"
    assert record.pull_request == 7


def test_an_archived_feedstock_has_no_pull_requests_to_act_on(
    tree: Any, names: NameSources
) -> None:
    """Nothing can be pushed to it or merged into it (DESIGN.md 3.4.1)."""
    runner = FakeGitHub(pulls=[pull(base={"ref": "main", "repo": {"archived": True}})])

    assert scan(runner, tree, names).outcome == "unchanged"


def test_a_team_with_no_repository_is_not_a_failure(
    tree: Any, names: NameSources
) -> None:
    """`all-members` is org-wide -- one 404 in 487 (DESIGN.md 3.4)."""

    class Missing(FakeGitHub):
        def __call__(self, argv: Sequence[str]) -> str:
            raise NotFound("gh: Not Found (HTTP 404)")

    record = scan(Missing(), tree, names)

    assert record.outcome == "unchanged"
    assert record.detail == "no feedstock repository"


def test_an_unreadable_feedstock_stops_that_feedstock_only(
    tree: Any, names: NameSources
) -> None:
    """A sweep that aborts on the first bad recipe is a sweep nobody can run."""
    broken = FakeGitHub(
        pulls=[pull()], files={"recipe/recipe.yaml": "context:\n  version: '2.0.0'\n"}
    )
    good = FakeGitHub(pulls=[pull()])

    class Router:
        def __call__(self, argv: Sequence[str]) -> str:
            which = broken if "broken-feedstock" in " ".join(argv) else good
            return which(argv)

    run = run_scan(
        GitHub(run=Router()),
        tree,
        ["broken", "demo"],
        names,
        fetch=fetcher(previous=PREVIOUS_SDIST),
    )

    assert [record.outcome for record in run.feedstocks] == ["failed", "awaiting-ci"]
    assert run.needs_review is True


def test_a_hash_mismatch_stops_the_feedstock(tree: Any, names: NameSources) -> None:
    """swage reconciles against the bytes the recipe claims (DESIGN.md 3.6)."""
    record = scan(
        FakeGitHub(pulls=[pull()]), tree, names, current=sdist("[project]\nname='x'\n")
    )

    assert record.outcome == "failed"
    assert "sha256 does not match" in record.stopped


def test_selecting_one_feedstock_never_lists_teams(
    tree: Any, names: NameSources
) -> None:
    """Naming a feedstock makes this a two-call operation, not a sweep."""
    runner = FakeGitHub()

    assert select_feedstocks(GitHub(run=runner), tree, feedstock="demo") == ("demo",)
    assert runner.argvs == []


def test_selecting_a_family_filters_the_discovered_list(
    tree: Any, names: NameSources
) -> None:
    runner = FakeGitHub(
        teams=["apache-airflow-providers-amazon", "google-cloud-bigquery", "cartopy"]
    )

    selected = select_feedstocks(GitHub(run=runner), tree, family="airflow-providers")

    assert selected == ("apache-airflow-providers-amazon",)


def test_selecting_everything_takes_the_whole_discovered_list(
    tree: Any, names: NameSources
) -> None:
    runner = FakeGitHub(teams=["cartopy", "google-cloud-bigquery"])

    selected = select_feedstocks(GitHub(run=runner), tree, everything=True)

    assert selected == ("cartopy", "google-cloud-bigquery")


def test_an_unknown_family_is_named_rather_than_scanning_nothing(
    tree: Any, names: NameSources
) -> None:
    """Silently scanning zero feedstocks would look like a clean run."""
    runner = FakeGitHub(teams=["cartopy"])

    with pytest.raises(ConfigError, match="no such family 'gogle-cloud'"):
        select_feedstocks(GitHub(run=runner), tree, family="gogle-cloud")


def _wire_cli(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    runner: FakeGitHub,
    names: NameSources,
) -> Path:
    """Point the command at the fakes and at a throwaway cache directory."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setattr(CLI, "GitHub", lambda: GitHub(run=runner))
    monkeypatch.setattr(CLI, "load_package_index", lambda: names.index)
    monkeypatch.setattr(CLI, "load_grayskull_layer", lambda: names.grayskull)
    return tmp_path / "cache" / "swage" / "runs"


def test_the_command_writes_a_run_and_exits_zero_when_nothing_needs_you(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    config_root: Path,
    names: NameSources,
) -> None:
    runs = _wire_cli(monkeypatch, tmp_path, FakeGitHub(pulls=[]), names)

    code = main(
        ["--config-root", str(config_root), "scan", "--feedstock", "demo", "--quiet"]
    )

    assert code == ExitCode.OK
    out = capsys.readouterr().out
    assert "UNCHANGED (1)" in out
    assert "swage scan --feedstock demo" in out
    written = list(runs.glob("*/run.json"))
    assert len(written) == 1
    assert json.loads(written[0].read_text())["schema"] == SCHEMA_VERSION


def test_the_command_exits_one_when_something_needs_review(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    config_root: Path,
    names: NameSources,
) -> None:
    """`1` items need review, so a cron wrapper can alert on it (DESIGN.md 9.1)."""
    runner = FakeGitHub(pulls=[pull()], files={"recipe/meta.yaml": "{% set x = 1 %}"})
    _wire_cli(monkeypatch, tmp_path, runner, names)

    code = main(
        ["--config-root", str(config_root), "scan", "--feedstock", "demo", "--quiet"]
    )

    assert code == ExitCode.NEEDS_REVIEW
    out = capsys.readouterr().out
    assert "NEEDS MIGRATION (1)" in out
    # Scan's wording, not update's: this command converts nothing.
    assert "`swage update --migrate` converts in place" in out


def test_the_report_never_claims_a_scan_pushed_anything(
    tree: Any, names: NameSources
) -> None:
    """The record's vocabulary is `update`'s; the sentences are not.

    A `merge-ready` record means the same thing whichever command produced it,
    which is what keeps two run.json comparable -- but a bucket reading
    "pushed + labeled automerge" would assert something this command cannot do.
    """
    runner = FakeGitHub(pulls=[pull()], files={"recipe/recipe.yaml": STALE_RECIPE})
    run = run_scan(
        GitHub(run=runner),
        tree,
        ["demo"],
        names,
        fetch=fetcher(previous=PREVIOUS_SDIST),
    )

    out = render_summary(run, descriptions=SCAN_DESCRIPTIONS, color=False)

    assert "MERGE-READY (1)" in out
    assert "would push + label automerge" in out
    assert "pushed +" not in out


def test_the_report_never_offers_to_label_a_feedstock_it_would_not_push(
    tree: Any, names: NameSources
) -> None:
    """The bucket a path B feedstock lands in has to be one it can leave.

    MERGE-READY says "pushed + labeled automerge, awaiting CI", and this is
    exactly the feedstock where none of that happens: no commit, so no CI, so
    nothing ever dispatches conda-forge's automerge (DESIGN.md 2.1).
    """
    run = run_scan(
        GitHub(run=FakeGitHub(pulls=[pull()])),
        tree,
        ["demo"],
        names,
        fetch=fetcher(previous=PREVIOUS_SDIST),
    )

    out = render_summary(run, descriptions=SCAN_DESCRIPTIONS, color=False)

    assert "AWAITING CI (1)" in out
    assert "no changes needed" in out
    assert "MERGE-READY" not in out


def test_the_run_record_names_the_command_and_when(
    tree: Any, names: NameSources
) -> None:
    run = run_scan(
        GitHub(run=FakeGitHub(pulls=[])),
        tree,
        ["demo"],
        names,
        command="swage scan --feedstock demo",
    )

    assert run.command == "swage scan --feedstock demo"
    assert run.started
    assert run.schema_version == SCHEMA_VERSION


def test_plan_at_renders_a_ref_with_no_pull_request(
    tree: Any, names: NameSources
) -> None:
    """The comparison DESIGN.md 10 needs runs off `main`, not off a bot PR.

    The tools swage replaces act only on open bot pull requests, and a
    feedstock that still has one is usually blocked -- so their *published*
    output on the default branch is the larger and less biased sample.
    """
    runner = FakeGitHub(pulls=[])
    planned = plan_at(
        GitHub(run=runner),
        tree.for_feedstock("demo"),
        "main",
        RECIPE,
        None,
        names,
        fetch=fetcher(),
    )

    assert planned.rendered
    assert planned.recipe.text == RECIPE
    # It never asked whether a pull request existed, which is the point: this
    # feedstock has none and is still comparable. (This recipe sets its own
    # `python_min`, so the ref is not consulted either -- 55 of 60 noarch
    # recipes do not, and those read `.ci_support` at whatever ref they are
    # given, DESIGN.md 3.5.)
    assert not any("/pulls" in " ".join(argv) for argv in runner.argvs)


def test_plan_at_without_a_previous_version_keeps_every_removal(
    tree: Any, names: NameSources
) -> None:
    """No previous metadata means no removal can be justified (DESIGN.md 3.3.7).

    That is the safe direction by construction: a main-based rendering may add
    or change a line, and can never drop one it cannot account for -- which is
    what makes a diff against the published recipe readable as swage's opinion
    rather than as loss.
    """
    stale = recipe_text("2.0.0", URL, SHA256, RUN_MATCHING + "    - long-gone\n")
    planned = plan_at(
        GitHub(run=FakeGitHub(pulls=[])),
        tree.for_feedstock("demo"),
        "main",
        stale,
        None,
        names,
        fetch=fetcher(),
    )

    assert "long-gone" in planned.rendered
    assert not planned.plan.dropped
