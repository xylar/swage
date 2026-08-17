"""Reading a release's wheel when its sdist declares no dependencies (3.6.2).

The case is real and it is not a corner: an sdist built by setuptools' `sdist`
command carries no `Requires-Dist` unless the project declares its dependencies
declaratively, so a `setup.py` project publishes an sdist that says nothing and
a wheel of the same release that says everything. `alibabacloud-adb20211201`
4.1.0 is the fleet's example, and the two dependencies its recipe carries were
being reported as coming from nowhere.

Nothing here reaches the network; the wheels are built in memory.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile

import pytest

from swage.config import load_config
from swage.forge import ForgeError, fetch_upstream
from swage.forge.wheel import PYPI_JSON, wheel_metadata
from swage.recipe import read_recipe

from .conftest import WriteTree
from .test_forge_archive import make_sdist

NAME, VERSION = "demo", "4.1.0"

SILENT_PKG_INFO = f"""\
Metadata-Version: 2.1
Name: {NAME}
Version: {VERSION}
Summary: a release whose sdist states no dependencies at all
"""

WHEEL_METADATA = f"""\
Metadata-Version: 2.1
Name: {NAME}
Version: {VERSION}
Requires-Dist: alibabacloud-tea-openapi<1.0.0,>=0.4.5
Requires-Dist: darabonba-core<2.0.0,>=1.0.0
"""


def make_wheel(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as wheel:
        for name, text in files.items():
            wheel.writestr(name, text)
    return buffer.getvalue()


WHEEL = make_wheel({f"{NAME}-{VERSION}.dist-info/METADATA": WHEEL_METADATA})
WHEEL_URL = f"https://files.pythonhosted.org/{NAME}-{VERSION}-py3-none-any.whl"


def _release(*urls: dict[str, object]) -> bytes:
    return json.dumps({"urls": list(urls)}).encode()


def _wheel_entry(
    filename: str = f"{NAME}-{VERSION}-py3-none-any.whl",
    url: str = WHEEL_URL,
    payload: bytes = WHEEL,
) -> dict[str, object]:
    return {
        "packagetype": "bdist_wheel",
        "filename": filename,
        "url": url,
        "digests": {"sha256": hashlib.sha256(payload).hexdigest()},
    }


def _fetcher(responses: dict[str, bytes]):  # type: ignore[no-untyped-def]
    def fetch(url: str) -> bytes:
        if url not in responses:
            raise AssertionError(f"unexpected fetch: {url}")
        return responses[url]

    return fetch


JSON_URL = PYPI_JSON.format(name=NAME, version=VERSION)


def test_the_wheels_metadata_is_read() -> None:
    fetch = _fetcher({JSON_URL: _release(_wheel_entry()), WHEEL_URL: WHEEL})
    found = wheel_metadata(NAME, VERSION, fetch)
    assert found is not None
    metadata, filename = found
    assert [r.name for r in metadata.dependencies] == [
        "alibabacloud-tea-openapi",
        "darabonba-core",
    ]
    assert filename.endswith("-py3-none-any.whl")


def test_a_release_with_no_wheel_is_an_answer_rather_than_an_error() -> None:
    """`hdfs` 2.7.3 ships an sdist alone, so there is nowhere else to look."""
    fetch = _fetcher({JSON_URL: _release({"packagetype": "sdist", "url": "x"})})
    assert wheel_metadata(NAME, VERSION, fetch) is None


def test_the_pure_python_wheel_is_preferred() -> None:
    """conda-forge builds one noarch artifact, which is what that wheel is."""
    other = "https://files.pythonhosted.org/demo-4.1.0-cp313-cp313-linux.whl"
    fetch = _fetcher(
        {
            JSON_URL: _release(
                _wheel_entry(filename="demo-4.1.0-cp313-cp313-linux.whl", url=other),
                _wheel_entry(),
            ),
            WHEEL_URL: WHEEL,
        }
    )
    found = wheel_metadata(NAME, VERSION, fetch)
    assert found is not None
    assert found[1].endswith("-py3-none-any.whl")


def test_a_digest_that_does_not_match_the_index_is_refused() -> None:
    """Weaker than the recipe's pin, and still checked."""
    entry = _wheel_entry()
    entry["digests"] = {"sha256": "0" * 64}
    fetch = _fetcher({JSON_URL: _release(entry), WHEEL_URL: WHEEL})
    with pytest.raises(ForgeError, match="does not match the digest PyPI published"):
        wheel_metadata(NAME, VERSION, fetch)


def test_a_wheel_without_metadata_is_refused_rather_than_read_as_empty() -> None:
    payload = make_wheel({"demo/__init__.py": ""})
    fetch = _fetcher(
        {JSON_URL: _release(_wheel_entry(payload=payload)), WHEEL_URL: payload}
    )
    with pytest.raises(ForgeError, match=r"no \.dist-info/METADATA"):
        wheel_metadata(NAME, VERSION, fetch)


def test_a_vendored_metadata_deeper_in_the_wheel_is_not_taken() -> None:
    """Same shallowest-match rule the sdist reader applies to PKG-INFO."""
    payload = make_wheel(
        {
            "demo/_vendor/other-1.0.dist-info/METADATA": (
                "Metadata-Version: 2.1\nName: other\nVersion: 1.0\n"
                "Requires-Dist: wrong\n"
            ),
            f"{NAME}-{VERSION}.dist-info/METADATA": WHEEL_METADATA,
        }
    )
    fetch = _fetcher(
        {JSON_URL: _release(_wheel_entry(payload=payload)), WHEEL_URL: payload}
    )
    found = wheel_metadata(NAME, VERSION, fetch)
    assert found is not None
    assert [r.name for r in found[0].dependencies] != ["wrong"]


# --- what it means for a fetch --------------------------------------------

RECIPE = """\
schema_version: 1

context:
  version: 4.1.0

package:
  name: demo
  version: ${{ version }}

source:
  url: https://example.invalid/demo-4.1.0.tar.gz
  sha256: SHA

requirements:
  run:
    - python
"""


def _fetch_upstream(write_tree: WriteTree, sdist: bytes, responses: dict[str, bytes]):  # type: ignore[no-untyped-def]
    digest = hashlib.sha256(sdist).hexdigest()
    recipe = read_recipe(RECIPE.replace("SHA", digest))
    tree = load_config(
        write_tree(
            {"defaults.yaml": "trust: manual\nrecipe_owned:\n  names: [python, pip]\n"}
        )
    )
    config = tree.for_feedstock("demo")
    return fetch_upstream(
        recipe,
        config,
        None,
        _fetcher({"https://example.invalid/demo-4.1.0.tar.gz": sdist, **responses}),
    )


def test_a_silent_sdist_is_filled_in_from_the_wheel(write_tree: WriteTree) -> None:
    sdist = make_sdist({f"{NAME}-{VERSION}/PKG-INFO": SILENT_PKG_INFO})
    metadata = _fetch_upstream(
        write_tree, sdist, {JSON_URL: _release(_wheel_entry()), WHEEL_URL: WHEEL}
    )
    assert [r.name for r in metadata.dependencies] == [
        "alibabacloud-tea-openapi",
        "darabonba-core",
    ]
    # Recorded, because the recipe pins the sdist and not this file.
    assert metadata.dependency_source.endswith("-py3-none-any.whl")


def test_an_sdist_that_states_its_dependencies_is_left_alone(
    write_tree: WriteTree,
) -> None:
    """Never to correct or extend a list the sdist did state.

    Two distributions of one release disagreeing is a broken release, not
    something for swage to arbitrate unattended -- so the wheel is not even
    fetched, which is what the fetcher asserts by refusing unknown URLs.
    """
    stated = SILENT_PKG_INFO + "Requires-Dist: requests>=2\n"
    sdist = make_sdist({f"{NAME}-{VERSION}/PKG-INFO": stated})
    metadata = _fetch_upstream(write_tree, sdist, {})
    assert [r.name for r in metadata.dependencies] == ["requests"]
    assert metadata.dependency_source == ""


def test_an_sdist_that_only_names_its_extras_is_filled_in(
    write_tree: WriteTree,
) -> None:
    """Naming an extra is not stating a requirement.

    setuptools writes `Provides-Extra` from the keys of `extras_require` and
    `Requires-Dist` only for a project that declares dependencies
    declaratively, so a `setup.py` project with extras publishes a `PKG-INFO`
    that names them and states nothing else. `flask-appbuilder` 5.2.2 is the
    fleet's example: four extras named, no `Requires-Dist`, and 21 runtime
    dependencies in the wheel that its recipe already carries and swage
    reported as coming from nowhere.
    """
    named = SILENT_PKG_INFO + "Provides-Extra: talisman\nProvides-Extra: saml\n"
    sdist = make_sdist({f"{NAME}-{VERSION}/PKG-INFO": named})
    metadata = _fetch_upstream(
        write_tree, sdist, {JSON_URL: _release(_wheel_entry()), WHEEL_URL: WHEEL}
    )
    assert [r.name for r in metadata.dependencies] == [
        "alibabacloud-tea-openapi",
        "darabonba-core",
    ]
    assert metadata.dependency_source.endswith("-py3-none-any.whl")


def test_an_extra_that_states_a_requirement_is_left_alone(
    write_tree: WriteTree,
) -> None:
    """A requirement anywhere is a list swage does not second-guess.

    The narrowing is about silence, not about the core list: an sdist stating
    one dependency under one extra has told swage what it declares, and the
    wheel is not fetched -- which is what the fetcher asserts by refusing
    unknown URLs.
    """
    stated = (
        SILENT_PKG_INFO
        + "Provides-Extra: talisman\n"
        + 'Requires-Dist: flask-talisman>=1.0.0; extra == "talisman"\n'
    )
    sdist = make_sdist({f"{NAME}-{VERSION}/PKG-INFO": stated})
    metadata = _fetch_upstream(write_tree, sdist, {})
    assert metadata.dependencies == ()
    assert [r.name for r in metadata.optional_dependencies["talisman"]] == [
        "flask-talisman"
    ]
    assert metadata.dependency_source == ""


def test_a_release_that_genuinely_needs_nothing_records_no_source(
    write_tree: WriteTree,
) -> None:
    """An empty answer from the wheel confirms the sdist rather than replacing it."""
    bare = make_wheel(
        {
            f"{NAME}-{VERSION}.dist-info/METADATA": (
                f"Metadata-Version: 2.1\nName: {NAME}\nVersion: {VERSION}\n"
            )
        }
    )
    sdist = make_sdist({f"{NAME}-{VERSION}/PKG-INFO": SILENT_PKG_INFO})
    metadata = _fetch_upstream(
        write_tree,
        sdist,
        {JSON_URL: _release(_wheel_entry(payload=bare)), WHEEL_URL: bare},
    )
    assert metadata.dependencies == ()
    assert metadata.dependency_source == ""


def test_the_build_system_still_comes_from_the_archive(write_tree: WriteTree) -> None:
    """Core metadata has no build-system table, so the wheel must not blank it."""
    pyproject = (
        '[build-system]\nrequires = ["flit-core ==3.12.0"]\n'
        '[project]\nname = "demo"\nversion = "4.1.0"\n'
    )
    sdist = make_sdist(
        {
            f"{NAME}-{VERSION}/pyproject.toml": pyproject,
            f"{NAME}-{VERSION}/PKG-INFO": SILENT_PKG_INFO,
        }
    )
    metadata = _fetch_upstream(
        write_tree, sdist, {JSON_URL: _release(_wheel_entry()), WHEEL_URL: WHEEL}
    )
    assert metadata.build_requires is not None
    assert [r.name for r in metadata.build_requires] == ["flit-core"]
    assert [r.name for r in metadata.dependencies] == [
        "alibabacloud-tea-openapi",
        "darabonba-core",
    ]
