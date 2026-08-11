# swage

Maintenance automation for conda-forge feedstocks at scale.

conda-forge's `regro-cf-autotick-bot` reliably bumps `version` and `sha256` when
a new release lands, but it does not reconcile a recipe's dependencies against
what upstream actually declares. swage does that reconciliation, and — for
feedstocks explicitly blessed for it — gets the resulting pull request merged
without a human in the loop.

!!! warning "Early development"

    swage is being built in phases. Only the quirks database and the `config`
    command exist so far; `scan`, `update`, `status`, `audit`, `migrate`, and
    `explain` are registered but not implemented.

The full specification, including the delivery plan and an analysis of
conda-forge's automerge internals that the design depends on, lives in
[`DESIGN.md`](https://github.com/xylar/swage/blob/main/DESIGN.md).

## Getting a development environment

swage uses [pixi](https://pixi.sh) for development environments, and depends on
packages that are distributed through conda-forge rather than PyPI.

```console
$ pixi run -e dev check     # lint, format, type check, test
```

## Checking the quirks database

`swage config` validates every file in the quirks database and prints what it
resolves to. With no arguments it summarizes the whole tree:

```console
$ swage config
```

With `--feedstock`, it shows how the layers resolve for one feedstock, including
which file each name-map layer comes from:

```console
$ swage config --feedstock google-cloud-bigquery
```

The database is found by walking up from the working directory looking for
`config/defaults.yaml`, or from `$SWAGE_CONFIG_ROOT`, or from `--config-root`.
