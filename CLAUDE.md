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

## Design shorthand stays inside the design

`G1`-`G11`, "path A" and "path B", and `DESIGN.md 3.3.7` are how this project
talks to itself while the design is being worked out. **None of them may appear
in anything a person reads without the design open.** That means:

- **commit messages and comments swage writes to feedstocks** -- the worst case,
  because they are published to repositories swage does not own, read by people
  who have never seen this design and would not know to look for it, and
  permanent. swage's first ever pull-request comment said
  `- **G6**: trust is 'propose', not 'auto'`, which is exactly the defect.
- **terminal output**, including `swage explain`, whose whole job is answering a
  question the reader should not have to research first;
- **`config/`**, which is reviewed as a description of ~490 feedstocks;
- **`docs/`**, and any `--help` text.

Source comments and docstrings *are* the design process and may cite it freely.
`run.json` keeps `G1` as a **field**, because a structured artifact wants a
stable key -- but it carries the plain-language title beside it, and that title
is what every renderer prints.

The test is not whether a term appears in DESIGN.md. It is whether a maintainer
who has never read DESIGN.md can act on the sentence. `trust: propose` passes,
because it names a real key in a real file they can go and edit. `G6` fails.

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
- **`supported`/`skip` extras lists must be exhaustive**, on feedstocks that
  publish extras at all. An extra in neither list means swage cannot tell
  "considered and declined" from "never noticed", so the feedstock is flagged
  for review (G3) rather than merged. `skip` is how a decision not to publish
  gets recorded. A feedstock publishing no extras ignores them entirely.
- **swage never adds a `run_constrained` entry, and never adds an output.** Both
  are ways of saying "this upstream extra belongs in the recipe", and both are
  packaging decisions about CI cost and downstream benefit that no metadata
  contains. See DESIGN.md §3.3.9 and G4.
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

## Verify against the fleet, not against your expectations

Every layer so far has shipped with bugs the tests written alongside it did not
catch and a run over real data did, within minutes. Before committing a layer,
run it over everything in `~/code/conda-forge` — the 224 v1 recipes, the 89
airflow provider `pyproject.toml`, the 8,759 installed `METADATA` files, or
whatever that layer eats. It is a throwaway script, it takes a minute, and it
has never yet come back clean on the first try.

- **Categorise the output; do not just count failures.** The value is in
  separating "correctly refused" from "out of scope anyway" from "actual bug".
  33 precondition refusals looked alarming until 32 turned out to be compiled
  feedstocks swage would never touch.
- **Make the harness read the real config.** A harness more permissive than
  reality hides bugs as readily as it invents them. Twice a sweep reported
  failures that were really the harness listing every upstream extra as though
  it were published, or treating every output as taking core dependencies; once
  it read `config/`, 15 of 22 became 22 of 22.
- **A golden comparison beats an assertion you wrote.** `tests/corpus/` holds
  what the tool being replaced actually published. Reproducing it is a claim
  about swage; passing your own expectations is a claim about your
  expectations. Two ordering rules were wrong in ways only the corpus revealed.
- **When real data disagrees, work out which side is wrong before fixing
  either.** Several "bugs" turned out to be the sweep; several "artifacts"
  turned out to be real.

## Pull requests

- **Open a draft if you intend to keep pushing to the branch.** A non-draft
  pull request with green CI reads as finished work and will be merged as such.
  Mark it ready when the layer is done, and say in the message which it is.
- **After a merge, branch again from `main`.** Continuing to push to a branch
  whose pull request has already merged silently detaches the work: the pull
  request is closed, so pushes stop triggering CI, and the failure presents as
  "CI is broken" rather than as anything to do with branching.

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
- **Every pull request targets `main`, never another branch.** A pull request
  based on a branch merges into *that branch*, so merging the base first strands
  everything above it. That is not hypothetical: the recipe layer's PR merged
  into an already-merged `phase-0` two minutes after that branch reached `main`,
  and had to be recovered by cherry-picking.

  Branching *from* an unmerged branch is fine and sometimes necessary — a layer
  that needs one already in review has to start somewhere. Do it in a **new
  worktree and a new branch**, and open its pull request against `main` like any
  other. What is never fine is continuing to push to a branch whose pull request
  is already open as a way of stacking work on it.

  **A pull request built on an unmerged one stays a draft until its base
  merges**, and its description says what it is based on. Its diff against
  `main` contains the base's commits, so merging it merges them too — which
  means a draft below it could be merged by the back door, with none of the
  review that made it a draft. Mark it ready in the same gesture that merges
  the base, not before.
- **Small commits, each one green.** Every commit must leave
  `pixi run check` passing, or `git bisect` means nothing. The grain is
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
- **Every commit an agent writes ends with the co-author trailer**, on its own
  line after a blank line, exactly:

  ```
  Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
  ```

  This is a tool that acts unattended on other people's repositories, so
  `git log` should say plainly which commits a human wrote and which one
  didn't. That matters most in the moment someone is bisecting to find out why
  swage did something surprising. Amend it in before pushing if it is missing —
  and check, because it is the easiest convention in this file to drop silently:
  64 of the first 86 commits carry it, and the ones that do not are all from a
  session where it went unwritten here and was therefore forgotten.

### DESIGN.md changes land with the code they describe

`DESIGN.md` is edited **on the branch that implements it**, in the same commit
as that code wherever the two are one change. The spec and the behaviour are
then true of each other at every point in history, which is what makes a
bisect meaningful: a commit whose code says one thing and whose spec says
another is a commit nobody can read.

This replaces a long-lived `design` branch that batched spec edits and had them
cherry-picked into code pull requests. One writer did mean no conflicts, but it
put the finding and the change that acted on it in different commits — and
`main`'s copy of the spec was permanently behind, so the file every instruction
here says to read first was the one least likely to be current.

Design work that genuinely **precedes** its implementation still belongs in a
pull request of its own, describing the decision rather than sneaking it into
unrelated work.

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
