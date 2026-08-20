# A walkthrough

The commands each explain themselves. What follows is the loop they belong to:
find the backlog, assemble one decision, write it down, and check it landed.

Everything here is read-only until the last step, and the last step is opt-in
per feedstock.

## 1. Find the backlog

`swage audit` asks what swage would do to a feedstock if the bot filed a pull
request for it tomorrow. It reads the feedstock where it lives, fetches the
release the recipe names, and reconciles — without needing an open pull request
to react to.

```console
$ swage audit --family microsoft-kiota
```

```
swage audit --family microsoft-kiota    2026-08-15 16:55            (7 audited)

  PROPOSED (5)         swage would push this and leave the labeling to you
    microsoft-kiota-http                     +4 -2 in the recipe
    microsoft-kiota-serialization-form       +1 -1 in the recipe
    microsoft-kiota-serialization-json       +1 -1 in the recipe
    microsoft-kiota-serialization-multipart  +1 -1 in the recipe
    microsoft-kiota-serialization-text       +1 -1 in the recipe
  UNCHANGED (2)        the recipe already matches the release it names

  run: ~/.cache/swage/runs/2026-08-15T16-55-34/
```

`--all` sweeps every feedstock you maintain, which is around 490 of them and
about twenty minutes. The section that matters is **NEEDS REVIEW**: that is the
config backlog, and it is what the rest of this page is about.

```console
$ swage audit --feedstock pyjwt
```

```
swage audit --feedstock pyjwt    2026-08-15 16:55                   (1 audited)

  NEEDS REVIEW (1)     a decision is needed -- `swage draft <feedstock>` assembles it
    pyjwt  run_constraints `cryptography` is associated with no upstream extra; add it
           to run_constraints in config -- `extra: <name>` if it tracks one, `extra:
           null` if the bound is deliberate and tracks nothing
           note: upstream 2.13.0 declares extra 'crypto', which no output draws on
```

Every run also writes a `run.json` under `~/.cache/swage/runs/`, which is the
machine-readable record of everything it decided. `swage explain <feedstock>`
renders one feedstock out of it, and `swage status` reports what became of the
pull requests earlier runs pushed to.

## 2. Assemble one decision

`swage draft <feedstock>` puts everything the decision needs in one directory:

```console
$ swage draft microsoft-kiota-http
```

```
  workbench: ~/.cache/swage/drafts/microsoft-kiota-http
    FINDINGS.md    what is undecided, and what upstream says about it
    recipe.diff    what swage would change
  copy the config in with --execute once you have decided
```

| File | What it is |
|---|---|
| `FINDINGS.md` | what is holding the feedstock, which key answers each thing, and the evidence |
| `recipe.yaml` / `recipe.swage.yaml` | the recipe now, and as swage would write it |
| `recipe.diff` | the two, unified |
| `upstream/` | the metadata swage actually read |
| `config.yaml` | a config file drafted as far as it can be without deciding anything |

`FINDINGS.md` is the one to open. It quotes every mention of the disputed name
in the metadata swage read, and the recipe's own line with whatever comment
sits above it — which is often the whole answer, written by whoever made the
decision the first time.

`swage draft --family <name>` drafts a whole family and reports the questions
they share. It refuses `--execute`, because a family's answer usually belongs in
one family file rather than in a config file per feedstock.

## 3. Write the decision down

This is the part no tool does for you. swage says what is undecided and shows
the evidence; which answer is right is a packaging judgment.

`microsoft-kiota-http` is the example that prompted this page. What swage said:

```
`httpx[http2]` resolved to `httpx`, dropping extra `http2` -- map the
requirement in name_map if conda-forge has a package for it, or write out what
it pulls in under embedded_extras

`h2 >=3,<5` in `/requirements/run` is in the recipe and in no upstream
version -- drop it, declare it in add_requirements if conda-forge needs it for
good, […]
```

Two findings, one cause: the recipe lists `h2` because somebody expanded
httpx's `http2` extra by hand, and nothing recorded that. conda-forge publishes
no package for the extra, so the answer is
[`embedded_extras`](config/extras.md#embedded_extras):

```yaml
# config/feedstocks/microsoft-kiota-http.yaml
# conda-forge publishes no package for httpx's http2 extra. It pulls in h2,
# which the recipe lists directly.
feedstock: microsoft-kiota-http

embedded_extras:
  "httpx[http2]":
    - h2 >=3,<5
```

One entry answers both findings: the extra is accounted for, and the `h2` line
it explains is no longer a line from nowhere.

If the workbench's `config.yaml` is close enough to what you decided,
`swage draft <feedstock> --execute` copies it into `config/feedstocks/` — beside
an existing file rather than over it. Either way the file is a git commit, and
the comment in it is worth as much as the keys: it is what the next reader has
instead of your reasoning.

The [quirks database reference](configuration.md) has each key, what happens
when it is absent, and a worked example from a feedstock already using it.

## 4. Check it landed

Run the audit again on that feedstock alone. It re-reads config, so a decision
that did not land shows up immediately:

```console
$ swage config --feedstock microsoft-kiota-http    # what the layers resolve to
$ swage audit --feedstock microsoft-kiota-http     # and what that does to the verdict
```

`microsoft-kiota-http` moved from NEEDS REVIEW to PROPOSED, which is where the
family audit above finds it: nothing is left undecided, and what remains is
that nobody has blessed the feedstock for automatic merging.

## 5. Act on it

`swage update` is the only command that writes, and only with `--execute`:

```console
$ swage update --feedstock microsoft-kiota-http              # dry run
$ swage update --feedstock microsoft-kiota-http --execute    # pushes
```

Without `--execute` it reaches the same verdict it would with one, so the dry
run is a faithful preview. Because the two runs are otherwise identical, the
one that wrote nothing says so above every bucket:

```
  DRY RUN -- nothing was written; add --execute to push
```

What `--execute` does depends on [`trust`](config/trust.md#trust): at `propose`
— the default — swage pushes a commit and a comment to the bot's pull request;
at `auto` it also adds conda-forge's `automerge` label, and conda-forge merges
on green CI. At `never` it writes nothing at all.

None of those pushes a change a check could not account for. That is not the
ladder's decision: a feedstock the report holds is one swage has nothing to
offer for until somebody answers what it asked.

A feedstock earns `auto` after a cycle has been watched end to end, and the
reason goes in its config file beside the key.
