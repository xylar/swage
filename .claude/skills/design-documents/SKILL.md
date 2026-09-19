---
name: design-documents
description: Write or revise DESIGN.md, the v2 specification. Use when adding or changing a rule, not when fixing a bug.
---

# Design documents

Long is fine. A design document is scanned and returned to, not read
straight through. What must be scannable is the specification.

- Normative statements come first in a section and stand alone. Rationale
  goes in a `> **Why:**` block below, which a reader can skip.
- A rule's argument that already exists in `docs/design-v1.md` is cited by
  section (`v1 §3.3.7`), never restated.
- Rejected alternatives and decisions forced during implementation go in
  §16, cited from the places they affect. Never re-argued in place.
- A principle is stated once. Later sections cite it by number.
- Do not pre-empt objections. Drop "worth noting", "not an accident",
  "deliberately", "exactly", "precisely", "on purpose", "the whole point",
  "this is not a stylistic preference". State the decision and let it stand.
- Open questions go in §17, never mid-paragraph.
- `docs/design-v1.md` is frozen. Nothing is added to it.

## Calibration

The rules and numbers are Polaris's (`~/code/e3sm/polaris/main/AGENTS.md`),
adopted because this repository's own writing cannot calibrate them: nearly
every word here was written by an agent. v1's `DESIGN.md` ran 65,099 words
at 34 words per sentence with 187 of the phrases above. The v2 draft ran 26
words per sentence with 12 before a pass, and 23 with none after it. Aim
for twenty and none.

## Enough

From `DESIGN.md` §5.2. A table states the rule; one sentence says what the
table is for.

> | Rule | Keys |
> |---|---|
> | **most specific wins**, whole value | `trust`, `upstream`, `extras_as_outputs`, … |
> | **most specific wins, per entry** | `name_map`, `outputs`, `constraints`, … |
> | **union across layers** | `recipe_owned.*`, `add_requirements`, `retire`, … |
>
> Layers, least specific first: `defaults.yaml`, the matching family, the
> feedstock's own file. The loader is generated from this table.

## Too much

From v1 §3.3.6, on the same subject as one bullet in `DESIGN.md` §9.4: a
rule, its discovery, a blockquote on when it was found, a second blockquote
counting the fleet, a third citing three recipes' comments, and a paragraph
on the day the rule was wrong. Eleven paragraphs for "a `host` line naming a
package `.ci_support` pins carries no version". The eleven belong in the
decision log; the sentence belongs in the design.
