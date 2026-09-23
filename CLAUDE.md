# swage

A tool for maintaining ~490 conda-forge feedstocks: it reconciles recipe
dependencies against upstream metadata, keeps formatting consistent, and gets
routine updates merged without a human in the loop.

**Read `DESIGN.md` first** — its §1, "The model, in one page", before anything
else. That section states the assumptions the rest of the document elaborates,
and exists because one of them ("conda-forge builds one `noarch: python`
package") sat buried inside a reconciliation rule and quietly became the scope
of the tool. The rest of the file is the v2 specification: the rules, stated
once, in the terms the code uses.

The argument for each rule is in **`docs/design-v1.md`**, v1's design, frozen
at the `1.0.0` tag. `DESIGN.md` cites it as `v1 §3.3.7`, and so does this
file. **`docs/conda-forge.md`** collects the facts about conda-forge both rest
on — automerge's dispatch, the required checks, the two merge refusals — which
are not obvious from the outside and not documented anywhere else. In `src/` and
`tests/`, a citation of the form `design-v1.md 3.3.7` is the same file: v1's
code cites v1's design, and a module rewritten for v2 cites `DESIGN.md §9`.

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

`G1`-`G15`, "path A" and "path B", and `v1 §3.3.7` are how this project
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
who has never read DESIGN.md can act on the sentence. "swage is set to leave the
label to a person on this feedstock" passes, because it says what happened and
who acts next. `G6` fails, and so does `trust: propose`: the file holding that
key is in this repository, so a reader of a conda-forge pull request cannot open
it. Naming a config key is for swage's own surfaces -- the terminal, `explain`,
`run.json` -- where the reader is somebody who can edit it.

## Everything pushed to GitHub is published under the maintainer's name

Pull request titles and bodies, review comments, issue comments and commit
messages all appear under the account that pushes them, which is theirs.
**Write them as their author would**, impersonally, about the work rather than
about who asked for it.

That rules out a whole class of phrasing an agent reaches for by default:
"the maintainer decided", "as requested", "you asked for", "per your review".
Under the maintainer's name those read as them quoting somebody else about
their own repository. The comment closing #129 said "the maintainer's reading
is that a constraint difference in either direction is drift", which is exactly
the defect -- the reasoning was theirs, and the sentence attributed it to a
stranger.

Reasons that came out of a conversation get stated as conclusions. A decision
reached by asking the maintainer a question is simply the decision; the asking
is not part of the record. Where the record genuinely needs it -- a bound whose
justification came from somewhere outside the repository -- name the source,
not the exchange: "the recipe's own comment says", "upstream's pyproject.toml
carries it commented out".

The same goes for anything swage itself publishes, which is
[the section above](#design-shorthand-stays-inside-the-design)'s subject from
the other side: a comment on a feedstock pull request is read by people who
know nothing about this project and everything about that one.

## Writing for human readers

These rules apply to anything a colleague reads: pull request descriptions
and comments, reviews, issues, plans, `DESIGN.md`. Not source comments or
commit messages, where a reader who wants the mechanism is already in the
right place. Per-artifact rules, calibration and worked examples are in
`.claude/skills/<artifact>/SKILL.md`; Claude Code loads the matching one, and
other agents read it before writing. What swage itself writes to people — the
feedstock comment, a finding's sentence, the terminal — is `DESIGN.md` §3,
which is the same rules with a test behind them.

The rules and their numbers are Polaris's
(`~/code/e3sm/polaris/main/AGENTS.md`), because this repository cannot
supply its own: nearly every word in it was written by an agent, so its
medians describe the problem. Polaris's were measured over colleagues'
writing before any agent wrote there.

Assume the reader is a developer who knows conda-forge and has read
`DESIGN.md` §1, and no more.

Never repeat an explanation of something unchanged that `docs/` or
`DESIGN.md` already gives; link to it. Explain where there is a change, or a
nuance the discussion turns on.

Write less; do not pack the same content into denser sentences. Keep
headings, tables and links. The problem is length.

- **Lead with the answer.** The first two sentences say what you found,
  changed, or propose. Setup and reproduction go last.
- **One point per paragraph, and few paragraphs.** Colleagues write one to
  three per comment. Say each thing once.
- **Do not narrate the mechanism.** The chain of calls, and why the fix is
  right, go in the commit message. Here, say what broke and where to look.
- **Cut clauses that qualify rather than inform**, and any sentence whose
  only job is to justify the one before it.
- **Use backticks about half as often as feels natural.** They are for what
  a reader would type or grep. Code blocks hold artifacts you did not write,
  never authored prose.
- **One document, one decision.** Anything still relevant after this merges
  is an issue, not a comment.

On GitHub:

- **Do not hard-wrap.** Each paragraph and each bullet is one line, however
  long. GitHub wraps for display, and hard breaks make later edits show as
  reflowed paragraphs in the diff.
- Start with a paragraph saying what the pull request or issue is about,
  then sections for the detail.
- A description is not part of the branch, so it never goes in the worktree:
  a reviewer who fetches the branch would find a copy of what they are
  already reading. Write it outside the repository and pass it with
  `gh pr create --body-file`.
- Do not list commits. Do not describe testing in the description; that goes
  in its own `Testing` comment.
- An issue says what happens, what was expected instead, and enough about
  the configuration and commands to reproduce it.

Sign anything posted to GitHub on someone's behalf:

```
---

*Posted by Claude Code on @xylar's behalf. The testing, analysis and wording above are AI-authored; please check them accordingly.*
```

Name the agent, not the vendor.

## Constraints that are easy to get wrong

- **The build model is a property of each output, not of the fleet.** A
  `noarch: python` output is one package for every Python, so a
  `python_version` marker collapses to the tightest bound that holds across the
  range. An architecture-specific output is built once per Python, so the same
  marker becomes an `if: python < "3.13"` entry that says what upstream says.
  Read `build.noarch` per output; a feedstock can have both kinds.
  **Compiled feedstocks are in scope and always were.** See `DESIGN.md` §1,
  then v1 §3.3.1.
- **Push strictly before labeling, never the reverse.** conda-forge strips the
  `automerge` label if any commit lands after the `labeled` timeline event. To
  re-arm after a follow-up push, remove the label and re-add it — re-adding an
  already-present label creates no new event. See `docs/conda-forge.md`.
- **A label alone does nothing once CI has finished.** conda-forge's automerge is
  `workflow_dispatch`-only and is dispatched by CI status events. No new commit
  means no new CI means nothing will ever merge that PR. See
  `docs/conda-forge.md`.
- **swage does not merge, and there is no merge in it.** The no-changes case
  used to end in swage merging the pull request itself. GitHub refuses: merging
  one that re-renders `.github/workflows/conda-build.yml` needs a `workflow`
  scope the maintainer will not grant, and conda-smithy re-renders into 11 of
  the fleet's 14 newest bot pull requests. Those are reported as
  `READY TO MERGE` with a link, and a person presses the button. Do not add a
  merge back without reading v1 §5.2.2 first.
- **Dependency order follows upstream source order**, not alphabetical.
  `python` and `pip` come first where they apply; conda-forge-only additions form
  a separate alphabetized trailing block. See v1 §6.
- **A v0→v1 conversion is never a pull request of its own.** A feedstock still
  on the old format waits until it has a version to bump, and the conversion
  rides along with that update. `NEEDS MIGRATION` in a fleet audit describes
  those feedstocks; it is not a backlog, and converting them as a campaign is
  not work to propose. See v1 §7.
- **`conda-forge.yml` is off-limits** except during v0→v1 migration, where
  setting `rattler-build` and `pixi` is mandatory. Everything else there needs
  human judgment. See v1 §7.
- **`supported`/`skip` extras lists must be exhaustive**, on feedstocks that
  publish extras at all. An extra in neither list means swage cannot tell
  "considered and declined" from "never noticed", so the feedstock is flagged
  for review (G3) rather than merged. `skip` is how a decision not to publish
  gets recorded. A feedstock publishing no extras ignores them entirely.
- **swage never adds a `run_constrained` entry, and never adds an output.** Both
  are ways of saying "this upstream extra belongs in the recipe", and both are
  packaging decisions about CI cost and downstream benefit that no metadata
  contains. See v1 §3.3.9 and G4.
- **One output that builds both an arch and a noarch package is off limits.**
  `markupsafe` uses a `use_noarch` variable to build a compiled and a noarch
  package out of one output, with different requirements in each, so its `run`
  list holds two alternatives of the same dependency and swage would collapse
  them. That specific shape is refused before planning. **Build variants in
  general are not off limits** — three mpi builds, or one build per Python, are
  ordinary feedstocks. See v1 §3.3.5.

## Working style

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

- **Categorize the output; do not just count failures.** The value is in
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

### The fleet sweep is `--cached` by default

`swage audit --all` reads ~490 default branches, recipes and pull request lists
through `gh`, which is a subprocess apiece and about **17 minutes**. Two agents
running one at once compete for the same rate limit, and the second sweep of a
before-and-after comparison reads a fleet that has had a quarter of an hour to
move — so a feedstock somebody else changed arrives as a difference you have to
rule out by hand.

**`swage audit --all --cached` replays the reads the last sweep recorded**, in
seconds and with no GitHub calls, against the same bytes the recorded run saw.
That is what makes a comparison attributable: every difference between the two
renderings is the code's.

So the order is:

1. **The offline sweep first**, over the cached archives or recipes the layer
  eats. Seconds, no network, and it has caught the substantive design errors
  every time.
2. **`swage audit --feedstock <what you touched>`**, live, because a new code
  path has to be exercised against GitHub at least once.
3. **`swage audit --all --cached`**, to prove nothing else moved. Compare
  against the previous run's `run.json` and rendered recipes, and attribute
  every difference.

**Run a live `--all` when the cache is what you need to refresh** — when the
question really is what the fleet looks like now, and **whenever the last live
sweep is more than about a week old**. It is not the per-branch check. A
replayed audit reports the fleet as it was, says so in its own output, and must
never be quoted as current state.

A week is the rule of thumb because the bot files continuously: every replay
after that is answering with a fleet that has moved on, and the staleness is
invisible in the numbers themselves. The cache went a month unrefreshed once,
and every figure quoted in between — including the ones in the hand-off — was
of a fleet nobody had looked at.

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
- **Stage a commit's files by name; never `git add -A`.** It sweeps up
  whatever else is in the worktree -- a scratch file, a note, the leavings of
  a run that failed -- and the commit that results is not the one its message
  describes. It has already put a pull request description into a commit,
  which took a second amend to undo. `git diff --cached --stat` before
  committing says what is about to land.
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
as that code wherever the two are one change. `docs/design-v1.md` is never
edited: a finding made during v2 goes in `DESIGN.md` §16 and the commit that
acts on it. The spec and the behavior are
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
(`tests/corpus/`), because both are inputs swage's behavior depends on and
neither is reproducible from anything else; and `pixi.lock`, so CI resolves the
same environment twice running. Vendored fixtures keep their original licenses,
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
