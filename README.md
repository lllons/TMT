<p align="center">
  <img src="assets/Recording%202026-08-29%20103658.gif" width="600">
</p>


## "Too Many Tools" — a CLI coding agent. It edits files in a sandboxed workspace, runs code in a dozen languages, and commits and pushes as a co-author on your commits.

>**Needs Python 3.8+.**

## The command is `tmtcode`

Installing puts one command on your PATH:

```bash
tmtcode
```

Run it from any directory on the system. **The directory you run it in becomes the
project TMT works on.**

```bash
cd ~/Projects/MyWebsite && tmtcode      # TMT works on ~/Projects/MyWebsite
cd ~/Documents/MyProject && tmtcode     # TMT works on ~/Documents/MyProject
```

Install TMT once, anywhere. You never copy it into a project, and a project never
needs TMT files inside it.

## Install

```bash
git clone https://github.com/lllons/TMT.git
cd TMT
pip install -e .                 # puts `tmtcode` on PATH
pip install -e ".[live]"         # optional: adds requests and rich for streaming and colour
```

The agent itself needs nothing beyond the standard library; `requests` and `rich` only
add live streaming and colour, and TMT falls back without them.

After installing, leave the clone where it is and run `tmtcode` from wherever your work
is. The clone is TMT's home, not your project.

Without installing, a clone still runs directly, and from anywhere:

```bash
python /path/to/TMT/TMT.py                    # the current directory is the project
python /path/to/TMT/TMT.py ~/Projects/MyWebsite
```

Windows: `py`. macOS/Linux: `python3`.

## The two directories

TMT keeps its own installation and your project strictly apart. They are separate
things and they are meant to stay separate.

| | What lives there | Where it is |
|---|---|---|
| **Installation directory** | TMT's own source, your saved API key, TMT's git co-author identity, its logs | wherever you cloned it — `~/tools/TMT`, `C:\Coding\TMT` — set once, and it never moves |
| **Project directory** (the workspace) | the files TMT reads, edits, runs and commits | wherever you ran `tmtcode` |

Only the project directory is ever modified. TMT's own files stay in the installation
directory whichever project you are standing in, so it is the same agent — same key,
same co-author address — everywhere.

To say it plainly once more: **you do not copy TMT into a project to use it on that
project.** One clone, one install, then `cd` to any project and type `tmtcode`.

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
- The installation directory must be writable: `.tmt_key` and `logs/` are written
  there. `.tmt_git` and `.tmt_git.local` are only read from there.
- A directory is selected, never created. TMT refuses a path that does not exist, a
  file, a filesystem root, and your home directory.
- If the directory already has files in it and is not inside a git working tree, TMT
  describes what it is about to be pointed at and asks before starting. A git
  repository is its own undo, so it starts without asking.
- TMT never runs shell commands. It runs code only through `run_file`, and launches
  only the two applications listed below.
- Pushing uses whatever git credentials you already have. TMT stores none and
  implements no login.

## First launch

First launch asks for an [OpenRouter key](https://openrouter.ai/keys) and saves it to
`.tmt_key` in the installation directory (git-ignored). Set `OPENROUTER_API_KEY` to skip
that. It is asked for once for the install, not once per project.

Type a task at the `Task>` prompt. `quit` or `exit` to leave. Ctrl-C cancels the
current task without closing TMT.

Files under 8 KB are shown to the model automatically, up to a fixed number and total
size; the listing says so when it stops early. Larger files are read on demand.

## What you can ask for

Plain English. TMT picks the actions itself.

```
Task> write a python script that fetches a URL and prints the status code
Task> what does report.py do?
Task> find every TODO in src and list them
Task> change the timeout in net.py from 5 to 30 seconds
Task> run hello.py
Task> open notes.txt in notepad
Task> commit the changes and push to main
```

### Files

| Action | Purpose |
|---|---|
| `write_file` / `write_files` | Create a file, or several at once |
| `patch_file` | Search-and-replace — the default for edits |
| `replace_lines` | Replace an exact line range |
| `append_file` | Add to the end of a file |
| `read_file` / `read_lines` | Read a whole file, or a line range |
| `search_files` | Plain or regex search, optionally scoped to a folder |
| `copy_file` / `rename_file` / `delete_file` | Move, rename, remove |
| `create_folder` / `delete_folder` | Folders (recursive delete is opt-in) |
| `list_files` | List the workspace |

Paths are interpreted relative to the project directory, and anything that resolves
outside it is refused. Only that directory is listed, read or written.

Editing an existing file uses `patch_file`, not a rewrite, so untouched lines stay
untouched. Python files are syntax-checked before they are written; a broken edit is
rejected rather than saved.

### Running code

`run_file` executes and returns the output. Python, JavaScript, TypeScript, Ruby,
PHP, Lua, Perl, R, Go, C, C++, Java. 10-second timeout. The toolchain has to be on
your PATH. Code runs with the project directory as its working directory.

### Apps

`open_app` launches Notepad, or Explorer with a file selected. Nothing else — TMT
never runs shell commands.

## Git

TMT commits in the repository containing the project directory — not in TMT's own
repository. You stay the author and the committer of every commit it makes; TMT is
credited beside you with a `Co-authored-by` trailer.

```
Task> commit this                        commits, does not push
Task> commit these changes and push       commits and pushes
Task> push this to main                   targets main
Task> fix the bug                         edits only, no commit, no push
```

Commit and push are separate. TMT pushes only when your own words asked for one —
"fix the bug" never triggers a push, and neither does finishing an edit.

Actions: `git_status`, `git_diff`, `git_identity`, `git_commit`, `git_push`.

It stages only the files it changed, so your unrelated work stays uncommitted. It
never creates a branch, never invents a remote, and never force-pushes. If a push
fails, the commit stays local and you get the real error.

### TMT co-authorship

TMT does not commit as you, and it does not commit instead of you. The repository's own
git identity is the author and the committer. TMT adds one trailer to the message and
nothing else. One commit, both of you credited on it.

A commit TMT made, as git reports it:

```
$ git log -1 --format=fuller
commit f1977e70a471011bf9b5ab643aecdf5e18a8e8fa
Author:     Liam <liam@example.com>
AuthorDate: Sat Aug 29 13:10:01 2026 +1200
Commit:     Liam <liam@example.com>
CommitDate: Sat Aug 29 13:10:01 2026 +1200

    Add a greeting file

    Co-authored-by: TMT code <TMT.tmt.code@gmail.com>
```

It is a git trailer, not a line of prose that happens to have a colon in it, so git
itself will hand it back:

```
$ git log -1 --format=%(trailers:key=Co-authored-by)
Co-authored-by: TMT code <TMT.tmt.code@gmail.com>
```

What that means in practice:

- The author and committer are whoever the repository's git configuration says they
  are. TMT never puts itself in either field, and never writes to your git config,
  globally or per repo.
- If you have no git identity set, git refuses the commit. TMT reports that, tells you
  to set `user.name` and `user.email` yourself, and does not stand in as the author.
  Nothing is committed.
- TMT adds the trailer only to commits it creates. A `git commit` you run yourself is
  untouched.
- A message that already credits the TMT address gets one trailer, not two. The match is
  on the address, so the same address under a different display name still counts as
  already credited.
- Existing trailers survive. A `Co-authored-by:` for someone else is kept and TMT is
  added alongside it; a `Signed-off-by:` block is joined rather than pushed into a new
  paragraph.
- History is never rewritten. If the finished commit somehow lacks the trailer, TMT
  reports it and leaves the commit alone rather than amending it.
- With no TMT address configured, or with the shipped placeholder still in place, TMT
  refuses to commit and stages nothing.

Credit on GitHub is a separate question, and TMT does not control the answer:

- TMT decides the commit metadata — the trailer, and only the trailer.
- GitHub decides whether to credit the co-author. It matches the address in the trailer
  against the addresses verified on an account. An address verified on no account is
  credited to nobody, and the display name alone does nothing.
- Even when the address does match, GitHub's contributor and profile data can lag. A
  push does not necessarily change the contributor graph straight away.
- Getting credit is not the same as being allowed to push. Authentication is separate
  and stays yours.

### Co-author identity

```
TMT_GIT_NAME=TMT code
TMT_GIT_EMAIL=someone@example.com
```

The address TMT is credited under. It is never written into a commit as the author.
Read in this order: `TMT_GIT_*` environment variables, then `.tmt_git.local`
(git-ignored, per machine), then `.tmt_git` (tracked, ships with the project). The
name defaults to `TMT code`. The email has no default, and is never taken from your
git config — your git config supplies the author, not the co-author.

Both files sit in the installation directory, so TMT is credited under the same address
in every project you point it at.

`.tmt_git` is tracked on purpose: a commit email is public metadata, not a
credential, so every clone gets the same TMT co-author without setup. It holds a name
and an address and nothing else. Put no tokens, passwords or keys in it, or in
`.tmt_git.local`.

Run `git_identity` to see which source won, which files were consulted, and whether the
address is usable.

### Setting up GitHub attribution

The tracked `.tmt_git` names the address verified on the GitHub account that represents
TMT, so a fresh clone credits it correctly with no setup. If that email is ever a
placeholder, TMT refuses to commit rather than credit a co-author who identifies
nobody. To credit an account of your own instead:

1. Create a GitHub account for TMT.
2. Add and verify an address on it.
3. Put that address in `.tmt_git.local`, or in `.tmt_git` and commit it once.

Four separate things, only one of which TMT decides:

- **Authorship** — the author and committer written into the commit. Yours. TMT reads
  it back to report it and never sets or changes it, and your `user.name` and
  `user.email` are not modified, globally or per repo.
- **Co-author credit** — the `Co-authored-by` trailer. The one part of the commit TMT
  decides.
- **GitHub attribution** — GitHub matching that trailer's address to a verified
  account. Out of TMT's hands, and a display name alone does nothing.
- **Authentication** — who may push. Stays yours: your SSH key, credential manager,
  or `gh` login. TMT stores no credentials and implements no login.

## Interface

While a task runs: a THINKING animation until the first output, then a progress bar,
elapsed time and a live token count. Model text is revealed character by character as
it streams. The final answer is boxed.

Set `TMT_STREAM=0` to disable streaming. Streaming also needs `requests`; without it
TMT runs unstreamed.

## Configuration

| Variable | Default |
|---|---|
| `OPENROUTER_API_KEY` | from `.tmt_key` |
| `OPENROUTER_MODEL` | `minimax/minimax-m3:free` |
| `TMT_STREAM` | `1` |
| `TMT_GIT_NAME` | `TMT code` |
| `TMT_GIT_EMAIL` | none — required before TMT will commit |
| `TMT_GIT_ROOT` | the repository containing the project directory |
| the `PATH` argument, or `--dir` | the current directory |

## `tmtcode` not recognised

The command is installed, but the directory pip put it in is not on your PATH. Find
that directory:

```bash
python -c "import sysconfig; print(sysconfig.get_path('scripts'))"
```

It is `Scripts` on Windows and `bin` on macOS and Linux, under the Python or virtual
environment you installed into. Add it to PATH, or use either fallback — both take the
same arguments and pick the project directory the same way:

```bash
python -m TMT                     # anywhere, once installed
python /path/to/TMT/TMT.py        # anywhere, straight from a clone
```

If you installed into a virtual environment, `tmtcode` exists only while that
environment is active.

`tmtcode --help` prints the arguments.

## Tests

```bash
python run_tests.py
```

## License

Apache license 2. See [LICENSE](LICENSE).
