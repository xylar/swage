# Trust and policy

Four keys decide how much may happen with nobody looking. Each is set in
`defaults.yaml` and overridden, whole, by a family or a feedstock file.

Three of them — `removals`, `dynamic_dependencies`, `test_matrix` — are proving
periods rather than permanent rules. They start at the value that holds work
for a human, and each is promoted in one commit once there is a body of
correctly classified cases to point at.

## `trust`

The trust ladder, and the only key that decides whether swage writes anything
at all.

| Value | What swage does |
|---|---|
| `manual` | never pushes |
| `propose` | pushes a commit and a comment, never labels |
| `auto` | pushes, and adds conda-forge's `automerge` label when every check passes |

`auto` does not mean swage merges. It means conda-forge's automerge machinery
decides, on green CI, with nobody in the loop. swage has no merge in it.

**Where it goes.** `defaults.yaml` requires it, and it is `manual` there:
blessing is opt-in and per feedstock. A family may raise the floor for
everything it covers; a feedstock file raises it for one.

```yaml
# config/feedstocks/globus-cli.yaml
feedstock: globus-cli

trust: auto
```

Nothing about that line is self-explanatory a year later, which is why the
file around it is mostly prose: one output, one maintainer, no extras
published, every requirement accounted for, and a change that moves two lines
into the order upstream declares them in. Promotion is meant to be
evidence-backed, and the file is where the evidence is written down.

**What you see without it**, on every feedstock in the fleet that nobody has
blessed:

```
not approved for automatic merging (trust: manual)
```

That is the default talking, not a complaint about the recipe. A feedstock
whose only failing check is this one is reported as `PROPOSED` — ready except
that nobody has blessed it.

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
dropped it. That message held 37 feedstocks in one fleet audit, all in the
google-cloud family and all about the same line — and the answer was not
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
| `review` | held for a human |
| `trust` | the computed list is treated as declared |

A sdist may flag that its `Requires-Dist` was computed while building it, which
means another build could compute a different list. The list swage read is
complete; what is not guaranteed is that it is stable.

**Where it goes.** `defaults.yaml`, at `review`. This is the key with the
largest fleet-wide effect after `trust`: 67 feedstocks are held by it, 49 of
them one family, and one line in that family file answers all 49.

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
| `review` | held for a human |

conda-forge asks that a `noarch: python` package be tested on the newest Python
as well as on its minimum. Where a recipe tests only the minimum, swage adds
`"*"` to the `python_version` list while it is already updating that recipe —
never in a pull request of its own.

**Where it goes.** `defaults.yaml`, at `auto`. It began at `review`, which held
90 feedstocks in one fleet audit for one edit apiece, and was promoted after the
first of them were read and built. A feedstock that wants its own recipes back in front of a person can
still set `review`.

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
