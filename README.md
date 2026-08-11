# swage

> **swage** *(n.)* a shaped block or die a smith uses to work raw stock into a
> consistent standard profile. *(v.)* to shape metal with such a tool.

Maintenance automation for conda-forge feedstocks at scale.

conda-forge's `regro-cf-autotick-bot` reliably bumps `version` and `sha256` when
a new release lands, but it does not reconcile a recipe's dependencies against
what upstream actually declares. Across a few hundred feedstocks that
reconciliation is a steady, tedious stream of near-identical edits.

swage does that reconciliation: it reads upstream metadata from PyPI or GitHub,
computes what the recipe's requirements should be, applies a per-feedstock
database of known quirks, and — for feedstocks explicitly blessed for it — gets
the resulting pull request merged without a human in the loop.

**Status: design, not yet implemented.** See [DESIGN.md](DESIGN.md) for the full
specification, including the delivery plan and an analysis of conda-forge's
automerge internals that the design depends on.

## Design in one paragraph

swage reacts to the bot's pull requests rather than opening its own. For each, it
renders the recipe it believes is correct and compares. Every emitted requirement
carries provenance tracing it to upstream metadata or to an explicit config
entry, and a set of trust gates refuses to act autonomously on anything novel —
an unrecognized package name, an undeclared upstream extra, a new output. Quirks
live as version-controlled YAML rather than code, so adjusting one is a reviewable
diff. Feedstock-specific autonomy is opt-in and per-feedstock.

## Prior art

- [grayskull](https://github.com/conda/grayskull) — recipe generation, and a
  large accumulated body of PyPI↔conda-forge naming knowledge
- [conda-recipe-manager](https://github.com/conda/conda-recipe-manager) —
  structural recipe parsing and v0→v1 conversion
- [feedrattler](https://github.com/hadim/feedrattler) — feedstock conversion to
  the v1 recipe format

## License

BSD 3-Clause. See [LICENSE](LICENSE).

Development happens in the open, but this is a personal maintenance tool rather
than a project seeking contributors. Issues and pull requests may not get a
timely response.
