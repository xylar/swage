# Where metadata comes from

swage reconciles a recipe against the dependencies the release it packages
declares. Where that declaration lives is a per-feedstock fact, and swage does
not guess: config names it.

## `upstream`

Two sources, discriminated by `source`.

### `source: archive`

Read the metadata out of the archive the recipe's own `source.url` pins. Named
for what it reads rather than for where the archive is hosted — a PyPI sdist
and a GitHub release tarball are the same operation.

```yaml
# config/families/google-cloud.yaml
upstream:
  source: archive
```

That is the whole entry for ~50 feedstocks: each recipe already names its
archive, so nothing more has to be said. One optional key exists for the case
where it does:

- **`metadata`** — where inside the archive the metadata is, relative to the
  archive's single top-level directory. `client/python/pyproject.toml`, not
  `OpenLineage-1.40.1/client/python/pyproject.toml`, so the path survives a
  version bump. Only needed for a monorepo tarball, where the file at the root
  describes no package and guessing would reconcile the recipe against a
  different project entirely.

A feedstock with no `upstream` entry at all takes this path, which is why most
of the fleet needs no `upstream` key.

### `source: github`

Read a file out of a git tag. This is the shape for a monorepo that releases
many packages, where the tag rather than an archive is the unit of release.

```yaml
# config/families/airflow-providers.yaml
upstream:
  source: github
  repo: apache/airflow
  # e.g. providers-apache-hive/9.6.1
  tag: "providers-{slug}/{version}"
  # e.g. providers/apache/hive/pyproject.toml
  metadata: "providers/{slug_path}/pyproject.toml"
```

All three keys are required, and `tag` and `metadata` are templates over
exactly three substitutions:

| | |
|---|---|
| `{slug}` | the part of the feedstock name the family's glob matched — `apache-hive` for `apache-airflow-providers-apache-hive` |
| `{slug_path}` | the same, with `-` as a path separator: `apache/hive` |
| `{version}` | the release being reconciled against |

Anything else is a config error naming the file, rather than a fetch of a path
with an unsubstituted `{}` left in it.

**Where it goes.** Almost always a family file: which repository a release
comes from is the thing a family has in common. A feedstock file overrides it
whole.

**What you see when it is wrong.** Nothing about the key itself — swage goes and
reads whatever it names, so a wrong path surfaces as a fetch that failed or, in
the monorepo case, as a recipe reconciled against a different package's
dependencies. `swage draft <feedstock>` writes the metadata it read into the
workbench under `upstream/`, which is the fastest way to confirm it fetched
what you meant.

Metadata that is missing rather than misplaced is reported as a refusal before
planning starts. Nineteen feedstocks in the fleet are C libraries whose source
archives carry no Python metadata at all, and there is no key that changes that
— swage has no reader for what those recipes are built from.

## `default_build_requires`

What `host` is built with where upstream declares no build system at all.

```yaml
# config/defaults.yaml
default_build_requires:
  - setuptools
```

A project with no `[build-system]` table gets setuptools as its implicit
backend, and conda-forge follows that — the recipe still needs something in
`host` to build with. This is strictly a backup for silence: a project that
names hatchling or poetry-core gets what it asked for, and swage never
overrides a maintainer here.

It is written down rather than hardcoded so that changing it is a reviewable
config commit. Across the fleet, 21 noarch sdists ship `setup.py` and
`setup.cfg` with no `pyproject.toml`, and every one of those recipes already
lists exactly `setuptools` — so the key states what the fleet had already
decided rather than imposing anything on it.

**Where it goes.** `defaults.yaml` only.

## `pure_python_build_tools`

Build requirements a cross build takes from the host prefix, so a recipe never
repeats them in `build`.

```yaml
# config/defaults.yaml
pure_python_build_tools:
  - setuptools
  - setuptools-scm
  - wheel
```

A cross-compiled recipe copies part of `host` into its
`build_platform != target_platform` block, because a tool the build has to
*run* has to exist for the build platform. `pyproj` repeats `cython` there and
not `proj`; `python-eccodes` repeats `numpy` and `cffi` and not `findlibs`.

Which requirements belong in that block is a judgement per dependency, and
swage does not make it — a `host` change on such an output is reported so
somebody can check whether the block needs the same edit. This key settles the
other half: the requirements the question does not arise for. They are pure
python, imported by a backend already running under `cross-python_*`, so a
change to one cannot leave a block stale.

The fleet bears that out. `setuptools` sits in the `host` section of 15 of the
19 cross-compiled outputs and exactly one of them repeats it in `build`, while
`cython` is repeated by all 6 outputs that state it and `numpy` by 3 of 4.

**An allowlist, never a fallback.** A name missing from it is reported as
usual, which is a review rather than a recipe that builds natively and fails
cross-compiled. Adding one is a commit to `config/defaults.yaml`, and the
question to answer first is whether a build would ever execute it. A name the
recipe's own block *does* repeat is reported whatever this key says, since a
bumped bound leaves that copy stale.

**Where it goes.** `defaults.yaml` only.
