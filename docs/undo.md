# Undo: `/undo` and `/checkpoints`

TMT takes a picture of the workspace before the first action of a turn that could
change anything, and `/undo` puts it back. It is the answer to the reason people
watch an agent keystroke by keystroke: not that it is usually wrong, but that a
wrong turn used to be unrecoverable unless you happened to have committed.

```
Task> /checkpoints
Task> /undo
Task> /undo confirm
Task> /undo 0007 confirm
```

## Saying nothing changes nothing

`/undo` on its own is a **preview**. It names every file it would put back, every
file it would restore, and every file it would delete — and it changes none of them.
`/undo confirm` is what carries it out.

That is `replace_across`'s rule, reached for the same reason: this is the most
destructive thing TMT does to a tree, and a bulk change nobody looked at first is
how a repository gets wrecked.

```
Undo would change 3 files
Checkpoint 0007, taken 14:31:02: add the retry loop to the client

Put back  2
   src/client.py
   tests/test_client.py

DELETE (created since)  1
   src/backoff.py

41 files already match.

Nothing has changed. Run `/undo 0007 confirm` to do it.
```

## What a checkpoint is

Every workspace file small enough to hold, stored by the hash of its contents, taken
**once per turn** and only when something that could write is about to run. A turn
that only reads costs nothing at all and leaves no checkpoint — there would be
nothing to put back.

It is the whole workspace rather than the files an action named, and that is what
makes it worth having: a command can touch anything, so a snapshot scoped to named
paths could never undo a `make`, a formatter or a script the model just wrote.

Everything is stored and restored as **bytes**. A file with CRLF line endings comes
back with CRLF line endings.

## An undo can be undone

Restoring takes a checkpoint of the workspace first, so the state you undid is one
`/undo` away from coming back. The result says which checkpoint that is.

## What it does not cover, and says so

- **Anything written outside the workspace.** A permitted build tool running your
  repository's code can write anywhere, and no snapshot of this directory sees it.
  When the turn ran a command, the report names the command and says this.
- **A file too large to hold** (over 2 MB). Those are recorded by size and
  modification time instead, and if one of them moved during the turn the undo is
  **refused by name** rather than half done — TMT has that file's size and not its
  contents, so putting the rest back would leave it at its new contents and claim the
  workspace was restored.
- **A workspace too large to snapshot** (over 5,000 files or 100 MB of storable
  content). No checkpoint is taken, TMT says so once, and there is nothing to offer.
  Half a snapshot is worse than none.
- **Directories.** A folder created during the turn is left empty rather than
  removed: the walk sees files, so TMT cannot tell a folder the turn made from one
  that was already there and already empty, and removing the second kind would be the
  undo destroying something the turn never touched.
- **Work that is still running.** An undo is refused while a background agent, a note
  or a review is going, because rewriting the tree under something that is writing to
  it would leave both wrong and neither able to tell.

## `/checkpoints`

The list, newest first, with the time and the task each one was taken for. That is
where the id `/undo` takes comes from. The last 20 turns are kept, or 200 MB of
stored content, whichever runs out first; contents shared between checkpoints are
stored once, so a session of small edits costs very little after the first.

```
Checkpoints
7 kept, newest first. TMT keeps the last 20.

0007  14:31:02  add the retry loop to the client
      ran 2 commands
0006  14:12:44  before undoing 0005
      already restored
0005  14:09:31  rename the config module
```

## Where it lives, and who can reach it

Under TMT's own installation directory, keyed by a hash of the workspace path, beside
the code index and the project notebook. Nothing of TMT's is written into your
project, and two projects never share a history.

**The model cannot undo anything.** There is no action, no key and no mention of any
of this in the system prompt: `/undo` is typed by a person. A model that could
restore a checkpoint could undo its own work and then answer as though the turn had
gone differently, which would hand the one mechanism that proves what happened to the
party it exists to check. There is a test asserting the absence.
