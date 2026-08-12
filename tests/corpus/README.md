# Golden-test corpus

Real inputs and real expected outputs, vendored from the working directories of
the two tools swage replaces. They are copied here rather than read from those
directories at test time, because those are live working directories and will
change underneath the tests.

## `airflow-providers/<provider>_<version>/`

Complete triples left behind by
`airflow-feedstock/providers/update_providers.py`:

| File | Role |
|---|---|
| `pyproject.toml` | upstream metadata (input) |
| `old_recipe.yaml` | the recipe before the update (input) |
| `recipe.yaml` | the recipe after the update (expected output) |

Eight of the 89 available triples, chosen for coverage rather than at random:

| Provider | Why it is here |
|---|---|
| `providers-airbyte_6.0.1` | the simple case: single output, no comments |
| `providers-databricks_7.18.1` | comments interleaved between dependencies |
| `providers-common-sql_2.1.0` | multi-output, `# start`/`# end` embedded-extra markers, and trailing `# skipping output` comments |
| `providers-apache-hive_9.6.1` | embedded extras (`pyhive[hive_pure_sasl]`) |
| `providers-celery_3.23.1` | embedded extras |
| `providers-postgres_7.0.0` | embedded extras |
| `providers-cncf-kubernetes_10.21.0` | dependencies that need name mapping |
| `providers-microsoft-azure_14.1.0` | the largest single-output recipe |

## `google-cloud/<feedstock>/recipe.yaml`

Recipes from the feedstock checkouts under `google-cloud/feedstocks/`, which
cover that family. There is no `old_recipe.yaml` here -- for these the "before"
state lives in each feedstock's git history rather than on disk.
`google-cloud-bigquery` is the split-feedstock case: one sdist, two outputs, and
a metapackage whose `run` section is assembled from upstream extras.

Each directory also carries the upstream metadata its recipe was generated
from, taken out of the sdist that recipe's `source.url` names and
`source.sha256` pins — so these are the inputs to that recipe rather than a
reconstruction of them. Every archive's hash was verified against its recipe on
the way in.

**Ten of the eleven ship no `pyproject.toml` at all.** They are setuptools
projects whose sdists carry only `PKG-INFO`, which makes this family the
corpus's only coverage of two rules the airflow triples cannot reach: reading
runtime dependencies out of core metadata (§3.6.2), and `default_build_requires`
supplying `setuptools` to a `host` section upstream says nothing about
(§3.6.4). `google-cloud-bigquery` is the exception and carries both files.

### `google-cloud/google-cloud-bigquery/PKG-INFO` and `pyproject.toml`

Both files as they appear inside the `google-cloud-bigquery` 3.43.0 sdist —
the upstream inputs for the `recipe.yaml` sitting beside them. The sdist's
sha256 is the one that recipe pins, so these are the metadata that recipe was
generated from rather than a reconstruction of it:

```
e3dc25ab9ac8b2b089408493177d4d4508b098c80c3931786fbc20b075298fe6
```

**Both are kept because they disagree**, and the disagreement is the fixture.
For this one release of this one project:

| File | Extra as spelled |
|---|---|
| `pyproject.toml` (`[project.optional-dependencies]`) | `bigquery_v2` |
| `PKG-INFO` (`Provides-Extra:`) | `bigquery-v2` |

Build backends apply PEP 685 when they write core metadata and nothing applies
it to `pyproject.toml`, so an extra's name would otherwise depend on which
file an sdist happened to ship. swage normalizes both on read; keeping the
pair is what lets a test assert the two paths produce the same answer.

This is also the corpus's marker-reconciliation case: `grpcio` appears twice
under the `bqstorage` extra, once gated on `python_version >= "3.14"`, which is
what the recipe's `# more restrictive constraint for python >=3.14` comment
records (DESIGN.md 3.3.1).

## Provenance and licensing

Everything here is vendored unmodified, as test fixtures. swage is BSD-3-Clause;
these files are not, and keep the licences they came with.

- `airflow-providers/*/pyproject.toml` is copied from
  [apache/airflow](https://github.com/apache/airflow), copyright the Apache
  Software Foundation, licensed under Apache-2.0. Each file retains the ASF
  licence header it ships with.
- `airflow-providers/*/old_recipe.yaml` and `airflow-providers/*/recipe.yaml`
  come from the `apache-airflow-providers-*` conda-forge feedstocks,
  BSD-3-Clause.
- `google-cloud/*/recipe.yaml` comes from the `google-cloud-*` conda-forge
  feedstocks, BSD-3-Clause.
- `google-cloud/*/PKG-INFO` is copied from each project's sdist on PyPI,
  copyright Google LLC, licensed under Apache-2.0. Each carries its licence in
  its `License` and `Classifier` headers.
- `google-cloud/google-cloud-bigquery/PKG-INFO` and
  `google-cloud/google-cloud-bigquery/pyproject.toml` are copied from the
  `google-cloud-bigquery` 3.43.0 sdist on PyPI, copyright Google LLC, licensed
  under Apache-2.0. `pyproject.toml` retains the Apache licence header it ships
  with; `PKG-INFO` carries its licence in the `License` and `Classifier`
  headers.
