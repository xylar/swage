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
  recipe/      recipe.yaml model, ruamel-backed                   (§3.1)
  plan/        upstream + config + recipe -> RecipePlan + verdict (§5)
  forge/       GitHub: discover, read, clone, commit, push, label
  report/      terminal rendering + JSON run artifacts           (§9)
  cli/         commands                                          (§8)
```

### 3.1 `recipe` — stop parsing YAML with regexes

Both current tools scan recipe text line by line. That is the largest single
source of fragility in them, and it is why the airflow tool refuses to touch
multi-output feedstocks unless they are explicitly enumerated.

**swage's recipe model is v1 only, and v0 feedstocks are routed, not parsed.**
Most of the fleet is still v0: 335 of the 550 recipe directories in the
maintainer's checkouts carry `meta.yaml` rather than `recipe.yaml`. A v0 recipe
is not merely a different dialect — `{{ name }}` at the start of a value opens a
YAML flow mapping, so a v0 file does not parse as YAML at all. Surfacing that as
"invalid YAML" would make the single most common condition in the fleet look
like a corrupt file. swage detects v0 by the filename, before reading anything,
and reports the feedstock as **NEEDS MIGRATION** (§9). It is a routing decision,
not a failure — and `swage update --migrate` (§7.1) turns it into a conversion
and a dependency update in the same pull request, so that migrating does not cost
a wait for the bot to redo its work.

The intended dependency was
**[`conda-recipe-manager`](https://github.com/conda/conda-recipe-manager)**
(CRM, `conda-forge::conda-recipe-manager`), Anaconda's recipe parsing library —
already the engine underneath `feedrattler`, and documented as preserving
comments and formatting through a read-edit-write cycle. The round-trip spike
this section demanded found otherwise, so **swage uses `ruamel.yaml` with its own
recipe model instead.**

> **Spike outcome** (`spikes/crm_roundtrip.py`, CRM 0.10.5, 189 real recipes).
> CRM *reads* well: no parse failures, 74% render byte-identical, and no comment
> is ever lost on a plain round trip. It cannot carry the write path. Appending
> to or removing from a requirements list corrupts recipes where a comment
> trails a list — the comment is re-anchored to the next output and consumes its
> `- ` marker. Replacing a whole list, which is what swage actually does, never
> corrupts but silently deletes every comment inside the list, and
> `add_comment()` cannot restore them because it only emits trailing same-line
> comments. Decisively, comments do not follow their subject when a list is
> reordered, and §6 requires ordering by upstream source order — so a
> "more restrictive for python >=3.14" note ends up above an unrelated
> dependency, in a recipe that is valid YAML and silently false. The root cause
> is that CRM attaches a standalone comment to the *following* node rather than
> modelling it as a document element.

The consequences of the fallback are the ones anticipated here: more work, and
no v0→v1 conversion for free. CRM remains the right tool for that conversion, so
§7 keeps it — as a dependency used only there, or as a `feedrattler` subprocess.

**How swage writes.** ruamel parses for structure and source positions; swage
rewrites *only* the requirements blocks, splicing them back into the original
text by line range. Everything outside a requirements block is therefore
byte-identical by construction, which turns gate G5 — "the diff touches only
requirements sections" — from something to verify after the fact into something
that cannot be false. swage owns the contents of those blocks, including their
marker comments (§6), so it renders them rather than preserving them.

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

#### 3.3.1 Reconciling constraints across environment markers

A project routinely declares one dependency several times, differentiated by
environment markers. `apache-airflow-providers-databricks` declares:

```
pandas>=2.1.2; python_version <"3.13"
pandas>=2.2.3; python_version >="3.13" and python_version <"3.14"
pandas>=2.3.3; python_version >="3.14"
```

conda-forge builds **one `noarch: python` package**, installed on every Python
from `python_min` (§3.3.3) upward. The recipe therefore gets a single `pandas`
line, and that line has to hold for every Python in the range. Per package, after
name resolution:

1. **Discard requirements whose marker cannot be true for any Python ≥
   `python_min`.** This is what makes a `python_version < "3.9"` variant
   disappear rather than participate in the next step.
2. **Intersect the specifiers of everything that survives.**
3. **An empty intersection is a stop, never a guess** (§3.3.2).
4. Otherwise emit the intersected constraint. Where the binding bound came from a
   marker-qualified variant, emit the marker comment recording it —
   `# more restrictive for python >=3.14` — which is what stops the recipe
   looking like a mistake to the next reader.

Step 2 is deliberately stricter than upstream. On Python 3.10 upstream would
accept `pandas >=2.1.2` and the recipe will demand `>=2.3.3`. A single artifact
cannot do better, and the comment in step 4 is what makes that legible rather
than mysterious.

**A marker swage cannot reduce to the Python-version axis stops the feedstock**
— but not because no answer exists. See §3.3.4: the reason is that every
available answer is a packaging decision rather than a reconciliation.

#### 3.3.2 Contradictory constraints stop the feedstock

Constraints that do not overlap have no valid single answer:

```
pandas<2.1.2 ; python_version <"3.13"
pandas>=2.3.3; python_version >="3.13"
```

With `python_min` at 3.9 both markers are reachable, the intersection is empty,
and no version of `pandas` satisfies the package on every Python it will be
installed on. swage stops that feedstock, reports it under **FAILED** with the
conflict quoted, and exits 1. The message has to be enough to act on without
re-deriving anything:

```
apache-airflow-providers-databricks                                  FAILED
  contradictory upstream constraints for 'pandas'
    pandas<2.1.2     ; python_version < "3.13"
    pandas>=2.3.3    ; python_version >= "3.13"
  no single version satisfies both across python >=3.9 (python_min),
  and conda-forge builds one noarch package for all of them
  resolve by hand, or pin the intended constraint in
  config/feedstocks/apache-airflow-providers-databricks.yaml
```

> **The prior art gets this wrong, silently.** The airflow tool's
> `_merge_requirement_group` gathers only `>=` bounds, takes the highest, and
> `continue`s past any variant that has none. Given the pair above it emits
> `pandas >=2.3.3` and the `<2.1.2` bound vanishes with no warning, no comment,
> and no entry in the summary. That is precisely the class of failure swage
> exists to eliminate, and it is why the contradiction check is a stop rather
> than a warning: a warning in a run over several hundred feedstocks is a
> message nobody reads.

#### 3.3.3 `python_min` is read from the pull request, never fetched

`python_min` originates as conda-forge's global pinning value — **3.9** at the
time of writing, per CFEP-25 — and it moves whenever conda-forge drops a Python.
Recipes refer to it symbolically as `${{ python_min }}`, so the number is not in
the recipe text. swage nonetheless never fetches it, because the value that
matters is the one *this* feedstock builds with, and that is already resolved
inside the pull request swage is reading:

1. **`recipe.yaml`'s own `context.python_min`**, where the recipe sets one. That
   is what `${{ python_min }}` expands to in that recipe, so it wins outright.
   It does not control which Python the build runs on — `variants.yaml` does
   that — but swage cares about what the recipe's own references mean.
2. **`.ci_support/*.yaml`** otherwise. conda-smithy renders one per build variant
   with the global pinning and any feedstock-level `conda_build_config.yaml`
   already folded in, so it is the resolved answer rather than an input to one.
   Any single file will do: `python_min` cannot differ per architecture, so the
   first one read is the answer.

Both are files swage already has in hand, which removes the problem rather than
solving it. No fetch, no cache, no TTL, and no window in which swage evaluates
markers against a `python_min` conda-forge has since moved. A value that changes
the meaning of every environment marker in §3.3.1 should not also be one that can
go stale behind swage's back.

Where neither source exists — a feedstock conda-smithy has never rendered — swage
stops rather than assuming, and `requires_python.min` is not a fallback for it.

**This is not the same number as `requires_python.min` (§4), and conflating them
is a bug waiting to happen.** `requires_python.min` is swage's *policy* floor —
refuse a feedstock whose upstream Python floor rises above it, because that is a
packaging decision a human should see. `python_min` is conda-forge's *build*
floor, and it alone defines the range markers are evaluated over. They are 3.10
and 3.9 respectively today: exactly the one-version gap that would let a
marker-evaluation bug pass every test written against the wrong one.

#### 3.3.4 Platform markers have answers, and none of them are swage's to pick

An upstream dependency can be conditioned on the platform rather than on the
Python version:

```
pywin32>=306; sys_platform == "win32"
```

It is tempting to conclude that a `noarch: python` package simply cannot express
this. That is wrong, and the correction matters. conda-forge supports it through
conda-smithy's `noarch_platforms`, which builds the noarch package once per
listed platform; conda-smithy's own test fixture for the feature shows the whole
idiom:

```yaml
# conda-forge.yml
noarch_platforms: [linux_64, win_64]
```

```yaml
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

Each variant gets a distinct build string so the artifacts do not collide, and a
`__win` / `__unix` virtual package so the solver installs the one matching the
host. So a platform-conditional dependency has two real resolutions:

1. **Set up `noarch_platforms`** and condition the dependency, as above.
2. **Depend on it unconditionally**, shipping a package that is inert elsewhere.
   This is what feedstocks usually do in practice — `pytest`, the very recipe
   conda-smithy's fixture is built from, has since migrated to v1 and now lists
   `colorama` for every platform.

**swage stops on a platform marker because both of those are packaging decisions,
not reconciliations.** Option 1 edits `conda-forge.yml`, which §7 puts off-limits,
and adds per-platform build strings, which gate G5 forbids by confining the diff
to requirements sections. Option 2 ships users a dependency they will never load
— frequently the right call, and still a judgement about what the package
promises. Neither is inferable from upstream metadata.

The stop already exists, as it happens. The recipe reader refuses a requirements
list whose items are not plain strings (§3.1), and a conditional dependency in a
v1 recipe is exactly that — an `if:` / `then:` mapping. A feedstock already using
this idiom stops at read time today, naming the line, rather than being silently
mis-rewritten.

**How rare is this?** None of the 176 `noarch: python` recipes in the maintainer's
local checkouts use `__win` or `__unix`, and the canonical example has dropped it.
Rare enough not to build for, real enough that swage must not corrupt one it
meets. Teaching the recipe layer to read and preserve `if` / `then` requirements,
so that a feedstock already configured this way can be maintained rather than
refused, is the natural follow-up — deliberately not attempted before something
needs it.

#### 3.3.5 Build-variant switches put a feedstock out of scope entirely

A few feedstocks build both an arch-specific and a `noarch` package from one
recipe, switched by a variable the feedstock invents for itself.
[`markupsafe`](https://github.com/conda-forge/markupsafe-feedstock/tree/main/recipe)
is the example:

```yaml
# recipe/conda_build_config.yaml
use_noarch:
  - true    # [linux64]
  - false
```

```yaml
build:
  noarch: python             # [use_noarch]
  track_features:
    - markupsafe_no_compile  # [use_noarch]
requirements:
  host:
    - python {{ python_min }}    # [use_noarch]
    - python                     # [not use_noarch]
  run:
    - python >={{ python_min }}  # [use_noarch]
    - python                     # [not use_noarch]
```

Two builds come out of that: a compiled one, and a pure-Python one that
`track_features` deliberately deprioritizes so the compiled build wins wherever
both exist.

**This breaks the assumption every rule above rests on.** §3.3.1 intersects
constraints across the whole Python range *because there is one artifact*. Here
there are two, with genuinely different Python requirements, and intersecting
them produces a `run` section that is wrong for both. Worse, the requirements
sections hold mutually exclusive alternatives of the *same* dependency, selected
by something that is neither a platform nor a Python version — swage's model has
one list per output and no way to say "these two lines are alternatives, pick by
variant". Rewriting the list would silently collapse them into one.

So swage **refuses the feedstock outright**, by name, before planning starts:

```
markupsafe                                                           FAILED
  unsupported build-variant switch: use_noarch
  recipe/conda_build_config.yaml defines use_noarch, and the recipe uses it to
  build both a compiled and a noarch package with different requirements
  swage reconciles one noarch artifact at a time and would collapse those into
  a single wrong answer -- update this feedstock by hand
```

This is a **feedstock-level precondition**, not a recipe-parsing one: it is
checked by whatever reads the feedstock's files (§3.5), before a plan exists,
because the point is to not start. Detection is either a `noarch` value that is
conditional rather than a plain scalar, or a `recipe/conda_build_config.yaml`
defining a variable the recipe then uses in a selector. The first alone catches
`markupsafe`.

Supporting this properly would mean modelling a requirements section as several
variant-conditioned lists and producing a plan per variant — a real change to the
core model, worth making only if enough feedstocks need it. Until then the
failure is loud and specific, which is the whole requirement: a feedstock swage
cannot safely touch should say so in a way that sends the maintainer straight to
the reason.

#### 3.3.6 Which lines swage owns

Not every line in a requirements section came from upstream, and treating them
alike breaks immediately.

**Recipe-owned lines** are preserved verbatim and never sent through name
resolution:

- lines whose *name* is a template expression rather than a package name —
  `${{ pin_subpackage(name, exact=True) }}`, `${{ compiler('c') }}`,
  `${{ stdlib('c') }}`. The test is on the name specifically: `pandas >=${{ x }}`
  has the name `pandas` and is an ordinary upstream dependency whose constraint
  happens to be templated.
- `python` and `pip`, which are conda-forge conventions rather than anything
  upstream declares

The build backend in `host` is **not** on this list, though it looks like it
should be. `flit-core ==3.12.0` comes from upstream's
`[build-system] requires = ["flit_core==3.12.0"]`, exact pin included — it is
upstream-derived metadata that happens to live in a different table of
`pyproject.toml` than the runtime dependencies. Reconciling `host` means reading
that table too.

The recognized set is data rather than code, so blessing a new expression is a
reviewable config commit instead of a release, and a family or feedstock can
extend it where a recipe does something local:

```yaml
# config/defaults.yaml
recipe_owned:
  functions: [pin_subpackage, pin_compatible, compiler, stdlib]
  names: [python, pip]
```

These lines carry `Provenance(origin="recipe-kept")`. Without them
`${{ pin_subpackage(...) }}` would reach the mapper, fail to resolve, and G2
would block **every multi-output feedstock in the fleet** — the rule is
load-bearing, not a refinement.

**`recipe-kept` is an allowlist, never a fallback**, and that distinction carries
more weight than it first appears. A templated name swage does not recognize is
*preserved unchanged* — swage never rewrites what it does not understand — but it
gets no provenance, so G1 stops the feedstock with the expression quoted:

```
google-cloud-bigquery                                        NEEDS REVIEW
  G1: unrecognized template in /outputs/0/requirements/run
    ${{ pin_compatible('numpy') }}
  preserved unchanged; add to recipe_owned in config to bless it
```

That is goal 5 doing its job — anything novel stops and waits for a human — and
blessing it afterwards is one line of config. It also protects §3.3.7: were
`recipe-kept` a fallback for "swage could not explain this", every never-upstream
dependency would quietly acquire provenance, sail through G1, and the protection
against deleting undocumented maintainer intent would evaporate. The two rules
only hold each other up while this one stays an allowlist.

**swage plans `host` and `run`, and writes nothing else.** `build` holds
compilers and cross-compilation helpers that have no relationship to upstream
metadata, and never appears in a change set. `run_constraints` is a longer story
with a gate of its own — see §3.3.9.

**The `python` line stays symbolic.** conda-forge writes `python ${{ python_min }}.*`
in `host` and `python >=${{ python_min }}` in `run` precisely so the floor moves
when conda-forge moves it (§3.3.3), and swage does not replace that with a
literal. The one case where those lines are wrong is when upstream's
`requires-python` floor rises above conda-forge's `python_min` — which is exactly
the condition `requires_python.min` (§4) turns into a stop, because raising a
package's Python floor is a packaging decision with consequences for everyone
downstream, not a dependency reconciliation. So swage either leaves the `python`
line alone or stops; it never rewrites it.

#### 3.3.7 Two kinds of removal, and only one of them is a removal

The planner decides, for each line, whether to add, keep, or remove it. Adding
what upstream declares is routine. "Remove" turns out to be two different
operations wearing the same name, and conflating them is how a tool like this
destroys work.

**Upstream-dropped.** The dependency is in the metadata for the version the
recipe currently reflects and *absent* from the metadata for the version the bot
is bumping to. Upstream made an observable change; the recipe is stale. This is
the exact mirror of an addition — same evidence, same confidence — and it is the
only case where swage can honestly say the dependency is no longer needed.

**Never-upstream.** The dependency is in the recipe and in *neither* version's
metadata. Something put it there on the conda-forge side: a runtime import
upstream forgot to declare, a package conda-forge splits differently, a
workaround for something broken elsewhere — or nothing at all, and it is drift.
swage cannot tell intent from accident by looking, and **removing it would undo a
maintainer decision that was never written down.**

> **swage never removes a never-upstream requirement.** It keeps the line and
> reports it. Keeping preserves a decision that might exist; removing destroys
> one that might. Between two unknowns, only one of them is recoverable.

That case is already covered by an existing gate, which is a good sign the model
is right: a line with no upstream and no config entry has no `Provenance`, so G1
blocks it. The resolution is not for swage to guess but for the maintainer to
write the intent down — `add_requirements` in the quirks database (§4) — after
which the line has provenance, G1 passes, and it is kept for a stated reason
rather than by inertia. A recipe that has been through swage once is a recipe
whose conda-forge-only dependencies are documented, which is worth more than the
removal would have been.

**Telling them apart costs a second fetch.** Classification requires upstream
metadata for *both* versions — the one the recipe reflects and the one being
bumped to. The old version is read from the recipe at the pull request's base
commit. Where the old metadata cannot be fetched at all (a yanked release, a
deleted tag), the removal is **unclassified** and treated as never-upstream: the
safe direction, since the whole point is that swage does not delete on a guess.

#### 3.3.8 Removals are gated during a proving period, not forever

The long-term intent is that an upstream-dropped removal is as routine as an
addition, and merges on the same terms. It is not treated that way yet, because
"swage correctly identified that upstream dropped this" is a claim with no track
record behind it, and the failure mode is silent: a dependency that vanishes from
a recipe is invisible until something fails to import.

So removals are governed by policy rather than by a permanent rule:

```yaml
# config/defaults.yaml
removals: review          # review | auto
```

> **G8 — removals need review while `removals: review`.** A plan that drops an
> upstream-dropped requirement is labeled `swage:needs-review` regardless of the
> other gates, and the report names the dropped lines and the version they
> disappeared in. Under `removals: auto` an upstream-dropped removal is an
> ordinary change and G8 does not apply. A **never-upstream** line is never
> removed under either setting — that is §3.3.7, not a policy knob.

This is the trust ladder (§5.4) applied to an operation rather than to a
feedstock, and it is promoted the same way: deliberately, in a config commit, once
there is evidence. The exit criterion is concrete — a body of reviewed removals
where swage's classification was right every time, accumulated during Phase 3
and reviewed at Phase 4 (§10). Until then the cost is bounded: most bot PRs add
or bump, so gating removals leaves the common case untouched.

Two clarifications, because both are easy to get wrong:

- A line disappearing from inside an embedded-extras marker block (§6) is still
  a removal, and is classified the same way. Where it sat does not change what
  happened to it.
- Recipe-owned lines (§3.3.6) are never removals — they are kept by definition,
  not by a decision the planner makes.

G8 interacts with G7 only trivially: a removal means swage's rendering differs
from the PR's recipe, so the feedstock is on Path A regardless.

#### 3.3.9 `run_constrained` is read, never authored

Many conda-forge recipes use `run_constrained` to express an upstream extra: *if
you install pandas alongside this package, it must be at least this version.* It
is a natural-looking translation and a mistaken one. An extra is a set of
dependencies a user opts into; a `run_constrained` entry is a compatibility bound
imposed on everyone who happens to have that package in the same environment. The
two coincide sometimes and diverge quietly the rest of the time, and treating
them as equivalent causes about as many problems as it solves.

swage takes three positions on this, in decreasing order of firmness.

**swage never adds a `run_constrained` entry.** Not by default, not behind a
config flag. Putting an upstream extra into a recipe at all is a packaging
decision with real cost: on PyPI an extra is free, while on conda-forge it is a
package — a build, CI time, and a name someone maintains forever. The right
mechanism is usually an additional output, often a bundled one, and whether an
extra earns an output turns on whether some downstream conda-forge package would
benefit, or whether the bundle is something users would actually want. Those are
judgements about the ecosystem, and no metadata anywhere contains them. This is
G4's principle — a new output is a packaging decision — applied to the other
mechanism for the same reason. swage declines both routes to "this extra belongs
in the recipe".

**swage never removes one either**, for the reason in §3.3.7: an entry it cannot
attribute may encode a decision nobody wrote down.

**swage may eventually update one, once it is told what the entry means.** It
cannot today, because nothing in a recipe records which upstream extra — if any —
a given entry came from, and inferring it would be precisely the translation the
first rule rejects. The quirks database can carry the association:

```yaml
# config/feedstocks/<feedstock>.yaml
run_constraints:
  pandas: {extra: pandas}     # this bound tracks upstream's `pandas` extra
  jinja2: {extra: null}       # deliberate, and tracks nothing upstream
```

With that written down, a change to the extra's constraint can propagate. Without
it, every entry is left exactly as found.

> **G9 — every `run_constrained` entry is associated.** A recipe containing an
> entry that no config association explains is labeled `swage:needs-review`, with
> the unassociated entries named. The recipe is still updated — `host` and `run`
> are reconciled as usual — but a human proofreads before it merges.

No feedstock has associations yet, so today every recipe with a `run_constrained`
section lands in needs-review. That is the intended starting state rather than a
transitional annoyance. swage has just rewritten a `run` section whose
`run_constrained` entries may have been derived from the very same extras, and it
has no way to check whether the two still agree. The gate makes that uncertainty
visible instead of silent, and it retires itself one feedstock at a time as the
associations get written down.

#### 3.3.10 Attributing a line, and the four answers

Every requirement already in a recipe has to be explainable, and G1 is where that
is enforced. Attribution runs in order, and the outcomes are not
interchangeable — each carries a different message because each has a different
fix:

1. **recipe-owned** — a recognized template expression, or `python`/`pip`
   (§3.3.6). Origin `recipe-kept`.
2. **upstream core** — in the metadata's own dependency list. Origin
   `upstream-core`.
3. **a listed extra** — in an extra this output draws from. Origin
   `upstream-extra`, detail `extra:<name>`.
4. **an unlisted extra** — present upstream, but only under an extra this
   feedstock does not list. **G1 fails**, naming the extra.
5. **`add_requirements`** — a conda-forge-only dependency someone wrote down
   (§4). Origin `config-add`.
6. **nowhere at all** — in no upstream version (§3.3.7). **G1 fails**: declare it
   in `add_requirements`, or drop it.

Order matters between 3 and 4: a dependency belonging to both a listed and an
unlisted extra is explained by the listed one and needs no further thought.

**The two failures are one gate with different advice, and the difference is the
point.** Case 6 sends the maintainer to `add_requirements`. Case 4 must not —
there the fix is almost always to *list the extra*, so that swage maintains the
line from now on, and pointing at `add_requirements` would quietly convert a
maintainable dependency into a hand-managed one. Same verdict, opposite
remedies.

Case 4 is also what makes the non-exhaustive model safe. `outputs[].run.extras`
deliberately ignores extras it does not name (§4); without this check a recipe
could carry an unlisted extra's dependencies with nothing maintaining them, going
stale as upstream moves and never saying so. It is the rule the google-cloud tool
already implements, and it is what turns "ignore unlisted extras" from a quiet
default into a safe one.

Detecting case 4 means mapping every unlisted extra's dependencies through the
name resolver to compare them against conda-side names — real work in the planner
rather than a lookup. It earns its cost by being the difference between the right
advice and confidently wrong advice on a case that recurs.

**A pattern worth naming, since this is its third appearance.** `recipe_owned`
(§3.3.6), `add_requirements` (§3.3.7), and `run_constraints` here are the same
mechanism three times: swage refuses to act on what it cannot attribute, the
quirks database supplies the attribution, and the refusal retires itself as the
database fills in. Each looked like a special case on its own; together they are
the design working as intended. A fourth instance should be built the same way
rather than invented afresh.

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
    run:
      core: false
      extras: [pandas, bqstorage, ipywidgets]
      skip: [dbapi, tests]        # opts this output into exhaustiveness (G3)
add_requirements:                 # conda-forge needs these; upstream never says so
  run:
    - grpcio-gcp >=0.2.2          # conda-forge splits the grpc extra differently
```

`add_requirements` is how a conda-forge-only dependency stops being unexplained.
Without an entry, a line in the recipe that appears in no upstream version has no
`Provenance`, fails G1, and stops the feedstock — deliberately, because the
alternative is swage deciding on its own whether a maintainer meant it (§3.3.7).
With an entry it is kept for a stated reason. These lines are also what §6 places
in the alphabetized trailing block, since they have no upstream order to inherit.

Two policies live in `defaults.yaml` alongside `trust`:

```yaml
# config/defaults.yaml
trust: manual
removals: review                  # review | auto -- see §3.3.8
recipe_owned:                     # see §3.3.6
  functions: [pin_subpackage, pin_compatible, compiler, stdlib]
  names: [python, pip]
```

Three points of design worth stating explicitly:

- **`supported` / `skip` must be exhaustive, where a feedstock publishes extras
  at all.** An upstream extra in neither list means swage cannot tell whether it
  was considered and declined or simply never noticed, so the feedstock is
  labeled `swage:needs-review` naming the extra (G3). The dependency update is
  still pushed — the resolution is a human deciding to add an output or to write
  the extra into `skip`, and neither is urgent enough to withhold an otherwise
  correct update. `skip` **is** the mechanism for "deliberately not published";
  an entry there is a decision on the record rather than an omission.
  Same rule for `embedded_extras`: an empty list means "declared, adds nothing,"
  which is materially different from absent.
- **Attributability is required; exhaustiveness is opt-in.** Two different
  questions, wanting opposite defaults. *Attributability* — can swage explain
  every line already in the recipe? — is a correctness requirement, because an
  unattributable line is one swage will silently stop maintaining. It always
  gates, through G1 (§3.3.10). *Exhaustiveness* — has the maintainer considered
  every extra upstream offers, including ones the recipe never touches? — is
  awareness rather than correctness. Nothing is wrong when a new extra appears
  that no recipe line comes from, and demanding a config entry for each would
  mean recording that `requests`' `socks` extra is unused on a recipe that
  plainly does not use it.

  A feedstock therefore opts into exhaustiveness by declaring a `skip` list,
  which is the maintainer saying "I mean to account for all of these"; G3 then
  holds them to it. Without one, a new upstream extra is *reported and not
  gated*:

  ```
  google-cloud-storage    MERGE-READY
    note: upstream 2.19.0 adds extra `tracing` (no recipe line uses it)
  ```

  The gate follows the declaration instead of being imposed uniformly — the same
  bargain as everywhere else. Say what you mean and swage holds you to it; say
  nothing and it tells you rather than blocking you.
- **`skip` lives in swage's config, not in the recipe.** Recording a deliberate
  omission as a standardized recipe comment is tempting: it would sit beside the
  thing it describes and be visible to co-maintainers, which a config file in a
  separate repo is not. It is declined because it would write swage-specific
  directives into shared conda-forge repos, imposing a convention on maintainers
  who do not run swage. The visibility problem is real, and left unsolved rather
  than solved badly.
- **`outputs` unifies the two tools' divergent models.** The airflow tool's
  `MULTI_OUTPUT_PROVIDER_CONFIG` (extras become separate outputs) and the
  google-cloud tool's `RunConfig(core=, extras=)` (extras get folded into an
  existing output's `run`) are the same idea expressed twice. `extras_as_outputs`
  covers the first; `outputs[].run` covers the second; a feedstock can use both.
  Between them the two cardinalities that occur are already expressible: **many
  extras into one output** is `outputs[].run.extras`, the common case, and it is
  what the block-header comments in §6 annotate; **one extra into several
  outputs** is that extra named in each of their `extras` lists, which needs no
  new schema. What is *not* expressible is splitting one extra's dependencies
  across several outputs — some here, some there — and that is left unbuilt on
  purpose until a feedstock needs it, since the shape of the config would
  otherwise be a guess.
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
beyond G1–G6, G8 and G9 below:

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
| **G1** | Every requirement in the plan has a `Provenance` — upstream metadata, an explicit config entry, or a recognized recipe-owned line (§3.3.6) | no unexplained dependencies. `recipe-kept` is an allowlist of recognized structural lines, never a fallback for "swage could not explain this" |
| **G2** | Every name resolution is `exact` — no heuristic guesses, no unresolved names | §3.2 |
| **G3** | *(where the feedstock declares a `skip` list)* Every upstream extra appears in `supported`, `skip`, or `embedded_extras` | exhaustiveness is opt-in; without a `skip` list a new extra is reported, not gated (§4) |
| **G4** | The set of outputs is unchanged, and no published output has lost the upstream extra it is built from | a new output is a packaging decision; an output whose extra disappeared upstream is orphaned and needs a human (§4) |
| **G5** | The diff touches only requirements sections (plus formatting normalization) | anything else is out of scope for autonomy |
| **G6** | `trust: auto` for the feedstock or its family | blessing is explicit and opt-in |
| **G7** | *(Path B only)* swage's rendering is byte-identical to the PR's recipe | §5.3 — makes "no changes needed" verified, not assumed |
| **G8** | *(while `removals: review`)* The plan drops no requirement upstream dropped | §3.3.8 — a proving period, not a permanent rule. A *never-upstream* line is never dropped at all (§3.3.7) |
| **G9** | Every `run_constrained` entry is associated with an upstream extra in config | §3.3.9 — swage rewrote `run`, and cannot tell whether entries derived from the same extras still agree |

Fail any gate and the PR is *still* updated and pushed — the work is not thrown
away — but it is labeled `swage:needs-review` instead of `automerge`, and it
appears in the terminal report's NEEDS REVIEW section with the failing gate named.

The `trust` ladder is `manual` (never push) → `propose` (push, never auto-label)
→ `auto` (push and label when G1–G5, G8 and G9 pass). New feedstocks start at `manual`.
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

**Extra provenance** uses a different convention, because it is a different
shape. Where several upstream extras fold into one output's `run` (§4), each
extra's dependencies are introduced by a block header naming it:

```yaml
        # from the bqstorage extra
        - google-cloud-bigquery-storage >=2.29.0,<3.0.0
        # more restrictive constraint for python >=3.14
        - grpcio >=1.75.1,<2.0.0
        - pyarrow >=4.0.0
        # from the pandas extra
        - pandas >=1.3.0
        - pandas-gbq >=0.26.1
        - db-dtypes >=1.0.4,<2.0.0
```

A header runs until the next header or the end of the section, and other comments
may appear inside a block without ending it. One comment per *extra* rather than
per dependency is the whole point: `google-cloud-bigquery` folds in nine extras,
and annotating each of its twenty-odd lines individually would bury the recipe in
redundancy.

The two conventions differ because the situations do. A `# from the X extra`
header *partitions* a section — every line after it belongs to that extra until
told otherwise — so an opening marker suffices. A `# start`/`# end` pair
*delimits an island* of expanded dependencies sitting inside a list of ordinary
ones, where there is no next header to imply the end. Using paired markers for
both would double the comment count in the case that needs it least.

Both are swage-authored: requirements sections are swage's to render (§3.3.6),
so these comments are regenerated from the plan rather than preserved from the
previous recipe.

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

### 7.1 Migrating inside an update — `swage update --migrate`

Leaving migration entirely separate creates a round trip that costs more than
the migration: convert the feedstock by hand, wait for the bot to notice and redo
its version pull request, then run `swage update` against it. Three steps and a
wait, for what is one review.

So `swage update --migrate` will, for a v0 feedstock with an open bot pull
request, convert the recipe **and** reconcile its dependencies in that same pull
request. Without the flag, v0 feedstocks are reported as **NEEDS MIGRATION** and
otherwise untouched — the default stays "tell me", because converting several
hundred feedstocks is not something to trip into.

**Two commits, never one.** swage pushes the conversion and the dependency update
as separate commits, in that order. A combined diff is enormous — `meta.yaml`
deleted, `recipe.yaml` added, `conda-forge.yml` rewritten — and the dependency
reconciliation, which is the part that actually needs judgement, would be
invisible inside it. Split, the second commit is reviewable on its own, which is
the same argument this project applies to its own history (`CLAUDE.md`) pointed at
the feedstock instead.

**The conversion is verified before anything is reconciled against it.** After
CRM produces `recipe.yaml`, swage re-reads it with its own reader (§3.1). If that
fails — a construct CRM emits and swage refuses, a requirements list swage cannot
splice — the feedstock stops with the conversion unpushed. swage does not plan
against a recipe it cannot itself round-trip.

**Never automerged**, by §7's rule above rather than by the gates. G5 in
particular is meaningless here: the diff touches everything. The gates are still
*evaluated and reported*, because the maintainer reviewing the conversion should
also see what swage thought of the dependencies, but they gate nothing on this
path.

The volume control is the one `update` already has: it is dry-run by default, so
`swage update --family airflow-providers --migrate` reports how many feedstocks
it would convert and stops. Turning that into ninety pull requests takes
`--execute` and is a deliberate act.

---

## 8. Commands

```
swage scan     [--family F | --feedstock F | --all]   read-only; what would change
swage update   [--family F | --feedstock F]           render, push, label
               [--migrate]                            ... converting v0 first (§7.1)
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
    google-cloud-dataproc        G8: upstream dropped 'grpcio-status' in 2.28.0
    google-cloud-kms             G1: 'grpcio-gcp' in recipe, in no upstream
                                 version -- declare in add_requirements or drop
    google-cloud-storage         G9: run_constrained 'protobuf' not associated
                                 with an upstream extra -- proofread
  DEGRADED (1)                   pushed but NOT labeled -- rerun `swage status`
    google-cloud-spanner         label API call failed after 3 attempts
  MIGRATED (3)         v0 -> v1 converted and updated -- review both commits
  NEEDS MIGRATION (18) v0 meta.yaml -- rerun with `--migrate` to convert in place
  UNCHANGED (206)      no open bot PR
  FAILED (2)
    markupsafe                   unsupported build-variant switch 'use_noarch'

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

### 9.2 `swage explain` renders the record; it does not recompute it

The decisive choice for `explain` is not its layout but its input. **`explain`
renders a feedstock's record out of the run artifact, and can render one from a
past run:**

```
swage explain <feedstock> [--from-run DIR] [--json]
```

Because these commands run unattended, the question is almost never "what would
swage do now" — it is "why did it do *that*, at 03:00, while I was asleep." An
`explain` that recomputes answers a different question than the one being asked:
upstream has moved on, config may have changed, and a second computation path is
a second thing that can drift from the planner. Rendering the stored record means
`explain` cannot disagree with what actually happened. `--json` prints that record
verbatim — the same object inside `run.json` — so the human and machine views are
two renderings of one thing rather than two implementations of it.

Four sections, ordered the way the questions actually get asked:

```
swage explain google-cloud-bigquery                    run 2026-08-12T03-14

INPUTS
  recipe      v1, 2 outputs, 4 requirements blocks
  bot PR      #187  head 4a2f1c8
  upstream    google-cloud-bigquery 3.44.0    sdist METADATA
              previous 3.43.0                 for removal classification (3.3.7)
  python_min  3.9                             .ci_support/linux_64_.yaml
  config      config/feedstocks/google-cloud-bigquery.yaml
              config/families/google-cloud.yaml
              config/defaults.yaml

PLAN  /outputs/1/requirements/run
   keep   python >=${{ python_min }}         recipe-kept       recipe_owned.names
   keep   google-api-core-grpc >=2.28.0      upstream-core     identity
  ~bump   google-auth >=2.14.1 -> >=2.15.0   upstream-core     identity
   +add   proto-plus >=1.26.1                upstream-extra    extra:bigquery_v2
   -drop  grpcio-status >=1.33.2             upstream-dropped  absent in 3.44.0

GATES
  G1 pass   G2 pass   G3 n/a   G4 pass   G5 pass   G6 pass   G7 n/a
  G8 FAIL   drops grpcio-status, and `removals: review` (3.3.8)
  G9 FAIL   run_constrained `protobuf` associated with no extra (3.3.9)

VERDICT  swage:needs-review   (G8, G9)
```

The rules that make it useful:

- **One line per requirement, and every line names where it came from.** Three
  columns — the requirement, its `Provenance.origin`, and the source that
  justified it. Greppability is the point: `swage explain X | grep unresolved`
  answers a real question, and so does counting `upstream-core`.
- **The action is the first token**, so a plan reads as a diff at a glance.
- **Every source is a file path or a named layer**, never prose, so the next step
  is always opening a specific file.
- **Gates and verdict last**, because "why did this not merge" is the question
  that made someone run the command in the first place.
- **A feedstock that stopped before planning still explains**, printing INPUTS and
  a STOPPED section with the reason — a v0 recipe (3.1), a `use_noarch` switch
  (3.3.5), contradictory constraints (3.3.2). An empty plan would be the least
  helpful possible answer to "what happened".

---

## 10. Delivery plan

Each phase is independently useful and ends in something runnable.

**Phase 0 — skeleton.** `pyproject.toml` + hatchling, `src/` layout, ruff,
mypy strict, pytest + coverage, GitHub Actions on Linux/macOS/Windows, mkdocs.
`pixi.toml` for the dev environment, matching existing practice. Config schema
and loader; no behavior. *Ends with:* `swage --help` and a validated config tree.

**Phase 1 — read-only `scan`.** Upstream fetchers, mapping, recipe model,
planner, trust gates, terminal report. Nothing writes. Began with the
**round-trip spike** (§3.1), which decided the recipe model's foundation before
anything depended on it — that question is now settled in favour of `ruamel.yaml`.
*Ends with:* `swage scan` over the google-cloud family producing a plan.

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

**Phase 6 — `migrate`** (v0→v1), and `update --migrate` with it (§7.1). The
standalone command comes first because it is the one that can be run against a
scratch checkout and inspected; folding conversion into an update pull request is
only worth doing once the conversion itself is trusted.

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
- **Marker-reconciliation tests** (§3.3.1), tested for refusal as much as for
  results: a variant below `python_min` is ignored, overlapping bounds intersect
  to the tightest, a non-overlapping pair stops the feedstock, and a
  `sys_platform` marker stops it too. The contradiction case gets an assertion on
  the *message*, not just the failure — an error nobody can act on is barely
  better than the silent drop it replaces. A `use_noarch`-style build-variant
  switch (§3.3.5) gets the same treatment: a fixture feedstock that swage must
  refuse before planning, asserted on the message naming the variable.
  The `sys_platform` message is asserted
  to name both resolutions (§3.3.4), since "swage cannot do this" and "swage will
  not choose this for you" send the reader somewhere very different.
- **Line-ownership tests** (§3.3.6): a `${{ pin_subpackage(...) }}` line survives
  a rewrite untouched and never reaches the mapper. Worth its own test because
  getting it wrong blocks every multi-output feedstock at G2, which would look
  like a name-resolution problem rather than a classification one. Two more that
  matter as much: `pandas >=${{ x }}` *is* reconciled, because the test is on the
  name and not the line; and an unrecognized template is preserved yet still
  fails G1, since a `recipe-kept` fallback would silently disarm §3.3.7.
- **Attribution tests** (§3.3.10), one per outcome, and two in particular: a
  dependency reachable only through an *unlisted* extra fails G1 with a message
  naming that extra rather than pointing at `add_requirements`, and a dependency
  in both a listed and an unlisted extra is explained by the listed one. Getting
  the first wrong gives confidently wrong advice, which is worse than none.
- **`run_constrained` tests** (§3.3.9), all three of them refusals: swage never
  adds an entry even where an upstream extra would obviously suggest one, never
  removes one, and blocks automerge at G9 while any entry is unassociated. The
  first deserves the hardest guard, because "upstream declares an extra, so emit
  a constraint" is exactly the plausible-looking behaviour the rule exists to
  prevent.
- **Trust-gate tests** are the highest-value tests in the suite: each of G1–G9
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
| ~~CRM is pre-1.0; formatting preservation unverified~~ | **Resolved.** The Phase 1 spike found CRM cannot preserve comments through the edits swage makes; `ruamel.yaml` with a swage-owned recipe model is used instead (§3.1) |
| ruamel round-trip changes formatting swage did not intend | swage splices only requirements blocks into the original text, so nothing outside them can change (§3.1); the idempotence property in §6 is the test |
| GitHub secondary rate limits at 600 feedstocks | Single choke point with backoff; conditional requests; `scan --all` is not the common path |
| conda-forge changes its automerge logic or dispatch triggers | The push/label sequencing and the Path B merge live in one module with their own tests; §2 documents the source files to re-check. If conda-forge ever dispatches on `labeled`, Path B can be retired in favor of Path A |
| swage's required-check detection misses a CI provider, so it merges something conda-forge would have held | Require a non-empty required set; require *no* non-ignored check failing, not just that required ones passed; ship report-only first (Phase 3.5) and diff against hand-merges before enabling |
| The bot force-pushes between swage's CI check and its merge | `sha=pr.head.sha` pin on the merge call turns the race into a clean failure instead of merging unverified code (§5.2) |
| The bot resets its branch after a `--migrate` run, discarding a conversion that took real review | Far more expensive to lose than an ordinary update, since it is two commits and a human's attention. `swage status` (§8) must detect a migrated PR whose conversion commit is gone and re-report it rather than treating the feedstock as done |
| Blessing a feedstock that later goes novel | Gates are evaluated per-run, not per-blessing — `trust: auto` only permits automerge, G1–G5, G8 and G9 still must pass every time |
| grayskull/feedrattler/CRM release churn | Pin with floors, test against latest in a scheduled CI job |
| conda-forge moves `python_min`, silently changing which upstream markers are reachable | Fetch it rather than hardcoding it (§3.3.3); record the value used in `run.json` so a plan that changed for this reason is explainable after the fact |
| An upstream dependency is constrained per-platform rather than per-Python | Answers exist — `noarch_platforms`, or an unconditional dependency — but both are packaging decisions, so stop rather than pick (§3.3.4) |
| A feedstock builds several variants from one recipe, so "one noarch artifact" is false and every reconciliation rule with it | Detect the switch and refuse the feedstock before planning starts (§3.3.5); the failure names the variable so the maintainer is not left guessing why |

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
- What the escape hatch for a contradictory constraint (§3.3.2) looks like. A
  per-feedstock `constraints:` mapping a package to the constraint a human
  chose, applied only where swage would otherwise stop and carrying its own
  `Provenance` origin so G1 still traces it, is the obvious shape. Left
  unspecified until a real feedstock needs one — the stop is the important half,
  and an override nobody has needed yet is a guess about its own design.
