"""What `host` is built with when upstream says nothing (DESIGN.md 3.6.2).

The absent/empty distinction the upstream layer is so careful to keep exists
for exactly one decision, and this is it. `build_requires is None` means
upstream told swage nothing, which PEP 517 already answers -- the project is
built with the legacy setuptools backend. An empty `requires` means upstream
said it needs nothing, which is a different claim and gets nothing.

The rule that matters most is the one about *not* acting: a project naming its
own backend gets what it named, and swage must never quietly put setuptools in
a recipe whose maintainer wrote hatchling. So the tests below check provenance
rather than presence -- a line swage merely *kept* because it will not delete
what it cannot explain (DESIGN.md 3.3.7) looks identical to an added one until
you ask where it came from.
"""

from __future__ import annotations

from swage.config import ConfigTree, load_config
from swage.mapping import NameResolver, StaticPackageIndex
from swage.plan import PlannedSection, PythonMin, plan_section
from swage.recipe import read_recipe
from swage.upstream import UpstreamMetadata, parse_metadata, parse_pyproject

from .conftest import CONFIG_ROOT, WriteTree

PYTHON_MIN = PythonMin("3.10", "test")

#: An sdist with no `pyproject.toml` at all -- the shape of 21 of the fleet's
#: noarch archives, which ship `setup.py` and `setup.cfg` and nothing else.
SETUP_PY_ONLY = parse_metadata(
    "Metadata-Version: 2.1\nName: demo\nVersion: 1.0\nRequires-Dist: requests>=2\n"
)

KNOWN = StaticPackageIndex(
    frozenset({"setuptools", "hatchling", "flit-core", "requests"})
)


def _tree(write_tree: WriteTree, defaults: str = "") -> ConfigTree:
    return load_config(
        write_tree(
            {
                "defaults.yaml": (
                    "trust: manual\nrecipe_owned:\n  names: [python, pip]\n" + defaults
                )
            }
        )
    )


def _plan_host(
    tree: ConfigTree, upstream: UpstreamMetadata, host: str = "setuptools"
) -> PlannedSection:
    recipe = read_recipe(
        "requirements:\n"
        "  host:\n"
        "    - python ${{ python_min }}.*\n"
        "    - pip\n"
        f"    - {host}\n"
        "  run:\n"
        "    - python >=${{ python_min }}\n"
    )
    config = tree.for_feedstock("demo")
    return plan_section(
        recipe.outputs[0].blocks["host"],
        upstream,
        config,
        NameResolver(config.name_map, KNOWN),
        PYTHON_MIN,
    )


def origins(section: PlannedSection) -> dict[str, str]:
    return {r.text: r.provenance.origin for r in section.requirements}


def test_upstream_silence_means_setuptools(write_tree: WriteTree) -> None:
    """PEP 517's implicit backend, and what all 21 of those recipes say."""
    section = _plan_host(_tree(write_tree), SETUP_PY_ONLY)
    assert origins(section)["setuptools"] == "config-add"


def test_the_line_is_explained_rather_than_merely_kept(write_tree: WriteTree) -> None:
    """Unexplained would fail G1 and park 21 feedstocks in review forever."""
    section = _plan_host(_tree(write_tree), SETUP_PY_ONLY)
    assert section.unexplained == ()
    detail = {r.text: r.provenance.detail for r in section.requirements}["setuptools"]
    # Naming the file is the point: a report has to send someone somewhere.
    assert "config/defaults.yaml" in detail
    assert "default_build_requires" in detail


def test_a_declared_backend_is_never_overridden(write_tree: WriteTree) -> None:
    """The maintainer's answer wins; swage's backup is only for silence."""
    upstream = parse_pyproject(
        '[project]\nname = "demo"\n[build-system]\nrequires = ["hatchling"]\n'
    )
    section = _plan_host(_tree(write_tree), upstream, host="hatchling")
    assert origins(section) == {
        "python ${{ python_min }}.*": "recipe-kept",
        "pip": "recipe-kept",
        "hatchling": "upstream-core",
    }


def test_a_declared_backend_does_not_gain_setuptools(write_tree: WriteTree) -> None:
    """The failure that would matter: swage adding a backend nobody asked for."""
    upstream = parse_pyproject(
        '[project]\nname = "demo"\n[build-system]\nrequires = ["hatchling"]\n'
    )
    section = _plan_host(_tree(write_tree), upstream, host="hatchling")
    assert "setuptools" not in origins(section)


def test_an_empty_build_system_adds_nothing(write_tree: WriteTree) -> None:
    """Needing nothing to build is a different claim from saying nothing.

    The recipe's own line survives, because swage never deletes what it cannot
    account for -- but it survives *unexplained*, so G1 stops the feedstock
    rather than swage inventing a justification for it.
    """
    upstream = parse_pyproject(
        '[project]\nname = "demo"\n[build-system]\nrequires = []\n'
    )
    section = _plan_host(_tree(write_tree), upstream)
    assert origins(section)["setuptools"] == "recipe-kept"
    assert [u.text for u in section.unexplained] == ["setuptools"]


def test_the_backup_is_config_not_code(write_tree: WriteTree) -> None:
    """Changing it is a reviewable commit, which is why it is not hardcoded."""
    tree = _tree(write_tree, "default_build_requires: [flit-core]\n")
    section = _plan_host(tree, SETUP_PY_ONLY, host="flit-core")
    assert origins(section)["flit-core"] == "config-add"


def test_run_is_untouched_by_any_of_this(write_tree: WriteTree) -> None:
    """`[build-system]` is a host concern and must not leak into run."""
    recipe = read_recipe(
        "requirements:\n  run:\n    - python >=${{ python_min }}\n    - requests >=2\n"
    )
    config = _tree(write_tree).for_feedstock("demo")
    section = plan_section(
        recipe.outputs[0].blocks["run"],
        SETUP_PY_ONLY,
        config,
        NameResolver(config.name_map, KNOWN),
        PYTHON_MIN,
    )
    assert "setuptools" not in origins(section)


def test_the_shipped_defaults_say_setuptools() -> None:
    """A claim about `config/`, not about a fixture."""
    config = load_config(CONFIG_ROOT).for_feedstock("demo")
    assert config.default_build_requires == ("setuptools",)
