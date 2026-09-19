---
name: review-comments
description: Write a review comment, review findings, or a reply to review feedback on a swage pull request. Use when reviewing code, reporting what a sweep of someone else's branch turned up, or answering a reviewer's question.
---

# Review comments

The reader is deciding what to change.

- Put each finding as an inline comment on the line it concerns, one point
  each. That is why review bodies are short.
- The review body summarizes: what you ran, and the verdict. Two or three
  sentences.
- Use a list in the body only for requests that span files.
- No section on what already works. One line for all of it, if any.
- Say what you could not check.
- A sweep finding names the feedstock and quotes the rendered line, by
  requirement name *and* constraint, never by which lines appear.

## Calibration

Polaris's numbers, measured over its review comments from 2023. Review
bodies run 14 words at the median, 55 at the ninetieth percentile, and 239
at the longest. Inline comments run 22 to 32 words at the median and 170 at
the longest. A recent agent-written review there ran 1,117 words, with the
findings starting 444 words in.

## Enough

Inline, one point and a suggestion (Polaris):

> Since a user doesn't have control over what tests are in a test suite, I
> think we should just remove the `default` test case from the test suite.
> This might be appropriate to have in the developer's guide instead.

In the body, in this repository's terms (constructed):

> Replayed against the reference: 487 of 488 byte-identical. `include-what-you-use` loses `${{ llvm_version }}` from `llvmdev` and `clangdev` — inline below. I could not check the live `--feedstock` path.

## Too much

Four paragraphs of "what works", a heading on how the review was done, and
then each finding's mechanism traced — a static field computed once,
attached with `addField()` like any other, so every reduction carries a
copy — before the reader learns what to change.
