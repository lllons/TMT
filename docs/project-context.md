# Project context: `TMT_Context/`

A session used to start from nothing. Everything worked out about a project — where
the entry point is, which command runs the tests, what was implemented last week and
what was left half done — was rebuilt from scratch on every launch, or lost when the
process ended.

TMT now keeps two markdown files in the project it is working on:

```text
my-project/
├── src/
├── tests/
├── package.json
└── TMT_Context/
    ├── notes.md      how this project works
    └── progress.md   what has been done, what is being done, what remains
```

They are ordinary files. Open them, read them, edit them, commit them.

## When it is created

On the **first actual task** in a project — not when you launch TMT, and not when you
type a slash command. Launching and changing your mind leaves nothing behind.

```text
tmtcode
   ↓
"Add a dark mode to this application."
   ↓
no TMT_Context → create it, inspect the project, record what was found
   ↓
start working
```

If `TMT_Context/` is already there it is **not** recreated. If either file is already
there it is **not** overwritten — not merged, not regenerated, not touched at all.

## `notes.md` — how the project works

Written first from a real inspection of the repository, then corrected and added to as
TMT learns more. It has stable headings so both you and TMT can find things:

```text
# TMT Project Notes
## Project Overview      ## Dependencies
## Architecture          ## Constraints
## Important Files       ## Known Issues
## Build                 ## TMT Notes
## Testing
## Configuration
```

The initial inspection is deliberately shallow — manifests, markers, package scripts,
Makefile targets, `[tool.X]` sections, CI configuration, the README's first paragraph,
the top-level directories, and any entry point a manifest *declares*. It reads files
and runs nothing, so it costs a moment rather than a walk of a large repository.

**Nothing in it is invented.** Where a fact could not be established the file says so:

```text
## Build

Build command has not yet been confirmed. Nothing in this repository
names one that TMT recognised.
```

That is the whole rule. A guessed build command is worse than an empty section,
because it costs you a failed command and your trust in every other line.

## `progress.md` — what has happened

```text
# TMT Project Progress
## Current Status        ## Verification
## Completed             ## Important Decisions
## Currently Working On  ## Known Issues
## Remaining             ## Next Steps
## Tests
```

**A task is only recorded as completed when there is evidence for it** — a plan whose
every step finished, or files that were actually written. A task that ended without
doing anything stays under *Currently Working On* as an open item. TMT's intention to
implement something never becomes a tick.

**Test results are only ever real ones.** The Tests section is written from a
verification result built out of exit codes, so it says `39 passed, 3 failed` when
that is what happened, and says nothing at all when nothing ran.

## How it is used

The context is put into the model's prompt **before** the workspace snapshot, so the
first thing TMT reads is what it already worked out and the second is the repository
itself. That is the whole point: the second task in a project should be faster than
the first, because the layout does not have to be rediscovered.

```text
"Now add keyboard shortcuts."
   ↓
load TMT_Context → already knows the architecture, the test command,
                   what dark mode did, what is outstanding
   ↓
search only where it still needs to
```

It is budgeted rather than pasted whole. Small files go in complete; large ones keep
the sections that matter most and **say which ones were left out**, so the model is
never taught that a section it cannot see does not exist.

## The context never outranks the code

`TMT_Context/` is memory, not truth. If `notes.md` says authentication lives in
`auth.py` and the repository has moved it to `authentication/service.py`, **the
repository is right**.

TMT checks the paths the notes name against the paths that exist, and puts what it
finds in the prompt beside the notes themselves:

```text
STALE: these paths are named in the notes but are not in the workspace now:
src/main.py. Do not act on them without checking.
```

It does not edit the note for you — a file can be missing because the note is stale or
because you are mid-refactor, and only reading the code tells the two apart. What it
does is stop the note being believed, and correct it once it knows better.

## Your edits are protected

You can rewrite either file by hand at any time. TMT changes **one section at a time**
and writes every other byte back exactly as it read it — including headings it has
never heard of, your prose, and your spacing. There is no operation that hands a whole
file over, and no way to ask for one.

If you edit a file while TMT is working, your edit survives: the file is re-read at the
moment of the change rather than taken from a copy read earlier.

## Secrets are never written

No keys, no tokens, no passwords, no values out of a `.env`. TMT records the
requirement and not the value:

```text
## Configuration

Environment variables this project declares (names only -- values are
never recorded here), from `.env.example`:

- `API_KEY`
- `DATABASE_URL`
```

The real `.env` is never opened — its presence is noted from a stat and its contents
are not read. Everything written to either file also passes through the same
credential filter `remember` uses, and anything credential-shaped is redacted and
reported.

## With `/plan`, `/review` and `/verify`

The context does not duplicate those systems; it remembers what they concluded.

| | goes into |
|---|---|
| plan steps and their real status | `progress.md`, *Currently Working On* |
| what verification actually ran, and its numbers | `progress.md`, *Tests* and *Verification* |
| blocking review findings still outstanding | `progress.md`, *Known Issues* |

The final state is persisted **after** the completion gates have agreed and before the
answer goes out, so the order stays:

```text
work → verify → review → persist → end
```

Persisting can never release an answer that the plan, the verification or the review
was holding — it is not asked until all three already agreed. And `send_message` never
finalises anything: saying "I have implemented the feature" is a sentence, not
evidence.

## Per project, always

The context path is worked out from the active workspace every time it is asked for, so
two projects can never share one and information from one can never appear in the
other.

## Turning it off

Settings → **Project Context** → Enter. Default is **ON**.

With it off, TMT neither creates, reads nor updates any `TMT_Context`. **Files already
written are left exactly as they are** — they belong to the project and to whoever
wrote them, and a setting is not consent to delete somebody's notes.

The switch is stored in `.tmt_context` beside TMT's other per-install settings, so it
is TMT's own state and does not follow the workspace. The context *files* are the one
thing TMT deliberately writes into your project.

## If it cannot be created

A read-only checkout, a permissions failure, a full disk: TMT says so once and carries
on with your task.

```text
Persistent project context could not be created (PermissionError: ...).
Continuing without TMT_Context.
```

Nothing about this feature is worth failing a task for.

## Should it be committed?

That is your call. TMT does not touch your `.gitignore`. The default treats the two
files as ordinary project documentation — they are readable, diffable, and useful to
everyone working on the repository — so committing them shares what TMT has learned
with the rest of the team. Ignore them if you would rather they stayed local.

---

[← Back to the README](../README.md)
