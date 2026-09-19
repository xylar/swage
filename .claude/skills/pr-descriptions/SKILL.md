---
name: pr-descriptions
description: Write or update a pull request description on the swage repository. Use when opening a pull request or editing its body.
---

# Pull request descriptions

The reader is deciding whether to review.

- What changed and why, in a few sentences. Not how.
- Anything needing a reviewer decision goes in its own short list near the
  top, never mid-paragraph.
- A list of changed behaviors is fine. A trace of the mechanism is not.
- No commit list. No testing; that goes in a separate `Testing` comment.
- Say whether the pull request is finished or still being pushed to, and
  what it is based on if not `main`.
- Link the issue or the feedstock pull request that gives context, by full
  URL.
- Do not hard-wrap. Each paragraph and each bullet is one line.
- Several fixes usually means several pull requests.
- End with the signature `CLAUDE.md` gives, on its own line after a rule.

## Calibration

Polaris's numbers, measured over its pull requests from 2023 and 2024
before any agent wrote there; this repository has no human-written
descriptions to measure. They run 27 to 28 words at the median, 45 to 62 at
the seventy-fifth percentile, 103 to 110 at the ninetieth, and 354 at the
longest. A recent agent-written one there ran 1,167 words.

## Enough

A bug fix, stated and done (Polaris):

> When you add an input in a subdirectory and the target is also in the
> subdirectory of another step, polaris was previously incorrectly creating
> an absolute path to the input relative to the step's workdir, rather than
> the subdirectory where the symlink exists. This merge fixes that bug.

A change with a list, in this repository's terms (constructed):

> Reads an output's `build.python.entry_points` and reconciles it against the scripts upstream declares, keyed by name.
>
> - a retarget or an addition is written and noted beside the verdict
> - a drop is held once, by a new check
> - `entry_points: manual` takes a list out of reconciliation
>
> Replayed over the fleet this changes four feedstocks; the Testing comment names them.

## Too much

A description that traces the mechanism — which function reads which file,
why the fix is right, the chain of calls — has written the commit message
twice. Someone deciding whether to review does not need the cycle traced.
