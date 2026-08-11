# swage — design plan

> **swage** *(n.)* a shaped block or die a smith uses to work raw stock into a
> consistent standard profile.
>
> **swage** *(v.)* to shape metal with such a tool.

A tool for maintaining a few hundred conda-forge feedstocks: it finishes the
dependency work that conda-forge's version-bump bot leaves undone, keeps recipe
formatting consistent, and gets routine updates merged without a human in the
loop — handing them to conda-forge's automerge machinery where that works, and
merging them itself in the case where that machinery structurally can't (§2.1).

**Status:** design, not yet implemented.
**Repo:** `github.com/xylar/swage` — public, BSD-3-Clause.
**Open development from the start; contributor infrastructure deferred, not
declined.** The repo is public because developing in the open is the default
worth having. During initial development the design is still moving and the
maintainer is the only user, so `CONTRIBUTING.md`, issue templates, and
contributor onboarding docs would be documenting a moving target — they are
deliberately postponed rather than ruled out. Documentation in this period
targets the maintainer and anyone reading to understand the design.

**Revisit at Phase 7** (§10), when the old tools are retired and swage is doing
the whole job. That is also when publishing to conda-forge becomes reasonable,
and a tool other people install is a tool that needs a contributor path.
**Development:** `~/code/swage/main`, with git worktrees as branch-named siblings
(`~/code/swage/<branch>/`), matching the layout of the other projects in `~/code`.
Deliberately *not* under `~/code/conda-forge/`, which holds feedstock checkouts —
swage operates on that directory and should not live inside it.

---

## 1. Problem

Maintaining ~500–600 conda-forge feedstocks means a steady stream of
version-bump PRs from `regro-cf-autotick-bot`. The bot reliably handles the
mechanical part — bumping `version` and `sha256` — but it does not reconcile the
recipe's dependencies against what upstream actually declares. That reconciliation
is the tedious part, and it is what two existing bespoke tools already automate for
the two worst clusters:

| | `airflow-feedstock/providers/update_providers.py` | `google-cloud/update_google_cloud*.py` |
|---|---|---|
| Discovery | git tags on `apache/airflow` | GitHub repo search + maintainer check |
| Upstream metadata | `pyproject.toml` from a tag | sdist `METADATA` / `pyproject.toml` |
| Recipe parsing | line-by-line string scanning | structural, key-order aware |
| Quirks | module-level dicts in Python | module-level dicts in Python |
| Templating | Jinja2 with `[[ ]]`/`[% %]` delimiters | Jinja2 with `{{ }}` delimiters |
| Output | colorized status table, ranked | per-PR success/failure records |
| Endgame | push + `automerge` label | push + wait for manual merge |

They solve the same problem twice with incompatible interfaces, incompatible
config formats, incompatible reporting, and incompatible notions of "done."
swage generalizes both into one tool where the quirks are *data*, not code.

### Goals

1. Reconcile recipe dependencies against upstream (PyPI, sometimes GitHub) metadata.
2. Keep formatting consistent within and across feedstocks.
3. Handle multi-output feedstocks and extras-embedded-in-dependencies correctly.
4. Keep a reviewable, version-controlled database of per-feedstock quirks.
5. Let routine, well-understood updates merge with no human review — while
   guaranteeing that anything novel stops and waits for one.

### Non-goals

- **Bumping `version` / `sha256`.** `regro-cf-autotick-bot` already does this
  reliably. swage reacts to its PRs rather than duplicating them.
- **General `conda-forge.yml` maintenance.** Beyond alphabetizing keys — which is
  not desirable — nearly every meaningful change there requires human judgement.
  The one exception is the v0→v1 migration, where switching to `rattler-build`
  and `pixi` is a mandatory part of the conversion (see §7).
- **Replicating conda-forge's merge gate in the common case.** When swage commits
  a change, the resulting CI run drives conda-forge's own automerge and swage
  stays out of it. swage merges directly only in the one case conda-forge's
  machinery structurally cannot handle — see §5.
- **A web UI**, at least initially. See §9.

---

## 2. The single most important constraint: how conda-forge automerge really works

This came out of reading
[`conda_forge_webservices/github_actions_integration/automerge.py`](https://github.com/conda-forge/conda-forge-webservices/blob/main/conda_forge_webservices/github_actions_integration/automerge.py)
and [`webapp.py`](https://github.com/conda-forge/conda-forge-webservices/blob/main/conda_forge_webservices/webapp.py),
and it dictates the shape of swage's write path.

### 2.1 What triggers automerge — and why a label alone often doesn't

**The `automerge` label is not a trigger.** Nothing in conda-forge's system
watches for it. `automerge.yml` is a `workflow_dispatch`-only workflow: it never
self-triggers, and runs only when the webservices app explicitly dispatches it
with a `(repo, sha)` pair. That dispatch happens in exactly one place —
`StatusMonitorPayloadHookHandler` — on webhook deliveries for `status` events,
`check_suite` *completed* events, and `pull_request` events.

The consequence is severe and non-obvious:

> **Once CI has finished, no further `status` or `check_suite` event will ever
> fire for that commit. Adding the `automerge` label to a PR whose checks have
> already completed dispatches nothing, and the PR sits open forever.**

The handler does contain a `pull_request` dispatch branch, which in principle a
`labeled` action would reach — but that endpoint is the status-monitor payload
hook, whose purpose is feeding the CI dashboard from `check_run`/`status`
deliveries. Whether feedstock webhooks subscribe it to `pull_request` events is
deployment configuration not visible in the source. Empirically, over many years
of maintaining these feedstocks: **they do not.** Treat the label as inert
unless a CI run follows it.

The label works in the normal case only because pushing a commit starts a fresh
CI run, and *that* run's completion events do the dispatching. The label is a
flag the dispatched job reads — never the thing that summons it.

### 2.2 What the dispatched job checks

Once running, there are exactly two ways a PR gets automerged:

**Path A — the `automerge` label.** The PR carries the label, and *no commit
appears in the PR timeline after the most recent `labeled` event*. If a commit
does appear after the label, the bot **strips the label**, comments to say so, and
refuses.

**Path B — a bot PR.** The PR author is an allowed bot, `[bot-automerge]` is in
the title, **every commit is authored by an allowed bot**, and the feedstock has
`bot.automerge: true` in `conda-forge.yml`.

Four consequences fall directly out of this and §2.1:

1. **The label bypasses the `conda-forge.yml` requirement.** The
   `bot.automerge` check lives only in Path B. A labeled PR merges on green CI
   regardless of feedstock config. *swage therefore never needs to edit
   `conda-forge.yml` to make automerge work* — which is exactly the outcome we
   want given the non-goal above.

2. **Order is mandatory: push first, label last.** Labeling before pushing
   guarantees the label gets stripped.

3. **Pushing to a bot PR destroys Path B.** The moment swage commits to a
   `[bot-automerge]` PR, the "all commits are from a bot" test fails forever.
   So on a feedstock that had `bot.automerge: true`, a swage push *removes*
   automation unless swage adds the label. **swage must label every PR it
   pushes to, or it actively makes things worse than not running at all.**
   This is a genuine hazard for any partial-failure path and is called out again
   in §5.

4. **The label is useless when swage changes nothing.** If the recipe already
   matches upstream, swage pushes no commit, so no CI runs, so nothing dispatches
   automerge. Labeling here accomplishes exactly nothing and the PR stalls
   indefinitely. **This case can only be resolved by swage merging the PR
   itself** — see §5.2. It is also the single most common outcome across a few
   hundred feedstocks, which makes it the difference between a tool that saves
   real time and one that just relabels work you still have to do by hand.

A subtlety that follows from (2): re-adding an `automerge` label that is *already
present* is a no-op and produces no new timeline event, so a second push would
still sit "after" the original label event and get stripped. **To re-arm a PR
after a follow-up push, swage must remove the label and then re-add it**, which
creates a fresh `labeled` event with a later timestamp.

---

## 3. Architecture

Layers, bottom to top. Each is independently testable and has no knowledge of
the layers above it.

```
swage/
  config/      layered, schema-validated quirks database        (§4)
  upstream/    PyPI + GitHub metadata -> normalized model
  mapping/     PyPI name -> conda-forge name, with provenance
  recipe/      recipe.yaml / meta.yaml model (conda-recipe-manager)
  plan/        upstream + config + recipe -> RecipePlan + verdict (§5)
  forge/       GitHub: discover, read, clone, commit, push, label
  report/      terminal rendering + JSON run artifacts           (§9)
  cli/         commands                                          (§8)
```

### 3.1 `recipe` — stop parsing YAML with regexes

Both current tools scan recipe text line by line. That is the largest single
source of fragility in them, and it is why the airflow tool refuses to touch
multi-output feedstocks unless they are explicitly enumerated.

swage builds on **[`conda-recipe-manager`](https://github.com/conda/conda-recipe-manager)**
(CRM, `conda-forge::conda-recipe-manager`), Anaconda's recipe parsing library.
It is already the engine underneath `feedrattler`, exposes `RecipeReader` /
`RecipeParser` / `RecipeParserConvert`, edits via JSON-patch operations, and —
critically — is built to preserve comments and formatting through a read-edit-write
cycle. Depending on CRM directly rather than shelling out to `feedrattler` gets us
the parser *and* the v0→v1 conversion from one dependency.

> **Open risk.** CRM is pre-1.0 (0.10.x) and its comment/formatting-preservation
> guarantees are not fully documented. Phase 1 (§10) includes an explicit
> round-trip spike over a corpus of real feedstock recipes *before* any code
> depends on it. If it cannot round-trip cleanly, the fallback is `ruamel.yaml`
> in round-trip mode with a swage-owned recipe model on top — more work, no
> v0→v1 conversion for free, so we would then keep invoking `feedrattler` as a
> subprocess for §7.

### 3.2 `mapping` — name resolution, with provenance

Resolving a PyPI name to a conda-forge name is the step most likely to be
silently wrong, and it is the basis of the trust gate. Resolution is layered,
first match wins:

1. per-feedstock `name_map`
2. per-family `name_map`
3. global `config/name-map.yaml` (seeded from the existing tools' tables)
4. `grayskull`'s accumulated PyPI↔conda-forge knowledge
5. identity (the names match)
6. **unresolved** — not a guess, an explicit failure state

Every resolution returns a `Resolution(conda_name, source, exact: bool)`. Layers
1–3 and 5 are exact; layer 4 is exact only when grayskull reports a known mapping
rather than inferring one. Any unresolved name, or any inexact resolution, means
the feedstock cannot auto-merge (gate **G2**, §5). This is what turns "the tool
guessed and I didn't notice" into a stop condition.

### 3.3 `plan` — the core computation

`plan(recipe, upstream, config) -> RecipePlan`

A `RecipePlan` holds, for each output and each requirements section, the desired
list of requirement lines, where **each line carries a `Provenance`**:

```python
Provenance(
    origin: Literal["upstream-core", "upstream-extra", "config-add", "recipe-kept"],
    detail: str,          # e.g. "extra:pandas", "config/families/google-cloud.yaml"
    mapping: Resolution,
)
```

Provenance is not decoration — it is the mechanism by which the trust gates in §5
are *checked* rather than assumed. A dependency with no provenance is a
dependency swage cannot justify, and a plan containing one is not eligible for
automerge.

### 3.4 `discover` — which feedstocks are mine

Every conda-forge feedstock has a matching org team whose members are its
maintainers, and team membership is what actually grants the push and merge
access swage needs. So the authoritative, cheap answer to "which feedstocks do I
maintain" is one paginated call:

```
gh api --paginate user/teams   # filter to organization.login == "conda-forge"
```

Measured on 2026-08-11: **487 teams**, of which 99 are `apache-airflow-providers-*`
and 50 are `google-cloud-*` — so the two families that already have bespoke tools
are 31% of the total, which is the clearest possible argument for the family
concept in §4. The team slug is the feedstock name minus `-feedstock`.

This replaces the google-cloud tool's approach (search all repos, fetch every
recipe, check `recipe-maintainers`), which costs ~600 API calls instead of ~5.

> **Known approximation.** Team membership and a recipe's `recipe-maintainers`
> list can drift apart. Teams are the right basis for *enumeration* because they
> reflect access; the recipe is the right basis for *attribution*. swage
> enumerates from teams and, before writing to a feedstock, verifies the recipe
> still lists you — cheap at that point, since it has the recipe in hand anyway,
> and it catches the case where you were added to a team but the recipe disagrees.

The list is cached in the run directory with a TTL so repeated commands in one
session don't re-fetch it.

### 3.5 `forge` — API to read, clone to write

Reading recipes and PR state for 600 feedstocks via `git clone` is untenable, so
discovery and reading go through the GitHub REST API (contents endpoint, PR
listing), and only feedstocks that actually need a commit get cloned — into a
disposable cache dir, shallow, one branch.

All GitHub access goes through a single choke point with retry-with-backoff on
`429`/`5xx`/secondary-rate-limit, generalizing `run_gh_api` from the google-cloud
tool. At 600 feedstocks, secondary rate limits are a certainty, not a risk.
Authentication reuses the `gh` CLI's credentials, as both existing tools do.

---

## 4. The quirks database

Version-controlled YAML in the swage repo. Three layers, merged
defaults → family → feedstock, with the more specific layer winning. Every layer
is validated against a pydantic schema, so a typo in a quirk file is a startup
error with a line number, not a silently ignored key.

```
config/
  defaults.yaml               # global policy
  name-map.yaml               # PyPI -> conda-forge, global
  families/
    airflow-providers.yaml
    google-cloud.yaml
  feedstocks/
    google-cloud-bigquery.yaml
    apache-airflow-providers-amazon.yaml
```

A **family** is a glob over feedstock names plus shared upstream/quirk settings;
it is what makes "the airflow providers" a first-class concept rather than a
hardcoded module.

```yaml
# config/families/airflow-providers.yaml
family: airflow-providers
match:
  feedstock: "apache-airflow-providers-*"
upstream:
  source: github
  repo: apache/airflow
  tag: "providers-{slug}/{version}"
  metadata: "providers/{slug_path}/pyproject.toml"
trust: propose                    # this family is not blessed by default
requires_python:
  min: "3.10"                     # error if upstream floor exceeds this
extras_as_outputs:
  suffix: "{name}-with-{extra}"
  supported: [amazon, google, standard, ...]
  skip: [sqlalchemy]              # known-and-deliberately-not-published
name_map:
  docker: docker-py
  kubernetes: python-kubernetes
embedded_extras:
  "pyhive[hive_pure_sasl]":
    - pure-sasl >=0.6.2
    - thrift >=0.10.0
    - thrift_sasl >=0.1.0
  "aiobotocore[boto3]": []        # explicitly "nothing to add", not "unknown"
```

```yaml
# config/feedstocks/google-cloud-bigquery.yaml
feedstock: google-cloud-bigquery
family: google-cloud
trust: auto                       # blessed
outputs:
  google-cloud-bigquery-core:     # the library itself
    run: {core: true}
  google-cloud-bigquery:          # metapackage over the extras we ship
    run: {core: false, extras: [pandas, bqstorage, ipywidgets]}
```

Three points of design worth stating explicitly:

- **`supported` / `skip` must be exhaustive.** An upstream extra appearing in
  neither list is an error that stops the feedstock, exactly as both current
  tools already do. This is what prevents a newly added upstream extra from
  silently vanishing from the recipe. Same rule for `embedded_extras`: an empty
  list means "declared, adds nothing," which is materially different from absent.
- **`outputs` unifies the two tools' divergent models.** The airflow tool's
  `MULTI_OUTPUT_PROVIDER_CONFIG` (extras become separate outputs) and the
  google-cloud tool's `RunConfig(core=, extras=)` (extras get folded into an
  existing output's `run`) are the same idea expressed twice. `extras_as_outputs`
  covers the first; `outputs[].run` covers the second; a feedstock can use both.
- **`trust` is per-feedstock and defaults to `manual`.** Blessing is opt-in and
  explicit. See §5.

---

## 5. Autonomy: what "requires no modification" means

The division of labor is the heart of the design:

> **swage gates on the diff. Something must gate on CI. Which "something"
> depends on whether swage pushed a commit.**

swage decides whether a change is *routine*. What happens next splits along the
line drawn in §2.1, because the trigger mechanism — not policy — forces it.

### 5.1 Path A — swage changed the recipe (delegate to conda-forge)

Push the commit, then apply the `automerge` label as the very next call. The push
starts a fresh CI run whose completion events dispatch conda-forge's automerge
job, which finds the label and merges on green.

This is the preferred path and should stay the common one for changed recipes.
On it, swage never polls CI, never exercises its merge rights, and — most
valuable — inherits conda-forge's own `_get_required_checks_and_statuses` logic,
which works out
which CI providers a feedstock actually uses by inspecting `azure-pipelines.yml`,
`.github/workflows/conda-build.yml`, `.circleci/config.yml`, and friends, and
honors `bot.automerge_options.ignored_statuses`. That logic is more nuanced than
anything swage should reimplement, so where it can run, let it.

### 5.2 Path B — swage changed nothing (swage merges)

The recipe already matches upstream. There is no commit to push, so no CI will
run, so **nothing will ever dispatch conda-forge's automerge job again for that
commit** (§2.1). The label is inert. Left alone the PR stays open forever and
lands back on the maintainer's plate — which is exactly the tedium swage exists
to remove.

So in this case, and only this case, swage owns the merge:

1. **Confirm CI is genuinely finished and green.** Enumerate every check run and
   commit status on the PR head SHA. Determine the required set the same way
   conda-forge does — read the feedstock's CI config files at the PR head via the
   contents API (no clone needed) and read `bot.automerge_options.ignored_statuses`
   from `conda-forge.yml`. Require the required set to be **non-empty** (conda-forge
   refuses to merge when it can't identify a single required check, and so should
   swage), every required check *completed* and *successful*, and **no** non-ignored
   check in a failing state. This is deliberately stricter than conda-forge's rule:
   conda-forge asks "did the required ones pass?", swage additionally asks "is
   anything else broken?"
2. **Confirm the PR is mergeable** — `mergeable` is true and it is not already
   merged.
3. **Comment on the PR**, stating why it is being merged: that swage verified the
   recipe's dependencies already match upstream metadata, which checks it
   verified, and that swage merged it. This is the audit trail, and it is what
   makes an unattended merge reviewable after the fact.
4. **Merge**, matching conda-forge's own convention: `merge_method="merge"`,
   title `{pr.title} (#{pr.number})`, and — critically — **pinned to
   `sha=pr.head.sha`**. The SHA pin makes the merge fail rather than succeed if
   the bot pushed a new commit between swage's check and swage's merge. Without
   it, swage could merge code it never verified.

Do **not** apply the `automerge` label on this path; it does nothing and only
adds noise to the PR timeline.

### 5.3 The extra gate Path B requires

Path B is a stronger claim than Path A. On Path A swage says "my change is
routine" and conda-forge still independently decides to merge. On Path B swage
is the only thing between the bot's PR and `main`. That earns one more gate
beyond G1–G6 below:

> **G7 — byte-identical rendering.** swage must render the recipe from upstream
> metadata and confirm the result is byte-for-byte identical to what is already
> in the PR. "No changes needed" is then a *verified* claim rather than an
> assumption. If swage's rendering differs at all — including in formatting or
> dependency order — that is a change, and the feedstock goes down Path A
> instead.

G7 is what makes "requires no modification" mean something precise and testable.

### 5.4 Trust gates

A feedstock's PR gets the `automerge` label only if **all** of these hold:

| | Gate | Rationale |
|---|---|---|
| **G1** | Every requirement in the plan has a `Provenance` tracing to upstream metadata or an explicit config entry | no unexplained dependencies |
| **G2** | Every name resolution is `exact` — no heuristic guesses, no unresolved names | §3.2 |
| **G3** | Every upstream extra encountered appears in `supported`, `skip`, or `embedded_extras` | a new upstream extra must be triaged by a human |
| **G4** | The set of outputs is unchanged | a new output is a packaging decision |
| **G5** | The diff touches only requirements sections (plus formatting normalization) | anything else is out of scope for autonomy |
| **G6** | `trust: auto` for the feedstock or its family | blessing is explicit and opt-in |
| **G7** | *(Path B only)* swage's rendering is byte-identical to the PR's recipe | §5.3 — makes "no changes needed" verified, not assumed |

Fail any gate and the PR is *still* updated and pushed — the work is not thrown
away — but it is labeled `swage:needs-review` instead of `automerge`, and it
appears in the terminal report's NEEDS REVIEW section with the failing gate named.

The `trust` ladder is `manual` (never push) → `propose` (push, never auto-label)
→ `auto` (push and label when G1–G5 pass). New feedstocks start at `manual`.
Promotion is a deliberate config commit — which, because it lives in git, leaves
an auditable record of when and why each feedstock was blessed.

### 5.5 The partial-failure hazard

Per §2, pushing to a `[bot-automerge]` PR without labeling it leaves that PR
*less* automated than before swage ran. So the push-then-label sequence must be
treated as one unit:

- Label immediately after a successful push, as the very next API call.
- If labeling fails, retry; if it still fails, report the PR as
  **`DEGRADED — pushed but not labeled`** at the top of the report, not buried
  in a success list. This state requires human action.
- On a follow-up push to an already-labeled PR, remove-then-re-add the label to
  produce a fresh timeline event.
- `swage status` (§8) re-detects and re-arms any PR left in this state.

---

## 6. Formatting rules

Consistency is a stated goal, so the formatter is a specified, tested component
rather than an emergent property of a Jinja2 template.

**Dependency ordering** — the rule that rules out naive sorting:

1. Requirements that come from upstream appear **in upstream's own source
   order** (the order they appear in `pyproject.toml` / `METADATA`). Not
   alphabetical. This keeps swage's diffs against upstream small and legible.
2. `python`, then `pip`, come first where they apply (e.g. `host`).
3. Requirements conda-forge needs that upstream does not declare form a
   **separate trailing block, alphabetized**, since they have no upstream order
   to inherit.

**Embedded extras** keep the airflow tool's marker convention, generalized:

```yaml
    - pyhive >=0.6.0
    # start pyhive[hive_pure_sasl]
    - pure-sasl >=0.6.2
    - thrift >=0.10.0
    - thrift_sasl >=0.1.0
    # end pyhive[hive_pure_sasl]
```

These markers make the embedding round-trippable: swage can find the block it
previously wrote, replace its contents, and leave hand-written lines outside the
markers untouched. This is what makes repeated runs idempotent rather than
additive.

**Scope.** Formatting is normalized only on feedstocks swage is already
modifying for a dependency update, plus explicitly on `swage migrate`. No
drive-by reformatting of untouched feedstocks — a formatting-only PR across 600
repos is a bad neighbor to conda-forge CI.

**Property to test:** `format(format(x)) == format(x)`, and
`plan(apply(plan(r))) == no-op`. Idempotence is the thing that makes a tool like
this safe to run on a schedule.

---

## 7. v0 → v1 migration

`swage migrate <feedstock>` converts `meta.yaml` → `recipe.yaml` via CRM's
`RecipeParserConvert`, and — because this is the one context where
`conda-forge.yml` edits are mandatory rather than cosmetic — also sets
`conda_build_tool: rattler-build` and `conda_install_tool: pixi`, generalizing
`_ensure_conda_forge_tools_text` from the airflow tool.

Migration is **always `trust: manual`**, regardless of the feedstock's
configured trust. Conversion is documented as imperfect by both feedrattler and
CRM; a converted recipe gets human eyes and a `swage:needs-review` label, full
stop. This is a deliberate hard-coded exception to the trust ladder, not a
default that can be configured away.

---

## 8. Commands

```
swage scan     [--family F | --feedstock F | --all]   read-only; what would change
swage update   [--family F | --feedstock F]           render, push, label
swage status   [--since 7d]                           closed loop on prior runs
swage audit    [--all]                                read-only hygiene sweep
swage migrate  <feedstock>                            v0 -> v1
swage explain  <feedstock>                            why did it decide that?
```

- **`scan`** is the default gesture and touches nothing. It reports the plan and
  the trust verdict per feedstock. `update` is `scan` plus writes; it is
  dry-run by default and requires `--execute` to push.
- **`status`** closes the loop, and does real work rather than only reporting.
  It answers: of the PRs swage touched, which merged, which are still running CI,
  which had CI *fail*, and which are `DEGRADED` (§5.5) and need re-arming. It
  also **re-runs the Path B merge check** on every no-change PR whose CI was
  still running during the last `update`, and merges the ones that have since
  gone green. This matters because Path B's precondition — CI already finished —
  is often not yet true at `update` time on a freshly opened bot PR. `update`
  catches the ones that are ready; `status`, run later or from cron, sweeps up
  the rest. This is the report you read the morning after.
- **`explain`** dumps the full provenance chain for one feedstock: upstream
  metadata fetched, config layers applied, each name resolution and its source,
  each gate and its verdict. Debugging a quirks database without this is
  miserable, and the two existing tools have taught us that the "why did it do
  that?" question comes up constantly.

---

## 9. Output

Primary interface is the terminal, modeled on the airflow tool's ranked,
colorized summary — which is genuinely good and worth keeping — grouped by
outcome so the actionable items are unmissable:

```
swage update --family google-cloud            2026-08-11 14:02      (312 scanned)

  MERGED (28)          path B: no changes needed, CI already green, merged
  MERGE-READY (41)     path A: pushed + labeled automerge, awaiting CI
  AWAITING CI (13)     path B candidates, CI still running -- `swage status` later
  PROPOSED (12)        pushed, needs your review before labeling
  NEEDS REVIEW (9)
    google-cloud-aiplatform      G3: undeclared upstream extra 'evaluation'
    google-cloud-bigquery        G2: unresolved name 'db-dtypes'
  DEGRADED (1)                   pushed but NOT labeled -- rerun `swage status`
    google-cloud-spanner         label API call failed after 3 attempts
  UNCHANGED (206)      no open bot PR
  FAILED (2)

  run: ~/.cache/swage/runs/2026-08-11T14-02/
```

`AWAITING CI` is the bucket that makes `swage status` load-bearing rather than
cosmetic: those PRs need nothing from you, but nothing will merge them either
until swage looks again.

Each run also writes a directory containing a structured `run.json` (the full
plan, provenance, and verdicts) plus per-feedstock recipe diffs. That directory
is disposable — everything durable lives in git.

`run.json` being a stable, documented schema costs nothing now and is the only
thing a future web dashboard would need. **No web UI is planned**; the door is
left open rather than walked through.

### 9.1 Unattended-safe by construction

Scheduling is not built, but every command is designed to be safe to run
unsupervised, because `swage status` genuinely wants to run on a timer — it is
what sweeps up the Path B merges whose CI finished after the `update` run (§5.2).
Concretely that means:

- **No command ever prompts.** Anything needing a human decision resolves to
  `needs-review` and exits non-zero-but-not-failed, rather than blocking.
- **Exit codes are meaningful:** `0` nothing needs you, `1` items need review,
  `2` swage itself failed. A cron wrapper can alert on `1` and page on `2`.
- **`run.json` is the machine-readable contract**, versioned with a `schema`
  field, so a scheduler or future dashboard reads it instead of scraping the
  terminal output.
- **Every run is idempotent** (§6) — a duplicate or overlapping invocation must
  not double-push or double-merge.
- **A lockfile in the cache dir** prevents two concurrent runs from racing on the
  same feedstock.

Running it from cron or GitHub Actions should then be a wrapper the user writes,
not a feature swage ships.

---

## 10. Delivery plan

Each phase is independently useful and ends in something runnable.

**Phase 0 — skeleton.** `pyproject.toml` + hatchling, `src/` layout, ruff,
mypy strict, pytest + coverage, GitHub Actions on Linux/macOS/Windows, mkdocs.
`pixi.toml` for the dev environment, matching existing practice. Config schema
and loader; no behavior. *Ends with:* `swage --help` and a validated config tree.

**Phase 1 — read-only `scan`.** Upstream fetchers, mapping, CRM-backed recipe
model, planner, trust gates, terminal report. Nothing writes. Begins with the
**CRM round-trip spike** (§3.1) — this phase does not proceed until we know
whether CRM preserves formatting on real recipes. *Ends with:* `swage scan` over
the google-cloud family producing a plan.

**Phase 2 — differential validation.** Run `swage scan` and the two existing
tools over the same inputs and diff the rendered recipes. This is the phase that
earns the right to write anything, and it is cheap because the corpus already
exists (§11).

**Phase 3 — `update` writes (Path A).** Clone, commit, push, label — with the
push-then-label unit and the DEGRADED path from §5.5 built in from the start, not
bolted on. Dry-run default, `--execute` to push. First real use on a handful of
`trust: propose` feedstocks.

**Phase 3.5 — merge (Path B).** The CI-verification logic and direct merge from
§5.2, deliberately sequenced *after* pushing is proven in practice. Ships in two
steps: first report-only (`WOULD MERGE`, listing the checks it verified), so the
verification logic can be audited against feedstocks you then merge by hand and
compare; only once that agrees consistently does the actual `pr.merge()` call get
enabled. This is the one place swage takes an irreversible action nobody reviews,
so it earns the extra caution.

**Phase 4 — `status`.** Closes the loop, and sweeps up the Path B candidates
whose CI finished after the `update` run. After this, the tool is doing the job
described in the original ask.

**Phase 5 — `audit`** across all ~600 feedstocks, read-only.

**Phase 6 — `migrate`** (v0→v1).

**Phase 7 — retire the old tools.** Port the airflow and google-cloud quirks into
`config/families/`, run both old and new in parallel for a release cycle,
then delete the old scripts. This is also the point to add the contributor
infrastructure deferred at the top of this document — `CONTRIBUTING.md`, issue
templates, a documented config schema for people writing their own quirks — since
by then the design has stopped moving and publishing to conda-forge is reasonable.

---

## 11. Testing

The single biggest asset here is that **a golden-test corpus already exists**.
The airflow tool's working directories are literally input/expected-output
triples:

```
providers-databricks_7.18.1/
  pyproject.toml     <- upstream metadata (input)
  old_recipe.yaml    <- recipe before      (input)
  recipe.yaml        <- recipe after       (expected output)
```

There are dozens of these on disk already, spanning single-output and
multi-output providers, and the google-cloud tool's `feedstocks/` checkouts
provide the same for that family. Phase 1 should vendor a curated subset into
`tests/corpus/` as the primary regression suite.

- **Golden tests:** corpus triple → rendered recipe, byte-compared. Catches
  formatting regressions, which are otherwise invisible.
- **No network in tests.** HTTP interactions recorded as fixtures; the GitHub
  and PyPI layers are behind interfaces with fake implementations.
- **Property tests** for idempotence (§6) and for the ordering rule.
- **Trust-gate tests** are the highest-value tests in the suite: each of G1–G6
  gets an explicit case proving it *blocks* a plan it should block. A false
  negative here means an unreviewed bad recipe merges automatically, so these
  are tested for refusal, not just for acceptance.
- **Automerge-sequence tests** asserting push-strictly-before-label, and
  remove-then-re-add on follow-up pushes (§2).
- **Merge-gate tests** are the other half of the trust-gate suite, and are
  likewise tested for *refusal*: a pending check, a failing non-required check,
  an empty required-check set, a non-mergeable PR, and — most importantly — a
  head SHA that moved between verification and merge, which must fail the merge
  rather than succeed against unverified code (§5.2).

---

## 12. Open questions and risks

| Risk | Mitigation |
|---|---|
| CRM is pre-1.0; formatting preservation unverified | Spike first in Phase 1; `ruamel.yaml` + `feedrattler` subprocess as fallback (§3.1) |
| GitHub secondary rate limits at 600 feedstocks | Single choke point with backoff; conditional requests; `scan --all` is not the common path |
| conda-forge changes its automerge logic or dispatch triggers | The push/label sequencing and the Path B merge live in one module with their own tests; §2 documents the source files to re-check. If conda-forge ever dispatches on `labeled`, Path B can be retired in favor of Path A |
| swage's required-check detection misses a CI provider, so it merges something conda-forge would have held | Require a non-empty required set; require *no* non-ignored check failing, not just that required ones passed; ship report-only first (Phase 3.5) and diff against hand-merges before enabling |
| The bot force-pushes between swage's CI check and its merge | `sha=pr.head.sha` pin on the merge call turns the race into a clean failure instead of merging unverified code (§5.2) |
| Blessing a feedstock that later goes novel | Gates are evaluated per-run, not per-blessing — `trust: auto` only permits automerge, G1–G5 still must pass every time |
| grayskull/feedrattler/CRM release churn | Pin with floors, test against latest in a scheduled CI job |

**Name availability.** `swage` is free on conda-forge and on `github.com/xylar`.
PyPI `swage` is taken by a 0.0.1 placeholder ("package name placeholder",
uploaded 2026-01-15) — dormant squats like this are reclaimable under PEP 541,
and failing that `conda-swage` is free on PyPI. Since conda-forge is the intended
distribution channel, this does not block anything.

**Deferred decisions:**

- Whether `swage status` should also handle *reacting* to CI failure (e.g.
  auto-reverting its own commit) or only report it. Report-only for now.
- Whether to support proactive upstream watching (acting before the bot opens a
  PR) for feedstocks where the bot is unreliable. Deliberately out of scope for
  v1; the config schema leaves room under `upstream:`.
- Whether families should be able to compose (a feedstock in two families).
  Single-family for now.
