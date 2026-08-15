# Quirks database

Everything swage knows about a particular feedstock lives in version-controlled
YAML under `config/`, so adjusting a quirk is a reviewable diff rather than a
code change.

```
config/
  defaults.yaml               # global policy
  name-map.yaml               # PyPI -> conda-forge, global
  families/<family>.yaml
  feedstocks/<feedstock>.yaml
```

Every file is validated against a schema with unknown keys forbidden, so a typo
is a startup error naming the file and line rather than a setting that silently
does nothing. `swage config` reads the whole tree and prints what it resolves
to; `swage config --feedstock <name>` prints it for one feedstock, which is the
cheapest way to check that an edit landed where you meant it to.

## Start from the message

swage names the key that answers each thing holding a feedstock, both in the
report and in the `FINDINGS.md` that `swage draft <feedstock>` writes. If you
arrived here holding one of those sentences, this is where it goes:

| What swage said | Where the answer goes |
|---|---|
| `<name>` is in the recipe and in no upstream version | [`add_requirements`](config/names.md#add_requirements) to keep the line, [`retire`](config/names.md#retire) to delete it — or [`name_map`](config/names.md#name_map), where the diff shows swage adding the same package under another name |
| `<name>` is upstream's name for what conda-forge publishes as `<other>` | [`name_map`](config/names.md#name_map) |
| `<req>` resolved to `<name>`, dropping extra `<extra>` | [`name_map`](config/names.md#name_map) or [`embedded_extras`](config/extras.md#embedded_extras) |
| `<name>` was matched by guesswork rather than by a lookup | [`name_map`](config/names.md#name_map) |
| `<name>` comes from upstream extra `<extra>`, which this output does not list | [`outputs`](config/extras.md#outputs) |
| upstream extra `<extra>` is in neither supported nor skip | [`extras_as_outputs`](config/extras.md#extras_as_outputs) or [`outputs`](config/extras.md#outputs) |
| output built from upstream extra `<extra>`, which no longer declares | [`extras_as_outputs`](config/extras.md#extras_as_outputs) |
| unrecognized template; preserved unchanged | [`recipe_owned`](config/names.md#recipe_owned) |
| the recipe constrains `<name>` more tightly than upstream | [`constraints`](config/names.md#constraints) |
| run_constraints `<name>` is associated with no upstream extra | [`run_constraints`](config/names.md#run_constraints) |
| would remove `<req>` (gone in `<version>`) | [`removals`](config/trust.md#removals) |
| upstream computed `requires-dist` at build time | [`dynamic_dependencies`](config/trust.md#dynamic_dependencies) |
| the python test ran only on the minimum Python | [`test_matrix`](config/trust.md#test_matrix) |
| not approved for automatic merging | [`trust`](config/trust.md#trust) |

Two things swage says have no key, and no config file will make them go away:

- **this output also builds for a platform other than the one it is built on** —
  whether a cross-compilation block should repeat a `host` change is a
  judgement about that recipe, and there is nowhere to record it. Mirror the
  change by hand, or leave it.
- **the recipe builds from 2 sources**, and the other refusals swage reports
  before it plans anything. Those are recipe shapes swage will not touch.

## The keys, by subject

- **[Trust and policy](config/trust.md)** — `trust`, `removals`,
  `dynamic_dependencies`, `test_matrix`. How much may merge with nobody
  looking.
- **[Where metadata comes from](config/upstream.md)** — `upstream`,
  `default_build_requires`. Which release swage reconciles against, and where
  that release's declaration lives.
- **[Extras](config/extras.md)** — `extras_as_outputs`, `outputs`,
  `embedded_extras`. What becomes of an upstream extra.
- **[Names and requirement lines](config/names.md)** — `name_map`,
  `add_requirements`, `retire`, `constraints`, `run_constraints`,
  `recipe_owned`. Individual lines swage cannot account for on its own.

## Layering

Three layers merge, defaults → family → feedstock, with the more specific layer
winning. What "winning" means differs by key, and the difference is deliberate:

| Key | Across layers |
|---|---|
| `trust`, `upstream`, `removals`, `dynamic_dependencies`, `test_matrix` | the most specific value that is set, whole |
| `extras_as_outputs` | the most specific entry, **whole** — a feedstock restating it replaces the family's, `suffix` included |
| `outputs` | merged per output name |
| `constraints`, `run_constraints` | merged per package name, most specific wins |
| `name_map`, `embedded_extras` | an ordered stack, most specific first — never flattened, so a lookup can report *which file* answered |
| `add_requirements`, `retire`, `recipe_owned` | the union of every layer |

The unions are the ones worth understanding. A family retires a name for every
feedstock in it, and a feedstock naming something of its own must not cancel
that; `recipe_owned` behaves the same way, because a feedstock blessing one
local template expression would otherwise un-bless `python`, `pip` and
`pin_subpackage` and stop every line it has.

That `name_map` and `embedded_extras` stay a stack is what lets swage say where
a requirement came from, and where a requirement came from is what it checks
before merging anything.

A feedstock with no file of its own is normal: it resolves to its family's
settings, or to the defaults, which start at `trust: manual`.

## Family membership

A family is a glob over feedstock names:

```yaml
family: google-cloud
match:
  feedstock: "google-cloud-*"
```

A feedstock may also name its family explicitly, which is how it joins a family
whose glob it does not match:

```yaml
feedstock: apache-airflow-providers-google
family: airflow-providers
```

If a feedstock names one family and a *different* family's glob also matches,
that is an error — families do not compose. So is a declared name that
disagrees with the file name, or a family that does not exist:

```
config/feedstocks/google-ads.yaml: feedstock is 'google-adds' but the file is
named google-ads.yaml
```

The part of a name the glob matched is that feedstock's **slug**, and family
templates are written against it: `apache-airflow-providers-apache-hive` under
`apache-airflow-providers-*` has the slug `apache-hive`, which is what
[`upstream.tag`](config/upstream.md#upstream) interpolates.

## Two rules worth stating twice

**`supported` and `skip` must be exhaustive**, on a feedstock that declares a
`skip` list at all. An upstream extra in neither list stops the feedstock. That
is the mechanism preventing a newly added upstream extra from silently
vanishing from a recipe, so it is deliberately an error and not a warning — and
declaring the list is what opts a feedstock into it.

**An empty list is not an absent key.** `"aiobotocore[boto3]": []` means
"declared, and it adds nothing" — a decision someone made. An absent key means
"unknown", which stops the feedstock.

## Writing a decision down

`swage draft <feedstock>` assembles a workbench under `~/.cache/swage/drafts/`
holding `FINDINGS.md`, both recipes, the diff, the upstream metadata swage
read, and a `config.yaml` drafted as far as it can be without deciding
anything. `--apply` copies that file into `config/feedstocks/`, beside an
existing one rather than over it. See [the walkthrough](walkthrough.md) for the
loop it belongs to.

Comments in `config/` are read by whoever reviews the fleet. Keep them about
the feedstock — what was decided and what it rests on — rather than about
swage's own history.
