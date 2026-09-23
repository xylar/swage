# swage v2 — design

**Status:** draft, written against 1.0.0. Nothing here is built.

v2 is the same tool: the same scope, fleet, `config/`, commands, safety
rules and output. What changes is underneath. v1 grew one rule at a time out
of what the fleet taught it, and its internals record that history: three
implementations of one rule, a core function with seventeen parameters,
seventeen outcomes, fourteen gates of which two check nothing, five spellings
of one policy. v2 rebuilds the internals on the model v1 arrived at, and is
held to v1's output over the whole fleet while it does so (§14).

**How this document relates to v1's `DESIGN.md`.** That file interleaves
three things: the rules, the findings about conda-forge the rules rest on,
and the record of how each rule was reached. v2 keeps them apart.

- **This file is the rules.** It states what swage does in the terms the
  code uses. Where a rule's justification lives in v1, it cites the section
  and does not restate it.
- **`docs/conda-forge.md` is the findings**: the facts about conda-forge
  that hold whether or not swage exists. They come from v1 §2, §3.3.4,
  §3.3.6, §5.2 and §5.2.2, verbatim where the wording was already careful.
- **v1's `DESIGN.md` is the decision log**, frozen at the `1.0.0` tag as
  `docs/design-v1.md`. Every `v1 §n` below points into it. It is not
  extended: a decision made during v2 is a paragraph in §16 and a commit
  message.

A reader who has never seen v1 reads §1, §2 and §9 and knows what swage
does. A reader who wants the argument follows the citation. Rationale that
belongs in this file sits in a block like this one, below the rule it
explains, so a reader scanning for rules can skip it:

> **Why:** the rules are what the code is checked against; the argument is
> what a reviewer checks the rules against. They are read at different times.

Open questions are in §17.

---

## 1. The model, in one page

Carried over from v1 with one addition, the last sentence under the third
heading.

### The subject: a recipe, against what its upstream release declares

swage reconciles the dependencies a recipe states against the dependencies
the release it packages declares. Everything else — formatting, the test
matrix, the entry points, the merge machinery — serves getting that change
reviewed and landed.

**Where upstream's declaration lives is a per-feedstock fact, and config
names it** (§5). A family or feedstock says `source: github` with a tag and
a path, `source: archive` with the file to read inside it, `source: esmf`,
`source: cmake`, `source: manual` with the paths a person reads, or
`source: none`. Five readers exist: a `pyproject.toml` in a git tag, a
sdist's core metadata, a wheel's `METADATA` as a fallback, ESMF's makefiles,
and a CMake project's `CMakeLists.txt` (§6).

**A feedstock that does not package a Python distribution still has an
upstream declaration.** Where no reader exists, that is a gap in what swage
can read, not a boundary on what it covers (v1 front section). Such a
feedstock reports that it was not read, in those words.

**A build-system reader answers "which packages, and where does upstream say
so", not "which versions".** A `CMakeLists.txt` cannot state a version
bound. Silence from such a reader is not upstream declining to constrain a
package, and the recipe's own bound stands (v1 HANDOFF, `states_versions`).

### The build model is a property of each output, not of the fleet

An output declares how many artifacts it produces, and how upstream's
environment markers translate follows from that.

| Output | Artifacts | A `python_version` marker | A `sys_platform` / `platform_machine` marker | `python_min` | Python test matrix |
|---|---|---|---|---|---|
| `noarch: python` | **one**, installed on every Python from `python_min` up | collapses: one line that must hold across the whole range | stops: every way to express it is a packaging decision (v1 §3.3.4) | required; from the recipe or `.ci_support` (v1 §3.3.3) | conda-smithy's latest-Python rule applies (v1 §3.7) |
| `noarch: python` with `noarch_platforms` | **one per platform** | collapses, inside each artifact | translates to `if: win` where the artifacts differ | required | the rule applies |
| architecture-specific, with Python | **one per Python per target** | translates: `if: match(python, "<3.13")` | translates: `if: win`, `if: not (win and arm64)` | not stated; its absence is not an error | the variant list is the matrix |
| architecture-specific, no Python | one per target or variant | — | — | — | — |

There is no fifth row. One output that builds both an arch and a noarch
package is refused before planning starts (v1 §3.3.5).

**Collapsing and translating are the same fidelity rule under different
constraints, and in v2 they are the same code.** Every declaration is
evaluated on a grid of (Python minor, build target) cells. An output's build
model says which cells form one artifact. Within an artifact the answer must
hold on every cell; across artifacts the recipe says what differs. One
artifact is the noarch collapse. One artifact per cell is the arch
translation. One artifact per platform is `noarch_platforms`. §9.3 is the
algorithm.

**Read per output, never per feedstock.** conda-smithy scopes `noarch` per
output and so does swage.

### What swage writes, and what it never authors

Three regions of a recipe, and nothing else:

1. **requirements sections** — the lines and the comments swage renders
   around them;
2. **the python test matrix** in `tests` (v1 §3.7);
3. **`build.python.entry_points`**, from the scripts the release declares
   (v1 §3.3.15).

Under `source_versions: auto` alone, also the `context` entry and `sha256` of
a second source whose version a sibling release's exact pin dictates (v1
§3.6.5). That is the one hash swage authors rather than checks.

It never authors: the bot's `version` or `sha256`, a new output, a
`run_constrained` entry, a package's Python floor, a build variant, or
`conda-forge.yml` outside a v0→v1 migration. Each is a packaging decision no
metadata contains. The list is the boundary that lets an unattended tool be
trusted.

### Nothing merges by accident

swage pushes a commit, and conda-forge's automerge decides. The `automerge`
label is not a trigger — a CI run is — so the push comes strictly before the
label, and a label applied after CI has finished does nothing. The same
window is open on a pull request swage finds nothing to change in, and on a
blessed feedstock swage labels it there too. swage has no merge in it:
GitHub refuses a merge that re-renders `.github/workflows/`, and the
`workflow` scope was declined (v1 §5.2.2). A pull request that needs no
change and whose CI has finished is reported `READY TO MERGE` with a link,
and a person presses the button.

---

## 2. The contract: what v2 changes, and what it does not

### 2.1 Unchanged, and checkable

| | What is held constant | How it is checked |
|---|---|---|
| **Rendered recipes** | byte-identical to 1.0.0 over the whole fleet | `audit --all --cached` against the reference sweep (§14) |
| **Outcomes** | the same feedstock lands in the same bucket, under §11.2's renaming | the same replay, outcome column |
| **`config/`** | every file is read unchanged; every key keeps its meaning | the replay reads the committed tree |
| **The GitHub read surface** | the same `gh api` calls, in the same argv form | v1's `~/.cache/swage/reads/` replays under v2 |
| **`run.json`** | v2 reads every run v1 wrote | `swage explain` and `swage trust` over the 580 recorded runs |
| **Commands and flags** | `scan`, `update`, `status`, `audit`, `migrate`, `explain`, `draft`, `trust`, `completion`; `-f`, `-m`, `--all`, `--dry-run`, `--migrate`, `--cached` | the CLI tests |
| **The corpus** | `tests/corpus/` reproduces | the golden tests |
| **Safety rules** | `CLAUDE.md`'s list, every item | review |

### 2.2 Changed

- **One plan algorithm** for markers, over a grid (§9.3), replacing
  `reconcile`, `split_by_environment` and `split_by_platform`.
- **An `Output` value** carries the build model and everything derived from
  it (§9.1), replacing the parameters threaded through `plan_section`.
- **Findings replace gates** (§9.7). A check is a row in a table; a failure
  is a `Finding` with a kind, a subject, what was found and what to do. The
  `G`-numbers go, in code and in `run.json`; a table maps them when reading
  v1's runs.
- **One record type** (§11.1), produced where the decision is made,
  replacing the plan/`consider`/`report` triple and `build_record`'s twenty
  parameters.
- **Thirteen outcomes** with a `reason` field, from seventeen (§11.2).
- **One policy vocabulary** for the proving-period keys (§5.3).
- **Writing for readers** as a stated rule with a test (§3).
- **Compatibility shims dropped**: `--execute`, and the `merge-ready` /
  `ready-to-merge` pair (§11.2).
- **Shell completion** by callback (§12.3), once the CLI imports in under
  100 ms.
- **`migrate/` frozen**, not rewritten (§13).

### 2.3 Not changed

The `recipe` layer, the `names` resolver, the five readers, `forge`'s GitHub
and git code, and the formatter's ordering rules carry over close to as-is.
They are small, they are right, and each has a corpus behind it. The rewrite
is of the middle — plan, verdict, record — and of the vocabularies those
three share.

---

## 3. Writing for readers

This section governs everything a person reads that swage produces: the
comment on a feedstock pull request, the commit message swage writes, a
finding's sentence, the terminal summary, `explain`, and the `draft`
workbench. It also governs this document. `CLAUDE.md` and
`.claude/skills/` apply the same rules to what agents write in swage's own
repository — pull request descriptions, reviews, plans — and are not
repeated here.

The rules are Polaris's (`AGENTS.md`, "Writing for human readers", and its
`.claude/skills/`), adopted whole. So are its numbers.

> **Why:** the numbers have to come from somewhere, and not from here.
> Nearly every word in this repository was written by an agent, so swage's
> own medians describe the problem. Polaris's were measured over colleagues'
> writing from 2023 and 2024, before any agent wrote there.

### 3.1 The rules

- **Lead with the answer.** The first sentence says what happened or what to
  do. Setup and evidence go last.
- **One point per paragraph, and few paragraphs.** Say each thing once.
- **Do not narrate the mechanism.** What swage did and where to look; not
  the chain of rules that got there. The mechanism is in this document.
- **Cut clauses that qualify rather than inform**, and any sentence whose
  only job is to justify the one before it.
- **Backticks for what a reader would type or grep**, half as often as
  feels natural. A code block holds an artifact swage did not write — a
  recipe line, an upstream declaration — never authored prose.
- **One comment, one decision.** A comment names what is outstanding on
  this pull request. Anything still true after it merges is config, not a
  comment.
- **The two halves stay apart.** What is wrong is said in terms of the
  recipe and upstream, and is publishable. What to do about it names config
  keys in swage's repository, and is printed only by swage's own surfaces
  (v1 §3.3.10, §5.4). The rung is the same rule: a published sentence says
  what swage did and who acts next, never which key decided it.
- **Nothing a reader has to research.** No design shorthand, no `G6`, no
  key name from a file the reader cannot open, in anything published
  (`CLAUDE.md`).
- **Written as its author would write it.** Everything pushed to GitHub
  appears under the maintainer's account, so it is about the work, never
  about who asked for it (`CLAUDE.md`).
- **Said once.** swage does not post a comment a pull request already
  carries word for word. Bodies are compared rather than the trailer looked
  for, so a pull request whose situation has moved still gets the comment
  that now applies.
- **Signed.** Every comment swage posts ends with one trailer line naming
  the tool and linking it. It replaces v1's "link on first mention". A
  comment carrying a change says `change`; one carrying only a reading says
  `reconciliation`.

  ```
  ---

  *Posted by [swage](https://github.com/xylar/swage) on @xylar's behalf. The change above was generated, not reviewed; please check it accordingly.*
  ```

  > **Why:** Polaris signs agent-posted text so a reader knows the wording is
  > unreviewed. swage's comments are tool-generated rather than AI-authored,
  > so the shape carries over and the sentence says what is true of a tool.

- **Source docstrings cite; they do not restate.** A docstring says what
  the function does and names the section of this document that governs it.
  It does not restate the rule, its history, or the feedstock that forced
  it. That belongs in the section and in the commit that acted on it.

  > **Why:** v1's `src/` is 26,500 lines, of which 10,200 are docstrings
  > and comments — most of them the design, restated at every call site.
  > Polaris exempts code comments from its rules; this is the case its
  > numbers do not reach.

### 3.2 Calibration

Polaris's measured figures, as targets for swage's surfaces:

| surface | target | Polaris measured (human, 2023–24) |
|---|---|---|
| pull request comment, without its findings list or trailer | ≤ 55 words | review bodies: 14 words median, 55 at the 90th percentile |
| one finding's `said` half | one sentence, ≤ 32 words | inline review comments: 22–32 median |
| the sentence beside a feedstock in the terminal | ≤ 12 words | — |
| commit message swage writes: subject | ≤ 60 characters, imperative | — |
| commit message swage writes: body, without its lists | ≤ 62 words | pull request descriptions: 27 median, 45–62 at the 75th |
| this document | 20 words per sentence; no hedging phrases | Polaris's two design documents: 28 and 30, with 6 and 16 hedges |

The hedging phrases: "worth noting", "not an accident", "deliberately",
"exactly", "precisely", "on purpose", "the whole point", "this is not a
stylistic preference". v1's `DESIGN.md` has 187 of them at 34 words per
sentence.

### 3.3 The test

The comment, the findings and the terminal lines are rendered over the
corpus and every recorded run, and a test asserts the budgets above. A
message that outgrows its budget fails `pixi run check` where it is
rendered, not in review. A code span counts as one word, whatever is inside
it (`run/budgets.py`).

`tests/test_wording_budgets.py` plans the corpus and measures the terminal
line, both comments and the commit message; the corpus plans cleanly, so a
finding's sentence is measured where the checks are tested and, for the
config reasons a `recheck` publishes, over `config/`. `scripts/budgets.py`
measures the recorded runs, which the tests cannot ship; a v1 run has a
check's findings joined and cannot be measured.

> **Why:** the same reason the fleet sweep exists. Every layer so far shipped
> with defects its tests did not catch and a run over real data did; wording
> is a layer.

### 3.4 This document

Normative statements come first in a section and stand alone. Rationale is
in a **Why** block below, which a reader can skip. A principle is stated
once and cited by section afterwards. Rejected alternatives are in §16,
cited from where they apply, and are not re-argued in place. Open questions
are in §17.

---

## 4. Layers

```
swage/
  config/    the quirks database: schema, layering, loading           §5
  upstream/  readers: a release -> Declaration                         §6
  recipe/    recipe.yaml <-> Recipe; the splice writer                 §7
  names/     an upstream name -> a conda-forge name, with provenance   §8
  plan/      Output x Declaration x Recipe x Config -> Plan            §9
  forge/     GitHub reads (recorded, replayable) and writes            §10
  run/       the Record; run.json; the renderers                       §11
  cli/       the commands                                              §12
  migrate/   v0 -> v1, frozen                                          §13
```

Dependencies point downward only. `run/` depends on `plan/` and `forge/`;
`cli/` on everything; nothing depends on `cli/`. `names/` is v1's
`mapping/`, renamed because a `dict` is also called a mapping.

The layering is v1's. What changes is inside `plan/` and `run/`, and the
boundary between them: v1 has `plan/` produce a `RecipePlan`,
`cli/consider` wrap it in a `PlannedRecipe`, and `report/build` translate
both into a `FeedstockRecord`. v2 has `plan/` produce a `Plan` and `run/`
produce a `Record` from it, in one function with one signature.

---

## 5. `config` — the quirks database

### 5.1 What does not change

Every key in v1 §4 and `config/schema.py` keeps its name, shape and meaning:
`feedstock`, `family`, `match`, `trust`, `upstream` (six sources),
`extras_as_outputs`, `outputs[].run` (`core`, `extras`, `from_extras`,
`skip`), `name_map`, `recipe_owned`, `add_requirements`,
`temporary_requirements`, `constraints`, `temporary_constraints`,
`overruled_constraints`, `not_packaged`, `embedded_extras`, `retire`,
`built_everywhere`, `variant_conditions`, `run_constraints`, `unmaintained`,
`removals`, `dynamic_dependencies`, `test_matrix`, `source_versions`,
`entry_points`; the `defaults.yaml` keys `default_build_requires` and `pure_python_build_tools`;
`trust.yaml`'s batches; `name-map.yaml`, `cmake-map.yaml`, `link-map.yaml`.

The validators stay (v1 §4): `reason:` required on every override,
`not_packaged`, `built_everywhere`, `variant_conditions` and trust batch; a
name in at most one of the three constraint keys; extra names PEP 685
normalized; an extra in at most one of `extras` and `skip`.

### 5.2 Layering, stated once

| Rule | Keys |
|---|---|
| **most specific wins**, whole value | `trust`, `upstream`, `extras_as_outputs`, `removals`, `dynamic_dependencies`, `test_matrix`, `source_versions`, `unmaintained` |
| **most specific wins, per entry** | `name_map`, `outputs`, `constraints`, `temporary_constraints`, `overruled_constraints`, `not_packaged`, `built_everywhere`, `run_constraints`, `embedded_extras` |
| **union across layers** | `recipe_owned.*`, `add_requirements`, `temporary_requirements`, `variant_conditions`, `retire` |

Layers, least specific first: `defaults.yaml`, the matching family, the
feedstock's own file. `trust.yaml` supplies `trust` for a feedstock whose own
file does not state it (v1 §5.4). The loader is generated from this table.

### 5.3 One policy vocabulary

- **`trust: never | propose | auto`** — what may happen to a change every
  check accounted for. Unchanged.
- **`review | auto`** for every proving-period policy: `removals`,
  `dynamic_dependencies`, `test_matrix`, `source_versions`. `review` holds
  the feedstock for a person; `auto` treats the change as ordinary. Under
  `source_versions: review` the hold is `self-conflict`, and swage makes no
  edit: §1 says it authors the entry under `auto` alone (v1 §3.6.5).
- **`entry_points: reconcile | manual`** — whether an output's list is kept
  in step with upstream or is conda-forge's own (v1 §3.3.15). Not a proving
  period: there is nothing to promote.

This is the one config edit v2 needs, and it is a commit of its own:
`dynamic_dependencies: trust` → `auto` in the 22 files that carry it, after
the loader has learned the new spelling. The loader accepts the old
spellings, `trust` and `source_versions: never`, through the v2.0 cycle with
a note in the terminal.

> **Why:** v1 has `never|propose|auto`, `review|auto`, `review|trust`,
> `review|auto` and `never|auto` for what is one idea in four places and a
> different idea in one.

### 5.4 Loading

`ConfigTree` reads all of `config/` once, validates every file, and
answers `for_feedstock(name) -> FeedstockConfig` by §5.2. A
`FeedstockConfig` is frozen and records the files that produced it
(`config_layers` in the record). An unknown key is refused. That is the
opposite of `run.json`'s rule (§11.1): a typo in config is something a person
should hear about at once.

---

## 6. `upstream` — readers

### 6.1 The `Declaration`

One value, whatever read it:

```python
@dataclass(frozen=True)
class Declaration:
    name: str                       # the distribution or project name
    version: str | None
    source: str                     # archive URL, or repo@tag
    declared_in: str                # file(s) inside it, joined by " + "
    requires: tuple[Requirement, ...]        # runtime, in source order
    extras: Mapping[str, tuple[Requirement, ...]]   # PEP 685-normalized keys
    build_requires: tuple[Requirement, ...] | None  # [build-system] requires; None = absent
    python: str | None              # requires-python
    scripts: Mapping[str, str]      # console and gui scripts, name -> target
    dynamic: bool                   # PEP 643 Dynamic: Requires-Dist
    states_versions: bool           # False for esmf and cmake
```

`Requirement` is `(name, specifier, marker, raw)`, with the original text
kept for every message. Source order is preserved and nothing is collapsed;
collapsing is the planner's decision (v1 `upstream/model.py`).

### 6.2 The reader protocol

```python
class Reader(Protocol):
    def read(self, config: UpstreamConfig, fetch: Fetch) -> Declaration: ...
```

Five implementations, selected by `upstream.source`. `manual` and `none`
produce no `Declaration` and short-circuit to the `not-read` outcome when
config is read (v1 §3.6.8). Each reader's rules are v1's:

| `source` | reader | rules |
|---|---|---|
| `archive` (default) | sdist: `pyproject.toml` + `PKG-INFO`, each half from whichever can state it; wheel `METADATA` as fallback | v1 §3.6, §3.6.2, §3.6.3, §3.6.4 |
| `github` | `pyproject.toml` at a tag and path in a monorepo | v1 §3.6 |
| `esmf` | ESMF's makefiles joined with the feedstock's build script | v1 §3.6.6 |
| `cmake` | `find_package` under the guards swage can read, with `cmake-map.yaml` | v1 §3.6.7 |
| `manual` | none; the named paths are checked to exist where the archive can be fetched | v1 §3.6.8 |
| `none` | none; the config says what the feedstock builds | v1 front section |

The `cmake` reader is 1,000 lines of three-valued CMake condition
evaluation serving 14 feedstocks. It carries over as-is behind the protocol.
It is the one module where a plugin boundary is justified if a sixth reader
arrives.

### 6.3 Scripts

New in 1.0.0 (v1 §3.3.15). `scripts` is read from `[project.scripts]` and `[project.gui-scripts]`;
from `[tool.poetry.scripts]` and `[tool.flit.scripts]` for the backends that
predate PEP 621; and from `entry_points.txt` in a sdist's `.egg-info` or a
wheel's `.dist-info`. The declaration comes first, then what the backend
computed from it, the rule `_reconcile_sources` applies to the version.
`PKG-INFO` and `METADATA` never carry scripts. The planner writes
`build.python.entry_points` from this field (§9.6).

### 6.4 The previous version

Removal classification (§9.5) needs the declaration for the version the
recipe reflects as well as the one being bumped to. It is read from the
recipe at the pull request's base commit, through the same reader. Where it
cannot be read — a yanked release, a deleted tag — every removal is
`unclassified` (v1 §3.3.7).

---

## 7. `recipe` — the document

Carried over. `Recipe` is a ruamel-backed model of a v1 `recipe.yaml` that
preserves every comment and every byte outside the regions swage owns.
`read_recipe` refuses a v0 `meta.yaml` by filename before parsing (v1 §3.1)
and refuses one output that chooses whether it is noarch (v1 §3.3.5). It
reads conditional entries, build strings in both spellings (`hdf5 * x_*`
and `hdf5 [build=x_*]`), and the `noarch_platform` templates by expansion
rather than evaluation (v1 §3.3.4).

The writer splices. It replaces the line ranges of the regions §1 names and
leaves every other byte alone. That is an invariant with a test, not a gate.
v1's G5 is true by construction in code, whatever its design says about it
being checked; v2 asserts it over the corpus in the writer's tests and drops
the gate.

Regions, per output:

| region | path | planned by |
|---|---|---|
| requirements | `requirements.host`, `requirements.run` (and per output) | §9 |
| python test matrix | `tests[].python.python_version` | §9.6 |
| entry points | `build.python.entry_points` | §9.6 |
| second source version | `context.<name>_version`, that source's `sha256` | v1 §3.6.5, under `source_versions: auto` |

`build` requirements are never planned. A cross-compilation block that
repeats a `host` line is kept in step with the line it copies (v1 §3.3.6.1).
A name it does not repeat is a finding, not an edit (§9.7).

---

## 8. `names` — resolution with provenance

Carried over from `mapping/` unchanged in behavior. Five layers, first match
wins: the feedstock's `name_map`, its family's, `config/name-map.yaml`,
identity against conda-forge's `channeldata.json`, unresolved. The grayskull
mapping sits between the global map and identity (v1 `forge/index.py`).
Every answer carries `(conda_name, layer, exact)`. An inexact or unresolved
answer is the `unresolved-name` finding, which withholds the push (§9.7).

For a CMake declaration the map is `cmake-map.yaml` and the identity layer
is skipped: a `find_package` name is not a conda name.

---

## 9. `plan` — the core computation

### 9.1 The `Output`

Everything the build model implies is computed once per output, before any
requirement is looked at, and passed as one value:

```python
@dataclass(frozen=True)
class Output:
    name: str                      # what a report calls it; the package, or a staging label
    package: str | None            # what config's `outputs` matches; None for a staging output
    noarch: bool
    artifacts: Artifacts           # ONE | PER_PLATFORM | PER_CELL
    pythons: tuple[int, ...]       # minors in the universe (§9.2)
    targets: tuple[Target, ...]    # (platform, machine) in the universe
    python_floor: PythonMin | None # from context.python_min or .ci_support, and which; noarch only
    python_ceiling: Version | None # from the recipe's own `python <X`
    pinned: frozenset[str]         # variant keys .ci_support covers (v1 §3.3.6)
    cross_compiled: bool           # has a build_platform != target_platform block
    core: bool                     # draws upstream's runtime dependencies
    extras: tuple[str, ...]        # extras drawn whole, in config's order
    from_extras: Mapping[str, frozenset[str]]   # packages drawn from an extra
    sections: tuple[RequirementsBlock, ...]     # the blocks swage plans: host, run
```

The `Universe` of §9.2 is a property of the `Output`, and so is the floor:
asking a noarch output with no floor for either is the stop.

Derivation, per output:

- `noarch` is the output's `build.noarch == python`.
- `artifacts` is `PER_PLATFORM` where the output is noarch and more than one
  platform appears among the feedstock's rendered variants; `ONE` where it
  is noarch otherwise; `PER_CELL` where it is not.
- `pythons` is every minor from `python_floor` to `python_ceiling` for a
  noarch output, and the minors `.ci_support` renders for an arch one. All
  minors where it renders none, which is a feedstock with no Python in it.
- `targets` is conda-forge's build targets, never the feedstock's rendered
  subset (v1 §3.3.4): `linux` × `{x86_64, aarch64, ppc64le, s390x}`, `osx`
  × `{x86_64, arm64}`, `win` × `{AMD64, ARM64}`. Except on a `PER_PLATFORM`
  output, whose targets are the platforms it renders: each is an artifact,
  and a platform it does not render has none.
- `python_floor` is required for a noarch output and its absence is a stop.
  For an arch output it is not stated and its absence is not an error
  (v1 §3.3.3).
- `core`, `extras`, `from_extras` come from `outputs[package].run` and
  `extras_as_outputs`; `core: true` and no extras for an output config does
  not mention. `host` attribution ignores `core` (v1 `plan_section`).
- `sections` is the output's `host` and `run` blocks, those it has, in that
  order. `build` is read for `cross_compiled` and for the copies `_mirrors`
  keeps in step, and never planned (v1 §3.3.6.1).

> **Why:** a condition written against the fixed target set survives the
> feedstock gaining a platform. Explaining a build backend that is there and
> adding one that is not are different acts, so `host` ignores `core` for the
> first and honors it for the second.

### 9.2 The universe and the grid

A **cell** is one build: a Python minor and a target. The **universe** of an
output is `pythons × targets`. A **marker** is evaluated at a cell with
`python_version`, `python_full_version` at patch `.0` and `.99`, the
platform's `sys_platform`, `platform_system` and `os_name`, and
`platform_machine`.

Before evaluation, per declaration:

1. `platform_python_implementation` and `implementation_name` are resolved
   for CPython and folded away (v1 §3.3.4). A declaration gated *on* PyPy
   reaches no cell and drops out in step 4.
2. Where `built_everywhere[name]` is set, platform and machine comparisons
   are taken as true and erased (v1 §3.3.4.1). The raw text is kept for
   messages.
3. A marker that holds at one patch release of a minor and not another is
   **inexpressible**, and a stop (v1 §3.3.1.1).
4. A declaration active in **no cell** of the universe is dropped before
   anything is refused or rendered, and recorded as out of range for the
   removal step (v1 §3.3.7).

### 9.3 One rule: the artifact's constraint, and what differs between them

The output's `artifacts` groups cells:

| `Artifacts` | one artifact is | varies over |
|---|---|---|
| `ONE` | every cell | nothing |
| `PER_PLATFORM` | every cell of one platform | platform |
| `PER_CELL` | one cell | python, platform, machine |

For one requirement name with declarations `D` after §9.2:

**Within an artifact `A`:**

5. `asked(A) = {d ∈ D : d is active in some cell of A}`. Empty means the
   package is not asked for in `A`.
6. For each axis `A` spans — Python for `ONE` and `PER_PLATFORM`; platform
   and machine for `ONE`; machine for `PER_PLATFORM`; none for `PER_CELL`:
   - **Python axis: collapse.** A `d` active on some cells of `A` and not
     others binds on every cell (v1 §3.3.1), and a note records why
     (step 8).
   - **Platform or machine axis: stop.** A reachable `d` whose marker names
     the axis is refused, before any artifact is looked at. Every expression
     of it is a packaging decision (v1 §3.3.4). The message names the
     output, because a feedstock can hold both build models.
7. `constraint(A)` is the intersection of the specifiers of `asked(A)`, with
   `~=` spelled out first (v1 §6), then intersected with config's
   `constraints[name]` or `temporary_constraints[name]` where one exists.
   Where step 2 erased a marker and two declarations now describe the same
   cells, the **widest** survives (v1 `reconcile.widest`). An empty
   intersection is a **contradiction** and a stop, unless
   `overruled_constraints[name]` names the bound one artifact states. That
   bound *replaces* upstream's, must be satisfiable with at least one of
   them, and is an error where no contradiction arises (v1 §3.3.2.1). A
   contradiction between upstream and a config `constraints` entry is
   reported apart from one within upstream, because the fix is in a
   different file.
8. `note(A)` names the declarations the binding floor, ceiling and
   exclusions came from, in v1 §3.3.1's wording, where those are among the
   ones step 6 collapsed: active on some cells of `A` and not others. One active on
   every cell of `A` is unconditional there and is not named, whatever its
   marker says. Where step 7 overruled, the note is the overruled sentence.
   A `PER_CELL` artifact chooses nothing, so its note is empty.

**Across artifacts:**

9. Group artifacts by `(constraint, asked?)`. One group: one plain line, or
   no line where nothing asked (which step 4 already made out-of-range).
10. Several groups: one entry per group, conditioned on the axes the
    artifacts vary over. Runs of consecutive minors become
    `match(python, "<3.13")` / `">=3.13,<3.14"`. Groups of targets are
    named from the selector table, positive forms first and negated forms
    last; a group no selector names is a stop. Both axes are joined with
    `and` (v1 §3.3.4). Two groups partitioning the universe, both asked, are
    one entry with `else:` (v1 §3.3.1.1). A group in which the package is
    not asked for produces no entry.

That is all of v1's `reconcile`, `split_by_environment` and
`split_by_platform`. The three differ only in `artifacts`, and every rule
above is stated once.

**The fidelity property, which is the test:** for every cell in the universe,
evaluating the rendered entries at that cell yields `constraint(A)` for the
artifact containing it, and `None` where not asked. It holds by construction
for `PER_CELL`; for `ONE` and `PER_PLATFORM` it holds because the collapse
widened nothing. A property test over generated declarations checks it; the
corpus checks the wording.

### 9.4 Keys, ownership, and what a line is

Rules from v1 §3.3.6, as the code sees them:

- A requirement is keyed by **`(conda name, build string)`**, in either
  build-string spelling, held apart rather than normalized. `hdf5` and
  `hdf5 * ${{ mpi_prefix }}_*` are two requirements.
- **Recipe-owned** lines — name a function in `recipe_owned.functions`, or
  in `recipe_owned.names`, or a whole-dependency template naming a variable
  in `recipe_owned.variables` — are preserved verbatim, never resolved, and
  ordered first.
- **A `host` line naming a package `.ci_support` pins carries no version**,
  whatever upstream declares. `run` is not affected.
- **A line from a reader with `states_versions: false`** keeps the recipe's
  own bound.
- **A conditional entry swage did not derive is preserved whole**, and the
  names inside it are attributed one at a time. It goes only where config
  (`retire`, or a `variant_conditions` account) covers every name in it
  (v1 §3.3.7). An entry whose condition selects a build variant is explained
  by an unconditional upstream declaration only through `variant_conditions`
  (v1 §3.3.4).
- **The `noarch_platform` templates** are read by expansion, kept as
  written, and the plan's own copy of the dependency is dropped. Never
  converted from one spelling to the other.
- **A template bound the recipe writes** (`pandas >=${{ x }}`) that renders
  to the planned bound stays a template (v1 `_kept_template`).
- **A comment a maintainer wrote belongs to the requirement below it** and
  travels with that requirement's key. A trailing comment at the end of a
  section survives the section being rendered (v1 §6.1).

> **Why** the pinned `host` line carries no version: conda-build matches a
> `host` entry against a variant key, and an entry carrying a version fails
> to match. A bound there replaces conda-forge's pin rather than tightening
> it (v1 §3.3.6).

### 9.5 Attribution and removal

Unchanged from v1 §3.3.10 and §3.3.7. The vocabularies:

```python
Origin = Literal["upstream-core", "upstream-extra", "config-add", "recipe-kept"]
Fate   = Literal["kept", "retired", "upstream-dropped", "out-of-range", "never-upstream", "unclassified"]
```

Attribution, in order: recipe-owned; upstream core; a listed extra (whole,
through `from_extras`, or through an `embedded_extras` expansion); an
unlisted extra — a finding naming the extra, whose remedy is to list it and
not `add_requirements`; `add_requirements` or `temporary_requirements`;
nowhere — a finding whose remedy is `add_requirements` or dropping the line.

Removal: `upstream-dropped` and `out-of-range` are acted on under
`removals: auto` and held under `review`. `retired` is acted on
unconditionally, because config said what the line is. `never-upstream` and
`unclassified` are never removed: kept and reported. `not_packaged` keeps a
dependency out and is an error the day conda-forge packages it (v1 §3.2.3).
An upstream extra that disappears is not a removal; it orphans an output,
which is a finding (v1 §3.3.11).

### 9.6 Ordering, the test matrix, and entry points

**Order** (v1 §6): recipe-owned first (`python`, `pip`, the pins); then
upstream-declared lines in upstream's source order, with an
`embedded_extras` expansion in its parent's position and delimited by
swage's markers; then conda-forge-only additions, alphabetized, including a
line kept without provenance. Clauses within a constraint: floor, ceiling,
exclusions in upstream's order, and an exclusion the floor or ceiling
already rules out is **dropped**. Comments swage writes: the collapse note,
the `exclude` reason, the extra-block headers (v1 §6).

**The python test matrix** (v1 §3.7): a noarch output whose `tests[].python`
lists the minimum and not `"*"` gains the latest, unless a `run` requirement
caps `python` with `<`. This is a port of conda-smithy's
`_python_tests_cover_latest`, not of the hint text. Held under
`test_matrix: review`.

**Entry points** (v1 §3.3.15): an output's `build.python.entry_points`
list is reconciled against `Declaration.scripts`, keyed by the script's
name. A moved target under the same name is a retarget and a name upstream
declares that the list lacks is an addition; both are upstream's own
declaration and are said as a note beside the verdict. A name the list has
that upstream no longer declares is a drop, and the `dropped-script` finding
holds it once (§9.7). Only a list already there is reconciled: an output
with no `entry_points` key is left alone and not remarked on. A list holding
an `if:` entry is reported, never rewritten. Where something changes, the
list is written in upstream's order; where nothing does, the recipe's order
and spacing stand. Where upstream cannot say — a sdist with no
`entry_points.txt` and no `[project.scripts]` — nothing is compared.
`entry_points: manual` takes a list out of reconciliation.

### 9.7 Findings

A check is a row; a failure is a value:

```python
@dataclass(frozen=True)
class Finding:
    kind: Kind          # one of the table below
    subject: str        # the line, the name, the extra, the output
    where: str          # "`esmf`'s `host` requirements", never a path
    said: str           # what is wrong, in terms of the recipe and upstream; publishable
    remedy: str         # what to do, naming config keys; swage's own output only
```

| `kind` | v1 | severity | when |
|---|---|---|---|
| `unaccounted` | G1 | holds | a line in the recipe has no provenance |
| `unresolved-name` | G2 | **withholds the push** | a name resolved inexactly or not at all |
| `unclassified-extra` | G3 | holds where a `skip` list is declared; a note otherwise | an upstream extra in neither `supported` nor `skip` |
| `orphaned-output` | G4 | holds | an output's extra disappeared upstream |
| `removal` | G8 | holds while `removals: review` | a line would go on swage's own reading |
| `computed-dependencies` | G10 | holds while `dynamic_dependencies: review` | PEP 643 `Dynamic: Requires-Dist` |
| `recheck` | G11 | holds | a temporary constraint, requirement or overruling bound, at every bump |
| `test-matrix` | G12 | holds while `test_matrix: review` | the matrix would change |
| `cross-build-copy` | G13 | holds | a `host` change on a cross-compiled output names a package its `build` block does not repeat and `pure_python_build_tools` does not cover |
| `self-conflict` | G14 | **withholds the push** | an output requires a package this recipe builds at a version it does not build |
| `dropped-script` | G15 | holds | a script the recipe lists that upstream no longer declares (v1 §3.3.15) |

Dropped: G5, a writer invariant (§7); G7, the fact `unchanged` rather than
a check. G6 was never a check; it is the rung, and it enters the decision in
§9.8. Every kind has a plain-language title and a failure sentence in the
same table, and those are what every renderer prints. `run.json` carries
`kind`; reading a v1 run maps `G1`…`G15` through the second column. `said`
and `remedy` follow §3.

### 9.8 The plan and the decision

```python
@dataclass(frozen=True)
class Plan:
    recipe: Recipe                             # what was planned against
    upstream: RecipeUpstream                   # the release, or releases (v1 §3.6)
    outputs: tuple[Output, ...]
    sections: tuple[PlannedSection, ...]       # entries with provenance, per region
    test_matrices: tuple[TestMatrix, ...]
    entry_points: tuple[EntryPointChange, ...]
    findings: tuple[Finding, ...]
    rendered: str                              # the recipe as swage would write it
    correction: SourceCorrection | None        # the recipe as read, and what moved (v1 §3.6.5)
    # and what the checks read beside the sections: unassociated constraints,
    # unaccounted extras, cross-compiled hosts, self-conflicts, the floor
    @property
    def read(self) -> str: ...                 # the recipe as read: recipe.text, or correction.read
    @property
    def unchanged(self) -> bool: ...           # rendered == read, byte for byte
```

`plan_recipe` produces it, findings and rendering included, so no command
can push a plan without its findings or judge one unchanged without its
bytes. The recipe and the release travel with the plan because every
reader of it needs them beside it (§16).

A source version corrected under `source_versions: auto` is planned against
and is part of the change: `unchanged`, the record's diff and the commit
message all measure from the recipe as read, so a run whose only change is
the correction pushes it. The commit message lists what moved; the comment
does not, because it names what is outstanding (§3.1) and the move is in the
diff.

`Decision` is a pure function of `(plan.findings, plan.unchanged, trust, ci)`
and of whether the subject is a pull request, and the order is the
precedence:

```
unchanged, holding finding present   -> NOTHING       needs-review, naming the finding
unchanged, no pull request           -> NOTHING       unchanged
unchanged, CI finished and green     -> NOTHING       path B; ready-to-merge
unchanged, CI running, trust: auto   -> LABEL         labeled
unchanged, CI running or unreadable  -> NOTHING       awaiting-ci
unchanged, CI failed                 -> NOTHING       needs-review, naming the check
changed, trust: never                -> NOTHING       needs-review, reason "trust: never"
changed, withholding finding present -> NOTHING       needs-review, reason "held back: <finding>"
changed, holding finding present     -> PUSH          needs-review, naming the finding; the comment carries the `said` halves
changed, no findings, trust: auto    -> PUSH + LABEL  automerge
changed, no findings, trust: propose -> PUSH          needs-review, reason "<n> lines changed"
converted from v0 in this run        -> PUSH          needs-review whatever the findings said (v1 §7)
v0, no pull request, nothing found   -> as above      needs-migration (v1 §8.2)
```

A feedstock with no pull request is one `audit` planned on its default
branch: nothing waits on CI, and its v0 conversion is one swage would make
rather than one it made. A finding survives the `needs-migration` floor.

**The label is what `trust: auto` grants, and pushing is not what earns
it.** A pull request swage renders byte for byte is one every check passed
against the text that would merge — the same claim swage makes about a
change it pushed, and a stronger one for whoever reads it, who has no diff
to check. What decides whether the label does anything is CI: conda-forge
dispatches automerge from status events, so a label placed while a run is
still to report merges the pull request on green, and one placed afterwards
is inert (`docs/conda-forge.md`). swage labels inside that window and
reports it outside.

An unread CI is not that window. Where swage did not ask, or could not tell
what had to pass, the pull request is `awaiting-ci` on every rung — arming
on a reading swage does not have is the one way this path could merge
something unchecked.

**Every decision that read a release says so on the pull request.** A
commit records that swage looked; where there is no commit nothing else
does, and half the pull requests swage reaches need no change. The comment
names the release and the file that declared it, because a maintainer
merging on the strength of it has no diff to read. Five of them: a change
pushed and armed, and the four whose recipe already matched -- CI green, CI
running and armed, CI running and left, and held by a finding. The last
carries its findings' `said` halves like a refusal's, and is the one
sentence that cannot say what CI did, because swage does not read CI where
a finding holds a feedstock (v1 §5.1). A comment is posted once (§3.1), and
which one is chosen after the label rather than before, since a label that
did not land changes which sentence is true.

What decides that a comment is owed is `plan.unchanged`, not the outcome. A
recipe that renders byte for byte is a reading worth recording whatever
else was found; one that does not is a change swage is refusing to push,
and the pushed comment already says that where anything is pushed at all.

**A `run_constrained` entry upstream declares only under an extra gets a
comment of its own**, whatever else the run did. An extra's requirement is
conditional on asking for the extra and a run constraint is not, so
transcribing one into the other asserts what upstream never asserted, over
every environment holding the package. swage adds no such entry and removes
none — both are packaging decisions — so it says so and proceeds. The
comment is separate because it is about the recipe rather than this
release, it reads the same until somebody changes the recipe, and `_say`
posts it once. In config, `extra: null` says the bound tracks nothing
upstream and is silent; `extra: <name>` says which extra and is reported,
because naming a thing is not deciding about it; `keep` records the
decision and is the only way to quiet one. The argument lives in
`docs/config/names.md`, which the comment links, so what is published is a
page a reader can open rather than a key in a file they cannot.

Push strictly before label. Re-arm by removing and re-adding the label,
never by re-adding alone (v1 §2). A label failure after a successful push is
`needs-review` with the reason "pushed `<sha>`, but labeling failed: `<why>`
-- merge it yourself", which is what v1's `DEGRADED` heading told the reader.
A label failure with nothing pushed is `awaiting-ci`, whose line already
asks the reader for the label swage could not add.

> **Why** the rung decides one unchanged row and none of the others: it
> governs what may happen to a pull request, and the only unchanged row
> where anything can happen is the one with a CI event still to come. Once
> CI has finished the label is inert and swage cannot merge, so
> `ready-to-merge` and a failed check read the same on every rung (v1
> `outcome_for`).
>
> **Why** a pushed change with no findings is `needs-review` rather than v1's
> `proposed`: a person must look either way, and whether they approve a diff
> or answer a question is not a distinction they act on differently. The
> `reason` says which, and the record's empty `findings` list is what
> `swage trust` reads (§11.3).

---

## 10. `forge` — GitHub, recorded

Carried over. Everything is read through `gh api` from the pull request's
head and base commits — the recipe, `.ci_support`, `conda-forge.yml`, the CI
checks — and nothing is cloned until there is a commit to push (v1 §3.5).
The clone is per run and never reused; the push names the bot fork's head
ref (v1 `forge/repo.py`).

**The read surface is frozen for the rewrite.** `ReadRecorder` keys its
cache on the `gh api` argv, and v1's recorded sweeps are what v2 is
measured against (§14). No v2 module changes which endpoint answers a
question, or how the argv is spelled, until the rewrite is verified. After
that it may, and the reference is re-recorded live.

CI verification is the port of conda-forge's own `automerge.py` rules with
its two departures toward refusing (v1 `forge/checks.py`). Discovery is by
org team, the team's `name` rather than its `slug` (v1 §3.4). Which pull
request, when there are several, is v1 §3.4.1.

---

## 11. `run` — the record and the renderers

### 11.1 One record

```python
@dataclass(frozen=True)
class Record:
    feedstock: str
    outcome: Outcome
    reason: str = ""                  # the sentence beside the name in the terminal (§3.2)
    notes: tuple[str, ...] = ()
    pull_request: int | None = None
    pull_requests: int = 0
    head: str = ""
    upstream: UpstreamRecord | None = None
    outputs: tuple[OutputRecord, ...] = ()     # name, artifacts, floor, pythons
    config_layers: tuple[str, ...] = ()
    sections: tuple[SectionRecord, ...] = ()
    findings: tuple[FindingRecord, ...] = ()   # kind, title, subject, where, said, remedy
    decision: str = ""                # the action: nothing, push or push-label (§9.8)
    pushed: str = ""
    merge_check: MergeCheckRecord | None = None
    stopped: str = ""
    # written beside run.json, not inside it
    rendered_recipe: str = ""
    current_recipe: str = ""
    declaration_diff: str = ""
```

One function produces it — `record(feedstock, outcome, plan=, decision=,
...)` — in the pipeline (§12.2), for every command. A planned feedstock's
record is the plan, the decision and what `act` did; a stop — a feedstock
swage could not read, or skips, or does not reconcile — has neither plan nor
decision, so the outcome and the sentence are arguments of their own (§16). It replaces v1's `PlannedRecipe` and
`build_record`.

`run.json` is `{schema, command, started, feedstocks: [Record...]}` with
`schema: 5`: v1's code had reached 4 by the time it was frozen, and the
number in the file is the one that has to be unique. Reading is permissive
and writing is closed (v1 §9): an unknown field is ignored; an unknown
outcome is kept verbatim, rendered in a bucket of its own, and counts as
needing a person. A v1 run (schemas 1 to 4) is read through a mapping:
`gates[].name` → `findings[].kind` by §9.7's table, one finding per failing
check because v1 joined a check's findings and the join is not reversible;
`detail` → `reason`; `recipe` and `python_min` → that many unnamed
`outputs` carrying that floor; outcomes by §11.2's table. A failing `G6` is
no finding, because the rung never was one. `decision` keeps v1's two words,
which every renderer prints as a sentence.

### 11.2 Outcomes

Fourteen, from seventeen:

| v2 | v1 | means |
|---|---|---|
| `merged` | `merged` | the pull request merged |
| `closed` | `closed` | closed unmerged; swage's work was not taken |
| `ready-to-merge` | `ready-to-merge` | no change needed, CI green — a person merges |
| `automerge` | `merge-ready` | pushed and labeled; conda-forge merges on green |
| `labeled` | — | no change needed and CI still running, on a `trust: auto` feedstock: labeled, so conda-forge merges on green |
| `awaiting-ci` | `awaiting-ci` | no change needed, CI still running, and the label is a person's to add |
| `needs-review` | `needs-review`, `proposed`, `degraded` | a person must look; `reason` says whether that is approving a diff or answering a finding, and `pushed` says whether a commit landed |
| `unchanged` | `unchanged` | no open bot pull request, so nothing to plan against |
| `skipped` | `archived`, `unmaintained` | nothing swage does could land; `reason` says which |
| `not-read` | `not-read`, `not-reconciled` | config says where the declaration is, or that there is none |
| `declaration-moved` | `declaration-moved` | `not-read`, and the declaration changed between releases — needs a person |
| `needs-migration` | `needs-migration` | v0; rides along with `update --migrate` |
| `migrated` | `migrated` | converted and updated in one run |
| `failed` | `failed` | swage could not plan it; `reason` is the stop |

Exit code 1 is `{needs-review, declaration-moved, needs-migration, failed}`
plus any outcome this swage has no row for (v1 §9.1).

> **Why** `merge-ready` is renamed: it was one word-order away from
> `ready-to-merge` and meant the opposite thing about who acts. `automerge`
> is the label's name and says what was done.

### 11.3 Renderers

Every renderer renders a `Record` and recomputes nothing (v1 §9.2). Every
sentence they print follows §3.

- **terminal** — the grouped summary, one bucket per outcome, the reason
  beside each name, a link under every bucket whose content is "go and
  look". A feedstock named on the command line is always listed (v1 §9).
- **explain** — one feedstock's record in full: upstream, config layers,
  every line with its origin, every finding with both halves.
- **artifact** — `run.json` and the per-feedstock recipe, rendering and diff.
- **draft** — the workbench (v1 §8.1): `FINDINGS.md` with the metadata
  quoted back, and a `config.yaml` that pre-fills nothing a person has to
  decide.
- **trust** — which feedstocks the recorded audits say have earned a rung
  (v1 §8.4), reading v1 and v2 runs alike. v1 keys "approval was the only
  thing outstanding" on the `proposed` outcome; v2 keys it on a planned
  record whose `findings` list is empty. A v1 `proposed` maps to that.
- **the pull request comment** — `said` halves only, one per finding, the
  trailer from §3.1, written as its author would (`CLAUDE.md`).

`draft` and `trust` carry over close to as-is. They are large because they
quote, and quoting is their value.

---

## 12. `cli` — commands

### 12.1 The set

Unchanged: `scan`, `update [--migrate] [--dry-run]`, `status [--since]`,
`audit [--cached]`, `migrate`, `explain`, `draft [--apply] [--family]`,
`trust`, `completion`. Selectors `-f/--feedstock` (repeatable),
`-m/--family`, `--all` where it exists; a selector is required everywhere a
sweep would otherwise be implied (v1 §8). Exit codes `0`, `1`, `2` (v1 §9.1).

Dropped: `--execute`, which has done nothing since `update` became the
writing gesture. Shell history that says it gets "unrecognized argument".

### 12.2 One pipeline

`scan`, `update`, `status` and `audit` are one function with two switches:
whether to write, and whether the subject is a pull request or a feedstock.

```
select    -> which feedstocks (names, family, all, or the runs in a window)
locate    -> the pull request to act on (scan/update/status) or the default branch (audit)
read      -> recipe, .ci_support, config, previous version
declare   -> Declaration, current and previous
plan      -> Plan
decide    -> Decision
act       -> push, label, comment  (update only; the others record what would happen)
record    -> Record
```

`cli/pipeline.py` is the function; `consider` runs it from `read` on for a
`Subject`, which is a pull request's head or a default branch. `status`
re-plans an open pull request rather than remembering it (v1 §8). `audit`
plans against the default branch and, for a v0 feedstock, against the
conversion swage would make (v1 §8.2). `audit` writes nothing and is the
only command given a replaying recorder.

### 12.3 Startup and completion

The CLI imports in under 100 ms: `argparse` and nothing else until a command
runs, with pydantic, ruamel and the readers imported inside the command
functions. The shell calls swage back on every TAB through `argcomplete`:
`completion bash` and `completion zsh` print its hook, the code
`register-python-argcomplete swage` would, and a completer reads the cached
feedstock and family names v1's `remember`/`recall` keep. The hook is printed
without readline's fallback, so a value swage cannot enumerate, such as
`--since 7d`, completes to nothing rather than to filenames. v1's 600-line
bash and zsh generator goes. `argcomplete` is a dependency, landing
in the same commit as the completer that first imports it, and is imported
only when the shell is asking or the hook is being printed.

---

## 13. `migrate` — frozen

v0→v1 conversion through conda-recipe-manager, with swage proofreading the
result (v1 §7, §7.0.1, §7.1). 65 feedstocks are still v0, a conversion only
rides along with a version bump, and the module has no future once the fleet
is v1. It carries over as-is, tests included, and is the first thing v2
deletes when `needs-migration` reaches zero. It is not rewritten or
simplified.

---

## 14. Verification: the rewrite is a measurement

v1's tests caught almost none of v1's real defects; the fleet did, within
minutes, every time (`CLAUDE.md`, "Verify against the fleet"). v2 is built
against the fleet from the first commit.

### 14.1 The reference

Before the first v2 commit, on `main` at `1.0.0`:

1. `scripts/reference.sh snapshot ~/code/swage-reference/1.0.0` copies
   every cache a plan reads from — the recorded GitHub reads, the
   archives, the name index, the dynamic-metadata builds — and the copy is
   never overwritten.
2. `scripts/reference.sh replay ~/code/swage-reference/1.0.0`, run from the
   1.0.0 tree, writes the reference `run.json` and rendered recipes into
   the snapshot's `runs/`. That run is what every v2 replay is compared
   against.
3. `scripts/compare_published.py` over the corpus feedstocks, its output
   kept beside them.

The snapshot rather than a live sweep, because a live sweep is one input
among four: the name index has a 24 h TTL and is rewritten in place, and
an archive or a dynamic build fetched during a replay is a read the
reference never saw.

### 14.2 The measure

At every v2 commit, `scripts/reference.sh replay` against the reference must
render every recipe byte-identically and land every feedstock in the same
outcome under §11.2's mapping; `scripts/compare_replay.py` names every
difference. A difference is one of three things, and the commit that
introduces it says which:

- a v2 defect, fixed before the commit lands;
- a v1 defect v2 chooses to fix, recorded in §16 with the feedstock
  named, with the reference's expectation for that feedstock amended in the
  same commit;
- a wording change v2 chooses — the collapse note, a finding's sentence —
  likewise recorded, and made fleet-wide in one commit.

A commit that cannot attribute a difference does not land.

> **Why** §10 freezes the read surface: change the argv and the reference
> cannot answer.

### 14.3 Tests

Fewer than v1's 1,408, and different in kind:

- **Corpus tests** carry over whole. `tests/corpus/` and the golden triples
  test behavior rather than internals.
- **Property tests** for §9.3: generated declarations over generated
  universes, checking the fidelity property, idempotence (planning the
  rendered recipe renders the same bytes), and the ordering invariants.
- **Writer invariant**: every byte outside an owned region unchanged, over
  the corpus.
- **Wording budgets** (§3.3), over the corpus and the recorded runs.
- **Reader tests** carry over per reader.
- **Unit tests of internals** — `_existing_conditional`,
  `_with_expansion_markers`, `_from_split` — do not carry over, because the
  internals do not. Their fixtures are mined for corpus cases where they
  encode a real feedstock's shape, which most do.

`pixi run check` stays the gate on every commit, and its exit status is what
is checked, never its text.

---

## 15. The order of work

One branch per step, each a pull request against `main`, each green, each
measured by §14.2. Steps 1–3 carry the risk and the payoff and go first;
nothing after them changes a rendered byte.

1. **Freeze.** Tag `1.0.0` with the entry-points branch in. Record the
   reference. Move v1's `DESIGN.md` to `docs/design-v1.md`; this file
   becomes `DESIGN.md`; extract `docs/conda-forge.md`. Copy Polaris's
   writing skills into `.claude/skills/` and point `CLAUDE.md` at them. No
   code.
2. **The grid.** `plan/grid.py` implements §9.2–9.3 and replaces
   `reconcile`, `split_by_environment` and `split_by_platform` in one
   commit; `plan/specifiers.py` holds the specifier arithmetic they shared.
   The collapse note, the selector table and the condition spellings are
   copied, not rewritten. `plan_section` builds the `Universe` from the
   parameters it already takes. Measured: byte-identical.
3. **The `Output` value.** `plan/output.py` derives §9.1, and `plan_section`
   takes one `Output` in place of the ten parameters that describe it.
   Measured: byte-identical.
4. **Findings and the decision.** `plan/findings.py` replaces `gates.py`;
   §9.8's `Decision` replaces the verdict logic in `consider` and `update`.
   Measured: same outcomes under §11.2.
5. **The record.** `run/record.py`; one constructor; renderers read it.
   `schema: 2`, with the v1 mapping. `explain` and `trust` run over the 580
   recorded v1 runs and must not fail on any.
6. **The pipeline.** §12.2 replaces `consider.py`, `audit.py`'s planning
   half, and `status.py`'s re-plan.
7. **Wording.** The comment trailer, the budgets and their test (§3).
8. **Config policies.** §5.3, in two commits: the 22-file config edit, then
   the loader.
9. **Startup and completion.** §12.3.
10. **Shims.** `--execute`, the renamed outcome, and docstrings that
    restate the design (§3.1, last rule).

Steps 2 and 4 are one branch each and are not split further: a
half-migrated plan layer has two answers to every question and cannot be
measured. Step 3 is its own branch because it changes twenty call sites in
the tests and no rendered byte.

---

## 16. Decisions

Rejected alternatives and decisions forced during v2, one paragraph each,
cited from where they apply. Each names the feedstock or sweep that forced
it and the commit that carried it.

- **`proposed` merged into `needs-review`** (§9.8, §11.2). The two buckets
  were "pushed, approve the diff" and "pushed, answer a question", and a
  maintainer does the same thing with both: looks. The `reason` column keeps
  the distinction where it is read.
- **A comment trailer instead of a link on first mention** (§3.1). Polaris's
  convention, adopted so that everything posted under the maintainer's
  account by a tool says so in the same place every time.
- **Reachability before refusal, on every model** (§9.2 step 4). v1's
  noarch path refused a platform marker before asking whether the
  declaration reached any python; its arch path asked first, for `pyodps`.
  The grid drops an unreachable declaration first on every model. No
  feedstock in the 1.0.0 reference distinguishes the two. Commit
  "Reconcile upstream's markers over one grid".
- **`built_everywhere` erases both axes on every model** (§9.2 step 2).
  v1's per-platform path erased only the machine axis and kept a platform
  comparison as a real distinction. The key says both are about upstream's
  wheel matrix, and the grid takes both as true wherever it is asked. The
  three entries in `config/` are on `ONE` and `PER_CELL` outputs, where v1
  already erased both. Same commit.
- **The widest declaration is chosen over the whole universe** (§9.3
  step 7). v1 grouped declarations by where they held within one artifact
  on the per-platform path and over the whole grid elsewhere. Per artifact,
  two declarations gated on different pythons but both reaching one cell
  would have been read as alternatives and stopped for having no widest;
  over the universe they are the conjuncts they are. Same commit.
- **`overruled_constraints` settles nothing on a per-cell artifact** (§9.3
  step 7), as in v1. A contradiction on one build is upstream contradicting
  itself about that build, which no bound in config can decide; the entry
  is for one artifact serving a range.
- **The rung is a parameter of the decision, and the comment still names
  it** (§9.7, §9.8, §11.3). v1 listed the rung as a check, G6, and its
  sentence as one bullet among the findings; on a `propose` push with nothing
  found that bullet was the whole list. §11.3's comment is `said` halves
  only, which on such a push is an empty list under "because:". So the rung's
  sentence stayed a bullet, in the position G6's had, until step 7 settled
  the comment's shape. Commit "Rename the outcomes to §11.2's thirteen".
- **The rung is a sentence of the comment's body, and a conversion's comment
  omits it** (§3.2, §11.3). The list is what was found, and the rung is not a
  finding; a conversion is never labeled whatever the rung, so naming it
  there answers nothing. The body fits §3.2's 55 words with the list and the
  trailer excluded, and a conversion's rerender request counts as body.
  Commit "Say the rung in the comment's body and sign the comment".
- **The terminal line is the check's sentence and the first finding's
  subject** (§3.2, §11.3). v1 printed the first finding's whole sentence, up
  to 320 characters, and counted the rest of that check's findings; the line
  now reads ``a requirement is not accounted for: `six >=1.11.0` (+2 more
  findings)``, counting every other finding, and `explain` holds the
  sentences. A stop's first line is the line, without the feedstock's name
  where the message opens with it, and a config paragraph -- `unmaintained`,
  a `manual` or `none` upstream's `reason` -- is the stop rather than the
  line, so `explain` prints it under STOPPED and then the verdict. Six
  checks' sentences were shortened to fit, and `cross-build-copy`'s subject
  is the output rather than the section phrase. Every `needs-review`,
  `not-read` and `skipped` feedstock in the reference moves. Commit "Print
  twelve words beside a feedstock".
- **The `Plan` carries its recipe and its release, and no `rechecks`**
  (§9.8). `unchanged` is a comparison with the recipe's text, the record
  quotes the recipe's lines beside the plan's, and the release's name is
  what the commit message and the comment say; v1 threaded the two beside
  the plan through `PlannedRecipe` into every consumer. The `recheck`
  findings are what the design's `rechecks` field would have held, read off
  the sections' overrides. Commit "Produce §9.8's Plan, findings and
  rendering included".
- **The decision takes "no pull request" as a parameter** (§9.8). `audit`
  bucketed with a `readiness()` of its own and asked `decide` only for the
  action; the two differed in one fact, that nothing waits on CI where
  there is no pull request, and in a floor at `needs-migration` applied
  by hand. One function with the fact as a parameter is what §12.2's one
  pipeline can call. Commit "Decide an audited feedstock through decide()".
- **`record()` takes the outcome, not only a decision** (§11.1). The
  five-argument form assumed every record follows a decision; 64 of the
  reference sweep's 488 records are stops with no plan behind them, and a
  `Decision` for a stop would carry a sentence the record derives from the
  stop itself. The plan carries what four of the old keywords did. Commit
  "Run scan, update, status and audit through one pipeline".
- **No `exclude` key, and no `excluded` on the `Output`** (§5.1, §9.1). v1
  §3.3.13 designed `outputs[].run.exclude` and it was never implemented. The
  omissions it was written for are `skip` entries on `airflow`, with the
  reason as a comment, and a dependency conda-forge lacks is `not_packaged`.
  A field with no key to fill it would be read by nothing. Commit "Plan each
  section from one Output value".
- **Four checks said their remedy inside `said`** (§3.1, §9.7).
  `unclassified-extra`, `orphaned-output`, `computed-dependencies` and the
  dropped-extra case of `unresolved-name` named `supported`, `skip`,
  `extras_as_outputs.supported`, `dynamic_dependencies: trust`, `name_map` and
  `embedded_extras` in the half a comment publishes, as v1 did. Each is now
  the finding's `remedy`, and `said` says what is wrong in the recipe's and
  upstream's terms. Every feedstock those checks hold moves in the reference,
  `azure-synapse-artifacts`, `snakebite-py3` and `morefs` among them. Commit
  "Keep the remedy out of the half a comment publishes". `test-matrix` named
  `test_matrix` the same way, and moved when the budget test found its
  sentence at 36 words; commit "Measure every surface against §3.2".
- **`source_versions: never` is `review`, not a third value** (§5.3). The
  section's first draft said absence means off, which left `review` with no
  behavior of its own: swage under `never` already reports the conflict and
  holds for a person, which is what `review` means everywhere else, and §1
  allows the edit under `auto` alone. So the default is `review`, the old
  spelling reads as it, and no `source-version` finding is added for a
  rung no feedstock is on. Commit "Spell every proving-period policy
  review or auto".
- **A pull request needing no change is labeled on `trust: auto`** (§1,
  §9.8, §11.2). v1 §5.2 ruled the label out on that path, and the reason it
  gave was that the path pushes nothing. `yandexcloud` reached it on
  2026-09-23 and showed the rule up: the rung had never been read in the
  `unchanged` branch, so a blessed feedstock and a `never` one were told
  the same thing. Labeling there merges what the checks passed on, inside
  the window v1 §5.2 identifies and leaves open. The bucket is `labeled`
  rather than `automerge` so that `swage status` keeps `automerge` for a
  pull request that gained a commit since swage pushed to it, which is the
  one thing its line says. Commit "Label an unchanged pull request while
  its CI runs".
- **A finding is published on a pull request needing no change** (§3.1,
  §9.8). The four comments added with this rule covered a recipe that
  already matched and nothing outstanding; a recipe that already matched
  and *something* outstanding stayed silent, which is the pull request with
  the most to say. `apache-airflow-providers-amazon` #98 is the case: the
  bot bumped 9.23.0 to 9.36.0, every requirement already matched, and three
  temporary entries had not been re-checked at the new release. Left
  silent, the maintainer merging it cannot see that the recipe deviates
  from upstream in three deliberate places. The sentence about CI was
  dropped from the draft of this comment: `merge_check` is None wherever a
  finding holds, so swage does not know. Commit "Publish what a matching
  recipe is still held by".
- **An extra transcribed into `run_constrained` is reported, not classified**
  (§9.7, §9.8). G9 withheld the push until every entry was associated with
  an upstream extra, which asked "which extra is this?" when the question
  worth asking is why an extra's bound applies to environments that never
  requested it. `litellm` forced it: its `google` extra transcribed as
  `google-cloud-aiplatform ==1.133.0` made the package uninstallable
  alongside `apache-airflow-providers-google`, and upstream relaxed the
  bound one release later. swage detects the transcription itself by
  reading upstream's extras, so the config key stopped being the evidence
  and became the decision. The check is gone; ten entries across
  `dnspython`, `pyjwt` and `grpc-interceptor` are reported, and the four
  deliberate bounds on `gdal`, `proj.4` and `google-re2` are silent. Commit
  "Report a run constraint transcribed from an upstream extra".
- **The rung is published as behavior, not as a key** (§3.1). The comment
  said "`trust` is `propose` for this feedstock, which leaves the label to a
  person", and `CLAUDE.md` offered that as the example of a sentence a
  maintainer can act on, "because it names a real key in a real file they
  can go and edit". The file is in this repository, which the reader of a
  conda-forge pull request cannot edit and has never seen, so the premise
  held for the maintainer running swage and not for the audience the test is
  about. "swage does not have permission to add automerge" was considered
  and is worse: it names a mechanism the reader does recognize, GitHub's,
  and sends them looking for a problem that is not there. The sentence is
  now "swage is set to leave the label to a person on this feedstock", and
  §3.1's rule holds without an exception. Commit "Say what the rung does
  rather than what it is called".
- **A pull request that needed no change is commented on** (§3.1, §9.8).
  v1 commented only where something was outstanding, which left the check
  unrecorded on exactly the pull requests where no commit records it: 60 of
  the 119 distinct pull requests in the six weeks to 2026-09-23. A
  maintainer approving one of those had nothing to point at for what had
  been checked, and a co-maintainer had no way to know it happened -- 73 of
  the 99 v1 feedstocks readable locally have more than one maintainer. The
  same gap covered a pushed-and-armed pull request, whose label was the only
  trace that swage rather than a person armed it. Commit "Say on the pull
  request that swage checked it".
- **`is_known` is not a shim** (§2.2, §11.1, §15 step 10). §2.2 listed it
  among the shims to drop, and §15 scheduled that for the last step. It is
  the test behind §11.1's rule that an outcome this swage has no row for is
  kept, printed in a bucket of its own and counts as needing a person,
  which v1 §9 argues and the 580 recorded runs exercised when
  `nothing-to-reconcile` became `not-reconciled`. Dropping the function
  would drop the rule, so both lists name only `--execute` and the renamed
  outcome. Commit "Say which shims step 10 drops".
- **An exclusion outside the collapsed range is dropped** (§9.3 step 8,
  §9.6). v1 rendered every `!=` the intersection held, wherever the bounds
  had ended up. `pymilvus` excludes eight grpcio releases below python 3.14
  and asks for `>=1.75.1` above it, so the collapse wrote a floor above all
  eight and then refused them one at a time -- a line that rules nothing out
  and reads as distrust of releases it cannot install anyway. A dropped
  exclusion is also one the note no longer names. Commit "Drop exclusions
  that the bounds already rule out".
- **The admin service's pull requests can be pushed to** (§10). v1 §3.4.1
  recorded that `conda-forge-admin` forks with `maintainer_can_modify`
  false, so swage could read and plan its bumps but never write to them,
  and left "a bump swage cannot write to deserves its own verdict" as open
  work. The flag describes the pull request, not the fork: it reads false
  on every merged one whoever filed it -- the autotick bot's included,
  which swage pushes to routinely -- and true on every open one, including
  all forty of the admin service's sampled across conda-forge on
  2026-09-23. Reading it after a merge is where the claim came from.
  swage has pushed to an admin bump and did not notice: `isschecker` #5
  carries "Reconcile recipe dependencies with upstream metadata", and no
  push refusal appears among 4,270 recorded failures. No code acted on the
  claim -- one push path already serves both accounts -- so the verdict it
  called for is not work that needs doing. Commit "Drop the claim that
  admin bumps cannot be pushed to".

---

## 17. Open questions

Each waits on something that has not happened. It is recorded so the
question is already asked when it does.

1. **`PER_PLATFORM` with a machine marker.** v1 stops. The grid could
   translate it: `if: win and arm64` inside a per-platform noarch artifact
   is expressible. Not done in v2 because no feedstock asks for it, and a
   rule with no feedstock behind it is a rule nobody can verify.
2. **When `migrate/` goes.** At `needs-migration: 0` or at v2.0.0, whichever
   is later. 65 feedstocks is a year of bot bumps.

---

## 18. Where v1's rules live

For a reader who wants the argument, by v1 section:

| rule | v1 |
|---|---|
| automerge is dispatched by CI status events; push before label | §2, §2.1, §2.2 |
| the recipe model, comment preservation, splicing | §3.1 |
| name resolution layers; a dependency conda-forge lacks | §3.2, §3.2.3 |
| the noarch collapse and its note | §3.3.1 |
| the arch translation; `match(python, …)`; `else:` | §3.3.1.1 |
| contradictions; `overruled_constraints` | §3.3.2, §3.3.2.1 |
| `python_min` from the pull request, never fetched | §3.3.3 |
| platform and machine markers; the fixed target set; the selector table; `noarch_platforms` | §3.3.4 |
| `built_everywhere` | §3.3.4.1 |
| one output choosing whether it is noarch | §3.3.5 |
| which lines swage owns; build strings; pinned `host` lines; the mpi pair | §3.3.6 |
| cross-compilation blocks; `pure_python_build_tools` | §3.3.6.1 |
| three kinds of removal; `retire` | §3.3.7, §3.3.8 |
| `run_constrained` is read, never authored | §3.3.9 |
| attribution's six answers; the two halves of a finding | §3.3.10 |
| an extra that disappears; self-referential extras; `exclude`; `constraints` | §3.3.11–§3.3.14 |
| discovery; which pull request | §3.4, §3.4.1 |
| API to read, clone to write | §3.5 |
| the readers | §3.6–§3.6.8 |
| the python test matrix | §3.7 |
| the config keys | §4, §4.1 |
| path A and path B; the refusals; the trust ladder | §5–§5.5 |
| ordering and formatting; maintainer comments | §6, §6.1 |
| v0→v1 | §7, §7.1 |
| the commands; `draft`; `audit`; `trust`; `completion` | §8–§8.4 |
| output; `run.json`; `explain` | §9–§9.2 |
