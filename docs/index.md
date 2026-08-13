# swage

Maintenance automation for conda-forge feedstocks at scale.

conda-forge's `regro-cf-autotick-bot` reliably bumps `version` and `sha256` when
a new release lands, but it does not reconcile a recipe's dependencies against
what upstream actually declares. swage does that reconciliation, and — for
feedstocks explicitly blessed for it — gets the resulting pull request merged
without a human in the loop.

!!! warning "Early development"

    swage is being built in phases. The quirks database and the read-only
    `scan`, `explain` and `config` commands exist so far; `update`, `status`,
    `audit` and `migrate` are registered but not implemented. **Nothing swage
    does today writes to a feedstock.**

The full specification, including the delivery plan and an analysis of
conda-forge's automerge internals that the design depends on, lives in
[`DESIGN.md`](https://github.com/xylar/swage/blob/main/DESIGN.md).

## Getting a development environment

swage uses [pixi](https://pixi.sh) for development environments, and depends on
packages that are distributed through conda-forge rather than PyPI.

```console
$ pixi run check     # lint, format, type check, test
```

The `dev` environment installs swage itself in editable mode, so `import swage`
and the `swage` console script both work anywhere inside it with no further
step:

```console
$ pixi run swage --help
```

CI uses the `ci` environment instead — the same tooling without that editable
install, which it has no use for since pytest reaches `src/` on its own.

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

## Seeing what would change

`swage scan` reads feedstocks and reports what swage would do to them. It
writes nothing: it finds the bot's most recent version-bump pull request,
reconciles the recipe against the release that pull request pins, and prints
the trust verdict per feedstock.

```console
$ swage scan --feedstock google-cloud-bigquery
$ swage scan --family google-cloud
$ swage scan --all
```

A selector is required, because a bare `swage scan` would sweep every feedstock
you maintain — around 490 of them, and a few minutes of GitHub reads.

Exit codes are the contract for running it from cron: `0` nothing needs you,
`1` items need review, `2` swage itself failed. Each run also writes a
`run.json` under `~/.cache/swage/runs/`, which is the machine-readable record
of everything it decided; the directory is disposable.

Name resolution needs two files nobody writes by hand — conda-forge's package
list and the grayskull PyPI mapping — which are downloaded on first use and
cached for a day under `~/.cache/swage/index/`.

## Asking why

`swage explain <feedstock>` prints the whole provenance chain for one
feedstock: the inputs it read, every requirement line with where it came from,
each check and its verdict.

```console
$ swage explain google-cloud-bigquery
$ swage explain google-cloud-bigquery --json
$ swage explain google-cloud-bigquery --from-run ~/.cache/swage/runs/2026-08-12T19-51-57
```

It renders the record of a run rather than working the answer out again, and
that is deliberate. These commands are meant to run unattended, so the question
is almost never "what would swage do now" but "why did it do *that*, at 03:00,
while I was asleep" — by which time upstream has moved on and config may have
changed. Rendering the stored record means `explain` cannot disagree with what
actually happened. It defaults to the most recent run; `--from-run` names an
older one, and `--json` prints the record exactly as `run.json` holds it.
