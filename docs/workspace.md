# The workspace: where TMT works, and what it may touch

## The two directories

TMT keeps its own installation and your project strictly apart. They are separate
things and they are meant to stay separate.

| | What lives there | Where it is |
|---|---|---|
| **Installation directory** | TMT's own source, your saved API key, TMT's git co-author identity, its logs | `~/.tmtcode` after an npm install, or wherever you cloned it — `~/tools/TMT`, `C:\Coding\TMT` — set once, and it never moves |
| **Project directory** (the workspace) | the files TMT reads, edits, runs and commits | wherever you ran `tmtcode` |

Only the project directory is ever modified. TMT's own files stay in the installation
directory whichever project you are standing in, so it is the same agent — same key,
same co-author address — everywhere.

To say it plainly once more: **you do not copy TMT into a project to use it on that
project.** One install, then `cd` to any project and type `tmtcode`.

## Choosing the project directory

| Command | Project directory |
|---|---|
| `tmtcode` | the current directory |
| `tmtcode ../other-repo` | resolved against the current directory, then made absolute |
| `tmtcode /abs/path/to/project` | that path |
| `tmtcode --dir PATH` | the same thing as the positional `PATH`, kept for existing use |

A relative path is resolved against the directory you ran the command in and then made
absolute. Giving both a positional `PATH` and a `--dir` that name different directories
is an error, and TMT exits without starting.

TMT uses the directory it was given, exactly. It does not walk up looking for a project
root: run it in `MyWebsite/src` and `MyWebsite/src` is the workspace.

The resolved path is printed at launch, so a run from the wrong place is obvious:

```
Workspace: C:\Projects\my-repo
```

Everything outside that directory is off limits — a path that climbs out of the
workspace is refused, not followed.

## Permissions and limits

- TMT can create, overwrite and delete files anywhere inside the project directory, and
  nothing it does there is recoverable unless the directory is a git repository. It
  needs ordinary read and write permission on it.
- The installation directory must be writable: `.tmt_providers.json` (your keys) and
  `logs/` are written there. `.tmt_git` and `.tmt_git.local` are only read from there.
- A directory is selected, never created. TMT refuses a path that does not exist, a
  file, a filesystem root, and your home directory.
- If the directory already has files in it and is not inside a git working tree, TMT
  describes what it is about to be pointed at and asks before starting. A git
  repository is its own undo, so it starts without asking.
- TMT runs commands through exactly one action, `bash`, and it runs them under a
  policy: the project directory as the working directory, an environment built from
  scratch with your credentials left out, a curated `PATH`, no network unless the run
  allows it, a timeout that kills the whole process tree, and a question put to you
  before anything destructive or unrecognised. What that does and does not confine is
  set out in [Running commands](bash.md), including the part
  it cannot do.
- Beyond `bash`, TMT launches only the two applications listed below.
- Pushing uses whatever git credentials you already have. TMT stores none and
  implements no login.

---

[← Back to the README](../README.md)
