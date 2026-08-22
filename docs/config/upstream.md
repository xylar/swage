# Where metadata comes from

swage reconciles a recipe against the dependencies the release it packages
declares. Where that declaration lives is a per-feedstock fact, and swage does
not guess: config names it.

## `upstream`

Three sources, discriminated by `source`. Two more are readers for
feedstocks that package no python distribution, and have sections of their own
below.

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
archives carry no Python metadata at all. Several of them do declare their
dependencies somewhere a reader can get at — a `CMakeLists.txt`, ESMF's
makefile — and the two sections at the end of this page are what points one at
the file.

The rest get [`source: manual`](#upstream-source-manual), which names the files
without reading them. There is no `source: autotools` to read a `configure.ac`
with, because `AC_CHECK_LIB` asks whether a symbol links on this machine rather
than saying what the project needs, and the calls that do say it are macros
each project defines in its own `m4/` directory: `tempest-remap` writes
`ACX_NETCDF`, `ncview` writes `AC_PATH_NETCDF`, and both mean libnetcdf.

### `source: manual`

swage does not read this feedstock's declaration, and says where it is. The
fallback where no reader is available — every autotools feedstock in the fleet
today.

```yaml
# config/feedstocks/ncview.yaml
upstream:
  source: manual
  declares:
    - configure.in
    - m4macros/netcdf.m4
    - m4macros/udunits2.m4
    - m4macros/png.m4
  reason: >-
    ncview finds netCDF, udunits2 and libpng through m4 macros it defines
    itself — AC_PATH_NETCDF, AC_PATH_UDUNITS2 and AC_PATH_PNG, one per file
    under m4macros/. Its AC_CHECK_LIB calls name only the X libraries.
```

**`declares` is the point.** The paths are relative to the archive's top-level
directory, and they are the answer to "where does upstream say what it needs" —
which is the thing that is hard to remember six months later and easy to record
once. Order them the way a reader should open them: the entry point first, then
what it pulls in.

**swage plans nothing here and proposes no line.** That is deliberate. Reading
these files as an empty declaration would report every line of the recipe as
coming from nowhere, which is worse than saying plainly that swage does not
read them.

**A version bump says which of them changed.** Where swage has the release the
recipe is moving from — a bot pull request — it compares each declared file
against that release and names the ones that differ. Those feedstocks report as
`DECLARATION MOVED` and count as needing review; the rest report as `NOT READ`,
which is quiet.

**`swage draft <feedstock>` gives you the diff**, which is usually the whole
answer:

```
~/.cache/swage/drafts/ncview/
  recipe.yaml                      the feedstock's, as it is
  upstream/m4macros/netcdf.m4      this release's, whole
  upstream.before/m4macros/netcdf.m4   the one it is moving from
  upstream.diff                    what changed, one unified diff per file
  FINDINGS.md                      the files, and which of them moved
```

swage does not read any of it. It cannot tell you that `netcdf >= 4.7` became
`>= 4.9` — but putting the two lines beside each other tells you anyway, and
that is what makes this practical rather than merely honest.

**What it does not catch:** the comparison is against the release your recipe
currently reflects, so it finds what changed *since you last looked*, not what
was already wrong when you looked. A dependency upstream added two releases ago
and nobody noticed stays unnoticed.

**What you see when it is wrong.** A path that is not in the archive stops the
feedstock and names itself. That is the one thing here that can go wrong —
upstream moving its declaration leaves the config pointing at where it used to
be, and nothing else in swage looks at these paths.

**Where it goes.** A feedstock file. Which files a project declares in is not
something a family shares.

**Not the same as [`source: none`](#upstream-source-none).** That one says
there is nothing to read; this one says there is something, names it, and
admits swage cannot parse it.

### `source: none`

This feedstock packages no Python distribution. swage reports it as
`NOT RECONCILED`, plans nothing, and writes nothing.

```yaml
# config/feedstocks/e3sm-tools.yaml
upstream:
  source: none
  reason: >-
    e3sm-tools installs Fortran binaries and two scripts, whose dependencies
    are their import statements.
```

**`reason` is required and says what the feedstock does build**, because
`source: none` on its own cannot be told from a feedstock nobody finished
configuring.

Use it where the archive carries Python metadata for something the recipe does
not package — which is when swage will otherwise read that metadata and plan
confidently against the wrong project. `e3sm-tools` was to gain `mpi4py` from
`pyscream`'s `pyproject.toml`. A feedstock whose archive carries no Python
metadata at all needs no entry: swage already refuses those, and says so.

**A metapackage wants it for the opposite reason.** `e3sm-unified` pins the
versions of the conda packages that make up one analysis environment and
installs nothing of its own, so its recipe has no source to read at all. swage
refuses that too, with "the recipe declares no source" — but a refusal reported
as a failure is a thing to go and fix, and there is nothing here to fix. The
entry is what says so, once.

**Not for a feedstock whose declaration swage merely cannot read yet.** This
key says there is nothing to read, which is a different claim from "the
declaration is in a file swage has no parser for". `esmf` states its
dependencies in `build/common.mk` and has a reader of its own; a feedstock
whose upstream declares something somewhere should get a reader or wait for
one, rather than an entry saying its declaration does not exist.

**Where it goes.** A feedstock file, almost always. What a feedstock packages
is not a property a family shares.

## `outputs[].upstream`

Which release an output is built from, on a recipe that builds several.

Almost every recipe pins one archive, every output reconciles against it, and
this key is ignored. A few pin more than one: `airflow-feedstock` builds
`apache-airflow`, `apache-airflow-core` and `apache-airflow-task-sdk` out of
three sdists at two independent versions.

**An output whose name matches a release needs no entry.** swage reads the
project name out of each archive, so the `apache-airflow-core` output is
matched to the sdist whose metadata says `Name: apache-airflow-core`. The key
exists for the outputs that match nothing — the metapackages, which correspond
to no upstream distribution:

```yaml
# config/feedstocks/airflow.yaml
outputs:
  apache-airflow-core-with-all:
    upstream: apache-airflow-core   # whose extras this output folds in
    run:
      core: false
      extras: [graphviz, kerberos, otel, statsd]
```

The value is the project an archive declares, not the recipe's
`target_directory` and not a URL.

**Where it goes.** A feedstock file. Which archive an output is built from is
not something two feedstocks have in common.

**What you see when it is wrong.** An output that neither matches a release nor
is named here stops the feedstock before planning, and the message lists the
projects the sources declare:

```
airflow: the recipe builds from 3 sources and nothing says which of them
apache-airflow-core-with-all, airflow-with-all is built from
  the sources declare apache-airflow, apache-airflow-core, apache-airflow-task-sdk
  name one of those in config under outputs.<output>.upstream
```

Two sources declaring the *same* project stop it too, and this key cannot fix
that: it names a project, so it cannot tell two archives of one project apart.

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

Which requirements belong in that block is a judgment per dependency, and
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

## `upstream: {source: esmf}`

Dependencies read out of ESMF's makefile and the feedstock's own build script,
for a feedstock that packages a Fortran and C++ library rather than a python
distribution.

```yaml
# config/feedstocks/esmf.yaml
upstream:
  source: esmf
```

There is nothing to configure. Where the files are is part of what the reader
knows, and a key naming them would invite a second feedstock to point this
reader at a makefile it was never written for.

**What it reads.** `build/common.mk` in the source archive states which
libraries each of ESMF's build toggles links; `recipe/build.sh` in the
feedstock states which toggles are on. Neither is the declaration by itself —
read alone the makefile offers eleven optional libraries and the recipe takes
two of them.

**What it produces** is a `host` list, and the libraries are named by the
package conda-forge publishes them in, through
[`link-map.yaml`](names.md#link_map). A makefile has no notion of a runtime
dependency, so `run` is left to [`add_requirements`](names.md#add_requirements):
what a compiled library needs at run time is decided by run exports and by the
build-string pins that hold a variant, both of which are conda-forge's.

**What it reports rather than writes.** ESMF vendors a copy of ParallelIO and
states its version, which moves between releases. conda-forge links the
packaged `parallelio` instead and pins it by hand, so that pin is not something
any reader can produce — but the vendored version is worth knowing at a bump:

```
note: ESMF 8.9.1 builds against ParallelIO 2.6.6
(src/Infrastructure/IO/PIO/ParallelIO/configure.ac); the recipe pins
`parallelio` itself, so check the pin when this moves
```

**Why it is named for a project.** A makefile is not a metadata format. What
`build/common.mk` states, and that `recipe/build.sh` decides which of it
applies, are facts about ESMF rather than about makefiles, and a reader
pretending otherwise would be one nobody could predict the behavior of.

## `upstream: {source: cmake}`

The other reader, named the other way round, and the contrast is the point.
Read the dependencies out of the project's top-level `CMakeLists.txt`, for a
feedstock that packages a CMake project rather than a Python distribution.

```yaml
# config/feedstocks/proj.4.yaml
upstream:
  source: cmake
```

Where the file is needs no configuring. CMake decides that, and the `-D` flags
saying which of its `option(...)` blocks are on are in the feedstock's own
`recipe/build.sh`, already beside the recipe — swage reads both, at the commit
the recipe came from.

**What it produces is `host`, and only `host`.** A build system states what a
project links, which is a fact about building it. What ends up in a compiled
recipe's `run` section is the host packages' run exports plus whatever
build-string pins hold a variant, both of which are conda-forge's own reasons
for a line and both [`add_requirements`](names.md#add_requirements).

**`swage draft` puts both files in front of you.** The workbench's `upstream/`
holds `CMakeLists.txt` and the `recipe/build.sh` it was joined with, at their
own paths, read at the same commit the recipe came from — which is the fastest
way to see why a `find_package` did or did not count. The `esmf` reader's
workbench carries `build/common.mk` and `recipe/build.sh` the same way.

**`REQUIRED` is what decides whether swage proposes a line on its own.**
`find_package(SQLite3 REQUIRED)` is a dependency; `find_package(nlohmann_json
QUIET)` is upstream saying the project builds either way, and whether
conda-forge carries it is a packaging decision no file answers.

**`supported` and `skip` are where that decision goes.** They take
`find_package` names, matched without regard to case for the same reason
[`cmake_map`](names.md#cmake_map) is:

```yaml
# config/feedstocks/netcdf-cxx4.yaml
upstream:
  source: cmake
  # upstream requires netCDF and never says so: FIND_PACKAGE(netCDF QUIET)
  # falls back to a FIND_LIBRARY with a FATAL_ERROR behind it
  supported:
    - netCDF
```

A name in `supported` becomes a requirement the recipe's `host` is reconciled
against, and the plan says where upstream states it — quoting the call as
upstream wrote it, without `REQUIRED`, because that is what someone opening the
file will find. A name in `skip` is dropped, which is how a decision not to
build against something gets recorded. Anything in neither list is reported as
a note at every run rather than proposed or dropped, so a newly optional
dependency cannot pass unnoticed.

An entry naming a declaration this release does not make is reported too —
because upstream dropped it, promoted it to `REQUIRED`, or because a `-D` flag
in `build.sh` turns it off. It claims a decision that no longer exists, and
nothing else would look.

**A name has to be in [`cmake_map`](names.md#cmake_map)** — as a package, or
as an entry saying no single package answers it. A name in neither stops the
feedstock and is quoted.

**Where it goes.** A feedstock file. Which build system a project uses is not
something a family shares — and it is a property of the *feedstock*, not of the
archive: `moab`'s tarball carries a `CMakeLists.txt` and its recipe builds with
`./configure`, so this key would be wrong there.

**Why it is named for a build system.** `find_package(SQLite3 REQUIRED)` means
the same thing in every CMake project there is, which is what a makefile never
does. 14 of the archives swage has fetched carry a top-level `CMakeLists.txt`,
and the rules this reader follows are CMake's rather than any one project's.
