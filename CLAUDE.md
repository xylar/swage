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
