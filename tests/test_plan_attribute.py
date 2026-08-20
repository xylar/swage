"""Attribution tests, one per outcome (DESIGN.md 3.3.10, 11).

Two matter more than the rest. A dependency reachable only through an
*unlisted* extra must fail G1 with a message naming that extra rather than
pointing at `add_requirements` -- getting it wrong gives confidently wrong
advice, which is worse than none. And a dependency in both a listed and an
unlisted extra must be explained by the listed one.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

import pytest

from swage.config import (
    AddedRequirement,
    Layered,
    MappingLayer,
    RecipeOwned,
    load_config,
)
from swage.mapping import NameResolver, StaticPackageIndex
from swage.plan import (
    Attribution,
    AttributionIndex,
    Provenance,
    PythonMin,
    Unexplained,
    attribute,
    build_index,
    plan_recipe,
)
from swage.plan.lines import parse_line
from swage.recipe import read_recipe
from swage.upstream import RecipeUpstream, parse_pyproject

from .conftest import WriteTree

OWNED = RecipeOwned(
    functions=("pin_subpackage", "compiler", "stdlib"), names=("python", "pip")
)

UPSTREAM = parse_pyproject(
    """
[project]
name = "demo"
dependencies = ["requests >=2.21.0", "google-api-core[grpc] >=2.28.0"]

[project.optional-dependencies]
pandas = ["pandas >=1.3.0", "shared-dep >=1.0"]
tests = ["pytest >=7", "shared-dep >=1.0"]
docs = ["sphinx >=7"]
"""
)

INDEX_NAMES = frozenset(
    {
        "requests",
        "google-api-core-grpc",
        "pandas",
        "shared-dep",
        "pytest",
        "sphinx",
        "grpcio-gcp",
    }
)


def _resolver() -> NameResolver:
    layer = MappingLayer(
        "config/feedstocks/demo.yaml", {"google-api-core[grpc]": "google-api-core-grpc"}
    )
    return NameResolver(Layered((layer,)), StaticPackageIndex(INDEX_NAMES))


def _index(
    listed: tuple[str, ...] = ("pandas",), core: bool = True
) -> AttributionIndex:
    return build_index(UPSTREAM, listed, _resolver(), core=core)


def _attribute(
    text: str,
    listed: tuple[str, ...] = ("pandas",),
    added: Sequence[AddedRequirement] = (),
) -> Attribution:
    return attribute(parse_line(text), _index(listed), OWNED, added)


# --- the four that explain a line ----------------------------------------


def test_1_a_recipe_owned_line_is_explained_without_the_resolver() -> None:
    result = _attribute("${{ pin_subpackage(name, exact=True) }}")
    assert isinstance(result, Provenance)
    assert result.origin == "recipe-kept"
    assert result.mapping is None


def test_1_python_is_recipe_owned_by_name() -> None:
    result = _attribute("python >=${{ python_min }}")
    assert isinstance(result, Provenance)
    assert result.origin == "recipe-kept"
    assert result.detail == "recipe_owned.names:python"


def test_2_an_upstream_core_dependency_is_explained() -> None:
    result = _attribute("requests >=2.21.0")
    assert isinstance(result, Provenance)
    assert result.origin == "upstream-core"
    assert result.mapping is not None
    assert result.mapping.conda_name == "requests"


def test_2_a_core_dependency_carrying_an_extra_resolves_to_its_own_package() -> None:
    """`google-api-core[grpc]` is google-api-core-grpc, a different package."""
    result = _attribute("google-api-core-grpc >=2.28.0")
    assert isinstance(result, Provenance)
    assert result.origin == "upstream-core"
    assert result.mapping is not None
    assert result.mapping.pypi_name == "google-api-core[grpc]"


def test_3_a_listed_extra_explains_its_dependency() -> None:
    result = _attribute("pandas >=1.3.0")
    assert isinstance(result, Provenance)
    assert result.origin == "upstream-extra"
    assert result.detail == "extra:pandas"


def test_5_add_requirements_explains_a_conda_forge_only_line() -> None:
    added = (AddedRequirement("grpcio-gcp >=0.2.2", "config/feedstocks/demo.yaml"),)
    result = _attribute("grpcio-gcp >=0.2.2", added=added)
    assert isinstance(result, Provenance)
    assert result.origin == "config-add"
    assert result.detail == "config/feedstocks/demo.yaml"


def test_5_a_temporary_add_requirement_explains_the_line_just_as_well() -> None:
    """Temporary is about re-asking, not about whether the line is accounted for.

    The two have to be separable or the shape does not work: `airflow` carries
    `snowflake-connector-python !=4.4.0` to dodge a bad release, and swage must
    both stop calling that line unexplained (G1) and keep asking whether it is
    still needed (G11). An entry that satisfied neither would leave the
    feedstock stuck; one that satisfied both silently would let a workaround
    become permanent.
    """
    added = (
        AddedRequirement(
            "snowflake-connector-python !=4.4.0",
            "config/feedstocks/demo.yaml",
            reason="4.4.0 on conda-forge is broken",
            temporary=True,
        ),
    )
    result = _attribute("snowflake-connector-python !=4.4.0", added=added)
    assert isinstance(result, Provenance)
    assert result.origin == "config-add"


# --- the two that fail G1, and the difference between them ----------------


def test_4_an_unlisted_extra_names_the_extra_not_add_requirements() -> None:
    """The fix is to list the extra, so swage maintains the line from now on.

    Pointing at `add_requirements` would quietly convert a maintainable
    dependency into a hand-managed one -- same verdict, opposite remedy.
    """
    result = _attribute("pytest >=7")
    assert isinstance(result, Unexplained)
    assert result.kind == "unlisted-extra"
    assert result.extras == ("tests",)
    assert "`tests`" in result.reason
    assert "add the extra" in result.remedy
    assert "add_requirements" not in result.message


def test_6_a_never_upstream_line_points_at_add_requirements() -> None:
    result = _attribute("leftpad >=1.0")
    assert isinstance(result, Unexplained)
    assert result.kind == "nowhere"
    assert "add_requirements" in result.remedy
    assert "no upstream version" in result.reason


def test_6_offers_recording_a_temporary_requirement() -> None:
    """The third answer, and the one a helpful change would delete.

    A line working around another conda-forge package's broken metadata must
    not go in `add_requirements`: that entry silences G1 for good, and the line
    then outlives the bug it exists for with nothing left to notice. It must
    not be left unexplained either, which was the advice here until
    `temporary_requirements` existed -- that kept swage asking, but at the cost
    of the feedstock never being updated at all (DESIGN.md 3.3.14.1).

    So the message offers all three, because on this fleet "declare it or drop
    it" is not exhaustive.
    """
    result = _attribute("leftpad >=1.0")
    assert isinstance(result, Unexplained)
    assert "add_requirements" in result.remedy
    assert "temporary_requirements" in result.remedy
    assert "every version bump" in result.remedy


HOST_AND_RUN = parse_pyproject(
    """
[project]
name = "demo"
dependencies = ["protobuf >=6.33.5"]

[build-system]
requires = ["setuptools >=61"]
"""
)


def _attribute_section(text: str, section: str) -> Attribution:
    index = build_index(HOST_AND_RUN, (), _resolver(), section=section)
    return attribute(parse_line(text), index, OWNED, ())


def test_a_host_line_upstream_declares_at_run_time_says_so() -> None:
    """ "in no upstream version" would be false, and it is what gets decided on.

    `host` is reconciled against `[build-system] requires` (DESIGN.md 3.3.6),
    so a runtime dependency listed in `host` is not explained by this section
    -- but upstream does declare it, and telling a maintainer it does not
    invites dropping a line upstream asks for. `googleapis-common-protos`
    carries `protobuf` in `host` exactly this way.
    """
    result = _attribute_section("protobuf >=6.33.5", "host")
    assert isinstance(result, Unexplained)
    assert result.kind == "nowhere"
    assert "declared by upstream as a run dependency" in result.reason
    assert "no upstream version" not in result.reason
    assert "add_requirements" in result.remedy


def test_the_section_the_line_is_in_is_named() -> None:
    """ "This section" is not a section anybody can go and look at.

    The summary line a maintainer reads first carries no path and no output
    name, and a recipe states the same dependency in `host` and in `run`
    routinely -- `mpas_tools` states `netcdf4` in both -- so which of them was
    being talked about was left to the reader to guess. The path is what
    identifies it, and every finding opens with the line and the path it sits
    in.
    """
    result = _attribute_section("protobuf >=6.33.5", "host")
    assert isinstance(result, Unexplained)
    assert "`/requirements/host`" in result.reason
    assert "upstream's build-system requirements" in result.reason
    assert "this section" not in result.reason


def test_a_run_line_upstream_declares_as_a_build_requirement_says_so() -> None:
    """The other direction, on `mpas-analysis`'s `setuptools`."""
    result = _attribute_section("setuptools >=61", "run")
    assert isinstance(result, Unexplained)
    assert "declared by upstream as a build requirement" in result.reason
    assert "`/requirements/run`" in result.reason


def test_a_name_upstream_declares_nowhere_still_says_no_upstream_version() -> None:
    """The narrowing is about the other role, not about the message."""
    result = _attribute_section("leftpad >=1.0", "host")
    assert isinstance(result, Unexplained)
    assert "no upstream version" in result.reason


def test_the_two_failures_give_opposite_advice() -> None:
    """Asserted together because confusing them is the failure mode."""
    unlisted = _attribute("sphinx >=7")
    nowhere = _attribute("leftpad >=1.0")
    assert isinstance(unlisted, Unexplained) and isinstance(nowhere, Unexplained)
    assert "add_requirements" in nowhere.remedy
    assert "add_requirements" not in unlisted.message
    assert "`docs`" in unlisted.reason


# --- ordering --------------------------------------------------------------


def test_3_beats_4_when_a_dependency_is_in_both_kinds_of_extra() -> None:
    """Explained by the listed one; it needs no further thought."""
    result = _attribute("shared-dep >=1.0")
    assert isinstance(result, Provenance)
    assert result.detail == "extra:pandas"


def test_5_beats_4_so_a_declared_line_is_not_reported_as_an_unlisted_extra() -> None:
    """The maintainer already answered the question by writing it down."""
    added = (AddedRequirement("pytest >=7", "config/feedstocks/demo.yaml"),)
    result = _attribute("pytest >=7", added=added)
    assert isinstance(result, Provenance)
    assert result.origin == "config-add"


def test_1_beats_everything_so_a_structural_line_never_resolves() -> None:
    """`python` is also an upstream dependency of plenty of projects."""
    upstream_python = parse_pyproject(
        '[project]\nname = "demo"\ndependencies = ["python-dateutil >=2"]\n'
    )
    index = build_index(upstream_python, (), _resolver())
    result = attribute(parse_line("python ${{ python_min }}.*"), index, OWNED)
    assert isinstance(result, Provenance)
    assert result.origin == "recipe-kept"


# --- the allowlist boundary ------------------------------------------------


def test_an_unrecognized_template_is_preserved_and_still_fails_g1() -> None:
    """A `recipe-kept` fallback here would silently disarm DESIGN.md 3.3.7."""
    result = _attribute("${{ pin_compatible('numpy') }}")
    assert isinstance(result, Unexplained)
    assert result.kind == "unrecognized-template"
    assert "pin_compatible" in result.reason
    assert "recipe_owned" in result.remedy


def test_an_interpolated_name_fails_g1_rather_than_reaching_the_resolver() -> None:
    result = _attribute("${{ name }}-with-kerberos")
    assert isinstance(result, Unexplained)
    assert result.kind == "unrecognized-template"


# --- an output built only from extras --------------------------------------


def test_a_metapackage_is_not_explained_by_upstreams_core_dependencies() -> None:
    """A `core: false` output folds in extras and nothing else (DESIGN.md 4)."""
    result = attribute(
        parse_line("requests >=2.21.0"), _index(("pandas",), core=False), OWNED
    )
    assert isinstance(result, Unexplained)
    assert result.kind == "nowhere"


def test_a_metapackage_still_explains_its_listed_extras() -> None:
    result = attribute(
        parse_line("pandas >=1.3.0"), _index(("pandas",), core=False), OWNED
    )
    assert isinstance(result, Provenance)
    assert result.detail == "extra:pandas"


# --- resolution failures are G2's problem, not G1's -------------------------


def test_an_unresolvable_upstream_name_still_attributes() -> None:
    """Otherwise the maintainer is sent to fix the wrong problem.

    The line is upstream's; that it has no conda package is a name-resolution
    stop (G2), and reporting it as "in no upstream version" would point at
    `add_requirements` for something upstream plainly declares.
    """
    upstream = parse_pyproject(
        '[project]\nname = "demo"\ndependencies = ["not-on-conda-forge >=1"]\n'
    )
    index = build_index(upstream, (), _resolver())
    result = attribute(parse_line("not-on-conda-forge >=1"), index, OWNED)
    assert isinstance(result, Provenance)
    assert result.origin == "upstream-core"
    assert result.mapping is None


@pytest.mark.parametrize(
    "text", ["Requests >=2.21.0", "requests>=2.21.0", "  requests   >=2.21.0"]
)
def test_a_line_is_matched_on_its_normalized_name(text: str) -> None:
    result = _attribute(text)
    assert isinstance(result, Provenance)
    assert result.origin == "upstream-core"


def test_a_conda_name_with_an_underscore_is_not_normalized_away() -> None:
    """conda-forge package names are not PEP 503-normalized.

    `config/name-map.yaml` really does map `msal-extensions` to
    `msal_extensions`, and `facebook-business` and `slack-sdk` do the same.
    Normalizing before comparison loses the match and reports a plainly
    upstream dependency as coming from nowhere -- which is exactly what a
    sweep over the corpus caught.
    """
    upstream = parse_pyproject(
        '[project]\nname = "demo"\ndependencies = ["msal-extensions >=1.3.0"]\n'
    )
    layer = MappingLayer("config/name-map.yaml", {"msal-extensions": "msal_extensions"})
    resolver = NameResolver(
        Layered((layer,)), StaticPackageIndex(frozenset({"msal_extensions"}))
    )
    index = build_index(upstream, (), resolver)
    result = attribute(parse_line("msal_extensions >=1.3.0"), index, OWNED)
    assert isinstance(result, Provenance)
    assert result.origin == "upstream-core"
    assert result.mapping is not None
    assert result.mapping.conda_name == "msal_extensions"


def test_either_spelling_of_a_mapped_name_attributes() -> None:
    """The index carries both, so a recipe written the other way still matches."""
    upstream = parse_pyproject(
        '[project]\nname = "demo"\ndependencies = ["msal-extensions >=1.3.0"]\n'
    )
    layer = MappingLayer("config/name-map.yaml", {"msal-extensions": "msal_extensions"})
    resolver = NameResolver(
        Layered((layer,)), StaticPackageIndex(frozenset({"msal_extensions"}))
    )
    index = build_index(upstream, (), resolver)
    for text in ("msal_extensions >=1.3.0", "msal-extensions >=1.3.0"):
        assert isinstance(attribute(parse_line(text), index, OWNED), Provenance)


def test_host_is_attributed_against_build_system_requires() -> None:
    """`flit-core ==3.12.0` is upstream's own pin, in a different table.

    Indexing the runtime dependencies for `host` reports every build backend
    as coming from nowhere -- 8 of the corpus's recipes, before the sections
    were told apart.
    """
    upstream = parse_pyproject(
        '[project]\nname = "demo"\ndependencies = ["requests >=2"]\n'
        '[build-system]\nrequires = ["flit_core ==3.12.0"]\n'
    )
    resolver = NameResolver(
        Layered(()), StaticPackageIndex(frozenset({"flit-core", "requests"}))
    )
    host = build_index(upstream, (), resolver, section="host")
    result = attribute(parse_line("flit-core ==3.12.0"), host, OWNED)
    assert isinstance(result, Provenance)
    assert result.origin == "upstream-core"


def test_a_runtime_dependency_does_not_explain_a_host_line() -> None:
    upstream = parse_pyproject(
        '[project]\nname = "demo"\ndependencies = ["requests >=2"]\n'
        '[build-system]\nrequires = ["flit_core ==3.12.0"]\n'
    )
    resolver = NameResolver(
        Layered(()), StaticPackageIndex(frozenset({"flit-core", "requests"}))
    )
    host = build_index(upstream, (), resolver, section="host")
    assert isinstance(attribute(parse_line("requests >=2"), host, OWNED), Unexplained)


def test_no_build_system_table_leaves_host_unattributable() -> None:
    """Core metadata carries none, so `host` cannot be reconciled from it."""
    upstream = parse_pyproject('[project]\nname = "demo"\n')
    resolver = NameResolver(Layered(()), StaticPackageIndex(frozenset()))
    host = build_index(upstream, (), resolver, section="host")
    assert host.core == {}


def test_an_embedded_extras_expansion_explains_its_lines() -> None:
    """`pyhive[hive-pure-sasl]` has no conda package; config says what it means."""
    upstream = parse_pyproject(
        '[project]\nname = "demo"\ndependencies = ["pyhive[hive_pure_sasl] >=0.7"]\n'
    )
    expansion: dict[str, tuple[str, ...]] = {
        "pyhive[hive-pure-sasl]": ("pure-sasl >=0.6.2", "thrift_sasl >=0.1.0")
    }
    embedded = Layered(
        (MappingLayer("config/families/airflow-providers.yaml", expansion),)
    )
    resolver = NameResolver(Layered(()), StaticPackageIndex(frozenset({"pyhive"})))
    index = build_index(upstream, (), resolver, embedded_extras=embedded)
    for text in ("pure-sasl >=0.6.2", "thrift_sasl >=0.1.0"):
        result = attribute(parse_line(text), index, OWNED)
        assert isinstance(result, Provenance), text
        assert result.origin == "config-add"
        assert "pyhive[hive-pure-sasl]" in result.detail


def test_every_reason_fences_the_names_it_quotes() -> None:
    """G1 publishes these verbatim in a pull-request comment.

    `gates` joins each `Unexplained.reason` straight into the detail it
    publishes, so this is where that sentence has to be safe to render. The
    matching sweep over the gates themselves is
    `test_no_gate_detail_can_be_eaten_by_markdown`, and the defect both exist
    for was published to a real feedstock -- see that one for the account.
    """
    for text in (
        "pytest >=7",
        "leftpad >=1",
        "google-api-core >=2.25.0",
        "${{ mystery(1) }}",
    ):
        result = _attribute(text)
        assert isinstance(result, Unexplained)
        assert "`" in result.reason, f"{text}: quotes a name unfenced"
        outside_code = re.sub(r"`[^`]*`", "", result.reason)
        assert "*" not in outside_code, f"{text}: bare asterisk in {result.reason}"


def test_a_call_and_an_interpolation_get_different_advice() -> None:
    """`recipe_owned` blesses calls; a bare interpolation it cannot describe.

    `parsl` refers to one of its own outputs as `${{ name }}-with-monitoring`,
    and `recipe_owned()` refuses an interpolated name before it ever consults
    `names` -- so telling that maintainer to "add it to recipe_owned" was
    advice nobody could follow.
    """
    owned = RecipeOwned(functions=("pin_subpackage",), names=("python",))

    call = attribute(parse_line("${{ compiler('c') }}"), AttributionIndex(), owned)
    interpolated = attribute(
        parse_line("${{ name }}-with-monitoring"), AttributionIndex(), owned
    )

    assert isinstance(call, Unexplained)
    assert "add `compiler` to recipe_owned.functions" in call.remedy

    assert isinstance(interpolated, Unexplained)
    assert "recipe_owned" not in interpolated.message
    assert "interpolates rather than calls" in interpolated.remedy


# --- what a finding says, and where it says it -----------------------------


#: Every config key a remedy can name. A finding is published on the
#: feedstock's own pull request, so none of them may appear in one.
CONFIG_KEYS = (
    "add_requirements",
    "temporary_requirements",
    "name_map",
    "recipe_owned",
    "embedded_extras",
)


def test_no_finding_names_a_key_in_swage_config() -> None:
    """The half swage publishes says nothing about swage's own config.

    A G1 finding is rendered as a bullet in the comment swage leaves on the
    feedstock's pull request, under the maintainer's name, on a repository
    whose readers know nothing about this one. `mpas_tools-feedstock#159`
    carried "declare it in add_requirements if conda-forge needs it here" for
    exactly that reason (CLAUDE.md).
    """
    results = (
        _attribute("leftpad >=1.0"),
        _attribute("pytest >=7"),
        _attribute("${{ pin_compatible('numpy') }}"),
        _attribute_section("protobuf >=6.33.5", "host"),
    )

    for result in results:
        assert isinstance(result, Unexplained)
        assert not any(key in result.reason for key in CONFIG_KEYS), result.reason


def test_two_lines_of_one_name_are_told_apart() -> None:
    """`esmf` states `hdf5` three times, and reported three identical findings.

    A name is not an identifier: the same package appears in `host` and in
    `run`, and one section can state it twice with different build strings.
    Both halves of what tells them apart -- the line as the recipe spells it,
    and the section it sits in -- have to be in the sentence.
    """
    plain = _attribute("leftpad")
    pinned = _attribute("leftpad 1.*")
    in_host = _attribute_section("leftpad", "host")

    assert isinstance(plain, Unexplained)
    assert isinstance(pinned, Unexplained)
    assert isinstance(in_host, Unexplained)
    assert plain.reason != pinned.reason
    assert plain.reason != in_host.reason


TWO_OUTPUTS = """\
schema_version: 1

package:
  name: demo
  version: 1.0.0

outputs:
  - package:
      name: demo
    build:
      noarch: python
    requirements:
      run:
        - python
        - leftpad >=1.0
  - package:
      name: demo-extra
    build:
      noarch: python
    requirements:
      run:
        - python
        - leftpad >=1.0
"""


def test_a_finding_names_the_output_its_line_is_in(write_tree: WriteTree) -> None:
    """A section name does not identify a section on a multi-output recipe.

    Two outputs of one recipe state the same line, and `run` is the answer for
    both. The path is what tells them apart, and it is the block's, so it has
    to be carried through the plan rather than reconstructed from the section
    name -- which is how `/requirements/run` would be printed for a line in
    `/outputs/1/requirements/run`.
    """
    tree = load_config(
        write_tree(
            {"defaults.yaml": "trust: never\nrecipe_owned:\n  names: [python]\n"}
        )
    )
    config = tree.for_feedstock("demo")
    plan = plan_recipe(
        read_recipe(TWO_OUTPUTS),
        RecipeUpstream.of(parse_pyproject('[project]\nname = "demo"\n')),
        config,
        NameResolver(config.name_map, StaticPackageIndex.of("leftpad")),
        PythonMin("3.10", "recipe"),
    )

    reasons = [item.reason for item in plan.unexplained]
    assert any("`/outputs/0/requirements/run`" in reason for reason in reasons)
    assert any("`/outputs/1/requirements/run`" in reason for reason in reasons)
