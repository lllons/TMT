<p align="center">
  <img src="assets/Recording%202026-08-29%20103658.gif" width="600">
</p>


## "Too Many Tools" — a CLI coding agent. It edits files in a sandboxed workspace, runs code in a dozen languages, and commits and pushes automatically on any repo.

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

### Understanding a repository

Eight actions for finding your way around a codebase without reading it all. Each
answers one question, and TMT is told to pick the narrowest one that fits.

| Action | Purpose | Reach for it when |
|---|---|---|
| `tree` | Directories, files, sizes, nesting. Reads no contents | You need the shape of the project |
| `find_text` | Exact, case-sensitive search across every file at once. The query may span several lines | You know the characters you are looking for |
| `find_symbol` | Where a function, class, method, constant or type is *defined* | You want a definition, not a mention |
| `code_map` | What defines this, what imports it, what it imports, where it is referenced | You need to know what a change would affect |
| `replace_across` | The same exact edit in many files | Renaming something the whole project uses |
| `related_tests` | Reads the git diff and names the tests worth running | You changed one thing and do not want to run everything |
| `remember` / `recall` | Durable notes about this project, kept between sessions | Something cost you time to work out |

```
Task> show me the project structure
Task> find every place that calls self.workspace_root
Task> where is calculate_total defined?
Task> what imports agent_file_ops?
Task> rename old_function_name to new_function_name across src
Task> which tests should I run for what I just changed?
```

`find_text` is exact and case-sensitive; `search_files` is the loose, case-insensitive
one. Both exist because they answer different questions.

**`replace_across` previews by default.** It reports how many files and occurrences it
*would* change and writes nothing. Sending the same action again with `"apply": true`
performs it. Line endings and encoding are preserved, binary files are skipped, and a
replacement that would leave a Python file unparseable is refused rather than written.

**Facts and guesses are labelled differently.** Python symbols are found by parsing the
file, so those answers are exact; other languages are matched lexically and say so.
`related_tests` separates what the diff proves from what it is only guessing. Nothing
presents a heuristic as a measurement.

**Project memory** is stored beside TMT's own settings, keyed by project, never inside
your repository — the same rule as every other piece of TMT state. Notes are scanned
before they are written and anything shaped like a key, token or password is refused.

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

## Background agents

TMT can delegate. The main agent spawns background workers, they do real work through
the same actions and the same models it uses itself, and it waits for them and reports
what they did.

```
Task> spawn three agents to write multiply.py, divide.py and power.py, then wait for them
```

| | Runs | May edit | May push | Talks to you | Ends with |
|---|---|---|---|---|---|
| main agent | the session loop | yes | yes | yes | `respond` / `done` |
| worker | a background thread | yes | **no** | no | `internal_response` |
| note agent | a background thread | **no** | no | answer only | `internal_response` |

**Five workers at once.** The main agent does not count against that and neither does
the note agent. A sixth request is refused with a sentence saying so, not ignored.

`/agents` prints what they are doing. In a real terminal, Right Arrow at the end of an
empty line opens the same thing as a live panel, and Left closes it.

### `/note` — ask about the workspace without disturbing anything

```
Task> /note which module owns the prompt box?
```

A read-only agent answers from the workspace while everything else carries on. It may
search, read and inspect structure; it cannot create, edit, delete or push, and that is
enforced by a whitelist checked before every action rather than by asking it nicely.

The question goes on the same line. That form works everywhere, including a piped run —
the piped reader takes one task per line, so a two-stage prompt cannot be reached from a
pipe at all. In a real terminal a bare `/note` will ask for the question separately.

### What background agents deliberately cannot do

These are limits of the design, not things left unfinished:

- **A worker cannot push.** It may read `git status`, `diff`, `log` and `branch`, and it
  may commit; reaching a remote stays with the main agent, which needs your own words in
  the task before it can.
- **A worker cannot delete a file or a folder.** Both wait for a human to confirm at the
  terminal, and a background thread has no terminal to be asked at. A worker reports the
  path instead and the main agent does it.
- **A worker cannot run the test suite.** `run_file` gives up after 10 seconds and a real
  suite takes longer, so a worker asked to verify tests says it could not and what it did
  instead. It will not report a result it never saw.
- **"Kill" is cooperative, not instant.** Python cannot forcibly stop a thread. What is
  guaranteed, and what is tested, is that **no further tool call runs once an agent is
  killed** — cancellation takes effect at the next chunk or the next action boundary. An
  agent stuck on a stalled connection is marked killed and abandoned; its thread is a
  daemon and can never hold TMT open.
- **Waiting blocks the main agent.** It is an ordinary action, not a suspend. The
  interface stays alive while it waits because the live region repaints on its own
  thread, and Ctrl-C returns you to the prompt.
- **Workers do not coordinate their writes.** Any single write is atomic, and if two
  workers touch the same file the main agent is told which. There is no locking beyond
  that, so give concurrent workers separate files.

## Interface

While a task runs: a THINKING animation until the first output, then a progress bar,
elapsed time and a live token count. Model text is revealed character by character as
it streams. The final answer is boxed. A count of running agents appears beside the
meter whenever there are any.

**The agents panel is a column at the foot of the screen, not a full-height sidebar.**
It shares the live region with the reply and the prompt box; the conversation above it
keeps the full width and is never redrawn. That is a deliberate limit rather than an
unfinished one: the scrollback is TMT's only permanent record of a session, and both
escapes that would let a program own the whole window — narrowing the scrolling region,
and the alternate screen buffer — destroy it. Lines scrolled out of a narrowed region
are discarded rather than kept, so scrolling up would stop reaching the session's own
history. A test greps the modules to keep either from coming back.

On a terminal under 45 columns the panel takes the whole width of the live region and
the prompt box is not drawn while it is open; under 30 columns it refuses to open and
says why. Cards drop their activity line before their token line, and truncate rather
than wrap.

Set `TMT_STREAM=0` to disable streaming. Streaming also needs `requests`; without it
TMT runs unstreamed.

## Slash commands

At the prompt, a line beginning with `/` is answered by TMT itself and is never sent
to the model. Names are case-insensitive. Everything else is a task and goes to the
model exactly as before — including a line that merely starts with a path, such as
`/usr/bin/python is broken`.

| Command | What it does |
|---|---|
| `/context` | the conversation so far: model, provider, workspace, how many turns are carried into the next request, estimated tokens in and out, lines added and removed, and the last few questions |
| `/config` | the settings a request runs under: model, provider, effort, streaming, JSON mode, workspace, and whether an API key is set |
| `/clear` | forget the conversation and start fresh. The model, effort, workspace and every other setting are kept, and no file is touched |
| `/effort` | show the current effort level |
| `/effort low\|medium\|high` | set it |
| `/model` | show the current model and the ones this provider offers |
| `/model <name>` | switch to one, by id or by the name shown in Settings |
| `/note <question>` | answer one question about the workspace, changing nothing |
| `/agents` | what the background agents are doing |

**Effort** is how much work TMT will spend on one task. It changes two things, and
only things that are real on every provider: how long a reply is asked for, and how
many rounds of the agent loop one question may take.

| Level | Reply length asked for | Rounds per task |
|---|---|---|
| `low` | 4096 tokens | 12 |
| `medium` (default) | 4096 tokens | 35 |
| `high` | 8192 tokens | 60 |

The reply length does not go below 4096 at any level. Every reply is one JSON object
and the ones that matter carry a whole file inside it, so a smaller limit does not
make the model terser — it cuts the object off mid-string and the write never
happens. The setting is stored in `.tmt_effort` beside the installation and survives
a restart, like the model choice.

**Completion.** In a real terminal, typing `/` lists the seven commands under the
line you are typing, and it narrows as you go: `/mo` leaves `/model`. Tab completes
as far as the candidates agree — `/mo` becomes `/model `, `/co` becomes `/con`,
because `/context` and `/config` still both apply. A piped or redirected run reads a
whole line and draws no list; the commands themselves still work there.

**Secrets.** Neither `/context` nor `/config` ever prints a key, a token or a
password. `/config` says whether a key is set and nothing else about it — not the
value, and not a masked form of it either.

## Configuration

| Variable | Default |
|---|---|
| `OPENROUTER_API_KEY` | from `.tmt_key` |
| `OPENROUTER_MODEL` | `minimax/minimax-m3:free` |
| `TMT_STREAM` | `1` |
| effort | `medium`, from `.tmt_effort`; set with `/effort` |
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

629 tests, about a minute. Eight of them read the API key from `.tmt_key`, so on a
fresh clone with no key configured those eight fail and the rest pass.

## License

Apache license 2. See [LICENSE](LICENSE).
