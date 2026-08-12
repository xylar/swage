# swage

A tool for maintaining ~490 conda-forge feedstocks: it reconciles recipe
dependencies against upstream metadata, keeps formatting consistent, and gets
routine updates merged without a human in the loop.

**Read `DESIGN.md` first.** It is the specification, and it carries findings
about conda-forge's automerge internals that are not obvious from the outside
and not documented anywhere else.

## Safety constraints

swage writes to real conda-forge feedstocks that other people depend on. During
development:

- **Never push to, comment on, label, or merge a real feedstock PR** unless the
  user explicitly asks for that specific action. Tests use fixtures; manual
  verification uses `--dry-run`. There is no "just this once to check it works."
- **Never commit credentials.** Auth comes from the `gh` CLI at runtime.
- The repo is **public**. Anything committed is world-readable immediately and
  may be indexed even if later removed.

## Constraints that are easy to get wrong

- **Push strictly before labeling, never the reverse.** conda-forge strips the
  `automerge` label if any commit lands after the `labeled` timeline event. To
  re-arm after a follow-up push, remove the label and re-add it — re-adding an
  already-present label creates no new event. See DESIGN.md §2.
- **A label alone does nothing once CI has finished.** conda-forge's automerge is
  `workflow_dispatch`-only and is dispatched by CI status events. No new commit
  means no new CI means nothing will ever merge that PR. This is why swage merges
  directly in the no-changes case. See DESIGN.md §2.1 and §5.2.
- **Dependency order follows upstream source order**, not alphabetical.
  `python` and `pip` come first where they apply; conda-forge-only additions form
  a separate alphabetized trailing block. See DESIGN.md §6.
- **`conda-forge.yml` is off-limits** except during v0→v1 migration, where
  setting `rattler-build` and `pixi` is mandatory. Everything else there needs
  human judgement. See DESIGN.md §7.
- **`supported`/`skip` extras lists must be exhaustive.** An upstream extra in
  neither list is an error that stops the feedstock — that is the mechanism
  preventing a new upstream extra from silently vanishing from a recipe.
- **A feedstock with a build-variant switch is off limits.** `markupsafe` uses a
  `use_noarch` variable to build both a compiled and a noarch package from one
  recipe, with different requirements in each. swage assumes one noarch artifact
  and would collapse the two into a single wrong answer, so it refuses the
  feedstock before planning. See DESIGN.md §3.3.5.

## Working style

- Deliver what was asked at the scope intended. Make routine judgment calls
  without asking; check in only when different readings lead to materially
  different work. If you think the ask is wrong, say so in a sentence and
  proceed as asked rather than quietly redefining the task. Report completion
  only when the work is actually done; if something can't be finished, do the
  rest and say plainly what is missing.
- Don't add features, abstractions, or error handling beyond what the task
  needs. Don't validate against states that can't occur. Validate at system
  boundaries — the GitHub API, upstream metadata, user config — and trust
  internal code in between.
- Subagents are for genuinely independent, sizeable tracks. Work you could
  finish in a handful of tool calls should be done directly, and verification
  belongs in the main loop rather than a subagent.

## Layout

`~/code/swage/main` is the main worktree; branches are siblings at
`~/code/swage/<branch>/`. The feedstock checkouts swage operates on live in
`~/code/conda-forge/` — swage deliberately does not live there.

## How work lands

A tool that takes unattended actions on other people's repositories should have
a history you can bisect when one of those actions turns out to be wrong. That
is what these conventions are for.

- **One branch per layer, in a worktree**, always opening a pull request against
  `main`. Pushing to `main` directly is possible with admin rights and is
  deliberately kept possible, but it is an escape hatch, not a shortcut — an
  agent should never take it without being asked for that specific action.
- **Branch from `main`, never from another branch.** A stacked pull request
  merges into *its own base*, so merging the base first strands everything above
  it. That is not hypothetical: the recipe layer's PR merged into an
  already-merged `phase-0` two minutes after that branch reached `main`, and had
  to be recovered by cherry-picking. Layers within a phase touch different files
  and merge in any order, so stacking buys nothing.
- **Small commits, each one green.** Every commit must leave
  `pixi run -e dev check` passing, or `git bisect` means nothing. The grain is
  one capability plus the tests that prove it — not a checkpoint at the end of a
  session.
- **A dependency lands in the same commit as the first code that uses it**, never
  ahead of it. A commit adding a dependency nothing imports proves nothing about
  it.
- **Data and the code that reads it are separate commits** where the data stands
  on its own. `config/` is reviewed as a description of ~490 feedstocks; the
  loader is reviewed as code.
- **Commit messages** use an imperative subject and a body explaining *why*
  rather than restating the diff. Findings that took work to establish belong in
  the commit that acts on them.

### DESIGN.md changes are batched

`DESIGN.md` is edited **only** on the long-lived `design` branch
(`~/code/swage/design/`), never on a code branch. One writer means no conflicts;
the cost is that `main`'s copy lags, which is paid down by merging the branch at
phase boundaries or whenever code needs the spec current. Design work that
precedes its implementation accumulates there rather than generating a pull
request of its own.

### Branch protection on `main`

Requires a pull request and the four CI jobs, blocks force pushes and deletion,
and requires **zero** approving reviews — a solo maintainer cannot approve their
own pull request, so requiring one would be a lock-out rather than a safeguard.
Squash merging is disabled at the repository level, because it would collapse the
small commits above into one per pull request and undo the reason for making
them.

### What is and is not committed

Committed: the quirks database (`config/`) and the golden-test corpus
(`tests/corpus/`), because both are inputs swage's behaviour depends on and
neither is reproducible from anything else; and `pixi.lock`, so CI resolves the
same environment twice running. Vendored fixtures keep their original licences,
recorded in `tests/corpus/README.md`, rather than inheriting swage's.

Not committed: run artifacts, the pixi environment, and anything swage generates
— everything durable lives in git or in the feedstocks themselves.

## Golden-test corpus

The existing bespoke tools left behind input/expected-output triples that are
the highest-value regression tests available:

```
~/code/conda-forge/airflow-feedstock/providers/providers-<name>_<version>/
    pyproject.toml     upstream metadata  (input)
    old_recipe.yaml    recipe before      (input)
    recipe.yaml        recipe after       (expected output)
```

`~/code/conda-forge/google-cloud/feedstocks/` has checkouts covering that family.
Curate a subset into `tests/corpus/` rather than reading from those paths at test
time — they are working directories and will change.
