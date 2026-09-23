"""`run_constraints` tests (design-v1.md 3.3.9; DESIGN.md §9.7).

swage never adds an entry even where an upstream extra would obviously suggest
one, and never removes one. What it does is notice the entry upstream declares
only under an extra and say so, because the two mean different things: an extra
is opted into, a run constraint binds every environment holding the package.
The first rule deserves the hardest guard, because "upstream declares an extra,
so emit a constraint" is exactly the plausible-looking behavior it prevents.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from swage.config import Feedstock, Layered, RunConstraint
from swage.mapping import NameResolver, StaticPackageIndex
from swage.plan import transcribed_extras
from swage.upstream import RecipeUpstream, UpstreamMetadata, UpstreamRequirement


def _upstream(**extras: tuple[str, ...]) -> RecipeUpstream:
    """One release declaring each named extra over the packages given."""
    return RecipeUpstream.of(
        UpstreamMetadata(
            "demo",
            optional_dependencies={
                extra: tuple(UpstreamRequirement(name=name) for name in names)
                for extra, names in extras.items()
            },
        )
    )


def _resolver(*names: str) -> NameResolver:
    """Names conda-forge has, resolved to themselves. A name it does not have
    falls back to the name upstream used, which is the safe direction: an
    entry swage cannot map simply is not noticed."""
    return NameResolver(Layered(()), StaticPackageIndex.of(*names))


def test_an_entry_upstream_declares_only_under_an_extra_is_noticed() -> None:
    found = transcribed_extras(
        ("cryptography >=3.4",), {}, _upstream(crypto=("cryptography",)), _resolver()
    )
    assert [(f.name, f.extra) for f in found] == [("cryptography", "crypto")]


def test_an_entry_no_extra_declares_is_left_alone() -> None:
    """`gdal`'s `libgdal` lockstep and `proj.4`'s retired name are this: a
    deliberate bound that transcribes nothing, and swage has nothing to say
    about it."""
    found = transcribed_extras(
        ("libgdal 3.11.*",), {}, _upstream(crypto=("cryptography",)), _resolver()
    )
    assert found == ()


def test_naming_the_extra_is_not_deciding_about_it() -> None:
    """`extra: <name>` says which kind of entry it is, which is the kind swage
    keeps reporting. `pyjwt` and `grpc-interceptor` are both this."""
    found = transcribed_extras(
        ("cryptography >=3.4",),
        {"cryptography": RunConstraint(extra="crypto")},
        _upstream(crypto=("cryptography",)),
        _resolver(),
    )
    assert [(f.name, f.extra) for f in found] == [("cryptography", "crypto")]


def test_a_deliberate_bound_tracking_nothing_is_silent() -> None:
    """`extra: null` is `gdal`'s `libgdal` lockstep and `proj.4`'s retired
    name: nothing upstream is behind them, so there is nothing to report."""
    found = transcribed_extras(
        ("libgdal 3.11.*",),
        {"libgdal": RunConstraint()},
        _upstream(crypto=("cryptography",)),
        _resolver(),
    )
    assert found == ()


def test_keep_records_the_decision_and_quiets_it() -> None:
    """The only way to stop swage reporting a transcribed entry, and it takes
    a reason, so the decision is on the record rather than merely made."""
    found = transcribed_extras(
        ("cryptography >=3.4",),
        {"cryptography": RunConstraint(extra="crypto", keep="downstream relies on it")},
        _upstream(crypto=("cryptography",)),
        _resolver(),
    )
    assert found == ()


def test_config_names_an_extra_the_reading_missed() -> None:
    """A name swage cannot map resolves to nothing and would go unnoticed;
    config saying which extra it is settles what the reading could not."""
    found = transcribed_extras(
        ("py-cryptography >=3.4",),
        {"py-cryptography": RunConstraint(extra="crypto")},
        _upstream(crypto=("cryptography",)),
        _resolver(),
    )
    assert [(f.name, f.extra) for f in found] == [("py-cryptography", "crypto")]


def test_every_such_entry_is_named_in_recipe_order() -> None:
    """A maintainer rewrites the block in one pass, so all of them are named."""
    upstream = _upstream(doh=("httpx", "h2"), doq=("aioquic",))
    found = transcribed_extras(
        ("aioquic >=1", "libgdal 3.*", "httpx >=0.26"), {}, upstream, _resolver()
    )
    assert [(f.name, f.extra) for f in found] == [
        ("aioquic", "doq"),
        ("httpx", "doh"),
    ]


def test_the_first_extra_declaring_a_package_is_the_one_named() -> None:
    """Naming both would be about which extra; the point is the practice."""
    upstream = _upstream(first=("httpx",), second=("httpx",))
    found = transcribed_extras(("httpx >=0.26",), {}, upstream, _resolver())
    assert [f.extra for f in found] == ["first"]


def test_an_association_matches_either_spelling_of_a_conda_name() -> None:
    """conda names are not PEP 503-normalized; config should still be found."""
    upstream = _upstream(azure=("msal_extensions",))
    found = transcribed_extras(
        ("msal_extensions >=1.3",),
        {"msal-extensions": RunConstraint()},
        upstream,
        _resolver(),
    )
    assert found == ()


def test_an_empty_section_is_nothing_to_say() -> None:
    upstream = _upstream(crypto=("cryptography",))
    assert transcribed_extras((), {}, upstream, _resolver()) == ()


def test_a_run_constraints_association_is_schema_validated() -> None:
    entry = Feedstock.model_validate(
        {
            "feedstock": "demo",
            "run_constraints": {"pandas": {"extra": "pandas"}, "jinja2": {}},
        }
    )
    assert entry.run_constraints["pandas"].extra == "pandas"
    assert entry.run_constraints["jinja2"].extra is None


def test_an_association_naming_a_non_normalized_extra_is_refused() -> None:
    """Same rule as everywhere else: the extra must be spelled as swage reads it."""
    with pytest.raises(ValidationError, match="write 'bigquery-v2'"):
        Feedstock.model_validate(
            {"feedstock": "demo", "run_constraints": {"x": {"extra": "bigquery_v2"}}}
        )
