# Names and requirement lines

These keys answer questions about one line in a recipe: what a name means, why
a line is there that upstream never asked for, and what a bound is doing that
upstream never declared.

The rule they all serve is that swage acts only on what it can attribute. A
line with no upstream basis and no config entry is not deleted and not silently
kept — the feedstock stops, and one of these keys is how the answer gets on the
record.

## `name_map`

PyPI project name → conda-forge package name, where the two differ.

```yaml
# config/name-map.yaml
GitPython: gitpython
asana: python-asana
docker: docker-py
```

Names resolve through the feedstock's map, then its family's, then this global
file, then grayskull's published table, and finally identity — the PyPI name
is used as-is only if conda-forge actually publishes a package under it. That
last step is a *check* rather than an assumption; without it every unknown name
would resolve to itself and nothing would ever be reported as unresolved.

Because the config layers sit above grayskull's table, a map is also how you
**hold a name against a rename**:

```yaml
# config/name-map.yaml
# Identity on purpose, to override grayskull. conda-forge publishes both
# `airflow` and `apache-airflow`, and grayskull's table maps the PyPI name to
# `airflow` -- which is not the name the ~99 provider recipes depend on.
apache-airflow: apache-airflow
```

An entry mapping a name to itself is therefore not redundant and must not be
tidied away.

A key may also carry an extra, which is how a requirement whose extra
conda-forge publishes as its own package is resolved whole:

```yaml
name_map:
  "google-api-core[grpc]": google-api-core-grpc
```

**Where it goes.** The global file for facts about conda-forge's naming that
hold everywhere; a family or feedstock `name_map` for a scope. Layers are a
stack, first match wins, and a lookup reports which file answered.

**What you see without it:**

```
`psycopg2-binary >=2.9.10` in `apache-airflow-providers-postgres`'s `run`
requirements is upstream's name for what conda-forge publishes as `psycopg2`,
which swage renders instead -- drop this line, or map `psycopg2-binary` to
`psycopg2-binary` in name_map if this feedstock means conda-forge's package of
that name
```

That is `apache-airflow-providers-postgres`, and the two answers really are
different packages: dropping the line accepts `psycopg2`, mapping the name to
itself says conda-forge's `psycopg2-binary` is the one meant.

**`pip check` is often how you tell.** conda-forge publishes `psycopg2-binary`
as a metadata-only package so that `pip check` passes for anything depending on
that name, and a recipe whose tests run `pip check` needs it wherever upstream
declares the name among its *core* dependencies -- `psycopg2` installs no
`psycopg2_binary` metadata, so pip reports it missing and the build fails.
Where upstream declares the name only under an extra, pip never asks and
dropping the line is right. Both answers are in the fleet: the postgres and
presto providers map the name to itself, `sqlalchemy` and `wetterdienst` retire
it.

A weaker version of the same failure is a name resolved by guesswork:

```
`<name>` was matched to `<other>` by guesswork rather than by a lookup
```

which also wants an entry, so that the answer stops being a guess.

## `link_map`

Library name to conda-forge package name, in `config/link-map.yaml`. The other
half of `name_map`, for a feedstock whose upstream declares its dependencies as
libraries to link rather than as python distributions.

```yaml
# config/link-map.yaml
libnetcdf: libnetcdf
libnetcdff: netcdf-fortran
libpioc: parallelio
```

Keyed on the **library file's stem**, `lib` included, which is what a linker
flag names once `-l` is expanded: `-lnetcdff` is `libnetcdff.so`. Keeping the
prefix keeps the two questions apart — `libnetcdf` is a package name as well as
a library name, and `netcdf` as a PyPI name is a third thing again.

There is no standard to borrow. netCDF's C library is `netCDF` to CMake,
`netcdf` to pkg-config, `netcdf-c` to Spack and `libnetcdf` here.

**Where it goes.** One global file, not layered per feedstock: which package
publishes `libnetcdff.so` is a fact about conda-forge, and a feedstock
overriding it would be answering a different question from the one asked.

**What you see without an entry:**

```
links libraries swage cannot name a package for
    -lnetcdf (ESMF_NETCDF=split)
  add the library's stem to config/link-map.yaml, which says which conda-forge
  package publishes which library
```

## `cmake_map`

CMake `find_package` name to conda-forge package name, in
`config/cmake-map.yaml`. The third of these tables, for a feedstock whose
upstream declares its dependencies to CMake.

```yaml
# config/cmake-map.yaml
HDF5: hdf5
netCDF: libnetcdf
SQLite3: libsqlite
Threads:
```

**Looked up without regard to case**, because CMake projects do not agree on
one spelling and there is nothing to appeal to: `netcdf-fortran` writes
`netCDF`, `cprnc` writes `NetCDF` and `moab` writes `NETCDF`, all meaning
`libnetcdf`. Two entries differing only in case are a config error.

**An entry with no value says no single conda-forge package answers the
name.** `Threads` and `OpenMP` are CMake asking about the compiler; `Doxygen`
and `PkgConfig` are build tools, and what a build system declares is `host`;
`MPI` is a real dependency whose package is `mpich`, `openmpi` or nothing
depending on the variant, which is why a recipe writes `${{ mpi }}` there.
Recording one is how "looked at, and it is not a host dependency" gets said
rather than the feedstock stopping forever.

**Which package publishes a library and which package a recipe wants are
different questions.** `FindSQLite3` looks for `libsqlite3`, which is in
`libsqlite`, so that is the entry here — while `proj.4` takes `sqlite`,
because PROJ's build also runs the `sqlite3` program. That second answer is a
choice about one recipe and goes in its own [`name_map`](#name_map).

**Where it goes.** One global file, not layered per feedstock, for the same
reason as `link_map`.

**What you see without an entry:**

```
names packages swage cannot map to conda-forge
    find_package(TIFF REQUIRED)
  add the name to config/cmake-map.yaml, which says which conda-forge package
  a `find_package` name means -- and which names mean no package at all
```

## `add_requirements`

conda-forge-only dependencies that upstream never declares, and that the recipe
needs anyway.

```yaml
add_requirements:
  run:
    - line: freetds
      reason: pymssql links against the FreeTDS client library
```

Only `host` and `run` exist, because those are the only sections swage plans:
`build` holds compilers, which have no relationship to upstream metadata.

**`reason` is required, and `TODO` and the empty string are refused.** While
every entry is hand-written a YAML comment would do, because somebody typing
one is already thinking about why. `swage draft` changes that: it makes the
typing free and leaves the thinking exactly as expensive, and what that
produces is a database of entries that silence checks and explain nothing.
Anything other than `TODO` is accepted — whether a sentence is a good reason
is not the schema's business.

**An entry can name one output.** A section-level entry applies to every output
the recipe builds, which is right for most of them and wrong for a line that
belongs to one package:

```yaml
add_requirements:
  run:                              # every output
    - line: freetds
      reason: pymssql links against the FreeTDS client library
  outputs:
    apache-airflow-providers-amazon-with-cncf-kubernetes:
      run:
        - line: packaging >=24.1.0,<26.0.0
          reason: the kubernetes provider needs a floor conda-forge's own does not carry
```

Without the per-output form, a recipe like `gdal` — 21 outputs over 48 native
libraries — would have every library added to every output.

**Where it goes.** Family or feedstock, and unioned across layers — a family and
a feedstock can each have a reason to add something, and the more specific one
does not cancel the other.

## `temporary_requirements`

The same shape, for a line the recipe carries **for now**.

```yaml
temporary_requirements:
  outputs:
    airflow-with-all:
      run:
        - line: snowflake-connector-python !=4.4.0
          reason: 4.4.0 on conda-forge fails to import; nothing airflow depends on pins it away
```

`add_requirements` says conda-forge needs the line and nothing asks about it
again. This one says the line is working around somebody else's problem, so
swage keeps it *and* reports it at every version bump — which is when somebody
can tell whether the bad release is still reachable.

**Reach for it when there is no upstream bound to tighten.**
[`temporary_constraints`](#temporary_constraints) is the right key for a
dependency upstream declares, because there the workaround is a *bound* on a
line that would exist anyway. Where the package is a dependency of a dependency
— named in the recipe only so the solver cannot reach it — upstream declares
nothing to tighten, and this is the key that fits.

`reason` is required exactly as it is above, and matters more: it is what tells
the next reader whether the problem it names has been fixed.

**What you see while an entry stands:**

```
every temporary entry has been re-checked
  `snowflake-connector-python !=4.4.0` is a temporary requirement -- 4.4.0 on
  conda-forge fails to import; nothing airflow depends on pins it away.
  Re-check whether it is still needed: drop the entry and let swage reconcile
  the line if it is not, or record it as permanent if the recipe is meant to
  keep it
```

The recipe is still updated and the pull request still pushed — this is a
question about the recipe, not a problem with the change, so it costs the
`automerge` label and nothing else.

**Where it goes.** Family or feedstock, unioned across layers, exactly as
`add_requirements`.

**What you see without it:**

```
`freetds` is in `pymssql`'s `host` requirements and in no upstream version --
drop it, declare it in add_requirements if conda-forge needs it for good, or in
temporary_requirements if it is working around another package's metadata and
should be re-checked at every version bump
```

**Read `recipe.diff` before answering this one.** If swage is *adding* a line
for the same software under a different name, the line is upstream's after all
and the answer is [`name_map`](#name_map). `timezonefinder` looks exactly like
an `add_requirements` case — `h3-py` and `python-flatbuffers` appear in no
upstream version — until the diff shows what swage would write in their place:

```diff
-    - h3-py >4
-    - python-flatbuffers >=25.2.10
+    - h3 >=4
+    - flatbuffers >=25.2.10
```

Upstream declares `h3` and `flatbuffers`, and on conda-forge those names belong
to the C libraries; the Python bindings are `h3-py` and `python-flatbuffers`,
which is what the recipe already had right. Two `name_map` entries fix it, and
`add_requirements` would instead pin the mistake in place — with both spellings
in `run`, since the added line does not stop swage writing its own.

That trap aside, the last sentence of the message is the important one, and it
is why this page will not tell you to reach for `add_requirements` whenever a
line is unexplained. The question is what kind of line it is:

- **A dependency conda-forge needs for good** — `pymssql` needs `freetds`,
  `esmpy` needs `esmf`, `pyproj` needs `proj`. The conda package genuinely
  depends on something the Python metadata has no way to mention. Declare it.
- **A temporary constraint** working around another conda-forge package's
  broken metadata. Do **not** declare it. `apache-airflow-providers-amazon`
  carries three, under a recipe comment saying `# temporary constraints to
  avoid pip check problems`, and its config file deliberately declares none of
  them: an entry would silence the question permanently, and the constraint
  would outlive the bug it exists for with nobody ever looking again. Leaving
  it unexplained is what makes swage ask at the next version bump, which is
  exactly when somebody should check whether the upstream fix has landed.
- **An artifact** of a tool swage replaces, which should be deleted rather than
  kept. That is [`retire`](#retire).
- **Upstream's dependency under another name**, as above. That is
  [`name_map`](#name_map).

## `retire`

conda names whose *unexplained* lines swage may delete rather than keep.

```yaml
# config/families/google-cloud.yaml
# The grayskull workaround, retired. grayskull drops the extra from
# `google-api-core[grpc]`, so recipes maintained beside it were written to
# survive being regenerated: a constrained plain `google-api-core`, which is
# what grayskull would produce anyway, next to a bare `google-api-core-grpc`
# carrying the dependency that actually matters. swage resolves the requirement
# properly, which makes the plain line dead in every recipe whose upstream
# declares only the extra.
retire:
  - google-api-core
```

An entry is only ever consulted for a line **nothing upstream accounts for**,
in any version and under any extra. So it says "where this line has no upstream
basis, it is an artifact", never "remove this dependency" — and that is what
makes listing a name safe by construction rather than by care.
`google-cloud-storage` declares plain `google-api-core` itself, so it never
reaches the list at all.

A line the recipe states inside an `if:` is reached only where the list covers
**every** name in that entry. swage does not delete a structure it did not
author on evidence about one of the names inside it, so an entry naming a
retired dependency beside one somebody still means is kept whole. `colorlog`
conditions `colorama` on Windows and nothing else, so listing `colorama`
removes the entry with it.

**Where it goes.** Family or feedstock, unioned across layers: a family retires
a name for every feedstock in it, and a feedstock naming something of its own
must not cancel that.

**What you see without it.** The same message
[`add_requirements`](#add_requirements) answers — the two keys are the two
answers to one question, and which one is right depends on whether the line
should stay or go.

## `constraints`

A bound this feedstock states that upstream does not, and why.

```yaml
constraints:
  numpy:
    bound: "<3"
    reason: this wraps a C extension built against the numpy 2 ABI
```

**A constraint that differs from upstream's is drift until somebody says
otherwise.** swage reconciles it like any other difference — in either
direction, raising a floor or dropping a cap — and the change is visible as a
bump line in the plan and in the pushed diff. An entry here is what makes the
difference a decision instead, and `reason` is the only place the next reader
can learn what it is for. `TODO` and the empty string are refused, for the
same reason they are in [`add_requirements`](#add_requirements).

`bound` is the *additional* constraint, not the whole specifier: it is
intersected with what upstream declares, through the same ordering and
satisfiability checks as everything else, so a recipe line reading
`numpy >=2,<3` against an upstream `numpy >=2` is recorded as `<3`.

**Where it goes.** Family or feedstock, merged per package name, most specific
winning — an entry is a statement about one dependency, so a feedstock
correcting its family's replaces it rather than adding to it.

## `temporary_constraints`

The same, for a bound that must not outlive the reason it was added for.

```yaml
temporary_constraints:
  scikit-image:
    bound: "!=0.20.0"
    reason: 0.20.0 segfaults on the mesh converter; drop when it is yanked
```

swage keeps the bound exactly as `constraints` does, and **holds the feedstock
at every update** so somebody re-checks whether the workaround is still
needed. A workaround that becomes permanent because nobody looked is the
failure this exists to prevent, and it is why the two keys are separate rather
than one key with a flag: what you write is the claim you are making.

```
`!=0.20.0` is a temporary constraint -- 0.20.0 segfaults on the mesh
converter; drop when it is yanked. Re-check whether it is still needed: move
it to `constraints:` if it is meant to hold for good, or drop the entry and
let swage reconcile the line
```

A name may appear in one key or the other, never both.

## `run_constraints`

What an existing `run_constraints` entry in the recipe means.

```yaml
run_constraints:
  libgdal:
    extra: null
```

`run_constraints` in a recipe bounds a package for whoever happens to have it
in the same environment, without depending on it. Nothing in the recipe records
which upstream extra — if any — such an entry came from, and inferring it would
be guesswork. Written down, a change to that extra's constraint can propagate;
without it, the entry is left exactly as found and the feedstock is held.

**Most entries should not get an association.** An extra is opted into; a run
constraint is imposed on everyone with the package in the same environment, so
an entry that merely restates an extra — upstream's bound, copied — is the
wrong shape and belongs out of the recipe. Writing `extra: <name>` for one says
the copy is to be maintained instead. swage never removes a run constraint, so
the way to say "this is going" is to leave it unanswered: the feedstock stays
held, and the finding disappears when the entry does. `extra: <name>` is for
the entry that tracks an extra *and* is meant to stay.

`extra: null` is a real answer rather than a missing one: it says the bound is
deliberate and tracks nothing upstream, which is a different statement from the
entry never having been considered.

`extra: <name>` names the extra an entry tracks, for the entry meant to stay:

```yaml
run_constraints:
  cryptography:
    extra: crypto
```

**Not to be confused with [`constraints`](#constraints)**, which is about a
dependency the package actually installs. These two keys are about different
recipe sections and neither answers the other's finding.

**Where it goes.** Family or feedstock, merged per package name, most specific
winning.

**What you see without it:**

```
run_constraints `cryptography` is associated with no upstream extra; add it to
run_constraints in config -- `extra: <name>` if it tracks one, `extra: null` if
the bound is deliberate and tracks nothing
```

That is `pyjwt`, whose upstream declares a `crypto` extra that no output draws
on. `dnspython` reports it eight times over, `google-resumable-media` three,
and on those two the answer is not an association but a recipe with fewer run
constraints in it.

## `recipe_owned`

Requirement lines that are conda-forge structure rather than upstream metadata.
They are preserved verbatim and never sent through name resolution.

```yaml
# config/defaults.yaml
recipe_owned:
  functions:
    - pin_subpackage
    - pin_compatible
    - compiler
    - stdlib
  names:
    - python
    - pip
  variables:
    - mpi
```

`functions` match the **name position only**: `${{ pin_subpackage(...) }}` is
structure, while `pandas >=${{ x }}` is an ordinary dependency whose constraint
happens to be templated.

`variables` are for the case where a build variant *is* the package.
`${{ mpi }}` is `mpich`, `openmpi` or `nompi` depending on which variant is
building, and the recipe cannot write the name down because the name is not
known until conda-build picks a value. An entry matches the whole name position
and one bare identifier, so blessing `mpi` blesses `${{ mpi }}` and nothing
else.

This is data rather than code so that blessing a new expression is a reviewable
config commit instead of a release. It is an **allowlist, never a fallback**: an
unrecognized template is still preserved unchanged, but swage cannot say where
it came from, so it holds the feedstock and quotes the line. Were it a fallback,
every never-upstream dependency would quietly acquire an explanation and the
protection would evaporate.

**Where it goes.** `defaults.yaml` requires it. A family or feedstock *extends*
the set rather than replacing it — overriding would un-bless `python`, `pip` and
`pin_subpackage` for the one feedstock that needed to add something local, and
stop every line it has.

**What you see without it:**

```
`${{ hypothetical(...) }}` in `demo`'s `run` requirements is a template swage
does not recognize, and is preserved unchanged -- add `hypothetical` to
recipe_owned.functions in config to bless it
```

What an entry can bless is a **call**, named in `functions`, or a whole-name
variable, named in `variables`:

```
`${{ mpi }}` in `esmf`'s `host` requirements is a template swage does not
recognize, and is preserved unchanged -- add `mpi` to recipe_owned.variables in
config, if this is a build variant's key rather than a package name
```

A name a recipe interpolates *into* something else — `${{ name }}-with-monitoring`,
which is how `parsl` refers to one of its own outputs — is described by none of
the three: `functions` cannot match it, `names` holds literals, and `variables`
matches only a name that is nothing but the variable. swage says so rather than
offering a key that cannot answer it:

```
`${{ name }}-with-monitoring` in `parsl-with-visualization`'s `run`
requirements is a template swage does not recognize, and is preserved unchanged
-- config cannot account for a name a recipe interpolates rather than calls.
Where it names another output of this recipe, `${{ pin_subpackage(...) }}` is
the form swage already understands
```

## `variant_conditions`

An `if:` in the recipe that selects a conda-forge **build variant**, rather than
narrowing what upstream declares.

```yaml
# config/feedstocks/esmf.yaml
variant_conditions:
  - condition: mpi != "nompi"
    packages: [parallelio]
    reason: >-
      conda-forge builds esmf once per mpi implementation, and ESMF's build
      turns PIO on only for the mpi builds.
```

By default a recipe that states a dependency only under a condition, where
upstream declares it always, stops the feedstock: it looks like a recipe that
is missing that dependency everywhere else, and flattening the condition away
would hide it. That is the right answer nearly every time. It is the wrong one
where the condition is conda-forge's own axis — `esmf` builds once per mpi
implementation, and the dependency really does exist only in the mpi builds.
Nothing upstream can say that, so a maintainer has to.

An entry makes swage **preserve the conditional entry exactly as written** and
explain it with upstream's unconditional declaration, instead of writing an
unconditional line beside it.

**`packages` says which lines the entry decides about, and is required.** The
condition alone would bless whatever upstream-declared dependency happened to
sit inside it — so moving an unrelated package into `esmf`'s `mpi != "nompi"`
block would be accepted silently, and a reviewer reading `config/` could see
the condition without seeing what it did.

**It is not a list of what the block contains.** swage keeps the conditional
entry exactly as the recipe writes it — byte for byte, contents included — and
never decides what goes inside one. What the list decides is whether the entry
*survives*, so it holds the packages swage plans a requirement for, which are
the only ones whose condition is at risk:

```yaml
# recipe/recipe.yaml -- what config decides about, not config itself
requirements:
  host:
    - if: mpi != "nompi"        # kept exactly as written
      then:
        - ${{ mpi }}            #   not listed, and stays anyway
        - parallelio 2.6.9.*    #   `parallelio` is listed, so the block survives
```

Drop `parallelio` from the list and swage writes the plain unconditional line
upstream's declaration implies, and the condition is gone. `${{ mpi }}` needs
no entry because nothing plans a line for it — leaving it out is not a claim
that ESMF has no MPI dependency, since it has one and `${{ mpi }}` is a real
package, whichever of `mpich`, `openmpi` and `nompi` the variant builds
against. Nothing in `build/common.mk` declares libraries under `ESMF_COMM`, so
there is no planned line to flatten and nothing to decide. It stays inside the
block because the recipe put it there, explained as
[recipe-owned](#recipe_owned) structure.

A package the entry does not name is refused as before, and the message says
so rather than asking a question already answered:

```
cannot plan /requirements/host: it states 'zstandard' under a condition config
blesses for other packages
    if: mpi != "nompi"
  config accounts for this condition around parallelio, and upstream asks for
  'zstandard' on every build this output produces
  add 'zstandard' to that entry's `packages` if it belongs there too, or move
  the line out of the condition
```

**Where the entry shows up afterwards.** `swage explain` names the condition
beside the dependency, so the line and the config entry that kept it can be
read together:

```
keep  if: mpi != "nompi" then: ${{ mpi }}, parallelio 2.6.9.*
      upstream-core   upstream, under if: mpi != "nompi"
```

**What you see without it:**

```
cannot plan /requirements/host: it states 'parallelio' conditionally and upstream does not
    if: mpi != "nompi"
  upstream asks for it on every build this output produces, so swage would write one
  unconditional line -- parallelio -- and the condition would be gone
  keeping it is a decision about what the package promises, so swage makes neither
```

**How it is matched.** As text, with whitespace normalized, so a recipe writing
`mpi!="nompi"` and a config file writing `mpi != "nompi"` are the same
condition. Quoting is left alone and nothing is evaluated: this blesses one
condition somebody looked at, not a family of expressions.

**Where it goes.** A family or a feedstock, and the two are unioned — a family
blesses what its whole family builds under, and a feedstock adds its own
without cancelling that. `reason` is required, and `TODO` is refused.

## `built_everywhere`

A dependency whose platform or machine marker describes upstream's **wheel
matrix** rather than where the dependency is needed.

```yaml
built_everywhere:
  greenlet:
    reason: >-
      conda-forge builds greenlet for every subdir sqlalchemy is built for,
      including the osx-arm64 that upstream's enumeration of its own wheel
      machines leaves out.
```

Upstream often gates a dependency on the machines it publishes wheels for, or
skips a platform where `pip install` would have to compile something.
`sqlalchemy` declares `greenlet` on `aarch64`, `ppc64le`, `x86_64` and the
Windows spellings, leaving out macOS on ARM.
`apache-airflow-providers-mysql` declares `mysqlclient` under
`sys_platform != "darwin"`, with a comment saying macOS needs pkg-config and a
MySQL client library. Neither is a statement about where the dependency does
its job — conda-forge builds both packages for those targets, so on conda-forge
the marker excludes nothing.

An entry says so, and the platform and machine halves of the marker are then
taken as true. What is left names only axes that really do vary, so
`greenlet >=1` and `mysqlclient >=2.2.5` become plain lines.

**It excuses those two axes and nothing else.** A marker naming an operating
system release or an interpreter build is refused exactly as before: this
records where conda-forge builds and claims nothing beyond that.

**Two declarations about the same builds take the widest.** Once the machine is
not something the package varies over, upstream's two declarations for two
machines are two statements about the same builds, and the wider constraint is
the one written. `apache-airflow-providers-jdbc` excludes jpype1 1.7.0 on macOS
ARM alone, where that release shipped no wheel — so `>=1.5.1` is kept and the
exclusion goes with the wheel gap it describes. Where neither constraint admits
everything the other does, there is no widest one and swage stops, naming both
declarations rather than inventing a range nobody wrote.

**What you see without it:**

```
platform-conditional constraint for 'mysqlclient'
    mysqlclient>=2.2.5; sys_platform != "darwin"
  the marker turns on sys_platform, which does not vary across the Pythons one noarch package is installed on
  ...
  a third answer applies where conda-forge builds mysqlclient for every target this
  package is built for: the marker is then about upstream's own wheels rather than
  about where mysqlclient is needed, and recording that in built_everywhere in
  config/feedstocks/apache-airflow-providers-mysql.yaml writes one plain line
  otherwise resolve by hand
```

**Check the dependency really is built everywhere before writing one.** The
entry is a judgment about what the package promises, and `reason` is where the
next reader checks it — name the targets, not the marker. `TODO` and the empty
string are refused.

**Where it goes.** A family or a feedstock, merged per package name, most
specific winning — an entry is a statement about one dependency.
