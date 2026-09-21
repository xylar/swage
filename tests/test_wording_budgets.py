"""Every surface swage writes for a person, measured against DESIGN.md §3.2.

The corpus's recipes are planned twice each -- the recipe before the bump
and the one the tool swage replaces published -- and the terminal line, the
comments and the commit message each produces are held to their budgets. A
message that outgrows its budget fails here, where it is rendered, rather
than in review (§3.3). The corpus plans cleanly, so a finding's sentence is
measured where the checks are tested (`test_plan_findings.evaluate_gates`),
and `scripts/budgets.py` measures everything over recorded runs, which are
an input this test cannot ship.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from swage.cli.update import migration_comment, refusal_comment
from swage.config import FeedstockConfig, load_config
from swage.forge import commit_message, conversion_message, upstream_location
from swage.mapping import NameResolver
from swage.plan import Finding, Plan, decide, plan_recipe
from swage.recipe import read_recipe
from swage.run import record
from swage.run.budgets import (
    COMMENT_BODY,
    COMMIT_BODY,
    COMMIT_SUBJECT,
    FINDING_SAID,
    TERMINAL_LINE,
    comment_body,
    commit_body,
    words,
)
from swage.upstream import RecipeUpstream

from .conftest import CONFIG_ROOT
from .test_plan_corpus import PYTHON_MIN, Case, _feedstock, _package_index

CORPUS = Path(__file__).resolve().parent / "corpus"


@dataclass(frozen=True)
class Planned:
    """One corpus recipe, planned, with what the pipeline would say of it."""

    name: str
    config: FeedstockConfig
    plan: Plan

    @property
    def release(self) -> str:
        upstream = self.plan.upstream.primary
        return f"{upstream.name} {upstream.version}"


def _planned() -> list[Planned]:
    tree = load_config(CONFIG_ROOT)
    found = []
    for family in ("airflow-providers", "google-cloud"):
        for directory in sorted((CORPUS / family).iterdir()):
            case = Case(directory.name, directory)
            upstream = case.upstream()
            feedstock = _feedstock(case, upstream)
            config = tree.for_feedstock(feedstock)
            for recipe_name in ("old_recipe.yaml", "recipe.yaml"):
                path = directory / recipe_name
                if not path.is_file():
                    continue
                recipe = read_recipe(path.read_text(encoding="utf-8"))
                resolver = NameResolver(
                    config.name_map, _package_index(recipe, tree, feedstock)
                )
                plan = plan_recipe(
                    recipe, RecipeUpstream.of(upstream), config, resolver, PYTHON_MIN
                )
                found.append(Planned(f"{case.name}/{recipe_name}", config, plan))
    return found


PLANNED = _planned()

#: A finding at the budget's edge, for the shapes a list changes: the comment
#: gains a sentence over it, and the terminal line names its subject.
AT_THE_EDGE = Finding(
    "unaccounted",
    "google-api-core >=1.34.1,!=2.0.*,!=2.1.*,!=2.2.*,!=2.3.*,!=2.4.*,<3.0.0",
    "`google-cloud-tasks`'s `run` requirements",
    " ".join(["word"] * FINDING_SAID),
)


def test_the_corpus_plans_cleanly() -> None:
    """Why the findings are measured elsewhere, stated so it stays true."""
    assert not any(planned.plan.findings for planned in PLANNED)


@pytest.mark.parametrize("planned", PLANNED, ids=lambda p: p.name)
def test_the_terminal_line_fits(planned: Planned) -> None:
    plan = planned.plan
    for findings in ((), (AT_THE_EDGE, AT_THE_EDGE)):
        plan = replace(planned.plan, findings=findings)
        decision = decide(plan.findings, plan.unchanged, planned.config)
        made = record(
            planned.config.feedstock, decision.outcome, plan=plan, decision=decision
        )
        assert words(made.reason) <= TERMINAL_LINE, made.reason


@pytest.mark.parametrize("planned", PLANNED, ids=lambda p: p.name)
def test_the_comment_fits(planned: Planned) -> None:
    """Both comments, at both rungs, with and without a list or the `tools` clause."""
    for findings in ((), (AT_THE_EDGE,)):
        for trust in ("auto", "propose"):
            config = replace(planned.config, trust=trust)
            comment = refusal_comment(planned.release, findings, config)
            assert words(comment_body(comment)) <= COMMENT_BODY, comment
        for added in ((), ("conda_build_tool", "conda_install_tool")):
            comment = migration_comment(planned.release, findings, added)
            assert words(comment_body(comment)) <= COMMENT_BODY, comment


@pytest.mark.parametrize("planned", PLANNED, ids=lambda p: p.name)
def test_the_commit_message_fits(planned: Planned) -> None:
    """With and without a moved source version, which is a list."""
    moved = "apache-airflow-task-sdk 1.3.0 to 1.3.1, which apache-airflow-core requires"
    for lines in ((), (moved,)):
        message = commit_message(
            planned.release,
            upstream_location(planned.plan.recipe, planned.config),
            lines,
        )
        subject, _, body = message.partition("\n")
        assert len(subject) <= COMMIT_SUBJECT
        assert words(commit_body(message)) <= COMMIT_BODY, body


def test_the_conversion_message_fits() -> None:
    """The lists are quoted from the recipe and the converter; the prose is budgeted."""
    message = conversion_message(
        ["conda_build_tool", "conda_install_tool"],
        ["The variable `tests_to_skip` is defined multiple times."],
        ["the `arm64` condition is not in the converted recipe\n  meta.yaml  - x"],
        ["win      9 lines  ->  9 if:/then: entries"],
    )
    subject, _, _ = message.partition("\n")
    assert len(subject) <= COMMIT_SUBJECT
    assert words(commit_body(message)) <= COMMIT_BODY, commit_body(message)


#: The longest sentence `findings._rechecks` puts before a config `reason`:
#: ``x overrules upstream's conflicting bounds --``, six words.
_RECHECK_PREFIX = 6


def _recheck_reasons() -> list[tuple[str, str]]:
    """Every config reason a `recheck` finding would publish, by feedstock."""
    tree = load_config(CONFIG_ROOT)
    found = []
    for feedstock in sorted(tree.feedstocks):
        config = tree.for_feedstock(feedstock)
        for key in ("temporary_constraints", "overruled_constraints"):
            for override in getattr(config, key).values():
                found.append((feedstock, override.reason))
        additions = config.add_requirements
        for entries in (
            *additions.every.values(),
            *(
                sections
                for output in additions.per_output.values()
                for sections in output.values()
            ),
        ):
            found.extend(
                (feedstock, entry.reason) for entry in entries if entry.temporary
            )
    return found


@pytest.mark.parametrize("feedstock,reason", _recheck_reasons(), ids=lambda x: x[:24])
def test_a_config_reason_fits_a_findings_sentence(feedstock: str, reason: str) -> None:
    """A `recheck` finding's sentence is the bound and the config's own words.

    The reason is written in `config/` by a person and published as it is,
    so the budget reaches it there; the reference sweep is where it was
    found to (`apache-beam`, `grpcio-gcp`).
    """
    assert words(reason) + _RECHECK_PREFIX <= FINDING_SAID, reason
