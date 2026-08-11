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
does nothing.

## Layering

Three layers merge, defaults → family → feedstock, with the more specific layer
winning:

- **Scalar settings** (`trust`, `upstream`, `requires_python`,
  `extras_as_outputs`) take the most specific value that is set, whole.
- **`outputs`** merges per output name.
- **`name_map` and `embedded_extras`** do not flatten. They stay an ordered
  stack of layers, so a lookup reports *which file* supplied the answer. That
  provenance is what the trust gates check.

A feedstock with no file of its own is normal: it resolves to its family's
settings, or to the defaults.

## Family membership

A family is a glob over feedstock names. A feedstock may also name its family
explicitly, which is how it joins a family whose glob it does not match. If a
feedstock names one family and a *different* family's glob also matches, that is
an error — families do not compose.

## Fields

### `defaults.yaml`

| Key | Meaning |
|---|---|
| `trust` | The bottom of the trust ladder. Required. |
| `requires_python.min` | swage refuses a feedstock whose upstream Python floor is above this. |

### `families/<family>.yaml` and `feedstocks/<feedstock>.yaml`

The declared name must match the file name. Family files add `family` and
`match.feedstock`; feedstock files add `feedstock` and an optional `family`.
Both may set:

| Key | Meaning |
|---|---|
| `trust` | `manual` never pushes, `propose` pushes but never auto-labels, `auto` pushes and labels when the trust gates pass. |
| `upstream` | Where metadata comes from: `source: pypi` (with an optional `project`), or `source: github` with `repo`, `tag`, and `metadata`. |
| `requires_python.min` | As above. |
| `extras_as_outputs` | Upstream extras that become separate outputs: a `suffix` pattern plus `supported` and `skip` lists. |
| `outputs` | Per output, `run.core` (take upstream's own dependencies) and `run.extras` (upstream extras folded into this output's `run`). |
| `name_map` | PyPI name → conda-forge name, for this scope. |
| `embedded_extras` | For an upstream requirement carrying an extra, the conda-forge packages it expands to. |

## Two rules worth stating twice

**`supported` and `skip` must be exhaustive.** An upstream extra in neither list
stops the feedstock. That is the mechanism preventing a newly added upstream
extra from silently vanishing from a recipe, so it is deliberately an error and
not a warning.

**An empty list is not an absent key.** `"aiobotocore[boto3]": []` means
"declared, and it adds nothing" — a decision someone made. An absent key means
"unknown", which stops the feedstock.
