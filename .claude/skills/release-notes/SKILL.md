---
name: release-notes
description: Write or revise the notes for a swage GitHub release. Use when preparing a release or rewriting an existing release's description.
---

# Release notes

The reader runs swage, or maintains a feedstock it writes to, and wants to
know what is new and what will break when they upgrade. The notes replace
GitHub's generated list of pull requests, but every pull request still
appears somewhere.

## Structure

```
<2 or 3 sentences: what this release is about>

## Major features
### <Theme>
<2 to 4 sentences.> (#N, #M)

## Minor features
### Reconciliation
### Recipes
### Commands and output
### On feedstock pull requests
### v0 to v1 conversion
- <What changed> (#N)

## Breaking changes
## Bug fixes
## Fleet config
## Design and documentation
## Developer tools and CI
## Dependencies
## New contributors
**Full changelog**: https://github.com/xylar/swage/compare/<prev>...<tag>
```

Drop an empty section or subsection. Add a subsection under Minor features
when a new area needs one. A first release has no Breaking changes and
links `https://github.com/xylar/swage/commits/<tag>` instead of a compare.

The release title is the tag, `2.0.0`, with no `v`.

## Gathering

- Take the list of pull requests from GitHub:
  `gh api repos/xylar/swage/releases/generate-notes -f tag_name=<tag> -f target_commitish=main -f previous_tag_name=<prev> -q .body`.
  Keep its New contributors list.
- Read each pull request's title and description with `gh pr view`, or all
  of them at once with
  `gh pr list --state merged --limit 500 --json number,title,body`.
- Take dependency versions from `pixi.toml` and `pyproject.toml` at the two
  tags, not from titles: `git diff <prev> <tag> -- pixi.toml pyproject.toml`.
- Find added, renamed or removed commands and flags by running
  `swage --help` and each command's `--help` at both tags, and config keys
  with `git diff <prev> <tag> -- src/swage/config docs/config`.

## Sorting

- A major feature is a theme that spans several pull requests or a
  capability someone would go looking for: a new command, a new kind of
  feedstock swage can handle, a change in what it publishes. The line is
  blurry; do not agonize over it.
- Breaking changes: removed or renamed commands, flags and config keys,
  a `run.json` schema bump, and anything swage now does differently on a
  feedstock that its maintainers would notice. A pull request can appear
  both here and in a feature or fix section.
- Bug fixes say what was wrong, not how it was fixed. Flag a fix that
  changes what swage writes to a recipe.
- Fleet config collapses the pull requests that only touch `config/` into
  a few lines by kind: feedstocks given more or less trust, feedstocks
  described or held, name mappings. Name the feedstocks; the reader may
  maintain one.
- Design and documentation holds `DESIGN.md`, `docs/` and `CLAUDE.md`
  changes that shipped without code. A spec change that landed with its
  code is part of that code's line.

## Writing

- One line per bullet and per paragraph; GitHub release notes are not
  hard-wrapped.
- Rewrite titles as what changed for the user, not what the pull request
  did.
- No design shorthand: no `G6`, no "path A", no `v1 §3.3.7`. The notes
  are read without `DESIGN.md` open (`CLAUDE.md`).
- Every pull request so far is @xylar's, so the notes leave authors out.
  Credit a pull request by anyone else with their handle, and credit the
  human for one an agent opened.
- End with the signature from `CLAUDE.md`.

## Checking

Every number from the generated notes must appear in the draft:

```
gh api repos/xylar/swage/releases/generate-notes \
    -f tag_name=<tag> -f target_commitish=main -f previous_tag_name=<prev> -q .body \
  | grep -o 'pull/[0-9]*' | cut -d/ -f2 | sort -nu \
  | while read n; do grep -qE "#$n\b" notes.md || echo "missing #$n"; done
```

Show the draft to the user before posting it. A new release goes up as a
draft with `gh release create <tag> --draft --target main --title <tag>
--notes-file notes.md`; publishing it creates the tag at the target's head,
so publish only once `main` is the commit to tag. An existing one is
rewritten with `gh release edit <tag> --notes-file notes.md`.

## Example

Polaris's 1.0.0 and 1.1.0 notes
(https://github.com/E3SM-Project/polaris/releases) are the reference for
tone and length: 1.1.0 covers about a hundred pull requests in 1,400 words.
From them:

> ### Tasks and suites across components
> Each component now sets up the steps it owns, so a task or suite can
> include shared steps from another component with that component's config
> options. Helpers called by several tasks can now share one config, and
> the Coriolis helpers move into the framework so any component can use
> them. (#704, #686, #692, @xylar)

> ## Bug fixes
> - Make the `sphere_transport`, `geostrophic` and `merry_go_round` viz
>   steps work for both models; filament diagnostics now use the configured
>   time, which changes their values (#767, @xylar)
