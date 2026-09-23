# conda-forge internals swage depends on

Facts about conda-forge that hold whether or not swage exists. Each was
established by reading conda-forge's code or by running against real
feedstocks, and each names its source. `DESIGN.md` cites this page; this page
says nothing about what swage decided to do with any of it. The section of
the [v1 design](design-v1.md) that first recorded a fact is given as `v1 §n`,
and holds the evidence in full.

## Automerge

Source: [`automerge.py`](https://github.com/conda-forge/conda-forge-webservices/blob/main/conda_forge_webservices/github_actions_integration/automerge.py)
and [`webapp.py`](https://github.com/conda-forge/conda-forge-webservices/blob/main/conda_forge_webservices/webapp.py)
in conda-forge-webservices. v1 §2.

### The label is not a trigger

Nothing in conda-forge watches for the `automerge` label. `automerge.yml` is
a `workflow_dispatch`-only workflow: it never self-triggers, and runs only
when the webservices app dispatches it with a `(repo, sha)` pair. That
dispatch happens in one place, `StatusMonitorPayloadHookHandler`, on webhook
deliveries for `status` events, `check_suite` *completed* events, and
`pull_request` events.

Once CI has finished, no further `status` or `check_suite` event fires for
that commit. Adding the label to a pull request whose checks have completed
dispatches nothing, and the pull request sits open.

The handler has a `pull_request` branch that a `labeled` action would reach
in principle. Whether feedstock webhooks subscribe that endpoint to
`pull_request` events is deployment configuration not visible in the source.
Over years of maintaining these feedstocks: they do not. The label works in
the normal case because pushing a commit starts a CI run, and that run's
completion events do the dispatching.

### What the dispatched job checks

Two ways a pull request merges:

- **The label.** The pull request carries `automerge`, and no commit appears
  in its timeline after the most recent `labeled` event *whose label is
  `automerge`*. A commit after that event strips the label, with a comment.
  `_no_extra_pr_commits` matches `label.name == "automerge"` when it scans
  the timeline, so other labels applied after a commit are invisible to it.
- **A bot pull request.** The author is an allowed bot, `[bot-automerge]` is
  in the title, every commit is authored by an allowed bot, and the
  feedstock has `bot.automerge: true` in `conda-forge.yml`.

Consequences:

1. The label bypasses the `conda-forge.yml` requirement. `bot.automerge` is
   checked only on the second path.
2. Push first, label last. A label applied before a push is stripped by it.
3. A commit from anyone else on a bot pull request ends the second path for
   that pull request: the all-commits-from-a-bot test fails from then on.
4. Re-adding a label that is already present is a no-op and produces no new
   timeline event. To re-arm after a further push, remove the label and add
   it again.
5. With no commit to push there is no CI run, so nothing dispatches the job,
   and a label does nothing.

### Which checks must pass

Source: `_get_required_checks_and_statuses`, `_get_github_checks`,
`_get_github_statuses` and `_all_statuses_and_checks_ok` in `automerge.py`.
v1 §5.2.1.

The required CI providers are decided from the files conda-smithy wrote,
because no API answers it: `azure-pipelines.yml`, `.travis.yml`,
`.drone.yml`, and `appveyor.yml` or `.appveyor.yml`, each making its
provider required by existing; `.github/workflows/conda-build.yml` and
`.circleci/config.yml`, each read for whether it is live. `linter` is
required unconditionally, so a feedstock with no CI still has a non-empty
required set. An empty required set is a refusal.

- Two providers are configured by a file that outlives being switched off.
  conda-smithy writes a *disabled* GitHub Actions workflow rather than
  deleting it — `name: Disabled build`, `- run: exit 0`, `if: false` — and
  leaves `.circleci/config.yml` in place with a `filters:` block that
  ignores every branch. Both files have to be read, not merely found.
- A provider is matched to a check by substring. The provider is `azure`;
  the context Azure posts is `conda-forge/azure-pipelines`. One provider
  matches several reports, one per platform. A provider matching nothing is
  unfinished, not passed.
- A GitHub Actions suite containing a run called `automerge` is not a
  passing build. The automerge workflow is itself an Actions run, and its
  suite goes green whenever it finishes.
- CI configuration is read from the pull request's head, which is the fork
  and the commit CI ran on. `conda-forge.yml`, including
  `bot.automerge_options.ignored_statuses`, is read from the base branch,
  because a fork can say anything.
- GitHub keeps every status ever posted for a context. A build that went red
  and was re-run has two, and only the newest is current.
- `mergeable` is computed lazily. The first read of a pull request nobody has
  asked about answers `null`, which means ask again.

`mergeable_state` does not track whether anything passed. With every check
green, `mergeable` true and no branch protection, GitHub still says `blocked`
on feedstocks whose CI reports as check runs, and `clean` on those whose CI
reports as commit statuses
([google-ads#55](https://github.com/conda-forge/google-ads-feedstock/pull/55),
[weaviate-client#38](https://github.com/conda-forge/weaviate-client-feedstock/pull/38),
[google-cloud-aiplatform#197](https://github.com/conda-forge/google-cloud-aiplatform-feedstock/pull/197)).
`blocked` is no obstacle to the maintainer's merge button.

### Merging with a maintainer's token

v1 §5.2.2. Two refusals, met in order:

- **"The base branch policy prohibits the merge."** A feedstock's `main`
  enforces no status checks — `enforcement_level: off`, zero contexts — and
  its only branch rules are `deletion` and `non_fast_forward`. The rule
  doing the prohibiting is not visible to a non-admin. `viewerCanMergeAsAdmin`
  is true: the maintainer's green button is an admin bypass, and always has
  been. Of the five most recent bot pull requests merged on `google-ads`,
  three were merged by the maintainer and two by conda-forge's admin app.
  `--admin` clears this refusal.
- **"Refusing to allow an OAuth App to create or update workflow
  `.github/workflows/conda-build.yml` without `workflow` scope."** The `gh`
  CLI's token has `repo` and not `workflow`. Merging a pull request that
  re-renders a workflow file writes that file. conda-smithy re-renders one
  into most bot pull requests: 11 of the 14 newest across one maintainer's
  fleet.

## The bot

v1 §3.4.1.

- Two accounts file version bumps. `regro-cf-autotick-bot` is the autotick
  bot. `conda-forge-admin` files `chore: update package version to
  <version>` when a maintainer asks for a bump by hand.
- Both accounts fork to a user account of their own, and while their pull
  requests are open both allow edits by maintainers, so swage pushes to
  either one's branch. Forty of the admin service's open pull requests were
  sampled on 2026-09-23 -- bumps, rerenders, recipe conversions and user
  additions -- and all forty allowed it.
- `maintainer_can_modify` reads false on every *merged* pull request,
  whoever filed it, so it describes the pull request rather than the fork.
  Read it while the pull request is open; read after the merge it says the
  bot's own branches cannot be written to either.
- The bot files from a fork, `regro-cf-autotick-bot/<feedstock>-feedstock`.
  A commit on its pull request belongs to the fork, and a push names the
  fork's head ref.
- The bot stops filing new version bumps once four of its previous ones sit
  unmerged on a feedstock.
- The admin service files far more rerenders and `MNT:` migrations than
  bumps: 193 of its 200 open pull requests across conda-forge, at one
  reading.

## Feedstocks and teams

v1 §3.4.

- Every feedstock has a matching org team whose members are its maintainers,
  and team membership grants push and merge access. `gh api --paginate
  user/teams`, filtered to `organization.login == "conda-forge"`, lists a
  maintainer's feedstocks in one call.
- The feedstock name is the team's `name`, not its `slug`. GitHub flattens a
  dot to a hyphen in the slug: `proj.4` becomes `proj-4`, `sqlean.py`
  becomes `sqlean-py`. The repository exists under the name and not the
  slug.
- Not every team is a feedstock. `all-members` is org-wide and has no
  repository.
- A feedstock's name is not its package's name. `proj.4-feedstock` builds
  `proj`.
- An archived feedstock accepts no push, merge or label.

## Recipes and rendering

### `python_min`

v1 §3.3.3, and `plan/python_min.py`.

`python_min` is conda-forge's global pinning value and moves when conda-forge
drops a Python. A recipe refers to it as `${{ python_min }}`, so the number is
not in the recipe text. It is resolved in two places inside a feedstock: the
recipe's own `context.python_min`, where it sets one, and otherwise
`.ci_support/*.yaml`, which conda-smithy renders per build variant with the
global pinning folded in. conda-smithy writes it into `.ci_support` only for
a feedstock that builds a `noarch: python` package. `pyproj` renders 26
variants and declares it in none. Across 217 checkouts carrying it, no
feedstock's variants disagree; 82 say 3.9 and 135 say 3.10.

### Variant keys and `host` lines

v1 §3.3.6.

conda-build reads the conda-forge-wide pin out of `conda_build_config.yaml`
by matching a `host` entry against a variant key. An entry carrying a version
or a build string does not match. So a recipe that wants both the pin and an
mpi build selection states the package twice:

```yaml
# recipe/recipe.yaml
requirements:
  host:
    - hdf5
    - hdf5 * ${{ mpi_prefix }}_*
```

`esmf`, `libnetcdf` and `moab` say so in their own comments — "need to list
netcdf-fortran, hdf5 and libnetcdf twice to get version pinning from
conda_build_config and build pinning from `{{ mpi_prefix }}`"; "without this
repeat reference, conda-smithy doesn't pin correctly". `moab`'s rendered
matrix carries `hdf5: [1.14.6]`, `libnetcdf: [4.10.0]` and
`libpnetcdf: [1.14.1]`, one key per package the recipe states twice. Of 618
`host` lines naming a variant key with no build string across one fleet, 609
carry no version. The build field has a second spelling,
`hdf5 [build=${{ mpi_prefix }}_*]`.

### `noarch_platforms`

v1 §3.3.4.

conda-smithy's `noarch_platforms` builds a `noarch: python` package once per
listed platform. Its own test fixture shows the idiom:

```yaml
# conda-forge.yml
noarch_platforms: [linux_64, win_64]
```

```yaml
# recipe/recipe.yaml
build:
  noarch: python
  string: "win_pyh{{ PKG_HASH }}_{{ PKG_BUILDNUM }}"   # [win]
  string: "unix_pyh{{ PKG_HASH }}_{{ PKG_BUILDNUM }}"  # [unix]
requirements:
  run:
    - colorama  # [win]
    - __win     # [win]
    - __unix    # [not win]
```

Each variant gets a distinct build string so the artifacts do not collide,
and a `__win` / `__unix` virtual package so the solver installs the one
matching the host. A second spelling writes the platform into the dependency
name through a per-feedstock `noarch_platform` variable:

```yaml
# recipe/recipe.yaml
requirements:
  run:
    - __${{ noarch_platform }}
    - ${{ "colorama" if noarch_platform == "win" else "python" }}
```

Across the eleven conda-forge feedstocks that write it, the variable's values
are `linux`, `osx`, `win` or `unix`. conda-smithy's changelog records that
"recipes with `noarch_platforms` will no longer give a lint when selectors
are used", so both spellings are accepted. `pytest`, the recipe the fixture
came from, has since dropped the idiom and lists `colorama` for every
platform.

### Conditions are string comparisons

v1 §3.3.1.1.

A v1 recipe's `if:` is evaluated by minijinja over the variant's value as
text. `python < "3.13"` compares `"3.9"` against `"3.13"` character by
character, and 3.9 falls outside a range it belongs to. rattler-build's
documentation: "the comparison is a string comparison done by minijinja… use
the `match` function to compare versions." One fleet writes 46
`match(python, …)` against 13 bare comparisons.

### Build targets and their selectors

v1 §3.3.4.

conda-forge builds `linux-64`, `linux-aarch64`, `linux-ppc64le`,
`linux-s390x`, `osx-64`, `osx-arm64`, `win-64` and `win-arm64`. A recipe
selects them by name: `linux`, `osx`, `win`, `unix`, `x86_64`, `aarch64`,
`arm64`, `ppc64le`, `s390x`, and negations of these. A PEP 508 marker spells
the same machines differently: `platform_machine == "arm64"` on macOS,
`"ARM64"` on Windows, and `"AMD64"` for what a recipe selects as `x86_64`.
conda-forge builds no PyPy, so a `platform_python_implementation` marker is
true for CPython everywhere.

### The latest-Python test

v1 §3.7, and conda-smithy's `_python_tests_cover_latest`.

conda-forge's linter hints that a `noarch: python` recipe should test the
latest Python as well as the minimum. The check is skipped when any `run`
requirement is `python` with a `<` in it, because a capped Python makes a
latest-Python test meaningless. Of 45 feedstocks in one set of checkouts
whose python test does not cover the latest, 22 cap Python.

### `.ci_support` names the built Pythons and platforms

v1 §3.3.1.1, §3.3.4.

conda-smithy renders one `.ci_support/*.yaml` per build variant, with the
platform and, for an output built per Python, the Python in the file name.
The set of files is the build matrix: which Pythons an architecture-specific
output is built for, and, for a `noarch: python` output, whether it is built
once or once per platform.

## v0 recipes

v1 §3.1, §7.

A v0 `meta.yaml` does not parse as YAML: `{{ name }}` at the start of a value
opens a flow mapping. A feedstock's recipe format is told by filename,
`recipe/meta.yaml` against `recipe/recipe.yaml`. conda-recipe-manager
converts v0 to v1 and, across 137 v0 feedstocks in one set of checkouts,
produced a recipe.yaml a v1 reader rejects once: a whole-line comment at the
end of one output's `run` list is re-emitted ahead of the next output
without the `-` that opened it, so the second output's keys land in the
first output's mapping.
