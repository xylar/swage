# Trust and policy

Four keys decide how much may happen with nobody looking. Each is set in
`defaults.yaml` and overridden, whole, by a family or a feedstock file.
A fifth, `unmaintained`, takes a feedstock out of swage's hands entirely.

Three of them — `removals`, `dynamic_dependencies`, `test_matrix` — are proving
periods rather than permanent rules. They start at the value that holds work
for a human, and each is promoted in one commit once there is a body of
correctly classified cases to point at.

## `trust`

What swage may do with a feedstock, once the checks have decided whether it has
anything to offer.

| Value | What swage does |
|---|---|
| `never` | writes nothing to this feedstock, ever |
| `propose` | pushes a change every check accounted for, comments, never labels |
| `auto` | the same, and adds conda-forge's `automerge` label |

`auto` does not mean swage merges. It means conda-forge's automerge machinery
decides, on green CI, with nobody in the loop. swage has no merge in it.

**Whether swage pushes is not this key's answer.** A change something could not
account for is never pushed, at any rung — the checks say so, and the report
names them. So the rungs differ only in what happens to a change that *is*
understood: nothing, a pull request somebody reads, or a pull request
conda-forge merges.

That is what makes the ladder worth having and keeps it out of your way. A
feedstock needs a config file when swage needs teaching — a name to map, a
dependency to account for — and never merely to say "yes, this one is fine".
Nothing is left to say: the checks said it.

**Where it goes.** `defaults.yaml` requires it, and it is `propose` there, which
is what the whole fleet gets. Raise it to `auto` for a feedstock in its own
file:

```yaml
# config/feedstocks/globus-cli.yaml
feedstock: globus-cli

trust: auto
```

Nothing about that line is self-explanatory a year later, which is why the
file around it is mostly prose: one output, one maintainer, no extras
published, every requirement accounted for, and a change that moves two lines
into the order upstream declares them in. Promotion is meant to be
evidence-backed, and the file is where the evidence is written down. No family
may confer `auto`, so a glob matching a hundred feedstocks cannot bless them.

**`never` is the other direction**, and the one rung that is about the
feedstock rather than about a change — for one you are migrating by hand, or
one a co-maintainer wants left alone:

```
swage writes nothing to this feedstock (trust: never); remove that line from
config/feedstocks/<name>.yaml for swage to push the change and comment
```

It is spelled `never` rather than `off` because YAML reads a bare `off` as the
boolean `false` — as it does `no`, `yes` and `on`.

**Two flags, two questions.** `--execute` says this run may write; `trust` says
this feedstock may be written to. Both have to be true, which is why
`swage update --execute` on a `never` feedstock does nothing and says so.

## `unmaintained`

Why nobody maintains this feedstock any more, in a sentence somebody can check.
swage reads no further: no recipe, no archive, no plan, and a bucket of its own
in the report.

```yaml
# config/feedstocks/apache-airflow-providers-jira.yaml
feedstock: apache-airflow-providers-jira

unmaintained: >-
  apache/airflow has removed this provider from its monorepo: there is no
  providers/jira/pyproject.toml at the tag the recipe pins, so there is
  nothing upstream left to reconcile against. Archiving requested in
  https://github.com/conda-forge/admin-requests/pull/2298
```

**Not the same as `trust: never`,** and the difference is what happens before
the writing. A `never` feedstock is a live one that swage must not write to:
it is still read, still planned, and still reported as work — because the
point is that a person acts on it instead. An `unmaintained` feedstock is not
work for anybody, so reading it is effort spent to produce an answer nobody
wants. Reach for `never` when the feedstock has a future and swage is not the
one to give it; reach for `unmaintained` when it does not.

**Nor is it for an archived feedstock.** GitHub reports those itself and swage
already skips them, so an entry here would be a second copy of a fact that
maintains itself. This key is for the gap before that: archiving a conda-forge
feedstock is a request somebody else merges, and until they do, the repository
still accepts writes and looks exactly like a live one. Once the archiving
lands, swage says so and asks for the entry to be dropped:

```
GitHub now reports this feedstock as archived, which says the same thing on
its own; the `unmaintained` entry in config/feedstocks/<name>.yaml can be
dropped
```

**A feedstock file only**, never a family. A family entry would retire
feedstocks added to it afterwards — silently, and in the direction of doing
nothing, which is the direction nobody notices.

## `removals`

Whether a dependency **upstream dropped** may merge unattended.

| Value | What happens |
|---|---|
| `review` | the removal is pushed, and the pull request is held for a human |
| `auto` | an ordinary change |

Only upstream-dropped removals are in question: the dependency is in the
metadata for the version the recipe reflects and gone from the metadata for the
version being bumped to. A line that appears in *neither* version is never
removed on swage's say-so — see [`add_requirements`](names.md#add_requirements)
and [`retire`](names.md#retire), which are the two ways to say what such a line
is.

**Where it goes.** `defaults.yaml`, at `review`. A feedstock whose upstream
prunes dependencies routinely can set `auto` for itself.

**What you see while it is `review`:**

```
would remove `google-api-core >=2.17.1,<3.0.0`
```

A `(gone in 2.42.0)` follows the requirement where swage knows which version
dropped it. That message once held a whole family at once, every one of them
about the same line — and the answer was not
`removals: auto` but [`retire`](names.md#retire), because the line was an
artifact of the tool swage replaces rather than a dependency anyone had
depended on. Which of the two a removal wants is the thing to decide here.

The failure mode this guards is silent. A dependency that vanishes from a
recipe is invisible until something fails to import.

## `dynamic_dependencies`

Whether a dependency list upstream **computed at build time**, rather than
declaring, may merge unattended.

| Value | What happens |
|---|---|
| `review` | pushed, and the pull request is held for a human |
| `trust` | the computed list is treated as declared |

A sdist may flag that its `Requires-Dist` was computed while building it, which
means another build could compute a different list. The list swage read is
complete; what is not guaranteed is that it is stable.

**Where it goes.** `defaults.yaml`, at `review`. This is the key with the
largest fleet-wide effect after `trust`, and what it holds tends to concentrate
in a family, where one line in the family file answers all of them at once.

**What you see while it is `review`:**

```
upstream computed `requires-dist` at build time rather than declaring it, so
another build may produce a different list -- proofread, or set
dynamic_dependencies: trust for this feedstock
```

## `test_matrix`

Whether a recipe whose python test matrix swage completed may merge unattended.

| Value | What happens |
|---|---|
| `auto` | an ordinary change |
| `review` | pushed, and the pull request is held for a human |

conda-forge asks that a `noarch: python` package be tested on the newest Python
as well as on its minimum. Where a recipe tests only the minimum, swage adds
`"*"` to the `python_version` list while it is already updating that recipe —
never in a pull request of its own.

**Where it goes.** `defaults.yaml`, at `auto`. It began at `review`, which held
a long tail of feedstocks for one edit apiece, and was promoted after the first
of them were read and built. A feedstock that wants its own recipes back in
front of a person can still set `review`.

**What you see when it is `review`:**

```
the python test ran only on `${{ python_min }}.*`; this `noarch: python`
package installs on every Python from that minimum up, so swage added `"*"` to
its `python_version` -- held for a maintainer to confirm while `test_matrix` is
`review`
```

This is the one edit swage makes outside a requirements block, which is why it
had a proving period of its own. Whether the edit is *right* is not what the
check guards: adding the newest Python to the matrix makes the tests run on
that Python, so a green run is the change proving itself and a red one is an
incompatibility that was already shipping untested.

## `source_versions`

Whether swage may set the version a **second source** is pinned at.

| Value | What happens |
|---|---|
| `never` | the conflict is reported and a person makes the edit |
| `auto` | swage sets the `context` entry and the `sha256` beside it |

The conda-forge bot bumps one version per feedstock: the one the feedstock is
named for. A recipe building several archives at independent versions has the
others, and nothing bumps them — `airflow` writes the instruction into the
recipe, addressed to a person:

```
context:
  version: "3.3.1"
  task_sdk_version: "1.3.0"  # manually update with each airflow release
```

Left undone, the recipe builds `apache-airflow-task-sdk` 1.3.0 while
`apache-airflow-core` 3.3.1 — built by the same recipe — requires
`apache-airflow-task-sdk==1.3.1`. Every line is individually right and the
packages cannot be installed together.

**swage does not choose the version.** It comes from a sibling release's exact
pin, read out of an archive the recipe already pins and swage already verified.
Nothing asks what upstream published most recently. A range dictates nothing
and is passed over.

**Read this before turning it on.** Every other `sha256` swage touches is a
*check* — it downloads what the recipe claims and refuses if the bytes differ.
This one swage **writes**, because the archive is one the recipe does not name
yet. What keeps that honest is that the URL is the recipe's own template with a
single substitution, the version came from a hash-verified sibling rather than
a query, and the archive that comes back has to declare that exact project at
that exact version or swage refuses it.

A templated constraint that already says what swage would write is left alone,
so `apache-airflow-task-sdk ==${{ task_sdk_version }}` stays a template rather
than becoming a literal.

**Where it goes.** A feedstock's own file. It is `never` in `defaults.yaml` and
there is no reason to set it for a family: this is a property of one recipe's
shape, and it is a rare one.
