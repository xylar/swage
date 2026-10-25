# Fixing a feedstock by hand

`swage apply` makes the commits `swage update` would push, in a checkout of
the feedstock on your own machine, and pushes nothing. Use it when a feedstock
needs swage's fix and there is no bot pull request to carry it: a recipe whose
dependencies are wrong for the release it already builds, often one still on
the old `meta.yaml` format.

Where a bot pull request is open, use `swage update -f <feedstock>` instead. It
puts the same commits on the bot's branch and handles the label and the
comment.

## The steps

Check out a branch of the feedstock with no uncommitted changes. Any clone
works, including a fork: swage finds out which feedstock it is from the
remotes.

```console
$ cd ~/code/conda-forge/calver-feedstock/fix-dependencies
$ swage apply --dry-run
$ swage apply
```

The dry run says what would be committed and commits nothing. `apply` then
adds up to three commits to the branch, the same ones in the same order as on
a bot pull request:

1. **The conversion**, where the recipe is still `meta.yaml`: `recipe.yaml`
   replaces it, and `conda-forge.yml` is set to build it with rattler-build.
   The commit message says what the converter could not carry over.
2. **The dependency update**, reconciled against what the release declares.
3. **The switch to `hint-grayskull`** in `conda-forge.yml`, on the feedstocks
   [`config/grayskull.yaml`](config/trust.md#grayskull) lists.

## What the report means

| Bucket | Meaning |
|---|---|
| `COMMITTED` | the commits are on the branch and swage found nothing to ask about |
| `NEEDS REVIEW` | commits may be on the branch, but a check found something; the note under the name says which commits |
| `UNCHANGED` | the recipe already matches the release; nothing committed |
| `NEEDS MIGRATION` | the old recipe could not be converted; `swage migrate <feedstock>` says why |
| `FAILED` | swage could not plan the feedstock; the line says why |

`apply` commits nothing where `update` would push nothing: on a feedstock
whose trust is `never`, and where a check says the change itself may be wrong,
such as a name that is no conda-forge package. A finding about a decision that
belongs in config is answered the usual way: `swage draft <feedstock>`
assembles it, as [the walkthrough](walkthrough.md) describes.

`swage explain <feedstock>` prints the whole record of the run, every
requirement line with where it came from, and `--json` prints it as `run.json`
stores it. The run directory the report names also holds the recipe before
and after.

## What is left to you

- **Review the commits, then push.** Nothing goes to GitHub until you do.
- **Bump `build.number`** if the version is unchanged. A merged change on a
  release conda-forge already built does not upload without it.
- **Rerender.** A converted recipe, or one whose `build.skip` swage raised,
  needs a rerender before it builds; the report says so where it applies.
  Commenting `@conda-forge-admin, please rerender` on the pull request does
  it.

A conversion with no version update is fine here, because you chose the
feedstock. It is not a reason to convert others: `NEEDS MIGRATION` in a fleet
audit lists feedstocks waiting for their next update, not work outstanding.
