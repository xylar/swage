"""Tests for the quirks database loader (DESIGN.md 4).

The loader's job is to merge three layers without losing track of which layer
said what, and to fail legibly when a file is wrong. Both halves are tested
here against trees built on the fly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from swage.config import ConfigError, find_config_root, load_config

from .conftest import WriteTree

DEFAULTS = "trust: never\nrecipe_owned:\n  names: [python, pip]\n"


def test_feedstock_without_a_file_inherits_its_family(write_tree: WriteTree) -> None:
    """Most of the ~490 feedstocks will never have a file of their own."""
    root = write_tree(
        {
            "defaults.yaml": DEFAULTS,
            "families/demo.yaml": (
                "family: demo\n"
                "match:\n"
                '  feedstock: "demo-*"\n'
                "trust: propose\n"
                "upstream:\n"
                "  source: archive\n"
            ),
        }
    )
    resolved = load_config(root).for_feedstock("demo-widget")
    assert resolved.family == "demo"
    assert resolved.trust == "propose"


def test_unmatched_feedstock_falls_back_to_defaults(write_tree: WriteTree) -> None:
    root = write_tree({"defaults.yaml": DEFAULTS})
    resolved = load_config(root).for_feedstock("something-else")
    assert resolved.family is None
    assert resolved.trust == "never"
    assert resolved.upstream is None


def test_feedstock_overrides_family(write_tree: WriteTree) -> None:
    root = write_tree(
        {
            "defaults.yaml": DEFAULTS,
            "families/demo.yaml": (
                'family: demo\nmatch:\n  feedstock: "demo-*"\ntrust: propose\n'
            ),
            "feedstocks/demo-widget.yaml": (
                "feedstock: demo-widget\nfamily: demo\ntrust: auto\n"
            ),
        }
    )
    assert load_config(root).for_feedstock("demo-widget").trust == "auto"


def test_name_map_layers_are_ordered_and_carry_provenance(
    write_tree: WriteTree,
) -> None:
    """Resolution is first-match-wins across layers, and says which won."""
    root = write_tree(
        {
            "defaults.yaml": DEFAULTS,
            "name-map.yaml": "docker: docker-py\nshared: from-global\n",
            "families/demo.yaml": (
                "family: demo\n"
                "match:\n"
                '  feedstock: "demo-*"\n'
                "name_map:\n"
                "  shared: from-family\n"
                "  kubernetes: python-kubernetes\n"
            ),
            "feedstocks/demo-widget.yaml": (
                "feedstock: demo-widget\n"
                "family: demo\n"
                "name_map:\n"
                "  shared: from-feedstock\n"
            ),
        }
    )
    name_map = load_config(root).for_feedstock("demo-widget").name_map
    assert [layer.source for layer in name_map.layers] == [
        "config/feedstocks/demo-widget.yaml",
        "config/families/demo.yaml",
        "config/name-map.yaml",
    ]
    assert name_map.lookup("shared") == (
        "from-feedstock",
        "config/feedstocks/demo-widget.yaml",
    )
    assert name_map.lookup("kubernetes") == (
        "python-kubernetes",
        "config/families/demo.yaml",
    )
    assert name_map.lookup("docker") == ("docker-py", "config/name-map.yaml")
    assert name_map.lookup("never-heard-of-it") is None


def test_embedded_extras_empty_list_is_not_absent(write_tree: WriteTree) -> None:
    """Declared-but-empty has to survive the merge as its own answer."""
    root = write_tree(
        {
            "defaults.yaml": DEFAULTS,
            "families/demo.yaml": (
                "family: demo\n"
                "match:\n"
                '  feedstock: "demo-*"\n'
                "embedded_extras:\n"
                '  "aiobotocore[boto3]": []\n'
            ),
        }
    )
    extras = load_config(root).for_feedstock("demo-widget").embedded_extras
    assert "aiobotocore[boto3]" in extras
    assert extras.lookup("aiobotocore[boto3]") == ((), "config/families/demo.yaml")
    assert extras.lookup("pandas[sql-other]") is None


def test_unknown_key_is_an_error_with_a_line_number(write_tree: WriteTree) -> None:
    """A typo is a startup error with a line number, not a silent no-op."""
    root = write_tree(
        {
            "defaults.yaml": DEFAULTS,
            # `trust` misspelled on line 4.
            "families/demo.yaml": (
                "family: demo\n"
                "match:\n"
                '  feedstock: "demo-*"\n'
                "trsut: propose\n"
                "upstream:\n"
                "  source: archive\n"
            ),
        }
    )
    with pytest.raises(ConfigError) as excinfo:
        load_config(root)
    assert excinfo.value.line == 4
    assert "trsut" in str(excinfo.value)


def test_nested_error_points_at_the_nested_line(write_tree: WriteTree) -> None:
    root = write_tree(
        {
            "defaults.yaml": DEFAULTS,
            "families/demo.yaml": (
                "family: demo\n"
                "match:\n"
                '  feedstock: "demo-*"\n'
                "upstream:\n"
                "  source: github\n"
                "  repo: apache/airflow\n"
                '  tag: "providers-{slug}/{version}"\n'
            ),
        }
    )
    with pytest.raises(ConfigError) as excinfo:
        load_config(root)
    # `metadata` is missing from the github upstream block, which starts at 4.
    assert excinfo.value.line == 4
    assert "metadata" in str(excinfo.value)


def test_schema_errors_name_the_file_they_came_from(write_tree: WriteTree) -> None:
    root = write_tree(
        {
            "defaults.yaml": DEFAULTS,
            "feedstocks/demo-widget.yaml": (
                "feedstock: demo-widget\n"
                "extras_as_outputs:\n"
                '  suffix: "{name}-with-{extra}"\n'
                "  supported: [pandas]\n"
                "  skip: [pandas]\n"
            ),
        }
    )
    with pytest.raises(ConfigError, match="both 'supported' and 'skip'") as excinfo:
        load_config(root)
    assert excinfo.value.path.name == "demo-widget.yaml"


def test_invalid_yaml_reports_its_line(write_tree: WriteTree) -> None:
    root = write_tree({"defaults.yaml": "trust: never\n  bad: indent\n"})
    with pytest.raises(ConfigError) as excinfo:
        load_config(root)
    assert excinfo.value.line is not None
    assert "invalid YAML" in str(excinfo.value)


def test_missing_defaults_is_an_error(write_tree: WriteTree) -> None:
    root = write_tree({"name-map.yaml": "docker: docker-py\n"})
    with pytest.raises(ConfigError, match="required config file is missing"):
        load_config(root)


def test_filename_must_match_the_declared_name(write_tree: WriteTree) -> None:
    root = write_tree(
        {
            "defaults.yaml": DEFAULTS,
            "families/demo.yaml": 'family: nope\nmatch:\n  feedstock: "demo-*"\n',
        }
    )
    with pytest.raises(ConfigError, match="family is 'nope'"):
        load_config(root)


def test_unknown_family_reference_is_an_error(write_tree: WriteTree) -> None:
    root = write_tree(
        {
            "defaults.yaml": DEFAULTS,
            "feedstocks/demo-widget.yaml": "feedstock: demo-widget\nfamily: ghost\n",
        }
    )
    with pytest.raises(ConfigError, match="unknown family 'ghost'"):
        load_config(root)


def test_two_matching_families_is_an_error(write_tree: WriteTree) -> None:
    """Families do not compose, so an ambiguous match must not pick one."""
    root = write_tree(
        {
            "defaults.yaml": DEFAULTS,
            "families/demo.yaml": 'family: demo\nmatch:\n  feedstock: "demo-*"\n',
            "families/widgets.yaml": (
                'family: widgets\nmatch:\n  feedstock: "*-widget"\n'
            ),
        }
    )
    tree = load_config(root)
    with pytest.raises(ConfigError, match="matches several families"):
        tree.for_feedstock("demo-widget")


def test_declared_family_conflicting_with_a_glob_is_an_error(
    write_tree: WriteTree,
) -> None:
    root = write_tree(
        {
            "defaults.yaml": DEFAULTS,
            "families/demo.yaml": 'family: demo\nmatch:\n  feedstock: "demo-*"\n',
            "families/widgets.yaml": (
                'family: widgets\nmatch:\n  feedstock: "*-widget"\n'
            ),
            "feedstocks/demo-widget.yaml": "feedstock: demo-widget\nfamily: demo\n",
        }
    )
    with pytest.raises(ConfigError, match="belongs to one family"):
        load_config(root)


def test_declared_family_need_not_match_the_glob(write_tree: WriteTree) -> None:
    """An explicit `family:` is how a feedstock joins a family it doesn't match."""
    root = write_tree(
        {
            "defaults.yaml": DEFAULTS,
            "families/demo.yaml": 'family: demo\nmatch:\n  feedstock: "demo-*"\n',
            "feedstocks/oddball.yaml": "feedstock: oddball\nfamily: demo\n",
        }
    )
    assert load_config(root).for_feedstock("oddball").family == "demo"


def test_outputs_merge_per_output_name(write_tree: WriteTree) -> None:
    root = write_tree(
        {
            "defaults.yaml": DEFAULTS,
            "families/demo.yaml": (
                "family: demo\n"
                "match:\n"
                '  feedstock: "demo-*"\n'
                "outputs:\n"
                "  shared:\n"
                "    run:\n"
                "      core: true\n"
            ),
            "feedstocks/demo-widget.yaml": (
                "feedstock: demo-widget\n"
                "family: demo\n"
                "outputs:\n"
                "  own:\n"
                "    run:\n"
                "      core: false\n"
                "      extras: [pandas]\n"
            ),
        }
    )
    outputs = load_config(root).for_feedstock("demo-widget").outputs
    assert set(outputs) == {"shared", "own"}
    assert outputs["shared"].run.core is True
    assert outputs["own"].run.extras == ("pandas",)


def test_find_config_root_walks_up(write_tree: WriteTree) -> None:
    root = write_tree({"defaults.yaml": DEFAULTS})
    nested = root.parent / "a" / "b"
    nested.mkdir(parents=True)
    assert find_config_root(nested) == root.resolve()


def test_find_config_root_without_a_database(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match=r"no config/defaults\.yaml"):
        find_config_root(tmp_path)


def test_missing_config_root_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="config root does not exist"):
        load_config(tmp_path / "nope")


def test_recipe_owned_is_required_of_the_defaults(write_tree: WriteTree) -> None:
    """Without `python` and `pip` blessed, G1 blocks every feedstock there is.

    Load-bearing enough that it is stated in the file rather than defaulted in
    code, where a config commit could not reach it (DESIGN.md 3.3.6).
    """
    with pytest.raises(ConfigError, match="recipe_owned"):
        load_config(write_tree({"defaults.yaml": "trust: never\n"}))


def test_a_feedstock_extends_the_recipe_owned_allowlist(write_tree: WriteTree) -> None:
    """Extending, not replacing -- overriding would un-bless the global set."""
    root = write_tree(
        {
            "defaults.yaml": DEFAULTS,
            "feedstocks/demo.yaml": (
                "feedstock: demo\nrecipe_owned:\n  functions: [cdt]\n"
            ),
        }
    )
    owned = load_config(root).for_feedstock("demo").recipe_owned
    assert owned.functions == ("cdt",)
    assert owned.names == ("python", "pip")


def test_a_family_and_a_feedstock_both_extend_it(write_tree: WriteTree) -> None:
    root = write_tree(
        {
            "defaults.yaml": DEFAULTS,
            "families/fam.yaml": (
                "family: fam\nmatch:\n  feedstock: 'demo*'\n"
                "recipe_owned:\n  functions: [pin_subpackage]\n"
            ),
            "feedstocks/demo.yaml": (
                "feedstock: demo\nfamily: fam\nrecipe_owned:\n  functions: [cdt]\n"
            ),
        }
    )
    owned = load_config(root).for_feedstock("demo").recipe_owned
    assert set(owned.functions) == {"cdt", "pin_subpackage"}
    assert owned.names == ("python", "pip")


def test_a_feedstock_with_no_recipe_owned_gets_the_defaults(
    write_tree: WriteTree,
) -> None:
    root = write_tree(
        {"defaults.yaml": DEFAULTS, "feedstocks/demo.yaml": "feedstock: demo\n"}
    )
    assert load_config(root).for_feedstock("demo").recipe_owned.names == (
        "python",
        "pip",
    )


def test_add_requirements_carries_the_file_that_asked_for_it(
    write_tree: WriteTree,
) -> None:
    """Provenance needs the file, not just the line (DESIGN.md 3.3).

    A `config-add` line is only explained if swage can say which config entry
    explains it, so the source travels with the text.
    """
    root = write_tree(
        {
            "defaults.yaml": DEFAULTS,
            "feedstocks/demo.yaml": (
                "feedstock: demo\nadd_requirements:\n  run:\n    - grpcio-gcp >=0.2.2\n"
            ),
        }
    )
    added = load_config(root).for_feedstock("demo").add_requirements
    assert [(a.text, a.source) for a in added["run"]] == [
        ("grpcio-gcp >=0.2.2", "config/feedstocks/demo.yaml")
    ]
    assert added["host"] == ()


def test_a_family_and_a_feedstock_both_add_requirements(write_tree: WriteTree) -> None:
    """Each may have its own reason; the specific one does not cancel the other."""
    root = write_tree(
        {
            "defaults.yaml": DEFAULTS,
            "families/fam.yaml": (
                "family: fam\nmatch:\n  feedstock: 'demo*'\n"
                "add_requirements:\n  host: [setuptools]\n"
            ),
            "feedstocks/demo.yaml": (
                "feedstock: demo\nfamily: fam\n"
                "add_requirements:\n  host: [wheel]\n  run: [six]\n"
            ),
        }
    )
    added = load_config(root).for_feedstock("demo").add_requirements
    assert {a.text for a in added["host"]} == {"setuptools", "wheel"}
    assert [a.text for a in added["run"]] == ["six"]
