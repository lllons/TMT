<p align="center">
  <img src="assets/Recording%202026-08-29%20103658.gif" width="600">
</p>

<p align="center">
  <b>English</b> ·
  <a href="otherlang/README.zh.md">中文</a> ·
  <a href="otherlang/README.ja.md">日本語</a> ·
  <a href="otherlang/README.fr.md">Français</a> ·
  <a href="otherlang/README.es.md">Español</a> ·
  <a href="otherlang/README.ru.md">Русский</a>
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

## The launch screen

Every launch of `tmtcode` opens on the same screen: the TMT wordmark filling the
terminal, and under it

```
                              Press Enter to Continue
```

The line pulses while it waits — the gradient sweeping along it on a colour terminal,
a slow weight pulse on one without colour, and nothing at all where escapes cannot be
used, because colour is never the message here either. Enter is the only key that goes
on; Ctrl-C closes TMT. Everything else is ignored, so a first task typed before the
screen has settled cannot set something off.

**The launch screen always appears.** It is not a setting and there is nothing to turn
off. What *is* a setting is what happens after Enter.

### After Enter: the update check

With `Auto Update on Launch` **on** (the default), TMT looks at its own git checkout to
see whether a newer version exists, and says so on the same screen:

```
                              Searching for updates...
```

and then one of

```
                                    Up to date.            nothing was pulled, nothing restarted
                          Update complete. Restarting...   a fast-forward was applied
                            Continuing without updating.   an update could not be taken safely
                Update check failed. Continuing without update.
```

With the setting **off**, none of that happens and TMT does not pretend it did — no
"searching" line is shown for a search that was never made. It goes straight on.

After that, TMT continues exactly as it always has: the API-key setup if this
installation has not been configured yet, and otherwise the ordinary start screen.

### When TMT will and will not update itself

It updates only when the update is unambiguously safe, and it never touches your work.

| What it finds | What it does |
|---|---|
| already current | nothing. No pull, no restart |
| remote ahead, clean tree, fast-forward possible | fast-forwards, then restarts |
| **uncommitted local changes** | refuses, and says so. Your changes are untouched |
| **the branch has diverged** — local and remote both moved | refuses. Local commits are never discarded |
| no upstream configured, or a detached HEAD | says it cannot tell, and continues |
| not a git checkout at all | continues |
| no network, no git, a bad remote, a failed merge | reports the failure and continues |

It works on the branch you already have checked out and never creates, switches or
forces one. It uses `git fetch` and `git merge --ff-only` and nothing else: **it never
runs `git reset --hard`, `git clean`, a force checkout, or a plain `git pull`** — a
pull can merge, and a merge during startup is exactly what must not happen. A test
reads the updater's own source and asserts those commands do not appear in it.

TMT stays usable with no internet. A failed update check is a line on the splash and
nothing more.

### Restarting

A successful update replaces the process with a fresh one, so the new code really runs
rather than the old modules staying imported. Your command line is preserved —
`tmtcode --dir ~/project` comes back as `tmtcode --dir ~/project`.

You then see the launch screen again, which is expected: the launch screen is part of
every startup. The restarted process finds itself current and continues. **It cannot
loop** — at most one automatic restart happens per launch, and the second process
knows it is the second.

### Turning it off

Settings → `Auto Update on Launch`, Enter to toggle:

```
  AI Provider            Which service answers a request
  API Key                The credential that service is given
  Model                  Which model TMT runs on
> Auto Update on Launch  Check for a newer TMT after the launch screen  ON
  Back                   Return to the menu
```

It is stored in `.tmt_autoupdate` in the installation directory, beside the model and
effort settings, so it belongs to the install rather than to one project and survives
restarts. A missing file means on; a file nobody can read, or one edited into nonsense,
also means on rather than an error at startup.

**Turning it off does not turn off the launch screen.** The splash is shown either way.

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

## Talking to you: `send_message` and `end_conversation`

TMT has exactly two ways of putting words on your screen, and the whole
difference between them is whether the task carries on afterwards.

| Action | Shows you text | Ends the task |
|---|---|---|
| `send_message` | yes | **no** — control returns to the agent, every time |
| `end_conversation` | yes | **yes** — and it is the only action that ends one |

**`send_message` is for saying things on the way.** "I'll read the parser
first", "the tests are green, so the docs are next", "this file is larger than
I expected." It is printed into the session where you can scroll back to it,
and then TMT carries on from exactly where it was. It can be used as many times
in one task as it is worth using; there is no cap and nothing about it is
final.

**`end_conversation` is the ending.** Its message is the summary you are left
looking at, which is why the agent is told that work not described there might
as well not have happened. There is no second way to stop: no separate `done`,
and no flag on a message that quietly turns it into the last one. TMT finishes
a task with this action or it does not finish it.

**Wanting to end it is not the same as being allowed to.** `end_conversation`
is what the completion gates hold, and any capability you turned on for that
prompt can refuse it: a plan with steps still outstanding, a review that has not
passed, verification that has not run or that found a failing check. A refusal
is not an error and it does not end the turn — TMT is handed the reason, goes
back to work, and the answer is still unsaid. See
[Capabilities](#capabilities-plan-review-verify).

Background agents have neither channel in any useful sense: nobody is reading
them, so a message costs a step and reaches no one, and the ending is a report
to the main agent instead. See [Background agents](#background-agents).

## Capabilities: `/plan`, `/review`, `/verify`

Three of the things TMT can do are not ordinary tool work. Writing a plan and
being held to it, having a second agent audit the diff, and running the
repository's own checks each cost a whole extra model run, put a column on the
screen, and can refuse TMT's own final answer. They are yours to spend, so
they are off unless you ask for them, and you ask by writing the command in
your prompt.

| Command | Enables |
|---|---|
| `/plan` | the planning workflow — TMT writes the steps and cannot finish until they are done |
| `/review` | independent code review — a separate read-only agent audits the work |
| `/verify` | Smart Verification — the checks this repository actually has, run for real |

```
Task> build me an authentication system
        nothing gated. Ordinary tools, ordinary answer.

Task> build me an authentication system /plan
        plans the work and is held to the plan. No review, no verification.

Task> fix this implementation /review
        an independent reviewer audits it before the answer goes out.

Task> add the endpoint /plan /verify /review
        the whole pipeline: plan, implement, verify, review, fix, answer.
```

**The slash is the whole distinction, and `verify` on its own is not enough.**
"verify this code", "please verify this", "verified" and "verification" are
things people say while asking for ordinary work, and none of them turns the
engine on. Only `/verify` does. The same goes for `plan` and `review`:
"review my code please" is a request for an opinion, `/review` is a request
for the gated, cycle-limited, independent reviewer.

Neither does a longer word that starts with one. `/planning`, `/planner`,
`/plan123`, `/reviewing` and `/verification` are ordinary text, and so is a
command inside a path — `src/review` and `abc/verify` are paths, not commands.

The rest of the rules are the ones you would guess:

- **Anywhere in the prompt.** Beginning, middle, end, or on their own lines in
  a pasted block. `/plan Build it`, `Build it /plan` and `Build the /plan
  feature` are the same request.
- **Any number of times.** `/plan ... /plan` enables planning once. There is no
  such thing as two plans.
- **Any capitalisation.** `/PLAN` works, and stays `/PLAN` on your screen —
  TMT styles your text and never rewrites it.
- **Independent.** `/plan` does not turn on review or verification, and
  neither of those turns on either of the others. You choose the workflow.
- **One prompt at a time.** A capability is authorised for the request that
  asked for it. The next question starts from nothing unless it asks too.

**Only you can turn these on.** Not TMT, not a background agent, not a
reviewer, and not a file it read. A model that decides the task looks big
enough for a plan, writes `/plan` into its own reasoning and calls the action
is refused by the runtime — the authorisation is read from the line you typed
and from nothing else. That is enforced twice: the unauthorised verbs are left
out of the prompt entirely, and the dispatcher refuses them again if one is
reached for anyway.

**They are highlighted as you type.** A valid command in the input box carries
the red → orange → green gradient across it, so you can see what you have
turned on before you press Enter, and see it disappear if you mistype it.
Only the exact command is painted: `verify` stays plain and `/verification`
stays plain. On a terminal with no colour the command is picked out in bold
and underline instead, and in a piped run there is no styling at all — the row
still reads `/plan`, which is the command spelled out.

While the turn runs, whatever you authorised is listed at the top of the
right-hand column:

```
                                                        CAPABILITIES 2
                                                        ● /plan
                                                        ● /verify

                                                        PLAN 2/5
```

**`/plan`, `/review` and `/verify` on their own are still the reports** they
have always been — see [Slash commands](#slash-commands). A line that is
nothing but the command shows you what TMT is doing; a line with a task in it
authorises the capability for that task.

### Files

| Action | Purpose |
|---|---|
| `write_file` / `write_files` | Create a file, or several at once |
| `patch_file` | Search-and-replace — the default for edits |
| `replace_lines` | Replace an exact line range |
| `append_file` | Add to the end of a file |
| `read_file` / `read_lines` | Read a whole file, or a line range |
| `glob` | Find files and directories by path pattern |
| `grep` | Search file contents and report path, line number and the line |
| `copy_file` / `rename_file` / `delete_file` | Move, rename, remove |
| `create_folder` / `delete_folder` | Folders (recursive delete is opt-in) |
| `list_files` | List the workspace |

Paths are interpreted relative to the project directory, and anything that resolves
outside it is refused. Only that directory is listed, read or written.

Editing an existing file uses `patch_file`, not a rewrite, so untouched lines stay
untouched. Python files are syntax-checked before they are written; a broken edit is
rejected rather than saved.

### The plan

**Ask for it with `/plan` in your prompt.** Without that command TMT does not write
a plan and nothing here gates the answer. See
[Capabilities](#capabilities-plan-review-verify).

With `/plan`, for anything substantial — add a feature, fix a bug across the repo, refactor a
subsystem, update the docs throughout a project — TMT writes a plan before it starts
and works through it in front of you. It appears as a column on the right of the live
area while it works, and it stays there, finished, beside the next prompt.

```
                                                        PLAN 2/5
                                                        ─────────────────────────
                                                        S1 ✓ Inspect repository
 09:14 · OpenRouter · MiniMax M3                        S2 ✓ Find and…erminology
 ───────────────────────────────────────────────────    S3 ● Update documentation
 > Describe your next task                              S4 ○ Run tests and verify
 ───────────────────────────────────────────────────    S5 ○ Explain changes
```

| Mark | Status | Colour | Means |
|---|---|---|---|
| `✓` | completed | green | the work for that step is actually done |
| `●` | in progress | orange | the one step being worked on now |
| `○` | pending | red | still to come |
| `!` | blocked | amber | it cannot proceed, and it still counts as unfinished |

Exactly one step is in progress at a time. Completing one promotes the next on its
own. Colour is confirmation, never the message: every status carries a mark as well,
and the whole column degrades to `+ > - !` and ASCII rules on a terminal that cannot
draw the rest.

**The plan is a contract, not a progress bar.** TMT is not permitted to finish a task
while a step is outstanding. A final answer sent with work left over is not shown to
you at all — the runtime refuses it, hands the model back the list of steps it still
owes, and the turn carries on. That is enforced by the program rather than asked for
in the prompt, so a model deciding it is finished does not make it finished:

```
Task> add the feature
 · Planning the work in two steps.
 ◆ Plan created with 2 steps.
 ▲ Plan not finished - 2 steps outstanding, next is S1 Implement the feature. Continuing.
 · The feature is in; running the tests next.
 ◆ S1 (Implement the feature) in_progress -> completed.
 · Suite is green.
 ◆ S2 (Run the tests) in_progress -> completed.
 ┌──────────────────────────────────────────────────────────────┐
 │ Added the feature and the suite is green: 12 tests, 0 failures.│
 └──────────────────────────────────────────────────────────────┘
```

The plan can be revised whenever the work turns out to be different from what was
expected — steps renamed, added, removed, or the whole plan replaced. Two things cannot
happen. A completed step is never reopened: a finished step stays finished, and a plan
whose shape was wrong is replaced outright rather than unwound. And a plan that has had
work done against it cannot be dropped — that was the one route round the gate, so
clearing is refused once any step is completed. Finishing it and reshaping it are both
visible on screen; quietly dropping it would not be.

**Not everything gets a plan.** "What does this function do?" is one answer, and a
plan for it would be noise on the screen and a gate on TMT's own reply. Plans are for
work with stages.

**The plan belongs to the task, not to the session.** It is retired the moment you ask
the next question, so an unfinished plan can never hold up an answer to something
unrelated. Nothing is written to disk. Background agents cannot see it or change it —
it is the main agent's contract with you, and a worker completing a step would let
TMT finish on work it had only claimed.

**On a terminal under 45 columns** the column is not drawn — the prompt box needs the
room more — and `/plan` prints the same thing as ordinary text at any width.

### Understanding a repository

Nine actions for finding your way around a codebase without reading it all. Each
answers one question, and TMT is told to pick the narrowest one that fits.

| Action | Purpose | Reach for it when |
|---|---|---|
| `tree` | Directories, files, sizes, nesting. Reads no contents | You need the shape of the project |
| `glob` | Files and directories matching a path pattern. `*` stops at a `/`, `**/` means any depth, and a pattern with no `/` matches a name anywhere | You need to know which files exist, or where one is |
| `grep` | Search inside files, reporting path, line number and the line. Exact and case-sensitive by default; the query may span several lines | You know the text you are looking for |
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

**`glob` finds files by path or name; `grep` finds text inside files.** That is the
whole distinction, and it is the one worth getting right: the order that works is
`glob` to find the candidate files, `grep` to find the lines in them, `read_lines` to
read the region, then edit, then test — rather than reading a repository to find one
line.

```json
{"action": "glob", "pattern": "agent_*.py"}
{"action": "glob", "pattern": "testing/**/*.py"}
{"action": "grep", "query": "end_conversation"}
{"action": "grep", "query": "def run_file", "glob": "agent_*.py"}
{"action": "grep", "query": "timeout", "path": "src", "ignore_case": true}
```

`grep` is exact and case-sensitive by default, like the tool it is named after.
`"ignore_case": true` makes it loose, `"regex": true` reads the query as a regular
expression, `"context"` adds lines either side of each match, and `"path"` or `"glob"`
restricts which files are read at all. It never returns a whole file: you get the path,
the line number and the line, and `read_lines` gets you the rest.

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

## Verification

**Ask for it with `/verify` in your prompt.** Without that command none of this
happens and nothing here gates the answer; the plain word `verify` is not enough.
See [Capabilities](#capabilities-plan-review-verify).

With `/verify`, before TMT is allowed to say a piece of work is done it runs the
checks this repository actually has, and the runtime will not let it answer until
they pass.

The point is the distinction between evidence and an opinion:

> "This should work" is not verification. `43 passed, 0 failed` is.

TMT does not ask the model whether the code works. It reads the repository, works out
what this project tests and lints and builds itself with, reads the diff to see what
changed, picks the checks that are worth running for *that* change, runs them, and
reports the exit codes. Nothing the model writes can move that result — there is no
key on any action that sets a status, and a check passes when a process exits zero and
at no other time.

### What it decides to run

**It prefers your commands over its own guesses.** In order:

1. a command this repository defines by name — a `package.json` script, a `Makefile`
   target, a `run_tests.py` in the root
2. tooling this repository configures — `[tool.ruff]`, `[tool.mypy]`, `tsconfig.json`
3. the project's package manager — `npm`, `pnpm`, `yarn`, `bun`, `uv`, `poetry`
4. the standard command for the ecosystem — `cargo test`, `go vet`, `pytest`
5. a guess, labelled as one

If your `package.json` says `"test": "vitest run"`, TMT runs `npm run test`. It does
not decide that node projects use jest. If your repository has a `run_tests.py`, that
is the test command — even where `pytest` would also work, because running a different
thing from what you run and calling it your verification would be wrong even when it
passed.

CI configuration is read as *evidence* of which tools you really use, and never as a
source of commands. Nothing TMT runs is a string taken out of a project file: what is
taken is a name, and the command is built from a fixed table around it. There is no
shell anywhere on that path.

**It runs cheap checks before expensive ones**, and stops at the first that fails:

| Level | What |
|---|---|
| 1 | syntax and format of what changed |
| 2 | lint, type checking, compiler checks |
| 3 | the tests that name what you changed |
| 4 | the tests around them |
| 5 | the project's build |
| 6 | the whole suite |

Once the type checker has failed, the ten minutes the integration suite would take are
ten minutes spent measuring a tree already known to be wrong. Everything after a
failure is reported as skipped, with that as the reason — so what was *not* checked is
visible rather than implied.

**It goes deeper when the change is riskier.** Authentication, migrations, database
schema, API contracts, concurrency, filesystem boundaries, shell execution, dependency
or build configuration — or simply a lot of files at once — get the full suite. A
documentation-only change gets the static checks and no test run.

### The four outcomes, kept apart

| | Means |
|---|---|
| **PASSED** | the command ran and exited 0. The only kind of evidence there is |
| **FAILED** | the command ran and exited non-zero. Something is wrong, and the output says what |
| **SKIPPED** | it was not run — the tool is not installed, or an earlier check had already failed |
| **ERROR** | it could not run or did not finish. Nothing is known, and this is *not* a failure of your code |

They are never collapsed into a boolean. A timeout is not a failure; a missing linter
is not a passing lint. TMT will not install anything to close one of those holes —
a missing dependency is reported, never quietly fixed.

### What it looks like

```
VERIFY 1/3
✓ Syntax          passed
✓ Lint            passed
✗ Targeted tests  2 failed, 41 passed
– Full suite      not run: Targeted tests did not pass
```

`/verify` prints the whole run, including the output of anything that failed.

### Which tests it picks

For a project whose test command can take paths, TMT works out which tests the change
reaches and runs those first. A test file counts as **targeted** when there is evidence
for it — it names a changed module, imports a changed symbol, or sits where the
project's own naming convention says it should — and as **related** when it is a
reachability guess. The two are kept apart and levelled apart, because a guess
presented as a measurement is worse than no selection at all.

Where the project's test command *cannot* be narrowed to paths — `npm test`, or a
`run_tests.py` that runs everything — TMT says so and runs the whole suite as the test
evidence. It does not run everything and label it targeted.

### When it happens

Verification is required when the runtime has seen **both** halves of substantial work:
a plan of three or more steps, and at least one file actually written — the same two
facts a review is decided on, observed rather than claimed. A question, a read, or a
small patch with no plan is not gated at all.

You override it in either direction with your own words. "…and run the tests" turns it
on; "no verification needed" turns it off. Saying nothing leaves it to the evidence.

It also has a place in the plan. A plan step named for verification cannot be marked
completed while verification is outstanding — that refusal is in code, not in a prompt.
And the final answer needs all three: **the plan complete, verification passed, and the
review passed.** None of them excuses another.

### The cycle, and its limits

A failing check is feedback, not the end of the task: TMT reads the output, fixes what
it reports, and verifies again. At most three rounds — after that the answer is
released rather than held forever, carrying a line saying verification never passed.
Silence would be the worse failure.

**A pass goes stale the moment the code moves under it.** Editing anything after
verification passed means what passed is not what would ship, and the next answer is
held until it has been verified again. That is what makes the fix-and-verify loop close
rather than being a suggestion.

If a repository has nothing runnable in it at all — no test command, no linter, nothing
installed — verification says so and the answer is released, and TMT has to tell you
plainly that the work is unverified. "I could not verify this" is useful; "verified"
when nothing ran is not.

## Independent review

**Ask for it with `/review` in your prompt.** Without that command no reviewer is
started and nothing here gates the answer. See
[Capabilities](#capabilities-plan-review-verify).

Verification and review answer different questions, and a substantial change needs
both. Verification asks *does this pass executable checks*; review asks *is this the
right change, and is it safe*. A green suite says the code does what its tests say — it
does not say the tests are the right tests, and it does not notice that you built the
wrong feature.

TMT reviews its own work before it is allowed to say it is done — and not by asking
itself. A **separate agent** reads the repository, the diff and your original request,
without having written any of it, and reports what it found. The main agent has to act
on the blocking findings, and the runtime will not let it answer until a review has
actually passed.

The point is the failure a green test suite does not catch:

> You asked for authentication with refresh-token support. The tests pass. The
> reviewer reads the diff and finds that refresh tokens are never checked for expiry,
> and that `/health` has quietly moved behind authentication. Neither is tested,
> because the same agent wrote the code and the tests.

### The cycle

```
      your request
           |
      plan --> implement --> tests
           |
      independent review
           |
   +-------+--------+
   |                |
 PASS            FINDINGS
   |                |
   |          main agent fixes
   |                |
   |            tests again
   |                |
   +-------<---- review again
           |
     plan complete
           |
      final answer
```

### What the reviewer sees, and what it does not

It is given the **user's original request** — treated as the source of truth — the
plan the implementing agent wrote, the plan's completion state, `git status`,
`git diff`, `git diff --stat`, the current commit, the paths that were actually
written, and a note of what was actually executed in the session. Everything past
that it fetches itself: the changed files, the code that calls them, the tests, the
project's own conventions. The diff comes first and the rest is expanded into only
where the diff cannot answer the question.

It is **read-only**, enforced in code rather than asked for in its prompt: every
writing verb is refused before it is dispatched. It reports; the main agent makes
every change. It also cannot run anything, so it cannot review the result of its own
run — what the session executed is stated to it as an observed fact, and judging what
that proves is its job.

It is told, in as many words, not to trust the tests, not to trust the plan, and not
to trust the implementing agent's account of its own work.

### Findings

Every finding carries a severity, a file, a line where one was actually read, what the
evidence was, why it matters, and a direction for the fix.

| Severity | Blocks completion |
|---|---|
| `CRITICAL` | yes — correctness, security, data loss, destructive behaviour |
| `MAJOR` | yes — a real bug, missing required behaviour, a likely regression, an important missing test |
| `MINOR` | no — a smaller defect worth fixing |
| `SUGGESTION` | no — optional |

Alongside the findings the reviewer returns a **requirements checklist** built from
your request, each marked satisfied, partial or not satisfied. That is the part that
tells "does this code look good" apart from "did we build what was asked", and it is
the check that catches a clean implementation of the wrong feature.

The verdict is `PASS`, `PASS_WITH_WARNINGS` or `FAIL`. A reply claiming a pass while
listing a blocking finding is recorded as a **FAIL** — the findings decide, and the
reviewer's own claim is shown beside it so the contradiction is visible.

### What stops it being gamed

- **Only a real review can produce a pass.** There is no key on any action that sets a
  verdict. The only thing that moves review state is a reviewer agent's own output,
  parsed and validated field by field. A model writing "review passed" writes a
  sentence, and sentences do not move the state machine.
- **A review that did not complete is not a pass.** A reviewer that crashed, timed
  out or returned something unreadable leaves the task in `ERROR`, which blocks the
  final answer exactly as a failure does.
- **A passing review goes stale when the code moves under it.** Edit a file after a
  review passed and the next answer needs a fresh one — what passed is not what would
  ship.
- **The review step in the plan cannot be ticked off early.** Marking a step whose
  title names review as completed is refused while the review has not passed.
- **A reviewer cannot review its own work.** `review` is refused to every background
  agent, including the reviewer, and the verb is documented only in the main agent's
  prompt.
- **The reviewer cannot be started while workers are running.** They would be writing
  to the tree it is reading, and the review would be of a state that never existed.

### When it happens

A review is required when the runtime has seen **both** halves of substantial work: a
plan of three or more steps, and at least one file actually written. Neither alone
counts — a long plan that changed nothing was research, and a one-line patch with no
plan was a favour. Both are facts TMT observed rather than claims the model made, so
a model cannot describe its work as small to avoid one.

You override it in either direction with your own words. "…and review the changes"
turns one on; "no review needed" turns it off. Saying nothing leaves the decision to
the evidence, which is the usual case.

### Limits

There are **three review cycles per task**. If the third still reports blocking
issues, the answer is released rather than held forever — holding it further would
spend the turn and end with no answer at all. It goes out carrying a line saying
plainly that review did not pass and how many findings are open, and the main agent
is required to say the same in its own words. Silence would be the worse failure.

### On screen

The review sits under the plan in the same right-hand column, in three rows at most:

```
PLAN 2/5                       PLAN 5/5
S1 + Inspect repository        S1 + Inspect repository
S2 + Implement feature         S2 + Implement feature
S3 > Add tests                 S3 + Add tests
S4 - Independent review        S4 + Independent review
S5 - Final verification        S5 + Final verification

REVIEW 1/3                     REVIEW 2/3
> Running independent review   + Review passed
```

Every state carries a mark as well as a colour, so the column reads with the escapes
stripped and on a terminal with no colour at all. `/review` prints the findings in
full, and is the way in on a window too narrow for two columns.

## Project context: `TMT_Context/`

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

### When it is created

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

### `notes.md` — how the project works

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

### `progress.md` — what has happened

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

### How it is used

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

### The context never outranks the code

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

### Your edits are protected

You can rewrite either file by hand at any time. TMT changes **one section at a time**
and writes every other byte back exactly as it read it — including headings it has
never heard of, your prose, and your spacing. There is no operation that hands a whole
file over, and no way to ask for one.

If you edit a file while TMT is working, your edit survives: the file is re-read at the
moment of the change rather than taken from a copy read earlier.

### Secrets are never written

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

### With `/plan`, `/review` and `/verify`

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

### Per project, always

The context path is worked out from the active workspace every time it is asked for, so
two projects can never share one and information from one can never appear in the
other.

### Turning it off

Settings → **Project Context** → Enter. Default is **ON**.

With it off, TMT neither creates, reads nor updates any `TMT_Context`. **Files already
written are left exactly as they are** — they belong to the project and to whoever
wrote them, and a setting is not consent to delete somebody's notes.

The switch is stored in `.tmt_context` beside TMT's other per-install settings, so it
is TMT's own state and does not follow the workspace. The context *files* are the one
thing TMT deliberately writes into your project.

### If it cannot be created

A read-only checkout, a permissions failure, a full disk: TMT says so once and carries
on with your task.

```text
Persistent project context could not be created (PermissionError: ...).
Continuing without TMT_Context.
```

Nothing about this feature is worth failing a task for.

### Should it be committed?

That is your call. TMT does not touch your `.gitignore`. The default treats the two
files as ordinary project documentation — they are readable, diffable, and useful to
everyone working on the repository — so committing them shares what TMT has learned
with the rest of the team. Ignore them if you would rather they stayed local.

## Background agents

TMT can delegate. The main agent spawns background workers, they do real work through
the same actions and the same models it uses itself, and it waits for them and reports
what they did.

```
Task> spawn three agents to write multiply.py, divide.py and power.py, then wait for them
```

| | Runs | May edit | May push | Talks to you | Ends with |
|---|---|---|---|---|---|
| main agent | the session loop | yes | yes | yes | `end_conversation` |
| worker | a background thread | yes | **no** | no | `internal_response` |
| note agent | a background thread | **no** | no | answer only | `internal_response` |

**Ten workers at once.** The main agent does not count against that and neither does
the note agent. An eleventh request is refused with a sentence saying so, not ignored.

`/agents` prints what they are doing. In a real terminal, Right Arrow at the end of an
empty line opens the same thing as a live panel, and Left closes it.

### The delegation contract

A delegation is a contract, not a wish. `spawn_agent` takes an optional `constraints`
object saying what that agent may do, how long it may run, and what it must report —
and **TMT enforces all three itself.** The agent is told its contract, and it is also
refused at the dispatcher, so it cannot get round any of it by choosing a different
tool.

```json
{"action": "spawn_agent",
 "task": "Investigate how authentication is put together in this repository.",
 "constraints": {
     "read_only": true,
     "timeout_seconds": 600,
     "report": {"file_list": true, "diff": true, "summary": true}
 }}
```

Every part of it is optional, and **a `spawn_agent` with no `constraints` behaves
exactly as it always did** — same prompt, byte for byte, same permissions, same report.
Nothing about an existing delegation changed.

Constraints are **per delegation**. Worker #1 can be read-only with five minutes while
worker #2 writes freely with fifteen; neither can see or affect the other's contract,
because a contract is one immutable object hanging off one record and there is no
global anywhere on the path.

#### `read_only`

`read_only: true` means the agent may inspect this workspace and may not change it. It
keeps every reading verb — `read_file`, `read_lines`, `list_files`, `glob`,
`grep`, `find_symbol`, `tree`, `code_map`, `related_tests`, `recall`,
`git_status`, `git_diff`, `git_identity` — and is refused everything else.

**Enforced at execution time, not asked for in the prompt.** The refusal happens before
the action runs, in two places: `agent_worker` checks every action before it is
dispatched, on the single-action path and on the batch path both, and
`agent_actions.execute_action` checks again at the dispatcher. Both ask the same
function, so there is one rule and two places that enforce it.

**It is a whitelist, not a list of banned verbs.** Every action added to TMT after this
was written is refused by default. A list of banned verbs would silently admit the next
mutation verb somebody registers, and the person adding it is not the person who wrote
the list.

That covers the paths that are not obviously file writes:

| Refused | Because |
|---|---|
| `write_file`, `append_file`, `write_files`, `patch_file`, `replace_lines`, `replace_across`, `copy_file`, `rename_file`, `create_folder`, `delete_file`, `delete_folder` | they change files |
| `run_file` / `run_python` | a program can write anything, so running one is a mutation path |
| `git_commit` | committing changes the repository |
| `open_app` | it launches an application outside the workspace |
| `remember` | it writes to TMT's own memory store |
| `git_push`, `plan`, `review`, `verify`, `project_context` | already refused to every background agent, contract or no contract |

**TMT has no general shell verb**, which is why there is no allowlist of "safe"
commands anywhere here. The only way a worker can execute arbitrary code is `run_file`,
and a read-only delegation is refused it outright — no parsing of command strings, no
guessing whether `sed -i` writes. That is the honest version of the guarantee: it rests
on there being one execution path and it being closed, rather than on TMT being able to
tell a mutating command from a harmless one.

**What it does not claim.** A read-only delegation cannot make a persistent change
through any verb TMT offers it. It is not a sandbox: TMT is not preventing writes at
the operating-system level, and if some future action opened one it would have to be
added to the whitelist deliberately before a read-only worker could reach it.

A refused attempt is **reported, not hidden**. The worker is told what was blocked and
why, so it can adjust and carry on — a blocked write is not automatically a failed
delegation — and the attempt is recorded and reaches the main agent in the result:

```
Constraint violations: 1 write operation blocked (write_file src/auth.py)
```

#### `timeout_seconds`

A whole number from 1 to 3600. It is the maximum runtime of the **whole delegation**,
not of one action, and it is not reset by an action finishing or by the model replying.

The clock starts when the worker actually starts, not when it is registered, so a
delegation never loses part of its time to something else being slow.

**Enforced by the runtime.** The deadline is checked at the same three boundaries
cancellation is: at the top of every step, between chunks of a streamed response, and
on the line immediately before every action is dispatched. When it passes:

- no further action runs;
- the agent's status becomes `timed_out` — which is **not** `failed` and not `killed`;
- its worker slot is released at once, so a delegation that was waiting for capacity
  can start;
- whatever it had done is kept, and whatever report it owed is still collected.

The guarantee is exactly the one `kill` carries and no larger: **after the deadline, no
further tool call is dispatched.** A request already in flight still finishes arriving,
because a Python thread cannot be terminated and a streamed response has no abort
primitive. Claiming instant termination would be a lie in the one place a lie is
expensive.

There is no timer thread. The deadline is arithmetic on the record, swept whenever the
answer could matter — before every capacity check, on every repaint, and inside every
wait, which never blocks past the nearest deadline. That is the same design the
five-second card retention uses, and for the same reasons: nothing to cancel, nothing
to leak, and a test drives it by advancing a number instead of waiting ten minutes.

Invalid timeouts are refused before anything starts: a negative one, a zero, a string,
a `true`, a fraction of a second, or anything past the hour ceiling. **A refused
contract starts no worker at all** — a delegation running under half a contract is the
one outcome nobody can reason about.

#### `report`

`file_list`, `diff` and `summary`, each independently. They are **not permissions** and
never affect what the agent may do.

- **`file_list`** — the files the agent's own actions actually read and wrote, taken
  from the requests those actions carried. Never assembled from anything the agent said
  about what it had read.
- **`diff`** — what git says about the files this agent wrote. Scoped to those files
  deliberately: the main agent goes on working while a worker runs and several workers
  can run at once, so the whole tree's diff is emphatically not one delegation's work.
  A read-only delegation's diff says `No changes permitted by delegation.` A writing one
  that changed nothing says `No workspace changes.`
- **`summary`** — the agent's own account of the work, which is the `response` of its
  `internal_response` and the only part of the report that is the model's words.

Reports are collected on **every** ending, not only on success: a timed-out or
cancelled delegation still has a real file list, a real diff and real timing, and
throwing that away because it did not finish normally would discard the only record of
what it managed.

What comes back to the main agent is structured and concise — no tool-call transcripts,
no raw logs:

```
Background agent #4
STATUS: TIMED OUT
Contract: READ ONLY  TIMEOUT 10:00  FILES  SUMMARY
Runtime: 10:00 of 10:00
Progress: 17 actions taken, 11 files inspected, 0 files changed

SUMMARY
  Found the authentication entry point in AuthService; three test modules cover it.

FILES
  Inspected (11):
    src/auth/service.py
    src/auth/token.py
    ...
  Changed: none
```

#### On the screen

The counter beside the prompt reads `4/10 agents`, the panel header reads
`AGENTS 4/10`, and a constrained agent's card carries its contract compactly:

```
██░░░░░░ #3  RO  8:32/10:00  +0 -0  ~4k out  1m28s  running
```

`RO` is read-only, the pair is time remaining against the limit — a real countdown off
the same arithmetic that will actually stop the work — and `F D S` on the card marks
the report requirements. `/agents` says all of it in full, where there is width to read
it. A timed-out agent reads `timeout` and is coloured as a stop rather than as a
failure, because it is one.

#### Nested delegation

Background agents cannot spawn agents of their own — their action context carries no
register at all — so a read-only delegation has no way to reach a writing one. That was
true before contracts existed and is unchanged; nothing here invents a nested-worker
rule for something that cannot happen.

### Watching them work

While agents are running, each one gets a row of its own directly under the main
progress bar:

```
██████████  60% Working                      <- the main agent, in colour
██░░░░░░ #1  +45 -3  4k out  47s  running    <- one row per agent, in grey
██████░░ #2  +0 -0  ~900 out  1m34s  running
████████ #3  +7 -120  ~15k out  2m21s  done
```

Each row carries the agent's number, the lines it has added and removed, the tokens it
has generated, how long it has been working, and its state. Everything on it is
measured rather than estimated, except where a figure is marked `~` — that means the
provider did not report it and TMT worked it out from the text, and it is marked
everywhere it happens.

**The agent bars are grey and the main bar is coloured, and that is the whole point of
the difference.** The colour gradient means "the main agent is working, and this is how
far along it is". Five coloured bars would read at a glance as one process reported five
times. The agents get the absence of a colour rather than a colour of their own.

**An agent's bar shows the share of its step budget it has spent, not how close it is to
being done.** Nothing can know the second — a bar that implied it would be inventing the
one figure nobody has. A finished agent's bar is full because it is over, which is the
one moment completion actually is known.

A finished agent's row and card stay for five seconds and then go. Its result does not:
the main agent can still ask for it long afterwards.

The counter above the input box counts agent work into the session's own totals:

```
+55 lines, -5 lines, ~12k context, 433 out, agents ~22k tokens
```

The lines include everything the agents wrote — a line a worker wrote is a line the
session wrote, and a counter reading `+0` while five workers rewrote the project would
be telling the truth about one thread and a lie about the session. The agents' token
spend is reported separately from `context`, because that one is how full the window of
the request in flight is, and adding five workers into it would describe a context that
does not exist.

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
- **"Kill" is cooperative, not instant, and so is a timeout.** Python cannot forcibly
  stop a thread. What is guaranteed, and what is tested, is that **no further tool call
  runs once an agent is killed or has passed its deadline** — cancellation takes effect
  at the next chunk or the next action boundary. An agent stuck on a stalled connection
  is marked killed and abandoned; its thread is a daemon and can never hold TMT open.
- **There is no queue.** Ten workers is a hard cap and the eleventh request is refused
  with a sentence, not parked. TMT has no scheduler to integrate with and building one
  for this would be a much larger thing than the cap needs; the refusal names the cap
  and says what to do about it, which is what the main agent acts on.
- **Waiting blocks the main agent.** It is an ordinary action, not a suspend. The
  interface stays alive while it waits because the live region repaints on its own
  thread, and Ctrl-C returns you to the prompt.
- **Workers do not coordinate their writes.** Any single write is atomic, and if two
  workers touch the same file the main agent is told which. There is no locking beyond
  that, so give concurrent workers separate files.
- **You never see a worker's own actions.** The interface shows a bar and a short label
  for each one, not the reads and edits it is making. What it did comes back in the main
  agent's summary, which is why the main agent is told to say what it delegated.
- **A card shows no elapsed time; the row under the progress bar does.** The panel
  repaints only when its content changes, and a duration drawn there would either go
  stale or force a repaint on every tick, which is what used to make the cursor flicker.

### What the agents cost

Every worker carries its own system prompt on every request, because the API is
stateless. That prompt is about 14k estimated tokens against the main agent's 19k: it
carries a `tree` of the project rather than the file contents the main prompt inlines,
which saves roughly 1,500 tokens per request. Ten workers each carry a copy, so
delegation is not free — it buys parallelism with tokens, and raising the cap from five
to ten doubled how much of it a session can buy at once. Delegate work that is
genuinely separable, not work you could do in two steps yourself.

A contract adds a few hundred tokens to the worker that carries one, and nothing at all
to a worker that does not: an unconstrained delegation's prompt is byte for byte the one
it was before contracts existed.

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

### Typing while it works

The prompt box stays live for the whole of a turn. You can write the next question
while the agent is still working on the last one, editing keys and all.

**Enter queues the line rather than interrupting.** It is answered as soon as the
current task finishes, and lines are answered in the order you entered them — so you
can stack up three follow-ups and walk away. The box says how many are waiting.

`/note` can be typed there too, which is the point of it: it answers from the workspace
without disturbing the work in progress.

Ctrl-C still stops the running task, exactly as before.

This needs a real terminal. A piped or redirected run reads one task per line and the
box is inert, which is what every scripted run and the test suite get.

Set `TMT_STREAM=0` to disable streaming. Streaming also needs `requests`; without it
TMT runs unstreamed.

## Slash commands

At the prompt, a line that is **nothing but** a `/` command is answered by TMT itself
and is never sent to the model. Names are case-insensitive. Everything else is a task
and goes to the model exactly as before — including a line that merely starts with a
path, such as `/usr/bin/python is broken`.

`/plan`, `/review` and `/verify` are the three that read both ways. Alone on the line
each is the read-only report below; with a task after it — `/plan Build the login
page` — the line is that task, with the capability turned on for it. See
[Capabilities](#capabilities-plan-review-verify).

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
| `/notes` | what TMT remembers about this project between sessions: where `TMT_Context/` is, what is in each file, and which notes name paths that no longer exist |
| `/agents` | what the background agents are doing |
| `/back` | step out to the startup menu, keeping the session. See below |
| `/plan` | the steps TMT is working through for this task, and what is left |
| `/verify` | what was actually run to check this task's work: every check, its command, its exit code, and the output of anything that failed |
| `/review` | what the independent review found: the verdict, every finding, and the history of this task's reviews |

### `/back` — the menu, without losing the session

`/back` puts the startup menu back on screen over a session that is still running.
Nothing is ended, cleared, cancelled or waited for: the conversation is still the
conversation, the plan is still the plan, and any background agents carry on working
behind it. Before this, Settings and Help were only reachable by quitting.

The menu it opens is the launch menu with three differences:

```
> Resume    Go back to the session, which is still here
  Settings  Provider, API key and the model TMT runs on
  Help      What TMT does, and how to drive it
  Exit      Close TMT and end the session
```

- **Start reads Resume**, and its label keeps moving through the gradient even while
  the cursor is somewhere else — that is the screen saying your session is still there.
- **Exit says it ends the session.** The word is the same as at launch; the
  consequence is not.
- **Settings is not offered while anything is still running.** The row is gone —
  not greyed, not disabled — and a line above the list says what is running and what
  to do:

```
Settings are not offered while work is running: 2 agents. Wait for it to
finish, then /back again.
```

The provider, the key and the model are all read while a request is in flight, so
changing one underneath a running agent lands a change nobody asked for in the middle
of a request that had already started. When the work finishes the row comes back on
the next frame, without closing the menu.

Choosing Resume clears the screen, draws the header again, and returns you to the
prompt with everything as you left it.

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

**Completion.** In a real terminal, typing `/` lists the ten commands under the
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
| project context | on, from `.tmt_context`; set in Settings. See [Project context](#project-context-tmt_context) |
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

The suite lives in `testing/`, split into `testing/unit/` and `testing/integration/`.
The runner stays at the root and discovers both; see
[testing/README.md](testing/README.md) for what belongs where.

1581 tests. Eight of them read the API key from `.tmt_key`, so on a fresh clone with
no key configured those eight fail and the rest pass.

It takes roughly fifteen minutes rather than the two it used to, and almost all of that
is one test in `test_agent_review.py` that starts three real reviewer agents and waits
out a live API round trip for each. That one is also the only test here that is not
deterministic: it settles a failing review and then sends bogus objects to prove none of
them can turn it into a pass — but running `review` runs a review, so a live reviewer
that likes your working tree passes it through the legitimate route and the assertion
fires. Re-run it before believing it.

## License

Apache license 2. See [LICENSE](LICENSE).
