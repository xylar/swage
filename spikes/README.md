# Spikes

Investigations that answer a question the design depends on, kept because the
answer is worth being able to re-check when a dependency releases.

They are not part of the package and are not covered by the lint, type, or test
tasks.

## `crm_roundtrip.py`

DESIGN.md 3.1 makes swage's whole `recipe` layer conditional on whether
[conda-recipe-manager](https://github.com/conda/conda-recipe-manager) can read
and re-emit a real feedstock recipe without losing comments or changing
formatting. This answers that, over the vendored corpus by default:

```console
$ pixi run -e spike spike-crm
```

Any path can be given instead, which is how it was run over the full set of 189
recipes in the two tools' working directories:

```console
$ pixi run -e spike python spikes/crm_roundtrip.py \
    ~/code/conda-forge/airflow-feedstock/providers \
    ~/code/conda-forge/google-cloud/feedstocks
```

It checks four things per recipe: that a parse-and-render round trip is
byte-identical; that comments survive with their indentation intact; that a
patched dependency is the only line that changes; and that replacing, appending
to, and removing from a requirements list leaves a document that still parses
and still has the shape it started with.

Exit code is 0 only if every recipe round-trips and no edit changes the
document's structure.
