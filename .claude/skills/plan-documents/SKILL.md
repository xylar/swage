---
name: plan-documents
description: Write a plan for work not yet started, for the user to approve before implementation begins.
---

# Plan documents

The reader is deciding whether to let you proceed.

- Open questions and anything needing a decision go at the top.
- The steps, in order, one line each.
- Do not justify each step. Do not list the files you will touch. Do not
  restate the codebase back.
- If a step needs a paragraph to explain, it belongs in `DESIGN.md`.
- A plan goes in the conversation, or in an untracked file at the root of
  the worktree it describes. It is never committed.

## Enough

Plans are approved in conversation, so there is no human example in this
repository to copy. The following is constructed.

> **Open:** does the grid land before or after the `Output` value, given
> that both touch `plan_section`?
>
> 1. `plan/output.py`: derive `Output` from the recipe, `.ci_support` and config.
> 2. `plan/grid.py`: the universe, the artifact grouping, one collapse.
> 3. Route `plan_section` through both; delete `reconcile`, `split_by_environment`, `split_by_platform`.
> 4. Property test for the fidelity rule; corpus unchanged.
> 5. `audit --all --cached` against the reference; attribute every difference.
