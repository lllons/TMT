<p align="center">
  <img src="assets/Recording%202026-08-29%20103658.gif" width="600">
</p>


## "Too Many Tools" — a CLI coding agent. It edits files in a sandboxed workspace, runs code in a dozen languages, and commits and pushes under its own git identity.

Needs Python 3.8+.

```bash
git clone https://github.com/lllons/TMT.git
cd TMT
pip install requests rich      # optional: adds live streaming and colour
python TMT.py                  # Windows: py TMT.py   macOS/Linux: python3 TMT.py
```

First launch asks for an [OpenRouter key](https://openrouter.ai/keys) and saves it to
`.tmt_key` (git-ignored). Set `OPENROUTER_API_KEY` to skip that.

Type a task at the `Task>` prompt. `quit` or `exit` to leave. Ctrl-C cancels the
current task without closing TMT.

## Workspace

TMT works on **the directory you run it in**.

```bash
cd ~/projects/my-repo && python /path/to/TMT/TMT.py   # that repo is the workspace
python TMT.py --dir ~/projects/other-repo             # or name one
```

The resolved path is printed at launch, so a run from the wrong place is obvious:

```
Workspace: C:\Projects\my-repo
```

`--dir` selects a directory; it never creates one. Paths outside the workspace are
refused. TMT will not start in a filesystem root or your home directory, and asks
first if the directory already has files and is not a git repository.

Files under 8 KB are shown to the model automatically, up to a fixed number and total
size; the listing says so when it stops early. Larger files are read on demand.

Your API key and TMT's git identity live beside the TMT install, not in the
workspace, so TMT is the same agent wherever you run it.

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

Editing an existing file uses `patch_file`, not a rewrite, so untouched lines stay
untouched. Python files are syntax-checked before they are written; a broken edit is
rejected rather than saved.

### Running code

`run_file` executes and returns the output. Python, JavaScript, TypeScript, Ruby,
PHP, Lua, Perl, R, Go, C, C++, Java. 10-second timeout. The toolchain has to be on
your PATH.

### Apps

`open_app` launches Notepad, or Explorer with a file selected. Nothing else — TMT
never runs shell commands.

## Git

TMT commits under its own identity, not yours.

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

### Identity

```
TMT_GIT_NAME=TMT code
TMT_GIT_EMAIL=someone@example.com
```

Read in this order: `TMT_GIT_*` environment variables, then `.tmt_git.local`
(git-ignored, per machine), then `.tmt_git` (tracked, ships with the project). The
name defaults to `TMT code`. The email has no default and is never taken from your
git config.

`.tmt_git` is tracked on purpose: a commit email is public metadata, not a
credential, so every clone gets the same TMT identity without setup. Put no tokens,
passwords or keys in it.

Run `git_identity` to see which source won.

### Setting up GitHub attribution

`.tmt_git` ships with a placeholder, and TMT refuses to commit while it is there —
an invented address identifies nobody. To make commits attribute to TMT:

1. Create a GitHub account for TMT.
2. Add and verify an address on it.
3. Put that address in `.tmt_git` and commit it once.

Three separate things, only the first of which TMT controls:

- **Commit identity** — the author and committer written into the commit. TMT sets
  this, for one git subprocess at a time. Your own `user.name` and `user.email` are
  never read or modified, globally or per repo.
- **GitHub attribution** — GitHub matches the commit email to a verified account. A
  name alone does nothing.
- **Authentication** — who may push. Stays yours: your SSH key, credential manager,
  or `gh` login. TMT stores no credentials and implements no login.

## Interface

While a task runs: a THINKING animation until the first output, then a progress bar,
elapsed time and a live token count. Model text is revealed character by character as
it streams. The final answer is boxed.

Set `TMT_STREAM=0` to disable streaming.

## Configuration

| Variable | Default |
|---|---|
| `OPENROUTER_API_KEY` | from `.tmt_key` |
| `OPENROUTER_MODEL` | `minimax/minimax-m3:free` |
| `TMT_STREAM` | `1` |
| `TMT_GIT_NAME` | `TMT code` |
| `TMT_GIT_EMAIL` | none — required for commits |
| `TMT_GIT_ROOT` | the repository containing the workspace |
| `--dir` (flag) | the current directory |

## Tests

```bash
python run_tests.py
```

## License

Apache license 2. See [LICENSE](LICENSE).
