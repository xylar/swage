"""A recipe line spelled the way upstream spells it (DESIGN.md 3.2.2).

The tools swage replaces did not resolve names, so the fleet's recipes are full
of lines written under upstream's spelling where conda-forge publishes the
package as something else: `pyOpenSSL` for `pyopenssl`, `psycopg2-binary` for
`psycopg2`. swage resolves the requirement and renders the conda name, which
leaves the recipe's line to be recognized as *the same requirement* or not.

Getting that wrong is quiet in both directions and neither is caught by a gate,
which is why both are tested here:

- where the two spellings normalize alike, the line attributes to the same
  upstream declaration, so **both lines carry a `Provenance`** -- G1 satisfied,
  G2 satisfied, and one requirement rendered twice.
- where they do not, the line attributes to nothing and G1 stops the
  feedstock -- correctly, but pointing at `add_requirements` for a dependency
  upstream declares outright, which is the confidently-wrong advice
  DESIGN.md 3.3.10 exists to prevent.
"""

from __future__ import annotations

from swage.config import ConfigTree, Layered, MappingLayer, load_config
from swage.mapping import NameResolver, StaticPackageIndex
from swage.plan import PlannedRequirement, PlannedSection, PythonMin, plan_section
from swage.recipe import read_recipe
from swage.upstream import parse_pyproject

from .conftest import WriteTree

PYTHON_MIN = PythonMin("3.10", "recipe")

INDEX = StaticPackageIndex.of(
    "hdf5",
    "psycopg2",
    "psycopg2-binary",
    "pyopenssl",
    "python",
)

#: Both renames the fleet actually carries. `pyOpenSSL` differs from its conda
#: name only by case, so PEP 503 normalization finds it; `psycopg2-binary` is a
#: different string entirely *and* a package conda-forge really publishes, so
#: nothing connects the two but the mapping.
NAME_MAP = MappingLayer(
    "config/name-map.yaml",
    {"pyOpenSSL": "pyopenssl", "psycopg2-binary": "psycopg2"},
)

DEFAULTS = "trust: never\nrecipe_owned:\n  names: [python, pip]\n"


def _resolver() -> NameResolver:
    return NameResolver(Layered((NAME_MAP,)), INDEX)


def _config(write_tree: WriteTree) -> ConfigTree:
    return load_config(
        write_tree(
            {"defaults.yaml": DEFAULTS, "feedstocks/demo.yaml": "feedstock: demo\n"}
        )
    )


def _section(
    write_tree: WriteTree, recipe_text: str, upstream_text: str
) -> PlannedSection:
    recipe = read_recipe(recipe_text)
    return plan_section(
        recipe.blocks["/requirements/run"],
        parse_pyproject(upstream_text),
        _config(write_tree).for_feedstock("demo"),
        _resolver(),
        PYTHON_MIN,
    )


def _recipe(*lines: str) -> str:
    body = "".join(f"    - {line}\n" for line in lines)
    return (
        "schema_version: 1\n\npackage:\n  name: demo\n  version: 2.0.0\n\n"
        f"requirements:\n  run:\n    - python >=${{{{ python_min }}}}\n{body}"
    )


def _upstream(*requirements: str) -> str:
    listed = ", ".join(f'"{text}"' for text in requirements)
    return f'[project]\nname = "demo"\nversion = "2.0.0"\ndependencies = [{listed}]\n'


def test_a_line_under_upstreams_spelling_is_renamed_not_duplicated(
    write_tree: WriteTree,
) -> None:
    """One requirement, one line -- under the name conda-forge publishes."""
    section = _section(
        write_tree, _recipe("pyOpenSSL >=22.1.0"), _upstream("pyOpenSSL >=22.1.0")
    )

    assert [item.text for item in section.requirements] == [
        "python >=${{ python_min }}",
        "pyopenssl >=22.1.0",
    ]


def test_a_note_above_the_old_spelling_moves_to_the_line_that_replaces_it(
    write_tree: WriteTree,
) -> None:
    """The comment is about the dependency, and the dependency is still here.

    DESIGN.md 6.1 anchors a preserved comment to the requirement below it. A
    rename is the one case where that requirement is rendered under a different
    name, so a comment keyed on the recipe's spelling would be dropped as
    though the line had been removed.
    """
    note = "# conda-forge builds this against openssl 3"
    recipe = _recipe("pyOpenSSL >=22.1.0").replace(
        "    - pyOpenSSL", f"    {note}\n    - pyOpenSSL"
    )
    section = _section(write_tree, recipe, _upstream("pyOpenSSL >=22.1.0"))

    renamed = next(item for item in section.requirements if item.name == "pyopenssl")
    assert renamed.comments == (note,)


def test_a_rename_across_spellings_still_renders_the_conda_name_once(
    write_tree: WriteTree,
) -> None:
    """`psycopg2-binary` is a package of its own, so the old line is kept too.

    swage does not delete a line it cannot account for (DESIGN.md 3.3.7), and
    conda-forge really does publish `psycopg2-binary`, so the recipe may mean
    it. Both lines are therefore in the section and a human decides -- but the
    reconciled one is rendered exactly once, under the name the requirement
    resolved to.
    """
    section = _section(
        write_tree,
        _recipe("psycopg2-binary >=2.9.10"),
        _upstream("psycopg2-binary >=2.9.10"),
    )

    assert [item.text for item in section.requirements].count("psycopg2 >=2.9.10") == 1
    assert "psycopg2-binary >=2.9.10" in [item.text for item in section.requirements]


def test_the_kept_line_is_reported_with_the_remedy_that_fits(
    write_tree: WriteTree,
) -> None:
    """Upstream declares this name outright, so `add_requirements` is wrong.

    Same verdict as a dependency that came from nowhere and the opposite
    advice, which is the distinction DESIGN.md 3.3.10 is built around. Pointing
    at `add_requirements` here would convert a line swage maintains into one
    nobody does.
    """
    section = _section(
        write_tree,
        _recipe("psycopg2-binary >=2.9.10"),
        _upstream("psycopg2-binary >=2.9.10"),
    )

    reported = section.unexplained[0]
    assert reported.kind == "renamed"
    assert "`psycopg2-binary" in reported.reason
    assert "`psycopg2`" in reported.reason
    assert "name_map" in reported.remedy
    assert "add_requirements" not in reported.message


def test_a_bare_line_beside_an_extra_names_the_requirement_it_came_from(
    write_tree: WriteTree,
) -> None:
    """`google-api-core` is upstream's base name, not a misspelling.

    The recipe carries the plain line because grayskull dropped the extra
    (DESIGN.md 3.2). Naming only the conda name would leave the reader looking
    for a `google-api-core` upstream never declares on its own.
    """
    resolver = NameResolver(
        Layered(
            (
                MappingLayer(
                    "config/name-map.yaml",
                    {"google-api-core[grpc]": "google-api-core-grpc"},
                ),
            )
        ),
        StaticPackageIndex.of("google-api-core", "google-api-core-grpc", "python"),
    )
    recipe = read_recipe(_recipe("google-api-core >=2.25.0"))
    section = plan_section(
        recipe.blocks["/requirements/run"],
        parse_pyproject(_upstream("google-api-core[grpc] >=2.25.0")),
        _config(write_tree).for_feedstock("demo"),
        resolver,
        PYTHON_MIN,
    )

    reported = section.unexplained[0]
    assert reported.kind == "renamed"
    assert "`google-api-core[grpc]`" in reported.reason
    assert "`google-api-core-grpc`" in reported.reason


def test_a_pinned_line_answers_an_upstream_declaration(
    write_tree: WriteTree,
) -> None:
    """A build string is conda-forge's, and upstream has no way to say one.

    So `hdf5` upstream and `hdf5 * ${{ mpi_prefix }}_*` in the recipe are one
    requirement written two ways. Rendered as two, the recipe grows an unpinned
    copy of a library it pins on purpose -- which in an mpi build resolves
    against the wrong variant, and which no gate catches: both lines attribute
    to the same declaration.
    """
    section = _section(
        write_tree,
        _recipe("hdf5 * ${{ mpi_prefix }}_*"),
        _upstream("hdf5"),
    )

    assert [item.text for item in section.requirements] == [
        "python >=${{ python_min }}",
        "hdf5 * ${{ mpi_prefix }}_*",
    ]


def test_an_upstream_bound_lands_in_the_version_field(
    write_tree: WriteTree,
) -> None:
    """A conda match spec is three fields, and the pin is in the third.

    The line the recipe wrote states no version because conda-forge's variants
    supply one; a bound upstream does state belongs in the version field, with
    the build string left where it was.
    """
    section = _section(
        write_tree, _recipe("hdf5 * ${{ mpi_prefix }}_*"), _upstream("hdf5 >=1.14.2")
    )

    assert [item.text for item in section.requirements] == [
        "python >=${{ python_min }}",
        "hdf5 >=1.14.2 ${{ mpi_prefix }}_*",
    ]


def test_both_spellings_survive_where_the_recipe_states_both(
    write_tree: WriteTree,
) -> None:
    """`esmf` states each library twice on purpose (DESIGN.md 3.3.6).

    One line takes its version from conda-forge's variants and the other its
    build from the mpi variant, and the recipe says so in a comment above them.
    The plain line answers upstream's declaration first, so the pinned one is
    kept beside it rather than taking it over.
    """
    section = _section(
        write_tree, _recipe("hdf5", "hdf5 * ${{ mpi_prefix }}_*"), _upstream("hdf5")
    )

    assert [item.text for item in section.requirements] == [
        "python >=${{ python_min }}",
        "hdf5",
        "hdf5 * ${{ mpi_prefix }}_*",
    ]


def test_a_pinned_line_does_not_take_over_a_line_the_recipe_wrote(
    write_tree: WriteTree,
) -> None:
    """The plan holds upstream's entries and the recipe's kept lines together.

    Upstream declares neither of these, so `hdf5` is in the plan only because
    the recipe states it and swage does not delete what it cannot explain.
    Reading that as an entry to take over deleted the line -- caught on `esmf`,
    where `hdf5` is exactly this case.
    """
    section = _section(
        write_tree, _recipe("hdf5", "hdf5 * ${{ mpi_prefix }}_*"), _upstream()
    )

    assert [item.text for item in section.requirements] == [
        "python >=${{ python_min }}",
        "hdf5",
        "hdf5 * ${{ mpi_prefix }}_*",
    ]


def test_an_added_line_with_a_build_string_is_not_a_second_requirement(
    write_tree: WriteTree,
) -> None:
    """The same defect from the other direction: config, not a rename.

    An `add_requirements` entry filed under the bare name lands beside the
    recipe line it explains rather than on top of it, because a line carrying
    a build string is keyed with one. Both are then rendered, and nothing
    downstream notices: config explains one, upstream explains neither, and G1
    is satisfied for both. `e3sm_diags` shipped that way and `esmpy` reproduced
    it the moment it got an entry of its own.
    """
    root = write_tree(
        {
            "defaults.yaml": DEFAULTS,
            "feedstocks/demo.yaml": (
                "feedstock: demo\n"
                "add_requirements:\n"
                "  run:\n"
                "    - line: esmf ==8.8.0 nompi_*\n"
                "      reason: the library this wraps, which is not on PyPI\n"
            ),
        }
    )
    recipe = read_recipe(_recipe("esmf ==8.8.0 nompi_*"))
    section = plan_section(
        recipe.blocks["/requirements/run"],
        parse_pyproject(_upstream()),
        load_config(root).for_feedstock("demo"),
        NameResolver(Layered((NAME_MAP,)), StaticPackageIndex.of("esmf", "python")),
        PYTHON_MIN,
    )

    written = [entry.text for entry in section.requirements]
    assert written.count("esmf ==8.8.0 nompi_*") == 1


def test_an_entry_for_a_conditional_line_explains_it_rather_than_adding_one(
    write_tree: WriteTree,
) -> None:
    """Declaring a dependency must not refuse the section that states it.

    `cassandra-driver` builds `libev` on everything but Windows. An
    `add_requirements` entry for it used to manufacture a plain line beside the
    conditional one, so the plan asked for the dependency unconditionally,
    swage concluded it would delete a condition it did not author, and refused
    `/requirements/host` outright -- turning a feedstock it could plan into one
    it would not touch.
    """
    root = write_tree(
        {
            "defaults.yaml": DEFAULTS,
            "feedstocks/demo.yaml": (
                "feedstock: demo\n"
                "add_requirements:\n"
                "  run:\n"
                "    - line: libev\n"
                "      reason: the event loop extension builds against it\n"
            ),
        }
    )
    recipe = read_recipe(
        "schema_version: 1\n\npackage:\n  name: demo\n  version: 2.0.0\n\n"
        "requirements:\n  run:\n    - python >=${{ python_min }}\n"
        "    - if: not win\n      then: libev\n"
    )
    section = plan_section(
        recipe.blocks["/requirements/run"],
        parse_pyproject(_upstream()),
        load_config(root).for_feedstock("demo"),
        NameResolver(Layered((NAME_MAP,)), StaticPackageIndex.of("libev", "python")),
        PYTHON_MIN,
    )

    # No plain line was manufactured beside the conditional one...
    assert [entry.text for entry in section.requirements] == [
        "python >=${{ python_min }}"
    ]
    # ...the condition the recipe states is still there, once...
    conditionals = [
        entry for entry in section.entries if not isinstance(entry, PlannedRequirement)
    ]
    assert len(conditionals) == 1
    # ...and the entry did its other job: the line inside it is accounted for.
    assert section.unexplained == ()
