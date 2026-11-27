"""Tests for reading upstream metadata from a setup.cfg (DESIGN.md §6.2).

The real file is weaviate-client 4.25.0's, whose GitHub tag tarball states
its run dependencies nowhere else.
"""

from __future__ import annotations

import pytest

from swage.upstream import EntryPoint, UpstreamError, parse_setup_cfg

from .conftest import REPO_ROOT

WEAVIATE = REPO_ROOT / "tests" / "corpus" / "setup-cfg" / "weaviate-client"
SETUP_CFG = (WEAVIATE / "setup.cfg").read_text(encoding="utf-8")


def parse(text: str) -> object:
    return parse_setup_cfg(text, "setup.cfg")


def test_weaviate_s_dependencies_are_read_in_upstream_order() -> None:
    """The comment lines inside `install_requires` are dropped and the marker
    after `;` is kept, as setuptools does.
    """
    metadata = parse_setup_cfg(SETUP_CFG)
    assert metadata is not None
    assert metadata.name == "weaviate-client"
    assert metadata.requires_python == ">=3.11"
    assert [r.raw for r in metadata.dependencies] == [
        "httpx>=0.26.0,<0.29.0",
        "certifi>=2026.10.19",
        "validators>=0.34.0,<1.0.0",
        "authlib>=1.7.1,<2.0.0",
        "pydantic>=2.12.0,<3.0.0",
        'grpcio>=1.59.5,<2.0.0; sys_platform != "emscripten"',
        "protobuf>=4.21.6,<8.0.0",
        "packaging>=21.0",
    ]
    assert metadata.dependencies[5].marker == 'sys_platform != "emscripten"'


def test_weaviate_s_extras_are_normalized() -> None:
    metadata = parse_setup_cfg(SETUP_CFG)
    assert metadata is not None
    assert metadata.extras == ("agents", "grpc-web")
    assert [r.name for r in metadata.optional_dependencies["grpc-web"]] == [
        "weaviate-client-web"
    ]


def test_a_version_setuptools_scm_computes_is_not_stated() -> None:
    """weaviate's `[metadata]` has no version; `attr:` names where one is."""
    metadata = parse_setup_cfg(SETUP_CFG)
    assert metadata is not None
    assert metadata.version is None

    computed = parse_setup_cfg("[metadata]\nname = x\nversion = attr: x.__version__\n")
    assert computed is not None and computed.version is None
    literal = parse_setup_cfg("[metadata]\nname = x\nversion = 1.2\n")
    assert literal is not None and literal.version == "1.2"


def test_a_setup_cfg_naming_no_project_is_not_a_declaration() -> None:
    """Most fleet archives ship one for flake8 or pytest alone."""
    assert parse_setup_cfg("[flake8]\nmax-line-length = 88\n") is None


def test_a_project_with_no_install_requires_declares_none() -> None:
    metadata = parse_setup_cfg("[metadata]\nname = x\n")
    assert metadata is not None
    assert metadata.dependencies == ()
    assert metadata.entry_points == ()


def test_a_one_line_value_splits_on_semicolons() -> None:
    """setuptools' rule for a requirement list written on one line."""
    metadata = parse_setup_cfg(
        "[metadata]\nname = x\n[options]\ninstall_requires = a; b>=1\n"
    )
    assert metadata is not None
    assert [r.raw for r in metadata.dependencies] == ["a", "b>=1"]


def test_the_dashed_spelling_of_a_key_is_read() -> None:
    metadata = parse_setup_cfg(
        "[metadata]\nname = x\n[options]\npython-requires = >=3.10\n"
        "install-requires =\n    a\n"
    )
    assert metadata is not None
    assert metadata.requires_python == ">=3.10"
    assert [r.name for r in metadata.dependencies] == ["a"]


def test_console_and_gui_scripts_are_read() -> None:
    metadata = parse_setup_cfg(
        "[metadata]\nname = x\n[options.entry_points]\n"
        "console_scripts =\n    x = x.cli:main\n    y = x.cli:other [extra]\n"
        "gui_scripts =\n    z = x.gui:main\n"
        "x.plugins =\n    p = x.plugin\n"
    )
    assert metadata is not None
    assert metadata.entry_points == (
        EntryPoint("x", "x.cli:main"),
        EntryPoint("y", "x.cli:other"),
        EntryPoint("z", "x.gui:main"),
    )


def test_a_requirement_list_read_from_a_file_is_refused() -> None:
    with pytest.raises(UpstreamError, match=r"file: requirements\.txt"):
        parse_setup_cfg(
            "[metadata]\nname = x\n[options]\n"
            "install_requires = file: requirements.txt\n"
        )


def test_an_unparseable_requirement_names_the_key() -> None:
    with pytest.raises(UpstreamError, match=r"\[options\] install_requires"):
        parse_setup_cfg(
            "[metadata]\nname = x\n[options]\ninstall_requires = a >= >= 1\n"
        )


def test_two_spellings_of_one_extra_are_refused() -> None:
    with pytest.raises(UpstreamError, match="same extra"):
        parse_setup_cfg(
            "[metadata]\nname = x\n[options.extras_require]\na_b = c\na-b = d\n"
        )


def test_a_file_configparser_cannot_read_is_an_error() -> None:
    with pytest.raises(UpstreamError, match=r"cannot read as setup\.cfg"):
        parse_setup_cfg("name = x\n")
