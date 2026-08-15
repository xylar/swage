# Extras

An upstream extra is an optional dependency group — `pip install
google-cloud-bigquery[pandas]`. conda has no such thing, so every extra a
project declares has to become something else in the recipe, and config is
where a feedstock says which.

There are three keys, and they answer three different questions:

- [`extras_as_outputs`](#extras_as_outputs) — this feedstock publishes an
  extra as a **separate output**, a metapackage of its own.
- [`outputs`](#outputs) — this feedstock **folds** an extra into an existing
  output's `run` section.
- [`embedded_extras`](#embedded_extras) — a **dependency of ours** carries an
  extra, and this is what that extra pulls in on conda-forge.

The first two are about the extras of the project being packaged. The third is
about the extras of something it depends on: `httpx[http2]` is a requirement,
not an extra of ours. The two namespaces are unrelated and a key from one never
answers a finding about the other.

## `extras_as_outputs`

Extras published as separate outputs, the way the airflow providers do it.

```yaml
# config/feedstocks/apache-airflow-providers-common-sql.yaml
extras_as_outputs:
  suffix: "{name}-with-{extra}"
  supported:
    - openlineage
    - pandas
    - polars
  # Deliberately not published as outputs. `apache-iceberg` is upstream's
  # `apache.iceberg`, normalized like every extra name swage reads.
  skip:
    - amazon
    - apache-iceberg
    - datafusion
    - pyiceberg-core
    - sqlalchemy
```

`suffix` is how an extra's output is named, with `{name}` the package and
`{extra}` the extra. It is required, because it is what tells swage which
outputs are metapackages over an extra and which are the library itself — and
those are planned differently. An extra's output carries that extra's
dependencies and a pin back to the real package; it takes none of upstream's
own.

`supported` lists the extras that have an output. `skip` lists the ones
deliberately without one, and declaring it is what opts the feedstock into
accounting for every extra from then on.

**Where it goes.** A family may set `suffix` alone as a convention, since which
extras each feedstock publishes differs per feedstock. A feedstock restating
the key **replaces the family's entry whole**, `suffix` included — which is why
`apache-airflow-providers-amazon` repeats a suffix identical to its family's.

**What you see without it.** Every `-with-*` output is planned as though it
took upstream's own dependencies, and swage refuses the feedstock. With the
`supported` list written down but an extra missing from both lists:

```
upstream extra `<extra>` is in neither supported nor skip; add it to one so the
decision is on the record
```

And when upstream stops declaring an extra an output was built from:

```
output built from upstream extra `<extra>`, which <version> no longer declares;
delete the output from the recipe and remove the extra from
extras_as_outputs.supported
```

Both are written here as the shapes rather than as real findings, because no
feedstock in the fleet is held by either today — the lists that would be held
to are exhaustive and upstream has orphaned nothing.

swage never adds or deletes an output. Both are packaging decisions about CI
cost and downstream benefit that no metadata contains.

## `outputs`

Extras folded into an output the recipe already has, the way the google-cloud
family does it.

```yaml
# config/feedstocks/google-cloud-storage.yaml
outputs:
  google-cloud-storage:
    run:
      core: true
      extras:
        - grpc
        - protobuf
      # `testing` is upstream's own test dependencies, which a conda-forge
      # package has no business installing for its users. `tracing` is opt-in
      # instrumentation -- real, but nothing downstream needs it badly enough
      # to put it in everyone's environment.
      skip:
        - tracing
        - testing
```

Keys are output names. `run.core` says whether the output takes upstream's own
dependencies; `run.extras` lists the extras folded into it; `run.skip` records
the ones deliberately left out.

A split feedstock says both things at once — one output for the library, one
metapackage over the extras conda-forge ships:

```yaml
# config/feedstocks/google-cloud-bigquery.yaml
outputs:
  google-cloud-bigquery-core:
    run:
      core: true
  google-cloud-bigquery:
    run:
      core: false
      extras:
        - bqstorage
        - pandas
        - ipywidgets
        # ...
      skip:
        - all
```

**Where it goes.** Family or feedstock, merged per output name, so a family can
describe an output every feedstock in it has and a feedstock can add its own.

An output with no entry takes upstream's own dependencies and no extras, which
is the right answer for most feedstocks — the key is only needed where an extra
is involved.

**What you see without it**, for every line an unlisted extra contributed:

```
`asgiref` comes from upstream extra `async`, which this output does not list;
add the extra so swage maintains the line, or remove the line
```

That is the whole finding on `flask`, twice over: `asgiref` from `async` and
`python-dotenv` from `dotenv`. Either answer is legitimate. Listing the extra
means swage maintains those lines against upstream from then on; removing them
means the conda package stops shipping what the extra provides.

`run.skip` is also how a feedstock that folds extras opts into exhaustiveness,
since `extras_as_outputs.skip` belongs to the other shape and borrowing it
would mean declaring an output-naming suffix on a feedstock that publishes no
extras as outputs at all.

## `embedded_extras`

What a **dependency's** extra pulls in, written out by hand.

```yaml
# config/feedstocks/microsoft-kiota-http.yaml
# conda-forge publishes no package for httpx's http2 extra. It pulls in h2,
# which the recipe lists directly.
embedded_extras:
  "httpx[http2]":
    - h2 >=3,<5
```

The key is the requirement as upstream declares it, extra and all. The value is
the conda-forge packages that extra amounts to.

swage will not derive these. Resolving another project's extras against
conda-forge is work whose wrong answers are indistinguishable from right ones
until something fails to import, so each entry is written by hand — and each
carries the reasoning that produced it, because nothing else can:

```yaml
# config/feedstocks/apache-airflow-providers-google.yaml
# Provider 21.0.0 declares `google-cloud-aiplatform[evaluation]>=1.98.0`.
# conda-forge has `google-cloud-aiplatform` and no package for that extra, so
# the six lines below are what the extra pulls in, written out by hand.
embedded_extras:
  "google-cloud-aiplatform[evaluation]":
    - pandas >=1.0.0
    - scikit-learn <1.6.0
    - jsonschema
    - ruamel.yaml
    - pyyaml
    - litellm >=1.72.4
```

swage renders that block between `# start google-cloud-aiplatform[evaluation]`
and `# end` markers, so where it begins and ends is stated in the recipe rather
than implied by a blank line a linter is free to remove.

**An empty list is a decision, and a different one from an absent key:**

```yaml
# config/feedstocks/weaviate-client.yaml
# setuptools-scm has made TOML support unconditional since version 8, so
# conda-forge's package already carries everything the extra ever meant.
embedded_extras:
  "setuptools_scm[toml]": []
```

Leaving the key out would mean nobody had looked yet, and stops the feedstock.
Writing `[]` says somebody looked and there is nothing to add.

**Where it goes.** Family or feedstock, kept as a stack rather than flattened,
so a lookup reports which file answered. A family entry covers every feedstock
in it — `airflow-providers` carries eight, including `celery[redis]`, which is
the one that added a dependency the recipe was missing altogether.

**What you see without it:**

```
`httpx[http2]` resolved to `httpx`, dropping extra `http2` -- map the
requirement in name_map if conda-forge has a package for it, or write out what
it pulls in under embedded_extras
```

The two remedies are exclusive. If conda-forge publishes a package *for the
extra* — `google-api-core[grpc]` is `google-api-core-grpc` — then it is a
[`name_map`](names.md#name_map) entry keyed on the whole requirement. If it
does not, the extra has to be spelled out here.

Note the extra name in the key. swage normalizes every extra it reads, so an
entry spelled the way `pyproject.toml` spells it — `bigquery_v2`,
`hive_pure_sasl` — never matches, and nothing reports that: it just leaves the
extra unexpanded. Writing `bigquery-v2` and `hive-pure-sasl` is the rule, and
the schema refuses anything else.
