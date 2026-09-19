---
name: testing-comments
description: Write a Testing comment on a swage pull request, recording what was run and what the results were. Use after running the check, a sweep or a live feedstock audit for a pull request.
---

# Testing comments

What you ran, and whether it passed. In this repository that is three
things, in order: `pixi run check`, the offline sweep, and the live
`--feedstock` audit where a code path touches GitHub.

- `pixi run check`: its exit status and the test count. Never a grep of its
  output.
- The sweep: which command, against which reference, how many feedstocks
  differed, and every difference attributed by name. "Byte-identical" is a
  claim about all 488.
- The live audit: which feedstock, and its outcome.
- Use a table only when several runs are compared.
- Do not restate in prose what a pasted result already shows.
- Failures unrelated to the branch go under their own heading at the end.

## Calibration

Polaris's Testing comments from 2023 run 21 to 43 words. A recent
agent-written one there ran 606 words.

## Enough

> ## Testing
>
> `pixi run check` green, 2,158 passed. `swage audit --all --cached` against the 1.0.0 reference: 488 of 488 byte-identical, same outcomes.

> ## Testing
>
> `pixi run check` green. Replayed against the reference: 486 of 488 identical. `cacts` and `pyodps` gain an entry point upstream declares, which is the change. `swage audit --feedstock cacts` live: `needs-review`, one finding, the new line.

## Too much

A table of the twelve differences, then the same twelve described again in
prose, then a paragraph on what a clean comparison would need. The table
was enough.
