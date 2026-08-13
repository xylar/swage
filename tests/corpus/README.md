# Golden-test corpus

Real inputs and real expected outputs, vendored from the working directories of
the two tools swage replaces. They are copied here rather than read from those
directories at test time, because those are live working directories and will
change underneath the tests.

`compiled/` is the exception and does not come from those tools at all: it is
recipes from feedstocks that build something other than one `noarch: python`
package, which neither tool ever produced. It carries inputs rather than
triples, and its own section below says what each one is for.

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

## `compiled/<feedstock>/`

Recipes from feedstocks that build at least one architecture-specific package.
Every other fixture in this corpus is `noarch: python`, because both tools swage
replaces only ever produced those — which is a fact about those two tools, not
about conda-forge or about what swage is for.

These are **inputs, not triples.** There is no expected output beside them: each
one is a real recipe on a real feedstock's default branch, carrying a shape
swage has to be able to read before it can have an opinion about it.
`tests/test_corpus_compiled.py` records what each is here to carry, and where
swage stops on it today.

| Entry | What the feedstock builds | Why it is here |
|---|---|---|
| `cprnc` | one compiled program, no Python anywhere | the minimal case: compilers and nothing conditional. swage reads it today, which is what shows being architecture-specific was never the blocker |
| `libnetcdf` | a C library, in `nompi`, `openmpi` and `mpich` variants | `if:`/`then:` entries in `build` and `host`, `pin_subpackage` in `run_exports`, and a variant (`mpi`) that comes from conda-forge's global pinning — so nothing in the feedstock declares it |
| `netcdf-fortran` | the same library's Fortran interface | the same variant, this time declared by the feedstock's own `recipe/conda_build_config.yaml`, which is the one form swage already refuses and should keep refusing. Compilers selected per platform, and conditionals in all three of `build`, `host` and `run` |
| `moab` | C++ and Fortran, over two variant axes (`mpi`, `tempest`) | requirements carrying a build-string selector — `hdf5 [build=${{ mpi_prefix }}_*]` — beside a plain reference to the same package |
| `pyproj` | a compiled Python package | the cross-compilation block (`if: build_platform != target_platform`, then `python`, `cross-python_${{ target_platform }}`, `cython`); a plain `python` in `host` and `run` where a noarch recipe writes `${{ python_min }}`; and a Python floor stated as `skip: match(python, "<3.11")` |
| `python-eccodes` | a compiled Python package over a C library | the same cross-compilation written as four separate one-line `if:` entries instead of one `then:` list, so the reader cannot assume a single shape |
| `snowflake-connector-python` | a compiled Python package with a large PyPI dependency list | the reconciliation case: 18 upstream runtime dependencies and a `run_constraints` entry, on a feedstock that builds one artifact per Python rather than one for all of them |
| `apache-beam` | 12 outputs: a compiled base package and 11 `noarch: python` outputs beside it | the mixed shape, which `python_min` is per output because of (DESIGN.md 3.3.3). Its `.ci_support` variants declare `python_min` precisely because some outputs are noarch, and its extras outputs use the `-with-<extra>` suffix swage's own config models |
| `gdal` | 21 outputs: a cache output with no package name, 18 plugin libraries, a metapackage and the Python bindings | the stress case, at 1013 lines |

Each entry holds the feedstock's `recipe/recipe.yaml`. Three carry more, because
the extra file is what makes the entry say what it says:

| Entry | Also vendored | What it settles |
|---|---|---|
| `netcdf-fortran` | `recipe/conda_build_config.yaml` | the feedstock declares `mpi` with three values, which is what a build-variant refusal is checked against (DESIGN.md 3.3.5) |
| `apache-beam` | one `.ci_support` variant | `python_min` resolves, to 3.10 |
| `pyproj` | one `.ci_support` variant | it does not resolve: **no** `.ci_support` file pyproj renders declares `python_min`, all 26 of them, because a feedstock whose Python is a build variant has no floor to state. The vendored file is one of the 26 |

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
- `compiled/*/` comes from the conda-forge feedstock its directory names,
  BSD-3-Clause, taken from that feedstock's default branch at:

  | Entry | `conda-forge/<feedstock>-feedstock` commit |
  |---|---|
  | `apache-beam` | `0738d3cb1e6032a6ab51213950646f12bc7a4b6a` |
  | `cprnc` | `8ebca9cce367aacbb50a9e19c82b2cfd350c0b6d` |
  | `gdal` | `8745d5c962bbe7d1b259ca489a6bf0daf1e410bc` |
  | `libnetcdf` | `427106b9e03f3ffc6796a09617b3f723c73b49f0` |
  | `moab` | `4b953995b4865d8ab56004ab48d94431758159ed` |
  | `netcdf-fortran` | `e1e15414b46575ce9e373708e72d502485fb486e` |
  | `pyproj` | `ca1f5e2fc6c38a73f8b6fa79ec336737c2a0e982` |
  | `python-eccodes` | `48b23249ff20817a7c4d7bebbbea9f379448c0c8` |
  | `snowflake-connector-python` | `4639d510bb1b96908d540196b9a4222221cced3c` |
- `google-cloud/*/PKG-INFO` is copied from each project's sdist on PyPI,
  copyright Google LLC, licensed under Apache-2.0. Each carries its licence in
  its `License` and `Classifier` headers.
- `google-cloud/google-cloud-bigquery/PKG-INFO` and
  `google-cloud/google-cloud-bigquery/pyproject.toml` are copied from the
  `google-cloud-bigquery` 3.43.0 sdist on PyPI, copyright Google LLC, licensed
  under Apache-2.0. `pyproject.toml` retains the Apache licence header it ships
  with; `PKG-INFO` carries its licence in the `License` and `Classifier`
  headers.
