# swage — design plan

> **swage** *(n.)* a shaped block or die a smith uses to work raw stock into a
> consistent standard profile.
>
> **swage** *(v.)* to shape metal with such a tool.

A tool for maintaining a few hundred conda-forge feedstocks: it finishes the
dependency work that conda-forge's version-bump bot leaves undone, keeps recipe
formatting consistent, and gets routine updates merged without a human in the
loop, by handing them to conda-forge's automerge machinery. Where that
machinery structurally cannot reach a pull request (§2.1), swage reports it as
ready and a person presses the button: swage has no merge in it (§5.2.2).

**Status:** Phases 0 to 6 are built (§10). `swage config`, `swage scan`,
`swage audit`, `swage update`, `swage migrate`, `swage explain`, `swage status`
and `swage draft` work, and swage writes to real feedstocks: four so far, all
merged, and one of them merged by conda-forge's automerge with no human in it.
Next is Phase 7, retiring the two tools swage replaces, after which it is doing
the whole job rather than most of it. The documentation is published at
`xylar.github.io/swage`.
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

## The model, in one page

Everything after this section is detail. These are the claims the rest of the
document elaborates — and the ones worth arguing with, because an assumption
that is only ever stated in the middle of a rule is one nobody gets the chance
to disagree with. That is not hypothetical: "conda-forge builds one
`noarch: python` package" was written down once, deep in §3.3.1, as the premise
of a reconciliation rule, and it silently became the scope of the whole tool.
It is a property of *one kind of output*, and this section exists so that the
next such claim has to be made where it can be seen.

### The subject: a recipe, against what its upstream release declares

swage reconciles the dependencies a recipe states against the dependencies the
release it packages declares. Everything else it does — formatting, the test
matrix, the merge machinery — is in service of getting that change reviewed and
landed.

**Where upstream's declaration lives is a per-feedstock fact, and config names
it** (§4). There is no single place to look and swage does not guess: a family
or feedstock says `source: github` with a tag and a path, or `source: archive`
with the file to read inside it. Three readers exist today — a `pyproject.toml`
in a monorepo tag, a sdist's core metadata, and a wheel's `METADATA` as a
fallback (§3.6).

**A feedstock that does not package a Python distribution still has an upstream
declaration**; swage has no reader for it. `libnetcdf` needs `hdf5`, `libcurl`
and `zlib` for reasons its maintainer can point at — a `CMakeLists.txt`, a
configure script, release notes — and that pointer is exactly the kind of thing
`config/` exists to hold. This is a **gap in what swage can read, not a
boundary on what it covers**, and the distinction matters because the last
boundary drawn by accident cost the tool half the fleet. Until a reader exists,
such a feedstock has nothing swage can reconcile and should say so in those
words, rather than failing over a `python_min` that a recipe with no Python in
it was never going to have.

### The build model is a property of each output, not of the fleet

An output declares how many artifacts it produces, and everything about how
upstream's environment markers translate follows from that. Of the 91 v1
recipes in the maintainer's checkouts, 48 build one noarch python package and
41 build architecture-specific packages; one builds both kinds from different
outputs, and one is a v0 recipe caught mid-conversion.

| Output | Artifacts | A `python_version` marker | A `sys_platform` marker | `python_min` | Python test matrix |
|---|---|---|---|---|---|
| `noarch: python` | **one**, installed on every Python from `python_min` up | collapses: one line that must hold across the whole range (§3.3.1) | stops: every way to express it is a packaging decision (§3.3.4) | required; from the recipe or `.ci_support` (§3.3.3) | conda-smithy's latest-Python rule applies (§3.7) |
| architecture-specific, with Python | **one per Python** | translates: `if: match(python, "<3.13")` / `then: …`, mirroring upstream (§3.3.1) | translates: `if: win` / `then: …` (§3.3.4) | not stated, and its absence is not an error | the variant list is the matrix; the rule does not apply |
| architecture-specific, no Python | one per platform or variant | — | — | — | — |

There is no fifth row: **one output that builds both an arch and a noarch
package** has two answers in every column and is refused before planning starts
(§3.3.5).

**Collapsing and translating are the same fidelity rule under different
constraints.** A noarch output has one requirements list to describe every
Python it will be installed on, so the strictest bound wins and a comment
records why. An arch output is built once per Python, so the recipe can say
what upstream says — and *must*, or the package claims constraints upstream
never made. Anything the recipe cannot express is a stop, not a guess.

**Read per output, never per feedstock.** `sqlalchemy` is a compiled base
output beside noarch metapackages; `apache-beam` is a compiled base output
beside eleven noarch extras outputs. conda-smithy scopes `noarch` per output
and so does swage.

### What swage writes, and what it never authors

Two regions of a recipe, and nothing else:

1. **requirements sections** — the lines and the comments swage renders around
   them;
2. **the python test matrix** in `tests` (§3.7).

It never authors: a `version` or `sha256` (the bot's job), a new output, a
`run_constrained` entry, a package's Python floor, or `conda-forge.yml` outside
a v0→v1 migration. Each of those is a packaging decision that no metadata
contains, and the list is not a style preference — it is the boundary that lets
an unattended tool be trusted at all. A rule of this kind belongs *here*, where
it can be read in one pass, rather than only in the section that discovered it.

### Nothing merges by accident

swage pushes a commit, and conda-forge's automerge decides. The one constraint
that shapes the entire write path: **the `automerge` label is not a trigger** —
a CI run is — so the push must come strictly before the label, and a label
applied after CI has finished does nothing at all. §2 is the evidence for that
and is reference material; §5 is the trust ladder and the gates.

### Reading guide

| | |
|---|---|
| **§1** | why the tool exists, and what it deliberately does not do |
| **§2** | how conda-forge's automerge actually works — reference, read once |
| **§3** | the layers, in dependency order. §3.3 is the core computation and the longest section in the document |
| **§4** | the quirks database: what config can say |
| **§5** | autonomy — the trust ladder and the gates every change is checked against |
| **§6**–**§9** | formatting, migration, commands, output |
| **§10**–**§12** | delivery plan, testing, open questions |

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
  not desirable — nearly every meaningful change there requires human judgment.
  The one exception is the v0→v1 migration, where switching to `rattler-build`
  and `pixi` is a mandatory part of the conversion (see §7).
- **Merging.** When swage commits a change, the resulting CI run drives
  conda-forge's own automerge and swage stays out of it. Where swage changes
  nothing there is no CI run to drive, and GitHub will not let swage close the
  pull request itself either — so it reports one that is ready and a person
  presses the button. See §5.2, which spent a draft specifying the opposite.
- **A web UI**, at least initially. See §9.

### What is in scope, said out loud

The non-goals above are the only boundaries. In particular **feedstocks that
build architecture-specific packages are in scope**, and always were: compilers,
several outputs, an arch base package beside noarch metapackages, a build
variant per Python or per mpi implementation. They are 41 of the 91 v1 recipes
in the maintainer's checkouts, and the tool exists to maintain that fleet rather
than a subset of it.

This is stated because the opposite was implied for a long time without anyone
deciding it. Both tools swage replaces produced only `noarch: python` packages,
so the golden corpus was entirely noarch, so §3.3.1's premise — true of a noarch
output — went unchallenged and hardened into a boundary. The corpus now carries
nine compiled feedstocks precisely so that the next rule written against a noarch
assumption fails a test instead of becoming a scope decision. **An assumption
that arrives through a fixture is not a decision, and the fix is to say what the
scope is rather than to note that it is wider than it looked.**

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
appears in the PR timeline after the most recent `labeled` event whose label is
`automerge`*. If a commit does appear after that event, the bot **strips the
label**, comments to say so, and refuses.

The qualifier is load-bearing rather than pedantic. `_no_extra_pr_commits`
matches `e.raw_data["label"]["name"] == "automerge"` when it scans the timeline,
so *other* labels applied after a commit are invisible to it. Read without the
qualifier, the rule says any label swage adds after pushing would re-arm
automerge on a pull request swage had just decided not to merge — which would
make §5.4's needs-review path actively dangerous. It does not.

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
and reports the feedstock as **NEEDS MIGRATION** (§9). A feedstock part-way
through conversion defeats the filename check — `apache-beam` has v0 Jinja in a
file named `recipe.yaml` — so a file that fails to parse *and* opens with `{%`
is reported as v0 rather than as broken YAML, for the same reason. It is a
routing decision,
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
> "tightest of upstream's floors" note ends up above an unrelated
> dependency, in a recipe that is valid YAML and silently false. The root cause
> is that CRM attaches a standalone comment to the *following* node rather than
> modeling it as a document element.

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

**A requirements section is a list of entries, not a list of lines.** An entry
is a requirement, or a condition with requirements under it:

```yaml
host:
  - python
  - cython
  - if: build_platform != target_platform
    then:
      - numpy
      - cffi
```

The reader models both, the renderer writes both, and the round trip is exact
for both -- with one normalization the fleet turned up and nothing else: a
branch key written `then: ` with a trailing space renders as `then:`, because
nothing distinguishes the two once read and swage renders a section's contents
rather than preserving them (§6). This is the v1 recipe grammar rather than a shape a few feedstocks
happen to use: `if:`/`then:`/`else:` is how a v1 recipe says anything
conditional at all, and refusing it is refusing the format. It is also the
difference between reading 54 of the fleet's v1 recipes and reading all of
them — every architecture-specific recipe that swage refused, it refused over
this, in `build`, `host` and `run` alike.

> **swage read `build` strictly and then never used it.** The reader validated
> every section — including one the planner does not touch — strictly enough to
> reject the whole recipe. Of the 91 v1 recipes in the maintainer's checkouts,
> 37 were refused and 33 of those were refused over `build` alone. Not reading
> `build` at all would have moved that to 21 refusals rather than to none:
> `host` and `run` carry conditional entries too, and 17 recipes stop at `host`
> the moment `build` is skipped. The lesson is not "read less" but the rule
> above — a reader that understands the format is the only version of this that
> ends.

### 3.2 `mapping` — name resolution, with provenance

Resolving a PyPI name to a conda-forge name is the step most likely to be
silently wrong, and it is the basis of the trust gate. Resolution is layered,
first match wins:

1. per-feedstock `name_map`
2. per-family `name_map`
3. global `config/name-map.yaml` (seeded from the existing tools' tables)
4. `grayskull`'s accumulated PyPI↔conda-forge knowledge
5. identity — conda-forge publishes a package under this very name
6. **unresolved** — not a guess, an explicit failure state

Every resolution returns a `Resolution(conda_name, source, exact: bool)`. Layers
1–3 and 5 are exact; layer 4 is exact only when grayskull reports a known mapping
rather than inferring one. Any unresolved name, or any inexact resolution, means
the feedstock cannot auto-merge (gate **G2**, §5). This is what turns "the tool
guessed and I didn't notice" into a stop condition.

#### 3.2.1 The two layers nobody writes by hand

Layers 1–3 are facts a maintainer wrote down, and between them they cover about
an eighth of what the fleet declares: of the 910 dependency references in the 89
airflow providers, 111. The other two layers are data, fetched and cached under
`~/.cache/swage/index` with a day's TTL, and without them a real run fails G2 on
almost every feedstock and reports it as hundreds of unresolvable names rather
than as a missing input.

**Layer 4 is regro's `grayskull_pypi_mapping.json`** — the table grayskull and
conda-forge's own autotick bot resolve against. It holds only the pairs that
*differ*: `docker` is in it because the package is `docker-py`, and `pandas` is
absent because there is nothing to say. So it answers "what is this called on
conda-forge" and cannot answer "does conda-forge have this".

**Layer 5 is conda-forge's `channeldata.json`**, and it is what makes identity a
*check* rather than an assumption. Without it every unknown name resolves to
itself, the unresolved state becomes unreachable, and G2 is disarmed by
construction.

> **Layer 4 outranks layer 5 even though layer 5 is the stronger evidence**, and
> the fleet says that is right. 91 of grayskull's 11,904 entries rename a name
> conda-forge *already publishes*, and nearly all of them must: conda-forge's
> `blosc` is the C library and the Python binding is `python-blosc`, `couchdb`
> is the database, and `astropy`'s library is `astropy-base`. Identity-first
> would resolve those to the wrong package while looking entirely reasonable.

The one case in the maintainer's fleet where the table disagrees with the
recipes is `apache-airflow`, which grayskull maps to `airflow` — most likely the
name from before Apache prefixed its projects. Both packages exist and ~99
provider recipes depend on `apache-airflow`, so `config/name-map.yaml` maps it
to itself. **An identity entry in a name map is therefore load-bearing, not
redundant**, and it is the only way to hold a name against layer 4.

Measured after both layers landed, over the same 910 references: 566 identity,
229 grayskull, 111 config, 2 unresolved — and both of those genuinely have no
conda-forge package, so stopping their feedstocks is G2 working.

**Resolution is keyed on the requirement, not on the bare name.** conda-forge
frequently publishes a dependency's extra under a *name of its own*:

```
google-api-core[grpc]           -> google-api-core-grpc
apache-airflow[aiobotocore]     -> apache-airflow-providers-amazon-with-aiobotocore
```

Usually that name is a further output of the same feedstock, layered on top of
the base package rather than carved out of it. `google-api-core-grpc` is the
second output of the `google-api-core` split recipe, and its `run` section is
`pin_subpackage(google-api-core, exact=true)` plus `grpcio` and
`grpcio-status` — the base package *and* what the extra pulls in. The only way
to ask for that is by its name.

So every layer is looked up by `name[extra,...]`. Keying on the name would
resolve `google-api-core[grpc]` to `google-api-core`, quietly dropping the
extra and producing a recipe that builds and under-specifies — the failure
that is hardest to notice, because nothing is missing until something fails to
import.

**Falling back to the bare name when the key resolves to nothing is the same
failure**, arrived at one step later, and swage does not do it silently.
`celery[redis]` would become `celery`, resolved by identity and therefore
*exact*, and G2 would have nothing to object to. So the extra has to be
accounted for, and there are exactly two ways:

1. **a `name_map` entry keyed on the requirement.** `google-api-core[grpc]` →
   `google-api-core-grpc` where conda-forge publishes the extra under a name
   of its own, and `google-auth[pyopenssl]` → `google-auth` where the extra
   needs nothing conda-forge does not already ship. The second is an identity
   entry and it is load-bearing for the same reason `apache-airflow`'s is: it
   is the only way to put "considered, and the bare name is right" on the
   record.
2. **an `embedded_extras` entry** (below), which writes out what the extra
   pulls in and leaves the bare name correct as far as it goes.

With neither, swage still *renders* the bare name — it never mangles a line it
cannot justify — but the resolution records the dropped extras, is not exact,
and **G2 stops the feedstock** naming both remedies. An empty `embedded_extras`
list is a legitimate answer here and means "considered, and it expands to
nothing". The prior art wrote that as an empty `# start` / `# end` pair in
`celery` and `psycopg`'s recipes; swage writes a caption on the dependency
instead (§6).

> **This is exactly what grayskull does, and the fleet carries the scar
> tissue.** grayskull drops the extra from `google-api-core[grpc]<3.0.0,>=2.25.0`,
> so recipes maintained alongside it were written to survive being regenerated:
> a constrained `google-api-core >=2.25.0,<3.0.0` — what grayskull would produce
> anyway — plus a bare `google-api-core-grpc` carrying the dependency that
> actually matters. The pair is one requirement wearing two lines, and the
> missing constraint on the second is deliberate: grayskull had no opinion about
> a package it did not know existed, so leaving it unconstrained kept the two
> tools from overwriting each other.
>
> **swage retires the workaround rather than reproducing it.** It resolves the
> requirement correctly, so `google-api-core-grpc >=2.25.0,<3.0.0` is what it
> renders; the extra plain line then appears in no upstream version, gets no
> `Provenance`, and G1 stops the feedstock naming it (§3.3.10 case 6). That is
> the right shape — swage never deletes a line it cannot account for (§3.3.7),
> so a human removes it once and the feedstock is clean from then on.
>
> **The pair is not always a workaround, and nothing may assume it is.**
> `google-cloud-storage` declares plain `google-api-core` among its core
> dependencies *and* `google-api-core[grpc]` under its `grpc` extra, so both
> lines are upstream-derived and both are correct. The two cases are
> indistinguishable by shape and distinguishable by metadata, which is the
> argument for attributing every line rather than pattern-matching recipes.

Where no conda package corresponds to the dependency-with-extra, the answer is
not a mapping at all: `embedded_extras` (§4) lets the maintainer write out the
dependencies that extra pulls in, and §6's `# start` / `# end` markers make
that block swage's to replace rather than something a rerun would destroy.
swage does not attempt to derive those itself — doing it robustly means
resolving another project's extras against conda-forge, and a wrong answer is
indistinguishable from a right one until the package is used.

#### 3.2.2 A recipe line is keyed on the name it resolves to, not the name it is written under

The fleet's recipes were written by tools that did not resolve names, so they
routinely spell a dependency the way *upstream* spells it: `pyOpenSSL` where
conda-forge publishes `pyopenssl`, `psycopg2-binary` where it publishes
`psycopg2`. swage resolves the requirement and renders the conda name, which
leaves the line already in the recipe to be recognized as **the same
requirement** or as a different one.

> **The planner matches a recipe line to the plan by the conda name the line
> *resolves to*.** Matching on the line's own spelling makes one requirement
> look like two, and swage renders both.

Nothing downstream catches that, which is what makes it worth stating. Both
lines attribute to the same upstream declaration, so both carry a `Provenance`
and G1 is satisfied; both resolve exactly, so G2 is too.
`apache-airflow-providers-snowflake` would have been pushed carrying
`pyopenssl >=22.1.0` and `pyOpenSSL >=22.1.0` side by side. The same key
decides which planned line a preserved comment (§6.1) belongs to, since a note
about a dependency has to follow it through a rename.

**Where the two spellings are genuinely different packages, the line stays and
G1 explains it** — conda-forge really does publish `psycopg2-binary`, so the
recipe may mean it, and swage does not delete what it cannot account for
(§3.3.7). What the report must not do there is offer `add_requirements`: that
is the remedy for a dependency upstream never declares, and upstream declares
this one by name. It is the third instance of §3.3.10's rule that one verdict
can need opposite advice.

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

**What that means for the recipe depends on how many artifacts the output
builds**, which is the per-output build model of the front section. The rest of
this section is the `noarch: python` case; §3.3.1.1 is the other one.

A `noarch: python` output is **one package**, installed on every Python from
`python_min` (§3.3.3) upward. It therefore gets a single `pandas` line, and that
line has to hold for every Python in the range. Per package, after name
resolution:

1. **Discard requirements whose marker cannot be true for any Python this
   package is installed on** — at or above `python_min`, and below the cap the
   recipe puts on its own `python` line where it has one (§3.3.3). This is what
   makes a `python_version < "3.9"` variant disappear rather than participate
   in the next step.
2. **Intersect the specifiers of everything that survives.**
3. **An empty intersection is a stop, never a guess** (§3.3.2).
4. Otherwise emit the intersected constraint. Where the binding bound came from a
   marker-qualified variant, emit the marker comment recording it —
   `# tightest of upstream's floors (python >=3.14)` — which is what stops the
   recipe looking like a mistake to the next reader.

**The comment names both ends where they came from different declarations.** A
line takes its floor from whichever variant demands the most and its ceiling
from whichever allows the least, and those are routinely not the same one:
`google-ads` takes `protobuf >=5.26.1` from upstream's `python >="3.13"`
declaration and `<8.0.0` from its `python >="3.10"` one. A comment naming only
the floor invites the reader to assume the whole constraint came from there,
which is the misreading this comment exists to prevent wearing a quieter
disguise — so that line reads
`# tightest of upstream's floors (python >=3.13) and ceilings (python >=3.10)`.
The maintainer had written that distinction into the recipe by hand before
swage ever ran on the feedstock, which is the best evidence available that it
is worth writing. A ceiling that binds alone gets the mirror-image comment;
where both ends come from the same declaration the short sentence stands.

The parenthetical names the marker in the same comma-joined form a constraint
on a dependency line is written in, so a window reads as
`(python >=3.12,<3.14)` rather than as the marker's own
`python_version >= "3.12" and python_version < "3.14"`. A marker with no such
reading — an `or`, or an axis this cannot reduce — is quoted verbatim instead,
which is longer but never wrong.

> **The wording is swage's own, and neither tool's.** The airflow tool wrote
> `# more restrictive for python >=3.14`, the google-cloud tool
> `# more restrictive constraint for python >=3.14`, and this document quoted
> one here and the other in §6 for long enough that the contradiction shipped.
> Both had to go regardless of which was picked: they say what the line *is*
> and not why, which leaves the reader a wrong answer within reach — they read
> as though the constraint applied only from 3.14 upward. It does not. It binds
> on every Python this package is installed on, which is the whole point of
> step 2, and someone acting on the misreading would make the line conditional
> — the one edit this comment exists to prevent. Naming the *selection* says
> what happened: several variants were in play and the strictest won.
> Fleet cost of the rewording: 88 comments across 53 recipes.

Step 2 is deliberately stricter than upstream. On Python 3.10 upstream would
accept `pandas >=2.1.2` and the recipe will demand `>=2.3.3`. A single artifact
cannot do better, and the comment in step 4 is what makes that legible rather
than mysterious.

**A marker swage cannot reduce to the Python-version axis stops a noarch
output** — but not because no answer exists. See §3.3.4: the reason is that
every available answer is a packaging decision rather than a reconciliation.

#### 3.3.1.1 An output built once per Python states what upstream states

An architecture-specific output is built once per Python, so the collapse above
is not merely unnecessary — it is wrong. `pandas>=2.3.3` on the 3.14 build is
what upstream asked for; the same constraint on the 3.10 build is a claim
upstream never made, and one that can make a solve fail for no reason. The
recipe can carry the distinction, and therefore must:

```yaml
requirements:
  run:
    - if: match(python, "<3.13")
      then: pandas >=2.1.2
      else: pandas >=2.2.3
```

**This is the fleet's own idiom, not an invention.** `apache-beam` writes this
by hand, in `host` and in `run`, for `grpcio`, `grpcio-tools` and
`google-apitools` — a maintainer answering this question the same way, in a
recipe swage now carries as a fixture.

**`match` rather than a bare comparison, because a bare comparison is a string
comparison.** A recipe's `if:` is evaluated by minijinja over the variant's
value as text, so `python < "3.13"` compares `"3.9"` against `"3.13"` character
by character — `'9' > '1'`, and python 3.9 falls outside a range it belongs to.
rattler-build documents the trap and the answer together: "the comparison is a
string comparison done by minijinja… use the `match` function to compare
versions."

swage wrote the bare form until it was caught rewriting `pyodps`'s
`not win and match(python, "<=3.12")` into `unix and python < "3.13"` — turning
a maintainer's version-aware condition into a lexicographic one on a feedstock
swage does not own. It had gone unnoticed because every minor release
conda-forge currently builds has two digits, and two-digit minors do compare
correctly as strings; the defect was waiting for a floor below 3.10 rather than
absent. The spelling had been justified as "the way the fleet writes it", from
a survey of what recipes contain rather than of what rattler-build means — and
the fleet writes both, 46 `match(python, …)` against 13 bare comparisons.

swage still **reads** both, because real recipes contain both and a condition
it did not write is preserved verbatim.

**Where the two ranges partition the axis, swage writes one entry with an
`else:` rather than two entries.** `apache-beam` writes two, and both spellings
say the same thing; the shorter one says it once, and conda-forge's own tooling
has been seen normalizing v1 recipes toward it during version updates. Three or
more ranges cannot be written with a single `else:` and stay one entry each,
since the alternative is nesting a conditional inside a branch to save a line.

So the rule generalizes rather than splitting in two: **the recipe says what
upstream says, as precisely as the artifact allows.** A noarch output cannot
distinguish Pythons, so the strictest bound wins and step 4's comment records
that a choice was made. An arch output can, so it does, and no comment is
needed because nothing was chosen.

Three consequences follow, and each is a real constraint on the implementation:

1. **swage authors conditional entries** in a requirements section — the first
   structure it writes rather than preserves. It authors them only as a direct
   translation of an upstream marker, one entry per range of Pythons upstream
   answers differently for. A condition it did not derive that way is not
   swage's to write.

   The ranges come from evaluating every marker at every Python minor release
   and merging consecutive releases that agree, which is exact at the
   granularity a recipe can express — conda-forge builds one artifact per minor
   release, so a marker distinguishing two *patch* releases of one minor is
   inexpressible, and that is a stop rather than a guess. It also means an
   unmarked declaration binds inside every range rather than sitting beside
   them as a second line: upstream saying `grpcio<2` and `grpcio>=1.67.0` for
   3.13 is one constraint on 3.13, not two entries for a reader to intersect.
2. **A marker on the Python axis translates; anything else follows §3.3.4.**
   The axis a condition can key on is what the recipe's own variants offer,
   which is why `python` works and, for an arch output, `win` and `unix` do too.
3. **The `python_min` range does not apply, but the build matrix does.** Step 1
   discards variants that cannot be true across a noarch package's range; an
   arch output has no such range. Which Pythons it *is* built for is
   `.ci_support`'s answer rather than `python_min`'s (§3.3.3) — one rendered
   file per variant, with the Python in its name — and a variant reaching none
   of them is discarded here for the same reason step 1 discards its own: it
   describes an artifact conda-forge does not produce.

   **Treating that as mere tidiness cost a feedstock.** `pyodps` declares
   `oldest-supported-numpy==2023.10.25; platform_machine=='aarch64' and
   python_version<'3.9'` and is built for 3.10 up. swage refused the whole
   feedstock over the `platform_machine` half of a marker whose Python half had
   already made it moot, sending a maintainer to resolve by hand a case that
   cannot arise. So reachability is decided *before* anything is refused or
   rendered — and the never-selected condition is not written either, since a
   reader has no way to tell it from one upstream asked for.

#### 3.3.2 Contradictory constraints stop a noarch output

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

**On an output built once per Python there is no contradiction to report.** The
pair above describes two Pythons with different answers, which §3.3.1.1 writes
as two conditional entries. The stop is a consequence of one artifact having to
serve a range, not a fact about the metadata — so a rule that read "swage stops
the feedstock" would refuse a recipe whose upstream declaration is perfectly
consistent. The intersection is only computed where it has to hold.

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

**It is needed per output, and only where an output needs it.** `python_min` is
what `${{ python_min }}` expands to and the bottom of the range §3.3.1 collapses
over — both questions a `noarch: python` output asks and an
architecture-specific one does not. conda-smithy agrees: it writes `python_min`
into `.ci_support` for a feedstock that builds a noarch python package, and not
otherwise. `pyproj` renders 26 variants and **none of them declares one**,
because a feedstock whose Python is a build variant has no floor to state.
`apache-beam` declares it in every variant, because eleven of its twelve outputs
are noarch.

So the absence is an answer rather than a failure. Where an output needs
`python_min` and neither source has it — a noarch feedstock conda-smithy has
never rendered — swage stops rather than assuming. Where no output needs it,
swage never asks, and a message telling the maintainer of a compiled feedstock
to re-render is one that would not have helped.

**This is not upstream's `requires-python`, and conflating the two is a bug
waiting to happen.** Upstream's floor is a claim about the *project*; this is
conda-forge's *build* floor, and it alone is the bottom of the range markers
are evaluated over. §4.1 is where the two meet: a project floor above this one
means the recipe's `${{ python_min }}` lines promise a Python upstream has
dropped, which is a stop. They are typically one version apart — exactly the
gap that would let a marker-evaluation bug pass every test written against the
wrong number.

**The range has a top as well, and it comes from the recipe's own `python`
line.** A recipe writing `- python >=${{ python_min }},<3.14` in `run` is
saying this package is not installed on 3.14, so an upstream
`python_version >= "3.14"` variant describes a Python it will never see — the
same sentence that discards a variant below the floor, pointed at the other
end. Markers are therefore evaluated over `[python_min, cap)`.

> Ignoring the cap is not a conservative default, it is a wrong answer that
> looks conservative. `google-cloud-pubsublite` caps at 3.14 and its comment
> says why — *"we don't have grpcio >=1.75.1 on conda-forge yet, so we're not
> ready for python >=3.14"* — and reconciling that variant in demands exactly
> the `grpcio` conda-forge does not have. The recipe would not solve.

The cap is read per output, since a split recipe can cap one package and not
another, and only from `run`: `host` pins the Python the build runs on, which
is a different question. A cap swage cannot read as a literal — 52 lines in the
fleet's checkouts write `<${{ python_over }}` — is treated as no cap at all,
which is the safe direction: swage reconciles over the wider range and renders
the stricter constraint, where the reverse would silently drop one. 45 of those
checkouts carry a cap and most are `<4.0`, which reaches no marker anybody
writes.

#### 3.3.4 Platform markers, where the answer depends on the artifact

An upstream dependency can be conditioned on the platform rather than on the
Python version:

```
pywin32>=306; sys_platform == "win32"
```

**An architecture-specific output expresses this directly**, and there is
nothing to decide:

```yaml
requirements:
  run:
    - if: win
      then: pywin32 >=306
```

That is the same translation as §3.3.1.1's, on the axis the build already
varies over, inside a requirements section, touching nothing else. It is
ordinary in compiled recipes: 20 of the 41 architecture-specific recipes in the
maintainer's checkouts stop the reader at `host` or `run` even when `build` is
skipped entirely, which is this shape counted from the other side.

It is literally the same translation: upstream's markers are evaluated for
every Python on every build target, and whichever axis the answer varies along
is the axis the conditions key on. Builds that agree are named the way a recipe
names them — `unix` for what is not Windows, `not linux` for what is not Linux,
`aarch64` or `arm64` for one machine, `osx and arm64` where it takes both.

**The machine is an axis, not an exception.** conda-forge builds
`linux-aarch64`, `linux-ppc64le`, `osx-arm64` and `win-arm64`, and the fleet's
recipes select them by name: 8 entries say `if: ppc64le` and one says
`if: aarch64 and mpi == "nompi"`. swage refused `platform_machine` as "not
something this output is built once for each of", which is false, and the
refusal reached real feedstocks. The marker and the selector disagree about
spelling, which is the part worth writing down: a marker says
`platform_machine == "arm64"` on macOS, `"ARM64"` on Windows, and `"AMD64"` for
what a recipe selects as `x86_64`.

**A marker varying along both axes at once says both.** One condition per group
of builds that agree, joined with `and` to the run of Pythons it holds over:

```yaml
- if: unix and match(python, "<3.13")
  then: cython >=3.0,<3.1
- if: unix and match(python, ">=3.13")
  then: cython >=3.1,<3.3
```

That is `pyodps`, whose upstream declares
`cython>=3.0,<3.1; platform_system!='Windows' and python_version <= '3.12'` and
whose recipe already answers it by hand as
`if: not win and match(python, "<=3.12")`. swage refused the feedstock rather
than write what the recipe it was reading already said — and 190 entries in the
maintainer's checkouts join two conditions with `and`, so this was never a
structure swage would have been inventing.

A group of builds that **no selector names** is still a stop: swage will not
compose a disjunction nobody writes by hand, and the message says which builds
it could not name.

**A `noarch: python` output is the hard case**, and the rest of this section is
about it.

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

**Where the package is built once, swage stops on a platform marker in a
noarch output, because both of those are packaging decisions rather than
reconciliations.** Option 1 edits `conda-forge.yml`, which §7 puts off-limits,
and adds per-platform build strings, which gate G5 forbids by confining the
diff to requirements sections. Option 2 ships users a dependency they will
never load — frequently the right call, and still a judgment about what the
package promises. Neither is inferable from upstream metadata. A single
artifact is installed on every platform at once, so the marker has no answer,
and that stop is right and stays.

**Where it is built once per platform, the axis exists and swage answers on
it.** The platform is not a variable a `noarch: python` package cannot vary
over; it is a variable *that model* cannot vary over, and `noarch_platforms`
is a different model. So the decision is read off the rendered variants: more
than one platform there and the marker is reconciled once per platform, the
same translation §3.3.1.1 makes on the Python axis. `colorlog` is built for
linux and win, upstream declares `colorama; sys_platform == "win32"`, and
`if: win` / `then: colorama` is what both sides were already saying — swage
had been refusing the feedstock over a condition it agreed with.

**How rare is this?** None of the 176 `noarch: python` recipes in the
maintainer's local checkouts use `__win` or `__unix`, and the canonical example
has dropped it. Rare — and every recipe that does write it is one where
somebody has already answered this question, which is why the thing swage must
never do with one is silently reverse that answer.

**The idiom has two spellings, and swage once read neither.** `colorlog`
writes a condition, which parses into something swage can reason about.
`click` writes the platform into the dependency *name*, so the same fact
arrives as unconditional lines whose text is a template:

```yaml
requirements:
  run:
    - __${{ noarch_platform }}
    - ${{ "colorama" if noarch_platform == "win" else "python" }}
```

Both are read by **expansion, not evaluation**: swage substitutes one known
variant's known values and matches one known shape, rather than running a
template engine. `noarch_platform` is not a conda-smithy variable — each
feedstock declares it in `recipe/variants.yaml` or
`recipe/conda_build_config.yaml` — and across the eleven conda-forge feedstocks
that write the idiom its values are always `linux`, `osx`, `win` or `unix`. So
`__${{ noarch_platform }}` expands to the four virtual packages
`config/defaults.yaml` already blesses as recipe structure, and the choice
expression expands to the two names it can take. That expression's `else` half
is usually filler: a bare `if` leaves an empty entry, and `python` is a
dependency regardless.

**A line is explained when every name it can take is explained.** All or
nothing, which is what keeps recognition an allowlist rather than the fallback
§3.3.7 depends on it not being — `some-package-${{ noarch_platform }}` expands
just as readily and is owned by nothing.

**Read, never authored, and never converted from one spelling to the other.**
The line is kept exactly as written and the plan's own copy of the dependency
is dropped. Without that second half swage kept the templated line *and*
planned `if: win` / `then: colorama` beside it, so the recipe asked for
`colorama` twice, in two spellings, with only review in the way. Rewriting the
template into the condition would be correct and would still be wrong: the two
say the same thing, conda-smithy's linter accepts both, and which one a recipe
uses is the maintainer's call. The idiom is a workaround for a lint that no
longer fires — conda-smithy's changelog records that "recipes with
`noarch_platforms` will no longer give a lint when selectors are used" — so
`colorlog` and `poetry` write plain selectors for the same thing. That the two
spellings are equally valid is exactly why swage must not turn one into the
other.

**A template swage reads is still a template swage cannot update.** If upstream
later constrains `colorama`, there is nowhere to write that bound: the line
carrying the dependency is one swage refuses to author, so the feedstock stops.
Nothing is in that position today — `click`'s upstream declares `colorama`
bare. The same gap applies to a real dependency riding inside a preserved
conditional, which is kept verbatim and therefore neither reconciled nor
reported.

> **The stop used to come for free, and that was a fact about the reader
> rather than about the rule.** Until the recipe layer learned conditional
> entries, it refused any requirements list whose items were not plain strings
> (§3.1) — so a noarch feedstock using the `noarch_platforms` idiom stopped at
> read time, and so did every compiled feedstock in the fleet, for the same
> reason and wrongly. Once the reader can see a conditional, the stop has to be
> stated by the planner, for the noarch case, on purpose. An accidental refusal
> that happens to be right in one case and wrong in forty is not a rule.

**The Python implementation is not one of these axes.** `trino-python-client`
declares `orjson >= 3.11.0 ; platform_python_implementation != "PyPy"`, and
swage stopped the feedstock over it as though a choice had to be made. There is
none: conda-forge dropped PyPy, so every package it builds runs CPython and the
condition holds on all of them. Markers are resolved against that before
anything in this section applies — a comparison on
`platform_python_implementation` or `implementation_name` is evaluated for
CPython and folded away, leaving a marker that names only axes something is
really built along. A declaration whose whole marker was about the
implementation is then unconditional; one gated *on* PyPy reaches nothing
conda-forge builds and drops out with the other unreachable variants
(§3.3.1). Neither the noarch path nor the arch path ever sees the variable, so
neither needs an opinion about it.

#### 3.3.5 One output that builds both an arch and a noarch package is out of scope

A few feedstocks build both an arch-specific and a `noarch` package **out of a
single output**, switched by a variable the feedstock invents for itself.
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

**One list of requirements is being asked to describe two packages.** The `run`
section above holds mutually exclusive alternatives of the *same* dependency,
selected by something that is neither a platform nor a Python version — and
swage keeps one list per output, with no way to say "these two lines are
alternatives, pick by variant". Rewriting the list would silently collapse them
into one. §3.3.1 also intersects constraints across a Python range *because the
output is one noarch artifact*, which stops being true when the same output is
sometimes compiled.

So swage **refuses the recipe outright**, before planning starts:

```
markupsafe                                                           FAILED
  unsupported conditional noarch in /build/noarch
    noarch: ${{ "python" if use_noarch }}
  the recipe chooses whether this output is noarch rather than stating it, so
  one output builds both an architecture-specific and a noarch package, with
  different requirements
  swage keeps one list of requirements per output and would collapse those into
  a single wrong answer -- update this feedstock by hand
```

Detection is a `noarch` value that is chosen rather than stated: a template
expression, or an `if:`/`then:` list in place of the `build` mapping. A recipe
that says `noarch: python`, says `noarch: generic`, or says nothing at all has
settled the question, however many variants it goes on to build.

**Build variants are not the criterion, and were never meant to be.** This
section once refused any recipe that mentioned a multi-valued key from its own
`recipe/conda_build_config.yaml`, on the theory that a variant multiplies
artifacts and so multiplies requirements. That reasoning does not hold. A
feedstock building three mpi variants, or one artifact per Python, is an
ordinary conda-forge feedstock: its variants differ in compilers, `${{ mpi }}`
and build strings — lines swage keeps verbatim under §3.3.6 — and where a
requirement really does belong to one variant, a v1 recipe says so in
`if:`/`then:` structure a reader can see. What made `markupsafe` unreadable is
that it is v0, where the selectors are YAML comments; and swage routes v0 to
migration without planning it (§3.1), so the check never protected against its
own example.

The measured cost of the over-broad version, over the 48 v1 recipes on default
branches in the maintainer's checkouts: five refusals, every one for `mpi`,
every one a feedstock that happened to declare `mpi` locally — while
`libnetcdf`, `moab` and `libpnetcdf` built the same three mpi variants off
conda-forge's global pinning and passed. It selected for where a variant was
written down rather than for any hazard, which is worse than no check, because
it reads as protection.

Supporting `markupsafe` properly would mean modeling a requirements section as
several variant-conditioned lists and producing a plan per variant — a real
change to the core model, worth making only if enough feedstocks need it. Until
then the failure is loud and specific, which is the whole requirement: a recipe
swage cannot safely touch should say so in a way that sends the maintainer
straight to the reason.

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

**A build string is the recipe's, whatever the name in front of it is.** A
conda match spec has three fields — name, version, build — and the third is one
upstream metadata has no way to state, so `hdf5 * nompi_*` is a requirement
nothing in a `pyproject.toml` could ever explain. That makes it *part of what
names the requirement* rather than a constraint on one:

```yaml
  host:
    # need to list netcdf-fortran, hdf5 and libnetcdf twice to get version
    # pinning from variants and build pinning from ${{ mpi_prefix }}
    - hdf5
    - hdf5 * ${{ mpi_prefix }}_*
```

Those are two requirements, and `esmf`, `mpas_tools` and `e3sm-tools` all say
so in a comment. Filed under `hdf5` alone the second read as a *constraint
change* to the first, so swage rewrote `hdf5 * ${{ mpi_prefix }}_*` to `hdf5`
and the mpi build pinning left the recipe — silently, because every gate was
satisfied: both lines are conda-forge's own, G1 was already asking about them
for a different reason, and G5 saw a requirements-only change. The comment
explaining the pair went with it, since a section holds one preserved remark
per requirement and the two lines shared a key.

> **Seven lines across three feedstocks in the fleet audit of 18 August**, all
> in `host`, all of them dropping an mpi build pin. Nothing was pushed — then
> because nothing in the fleet was blessed, and now because their own checks
> hold them (§5.4). The idiom itself is not rare — 18 of the 91
> feedstocks on disk state a requirement with a build string, `libnetcdf`,
> `netcdf-fortran`, `moab`, `nco` and `parallelio` among them — and it is
> harmless wherever the recipe does not also state the package plain.

§3.3.5 already said this was the behavior, in the sentence explaining why
building three mpi variants is not a reason to refuse a feedstock: its
variants differ in "compilers, `${{ mpi }}` and build strings — lines swage
keeps verbatim under §3.3.6". They were not kept, and §3.3.6 did not say they
were. It does now.

**No line in the fleet has both a build string and an upstream declaration to
answer to** — 0 of 487 — which is the case this rule does not decide. Were
there one, swage would state upstream's version of the requirement beside the
recipe's pinned one and G1 would hold the feedstock with both quoted, which is
the right way round for a question nobody has had to answer yet.

The build backend in `host` is **not** on this list, though it looks like it
should be. `flit-core ==3.12.0` comes from upstream's
`[build-system] requires = ["flit_core==3.12.0"]`, exact pin included — it is
upstream-derived metadata that happens to live in a different table of
`pyproject.toml` than the runtime dependencies. Reconciling `host` means reading
that table too, and §3.6.2 covers what that costs: core metadata does not carry
it, so a sdist's `PKG-INFO` alone cannot reconcile `host` at all.

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
  G1: unrecognized template in `google-cloud-bigquery`'s `run` requirements
    ${{ pin_compatible('numpy') }}
  preserved unchanged; add to recipe_owned in config to bless it
```

That is goal 5 doing its job — anything novel stops and waits for a human — and
blessing it afterwards is one line of config. It also protects §3.3.7: were
`recipe-kept` a fallback for "swage could not explain this", every never-upstream
dependency would quietly acquire provenance, sail through G1, and the protection
against deleting undocumented maintainer intent would evaporate. The two rules
only hold each other up while this one stays an allowlist.

**swage plans `host` and `run`. Whether it plans part of `build` is an open
question** — §3.3.6.1, and the one substantial thing this document does not yet
answer. `run_constraints` is a longer story with a gate of its own, see §3.3.9.

**The `python` line stays symbolic, and how it is spelled is the output's
business.** A `noarch: python` output writes `python ${{ python_min }}.*` in
`host` and `python >=${{ python_min }}` in `run`, precisely so the floor moves
when conda-forge moves it (§3.3.3). An architecture-specific output writes a
bare `python` and takes its pin from the build variant. Both are conda-forge
conventions rather than anything upstream declares, both are `recipe-kept`, and
swage rewrites neither — so this rule needs no per-output branch, only the
awareness that seeing a bare `python` in `host` is not a recipe missing its
floor.

The one case where a noarch output's `python` lines are wrong is when upstream's
`requires-python` floor rises above conda-forge's `python_min`, and §4.1 makes
that a stop: raising a package's Python floor is a packaging decision with
consequences for everyone downstream, not a dependency reconciliation. So swage
either leaves the `python` line alone or stops; it never rewrites it.

#### 3.3.6.1 `build` is not simply off-limits, and this is undecided

The claim above used to read *"swage plans `host` and `run`, and writes nothing
else; `build` holds compilers and cross-compilation helpers that have no
relationship to upstream metadata."* The second half is false for every
cross-compiled Python package, and the fleet says so plainly:

```yaml
# pyproj
requirements:
  build:
    - if: build_platform != target_platform
      then:
        - python
        - cross-python_${{ target_platform }}
        - cython          # <- also in host, and it is upstream's
  host:
    - python
    - pip
    - cython
    - proj
    - setuptools
```

`cython` is in `build` because a cross-compiled build needs it for the build
platform, and in `host` because that is where the recipe states upstream's
`[build-system] requires`. **15 of the 19 outputs in the maintainer's checkouts
that have a cross-compilation block repeat a `host` requirement inside it** —
`cython`, `numpy`, `cffi`, `pybind11`, `maturin`, `grpcio-tools`. Every one of
those names comes from upstream's build-system table, which §3.3.6 already says
swage reconciles.

So a swage that adds a build requirement to `host` and leaves `build` alone
produces a recipe that builds natively and fails when cross-compiled — a defect
that would not appear on the platform CI usually runs first, and one no gate as
currently written would catch.

**What is not in doubt:** compilers, `${{ stdlib(...) }}`, `${{ mpi }}`,
`gnuconfig`, `make`, `cmake` and the rest of `build` are recipe-owned, and swage
must never author them. They answer a question upstream metadata does not ask.

**What is in doubt** is the shape of the rule for the rest:

- a **mirror rule** — the cross-compilation block tracks `host`'s
  upstream-derived entries, and nothing else in `build` is swage's — is the
  obvious candidate, and would have swage adding `cython` to two places while
  never touching the compiler beside it;
- but the block is *not* a copy of `host`: `pyproj` mirrors `cython` and not
  `proj`, `python-eccodes` mirrors `numpy` and `cffi` and not `findlibs` or
  `eccodes`. What gets mirrored is the subset needed *on the build platform*,
  which is a packaging judgment about each dependency, not a set operation;
- and `apache-beam` conditions two of its mirrored entries on the Python
  version as well as on the platform, so the rule has to compose with §3.3.1.1
  rather than sit beside it.

**This needs enough examples before it needs a rule**, which is why it is
recorded here as an open question rather than answered. The compiled corpus
carries seven outputs with a cross-compilation block; a rule should be written
against those and checked against every cross block in the fleet, the way the
test-matrix rule was checked against conda-smithy (§3.7).

**Until then the interim rule is a gate, not a refusal.** swage plans `host`
and `run` as it does everywhere else, and where the plan would *change* an
output's `host` and that output has a cross-compilation block, the feedstock is
held for review rather than merged unattended. A change rather than an addition,
because a bumped bound needs mirroring exactly as much as a new line does. The
work is still pushed and the recipe is still correct as far as it goes; what a
human is being asked is whether the block beside it needs the same edit.

**A reordering is neither, and that was most of what the gate asked about.**
Of the 17 outputs it held in the fleet audit of 16 August, **8 stated exactly
the requirements swage planned, in exactly those words** — `cassandra-driver`,
`dulwich`, `google-re2`, `pendulum`, `pinecone`, `pyspharm`, `python-ldap`,
`timezonefinder` — and differed only in the order §6 puts them in. What a
mirroring decision needs is which requirements the block repeats and under what
constraint, and a reordering changes neither. So the gate compares the
requirements a section holds rather than the lines it renders, and a question
asked about half of these feedstocks was a question with no answer in it.

> **Blocks do tend to follow `host`'s order, and it is not a reason to hold
> one.** Of the 20 cross-compilation blocks in the fleet that repeat two or
> more `host` names, **18 list them in the order `host` does** — `netcdf4` and
> `esmf` are the exceptions. That correspondence is real, and what it argues
> for is reordering the block to match when swage reorders `host`, in a section
> swage does not write today. It is never an argument for asking a human
> whether a dependency needs mirroring, which is the only question this gate
> exists to ask.

**A change to a requirement no cross build could want is neither, and that is
the rest of what it asked about.** `setuptools` is imported by the build
backend, which already runs under `cross-python_*` and takes it from the host
prefix — so a bumped `setuptools` cannot leave a block stale, because no block
copies it. The fleet says so about a whole class of requirement rather than
about one name: `setuptools` appears in the `host` section of **15 of the 19
outputs with a cross-compilation block and exactly one repeats it in `build`**,
and the same holds for `setuptools-scm`, `poetry-core`, `packaging`, `toml`,
`tomli`, `wheel` and the rest of the pure-python packaging machinery. Against
that, `cython` is repeated by **all 6** outputs that state it, `numpy` by 3 of
4, and `cffi`, `pybind11`, `maturin` and `meson-python` by every output that
has one. Those are two visibly different populations, not a line drawn through
the middle of one.

So `config/defaults.yaml` carries the half of the question that has an answer,
as `pure_python_build_tools`, and the gate holds only where a changed
requirement could need a copy on the build platform: any name not on that list,
plus any name this output's own `build` already repeats — whose copy a bump
would leave stale, whatever the list says about it.

**Which way this fails is the point.** The list is an allowlist and a name
nobody has checked holds the feedstock, so an incomplete list costs a review
and never a recipe that builds natively and fails cross-compiled. It is config
rather than inference for the reason `config/name-map.yaml` is: which packages
ship something a build has to execute is a fact about conda-forge that somebody
checks once, and guessing it from a name is the kind of inference §3.6.1 rules
out everywhere else.

> **Nine outputs to five, and two feedstocks all the way out.** Of the nine the
> gate held in the fleet audit of 19 August, four change only names on the
> list — `esmf`, `lazy-object-proxy`, `libcst`, `mpas_tools` — and
> `lazy-object-proxy` and `libcst` are held by nothing else at all, so both
> become ordinary proposals. The five that remain each change `cython` or
> `numpy`, which is the question this gate exists to ask. `pymssql` stays among
> them over `standard-distutils`, a name deliberately left off the list because
> conda-forge publishes no package under it.

**What this does not do is answer the open question.** Which requirements a
cross-compilation block should repeat is still a judgment per dependency, and
swage still writes nothing into `build`. What has been settled is only which
requirements the question does not arise for.

> **What that is worth is one feedstock, and the rest is noise removed.** Seven
> of the eight are held by something else too — `pinecone` by a name that does
> not resolve, `timezonefinder` by a bound the recipe states and upstream does
> not — so what changes for them is a sentence in the report rather than a
> verdict. `pyspharm` is held by nothing else and moves to needing only a
> `trust` line. A gate that asks a real question on 9 outputs is worth more
> than one that asks it on 17, whichever way the buckets fall.

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

**But not every never-upstream line should ever get an entry, and reading the
paragraph above as "eventually they all do" is a mistake with teeth.** A third
kind of line exists, it is common on conda-forge, and it is the one case where
a gate that keeps failing is the *feature*:

```yaml
        # temporary constraints to avoid pip check problems
        - pyiceberg >=0.8.1
        - pynacl <1.6.1
```

Some other package's metadata is wrong — a dependency of a dependency declaring
the wrong bound — and fixing it properly means a pull request against a
feedstock somebody else maintains. Until that lands, the recipe carries a
constraint that has nothing to do with its own upstream and everything to do
with a bug elsewhere.

> **A temporary workaround must not be blessed.** `add_requirements` says
> conda-forge needs this dependency *for good*. An entry for a workaround
> silences G1 permanently, and the constraint then outlives the bug it exists
> for with nothing left to notice — the recipe keeps pinning `pynacl <1.6.1`
> years after the fix, because the one mechanism that would have asked about it
> was switched off on the day it was added.

So the honest answer for these lines is **no config at all**. G1 fails, the
feedstock needs a human at every version bump, and that is precisely the point:
a version bump is when somebody should check whether the upstream fix has
landed and the constraint can go. The gate is the reminder, and its cost — one
feedstock that never merges unattended — is what the reminder is worth.

This makes the three answers to a never-upstream line, in the order the report
should offer them: **drop it** where conda-forge does not need it; **declare
it** where conda-forge needs it permanently; **leave it** where it is a
workaround, and let the gate ask again next time. Only the middle one is a
config entry, and a tool that generates config for unexplained lines (§8) must
not present it as the default — it is the answer that is wrong for this whole
class, and the class is not small.

**`retire` (§4) is how "drop it" gets written down, and it reaches a
conditional entry only when it covers the whole of one.** swage does not delete
a structure it did not author on evidence about one of the names inside it, so
an `if:` entry is preserved and its dependencies attributed one at a time.
Where config accounts for every name inside, that reason is spent: the entry
states nothing anybody still means, and it goes with the lines it holds. Where
it accounts for only some, the entry stays whole, because removing it would
take the rest away with it — the same all-or-nothing rule §3.3.4 reads the
`noarch_platform` templates under, for the same reason.

> **Two feedstocks were unanswerable until this.** `colorlog` conditions
> `colorama` on Windows in `host`, where upstream declares it only to run;
> `dulwich` conditions `setuptools` on python 3.12 and up, where upstream
> declares it only to build with. Both are exactly the artifact `retire`
> exists for, and `config/feedstocks/dulwich.yaml` carried a comment saying
> so — and saying the line needed a recipe edit instead, which was true of the
> mechanism rather than of the line.

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
> upstream-dropped requirement is held for review regardless of the other gates,
> and the report names the dropped lines and the version they disappeared in. Under `removals: auto` an upstream-dropped removal is an
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
- A whole *extra* disappearing is handled in §3.3.11. Where its dependencies
  fold into an output's `run`, they are upstream-dropped removals and G8 covers
  them; where the extra was published as an output, G4 stops the feedstock and
  the output is removed by hand.
- Recipe-owned lines (§3.3.6) are never removals — they are kept by definition,
  not by a decision the planner makes.
- **A `retired` line (§4) is removed but not gated.** G8's proving period is
  about swage's own reading of two releases, and a `retire` entry is not that:
  it is hand-written, and it is only ever reached once upstream has been asked
  and had nothing to say about the name in any version or under any extra. The
  maintainer has already written the answer down, so holding it asks them to
  re-decide it on every feedstock the entry covers, every time. G1 has always
  exempted retired lines for this reason; G8 did not, and that was an
  oversight rather than a policy.

  > **It cost a fifth of the fleet's backlog, invisibly.** The google-cloud
  > family retires `google-api-core`, the grayskull workaround described in
  > §3.2 — 38 of its 50 feedstocks carry the dead line, and every one of them
  > was held by G8 for a removal `config/families/google-cloud.yaml` explains
  > in three paragraphs. It stayed hidden because `scan` only plans feedstocks
  > with an open bot pull request and none of these has one; the first
  > fleet-wide `audit` (§8.2) surfaced it as 36 held feedstocks all reporting
  > the same sentence.

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
judgments about the ecosystem, and no metadata anywhere contains them. This is
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

**An association is not a blessing, and most entries should not get one.**
Where an entry merely restates an extra — upstream's bound, copied — the recipe
has already made the translation this section opens by rejecting, and writing
`extra: <name>` for it says that copy is to be maintained from now on. The
answer there is to take the entry out of the recipe, which is a change swage
does not make. So the association is left unwritten, the gate goes on asking,
and it stops asking when the entry goes. `dnspython` carries eight of those and
`google-resumable-media` three, and both lose them at their next version bump.
`extra: <name>` is for the entry that tracks an extra *and* is meant to stay,
which is the rarer case of the two.

> **G9 — every `run_constrained` entry is associated.** A recipe containing an
> entry that no config association explains is held for review, with the
> unassociated entries named. The recipe is still updated — `host` and `run`
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

**A finding is two halves, and only one of them is publishable.** What is
wrong is said in terms of the recipe and of upstream: the line as the recipe
spells it, the path of the section it sits in, and what upstream does or does
not declare. What to do about it names a config key, and those keys exist in
swage's repository rather than in the feedstock somebody is reading (§5.4).
Every outcome above carries the halves separately, so the pull request comment
can render the first alone while the terminal report, `swage explain` and
`run.json` print both.

**A package name does not identify a finding; the line and the section it is
in do.** A recipe states the same dependency in `host` and in `run` routinely,
and one section can state it twice with different build strings — `esmf`
states `hdf5` three times and `netcdf-fortran` three more, across two sections
of one output. Reported by name alone, those six findings were six identical
sentences.

**Said the way a maintainer would say it, never as a path.** A section is
`` `pymssql`'s `host` requirements ``: the package the output builds, and the
key the recipe states it under. The first version of this printed the block's
position in the parsed document — `/outputs/1/requirements/run` — which reads
as a file to go and open and numbers the outputs from zero besides. The
package name is the output's own and not the recipe's, since
`parsl-with-visualization` states lines `parsl` does not; and it is what a
report calls the output rather than what config matches it by, because an
output that only stages something for later outputs — `gdal`'s `core-build` —
has requirements to report on and no package name at all. Of the 349 planned
outputs in the fleet, every one has a name a report can use.

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

#### 3.3.11 When an upstream extra disappears

§3.3.7 separates the two kinds of removal at the level of a single line. An
*extra* going away upstream is a third thing that looks like a removal, and it
has two shapes with different answers.

**The extra was one of several folded into an output's `run`** — the
`outputs[].run.extras` shape (§4), and the common one. Its dependencies are
ordinary upstream-dropped removals: upstream made an observable change, the
evidence is the same as for any other dropped line, and swage removes them,
pushes, and lets G8 hold the pull request while `removals: review`. Nothing
here needs a rule of its own; the only thing worth noticing is that the *extra*
disappeared and not merely a dependency, so the report says so.

**The extra was published as an output of its own** — the `extras_as_outputs`
shape. The output is now orphaned: it is built from an extra upstream no longer
declares. That is G4, and the answer is that **removing the output is the
maintainer's job, not swage's.**

Two reasons, and the first is decisive:

1. **swage's write path cannot do it without giving up the thing it exists for.**
   The writer replaces requirements-block line ranges and nothing else (§3.1).
   An output spans `package:`, `build:`, `requirements:` and `tests:`, so
   deleting one means removing an arbitrary region — and teaching the writer to
   do that forfeits "G5 true by construction", which is the property the whole
   recipe layer was designed around and the stated reason CRM was rejected. A
   rare case is a bad price for that.
2. **swage could not finish the job anyway.** `extras_as_outputs.supported`
   still names the vanished extra, and that lives in swage's own repository,
   which swage does not write. A human commits here regardless — and a run that
   deleted the output while leaving `supported` naming it would leave the recipe
   and the config disagreeing, which is worse than not having touched it.

So swage reconciles every surviving output, pushes that work, fails G4, and the
report names both halves of what is left to do:

```
apache-airflow-providers-amazon                              NEEDS REVIEW
  G4: output `apache-airflow-providers-amazon-with-pandas` is built from
      upstream extra `pandas`, which 9.2.0 no longer declares
  the other outputs were reconciled and pushed; this one needs you to
    - delete the output from recipe.yaml
    - remove `pandas` from extras_as_outputs.supported in
      config/feedstocks/apache-airflow-providers-amazon.yaml
```

This costs very little coverage. Only 2 of the 89 providers in the prior art
publish extras as outputs at all (`amazon` and `common-sql`), while the
`outputs[].run.extras` shape above — fully automated — is the one the
google-cloud family uses throughout.

#### 3.3.12 Self-referential extras, and the bundle outputs they explain

An extra routinely refers to *the project's own* other extras:

```
# apache-airflow-core 3.3.0
all = ["apache-airflow-core[graphviz,gunicorn,kerberos,otel,statsd]"]

# google-cloud-bigquery 3.43.0
all = ["google-cloud-bigquery[bigquery_v2,bqstorage,geopandas,...]"]
```

Read literally, `all` has one dependency, and that dependency is the package
being built. **swage expands these rather than resolving them**: where a
requirement's name is the project's own name, its extras are looked up and
spliced in, recursively, with a visited set so a cycle terminates.

Detection is **structural — the requirement's name equals the project's name**
— and not the extra being called `all`. The prior art hardcodes
`SELF_REFERENTIAL_EXTRAS = ("all",)`, which works only for as long as everyone
picks the same word for it. `apache-airflow` alone has `all`, `all-core` and
`all-task-sdk`.

A requirement naming a *different* project with extras is not this. It is an
ordinary dependency whose conda-side name may or may not exist, and §3.2 covers
resolving it.

**This is what the bundle outputs are.** It is tempting to read
`airflow-with-all` and `apache-airflow-core-with-all` as conda-forge
inventions — metapackages someone assembled by hand — and treating them that
way would put them out of scope. They are not. They correspond exactly to
upstream bundling extras:

| output | upstream extra |
|---|---|
| `apache-airflow-core-with-all` | `apache-airflow-core[all]` |
| `airflow-with-all-core` | `apache-airflow[all-core]` |
| `airflow-with-all` | `apache-airflow[all]` (104 entries) |

So a bundle is not a fourth kind of output needing a model of its own; it is
`outputs[].run.extras` naming a single bundling extra, and it has to be
maintained in sync with upstream like any other. What makes bundles *look*
special is only that their extra is large and that some of its members have no
conda-forge package — which is §3.3.13, not a structural difference.

That they drift is not hypothetical. Upstream `apache-airflow-core[all]` is
`graphviz, gunicorn, kerberos, otel, statsd`; the recipe's
`apache-airflow-core-with-all` carries `graphviz, kerberos, otel, sentry,
statsd`. One of `gunicorn` missing and `sentry` added is a deliberate decision
and the other may be drift, and nothing in the recipe distinguishes them.

#### 3.3.13 Dependencies deliberately not included

Sometimes an upstream dependency has no conda-forge package, or has one that
cannot be used. **Usually that is a blocker and swage should stop** — G2
already does, since the name will not resolve, and shipping a recipe missing a
dependency it needs is worse than shipping nothing.

Bundling extras are the exception, and `airflow-with-all` is why. Upstream's
`all` names 104 packages; a dozen have no conda-forge equivalent or are known
broken against something else in the environment. For a bundle, "give me
everything available" is genuinely more useful to a user than nothing at all,
which is not true of an ordinary output. The recipe records this today as
commented-out lines with reasons attached:

```yaml
        # not on conda-forge
        # - apache-airflow-providers-akeyless >=0.1.0
        # doesn't work with uvicorn >=0.37.0 (dependency of apache-airflow-core)
        # - apache-airflow-providers-google >=10.24.0
```

Eleven of them, in one output. **Requirements sections are swage's to render
(§6), so swage would regenerate that section and delete every one** — reasons
included. §3.3.7 protects a line that is *present* and unexplained; nothing yet
protects a decision about a line deliberately *absent*.

> **An omission has to be declared before swage will make it**, and once
> declared it is sticky. The quirks database records the package and the
> reason; swage then leaves it out on every subsequent run without asking
> again.

```yaml
# config/feedstocks/airflow.yaml
outputs:
  airflow-with-all:
    run:
      extras: [all]
      exclude:
        apache-airflow-providers-akeyless: not on conda-forge
        apache-airflow-providers-google: >-
          doesn't work with uvicorn >=0.37.0 (a dependency of
          apache-airflow-core)
```

Three properties follow, and each is a deliberate choice against a plausible
alternative.

**Sticky, rather than re-proposed each version.** swage could re-add an omitted
package on every update and let the maintainer rediscover that it still does
not work. That is defensible — constraints do get lifted — but it makes the
maintainer re-derive the same conclusion indefinitely, and the work is
open-ended. Re-enabling is instead an explicit act, taken when there is reason
to think something changed.

**The reason is rendered back into the recipe.** §4 accepts a real cost for
`skip`: a decision recorded in swage's repository is invisible to a
co-maintainer reading the feedstock, and it calls that cost "left unsolved
rather than solved badly". *That cost does not apply here*, because swage
renders this section and can therefore emit the omission as a comment it owns:

```yaml
        # excluded: apache-airflow-providers-google
        #   doesn't work with uvicorn >=0.37.0 (a dependency of
        #   apache-airflow-core)
```

This is not a convention a co-maintainer has to know about — swage regenerates
it from config every run, exactly as it does `# from the X extra`. Config stays
the source of truth, so the decision survives; the recipe stays readable, so
the next person sees why. It is the one place the visibility problem §4 gave up
on is actually solvable, and only because swage owns the rendering.

**A stale omission is reported, never gated.** The failure mode of stickiness
is an exclusion outliving its reason: the provider appears on conda-forge and
nobody notices for a year. swage knows the channel's package list, so it says
so and leaves the decision alone — the same bargain as a newly appeared extra
in §4:

```
airflow                                                        MERGE-READY
  note: apache-airflow-providers-cohere is now on conda-forge
        (excluded since 3.1.0: not on conda-forge)
```

**`exclude` is per entry and never a policy.** A blanket "drop whatever is not
on conda-forge" would be wrong on every output except a bundle, and would turn
G2 — the gate that catches a name swage could not resolve — into a silent
filter. Each omission is named, and naming it is the decision.

> **Measured, and deferred.** As specified this reaches `run` and no other
> section, and no feedstock has yet needed it: `airflow`, the feedstock it was
> written for, is planned since §3.6 learned to read a release per output, and
> what its `airflow-with-all` output declines is whole extras rather than
> packages within one — which `outputs[].run.skip` already records. The
> fleet's two unresolvable names are elsewhere —
> `oldest-supported-numpy` on `gdal` and `standard-distutils` on `pymssql`
> are both `host` requirements, which this shape does not cover — and neither
> feedstock is unblocked by clearing them: `gdal` also fails G1, G9 and G13,
> `pymssql` G1 and G13. So the demand is one section short of the schema and
> zero feedstocks deep. Build it when a plannable feedstock needs it, and
> settle the `host` question then rather than guessing at the shape now.

#### 3.3.14 A bound the recipe has and upstream does not

Every rule above is about which dependencies a section holds. This one is about
a dependency that is *staying*, and about its constraint:

```yaml
    # temporarily constrain to earlier airflow and task-sdk to prevent
    # solver troubles
    - apache-airflow >=2.11.0,<3.1.3
```

`apache-airflow-providers-google` declares `apache-airflow>=2.11.0` upstream and
nothing more, so swage renders that and the `<3.1.3` disappears.

> **A constraint that differs from upstream's is drift until config says
> otherwise.** swage reconciles it like any other difference and the change is
> visible as a bump line in the plan and in the diff. What makes a difference a
> decision is an entry in the quirks database, and the entry says whether the
> bound is meant to hold for good or only until the problem it works around is
> fixed.

**The rule used to run the other way**, and the asymmetry is what undid it.
swage held a recipe that refused a version upstream allows — a ceiling — while
silently reconciling a recipe that *allows* a version upstream refuses, which
is the same kind of difference in the other direction. Measured across the
fleet's renderings the two are the same size: four feedstocks state a bound
tighter than upstream, five state one looser, and `pyspark-client` raising
`pyarrow >=15.0.0` to `>=18.0.0` was as much an override of a recipe's own
words as dropping a cap is. Treating one as a decision to defend and the other
as drift to fix was a guess about which differences maintainers meant, and no
metadata supports it.

So both directions reconcile, and a bound somebody means to keep is written
down:

```yaml
# config/feedstocks/apache-airflow-providers-google.yaml
temporary_constraints:
  apache-airflow:
    bound: "<3.1.3"
    reason: airflow 3.1.3 breaks the solver for this provider
```

**Two keys, because they make different claims.** `constraints` says the bound
outlives the reason it was added for; `temporary_constraints` says it must not,
so swage keeps the bound *and* asks again at every update until somebody
re-checks it. A workaround becoming permanent because nobody looked is
the failure the second key exists to prevent, and it is why this is not one key
with a flag: what you write is the claim you are making.

#### 3.3.14.1 A line upstream does not declare at all

An override tightens a bound on something upstream declares. Some workarounds
have nothing to tighten:

```yaml
# recipe/recipe.yaml, in airflow-with-all
    # temporary constraints because of bad dependencies
    - snowflake-connector-python !=4.4.0
    - pyexasol !=1.1.1,!=2.0.0
```

Neither package is a dependency of any `airflow` output. They are dependencies
*of* dependencies, named here so the solver cannot reach releases that are
still on the channel and still broken — 4.4.0, 1.1.1 and 2.0.0 all resolve
today. No `temporary_constraints` entry can express that, because there is no
upstream declaration to intersect with, and `add_requirements` says the
opposite of what is meant: that conda-forge requires the line for good.

> **`temporary_requirements` is `add_requirements` with the other claim**, the
> same shape and the same required `reason`, exactly as `temporary_constraints`
> is `constraints` with the other claim. The line is rendered and accounted for
> (G1), and it is reported at every version bump (G11) so somebody decides
> whether the bad release is still reachable rather than the bound outliving
> the problem.

**The two keys are one list by the time anything plans.** Everything downstream
wants the same thing from both — render the line, account for it — and only
G11 asks which key it came from. Splitting the shape at the config surface and
joining it immediately afterwards is deliberate: the surface is where the claim
is made, and the planner has no use for the distinction.

**This was unusable until failing that check stopped costing the update.**
Recording a workaround only pays if swage re-asks at the next version bump, and
while any failing check meant nothing was pushed, a bound nobody could retire
kept the feedstock from ever reaching one — so the honest options were to
delete a load-bearing line or to leave the feedstock stuck. §5.4's split is
what makes this key worth having.

`reason` is required on both, for the reason it is required on
`add_requirements` (§4): a config entry that silences a check and explains
nothing is worse than the check.

Four details, each a decision rather than an implementation note:

- **The bound is intersected before rendering**, so it goes through the same
  clause ordering (§6) and the same satisfiability check as everything upstream
  said. An entry no upstream version can satisfy is a stop, with its own
  message pointing at the config file rather than at upstream.
- **An entry accounts for the bound it states and no other.** Where the recipe
  goes further than config, the difference is drift like any other and swage
  reconciles it — the entry is a claim about one bound, not a license for
  whatever the recipe happens to say.
- **The previous version's metadata is not needed to tell the cases apart.** A
  floor above upstream's is either hand-applied or a bound upstream has since
  lowered, and under the old rule that distinction decided whether swage could
  drop it. Now neither is a reason to hold: both reconcile, and the one that
  was deliberate is the one in config.
- **`constraints` is not `run_constraints`** (§3.3.9), though the names are one
  word apart. This one tightens a dependency the package installs; that one is
  about the recipe's `run_constraints` section, a bound imposed on whoever
  happens to have the package in the same environment.

The line's `Provenance` is unchanged — upstream still explains why the
dependency is there, which is G1's question. Config explains why the bound
differs, which is G11's. Keeping them apart is what lets a feedstock record a
temporary pin without also claiming upstream asked for it.

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
concept in §4.

> **The feedstock name is the team's `name`, not its `slug`.** GitHub flattens
> a dot to a hyphen when it derives a slug, so `proj.4` becomes `proj-4` and
> `sqlean.py` becomes `sqlean-py`. Six of the 487 are affected, and every one
> has a live feedstock under its *name* and nothing whatsoever under its slug.
> Reading slugs 404s on precisely the feedstocks whose names are odd enough
> that nobody would think to check them.

**Not every team is a feedstock.** `all-members` is an org-wide team with no
repository behind it, and nothing in the team object distinguishes it. That is
one 404 in 487, which is cheaper than a hardcoded exclusion list that would go
stale silently — so discovery reports what it found and the read that follows
deals with a feedstock that turns out not to exist.

> **A feedstock's name is not its package's name**, and `proj.4` is the case
> in point: the feedstock is named for what the project used to be called, its
> recipe is v1, and the package it builds today is `proj` 9.8.1. Nothing may
> infer one from the other. A repository is `<feedstock>-feedstock`; a package
> name comes from the recipe and from nowhere else.
>
> This is worth stating because the inference is easy to make by accident, and
> `extras_as_outputs.suffix` (§4) is where it would land: an output named
> `{name}-with-{extra}` wants the *package* name, and every feedstock using
> that shape today happens to have both names the same, so the mistake would
> be invisible until it wasn't.

This replaces the google-cloud tool's approach (search all repos, fetch every
recipe, check `recipe-maintainers`), which costs ~600 API calls instead of ~5.
Measured end to end, the one call answers in under six seconds.

#### 3.4.1 Which pull request, when there are several

A feedstock routinely has more than one open bot pull request — 7 of the 15
that have any, at the time of writing — so choosing is a policy that has to be
stated rather than an edge case to be assumed away. They come in two shapes:

- **superseded version bumps.** `cime_gen_domain` carries v6.1.120 through
  v6.1.123 side by side, and only the newest describes the release anyone
  wants.
- **migrations.** `libcf` carries four rebuilds for successive Pythons. These
  are the same author doing a different job: no version changes, so there is
  nothing upstream to reconcile against that the recipe does not already have.

> **swage acts on the most recent version update, and the report says how many
> pull requests there were.** Naming the count is the load-bearing half: acting
> on one of four without saying so is how a maintainer discovers months later
> that swage has been ignoring three.

**Two accounts file version bumps, not one.** `regro-cf-autotick-bot` is the
autotick bot. `conda-forge-admin` is the admin service, and it files
`chore: update package version to <version>` when a maintainer asks for a bump
by hand rather than waiting — which is exactly what a maintainer does when the
bot's own pull request is stuck. Both are listed in `BOT_AUTHORS`.

> **Failing to recognize an author does not skip the feedstock — it selects a
> staler pull request.** This is the sharp edge, and it is not what the phrase
> "swage ignores what it does not recognize" suggests.
> `apache-airflow-providers-google` had the admin service's 22.3.0 pull request
> open with `main` on 19.1.0, and the only candidate swage could see was the
> bot's 21.0.0 from four months earlier. swage planned 21.0.0 and reported
> nothing unusual, because the newer pull request did not exist as far as it
> was concerned. An unrecognized author is therefore a correctness bug, not a
> coverage gap.

Widening the list does not widen what swage acts on. The admin service files
far more rerenders and `MNT:` migrations than bumps — 193 of its 200 open pull
requests across conda-forge when this was written — and those move no version,
so the version test below drops them exactly as it drops the bot's own
migrations.

**Recognizing a pull request is not the same as being able to write to it.**
The admin service forks with `maintainer_can_modify` false, so swage can read
and plan its pull request but its push is refused, and the feedstock is
reported failed. That is the right way round — swage says it cannot act rather
than acting on the wrong pull request — but "a version bump swage cannot write
to" deserves its own verdict rather than arriving as a push failure, and that
is open work.

**Migrations are out of scope, and deliberately so.** They change no version,
so there is nothing upstream to reconcile that the recipe does not already
have — and on green CI they are a trivial merge. Leaving them to a human keeps
the accountability of somebody having looked and judged it safe, which is
worth more here than the few seconds it saves. Automating them is possible
future work; today the risk of getting it wrong outweighs the benefit.

**A version update is detected from the version, never from the branch name.**
The bot's `rebuild-*` versus `<version>_<hash>` convention would work today and
would break in silence the day the bot changes it — and a silent break here
means swage acting on pull requests it was told to leave alone. So the test is
whether the recipe's version differs from the version on the branch the pull
request targets. That comparison also yields the base version the planner needs
to tell an upstream-dropped removal from a never-upstream one (§3.3.7), so it
is one read answering two questions rather than a check paid for on its own.

**Four open pull requests is a signal, not a coincidence.** conda-forge's bot
stops filing new ones once four of its previous sit unmerged, so a feedstock at
four is one where the bot has given up and no newer version will be offered
until the backlog clears. Both examples above are at exactly four, and so is
`apache-airflow-providers-amazon`. That is worth reporting in its own right:
the difference between "three superseded pull requests" and "this feedstock has
stopped receiving updates".

**An archived feedstock is ignored**, and detectably so for free, because a
pull request carries its base repository. Nothing can be pushed to it or merged
into it, so a pull request sitting on one is a pull request no automation
should touch. Four feedstocks are in this state, one of them still wearing an
`automerge` label that will never act.

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

> **Every read passes `--method GET`, and the reason is not tidiness.** `gh`
> infers POST from the presence of an `-f` field, so the identical argv without
> it *creates* against the endpoint instead of reading it — and against
> `/pulls` that means opening a pull request on somebody else's feedstock.
> This is not a hypothetical: calling the pull listing by hand produced
> `"base", "head" weren't supplied`, which is GitHub declining to open one only
> because the arguments for it were missing. A read path that can turn into a
> write path by omission is exactly the accident §5.5's care about ordering
> exists to prevent, one layer lower down.

**Absence is a first-class answer, not an error.** A 404 gets its own type, and
most of the time it is the ordinary case rather than a failure: a feedstock
with no `recipe/conda_build_config.yaml`, a feedstock conda-smithy has never
rendered, a `recipe/recipe.yaml` that is missing because the feedstock is still
v0 and wants routing to migration (§3.1). Re-deriving "does not exist" from an
error message at each call site is how the most common condition in the fleet
eventually gets reported as a corrupt file.

What a read costs is worth keeping down, because it is per feedstock. The
recipe is one call and `conda_build_config.yaml` is a second that usually 404s.

**`.ci_support` is the common path, not the exception**, and assuming otherwise
gets the shape of the read wrong. Only 4 of the 60 noarch feedstocks in the
maintainer's checkouts set their own `context.python_min`; 55 refer to
`${{ python_min }}` without setting it, so the build floor comes from a
rendered build config (§3.3.3). Reading it is therefore a separate call the
caller makes deliberately, rather than a flag on the recipe read — because only
the recipe can say whether the floor is needed, and a flag would mean
discovering that *after* the recipe has been read and then fetching the recipe
a second time to collect one more file.

### 3.6 `upstream` — two sources that do not agree

Upstream metadata reaches swage two ways: a `pyproject.toml`, which is the
airflow-providers path (a file in a monorepo tag), and a sdist's core metadata
`PKG-INFO` / `METADATA`, which is the google-cloud path. They describe the same
release and they are not interchangeable.

**Which release, and where it lives, both come out of the recipe.** The sdist
path needs no query about what upstream published most recently: `source.url`
already names the archive and `source.sha256` already pins it, so swage
downloads exactly what this pull request proposes to build and verifies the
bytes against the recipe's own claim before reading a line of them. The tag
path is the same idea — the tag is built from the recipe's `context.version`.
This is §3.3.3's reasoning applied to a second value: a number that decides
what swage reconciles against should not be one that can move between the
read and the decision. It also removes a failure that would be invisible,
where the bot bumps to 3.44.0 and swage reconciles against a 3.45.0 that
appeared in between.

A hash mismatch is therefore a stop rather than a warning, and it earns its
keep immediately: sweeping the maintainer's checkouts, it caught a
half-finished version bump whose `sha256` had been updated while the `version`
its URL is built from had not.

**A recipe may build several releases, and each output reconciles against its
own.** `airflow-feedstock` packages three sdists at two independent versions —
`apache-airflow`, `apache-airflow-core` and `apache-airflow-task-sdk`, the last
of them moving on its own schedule. Reconciling every output against whichever
source came first would be a silently wrong answer of exactly the kind §3.3.2
refuses, so swage reads all of them and decides per output.

> **An output draws on the release that declares its name.** The
> `apache-airflow-core` output builds the sdist whose metadata says
> `Name: apache-airflow-core`. That is a fact stated by the archive rather
> than a guess about source order or a shell script parsed out of
> `build.script`, and it needs nothing written down for the outputs that are
> the distributions upstream publishes.

The rest are metapackages, and upstream has no distribution for them.
`airflow-with-all` folds in the extras of `apache-airflow`;
`apache-airflow-core-with-all` those of `apache-airflow-core`. Nothing in
either the recipe or the metadata tells those two apart, so
`outputs[].upstream` is where it is stated (§4), naming the release by the
project it declares. An output that neither matches nor is placed stops the
feedstock, and the message names both the releases available and the key.

**Two sources declaring the same project stop it too.** `aiohttp` pins its
sdist and a GitHub archive of the same release, one for the package and one
for sources the tests need; both call themselves `aiohttp`, so the name that
tells releases apart does not tell these apart. That feedstock is stopped and
`outputs[].upstream` cannot unstick it — the key names a project, and both
sources answer to the same one. Solve it when a feedstock needs it; nothing is
lost by refusing, since the two archives are the same release either way.

**The feedstock is still a release of one thing.** The first source is what
names it — the version in the pull request title, the commit message, the
report line — and that is `RecipeUpstream.primary`. `swage draft` writes every
source's metadata into the workbench, each under the directory the recipe
unpacks it into, so the reader who needs the other two has them.

**A recipe that builds several releases can disagree with itself, and G14 is
what catches it.** `airflow`'s outputs depend on each other:
`apache-airflow-core` requires `apache-airflow-task-sdk`, which the same recipe
builds. Upstream 3.3.1 asks for `==1.3.1`; the recipe's
`context.task_sdk_version` pins the 1.3.0 sdist, because the bot bumped
`version` and left the line beside it — commented *manually update with each
airflow release* — alone.

Every line involved is individually right, so nothing in the diff shows the
conflict. What swage would push builds a task-sdk that the core package it is
built beside refuses to install with.

> **What the recipe builds is not what `context.version` says.** It is the
> version in the URL each source pins and the hash it verifies, which is what
> `RecipeUpstream` already read. Asking the archives rather than parsing
> `context` means the check cannot be fooled by a variable that names one
> thing and holds another — which is the failure being looked for.

swage reconciles the requirement to what upstream declares and stops there. The
cause is in `context`, and swage writes nothing outside a requirements block
(§3.1), so this is reported and gated rather than fixed. A constraint swage
cannot evaluate — a template, a build string — is passed over: a check that
cannot read a constraint has found nothing, and a templated pin follows the
same context variable the source URL does, so it cannot disagree with it by
construction.

#### 3.6.1 Extra names are normalized; package names are not

The two formats spell the same extra differently. `google-cloud-bigquery`
3.43.0's sdist carries both files, and they disagree:

```
pyproject.toml   [project.optional-dependencies]   bigquery_v2
PKG-INFO         Provides-Extra:                   bigquery-v2
```

Build backends apply PEP 685 when they write core metadata; nothing applies it
to `pyproject.toml`. This is routine rather than exotic — across the corpus,
`apache.iceberg`, `cncf.kubernetes`, `common.messaging`, `microsoft.azure`,
`microsoft.mssql` and `GSSAPI` all come back respelled — and it reaches extras
carried by a dependency too, where `pyhive[hive_pure_sasl]` becomes
`pyhive[hive-pure-sasl]`.

> **Every extra name is PEP 685-normalized on the way in, from both sources,
> and the quirks database is written in that form.**

Left alone, an extra's name would depend on which file an sdist happened to
ship, and three things would break quietly. Config keyed on the other spelling
misses, so an `embedded_extras` entry stops expanding. G3 names an extra the
maintainer already declined in `skip`, which is advice pointing at the wrong
fix. And the block-header comments of §6 change spelling — those live *inside*
requirements blocks, so **G7 byte-identity would become a function of the
source path**, making "no changes needed" unreproducible.

Package names are deliberately *not* normalized at this layer. That is §3.2's
job, and the mapper needs the name as upstream wrote it to look up quirks.

> **The two namespaces have opposite rules, and applying the wrong one is a
> mistake that reads as correct.** Extra names *must* be normalized, because
> the two metadata sources disagree about them. **conda-forge package names
> must not be**, because conda-forge does not normalize its own package names:
> `config/name-map.yaml` really does map `msal-extensions` to
> `msal_extensions`, and `facebook-business` and `slack-sdk` do the same. The
> underscore is the package name, not a spelling mistake.
>
> Normalizing a conda name before comparing it silently loses the match, and
> the symptom points somewhere else entirely: a dependency upstream plainly
> declares gets reported as coming from nowhere, sending the maintainer to
> `add_requirements` for something that needs no entry at all. Anywhere swage
> compares a recipe line against a conda name, both spellings are indexed and
> the exact one is tried first.
>
> **This applies to the channel too, and the numbers are not small.** conda-forge
> publishes 2,163 packages whose names carry an underscore and 544 whose names
> carry a dot, out of 33,842 — `kubernetes_asyncio`, `zope.interface` — and
> nothing under their PEP 503 forms. An identity check that asks only for the
> normalized name cannot resolve any of them. That is what half-implementing
> this rule looks like from the outside: a dependency conda-forge plainly has,
> reported as unresolvable.

#### 3.6.2 `[build-system] requires`, and absent versus empty

`flit-core ==3.12.0` in every airflow provider's `host` looks like a
conda-forge convention and is not one. It is upstream's own
`[build-system] requires = ["flit_core==3.12.0"]`, exact pin included — the
same metadata as the runtime dependencies, in a different table (§3.3.6).

Absent is kept distinct from empty. A missing `[build-system]` table reports
`None`, never an empty list, because the planner reconciles `host` against this
and *"upstream told us nothing"* must not arrive looking like *"upstream needs
nothing"* — the second is how a `host` section gets emptied. PEP 518 makes
`requires` mandatory once the table exists, so a table lacking it is a
malformed file rather than a project that needs nothing to build.

Core metadata has no build-system information at all, so it reports `None` by
construction. **A `host` section therefore cannot be reconciled from a sdist's
`PKG-INFO` alone, and the sdist path must prefer an sdist's `pyproject.toml`
wherever it ships one.**

**Preferring it is not the same as requiring it to be readable, and the two
tables are read independently.** Downloading all 88 source archives the
fleet's noarch recipes name refused 24 of them, and 21 of those were PyPI
sdists carrying a complete `PKG-INFO` beside a `pyproject.toml` swage cannot
use: 15 declare no PEP 621 `[project]` table at all — poetry and plain
setuptools both do this — and 3 more compute their dependencies at build time.
Refusing a fifth of the fleet while holding metadata that states the answer
outright is the mistake §3.6.3 rejects for a dynamic `Requires-Dist`, and for
the same reason: the list is present and complete, and only its provenance is
unusual.

> **Each half of the metadata comes from the file that can state it.** The
> runtime dependencies come from `[project]` where it is readable and from
> `PKG-INFO` where it is not; `[build-system] requires` comes from
> `pyproject.toml` whenever the file is there at all, whatever state
> `[project]` is in.

**Every output's `host` is attributed against `[build-system] requires`, even
one that draws no core dependencies.** `outputs[].run.core` says whether an
output takes upstream's *runtime* dependencies — it is nested under `run` in
config for that reason — and reading it as a statement about `host` too left
`google-cloud-bigquery`'s metapackage reporting its own `setuptools` as coming
from no upstream version, while the identical line in the `-core` output beside
it attributed fine.

> **Attribution and rendering answer differently here, on purpose.** swage
> indexes the backend so a line that is there can be explained; it does not
> *plan* one into an output whose `host` never carried it. The fleet's recipes
> disagree about which outputs build from source — `google-cloud-bigquery`'s
> metapackage lists `setuptools`, and all 18 of the amazon provider's
> `-with-*` outputs list no backend at all — so adding one uniformly would
> change 18 recipes nobody asked to change.

> **A line unexplained here is not a line upstream never mentions, and the
> report used to say it was.** Because `host` is reconciled against the
> build-system table alone, a *runtime* dependency listed in `host` fails G1 —
> correctly, since nothing upstream says it is needed to build — under the
> sentence "is in the recipe and in no upstream version". That sentence is
> false, and it is the one a maintainer acts on: read literally it invites
> dropping a line upstream asks for. So where the name is declared in the
> other role, the report names that role instead, and offers the same
> `add_requirements` remedy without the claim.
>
> Six lines across five feedstocks in the fleet audit of 16 August: `protobuf`
> in `googleapis-common-protos`, `numpy` in `esmpy`, `netcdf4` in
> `mpas_tools`, `pyasn1` and `pyasn1-modules` in `python-ldap`, and — the one
> case the other way round — `setuptools` in `mpas-analysis`'s `run`. Six of
> 174 such lines, which is what makes this a wording fix rather than a rule
> change: the verdict is unchanged and only the reason is now true.
>
> **The sentence names the section as well.** Its first wording said the name
> was declared "rather than in this section", which is not a section anybody
> can go and look at: the summary line carries no path and no output name, and
> a recipe states the same dependency in `host` and in `run` routinely —
> `mpas_tools` states `netcdf4` in both, and it is the `host` one that fails.
> So the sentence names the section the line sits in and the upstream list
> that section is reconciled against, which between them are what make the
> remedy actionable. Every other outcome of §3.3.10 now opens the same way,
> with the line and its path, for the same reason.

That second clause is the one that matters here, and 18 of the 88 archives
turn on it: a poetry project states `poetry-core` in `[build-system]` and
nothing whatsoever in `[project]`. Reading only the table that failed would
leave `host` unreconcilable and every line in it unexplained at G1, which is
this very section's argument pointed at the wrong outcome.

**The shallowest match wins**, not the first one found. An sdist keeps its
metadata beside a single top-level directory, and matching on a path suffix —
which the prior art does — picks a vendored or fixture copy from deeper in the
tree whenever one sorts earlier.

**Where the shallowest one is the wrong one, config says so.** A monorepo's
release tarball carries a `pyproject.toml` per project in it, and the root one
frequently describes no package at all — `OpenLineage`'s ships seven, and its
root file configures the repo's tooling. Which subdirectory holds the package
is not something swage can infer, and guessing wrongly reconciles a recipe
against a different project entirely, so `upstream.metadata` names it (§4).
This is the answer the airflow family already has for the same problem, which
is a good sign it is the right shape.

That path is an **instruction rather than a hint**: when the named file cannot
be read, swage refuses rather than falling back to the root. The fallback
would be right often enough to be tempting and is exactly the silent
wrong-project failure the setting exists to prevent — a monorepo that
restructures between releases would quietly start reconciling against whatever
happens to sit at the root.

What is still refused is an archive with no readable metadata anywhere: 3 of
the 88, all of them GitHub source-repo tarballs rather than sdists. Two carry
no packaging metadata at all, and one is the monorepo above, awaiting the
config entry.

**A `setup.py` is not a metadata source, and swage will not make it one.** It
states its dependencies only by executing, and running upstream code to find
out what a recipe should say is not a trade swage makes — a compromised or
merely careless release would be executing on the maintainer's machine, with
the maintainer's credentials, during an unattended run. An sdist built from
one carries `PKG-INFO` anyway, which is where those 21 archives are read from.
`setup.cfg` is declarative and could in principle be read; nothing in the
fleet needs it, so it is not.

**But that `PKG-INFO` is frequently silent about dependencies, and the wheel of
the same release is not.** setuptools writes `Requires-Dist` into an sdist's
`PKG-INFO` only for a project that declares its dependencies declaratively; a
project setting `install_requires` in `setup.py` publishes an sdist naming
itself and its version and saying nothing about what it needs. The wheel is
built *after* `setup.py` has run, so its `METADATA` carries the complete list.

`alibabacloud-adb20211201` 4.1.0 is the fleet's case. Its sdist is
`Metadata-Version: 2.1` with no `Requires-Dist` line at all; its
`py3-none-any` wheel declares `alibabacloud-tea-openapi` and `darabonba-core`,
which are exactly the two lines its recipe carries and which G1 had been
reporting as coming from nowhere.

> **Where an sdist declares no dependencies at all, swage reads the wheel's
> `METADATA` for the same release.** This is not executing `setup.py` by
> another route: it is declarative metadata upstream published, parsed exactly
> as `PKG-INFO` is. No upstream code runs and no Python is parsed.

Deliberately narrow, in three ways.

It fires **only on silence** — no requirement stated anywhere, neither in the
core list nor inside any extra — never
to correct or extend a list the sdist did state. Two distributions of one
release disagreeing about their dependencies is a broken release, and
arbitrating that unattended is not swage's business. Silence and emptiness are
different claims, the same distinction this section already draws for
`[build-system] requires`, and swage does not need to tell them apart from the
sdist alone: asking the wheel costs one request and the answers only ever
agree or fill a gap. A release that genuinely needs nothing has a wheel that
says so, and nothing changes.

> **Naming an extra used to count as speaking, and that hid whole dependency
> lists.** The rule read "no dependencies *and* no extras", which sounds like
> the same claim and is not: setuptools writes `Provides-Extra` from the keys
> of `extras_require` and `Requires-Dist` only for a project that declares
> dependencies declaratively, so a `setup.py` project *with* extras publishes
> exactly the `PKG-INFO` this fallback exists for — and was the one shape it
> skipped. `flask-appbuilder` 5.2.2 names four extras, carries no
> `Requires-Dist`, and its wheel declares the 21 runtime dependencies its
> recipe already has; every one of them was reported as coming from nowhere,
> which made one feedstock 21 of the fleet's 74 outstanding requirement
> questions and the largest single entry in that backlog. `python3-openid`
> 3.2.0 is the same shape and its whole finding was `defusedxml`.
>
> Sizing it from the 289 sdists in the archive cache: **11 archives across 10
> releases** state no `Requires-Dist` while carrying an
> `egg-info/requires.txt` that lists their dependencies — `flask-appbuilder`,
> `psij-python`, `python3-openid`, `pyodps` and six `alibabacloud` releases.
> The four whose sdists name no extras were already covered; the rest are what
> this widening reaches.

`build_requires` still comes from the archive. Core metadata carries no
build-system table, so the wheel has nothing to say about `host` and must not
be allowed to blank it.

And **the bytes are verified against the index rather than against the recipe**,
which is weaker and has to be said out loud. The recipe pins the sdist's
`sha256` and swage checks it; it says nothing about a distribution it never
mentions, so the wheel is checked against the digest PyPI published for it in
the response that named it. That is a different level of trust from everything
else swage reads, so the metadata records which file stated the dependencies
and the report prints it as a note:

```
alibabacloud-adb20211201  pushed 9f2c1ab to the pull request
  note: dependencies read from alibabacloud_adb20211201-4.1.0-py3-none-any.whl;
        this release's sdist declares none
```

A release with no wheel at all is an answer rather than an error — `hdfs`
2.7.3 ships an sdist alone, so a project can be silent with nowhere else to
look, and the feedstock stops at G1 as it did before. A wheel that cannot be
read is not that: an index that will not answer or a digest that does not
match is swage being unable to tell whether there is one, and treating it as
absence would turn a broken index into a feedstock that looks dependency-free.

#### 3.6.3 A computed dependency list is recorded, not refused

PEP 643 lets a sdist flag that a field was computed at build time rather than
declared, so another build might compute something different:

```
Dynamic: Requires-Dist
```

It is tempting to refuse this the way `[project] dynamic = ["dependencies"]`
is refused, and that would be wrong. The two are not the same claim. A dynamic
`[project]` table leaves *nothing to read*; a dynamic `Requires-Dist` ships the
**full computed list** and only warns that it might differ elsewhere.

The difference is not academic. Sweeping every metadata file on the
maintainer's machine — 89 provider `pyproject.toml` and 8,759 installed
`METADATA`/`PKG-INFO` — found five real projects flagging it: `apache-beam`
(183 requirements), `openlineage-integration-common` (58), `sagemaker-studio`
(30), `influxdb3-python` (14) and `pyspark-client` (8). **Not one of them
declares a `[project]` table to fall back on.** Refusing would stop them dead
while holding complete, usable metadata, and the google-cloud tool swage
replaces does not check this at all — so it would also be a coverage
regression against the thing being replaced.

So the fact is recorded rather than acted on, and a gate weighs it (**G10**,
§5.4). This is the same shape as an inexact `Resolution` reaching G2 instead of
failing the mapper: the layer reports, the gate decides.

**This is a proving period, not a permanent rule**, on the model of §3.3.8. How
onerous G10 turns out to be depends on how many feedstocks are affected and how
often their dependencies really move, and neither is known yet. If it bites,
the escape hatch is a policy in `defaults.yaml` rather than a code change:

```yaml
dynamic_dependencies: review        # review | trust
test_matrix: review                 # review | auto -- see §3.7
```

so relaxing it for a family or a feedstock stays a config commit with an
auditable record of when and why.

#### 3.6.4 Silence about the build system means setuptools

A project with no `[build-system]` table has not left swage without an answer.
PEP 517 says such a project is built with the legacy setuptools backend, and
conda-forge follows that — the recipe still needs something in `host` to build
with. So `build_requires is None` resolves to `setuptools`, and this is the
single decision the absent-versus-empty distinction above exists to serve: an
empty `requires` is upstream saying it needs nothing, and gets nothing.

> **It is a backup for silence, never an override.** A project that names
> hatchling or poetry-core has `build_requires`, so the default never runs for
> it. swage does not replace a stated build backend, and a test guards that
> specifically: adding a backend nobody asked for is the failure that would
> matter.

The value lives in `defaults.yaml` rather than in code, so changing it is a
reviewable commit:

```yaml
default_build_requires: [setuptools]
```

It arrives through the same mechanism as `add_requirements` (§4), so the line
carries the file that asked for it and G1 explains it like any other
config-supplied requirement rather than stopping the feedstock.

The fleet says this states a settled convention rather than imposing one. 21
noarch archives declare no build system; every one ships `setup.py` and
`setup.cfg` with no `pyproject.toml`, and every one of their recipes already
lists exactly `setuptools` in `host`. Without the rule all 21 would fail G1 on
that line forever.

#### 3.6.4 The version the bot does not bump

The conda-forge bot bumps one version per feedstock: the one the feedstock is
named for. A recipe building several archives at independent versions has the
others, and nothing bumps them. `airflow` writes the instruction in the recipe
and it is an instruction to a person:

```yaml
context:
  version: "3.3.1"
  task_sdk_version: "1.3.0"  # manually update with each airflow release
```

Left undone, the recipe builds `apache-airflow-task-sdk` 1.3.0 while
`apache-airflow-core` 3.3.1 — built by the same recipe, from an archive whose
hash that recipe pins — requires `apache-airflow-task-sdk==1.3.1`. Every line
is individually right and the packages cannot be installed together, which is
G14. Until this section that was where it ended: swage reported the conflict
and a maintainer made the edit before swage could do anything else, so the
feedstock needed a person *first* and swage second.

> **swage maintains the entry**, under `source_versions: auto` in the
> feedstock's own file. It is off everywhere else.

**swage does not choose the version.** It is dictated by a sibling release's
exact pin, read out of an archive the recipe already pins and swage already
verified against that pin. Nothing asks what upstream published most recently —
§3.6's rule, and the reason the answer cannot move between the read and the
decision. A *range* dictates nothing and is passed over: `apache-airflow-task-sdk`
1.3.0 asks for `apache-airflow-core >=3.3.0,<3.4.0`, which the recipe already
satisfies and which names no version to move to.

**Which `context` entry may move is decided by three tests**, each closing a
way the edit could reach further than intended. The entry must be referenced by
that source's URL **and no other**, or moving it moves an archive nobody asked
about. It must currently hold **exactly that release's version**, or it names a
fragment of the URL rather than the version. And it must not be `version`,
which is the bot's and drives the package version of every output that reads
it. Fewer or more than one candidate is a stop.

##### swage authors a hash here, and nowhere else

Every other sha256 swage touches is a **check**: it downloads what the recipe
claims and refuses if the bytes differ, which caught a half-finished bump in
the maintainer's checkouts. This one is **written**, because the archive is one
the recipe does not name yet. That is a real change in what a hash in these
recipes means, and it is worth stating rather than discovering.

Three things narrow it, and they are the whole of the argument for doing it at
all:

- the URL is the recipe's own template with a single substitution;
- the version came from a hash-verified sibling rather than from a query;
- the downloaded archive **must declare that exact project at that exact
  version** or it is refused — which is what stands in for the check swage is
  not making. A project that renames its sdist, a mirror serving something
  else, a versioned URL quietly serving the latest release: each of those
  produces an archive whose own metadata contradicts what was asked for.

And it is opt-in per feedstock, so this is a thing a maintainer turns on for a
recipe they have looked at rather than a thing that happens.

##### The template stays a template

A recipe writing `apache-airflow-task-sdk ==${{ task_sdk_version }}` reads that
entry three times: the source URL, the built package's version, and the
requirement. Rendering `==1.3.1` into the last of those is *correct* — it says
the same thing — and it replaces a maintainer's single point of truth with
copies of a number, in a recipe swage does not own.

> **A templated constraint survives exactly when it is equivalent**: when it
> resolves, through the recipe's own context, to the line swage was about to
> write. Anything else is reconciled like any other drift, because then the
> recipe and upstream really do disagree and §3.3.14 says upstream wins.

The two halves are one change and neither is much use alone. Maintaining the
entry while flattening the lines that read it leaves a recipe whose requirement
is a literal and whose source is a variable — correct, and one bump away from
looking like the conflict G14 exists to catch. Preserving the templates without
maintaining the entry leaves the entry stale, which *is* that conflict.

### 3.7 `tests` — the second thing swage writes

Everything above reconciles requirements. This does not: it is a conda-forge
convention, enforced by conda-smithy's linter, that a `noarch: python` recipe
should test the *latest* Python as well as the minimum. swage is already in
the recipe when the bot bumps a version, and this is a change it can make
correctly while it is there.

**The rule, read from `_python_tests_cover_latest` rather than from the hint
text.** For a v1 recipe, per output where `build.noarch` is `python`:

- every entry in `tests` that has a `python:` key must have a
  `python.python_version` list containing the exact entry `"*"`;
- an entry *without* a `python:` key is skipped entirely;
- `python_version` absent counts as failing;
- **and none of it applies if any `run` requirement is `python` with a `<` in
  it.** A capped Python makes a latest-Python test meaningless, so the linter
  skips the whole check.

That last clause is not a corner case, and reading the source rather than the
hint is what found it. Of the 45 feedstocks in the maintainer's checkouts with
a Python test that does not cover the latest, **22 cap Python in `run`** —
just under half. A swage that added `"*"` from the hint text alone would have
written a latest-Python test into 22 feedstocks that deliberately do not
support the latest Python, and every one would have been a wrong answer that
CI was entitled to fail.

**What it writes is one shape.** Across the same checkouts, 334 python test
blocks: 93 already carry the two-item list, 241 carry a scalar
`python_version: ${{ python_min }}.*`, and exactly one omits the key. So the
edit is a scalar becoming a list, 241 times out of 242:

```yaml
tests:
  - python:
      imports:
        - globus_cli
      pip_check: true
      python_version:            # was: python_version: ${{ python_min }}.*
        - ${{ python_min }}.*
        - "*"
```

**It never opens a pull request of its own.** swage writes this only where it
is already pushing a dependency update, so the migration costs no new pull
requests and rides the CI run that was going to happen anyway. The backlog
drains as versions bump. The cost is stated rather than hidden: adding the
entry makes CI test a Python the package has never been tested on, so a real
incompatibility can turn a routine bump red — an incompatibility that was
already there, and that shipped to users silently while nothing looked for it.

**It is the first thing swage writes outside a requirements block**, which
costs a structural guarantee. Until now "only requirements changed" held *by
construction*: the writer replaced the line ranges the reader identified and
could not touch another byte. With a second splice region that becomes a claim
to check rather than a property to rely on, and §5.4's wording changes with it.

So it gets a proving period, in the idiom the other two already use
(§3.3.8, §3.6.3): while `test_matrix: review`, a recipe whose test matrix
swage changed is held for a human rather than merged unattended. Once that has
been boring for a while, one commit to `config/defaults.yaml` flips it fleet
wide.

```yaml
# config/defaults.yaml
test_matrix: review       # review | auto
```

**Multi-output is handled per output and, in practice, barely arises.** The
linter scopes to each output's own `build.noarch` and `tests`, so swage does
too — but the maintainer's multi-output family is the airflow providers, whose
19 outputs test with `script:` rather than `python:` and are therefore skipped
by the rule entirely. The shape that needs the edit is the single-output
recipe with one `python:` test.

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

The archive path is spelled **`source: archive`, not `source: pypi`**, because
what it selects is *the archive the recipe's `source.url` pins* and where that
archive is hosted is not the point: the google-cloud family fetches PyPI
sdists and `openlineage-python` a GitHub release tarball, and both are one
operation. It takes an optional `metadata:` naming the file to read inside
that archive (§3.6.2):

```yaml
# config/feedstocks/openlineage-python.yaml
upstream:
  source: archive
  metadata: client/python/pyproject.toml   # not OpenLineage-1.40.1/client/...
```

The path is relative to the archive's single top-level directory rather than
to its root, because that directory carries the version and these entries are
committed config — written against the root, every one would need editing on
every bump.

**`{slug}` is whatever the family's glob matched**, and deriving it that way
rather than by a rule of its own is what keeps the airflow providers from
being a hardcoded module after all. `apache-airflow-providers-*` matching
`apache-airflow-providers-apache-hive` is already the statement that
`apache-hive` identifies the package and the rest belongs to the family, so
the glob is the one thing that knows where the prefix ends. `{slug_path}` is
the same value with `-` as `/`, which is where the monorepo keeps
`providers/apache/hive/`. A second family of the same shape gets both for
free, and `{version}` comes from the recipe (§3.6).

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
    - line: grpcio-gcp >=0.2.2
      reason: conda-forge splits the grpc extra differently
```

**`outputs[].upstream` is the other key, and only a several-source recipe has
one.** It names which of the recipe's releases an output is built from, by the
project the archive declares. An output whose own name matches a release needs
nothing — `apache-airflow-core` builds the sdist that calls itself
`apache-airflow-core` — so this is written only for the metapackages, which
correspond to no upstream distribution at all:

```yaml
# config/feedstocks/airflow.yaml
outputs:
  apache-airflow-core:            # matched by name; `upstream` would be noise
    run: {core: true}
  apache-airflow-core-with-all:   # a metapackage over airflow-core's extras
    upstream: apache-airflow-core
    run:
      core: false
      extras: [graphviz, kerberos, otel, statsd]
```

On a recipe with one source it is ignored, because there is nothing to choose
between (§3.6).

`add_requirements` is how a conda-forge-only dependency stops being unexplained.
Without an entry, a line in the recipe that appears in no upstream version has no
`Provenance`, fails G1, and stops the feedstock — deliberately, because the
alternative is swage deciding on its own whether a maintainer meant it (§3.3.7).
With an entry it is kept for a stated reason. These lines are also what §6 places
in the alphabetized trailing block, since they have no upstream order to inherit.

**`reason` is a required field rather than a YAML comment, and that is a
decision about what happens when entries get cheap to produce.** While every
entry is hand-written the comment convention is fine — somebody typing one is
already thinking about why. `swage draft` (§8.1) changes that: a tool that
emits skeletons makes the *typing* free while leaving the *thinking* exactly as
expensive, and the predictable result is a database of entries that silence
gates and explain nothing. The schema therefore refuses an entry with no
`reason`, and refuses `TODO` and the empty string specifically, since those are
what a draft ships with. Anything else is accepted: judging whether a sentence
is a good reason is not the schema's business, and only the maintainer can say.

**`add_requirements` is per output as well as per section.** A line frequently
belongs to exactly one output — `apache-airflow-providers-amazon`'s
`packaging >=24.1.0,<26.0.0` is on `-with-cncf-kubernetes` and nowhere else,
and a section-wide entry would put it on all 19. The section-level form stays,
because most entries really do apply to every output:

```yaml
add_requirements:
  run:                            # every output
    - line: grpcio-gcp >=0.2.2
      reason: conda-forge splits the grpc extra differently
  outputs:
    apache-airflow-providers-amazon-with-cncf-kubernetes:
      run:
        - line: packaging >=24.1.0,<26.0.0
          reason: cncf-kubernetes needs a floor conda-forge's own does not carry
```

Two policies live in `defaults.yaml` alongside `trust`:

```yaml
# config/defaults.yaml
trust: propose
removals: review                  # review | auto  -- see §3.3.8
dynamic_dependencies: review      # review | trust -- see §3.6.3
recipe_owned:                     # see §3.3.6
  functions: [pin_subpackage, pin_compatible, compiler, stdlib]
  names: [python, pip]
```

Three points of design worth stating explicitly:

- **`supported` / `skip` must be exhaustive, where a feedstock publishes extras
  at all.** An upstream extra in neither list means swage cannot tell whether it
  was considered and declined or simply never noticed, so the feedstock is held
  for review naming the extra (G3). The dependency update is
  still pushed — the resolution is a human deciding to add an output or to write
  the extra into `skip`, and neither is urgent enough to withhold an otherwise
  correct update. `skip` **is** the mechanism for "deliberately not published";
  an entry there is a decision on the record rather than an omission.
  Same rule for `embedded_extras`: an empty list means "declared, adds nothing,"
  which is materially different from absent — though that accounting answers to
  G2 rather than to G3, since an `embedded_extras` key names a *dependency's*
  extra and this gate asks about the project's own (§5.4).
- **`skip` exists in both output shapes, and it has to.** Declaring one is how a
  feedstock opts into exhaustiveness, so a shape with nowhere to write
  "considered and declined" is a shape that can *never* opt in — G3 reports
  `n/a` on it forever, which reads as "fully specified" and is the opposite of
  what the gate is for. `extras_as_outputs.skip` covers the shape that publishes
  each extra as its own output; `outputs[].run.skip` covers the shape that folds
  several into an existing one. Implementing only the first left the entire
  google-cloud family unable to opt in, on feedstocks whose extras were
  otherwise completely described. An extra in both `extras` and `skip` is a
  typo rather than a policy and is refused at load.
- **Every extra name in config is written PEP 685-normalized** — `bigquery-v2`,
  not `bigquery_v2`; `apache-iceberg`, not `apache.iceberg`;
  `pyhive[hive-pure-sasl]`, not `pyhive[hive_pure_sasl]`. That is the spelling
  swage reads upstream extras as (§3.6.1), and a name written any other way can
  never match. Nothing downstream would say so either: a stale
  `embedded_extras` key silently stops expanding, and a stale `skip` entry makes
  G3 name an extra that was already declined. The schema therefore refuses a
  non-normalized name at load time, naming the form to write, rather than
  leaving it to fail as a lookup miss much later.
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
    note: upstream 2.19.0 declares extra 'tracing', which no output draws on
  ```

  The gate follows the declaration instead of being imposed uniformly — the same
  bargain as everywhere else. Say what you mean and swage holds you to it; say
  nothing and it tells you rather than blocking you.
- **`skip` lives in swage's config, not in the recipe.** Recording a deliberate
  omission as a standardized recipe comment is tempting: it would sit beside the
  thing it describes and be visible to co-maintainers, which a config file in a
  separate repo is not. It is declined because the convention would be
  **undiscoverable by the people it binds**. A co-maintainer has no way to learn
  that a comment carries meaning for a tool they have never heard of, so they
  could reword or delete it in perfectly good faith — and swage would then lose a
  decision, or act on a stale one, with nobody having done anything wrong. A
  convention that only holds while everyone knows about it, in a repo where there
  is no way to tell everyone, is not a convention. The visibility problem that
  motivated the idea is real, and is left unsolved rather than solved badly.
- **`outputs` unifies the two tools' divergent models.** The airflow tool's
  `MULTI_OUTPUT_PROVIDER_CONFIG` (extras become separate outputs) and the
  google-cloud tool's `RunConfig(core=, extras=)` (extras get folded into an
  existing output's `run`) are the same idea expressed twice. `extras_as_outputs`
  covers the first; `outputs[].run` covers the second; a feedstock can use both.
  Between them the two cardinalities that occur are already expressible: **many
  extras into one output** is `outputs[].run.extras`, the common case, and it is
  what the block-header comments in §6 annotate; **one extra into several
  outputs** is that extra named in each of their `extras` lists, which needs no
  new schema. **One extra across several outputs** is `outputs[].run.from_extras`,
  which names the packages an output takes from an extra rather than folding
  the whole of it in. `wetterdienst` needed it: upstream's `export` extra
  holds five packages and `zarr >=3.1` needs a later python than the other
  four, so the recipe publishes `-with-export-without-zarr` at its own floor
  and `-with-export` above it. Listing `export` whole in either would write
  all five into it. An extra named there is drawn on exactly as one in
  `extras` is — same provenance, and accounted for at G3 — and an extra may be
  taken whole or in part but never both.
- **`exclude` records a dependency deliberately left out**, per output, with
  its reason (§3.3.13). It is the fourth instance of the pattern above: swage
  refuses to drop a dependency it cannot account for, the quirks database
  supplies the account, and the omission becomes a decision on the record
  rather than an absence. Unlike `skip`, the reason is rendered back into the
  recipe as a comment swage owns, because swage renders that section anyway.
- **`constraints` records a bound the recipe adds beyond upstream's** — the
  fifth instance, and the first at the level of a constraint rather than a line
  (§3.3.14). `apache-airflow: "<3.1.3"` keeps a ceiling a maintainer applied by
  hand, which swage would otherwise drop with every gate satisfied. **It is not
  `run_constraints`**, one word away in the same file: that one associates an
  entry of the recipe's `run_constraints` section with an upstream extra
  (§3.3.9), and the two never touch the same line.
- **`trust` says what may happen to a change the checks accounted for**, and
  never whether one is offered. `propose` in `defaults.yaml` is the fleet's;
  `never` and `auto` are written per feedstock, and are the only two decisions
  a file has to carry. See §5.4.

---

#### 4.1 The Python floor is compared, not configured

`config/defaults.yaml` carried a `requires_python: min` for most of this
project's life, under a comment claiming swage refused a feedstock whose
upstream Python floor rose above it. **No such refusal ever existed**: the
value was loaded, resolved through the three config layers, printed by
`swage config`, and compared against nothing. What the comment claimed was not
wanted either — a package needing Python 3.11 is an ordinary package, and
declining to maintain its feedstock would be an odd thing for this tool to do.

The comparison worth having is narrower, and it needs no setting. A
`noarch: python` output writes `python ${{ python_min }}.*` in `host` and
`python >=${{ python_min }}` in `run`. Where upstream's `requires-python` floor
rises **above that output's `python_min`**, those lines claim a Python upstream
no longer supports, so swage stops — raising the package's floor is a packaging
decision with consequences for everyone downstream, and the message says to set
`context.python_min` in the recipe.

Three things follow, and the key was none of them:

1. **The comparison is per output, against that output's `python_min`**, not
   against a number in config. A static value cannot express "above *this*
   feedstock's build floor", which is 3.9 on 82 of the maintainer's checkouts
   and 3.10 on 135, and moves whenever conda-forge moves it. A configured
   number could only ever be a third value agreeing with neither.
2. **It does not apply to an output that is not `noarch: python`.** An
   architecture-specific output has no `python_min` to contradict; its floor is
   which Pythons the feedstock builds, and `build: skip: match(python, "<3.11")`
   is how a recipe states it — `pyproj` does exactly that.
3. **So the config key is gone.** Both numbers are in the planner's hand at the
   moment the question arises, and nothing is left for a maintainer to
   configure.

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

### 5.2 Path B — swage changed nothing (a person merges)

The recipe already matches upstream. There is no commit to push, so no CI will
run, so **nothing will ever dispatch conda-forge's automerge job again for that
commit** (§2.1). The label is inert. Left alone the PR stays open forever and
lands back on the maintainer's plate — which is exactly the tedium swage exists
to remove.

**swage cannot remove it, and the reason is a wall rather than a decision.**
This section spent its first draft specifying the merge in detail, that merge
was built, and the first time it ran against GitHub it was refused twice:

1. **"The base branch policy prohibits the merge."** Not about CI — the base
   branch enforces no status checks at all. Every merge of a bot pull request
   on these feedstocks is made by somebody entitled to bypass that policy: the
   maintainer through the green button, or conda-forge's own admin app.
   `--admin` clears it, and passing that flag is defensible for the reasons
   §5.2.2 records.
2. **"Refusing to allow an OAuth App to create or update workflow
   `.github/workflows/conda-build.yml` without `workflow` scope."** This one is
   final. conda-smithy re-renders a workflow file into most bot pull requests —
   **11 of the 14 newest ones across the maintainer's fleet** — so merging one
   means writing that file, and the credential swage borrows from the `gh` CLI
   is not permitted to. The scope can be added, and should not be: it would let
   any automation running as that maintainer rewrite CI in every repository
   they can push to, which is not a power conda-forge hands out casually and
   not one this tool needs.

So **swage does not merge, and there is no merge in it** — not behind a flag,
not behind a config key. What Path B does instead:

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
3. **Report it as `READY TO MERGE`, by name and with its URL**, and write
   nothing to the feedstock at all.

> **The check earns its keep even though nothing acts on it.** "This one is
> ready" is a verified claim — the recipe renders byte-identically (G7), every
> required provider passed, nothing else is failing — and a maintainer who
> trusts it can merge without re-deriving any of it. What swage saves on this
> path is the reading, not the clicking.

Do **not** apply the `automerge` label on this path; it does nothing and only
adds noise to the PR timeline. The one thing that *would* work — an empty
commit to start a CI run the label can act on — is a whole build spent on
nothing, and is rejected on that ground.

**`trust: auto` therefore means push-and-label, and nothing further.** The top
of the ladder is the same action as `propose` plus the label (§5.4). No rung of
it merges anything.

#### 5.2.1 What "the required set" turned out to mean

Step 1 is a port of conda-forge's own code rather than an interpretation of it,
and the parts that look arbitrary are the parts most worth copying exactly.
`_get_required_checks_and_statuses` decides which CI providers must pass **from
the files conda-smithy wrote**, because there is no API that answers it:
`azure-pipelines.yml`, `.travis.yml`, `.drone.yml`, `appveyor.yml` or its dotted
spelling, each making its provider required by existing. `linter` is required
unconditionally, which is why a feedstock with no CI at all still has a
non-empty required set, and why an empty one means something has gone wrong
rather than that nothing needs to pass.

Four details do not follow from reading the list, and each is a way to merge
something that has not passed:

- **Two providers are configured by a file that outlives being switched off.**
  conda-smithy writes a *disabled* GitHub Actions workflow rather than deleting
  it — `name: Disabled build`, `- run: exit 0`, `if: false`, all three — and
  leaves `.circleci/config.yml` in place with a `filters:` block that ignores
  every branch. Requiring a check that will never be reported stalls every pull
  request on that feedstock forever, so both files are read rather than merely
  looked for.
- **A provider is matched to a check by substring**, because the names do not
  line up: the provider is `azure` and the context Azure posts is
  `conda-forge/azure-pipelines`. One provider therefore matches several reports
  — an Azure build per platform — and a provider matching *nothing* is
  unfinished rather than passed, which is what makes "CI has not started yet"
  refuse rather than merge.
- **A GitHub Actions suite containing a run called `automerge` is not a passing
  build.** The automerge workflow is itself an Actions run, so its suite goes
  green whenever it finishes; counting it would let a feedstock's automerge job
  stand in for the build that was supposed to have passed.
- **CI configuration is read from the pull request's head and feedstock
  settings from its base.** The head is the fork, and is the commit CI actually
  ran on, so a feedstock that gained or lost a provider in this very pull
  request is judged by the right set. `conda-forge.yml` is the maintainer's
  file and is read from the branch being merged into, because a fork can say
  anything it likes — conda-forge's reason, kept.

GitHub also keeps every status ever posted for a context, so a build that went
red and was re-run has two and only the newest is current; and `mergeable` is
computed lazily, so the first read of a pull request nobody has asked about
answers `null`. Neither is a refusal: both mean *ask again*.

**`mergeable_state` is not a usable signal, and this was checked rather than
assumed.** GitHub reports a second, richer field beside `mergeable` —
`clean`, `blocked`, `unstable`, `dirty` — and reading it instead looks like an
improvement on conda-forge's rule. It is not, because it does not track whether
anything passed. Every check green, `mergeable` true, no branch protection, and
a ruleset that only forbids deletion and force-pushes — and GitHub still says
`blocked` for all three of
[google-ads#55](https://github.com/conda-forge/google-ads-feedstock/pull/55),
[weaviate-client#38](https://github.com/conda-forge/weaviate-client-feedstock/pull/38)
and
[google-cloud-aiplatform#197](https://github.com/conda-forge/google-cloud-aiplatform-feedstock/pull/197),
while equally green pull requests elsewhere in the fleet say `clean`. The split
follows whether a feedstock's CI reports as check runs or as commit statuses.

**And `blocked` is not an obstacle to the *maintainer's* merge.** They have a
green merge button on all three, checked by hand. Gating on this field would
refuse exactly the pull requests swage exists to close, so swage reads
`mergeable`, as conda-forge does, and leaves the question of whether CI passed
to CI.

It is an obstacle to a plain merge, though, and §5.2.2 is what that turned out
to mean.

#### 5.2.2 The two refusals, kept because they cost a day to learn

Neither is recoverable from documentation, and both would be re-derived from
scratch by anybody who reopened this question.

**The base branch policy.** The first merge swage attempted was refused with
"the base branch policy prohibits the merge", on `google-ads` #55, whose every
check had passed and whose `mergeable` was true. Nothing about it was CI: that
feedstock's `main` enforces **no status checks at all** —
`enforcement_level: off`, zero contexts — and its only branch rules are
`deletion` and `non_fast_forward`. The rule doing the prohibiting is not
visible to a non-admin. What GitHub does say is `viewerCanMergeAsAdmin: true`:
the maintainer can merge, and only by bypassing. **Their green button is that
bypass**, and it always has been — of the five most recent bot pull requests
merged on that feedstock, three were merged by the maintainer and two by
conda-forge's admin app. There is no non-bypassing path. `--admin` clears this,
and doing so is not overriding anybody's judgment about whether a build is fit
to merge: no such judgment is configured.

**The `workflow` scope.** With that cleared, the merge was refused again:
"refusing to allow an OAuth App to create or update workflow
`.github/workflows/conda-build.yml` without `workflow` scope". swage has no
credentials of its own and borrows the `gh` CLI's (§3.5), and that token has
`repo` but not `workflow`. Merging a pull request that re-renders a workflow
file *writes* that file, so GitHub refuses.

> **This is the one that ends the argument, because of how common it is.**
> Across the newest bot pull request on each of the maintainer's 14 feedstocks
> that have one, **11 touch `.github/workflows/conda-build.yml`** — conda-smithy
> re-renders routinely. The three that do not are feedstocks where the bot is
> backlogged or the package is dormant. Automating three stalled feedstocks is
> not worth carrying a merge path for.
>
> **Measuring it took two corrections, both worth remembering.** GitHub's
> `pulls/{n}/files` endpoint pages at 30, so three pull requests looked clean
> and were not; and counting every *open* bot pull request rather than the
> newest per feedstock mixed superseded ones into the sample. The first pass
> said "about half" and was wrong on both counts.

The scope can be added with one `gh auth refresh`. It should not be: it would
let anything running as that maintainer rewrite CI in every repository they can
push to. That is a power conda-forge does not hand out casually and swage does
not need.

### 5.3 The extra gate Path B requires

Path B makes a claim Path A does not have to. On Path A swage says "my change
is routine" and conda-forge still independently decides to merge. On Path B
nothing downstream checks anything: swage says the recipe needs no change, and
a person merges on the strength of that. The claim outlives the merge that
used to follow it — a report saying "ready" that turns out to be wrong wastes
the reading it was supposed to save. So it earns one more gate beyond G1–G6,
G8 and G9 below:

> **G7 — byte-identical rendering.** swage must render the recipe from upstream
> metadata and confirm the result is byte-for-byte identical to what is already
> in the PR. "No changes needed" is then a *verified* claim rather than an
> assumption. If swage's rendering differs at all — including in formatting or
> dependency order — that is a change, and the feedstock goes down Path A
> instead.

G7 is what makes "requires no modification" mean something precise and testable.

### 5.4 Trust gates

A feedstock's PR gets the `automerge` label only if **all** of these hold.

> **`G1`-`G11` are identifiers, not vocabulary.** They are how this document,
> the code and `run.json` refer to a check, and swage never says one out loud.
> Every check carries a plain-language title — "every requirement is accounted
> for" — and every failure carries a detail that stands on its own, because the
> surfaces these reach include a comment published to a repository swage does
> not own, read by somebody who has never seen this document. The first
> comment swage ever posted read `- **G6**: trust is 'propose', not 'auto'`,
> which is unactionable for exactly the person it was addressed to. CLAUDE.md
> carries the rule; this is the table it applies to first.

> **A config key is the same defect one step further in.** A finding says what
> is wrong; the remedy for it names `add_requirements`, `name_map`,
> `recipe_owned` — keys in a file in swage's repository, which the reader of a
> feedstock pull request has never seen and cannot edit. So a failure carries
> the two halves apart: the finding is published, the remedy stays in swage's
> own output, and `detail` holds both for the terminal report, `swage explain`
> and `run.json`. The comment on `mpas_tools-feedstock#159` said "declare it in
> add_requirements if conda-forge needs it here", which is the whole defect in
> one clause — under the maintainer's name, on a repository whose readers have
> no such file.

| | Gate | Rationale |
|---|---|---|
| **G1** | Every requirement in the plan has a `Provenance` — upstream metadata, an explicit config entry, or a recognized recipe-owned line (§3.3.6) | no unexplained dependencies. `recipe-kept` is an allowlist of recognized structural lines, never a fallback for "swage could not explain this" |
| **G2** | *(withholds the push)* Every name resolution is `exact` — no heuristic guesses, no unresolved names | §3.2 |
| **G3** | *(where the feedstock declares a `skip` list)* Every upstream extra appears in `supported` or `skip` | exhaustiveness is opt-in; without a `skip` list a new extra is reported, not gated (§4) |
| **G4** | The set of outputs is unchanged, and no published output has lost the upstream extra it is built from | a new output is a packaging decision; an output whose extra disappeared upstream is orphaned, and deleting it is the maintainer's job rather than swage's (§3.3.11) |
| **G5** | *(withholds the push)* The diff touches only requirements sections, the python test matrix, and — under `source_versions: auto` — the `context` entry and `sha256` of one source (plus formatting normalization) | anything else is out of scope for autonomy. Structural until §3.7 added a second splice region; now checked |
| **G6** | `trust: auto` for the feedstock or its family | blessing is explicit and opt-in |
| **G7** | *(Path B only)* swage's rendering is byte-identical to the PR's recipe | §5.3 — makes "no changes needed" verified, not assumed |
| **G8** | *(while `removals: review`)* The plan drops no requirement upstream dropped | §3.3.8 — a proving period, not a permanent rule. A *never-upstream* line is never dropped at all (§3.3.7) |
| **G9** | *(withholds the push)* Every `run_constrained` entry is associated with an upstream extra in config | §3.3.9 — swage rewrote `run`, and cannot tell whether entries derived from the same extras still agree |
| **G10** | *(while `dynamic_dependencies: review`)* Upstream declared its dependencies rather than computing them | §3.6.3 — a PEP 643 `Dynamic: Requires-Dist` list is complete but not guaranteed stable across builds; a proving period, not a permanent rule |
| **G11** | Every temporary constraint and temporary requirement has been re-checked at this version | §3.3.14 — a bound that differs from upstream's is drift swage reconciles; one recorded in `temporary_constraints`, or a line in `temporary_requirements`, is a workaround that must not become permanent by nobody looking |
| **G12** | *(while `test_matrix: review`)* The plan changes no python test matrix | §3.7 — the first edit outside a requirements block; a proving period, not a permanent rule |
| **G13** | *(withholds the push)* The plan changes no `host` requirement of a cross-compiled output that could need a copy in its `build` section | §3.3.6.1 — such a block repeats `host` requirements so the build tools resolve for the build platform, and which ones belong there is undecided. `pure_python_build_tools` names the ones the question does not arise for |
| **G14** | *(withholds the push)* No output requires a package this recipe builds at a version this recipe does not build | §3.6 — a split recipe's outputs depend on each other, and each line can be individually right while the two disagree. The fix is in `context`; swage makes it where `source_versions: auto` says it may (§3.6.4), and reports it everywhere else |

**What a failing gate costs depends on what the gate is about.** The checks
are not all the same kind of claim, and treating them as one was a mistake in
both directions.

> **Most of them say a decision is outstanding about a recipe that is
> otherwise sound.** G1 kept a line it could not explain — it never deletes one
> (§3.3.7) — so the forty lines around it are reconciled and one is a question.
> G3 is waiting for an extra to be classified, G4 for an orphaned output to be
> deleted, G10 for a proofread, G11 for a workaround to be re-checked, G12 for
> a proving period to end. **Those are pushed.** swage applies no label,
> comments on the pull request naming what is outstanding, and the feedstock
> is listed under NEEDS REVIEW with the note saying it was pushed.
>
> **A few say the rendering itself may be wrong.** G2 resolved a name by
> guesswork, so the line may ask for the wrong package. G5 changed something
> outside the regions swage owns. G9 rewrote `run` and cannot tell whether the
> `run_constrained` entries derived from the same extras still agree with it.
> G13 changed a `host` requirement whose copy in a cross build's `build`
> section may now be stale. G14 wrote a recipe whose own outputs cannot be
> installed together. **Nothing is pushed for those.** Offering a diff swage
> cannot vouch for asks a maintainer to check it line by line in a repository
> swage does not own, which is the one review nobody has time for and the one
> place a defect in swage becomes a defect in a recipe.

**Holding everything was the previous rule, and the argument for it does not
survive its own evidence.** It was adopted after two defects were found in a
day's sweeping — a dropped mpi build pin (§3.3.6) and a deleted maintainer note
(§6.1) — which the old rule would have pushed. But the sentence recording that
says they were found *on feedstocks whose checks were failing for unrelated
reasons*. The gates that held those recipes had nothing to do with the defects;
they were in the way by coincidence. A safety property that holds by
coincidence is not one, and the thing that actually finds a defect in swage is
a sweep over the fleet (§8.2), which found both.

What holding everything *did* do is make a feedstock's least answerable
question veto every other change to it. `airflow` is the case: two of its lines
are bounds dodging bad releases of packages it does not itself depend on, and
§3.3.14 says to leave such a bound unexplained precisely so swage asks about it
again at the next version bump. Under the old rule "asks again" meant "blocks
forever" — the feedstock could never be updated at all while a line nobody
could account for sat in it. The design's own instruction was unreachable
through the design's own gate.

> **The trust ladder is on neither side.** G6 says which rung a feedstock is on
> rather than anything about the change, which is why `held` excludes it: a
> `propose` feedstock fails G6 by definition, and reading that as a reason to
> withhold would leave `propose` unable to push, which is the whole of what
> `propose` does.

> **Moving the default exposed two places that read the ladder where it has no
> bearing**, both on path B and both hidden while every feedstock sat at the
> bottom rung. swage cannot merge a no-change pull request at any rung (§5.2.2)
> and a label on one whose CI has finished is inert (§2.1), so the ladder
> decides nothing there — yet it was deciding the bucket, which put a feedstock
> with nothing to change under "a decision is needed", and it was skipping the
> CI read, which then reported "CI still running" about builds swage had never
> looked at. `apache-airflow-providers-google` is the case: nothing to change,
> CI red, and the line beside it named a trust rung instead of
> `CI failed: azure, github-actions`. Where CI answered, CI is the line.

> **The verdict is a comment because there is no label to apply.** An earlier
> draft of this section said the pull request gets a `swage:needs-review` label.
> No conda-forge feedstock has one: the standard set is `automerge`, `bot-rerun`
> and GitHub's defaults, so swage would have to *create* the label in every
> feedstock it ever flags — several hundred repositories acquiring a permanent
> label because a tool ran once. A comment needs no such groundwork, carries the
> reason rather than only the fact, and lands where somebody reading the pull
> request is already looking.
>
> That it is safe to write anything at all after the push is worth stating,
> because the opposite would be a quiet disaster. conda-forge's
> `_no_extra_pr_commits` scans the timeline for a `labeled` event **whose label
> is `automerge`**, not for the most recent `labeled` event of any kind (§2.2),
> so nothing swage adds afterwards can re-arm automerge on a pull request swage
> has just flagged.

`needs-review` is accordingly a *verdict* swage states, not the name of anything
on GitHub, and that is how the run record spells it.

The `trust` ladder is `never` (write nothing to this feedstock) → `propose`
(push and comment) → `auto` (push, comment, and label). It decides what happens
to a change the gates *accounted for*; it never decides whether one is offered,
because the gates already answered that.

> **A rung has to correspond to work, or it is ceremony.** The ladder used to
> read `manual` → `propose` → `auto` with `manual` as the fleet default, so
> every feedstock needed a config file whose entire content was a trust line
> before swage would do anything with it — a commit that taught swage nothing
> and recorded no decision anybody could check later. The maintainer put it
> plainly: an update on the swage side is an extra step, and one that only says
> "yes" is pure overhead.
>
> What actually distinguishes a feedstock swage should write to is whether its
> config is complete, and the gates compute that already. So the fleet default
> is `propose`, a feedstock file exists only where swage needed teaching, and
> the two rungs anybody types are the two real decisions: never touch this one,
> or let this one merge unattended.
>
> `never` rather than `off` because YAML 1.1 reads a bare `off` as the boolean
> `false`, along with `no`, `yes` and `on`. The rung typed least often is the
> one that can least afford a spelling that needs quoting.

> **The two failing rungs get different sentences, because they mean opposite
> things.** G6 said "not approved for automatic merging (trust: `<rung>`)" for
> both, which is exact for `propose` — the commit is pushed, the comment is
> posted, and the label is what is missing — and answers a question nobody
> asked for the bottom rung, where nothing was written at all. The maintainer
> read that off an `--execute` run of two feedstocks they had asked for by name
> and could not account for it, which is the test this document sets: a
> sentence somebody can act on without the design open.

> **The two failing rungs get different sentences, because they mean opposite
> things.** G6 said "not approved for automatic merging (trust: `<rung>`)" for
> both, which is exact for `propose` — the commit is pushed, the comment is
> posted, and the label is what is missing — and answers a question nobody
> asked for `manual`, where nothing was written at all. The maintainer read
> that off an `--execute` run of two feedstocks they had asked for by name and
> could not account for it, which is the test this document sets: a sentence
> somebody can act on without the design open. `never`'s now says swage writes
> nothing here and names the file that changes it.
>
> The confusion under it is worth recording too, because the vocabulary invited
> it: `--execute` is about the *run* and `trust` is about the *feedstock*, and
> "manual" read as "a person drives this one" rather than "swage does not write
> here". That is what sent the rung to `never`.

Promotion to `auto` is a deliberate config commit — which, because it lives in
git, leaves an auditable record of when and why each feedstock was blessed. It
is the one rung that ends in a merge nobody reviewed, and no family may confer
it.

### 5.5 The partial-failure hazard

Per §2, pushing to a `[bot-automerge]` PR without labeling it leaves that PR
*less* automated than before swage ran. So the push-then-label sequence must be
treated as one unit:

- Label immediately after a successful push, as the very next API call.
- If labeling fails, retry; if it still fails, report the PR as
  **`DEGRADED — pushed but not labeled`** at the top of the report, not buried
  in a success list. This state requires human action.
- On a follow-up push to an already-labeled PR, remove-then-re-add the label to
  produce a fresh timeline event. This works because the push that precedes it
  starts a fresh CI run, which is the thing that dispatches automerge (§2.1).
- `swage status` (§8) re-detects any PR left in this state and reports it. It
  does **not** re-label it: by then CI on swage's commit has finished, and a
  label with no CI run to follow it dispatches nothing. Where that CI went
  green, status reports the PR as `READY TO MERGE` instead, which is the same
  outcome one click further away.

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

Rule 2 is the fleet's own convention rather than an imposition: across the
readable recipes, 159 `run` sections put `python` before a `pin_subpackage`
line and 2 put it after. Rule 3 does *not* cover an `embedded_extras`
expansion, which has an order to inherit — its parent's. See below.

**Rule 3 also covers a line swage kept without being able to explain it**, and
that is not obvious from the rule as stated. Such a line carries
`Provenance(origin="recipe-kept")` as a *placeholder* — `recipe-kept` is an
allowlist, never a fallback (§3.3.6) — so ordering on the origin alone sorted
it with the structural lines and hoisted it above every upstream requirement in
the section. It belongs where it will sit the moment somebody writes it into
`add_requirements`, since documenting a line should not also move it.

**Clause order within a constraint** — bounds first, floor then ceiling, and
exclusions last:

```
google-auth!=2.24.0,!=2.25.0,<3.0.0,>=2.14.1 -> >=2.14.1,<3.0.0,!=2.24.0,!=2.25.0
google-api-core<3.0.0,>=2.28.0               -> >=2.28.0,<3.0.0
kubernetes>=35.0.0,!=36.0.0,<37.0.0          -> >=35.0.0,<37.0.0,!=36.0.0
```

A range reads as a range that way: "2.14.1 up to 3.0.0, minus two" rather than
the ceiling buried behind the holes. Several exclusions keep upstream's order
among themselves, which is the only thing there is to go on for them.

> **This section previously specified "floor first, then upstream's own order",
> on the claim that one rule satisfied both families. It does not, and widening
> the golden corpus to google-cloud is what showed it.** Hoisting the floor is
> enough for `<3.0.0,>=2.28.0`, which is why the rule survived a corpus whose
> only multi-clause constraint had three clauses and came from a
> `pyproject.toml`. Add an exclusion to an alphabetized source and it breaks:
> `!=` sorts before `<`, so the rule yields
> `>=2.14.1,!=2.24.0,!=2.25.0,<3.0.0` where every published google-cloud recipe
> says `>=2.14.1,<3.0.0,!=2.24.0,!=2.25.0`.

Preserving the declared order cannot be made to work for both sources, and that
is the deeper reason for a canonical one. A `pyproject.toml` keeps its author's
sequence; a `METADATA` has been alphabetized by the build backend. Preserving
either makes a recipe's formatting depend on which file a sdist happened to
ship — the problem §3.6.1 solves for extra names, one namespace over. Goal 2 is
consistent formatting *across* feedstocks, and that needs one order rather than
the union of two authors' habits.

**The prior art split here, so only one of the two can be reproduced.** The
google-cloud tool canonicalized to exactly this order; the airflow tool passed
the constraint through as upstream wrote it, which is why `kubernetes` above
changes. Fleet cost of choosing: two lines.

The declared order survives only in the raw requirement text — `packaging`
sorts a `SpecifierSet` alphabetically, and it also intersects by *unioning*
clauses, so `>=2.1.2` and `>=2.3.3` come back as both rather than the one that
binds.

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
        # tightest of upstream's floors (python >=3.14)
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

**A header is written only where the section could answer the question more
than one way** — where it draws core dependencies as well as extras, or draws
more than one extra. An `extras_as_outputs` output takes exactly one extra and
no core dependencies, and its *name* already says which, so
`apache-airflow-providers-common-sql-with-pandas` carrying
`# from the pandas extra` above every line it has is redundancy rather than
provenance. None of the published provider recipes do it; every google-cloud
recipe that folds several extras into one output does.

The two conventions differ because the situations do. A `# from the X extra`
header *partitions* a section — every line after it belongs to that extra until
told otherwise — so an opening marker suffices. A `# start`/`# end` pair
*delimits an island* of expanded dependencies sitting inside a list of ordinary
ones, where there is no next header to imply the end. Using paired markers for
both would double the comment count in the case that needs it least.

**The pair is not symmetric in where it can live.** `# start` sits above the
first expanded line like any other comment, but `# end` belongs *below* the
last one — so it becomes the leading comment of whatever requirement follows,
or the section's trailing comment where the expansion runs to the end of the
block. Both placements occur in the corpus and both have to round-trip, which
is why a requirements block models trailing comments at all.

**An expansion that is empty on purpose gets a caption instead of a pair.**
`embedded_extras` distinguishes absent from empty (§4): absent means nobody has
looked at the extra, and G2 stops the feedstock over it (§3.2); empty means
somebody did, and conda-forge needs nothing beyond the bare dependency. That is
a decision, and it is recorded on the line it is about:

```yaml
    # celery[redis] needs nothing extra on conda-forge
    - celery >=5.5.0,<6
```

The prior tools recorded the same decision as a `# start` / `# end` pair around
no lines at all — 13 of the 22 marker pairs in the maintainer's checkouts are
that shape, so a *negative* answer is the mechanism's commonest use rather than
an edge case. A pair delimiting nothing states the conclusion only by
implication, and delimits an island with no extent; one line says it outright,
where the reader's question arises. The retired form needs no entry in
`plan/authored.py`'s `_RETIRED`, since the marker pattern already matches it.

Both are swage-authored: requirements sections are swage's to render (§3.3.6),
so these comments are regenerated from the plan rather than preserved from the
previous recipe.

> **A golden test that compares dependency lines cannot see any of this.** Both
> rules above were wrong until a corpus recipe was rendered and compared *byte
> for byte*: swage annotated every line of an output named for its extra, and
> emitted no marker pairs at all — so the first thing it would have done to a
> recipe carrying them is delete the markers that make a rerun idempotent
> rather than additive. Comment rendering is exactly as load-bearing as the
> dependencies, because G7 is a claim about the file.

**The extra names in both conventions are the normalized ones** (§3.6.1), so a
recipe reads `# from the bigquery-v2 extra` and `# start pyhive[hive-pure-sasl]`
whichever metadata source the plan was built from. Since these comments sit
inside requirements blocks, this is what keeps G7 from depending on which file
an sdist happened to carry.

**A third convention records a deliberate omission** (§3.3.13), rendered from
`exclude` in the quirks database rather than preserved from the recipe:

```yaml
        # excluded: apache-airflow-providers-google
        #   doesn't work with uvicorn >=0.37.0 (a dependency of
        #   apache-airflow-core)
```

It sits where the dependency would have gone, in upstream's own order, so the
gap is legible at the point it matters. This is the one swage-authored comment
that describes something *not* in the list, which is exactly why it has to be
generated rather than remembered: a hand-written note about an absent line has
nothing anchoring it, and the next rerender would drop it.

### 6.1 A comment swage did not write belongs to the dependency below it

The three conventions above are swage's own, regenerated every run. Everything
else in a requirements section was written by a maintainer, and **swage
preserves it, anchored to the requirement it sits above.**

```yaml
        # conda-forge package includes google-auth[pyopenssl] extra
        - google-auth >=2.14.1,<3.0.0
```

That note is about `google-auth`. It has to move when `google-auth` moves,
survive when the constraint is bumped, and disappear only when the dependency
does. The recipe model already works this way — a `Requirement` owns the
whole-line comments above it, which is the property that ruled out
conda-recipe-manager (§3.1) — so this is a rule about the *planner*, which is
the layer that decides what a rendered line's comments are.

> **The rule is that swage replaces only the comments it authors.** A
> requirement's rendered comments are the ones swage generates for it this run,
> followed by every comment the recipe had above it that swage did not write.

Without the second half the behavior is not merely lossy, it is *inconsistent*
in a way nobody would predict: a line swage cannot attribute keeps its comments,
because the planner passes them through with the line it declined to touch,
while a line swage explains has them replaced by whatever it generated — which
is usually nothing. So a note survives above a dependency swage does not
understand and is destroyed above one it does. `google-cloud-bigquery` carries
the corpus's only instance and it is of the second kind, which is why this went
unnoticed.

**Recognizing swage's own output is the whole difficulty**, and it is a
versioning problem rather than a parsing one. A comment matching a current
convention must be dropped before regeneration or every run duplicates it. But
the conventions have already changed once: recipes across the fleet carry
`# more restrictive for python >=3.14` and `# more restrictive constraint for
python >=3.14`, both of which *were* swage's marker comment in the sense that
matters — a tool generated them, no human chose them, and re-anchoring them as
maintainer prose would leave 53 recipes with two notes above one dependency
saying the same thing differently.

> **So the set of patterns swage recognizes as its own includes the retired
> ones, and retiring a convention means adding to that list rather than editing
> it.** The cost of forgetting is not a lost comment but a duplicated one, on
> the first run after a wording change, across every feedstock at once.

**A comment a person wrote can be retired too, and two have been.** The test
is not who typed it but whether swage now says the same thing in the same
place. `# strictest constraint for python >=3.13` on `google-ads` is the
marker note in a third spelling. `# conditional for python <3.13` on
`apache-airflow-providers-amazon` labels one of a pair of lines stating the
same dependency per python range — and swage collapses that pair into the
tightest of the two, so preserving the label would leave the recipe describing
a split it no longer has. Both were the maintainer's to give up and were given
up deliberately, feedstock by feedstock, rather than inferred. The direction
of doubt still runs the other way: a comment swage cannot account for is
prose, and prose is kept.

Three consequences worth stating, because each is a decision rather than a
detail:

- **Order is generated-then-preserved.** A block header (`# from the X extra`)
  partitions the section and must lead; swage's note about the line comes next;
  the maintainer's note sits closest to the dependency it describes. Stable
  ordering is what keeps G7 from depending on which comments a recipe happened
  to have.

  **A blank line is spacing rather than a note**, and is the one thing that
  precedes the generated comments. Ordered with the maintainer's remarks it
  lands *between* swage's note and the dependency the note is about, which
  reads as though the two were unrelated —
  `apache-airflow-providers-google` has a blank line above a marker note and
  would have been rendered exactly that way.
- **A preserved comment is not provenance.** It explains nothing to G1 and
  earns a line no `Provenance` — a dependency is justified by upstream metadata
  or by config, never by a remark next to it. Otherwise `add_requirements`
  would be optional and §3.3.7's protection would evaporate, which is the
  failure §3.3.6 already refuses for `recipe-kept`.
- **A comment above a removed line is removed with it.** It was about that
  dependency; there is nothing left for it to describe, and leaving it behind
  would re-anchor it to whatever followed — the exact corruption §3.1 rejected
  CRM for.

**A note at the end of a section is anchored to the section**, since there is
no requirement below it to belong to. That is the one position where what swage
generates is a marker rather than a note about a line, and the same
generated-then-preserved order applies: swage's `# end` closes its expansion,
and the maintainer's remark follows it. Without this the remark was deleted,
because swage renders the section and carried only what it had anchored to a
requirement.

> **Two sections in the fleet end this way**, `pymssql`'s `host` and one of
> `parsl`'s `run` lists, four lines between them, and each is a commented-out
> dependency with the reason it is commented out. On `pymssql` the note says
> `# seems to work without standard-distuitils` and swage's plan puts
> `standard-distutils` back, because upstream declares it under
> `python >=3.12`: deleting the note would reverse a decision and remove the
> record of it in one edit.
>
> `gdal` and `apache-airflow-task-sdk` write the same commented-out shape in
> the *middle* of a section, where the rule above already carries it with the
> requirement below — which is the measurement that says this is the last
> position where a comment was being lost. Across the 300 recipes of that
> audit, three lost a comment swage does not claim as its own: these two and
> `esmf`, whose loss is §3.3.6's.

What this does *not* solve is the decision itself. A preserved remark is still
just text — swage will go on proposing the dependency it argues against every
run, and G1 has nothing to say about a line that is not there. Recording an
omission so that swage stops re-proposing it is `exclude`'s job (§3.3.13),
which is specified and not yet built; the two are complementary, and this is
the half that stops the reasoning being thrown away in the meantime.

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

**A migration is never automerged**, regardless of the feedstock's configured
trust. Conversion is documented as imperfect by both feedrattler and CRM; a
converted recipe gets human eyes and a needs-review verdict, full stop. This
is a deliberate hard-coded exception to the trust ladder, not a default that
can be configured away.

Said precisely, because the ladder has three rungs and this caps rather than
replaces: a migration is treated as **at most `propose`**. A feedstock at
`trust: never` is still not written to, because that setting is the maintainer
saying "not this feedstock" and a conversion is not the thing that overrides
it. A feedstock at `propose` or `auto` gets both commits pushed and a comment,
and never the `automerge` label.

**A conversion is also the one thing §5.4's rule does not hold for.** There, a
change no check accounted for is not pushed at all; here the diff touches every
line of the recipe, so the checks have nothing to say about it and a person
reviews it whatever they said. The exemption is the ceiling itself rather than
an addition to it.

> **An earlier wording said "always `trust: manual`", and that could not be
> made true.** In the ladder as implemented, the bottom rung means swage writes
> nothing at all — so reading it literally would mean a migration never
> pushes, which contradicts §7.1 below in its entirety, that section being
> about what a migration pushes and in what order. What was meant is the
> ceiling, and the rung that expresses it is `propose`.

**Converting a compiled recipe is a different job from converting a noarch
one**, and the phase should be planned as two. A v0 recipe states everything
conditional in selector comments — `# [win]`, `# [not use_noarch]` — and a v1
recipe states it in `if:`/`then:` structure, so a compiled feedstock's
conversion is exactly the translation §3.1's reader models, applied in the
write direction and across every section rather than only requirements. Of the
fleet's **148** v0 feedstocks, **105** build a `noarch: python` package and
**41** use a compiler; the first group is the mechanical case CRM handles well,
and the second is where a conversion needs review it cannot get from a diff of
a file that was entirely rewritten.

> **Those are live numbers, and the ones they replace were not.** An earlier
> count said 136 v0 feedstocks, 79 noarch and 57 compiled, taken from the
> maintainer's local checkouts. Those checkouts are stale in a way that skews
> the split rather than merely aging it: of four v0 recipes picked out of them,
> **three had already been migrated by hand upstream**, and the compiled share
> they report is half again what the fleet actually carries. Anything sizing
> this phase reads `needs-migration` from an audit, which asks GitHub.

**What CRM does with those 148, run rather than assumed:** 142 convert into a
recipe swage can read back, and 6 are refused before conversion starts — five
because the recipe declares one key twice under different selectors, which v0
permits because selectors are comments, and one because it opens a Jinja
`{% if %}` block, which selects whole sections rather than one line and has no
`if:`/`then:` entry it maps onto. Only one of the six is noarch. So the noarch
group really is mechanical — 104 of 105 — and the compiled group is where the
refusals are, 4 of 41, exactly as the split above predicts.

**CRM's own severities are not a usable axis for what a reviewer reads.**
Everything short of an outright failure is filed as a warning, and that bucket
runs from "a v0 field went away" to "this dependency's version has been
changed". The second is `six 1.11.0` becoming `six 1.11.0.*` — a different
requirement, and the single thing in a conversion most worth another pair of
eyes. The first is 457 of the 558 messages the converter produces, dominated by
one that fires on any line holding a template and means only that CRM left the
line exactly as written, which is what swage wants. swage therefore names the
benign classes and treats everything else as something to read, rather than the
other way round: an unrecognized message reaches a person, the same direction
every other allowlist in swage points.

#### 7.0.1 What became of each condition — the review a diff cannot give

The 37 compiled recipes CRM does convert do not need a second code path. What
they need is a report, and the reason is the one that made this a phase of its
own: **the whole file is rewritten, so the diff says only that everything
changed**, and on a compiled recipe the thing being changed is the conditions.
So swage reads the converted recipe back against the `meta.yaml` it came from
and answers, for each condition the v0 recipe stated, what became of it — an
`if:`/`then:` entry, a `build.skip` clause, a value folded into `${{ … }}`, or
nothing at all.

**Two of those answers mean the recipe is wrong, and CRM reports neither as an
error.**

- **A condition that landed nowhere.** `igraph` puts an `arm64` selector on an
  entry of `build.script_env`; the converter emits `script: {}` and the
  environment variable is gone. `aiohttp` conditions four `{% set %}`
  statements that assemble a test-skip list, and loses all four — v1 has no
  `{% set %}`. CRM says something in both cases, and in both cases what it says
  is that a selector needs rewriting, which reads as a nuance rather than as a
  deletion.
- **A value the converter truncated.** Folding a condition into a scalar, CRM
  strips three characters from each end of it, meaning to unwrap a value that
  is *wholly* a `{{ … }}` expression. Where the value merely starts with one,
  `{{ PYTHON }} -m pip install . -vv --no-deps --no-build-isolation` comes out
  as `${{ PYTHON }} … --no-build-isolati if unix else '' }}` — valid YAML, read
  back by swage's own reader without complaint, and a different build command.
  `fiona` and `backports-datetime-fromisoformat` both ship it.

Detected on the artifact rather than by predicting which values CRM will
mangle: a line that opens a `${{` it never closes. That oracle goes on holding
when the converter's next release moves the bug, and over the 182 real v1
recipes in the maintainer's checkouts it fires on nothing.

**All four of those feedstocks are compiled, and that is not a coincidence.**
Conditioning a *scalar* is a compiled-recipe idiom — `build.script`,
`build.script_env`, a `{% set %}` above the recipe — and it is the shape CRM
handles worst. A noarch recipe conditions list *members*, which is the case
CEP-13 has an `if:`/`then:` entry for and which converts faithfully. Of the 142
recipes that convert, the review is quiet on 138.

> **A review that cries wolf is worse than no review**, and this one did on its
> first run over the fleet: it flagged eleven feedstocks, of which seven
> convert perfectly. A `build.skip` holds one boolean expression rather than a
> list of entries, so several conditions reach it joined together — and a
> condition can be a compound expression in its own right, `# [win and vc<14]`
> converting to `skip: win and vc<14` whole. Splitting the skip on `and`/`or`
> before looking inside it, which is the obvious way to stop `win` matching
> `not win`, reports every one of those as vanished. The rule that works is to
> search the clause whole and judge each hit by what precedes it.

Damage goes at the head of the concerns, ahead of anything CRM said, because it
is the only thing in a conversion report that means the recipe is wrong rather
than worth a look. It does **not** refuse the conversion: a migration is
reviewed by a person whatever its gates say (§7's ceiling), and a converted
recipe with one known-bad line in it, named and quoted, is a better starting
point for that person than no conversion at all. The ledger and the damage both
go into the conversion commit's message as well as the terminal, because the
reviewer is reading this on GitHub rather than in a shell.

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
reconciliation, which is the part that actually needs judgment, would be
invisible inside it. Split, the second commit is reviewable on its own, which is
the same argument this project applies to its own history (`CLAUDE.md`) pointed at
the feedstock instead.

**The conversion is verified before anything is reconciled against it.** After
CRM produces `recipe.yaml`, swage re-reads it with its own reader (§3.1). If that
fails — a construct CRM emits and swage refuses, a requirements list swage cannot
splice — the feedstock stops with the conversion unpushed. swage does not plan
against a recipe it cannot itself round-trip.

> **This is not a formality, and the message table would not have caught it.**
> `apache-airflow-providers-common-sql` ends one output's `run` list with a
> whole-line comment. CRM re-emits that comment ahead of the *next* output and
> drops the `-` that opens it, so the second output's keys land in the first
> output's mapping and `package` is declared twice. The file is not valid YAML
> and CRM reports no error of any kind. Nothing in the fleet's current 148
> reproduces it — that feedstock has since been migrated by hand — which is
> the argument for keeping a copy of it in the corpus rather than for dropping
> the check.

**Never automerged**, by §7's ceiling rather than by the gates. G5 in
particular is meaningless here: the diff touches everything. The gates are still
*evaluated and reported*, because the maintainer reviewing the conversion should
also see what swage thought of the dependencies, but they gate nothing on this
path.

**A migration is never the no-changes path either**, and this is the trap in
reusing the update machinery for it. Path B asks whether the recipe swage
planned against already says what swage would write, and skips the push when
it does (§5.2). On a migration the recipe it planned against is the *converted*
one, which exists nowhere yet — so a conversion needing no dependency edits at
all looks exactly like "nothing to do", and is in fact the case with the most
to push and the least to argue about. Whether there is a conversion to push is
asked first, before the comparison is reached.

The volume control is the one `update` already has: it is dry-run by default, so
`swage update --family airflow-providers --migrate` reports how many feedstocks
it would convert and stops. Turning that into ninety pull requests takes
`--execute` and is a deliberate act.

---

## 8. Commands

```
swage scan     [--family F | --feedstock N... | --all]   read-only; what would change
swage update   [--family F | --feedstock N...]        render, push, label
               [--migrate]                            ... converting v0 first (§7.1)
swage status   [--since 7d]                           read-only; what became of prior runs
swage audit    [--family F | --feedstock N... | --all]  read-only; the fleet's readiness
swage migrate  <feedstock>                            v0 -> v1
swage explain  <feedstock>                            why did it decide that?
swage draft    <feedstock> [--apply] | --family F    assemble a config decision
swage completion  bash | zsh | --refresh              a completion script for your shell
```

- **`scan`** is the default gesture and touches nothing. It reports the plan and
  the trust verdict per feedstock. `update` is `scan` plus writes; it is
  dry-run by default and requires `--execute` to push.

  **A selector is required** — one of `--feedstock`, `--family`, `--all`. A
  bare `swage scan` would sweep every feedstock the maintainer has, which is a
  real operation against GitHub rather than something to type by accident. A
  family that names nothing is refused rather than scanned, because selecting
  zero feedstocks and reporting a clean run over them is the most misleading
  answer available.

  **`--feedstock` takes any number of names**, either after one flag or by
  repeating it, and the run covers all of them in the order given. It took a
  single value until argparse's silent last-one-wins was noticed the worst way
  round: `swage update --feedstock a --feedstock b --execute` pushed to `b`,
  printed a header naming only `b`, and said nothing whatever about `a`. A
  selector that quietly covers less than it was asked to is the same class of
  error as a family that names nothing — which is why the header prints every
  name back, and is checked to.

  **Its outcome vocabulary is `update`'s, and only the wording differs.** An
  outcome is a statement about the gates rather than about what was written, so
  `merge-ready` means the same thing whichever command produced it and two
  `run.json` stay comparable. What a read-only run changes is the sentence
  beside the bucket: "would push + label automerge" rather than "pushed +
  labeled automerge", because the second describes something `scan` is
  structurally incapable of.

  **Path A and path B are told apart by rendering, not by guessing.** `scan`
  renders the plan back into the recipe and compares bytes — which is exactly
  G7's claim — and reports "no changes needed" only where that holds. Saying so
  matters because it is the one thing a reader cannot infer from the bucket:
  with no commit to push there is no CI run, so conda-forge's automerge can
  never be dispatched for that pull request (§2.1).

  Measured over the real fleet: 487 feedstocks in about four minutes, of which
  476 had no open bot pull request at all.
- **`status`** closes the loop, and is read-only. It reads swage's own runs
  inside a window, takes every pull request they pushed to or left waiting, and
  answers: which merged, which were closed unmerged, which are still running
  CI, which had CI *fail*, and which need a person. This is the report you read
  the morning after.

  **It follows the pull request, not the feedstock.** A superseded pull request
  and the one that superseded it are both real, and only one of them is the one
  swage pushed to, so "did my commit land" is asked by number. `scan` is the
  command whose subject is a feedstock.

  **A pull request still open is re-planned rather than remembered.** Saying a
  pull request is ready is a claim about the recipe *now*: between the two runs
  it may have gained a commit, or `config/` may have gained the file that
  settles what held it. It goes back through the same path `scan` uses, so
  there is no second implementation of a verdict to keep in step, and the
  buckets that describe a write go subjunctive because `status` writes nothing.

  This is what makes `AWAITING CI` load-bearing. Path B's precondition — CI
  already finished — is often not yet true when `update` runs on a freshly
  opened bot pull request. `update` catches the ones that are ready; `status`,
  run later or from cron, catches the rest and reports them `READY TO MERGE`
  with a link, for the person who presses the button (§5.2.2).

  > **It does not re-arm a `DEGRADED` pull request, and an earlier draft of
  > this section said it would.** That is worth keeping because the reasoning
  > is not obvious. `DEGRADED` means swage's commit landed and the labeling
  > call did not, so conda-forge's own path B is broken for that pull request
  > and the label is what would restore it. But conda-forge dispatches
  > automerge from CI status events (§2.1), so a label only does anything while
  > a CI run is still to report — and CI on the commit swage pushed has long
  > finished by the time a report anybody reads the morning after runs. The
  > window in which re-arming works is the minutes between a failed label call
  > and the end of CI, which is not when this command runs.
  >
  > What the re-arm was for is covered without writing anything. A pull request
  > swage pushed to, whose CI has since gone green, needs no change and is
  > mergeable — which is exactly `READY TO MERGE`. So the case re-arming could
  > not have helped is the case the report already handles, and the cost of
  > dropping it is one click on a rare failure rather than none.
- **`audit`** is read-only, and it is the only command whose subject is a
  feedstock rather than a pull request — which makes it the only one that sees
  the fleet at all. See §8.2.
- **`explain`** dumps the full provenance chain for one feedstock: upstream
  metadata fetched, config layers applied, each name resolution and its source,
  each gate and its verdict. Debugging a quirks database without this is
  miserable, and the two existing tools have taught us that the "why did it do
  that?" question comes up constantly.
- **`draft`** is `explain` for a decision that has not been made yet. See §8.1.
- **`completion`** prints a completion script for bash or zsh, and is the only
  command whose subject is the command line itself. See §8.3.

### 8.1 `swage draft` — assemble what a config decision needs

Every gate that stops a feedstock hands the maintainer a question, and answering
it means having three things open at once: what upstream actually declares, what
the recipe actually says, and somewhere to write the answer down. Today that is
a manual archaeology exercise per feedstock — find the sdist, extract it, find
the right metadata file, diff it against a recipe on a branch somewhere. The
gates are good at asking; nothing is good at answering.

```
swage draft <feedstock>          # write the workbench
swage draft <feedstock> --apply  # copy the draft into the config tree
swage draft --family <name>      # write one per feedstock, and what they share
```

**It reads the newest open bot pull request where there is one, and the default
branch where there is not.** Every other command is driven by a pull request,
because a pull request is what there is to act on. `draft` is not: a feedstock
with no open bot pull request is the ordinary case — 476 of 487 in the last
sweep — and it is exactly the case where somebody sits down to write a config
for the first time. Keying this command on one too would leave it unavailable
for most of the work it exists to do. Where there is one it is still the right
ref, because it carries the version bump whose reconciliation raised the
question.

The consequence is `plan_at`'s: with no pull request there is no previous
version, so no removal can be classified and every line the recipe has is kept
(§3.3.7). A workbench built from the default branch can therefore show lines
being added or changed and never one being dropped — the safe direction, and
worth knowing while reading the diff.

The workbench is a directory, and it is read-only against everything but itself:

```
~/.cache/swage/drafts/<feedstock>/
  recipe.yaml            the feedstock's, as it is
  recipe.swage.yaml      what swage would write
  recipe.diff            the two, unified
  upstream/pyproject.toml    or PKG-INFO / METADATA, named as swage found it
  FINDINGS.md            each thing a check found, the line, the remedy, and
                         every mention of the disputed name in the metadata
  config.yaml            the draft
```

**Nothing here is new work except the upstream file and the draft.** `scan`
already renders both recipes and writes them (§9), and `run.json` already holds
every verdict, every line's provenance and every remedy. What is missing is the
artifact itself: the metadata is fetched, parsed and dropped, since
`UpstreamMetadata` keeps only the parsed result. `draft` re-fetches for the one
feedstock rather than retaining the raw text on the model — carrying ~50 KB of
it through a 487-feedstock sweep to serve an interactive command is the wrong
trade, and this is the only caller that wants it.

**Quoting the metadata back is most of the value.** The remedy swage prints
says what the *options* are; what decides between them is what upstream says
about that name, and a maintainer should not have to go and look. Both halves
of a real example, from the first six feedstocks this was done by hand for:

```
### `setuptools`  —  in `google-cloud-bigquery`'s `host` requirements, kind: nowhere
Every mention of `setuptools` in `pyproject.toml`:
    requires = ["setuptools"]
```

That one was not a decision at all. It was a swage defect (§3.6.2), and it took
seconds to see with the file open beside the finding and considerably longer
without.

> **One bullet per finding, not per check.** A check joins what it found with
> `; ` for the single line the terminal report and `run.json` want (§5.4), and
> the workbench listed that joined string. `esmf` holds on thirteen lines swage
> cannot account for, so the heading that promises what is holding the
> feedstock was followed by one unbroken line of them — eleven restating the
> same forty-word remedy, and no two separable from each other by eye. The findings are kept apart
> as well as joined, and this file lists them apart. What to do about the whole
> set is said once, under *Where to write it down*, which names the key that
> answers the check and the shape the answer takes.

> **The tool must not pre-fill `add_requirements` for an unexplained line.**
> That is the answer that is wrong for the whole temporary-constraint class
> (§3.3.7), and the class is not small — five of the first eight findings in
> the fleet were in it. A skeleton offering it as the obvious next step is a
> machine nudging the maintainer toward the harmful choice. `FINDINGS.md`
> presents all three answers; `config.yaml` drafts only what swage can derive
> without judgment, which is `extras_as_outputs.supported` read off the
> recipe's own output names, the `skip` candidates, and the file header.

#### 8.1.1 `--family` — the questions a family shares

Drafting one feedstock makes one decision cheap. What the first fleet-wide
`audit` (§8.2) showed is that the decisions are not one per feedstock: **174
held feedstocks ask 8 kinds of question between them**, and inside a single
family it is usually one or two. So a maintainer facing 49 workbenches is
facing a decision they can take once, in one file — and a command that only
ran `draft` in a loop would leave them to work that out for themselves.

`--family` writes a workbench per feedstock and a `SUMMARY.md` above them:

```
~/.cache/swage/drafts/families/<family>/
  SUMMARY.md            the questions, who asks each, and where to answer
  <feedstock>/          the workbench of §8.1, unchanged
```

Two failures are the same question when they come from the same gate and their
wording matches once names, versions and punctuation are taken out. The
concrete wordings are kept and printed underneath, because whether a question
is about one name or forty is exactly what decides where it gets answered.

> **Punctuation is part of that and it is not tidying.** A detail listing two
> names keeps the comma between them once the names are removed, so "upstream
> computed `requires-dist`" and "upstream computed `provides-extra`,
> `requires-dist`" grouped apart — the same gate asking the same thing. The
> first real family draft reported its 49 google-cloud feedstocks as 41 and 8
> that way, which is precisely the arithmetic this summary exists to stop
> somebody doing in their head.

**The summary says where an answer belongs, never what it is.** Which file a
shared answer goes in is a fact about how config resolves; what to write in it
is the decision, and §8.1's whole refusal is that a machine must not propose
one. So a question asked by 49 feedstocks prints "or once for all 49 in
`config/families/<family>.yaml`" and stops there.

**`--apply` is refused with `--family`, and that is the point rather than a
gap.** The per-feedstock draft holds only what swage can derive without
judgment, and writing fifty of them into `config/` at once would put fifty
files in front of a reviewer that nobody has decided anything about — while the
summary's usual finding is that one family file answers them all. Applying
stays a per-feedstock gesture, taken once a decision exists.

Measured on the real family: 50 feedstocks drafted in under two minutes,
**2 questions between them** — 49 asking one, 13 asking the other.

**Persistence is git, and there is no copy-back protocol.** `--apply` writes
`<config-root>/feedstocks/<feedstock>.yaml`, which in the maintainer's checkout
is an ordinary modified file to read and commit. Inventing a sync mechanism
between a cache directory and the repository would create a second source of
truth for the one thing in swage that is *only* meaningful as reviewed history.
Where the file already exists, `--apply` refuses and writes `.yaml.draft`
beside it instead: a config file is hand-written prose as much as data, and
overwriting one to save a diff is a bad trade.

### 8.2 `swage audit` — what the fleet would do if the bot filed tomorrow

**Its subject is the feedstock, not a pull request, and that is the whole
reason it exists.** Every other command is driven by an open bot pull request,
because that is what there is to act on. The consequence is that swage has
never looked at most of what it maintains: a `scan --all` over 487 feedstocks
reports 479 of them `UNCHANGED — no open bot PR` and never opens their recipe.
Eight were planned. So the three questions a maintainer actually has — how much
of this fleet is automated, which feedstocks need a decision from me, and how
many are still v0 — have no answer today, and the tool that could answer them
is looking the other way.

Audit reads each feedstock's default branch and plans it there. Almost nothing
about that is new: `plan_at` is keyed on a ref rather than a pull request
precisely so a rendering can be produced without one (§10), and
`scripts/compare_published.py` already sweeps 149 feedstocks that way. Audit is
that sweep, fleet-wide, with the gates evaluated and the result recorded as a
run like any other.

**The question it asks is readiness: if the bot filed a pull request for this
feedstock tomorrow, what would swage do with it?** That is answerable now, and
answering it early is the point. The config decision that would hold a pull
request can be made *before* the pull request exists — which is the difference
between a maintainer meeting the backlog one bot pull request at a time and
working through it deliberately. This is the sense in which §10 sequences
`draft` before `audit`: audit produces the list, and draft is what makes each
item on it cheap to answer.

#### It adds no outcome to §9's vocabulary

Deliberate rather than economical. An outcome is a statement about the gates
rather than about what was written (§8), so a feedstock audit holds is one a
later `scan` must also hold — and two vocabularies would be two things to keep
in step, and would make an audit and a scan of the same feedstock
incomparable. Only the sentence beside each bucket moves:

- `MERGE-READY` — the gates pass. A bot pull request would be pushed and
  labeled with no human in it.
- `PROPOSED` — the gates pass except the trust ladder, which is where every
  feedstock starts.
- `NEEDS REVIEW` — a gate holds it, and its detail names the question.
  **This is the backlog `draft` exists to answer**, and `swage draft
  <feedstock>` is the next command to type.
- `UNCHANGED` — swage would change nothing; the recipe already matches the
  release it names.
- `NEEDS MIGRATION` — v0, so nothing above applies until §7 converts it.
- `FAILED` — a precondition stops swage here (§3.3.5, §3.6), or the recipe
  could not be read.

**`READY TO MERGE` and `AWAITING CI` never appear.** Both are statements about
a pull request's CI, and there is no pull request.

#### What needs no plan at all, and is invisible to everything else

These cost one pull-request listing per feedstock, need no recipe and no
upstream fetch, and no other command will ever report them — because every
other command is looking at a pull request it intends to act on, and each of
these is about one nobody is going to act on:

- **An `automerge` label on a pull request whose CI has finished.** It will
  never merge, because nothing will dispatch automerge for it again (§2.1), and
  it looks exactly like a pull request that is about to merge. Nothing in swage
  reports this today.
- **A feedstock at the bot's backlog cap of four**, where conda-forge's bot has
  stopped filing and no further version will be offered until somebody clears
  it (§3.4.1). The difference between "three superseded pull requests" and
  "this feedstock has stopped receiving updates".
- **An archived feedstock with an open bot pull request** — four of the
  maintainer's, one of them wearing an `automerge` label it will never act on.
- **A config file naming a feedstock the maintainer no longer maintains**,
  which is the quirks database going stale in the direction nobody looks.

These are notes rather than verdicts (§4). They do not move a feedstock between
buckets, because they are facts about a repository rather than about a plan.

#### What it costs, and what that decides

Planning a feedstock means fetching and hashing its sdist, so an audit costs
about what `compare_published.py` costs. Measured: **487 feedstocks in 17
minutes**, which is well under the hour or two estimated from that script and
is explained by the archive cache below — a warm family of 50 runs in 90
seconds against 2 minutes cold. Three consequences, and they are the parts of
this phase that were not already built:

- **A selector is required**, exactly as for `scan`. This section's synopsis
  used to write `[--all]` as though it were optional; a bare `swage audit`
  would be an unintended sweep an order of magnitude slower than the one
  `scan`'s rule already exists to prevent.
- **Archives have to be cached.** `download` fetches over HTTP and keeps
  nothing, which is right for a command that plans eight feedstocks and wrong
  for one that plans 490 — a second audit should pay for the recipes that
  changed and nothing else. The cache root already exists, for the
  name-resolution layers that are 24 MB between them.
- **The default branch has to be read rather than assumed.** Nothing in swage
  reads one today, and `compare_published.py` hardcodes `main` — fine for two
  families curated by hand, and not for 490 feedstocks.

#### What it does not do

- **It writes nothing** — not to a feedstock, and not to `config/`. Audit
  produces the list; `swage draft <feedstock> --apply` writes a config file,
  one feedstock at a time and deliberately (§8.1). An audit that filled in the
  quirks database would be the failure §4 guards against, at fleet scale: a
  hundred entries that silence gates and explain nothing.
- **It is not a second implementation.** Same `plan_at`, same `evaluate_gates`,
  same outcomes, and a run recorded in the same `run.json` — so `swage explain
  <feedstock>` out of an audit run answers "why is this one not ready" in the
  same terms a scan would.

### 8.3 `swage completion` — the commands, and the names, on the TAB key

```
swage completion bash > ~/.local/share/bash-completion/completions/swage
swage completion zsh  > ~/.zfunc/_swage
swage completion --refresh
```

**The names are the point.** Commands and options are a handful of short words
a maintainer learns in a week; the ~490 feedstocks are not, and they are the
argument every command takes. `apache-airflow-providers-common-compat` is
thirty-eight characters and differs from `apache-airflow-providers-common-sql`
only at the end, and a typo in `--feedstock` is not refused — a feedstock swage
is pointed at directly is one somebody has a reason to look at (§8), so a
mistyped name is a run against a repository that does not exist. Completing
options and commands too is what makes the script feel like every other one on
the system, and costs a dozen lines of generation.

Three decisions are worth recording.

**The script does the completing, and swage is not run at all.** `gh` and `pip`
call the tool back on each TAB and have it print candidates; swage cannot
afford it, because importing the CLI costs about a third of a second before
argparse sees a word, and a completion that pauses that long gets turned off.
So the commands and their options are baked into the script when it is
generated, and the names are read from a file by the shell. The cost is that
the script is a snapshot: an option added later does not complete until the
script is regenerated, which the script's own header says.

**Names come from a cache, because the authoritative answer is a network
call.** Which feedstocks are yours is one paginated read over ~490 teams
(§3.4) — fine once a run, impossible on a keystroke. So any run that discovers
writes what it found under the cache root, and the shell reads that file;
`--refresh` does the same on demand, for a maintainer who only ever names
feedstocks explicitly and so never causes a discovery. Family names come from
the quirks database and are written whenever it loads, which every command
does. A missing file completes nothing, exactly as a cache swage cannot write
means a slower swage rather than a broken one (§3.2).

**What the script knows about swage is generated from the parser**, not written
out beside it. A hand-kept list goes stale in the way nobody notices: a
completion offering a flag that was renamed looks like a broken shell rather
than an old script.

It does not install itself. Writing into a maintainer's dotfiles or their
completion directory is a change to their machine that swage has no business
making silently, and the redirect is one line they can read.

---

## 9. Output

Primary interface is the terminal, modeled on the airflow tool's ranked,
colorized summary — which is genuinely good and worth keeping — grouped by
outcome so the actionable items are unmissable:

```
swage update --family google-cloud            2026-08-11 14:02      (312 scanned)

  READY TO MERGE (28)  nothing to change and CI is green -- merge these yourself
    google-ads                   CI passed: linter, github-actions
                                 https://github.com/conda-forge/google-ads-feedstock/pull/55
  MERGE-READY (41)     pushed + labeled automerge; conda-forge merges it on green CI
  AWAITING CI (13)     no changes needed; CI still running -- `swage status` later
  PROPOSED (12)        pushed, needs your review before labeling
  NEEDS REVIEW (9)
    google-cloud-aiplatform      upstream extra 'evaluation' is in neither
                                 supported nor skip
    google-cloud-bigquery        no conda-forge package found for 'db-dtypes'
    google-cloud-dataproc        would remove 'grpcio-status', gone in 2.28.0
    google-cloud-kms             'grpcio-gcp' is in the recipe and in no upstream
                                 version -- drop it, or declare it in
                                 add_requirements
    google-cloud-storage         run_constraints 'protobuf' is tied to no
                                 upstream extra -- proofread
  DEGRADED (1)                   pushed but NOT labeled -- merge it yourself
    google-cloud-spanner         pushed 1f0cafe, but labeling failed: HTTP 403
  MIGRATED (3)         v0 -> v1 converted and updated -- review both commits
  NEEDS MIGRATION (18) v0 meta.yaml -- rerun with `--migrate` to convert in place
  UNCHANGED (206)      no open bot PR
  FAILED (2)
    markupsafe                   unsupported conditional noarch in /build/noarch

  run: ~/.cache/swage/runs/2026-08-11T14-02/
```

`AWAITING CI` is the bucket that makes `swage status` load-bearing rather than
cosmetic: those PRs need nothing from you, and nothing will move them either
until swage looks again and tells you which of them are ready to merge.

**`READY TO MERGE` is the bucket swage cannot empty**, and the only one whose
whole content is an instruction to the reader. The recipe needs no change,
every check has passed, and the pull request merges cleanly — and swage may
not merge it (§5.2). So unlike every other bucket it names each feedstock even
though nothing is wrong with any of them, and prints the pull request's URL
under each: a count says something is waiting, and the name and the link are
what get somebody there. The same link is printed under `PROPOSED`, `DEGRADED`
and `NEEDS REVIEW`, which are the other buckets whose content is "go and look
at this on GitHub".

**The line beside a feedstock names the thing you act on**, and the bucket
decides what that is. A held feedstock is named for the check that holds it;
one whose recipe already matches its release is named for what CI said; and
one swage would push says how many lines would change, which is what says
which to open first. It is never the trust rung: every feedstock in `PROPOSED`
is unlabeled for the same reason, the heading has already given it, and
printing it again gave thirty consecutive lines reading "not approved for
automatic merging (trust: propose)". The single exception is `trust: never`,
where the rung is not one reason among several but the whole account of a run
that wrote nothing.

Each run also writes a directory containing a structured `run.json` (the full
plan, provenance, and verdicts) plus per-feedstock recipe diffs. That directory
is disposable — everything durable lives in git.

`run.json` being a stable, documented schema costs nothing now and is the only
thing a future web dashboard would need. **No web UI is planned**; the door is
left open rather than walked through.

### 9.1 Unattended-safe by construction

Scheduling is not built, but every command is designed to be safe to run
unsupervised, because `swage status` genuinely wants to run on a timer — it is
what catches the Path B pull requests whose CI finished after the `update` run
and reports them ready (§5.2).
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

Three consequences follow from the input being a directory on disk:

- **It reads a run directory and nothing else** — no config, no network, no
  recipe. An unrelated typo in the quirks database must not stand between
  someone and the record of what swage already did.
- **The most recent run is chosen by name, not by mtime.** The name *is* the
  timestamp, and it is the one that cannot move: copying a run directory about
  or restoring one from a backup would reorder mtimes while leaving "which run
  happened last" with the same answer. A directory with no `run.json` in it is
  skipped, since a run that died part way through leaves one behind.
- **The exit code is the one the run gave that feedstock**, so asking about one
  that needs review says so the same way the sweep did.

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

PLAN  `google-cloud-bigquery-with-pandas`'s `run` requirements
   keep   python >=${{ python_min }}         recipe-kept       recipe_owned.names
   keep   google-api-core-grpc >=2.28.0      upstream-core     identity
  ~bump   google-auth >=2.14.1 -> >=2.15.0   upstream-core     identity
   +add   proto-plus >=1.26.1                upstream-extra    extra:bigquery_v2
   -drop  grpcio-status >=1.33.2             upstream-dropped  absent in 3.44.0

CHECKS
  pass  every requirement is accounted for
  pass  every name resolves to a conda-forge package
  n/a   every upstream extra is listed as supported or skipped
        feedstock declares no skip list
  pass  no output has lost the upstream extra it is built from
  pass  only requirements changed
  pass  this feedstock is approved for automatic merging
  n/a   the recipe already says what swage would write
        swage changed the recipe, so conda-forge decides the merge
  FAIL  nothing upstream dropped is removed without review
        would remove 'grpcio-status', gone in 2.28.0
  FAIL  every run constraint is tied to an upstream extra
        run_constraints 'protobuf' is tied to no upstream extra

VERDICT  needs review   (2 checks failed)
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
  a STOPPED section with the reason — a v0 recipe (3.1), a conditional `noarch`
  (3.3.5), contradictory constraints (3.3.2). An empty plan would be the least
  helpful possible answer to "what happened".

---

## 10. Delivery plan

Each phase is independently useful and ends in something runnable.

**Phase 0 — skeleton. Done.** `pyproject.toml` + hatchling, `src/` layout, ruff,
mypy strict, pytest + coverage, GitHub Actions on Linux/macOS/Windows, mkdocs.
`pixi.toml` for the dev environment, matching existing practice. Config schema
and loader; no behavior. *Ended with:* `swage --help` and a validated config tree.

**Phase 1 — read-only `scan`. Done.** Upstream fetchers, mapping, recipe model,
planner, trust gates, terminal report, `scan` and `explain`. Nothing writes.
Began with the **round-trip spike** (§3.1), which decided the recipe model's
foundation before anything depended on it — that question is now settled in
favor of `ruamel.yaml`. *Ended with:* `swage scan --all` over 487 feedstocks in
about four minutes, and `swage explain` rendering any of them out of the run
artifact.

> **Every layer in this phase shipped with a bug its own tests did not catch
> and a run over real data did, usually within minutes.** The pattern is
> consistent enough to plan around: tests written alongside a layer encode the
> author's model of the problem, and the fleet is where that model meets
> something that disagrees. Two of the sharpest examples were found only by
> comparing against real artifacts rather than against expectations — the
> planner's comment rendering (§6), which no line-based golden test could see,
> and the name resolver having no data source at all (§3.2.1), which no test
> using a fixture index could notice. Phase 2 formalizes this; until then, the
> rule is that a layer is not done until it has been run over
> `~/code/conda-forge` and the output *categorized* rather than counted.

**Phase 2 — differential validation. Done.** Diff what swage would write
against what the two tools it replaces published, over every feedstock in both
families. This is the phase that earns the right to write anything.

> **The live head-to-head is worth almost nothing, and the reason is
> structural.** Both tools act only on an open bot pull request, and a
> feedstock that still has one is usually blocked on something — 12 of 487 have
> one and 3 of those are renderable, every one stuck. So running them samples
> the pathological tail and nothing else. Their *output* has no such problem:
> the `recipe.yaml` on each default branch is what one of them produced, so
> rendering that same ref with swage and diffing needs no pull request and no
> tool run, and reaches the healthy majority. That is
> `scripts/compare_published.py`, 149 feedstocks in five minutes, and it renders
> through the same `plan_at` the scan uses so that what it compares is what
> swage would push rather than a second implementation's opinion about it.

> **The first thing it found is that the two tools disagree with each other**,
> and that this document had recorded both answers without noticing. Clause
> order (§6) and the marker comment's wording (§3.3.1) each had one convention
> in the airflow family and a different one in google-cloud, so no
> implementation could reproduce both and the corpus could not have said so
> while it covered one family. The golden test now spans both — 19 recipes —
> and where a convention had to be chosen it is applied to the published text
> before comparing, so one decision is recorded once rather than exempting
> every file it touches from byte-comparison.
>
> A second finding is about the *shape* of this phase rather than its results.
> The google-cloud recipes needed their upstream metadata vendored before they
> could be planned at all, and ten of the eleven sdists ship no
> `pyproject.toml` — so the family that was hardest to add is also the only
> coverage the corpus has of §3.6.2's core-metadata path and §3.6.4's
> `default_build_requires`. Both were built against fixtures written for them.
> Coverage of a rule by a test written alongside it is not coverage by
> anything real.

**The phase ends where the comparison stops saying anything new.** Reading the
divergences one at a time — not counting them — is what the phase actually
consisted of, and it found seven defects, **two of them invisible to every
gate**:

- **One requirement rendered twice** wherever a recipe spelled a package the
  way upstream does and conda-forge publishes it as something else (§3.2.2).
  Invisible to every gate: both lines attribute to the same upstream
  declaration, so G1 and G2 were satisfied by the duplicate.
- **The wrong remedy** for the same shape where the two spellings do not
  normalize alike — `add_requirements` offered for a dependency upstream
  declares by name (§3.2.2).
- **A line swage could not explain, sorted as conda-forge structure** and so
  hoisted above every upstream line in its section (§6).
- **A note quoting a raw marker** where the marker was a window rather than a
  single comparison (§3.3.1).
- **A blank line rendered between a note and the line it describes**, because
  spacing was carried through as though it were a remark (§6.1).
- **A recipe's own `python` cap ignored**, so markers were evaluated over a
  range wider than the package is installed on (§3.3.3). On
  `google-cloud-pubsublite` that demanded a `grpcio` conda-forge does not have,
  which is exactly what the cap's own comment says the feedstock is waiting
  out.
- **A bound the recipe states and upstream does not, dropped** with every gate
  satisfied, because G1 justifies a line and never looks at its constraint.
  swage still drops it — a constraint that differs from upstream's is drift —
  but `constraints` and `temporary_constraints` are where a bound somebody
  means to keep is written down, and the second holds the feedstock until the
  workaround is re-checked (§3.3.14).

> **Two of the seven were found by a sweep written to size a bug, not by the
> comparison itself.** The duplicate showed up in the diff for one feedstock;
> asking "how many others" meant rendering all 149 and looking for two lines in
> one section with the same normalized name, which is a question no diff
> against a published recipe can answer. Phase 1's rule — a layer is not done
> until it has been run over the fleet — generalizes: so is a *bug fix*, and
> the sweep that sizes one is as cheap to write as the test that pins it.
>
> **And re-running the comparison afterwards found two more, both introduced by
> the fixes.** Keying preserved comments on the planned line rather than the
> recipe's spelling is right, and it carried a hand-written label 50 lines down
> the section on the one recipe where two lines collapse into one. Neither
> would have been visible in any other way, which is the argument for the
> comparison being cheap enough to run twice.

**What is left is understood rather than absent.** The comparison ends where it
began, at 64 feedstocks identical to the published recipe, 67 differing only by
conventions swage chose on purpose, 10 worth reading, 2 correctly refused and 6
out of scope as v0. Every one of the ten is now named: a feedstock with no
config yet, which G1 refuses; four where config records on purpose what swage
adds; two where conda-forge's own name for a package differs from upstream's
and a human decides which is meant; a grayskull leftover an unlisted extra
keeps out of `retire`'s reach; the two lines of clause canonicalization §6
chose; and the hand-applied pin that is now a `temporary_constraints` entry.

> **The harness was absorbing more than it was told to, and the correction
> moved three feedstocks.** Its marker-wording rule was written to recognize a
> *rewording* — one line removed, one added — and applied per line, so an
> addition with no matching removal was absorbed just as readily. That is
> precisely what a duplicated comment looks like in a diff: the old comment is
> untouched and never appears, and only swage's new one shows. Counting
> additions against removals puts the count at 62 identical, 66 deliberate and
> 13 worth reading, and every one of the three that moved is swage writing a
> marker note where the published recipe carries none — in a `host` section the
> replaced tools never annotated, or above a dependency gated `python <3.11`
> whose note they only ever wrote for `>=`. Behavior swage always had, hidden
> by the harness rather than by the code.

> **A category that stays the same size while its contents become explainable
> is the phase working, not the phase stalling.** The count was never the
> deliverable — a fixed defect leaves the feedstock in "worth reading" whenever
> it also diverges for a reason nobody disputes, and three of these do.

The gates say the same thing from the other side. Across both families the
bound check stopped **2** feedstocks and G1 stops 29 — 22 of them on an extra
no output lists, 5 on a dependency in no upstream version, and 2 on the rename
§3.2.2 describes. Those two are the pins now recorded as
`temporary_constraints`, which is where a check firing twice in 149 belongs:
in config, naming what it found.

**Phase 3 — `update` writes (Path A).** Clone, commit, push, label — with the
push-then-label unit and the DEGRADED path from §5.5 built in from the start, not
bolted on. Dry-run default, `--execute` to push.

**It has pushed to a feedstock. Done.** `alibabacloud-adb20211201` was
promoted to `trust: propose` in a config commit of its own — blessing is an
auditable record or it is nothing (§5.4) — and `swage update --execute` put a
commit on the bot's pull request there. The feedstock was picked for blast
radius rather than for interest: one maintainer, one output, and a two-line
reorder to match upstream's own declaration order.

Everything behaved as specified, which is worth recording in the same detail
as a failure would have been. The commit touched `recipe/recipe.yaml` alone at
+1/−1, authored by the maintainer and co-authored by swage. The `automerge`
label was **not** applied, and swage commented saying why. CI started on the
new head, which is the observable half of §2.1: the push is what dispatches
it. The run record kept `head` (what the plan was computed against) and
`pushed` (what swage created) apart, which is what `swage status` uses to tell
swage's commit from a later bot one. And the clone stayed on disk beside
`run.json`, so the tree that was pushed and the reasoning that produced it are
one directory.

> **A dry run has to be the same command, not a rehearsal of one.** The first
> run over the real families found `apache-airflow-providers-amazon` in NEEDS
> REVIEW where `--execute` would have put it in PROPOSED: the rule separating
> "held only by trust" from "a gate found something" lived in the write path,
> so a run that wrote nothing could not reach it. It belongs in the shared
> bucket rule, which also has to know the *trust level* rather than reading it
> off G6 — `propose` and `manual` fail that gate identically and mean opposite
> things, and PROPOSED asserts a push that only one of them gets.

> **Being the same command is exactly why the report has to say which one it
> is.** The two runs differ in what they wrote and in nothing else, so the only
> signal was the subjunctive bucket wording — which speaks for MERGE-READY and
> PROPOSED and says nothing about a feedstock held for review. That is the
> fleet's default state: running `swage update` on a `trust: propose` feedstock
> produced a report byte-identical to what `--execute` prints. So a run that
> wrote nothing says so above every bucket, and it is the one line in the
> report that is about the run rather than about a feedstock.

> **What the write path assumes, asked of the fleet.** Four things fail
> silently and everywhere if they are wrong: a pull request having a head
> repository to push to, `maintainer_can_modify`, the feedstock defining the
> `automerge` label at all, and the SHA the listing reports being the branch
> tip that gets cloned. Across all 39 open bot pull requests: no exceptions.
> Four already carry `automerge`, which makes §2.2's remove-then-re-add the
> common path rather than a corner case.

> **The parts a dry run cannot reach were rehearsed against a real feedstock**
> — a real `gh repo clone` of the bot's fork and a real commit, with the push
> intercepted and never run. It is the same trick the fleet comparison plays:
> get the real inputs in front of the real code and read the result. It found
> a commit body 95 columns wide, which no unit test using a package called
> `demo` was ever going to show.

> **The live run found nothing, and that is not the same as proving nothing.**
> Every defect this phase turned up came from one of the three sweeps above,
> and the push itself only confirmed what they predicted. That is the argument
> for doing all three *before* writing rather than trusting one live run to
> stand in for them: a single feedstock exercises one shape, and the sweeps
> exercised the fleet.

The next `trust: propose` feedstock is `grpcio-status`, which passes every
check it is asked.

**Phase 3.6 — the python test matrix (§3.7). Done.** The second splice region,
the `test_matrix` policy, and G12. Numbered after 3.5 and sequenced *before*
it: the lint exists now and merging can wait, but a phase number should keep
meaning what it meant in the commits that already reference it, so nothing is
renumbered to say so.

> **Checked against conda-smithy, not against the tests.** Reimplementing
> `_python_tests_cover_latest` and running it and swage over every recipe on
> disk: 186 agree exactly, 0 disagree, and the one gap is the missing-key case
> swage declines on purpose. Unit tests could only ever have proved that swage
> does what the author thought the rule was, which on this rule is precisely
> the thing that was in doubt — the hint text and the enforcing code disagree
> about half the population.

> **The comparison harness needed teaching before it could be read.** It
> reported 24 feedstocks worth reading where it had reported 10, because a
> convention it does not recognize is indistinguishable from a defect. Naming
> the matrix diff put it back to 10, and the two feedstocks that left
> "identical" are the ones whose only remaining difference is now this. A
> harness that cries wolf about a signed-off decision is one nobody reads
> carefully, which is the same failure as a harness that is too permissive.

**Phases 3.7 and 3.8 — recipes that are not one noarch wheel (§3.1,
§3.3.1.1).** The scope correction, in two steps, sequenced *before* 3.5 because 3.5 merges pull
requests unattended and should not be built on a planner that cannot tell what
kind of package it is looking at.

- **3.7 — the reader. Done.** A requirements section is a list of entries
  rather than of lines: conditionals read as structure, rendered from their own
  layout, round-tripped exactly. All nine compiled corpus entries read, and the
  reader and round-trip tests cover the whole corpus again rather than skipping
  the part of it swage could not parse.

  > **What the sweep over all 319 v1 recipes on disk found.** 313 read; 5 are
  > refused for the two reasons that have nothing to do with conditionals — an
  > inline comment on a requirement (`libpnetcdf`) and a quoted requirement
  > carrying Jinja (a `gdal` conversion branch) — and one is v0 under a v1
  > filename. Exactly one recipe does not round-trip byte-for-byte:
  > `pendulum`'s conversion branch writes `then: ` with a trailing space, which
  > swage renders as `then:`. That is a one-character normalization inside a
  > block swage owns, and it is the whole of the difference.
  >
  > 118 of the 313 read and then stop at the planner, which is the state 3.8
  > exists to end: swage can now see these recipes correctly and still declines
  > to reconcile them, because doing so with the rules written for one noarch
  > artifact would be a silently wrong answer rather than a parse error.
- **3.8 — the per-output build model. Done.** A plan is made per output against
  the build model that output declares: `python_min` asked for only where an
  output needs one (§3.3.3), markers translated into conditions on the python
  and the platform an artifact is built for (§3.3.1.1, §3.3.4), a section
  planned rather than refused when it already holds conditional entries, and
  upstream's `requires-python` compared against the build floor with the config
  key retired (§4.1). The two stops the planner carried while the reader
  understood more of the format than it did are gone.

  > **What the sweep over the 374 v1 recipes on disk found.** 347 plan. 26 are
  > refused by the reader for reasons that have nothing to do with the build
  > model — 19 of them one staged-recipes example template, checked out on many
  > branches, whose requirement carries an inline comment — and one is v0 under
  > a v1 filename. Nothing crashes and no conditional entry is lost.
  >
  > It found one defect, which is why it is run: eight recipes came out with
  > their conditional entries reordered, because a conditional swage preserves
  > inherits the provenance of whatever is inside it and an unexplained
  > `recipe-kept` line sorts alphabetically into the trailing block. Structure
  > swage did not author now keeps the place it had.

  §3.3.6.1's rule is still open, and its interim answer landed as a gate: a
  plan that changes the `host` of an output with a cross-compilation block is
  pushed and held for review rather than merged unattended.

> **Both steps are spine changes, and that is the reason to take them now
> rather than after another feature.** Two model facts are currently hardcoded
> as universal: that a requirement is an unconditional line, and that
> `python_min` is a feedstock-wide value. Everything downstream — ordering,
> attribution, rendering, the gates — is written against them. Changing them
> once, deliberately, costs less than three later features each carrying a
> special case for the recipes they could not read.

**Phase 3.5 — merge (Path B).** The CI-verification logic and direct merge from
§5.2, deliberately sequenced *after* pushing is proven in practice. Ships in two
steps: first report-only (`WOULD MERGE`, listing the checks it verified), so the
verification logic can be read against real pull requests before anything acts
on it; then the merge itself. This is the one place swage takes an irreversible
action nobody reviews, so it earns the extra caution.

> **The audit the two steps were meant to give is smaller than it looked, and
> that is a fact about the trust ladder rather than about the checks.** Only a
> `trust: auto` feedstock reaches the merge check at all, and two feedstocks
> are blessed. What actually exercised the logic was running it against all 22
> open bot pull requests in the fleet and reading every verdict — which needs
> no blessing, no writes and no phase gate, and which is what the notes below
> record.

**Step one — the merge check, report-only. Done.** Every no-change pull request
that clears the gates gets its CI enumerated, its required providers worked out
the way conda-forge works them out (§5.2.1), and the result recorded whole in
`run.json`. Nothing merged: the bucket was `WOULD MERGE` and swage wrote
nothing at all, including with `--execute`.

> **Checked against the authority and then against the fleet, which found
> different things.** Running conda-forge's own
> `_get_required_checks_and_statuses`, copied verbatim, over the 551 feedstock
> checkouts on disk agrees with swage on all 551 — the value of that comparison
> is in the two providers conda-smithy configures with a file that outlives
> being switched off, which no test written from the same reading of that
> function would have caught differently than the reading did.
>
> The fleet run is what found the shapes: 22 open bot pull requests, 5 green, 2
> not yet reporting, 15 refused. Every refusal is a real one. Eleven have a
> build that failed, one has a merge conflict, one is a pull request GitHub
> lists as open and merged while the sweep was running — which is the
> already-merged guard firing on a live race rather than on a fixture — and
> [grpc-google-iam-v1#32](https://github.com/conda-forge/grpc-google-iam-v1-feedstock/pull/32)
> sat eight days with GitHub Actions green and *no linter status ever posted*,
> which conda-forge would refuse identically and which no amount of waiting
> will resolve.
>
> It also settled `mergeable_state` (§5.2.1), which had looked like a free
> improvement on conda-forge's rule and turns out to be unrelated to whether
> anything passed.

**Step two — the merge itself. Built, attempted, and removed.** `swage update
--execute` merged a no-change pull request on a `trust: auto` feedstock,
pinned to the commit whose CI it had verified, and commented afterwards. It
ran once, against `google-ads` #55, and GitHub refused it twice: once for the
base branch policy, and then — with that cleared by `--admin` — for writing a
workflow file without the `workflow` scope (§5.2.2). The second refusal is
structural, applies to 11 of the 14 newest bot pull requests in the fleet, and
is not worth the scope it would take to clear. The merge came out: the API
call, the flag, the merge comment and the merge commit body, leaving `swage`
with no way to merge anything at all.

> **Two orderings paid for themselves before they were deleted.** The comment
> went *after* the merge, so both refusals published nothing — had it gone
> first, that pull request would carry a permanent sentence saying swage merged
> it. And the pin was never tested in anger, but it is the reason the second
> attempt could safely run against the same commit as the first.

> **What survives is the check.** swage still establishes that a no-change pull
> request is green, mergeable and byte-identical to what it would write, and
> reports it as `READY TO MERGE` with its URL. The reading is what swage saves
> on this path; the clicking stays with a person.

> **And what it cost is worth stating plainly.** Path B is the single most
> common outcome across a few hundred feedstocks, and it was the difference
> between a tool that saves real time and one that relabels work you still
> have to do by hand. That difference is now smaller than the design assumed
> for its whole life: swage reads the fleet and tells you which handful of
> pull requests are ready, which is worth having and is less than was
> promised. Path A -- push, label, conda-forge merges -- is untouched, and it
> is where the autonomy actually lives.

**Phase 4 — `status`. Done.** Closes the loop: it reads swage's own runs in a
window, asks GitHub what became of every pull request they pushed to or left
waiting, and re-plans the ones still open through the same path `scan` uses.
After this, the tool is doing the job described in the original ask.

> **It is smaller than it was, because re-arming came out.** The phase was
> specified around repairing a `DEGRADED` pull request by re-adding its
> `automerge` label, and that cannot work at the moment this command runs
> (§5.5, §8). Dropping it took the write path out of the command entirely, and
> what it was for — a no-change pull request whose CI has since gone green — is
> reported as `READY TO MERGE` without writing anything at all.

**Phase 4.5 — `swage draft`** (§8.1), and the two config changes it forces:
`reason` required on an `add_requirements` entry, and a per-output form for it
(§4). **Done**, though the two config changes trailed the command by three
weeks: `draft` shipped emitting a skeleton the schema still accepted without a
reason, which is the exact failure this phase pairs them against. Eleven
feedstocks acquired entries in that window, each explaining itself in a YAML
comment the schema could not see. Sequenced here rather than earlier because it is ergonomics rather than
capability — the gates already ask the right questions — and sequenced *before*
Phase 5 because `audit` is what turns the config backlog from a handful of
feedstocks into a list nobody can work through by hand. Auditing 600 feedstocks
without a way to answer what the audit finds produces a report and no
decisions.

> **The order matters more than it looks.** Ten of ~490 feedstocks are
> configured today, and the eight findings across the six that needed attention
> took a working day of hand-assembly to adjudicate — of which the adjudication
> was minutes and the assembly was the rest. Five of the eight turned out to
> need *no config at all* (§3.3.7), which is only obvious with the metadata
> open beside the recipe. A tool that makes the assembly free does not make the
> decisions, and must not look like it is trying to.

**Phase 5 — `audit`. Done.** Across the whole fleet, read-only (§8.2). Three
pieces, and only the first was new work of any size:

1. **Plan every feedstock on its default branch**, rather than only the ones
   with an open bot pull request. `plan_at` and the gates already do this; what
   is missing is reading a repository's default branch instead of assuming
   `main`, and caching archives so a second audit pays only for what changed.
2. **The checks that need no plan** — an inert `automerge` label, a bot backlog
   at four, an archived feedstock with an open pull request, a config file for
   a feedstock nobody maintains. One listing per feedstock, and no other
   command will ever report them.
3. **The report**, which is §9's, with the two CI buckets absent and the
   sentences rewritten for a command that has no pull request in front of it.

> **The size of what is hidden is now measured, and it is the argument for the
> phase.** `draft` was sequenced before this one because auditing 600
> feedstocks without a way to answer what the audit finds produces a report and
> no decisions. What that argument did not say is how little is visible in the
> meantime. A `scan --all` today plans 8 feedstocks of 487 and reports the
> other 479 as having no open bot pull request — true, and it says nothing
> about them. 154 of the 487 resolve to config beyond the defaults, 149 of
> those through the two family globs and 13 through a file of their own, which
> leaves 333 with none at all; not one of the 333 has ever been planned against
> its real upstream metadata, because none of them had a pull request to plan.
>
> So whether the config backlog is 333 decisions or five is exactly what nobody
> can currently say, and saying it is what this phase is for. Note which
> direction the uncertainty runs: the default was then `manual`, so an
> unconfigured feedstock was never pushed to — and the answer survives §5.4's
> change to the ladder, because what is now never pushed is a change no check
> accounted for. Nothing is at risk in not knowing
> — the cost is only that the fleet is far less automated than it could be, and
> that no one can point at the gap.

> **And here is the answer, from the first fleet-wide audit: 487 feedstocks in
> 16 minutes.**
>
> ```
>     174  a decision is needed
>     148  v0 meta.yaml
>      74  the recipe already matches its release
>      49  ready except that nobody has blessed it
>      42  swage stops
> ```
>
> Two runs an hour apart agreed on every feedstock but one, which is the
> archive downloads: three failed in the first run and two in the second, and
> a feedstock whose sdist did not arrive is reported as stopped rather than
> guessed at. Expect a couple of feedstocks of that noise per sweep.
>
> **The config backlog is 174, not 333.** The difference is the 74 that need
> nothing and the 49 that need only a `trust` line — a third of what was
> unconfigured turns out to want no decision at all. And 49 feedstocks are one
> config line from being maintained unattended, which nothing before this could
> have pointed at.
>
> **148 are still v0**, which sizes Phase 6 against the live fleet rather than
> against the checkouts on disk: §7's "136 v0 feedstocks" was counted in a
> working directory that has since changed, and this is the number that will
> not go stale.
>
> **Of the 42 where swage stops, 30 are correct and most are not even in
> scope.** Nineteen are source archives with no Python metadata in them at all
> — `netcdf-c`, `proj`, `postgis`, `nco` — which is what a C library's tarball
> looks like to a tool that reconciles Python metadata. Six are the
> platform-conditional constraints of §3.3.4, four the several-source recipes
> of §3.6, one a contradictory constraint. Three more are network failures and
> will pass on the next run. That leaves ten worth reading, and one of them is
> a finding rather than a refusal: `parallelio`'s recipe pins a `sha256` that
> its own `url` does not produce (§3.6), which no other command would ever have
> looked at.
>
> **The bucket wording is the part to watch.** `FAILED (42)` reads as "swage is
> broken on 42 feedstocks" when for nineteen of them the honest sentence is
> "this is not a Python package and never was". Left as it is for now, because
> every one of them states its own reason on its own line, and because a fourth
> bucket meaning "out of scope" is a decision about the outcome vocabulary
> rather than about this phase.

> **The checks that need no plan found something on eight feedstocks, and one
> of them is the case §2.1 predicts and nothing had ever looked for.**
> `apache-airflow-providers-microsoft-azure` has two open pull requests, #67
> and #68, both carrying the `automerge` label, both with green CI that
> finished — #67 on 28 July. conda-forge dispatches automerge from CI status
> events, so there is no event left for either of them and they will sit there
> forever, looking exactly like pull requests about to merge. That is the
> whole argument for this half of the command in one example.
>
> Beside it: four archived feedstocks carrying open bot pull requests nothing
> can merge, and two feedstocks at the bot's backlog cap of four, where no
> further version is offered until somebody clears them. `libcf` and `cdtime`
> are both — archived *and* at the cap.

**Phase 6 — `migrate`** (v0→v1), and `update --migrate` with it (§7.1).
**Done.** The standalone command came first because it is the one that can be
run against a scratch checkout and inspected; folding conversion into an update
pull request is only worth doing once the conversion itself is trusted.

> **The conversion is CRM's. What swage had to build is the review, and that is
> where the phase went.** Running the converter over all 148 v0 feedstocks puts
> 142 through and refuses 6 before it starts. Nothing CRM reports says whether
> the recipe it produced still means what the `meta.yaml` did — the whole file
> is rewritten, so the diff says only that everything changed, and on a compiled
> recipe what changed is the conditions. Reading each v0 condition back against
> the converted recipe found two shapes that mean the recipe is wrong and that
> CRM files as warnings among 457 others: a condition that landed nowhere, and a
> scalar truncated mid-expression. Four feedstocks carry one, all four compiled
> (§7.0.1).

> **The review cried wolf on its first fleet run, which is the failure to watch
> for in a report nobody is obliged to read.** It flagged eleven feedstocks, of
> which seven convert perfectly: a `build.skip` holds one boolean expression, so
> splitting it on `and`/`or` before looking inside reports every compound
> selector as having vanished. Searching the clause whole and judging each hit
> by what precedes it leaves four.

**Phase 7 — retire the old tools.** Port the airflow and google-cloud quirks into
`config/families/`, run both old and new in parallel for a release cycle,
then delete the old scripts. This is also the point to add the contributor
infrastructure deferred at the top of this document — `CONTRIBUTING.md`, issue
templates, a documented config schema for people writing their own quirks — since
by then the design has stopped moving and publishing to conda-forge is reasonable.

> **The port is already done, and nobody had checked.** Both tools keep their
> quirks in module-level tables, and every entry in all six is in `config/`,
> compared table against file on 18 August:
>
> - airflow's `PIP_TO_CONDA_NAME_MAP`, 25 entries, all in
>   `config/name-map.yaml` — `ray` under the key `ray[default]`, because
>   conda-forge builds that extra as its own output and the rename is a
>   property of the whole requirement rather than of the name (§3.2.2);
> - airflow's `EXTRAS`, five keys: four in the family's `embedded_extras`, one
>   of them under the PEP 685 spelling of its name, and
>   `google-cloud-aiplatform[evaluation]` dropped on purpose;
> - airflow's `MULTI_OUTPUT_PROVIDER_CONFIG`, both providers, every supported
>   and skipped extra, in their own feedstock files;
> - google-cloud's two-entry name map, its four feedstocks of
>   `FEEDSTOCK_RUN_EXTRAS`, and `google-cloud-bigquery`'s per-output config;
> - `SELF_REFERENTIAL_EXTRAS`, which is one extra named `all` and is a `skip`
>   entry rather than a rule.
>
> The behavior around those tables — expanding a dependency's extra behind
> `# start`/`# end` markers, merging several requirements on one package,
> naming an output after an extra — is reimplemented rather than ported, and
> what says it agrees is the golden corpus (§11) and the published-recipe
> comparison rather than a reading of either script.
>
> So what is actually left in this phase is deleting two scripts that live in
> other repositories, and the contributor infrastructure. The comment in
> `config/families/airflow-providers.yaml` promising the full quirk set "in
> phase 7" was true when it was written and is not any more.

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
  better than the silent drop it replaces. A `use_noarch`-style conditional
  `noarch` (§3.3.5) gets the same treatment: a fixture recipe that swage must
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
  a constraint" is exactly the plausible-looking behavior the rule exists to
  prevent.
- **Upstream-source agreement** (§3.6.1) is asserted on one release read both
  ways. The corpus carries `google-cloud-bigquery` 3.43.0's `pyproject.toml`
  and `PKG-INFO` from the same sdist — whose sha256 is the one the recipe
  beside them pins — precisely because the two disagree on paper. The test is
  that they agree after parsing, which is a claim about both parsers at once
  and cannot be made against either in isolation. Reading the layer over every
  metadata file on the maintainer's machine (89 `pyproject.toml`, 8,759
  `METADATA`/`PKG-INFO`) is the one-off sweep that belongs beside it; that is
  what caught G10's refusal being too strict (§3.6.3).
- **Trust-gate tests** are the highest-value tests in the suite: each of G1–G11
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
| An upstream dependency is constrained per-platform rather than per-Python | On an arch output the recipe says it directly (`if: win`). On a noarch one it depends on the build model: a package built once per platform under `noarch_platforms` has the axis and swage answers on it, in whichever of the two spellings the recipe already uses; a package built once does not, and swage stops rather than choose between setting `noarch_platforms` up and depending unconditionally (§3.3.4) |
| A rule is written against the only kind of feedstock the corpus contains, and becomes the tool's scope without anyone deciding | What happened with `noarch: python`, over most of §3.3. The corpus now carries nine compiled feedstocks and a table of where swage stops on each; the front section states the build model where it can be argued with rather than leaving it inside a reconciliation rule |
| swage adds an upstream build requirement to `host` and leaves the cross-compilation block in `build` stale, so the recipe builds natively and fails cross-compiled | Undecided, and named as undecided (§3.3.6.1). 15 of the 19 outputs with a cross block repeat a `host` requirement in it. Until there is a rule, such a plan holds for review rather than merging |
| One output builds both an arch and a noarch package, so its requirements list holds two alternatives of the same dependency | Detect the conditional `noarch` and refuse the recipe before planning starts (§3.3.5); the failure quotes the line so the maintainer is not left guessing why |
| The two upstream metadata formats spell an extra differently, so config lookups and rendered comments depend on which file a sdist shipped | Normalize every extra name per PEP 685 at both parsers, write config in that form, and refuse a non-normalized config name at load (§3.6.1). Without this, G7 byte-identity varies with the source path |
| Upstream computes its dependency list at build time, so what swage reads may not be what installs | Record it rather than refusing — the list is complete, and the projects that do this have no `[project]` table to fall back on. G10 holds them for review while `dynamic_dependencies: review` (§3.6.3) |
| An sdist's `pyproject.toml` states no dependencies swage can read — poetry, plain setuptools, or a build-time computation — so preferring that file over `PKG-INFO` refuses a release whose metadata is sitting right there | Read each table from the file that can state it: dependencies from `PKG-INFO`, `[build-system]` still from `pyproject.toml` (§3.6.2). 21 of the fleet's 88 archives are this shape, and 18 of them would otherwise lose `host` as well as `run` |
| swage reconciles against a release the pull request is not proposing, because upstream published a newer one between the bot's commit and swage's read | Take the archive and its hash from the recipe rather than asking upstream what is latest, and verify the bytes before reading them (§3.6). The mismatch is a stop, which already caught a half-finished bump in the maintainer's own checkouts |
| A recipe builds one package from several sdists, so "the upstream release" is not a single thing | Stop and name every source rather than reconciling against whichever came first (§3.6). `airflow-feedstock` is the case, at three sdists and two versions; per-output upstream metadata is the real fix |
| A read turns into a write because `gh` infers POST from an `-f` field, and swage opens a pull request it never meant to | Every call in the choke point passes `--method GET`, with a test asserting it (§3.5). Found by tripping over it: GitHub answered `"base", "head" weren't supplied`, declining to open one only for want of arguments |
| Discovery reads team slugs, so the six feedstocks with a dot in their name are silently never seen | Enumerate from the team's `name`; the slug flattens `.` to `-` and 404s on exactly those six (§3.4) |
| A feedstock has several open bot pull requests and swage acts on one of them without saying so | Act on the most recent *version update*, and report the count (§3.4.1). 7 of the 15 feedstocks with a bot pull request have more than one |
| swage reconciles a migration pull request, whose version has not moved, and collides with work a human is shepherding | Migrations are out of scope: a version update is one where the recipe's version differs from the base branch's, tested on the version rather than on the bot's branch naming (§3.4.1). On green CI a migration is a trivial merge, and a human merging it is accountability worth keeping |
| swage pushes to an archived feedstock, where nothing can merge | Archived feedstocks are ignored, detected for free from the pull request's base repository (§3.4.1) |
| An account swage does not recognize files the version bump, so swage quietly plans a staler one instead of skipping the feedstock | Both accounts that file bumps are listed in `BOT_AUTHORS` (§3.4.1). `apache-airflow-providers-google` planned the bot's 21.0.0 with the admin service's 22.3.0 open and `main` on 19.1.0 — the unrecognized pull request read as no pull request, which makes a missing author a correctness bug rather than a coverage gap |
| A feedstock's name is taken for its package's name, so an output is built with the wrong one | Nothing infers one from the other; a package name comes from the recipe (§3.4). `proj.4-feedstock` builds `proj`, and `extras_as_outputs.suffix` is where the confusion would land |
| The archive is a monorepo tarball, so the `pyproject.toml` at its root belongs to no package — or to the wrong one | `upstream.metadata` names the file, relative to the top-level directory (§3.6.2, §4). It is an instruction, not a hint: a named file that cannot be read is a stop, because falling back to the root is the silent wrong-project failure the setting exists to prevent |
| Upstream declares no build system, so `host` has nothing to reconcile against and every line in it fails G1 | PEP 517 already answers this — setuptools — and `default_build_requires` states it in config (§3.6.4). Only ever a backup for silence: a project naming its own backend is never overridden. 21 of the fleet's archives need it and all 21 recipes already say exactly this |
| A name resolver with no data source behind it fails G2 on nearly every feedstock, and reports it as hundreds of unresolvable names rather than as a missing input | Ship both unwritten layers and cache them: the grayskull mapping for renames, `channeldata.json` so identity is a check rather than an assumption (§3.2.1). Config supplied 111 of the airflow providers' 910 references; the other 799 had nowhere to resolve from |
| A third-party mapping renames a package conda-forge already publishes under upstream's own name, and swage rewrites that dependency across a whole family | The mapping still outranks identity, because 91 of its entries do exactly this and nearly all of them must (`blosc` is the C library). The override is an identity entry in `config/name-map.yaml`, which is load-bearing rather than redundant (§3.2.1) |
| A golden test compares dependency lines, so the comments swage generates go unverified and a rerun silently deletes the markers that make it idempotent | Compare a rendered recipe against a published one byte for byte, which is what G7 claims anyway (§6, §11). Both comment rules were wrong until this was done, on four of eight corpus recipes |
| An output shape has nowhere to record a declined extra, so it can never opt into exhaustiveness and G3 reports `n/a` forever on a feedstock that looks fully specified | `skip` exists in both shapes -- `extras_as_outputs.skip` and `outputs[].run.skip` (§4). Implementing only the first left the whole google-cloud family unable to opt in |
| swage renders a requirements section and destroys commented-out lines recording why a dependency was deliberately left out | `exclude` moves the decision into the quirks database, where a rerun cannot lose it, and swage renders the reason back as a comment it owns (§3.3.13). Eleven such decisions exist in `airflow-with-all` today |
| A sticky `exclude` outlives its reason and nobody notices the package became available | swage knows the channel's package list, so an omitted package that now exists is reported as a note rather than gated (§3.3.13) — the same bargain as a newly appeared extra |
| A bundle output is mistaken for a conda-forge invention and put out of scope | Bundles correspond to upstream bundling extras and are `outputs[].run.extras` like any other output (§3.3.12); what makes them look special is only that some members have no conda package |
| The two tools swage replaces format the same thing differently, so "reproduce the prior art" has no single answer and this document records both | Pick one convention per disagreement and let the other family reformat once, measuring the cost first: clause order costs 2 lines fleet-wide and the marker comment 88 comments across 53 recipes (§6, §3.3.1). A corpus covering one family cannot surface these at all, which is why §11 now spans both |
| A config-drafting tool makes entries cheap to type while leaving the thinking exactly as expensive, so the quirks database fills with entries that silence gates and explain nothing | `reason` is a required field rather than a YAML comment, and `TODO` and the empty string are refused at load — which is what a draft ships with (§4). The tool also never pre-fills `add_requirements` for an unexplained line, because that is the wrong answer for the whole temporary-constraint class (§3.3.7, §8.1) |
| A golden test's fake package index is seeded from upstream's spellings, so it invents name-resolution failures that look like planner bugs | Build it from the published recipe, which is what conda-forge actually has. `google-cloud-bigquery` declares `Shapely` upstream where the channel publishes `shapely`; a generous index identity-resolved to upstream's spelling and rendered a duplicate line beside the real one (§11) |
| swage renders a requirements section and destroys a maintainer's note about a dependency that is present | Preserve every comment swage did not author, anchored to the requirement below it (§6.1). The planner previously kept them above lines it could not attribute and replaced them above lines it could, so a note survived where swage understood least |
| A comment convention changes wording, so the previous wording is no longer recognized as swage's own and gets preserved as maintainer prose — duplicating it on every affected feedstock at once | The recognizer holds retired forms as well as current ones, and retiring a convention means adding to that list rather than editing it (§6.1). Two wordings are already retired, across 53 recipes |
| A recipe carries a redundant dependency written to survive grayskull dropping an extra, and swage either reproduces it or deletes it | Neither: resolve the requirement correctly and let the leftover line fail G1, so a human removes it once (§3.2). The same shape can be legitimate — `google-cloud-storage` declares both plain and `[grpc]` upstream — so it is told apart by attribution, never by pattern |

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
- ~~What the escape hatch for a contradictory constraint (§3.3.2) looks like.~~
  **Resolved: `constraints:`, and a different feedstock needed it first.** The
  shape sketched here was right — a per-feedstock mapping of a package to the
  constraint a human chose — and two details of it were not. It is *not*
  applied only where swage would otherwise stop: the case that turned up is
  `apache-airflow-providers-google`'s hand-applied `<3.1.3`, where swage
  stopped at nothing at all and simply dropped the bound (§3.3.14). And it does
  *not* carry its own `Provenance` origin, because G1 asks why the dependency
  is in the recipe and upstream still answers that; what config explains is the
  bound. Keeping the two apart is what lets a feedstock record a temporary pin
  without claiming upstream asked for it. **A third detail was wrong and took
  longer to see**: a bound was only a decision when the recipe was *tighter*
  than upstream, while a looser one was reconciled with nothing said. Both are
  differences between what the recipe says and what upstream says, and the
  split key — `constraints` for a bound meant to hold, `temporary_constraints`
  for one that must be re-checked — is what replaced the guess.
- ~~**What `embedded_extras` accounts for at G3.**~~ **Resolved: the clause is
  gone.** It contributed the part of a key before the bracket —
  `pyhive[hive-pure-sasl]` contributed `pyhive` — which is a *package* name
  where G3 asks about the packaged project's own extras. Two unrelated
  namespaces, so it could only match by coincidence, and the open question
  called it inert on that basis. **It is not inert; it coincides today.**
  `apache-airflow-providers-amazon` declares upstream extras `aiobotocore` and
  `pandas`, and the family config carries `aiobotocore[boto3]` and
  `pandas[sql-other]` for entirely unrelated reasons, so G3 counted two real
  extras as accounted for on a name collision. That is a gate disarmed rather
  than a clause doing nothing, and neither feedstock declares a `skip` list
  yet, which is the only reason it has not yet mattered. The reading through
  the *inside* of the bracket was not adopted either: a dependency-carried
  extra is G2's business (§3.2), where swage now refuses to drop it silently.
- ~~**How a note that is not a gate reaches the report.**~~ **Resolved:
  `FeedstockRecord.notes`, printed under the feedstock.** All three shapes the
  question offered were really one, because §4 had already drawn the answer: the
  example shows the note indented beneath the feedstock's own line, so a
  *second field* rendered *as a line under the feedstock* is what satisfies it.
  Keeping it out of `detail` is the load-bearing half — a merge-ready feedstock
  has no detail to append to, and giving it one would make an advisory read as
  the reason it was held. The summary's listing rule widened from "carries a
  detail" to "has something to say", which is what lets a feedstock with no
  failing gate be named at all.

  The wording drifted from the example on purpose. §4 said *"adds extra"*, which
  claims the extra is **new**, and swage cannot know that without comparing
  against the previous version's metadata — which the plan does not carry. A
  note that fired for exactly one version bump and then went quiet would also be
  a signal that expires while the situation does not, so every unaccounted extra
  is reported on every run until somebody decides about it.
