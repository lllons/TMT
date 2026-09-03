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


## "Too Many Tools" — a CLI coding agent. It edits files in a workspace it cannot leave, runs shell commands through one guarded tool, and commits and pushes automatically on any repo.

>**Needs Python 3.8+.** Install it with `npm install -g tmtcode` (Node 14+ and git),
>or from a clone with pip.

## Quick start

**With npm — two commands, no clone and no pip step:**

```bash
npm install -g tmtcode
tmtcode
```

That is the whole of it. The install puts the `tmtcode` command on your PATH; the first
time you run it, it downloads TMT into `~/.tmtcode` — about twenty seconds, and it says
so while it happens — and then starts normally and asks for your API key.

**From a clone — pip puts the command on your PATH:**

```bash
git clone https://github.com/lllons/TMT.git
cd TMT
python -m pip install -e .        # installs TMT and puts `tmtcode` on your PATH
tmtcode
```

Windows: `py`. macOS/Linux: `python3`. Use `python -m pip install -e ".[live]"` to add
`requests` and `rich` for streaming and colour — TMT runs without them.

The clone stays where it is and is TMT's home, not your project: after installing, `cd`
to your work and run `tmtcode` there. `pip uninstall -y tmtcode` reverses it.

**Or run the clone with no install at all,** from anywhere:

```bash
python /path/to/TMT/TMT.py                    # the current directory is the project
python /path/to/TMT/TMT.py ~/Projects/MyWebsite
```

Run it from any directory on the system. **The directory you run it in becomes the
project TMT works on.**

```bash
cd ~/Projects/MyWebsite && tmtcode      # TMT works on ~/Projects/MyWebsite
cd ~/Documents/MyProject && tmtcode     # TMT works on ~/Documents/MyProject
```

Install TMT once, anywhere. You never copy it into a project, and a project never
needs TMT files inside it.

Then type a task at the `Task>` prompt, in plain English. TMT picks the actions itself.

```
Task> what does report.py do?
Task> change the timeout in net.py from 5 to 30 seconds
Task> run the test suite and tell me what failed
Task> commit the changes and push to main
```

`quit` or `exit` to leave. Ctrl-C cancels the current task without closing TMT.

Pointing TMT at a directory other than the current one, and what to do when `tmtcode`
will not start, are in [Install](docs/install.md) and
[The workspace](docs/workspace.md).

## What it can do

- **Edits files in one workspace it cannot leave.** Create, read, patch, replace an
  exact line range, append, copy, rename, delete. Edits are search-and-replace rather
  than rewrites, Python is syntax-checked before it is written, and a path that
  resolves outside the project directory is refused. →
  [Files and apps](docs/files.md)
- **Finds its way around a repository without reading all of it.** `tree`, `glob`,
  `grep`, `find_symbol`, `code_map`, `replace_across` (which previews by default),
  `related_tests`, and notes about a project that outlive the session. Structural
  facts and lexical guesses are labelled apart. →
  [Understanding a repository](docs/repository-tools.md)
- **Strings tools together.** `multi_tool` runs several calls in one round trip and
  hands every result back together — five files read at once, or the first six lines
  of every Python file through a `for_each` pattern. Every call inside it meets the
  same guards it would meet alone, and a background agent's whitelist is asked about
  each entry before any of them runs. →
  [Several tools at once](docs/multi-tool.md)
- **Runs commands through exactly one guarded tool.** `bash` supports pipes, `&&`,
  `||`, `;`, redirection and globbing — parsed by TMT itself, never handed to a shell.
  The environment is built rather than inherited and your credentials are left out,
  `PATH` is curated, the network is off unless the run allows it, the timeout kills the
  whole process tree, and anything destructive or unrecognised is put to you as a
  question. What that does and does not confine is stated plainly. →
  [Running commands](docs/bash.md)
- **Starts and collects long-running jobs.** A server or a watcher is registered rather
  than run, logged to its own file, and killed when the session ends. →
  [Running commands](docs/bash.md)
- **Commits and pushes on any repo.** You stay the author and the committer; TMT is
  credited beside you with a `Co-authored-by` trailer. It stages only what it changed,
  never creates a branch and never force-pushes, and pushes only when your own words
  asked for one. → [Git](docs/git.md)
- **Runs in a pipeline** (`--ci`). One task, no screens and no question that can
  block, a hard bound on turns and wall clock, one JSON object on stdout with
  `--json`, and an exit code to branch on. Nothing is relaxed to get there:
  dangerous commands are still denied, an approval nobody can give is refused
  rather than waited on, and a push needs both the flag and your own words. →
  [CI mode](docs/ci.md)
- **Asks you to decide, without losing its place.** When the answer changes what gets
  built and is not in the code, TMT puts a question on screen with up to five numbered
  options; you press one digit and the same task carries straight on with your answer.
  It never ends the task to ask, and with nobody at a keyboard it decides for itself
  and says which option it took and why it could not ask. → [Asking you](docs/ask-user.md)
- **Can be undone** (`/undo`). TMT photographs the workspace before the first action
  of a turn that could change it, so a turn you did not want is one command away from
  being put back — files it rewrote, files it deleted and files it created, named
  before anything moves and only carried out when you confirm. The undo is itself
  undoable, and what a snapshot cannot cover it refuses rather than half does. →
  [Undo](docs/undo.md)
- **Plans the work and is held to it** (`/plan`). The steps are on screen while it
  works, and the runtime will not let it answer while one is outstanding. →
  [The plan](docs/plan.md)
- **Reviews its own work with a separate agent** (`/review`). Read-only, independent,
  with severity-ranked findings and a checklist of your requirements; the answer waits
  for a review that actually passed. → [Independent review](docs/review.md)
- **Runs the checks your repository actually has** (`/verify`). It works out what this
  project tests, lints and builds itself with, picks what the change warrants, and
  reports exit codes — a check passes when a process exits zero and at no other time.
  → [Verification](docs/verification.md)
- **Delegates to background agents.** Ten workers at once, each under a contract TMT
  enforces itself: read-only or not, a deadline, and what it must report. →
  [Background agents](docs/background-agents.md)
- **Answers a question without disturbing anything** (`/note`), from a read-only agent,
  while the rest of the work carries on. →
  [Background agents](docs/background-agents.md)
- **Remembers the project between sessions.** `TMT_Context/notes.md` and `progress.md`
  are written into your project from evidence rather than intention, never overwrite
  your edits, and never carry a secret. → [Project context](docs/project-context.md)
- **Says things on the way, and once at the end.** `send_message` narrates without
  ending the task; `end_conversation` is the only action that finishes one. →
  [Talking to you](docs/messaging.md)
- **Shows the work as it happens.** A progress bar, elapsed time, a live token count, a
  streaming reply, a row per background agent, and the plan, review and verification
  columns beside the prompt. You can type the next question while it works. →
  [Interface](docs/interface.md)
- **Answers to itself at the prompt.** `/context`, `/config`, `/clear`, `/effort`,
  `/model`, `/note`, `/notes`, `/agents`, `/back`, `/plan`, `/verify`, `/review`. →
  [Slash commands](docs/commands.md)
- **Updates itself on launch.** A fast-forward only, never over uncommitted work and
  never on a diverged branch, whichever way TMT was installed. →
  [The launch screen](docs/launch-and-updates.md)
- **Four providers** — OpenRouter, OpenAI, Anthropic and Gemini — with the key kept in
  the installation directory rather than in your project. → [API keys](docs/api-keys.md)
- **Python 3.8+ and the standard library.** `requests` and `rich` are optional and add
  streaming and colour; TMT falls back without them.

## Documentation

Everything TMT does, one file per part of it, in [`docs/`](docs/):

| Doc | What is in it |
|---|---|
| [Install](docs/install.md) | the npm install, the pip install, what to do when `tmtcode` will not start, and uninstalling |
| [The workspace](docs/workspace.md) | the two directories, choosing the project directory, and the permissions and limits TMT works under |
| [Getting started](docs/getting-started.md) | the first launch, the menu, the tip under the header, and what you can ask for |
| [API keys](docs/api-keys.md) | the three places a key can live, and how to put one in by hand |
| [The launch screen](docs/launch-and-updates.md) | the splash, the update check, when TMT will and will not update itself, restarting, and turning it off |
| [Capabilities](docs/capabilities.md) | `/plan`, `/review` and `/verify`: what they cost, how they are turned on, and why only you can turn them on |
| [The plan](docs/plan.md) | the steps on screen, the marks and colours, and the gate that will not let a task finish early |
| [Files and apps](docs/files.md) | the file actions, and the two applications TMT may launch |
| [Understanding a repository](docs/repository-tools.md) | `tree`, `glob`, `grep`, `find_symbol`, `code_map`, `replace_across`, `related_tests`, `remember`/`recall` |
| [Several tools at once](docs/multi-tool.md) | `multi_tool`: several calls in one action, `for_each` over every file a pattern matches, the ceiling, and why nothing inside it gets round a guard |
| [Running commands](docs/bash.md) | the `bash` tool: what is refused, what is enforced, what you are asked about, the two sandbox levels, and background jobs |
| [Git](docs/git.md) | commits, pushes, the `Co-authored-by` trailer, the co-author identity, and GitHub attribution |
| [Verification](docs/verification.md) | how the checks are discovered and chosen, the four outcomes, which tests it picks, and the cycle limit |
| [Independent review](docs/review.md) | the reviewer's brief, the findings and severities, what stops it being gamed, and the limits |
| [Project context](docs/project-context.md) | `TMT_Context/notes.md` and `progress.md`: what goes in them, how they are used, and how your edits are protected |
| [Background agents](docs/background-agents.md) | delegation, the contract (`read_only`, `timeout_seconds`, `report`), `/note`, what agents cannot do, and what they cost |
| [Talking to you](docs/messaging.md) | `send_message` and `end_conversation`, and which of the two ends a task |
| [Interface](docs/interface.md) | the live region, the agents panel, and typing while TMT works |
| [Slash commands](docs/commands.md) | every command, `/back`, effort levels, completion, and what is never printed |
| [Configuration](docs/configuration.md) | the environment variables and settings files, and their defaults |
| [Tests](docs/tests.md) | running the suite, where it lives, and the one test that is not deterministic |

## Every tool TMT can use

Forty-six actions. You never name one — TMT picks them itself from what you asked for,
and is told to choose the narrowest tool that answers the question. Each group links to
the page that explains it.

**Files** — [Files and apps](docs/files.md)

| Tool | What it does |
|---|---|
| `write_file` | Create a file, or overwrite one |
| `write_files` | Create several files in one action |
| `patch_file` | Search-and-replace inside a file — the default for an edit |
| `replace_lines` | Replace an exact line range |
| `append_file` | Add to the end of a file |
| `read_file` | Read a whole file |
| `read_lines` | Read a line range, with the line numbers |
| `list_files` | List the workspace |
| `copy_file` | Copy a file |
| `rename_file` | Rename or move a file |
| `delete_file` | Delete a file, after asking you at the terminal |
| `create_folder` | Create a folder |
| `delete_folder` | Delete a folder — recursive is opt-in, and it asks |

**Finding your way around a repository** — [Understanding a repository](docs/repository-tools.md)

| Tool | What it does |
|---|---|
| `glob` | Find files and directories by path pattern |
| `grep` | Search inside files, reporting path, line number and the line |
| `tree` | Directories, files, sizes and nesting. Reads no contents |
| `find_symbol` | Where a function, class, method, constant or type is *defined* |
| `code_map` | What defines this, what imports it, what it imports, where it is referenced |
| `replace_across` | The same exact edit in many files. Previews unless told to apply |
| `related_tests` | Reads the git diff and names the tests worth running |
| `multi_tool` | Several calls in one action, or one call over every file a pattern matches — [Several tools at once](docs/multi-tool.md) |
| `remember` | Store a durable note about this project, secrets refused |
| `recall` | Read those notes back |

**Running things** — [Running commands](docs/bash.md), and [Apps](docs/files.md)

| Tool | What it does |
|---|---|
| `bash` | The one execution action. Runs a command line, or `start`s, `status`es, `logs` and `stop`s a long-running job |
| `open_app` | Open Notepad, or Explorer with a file selected. Nothing else |

**The web** — research rather than browsing. Both are read-only, and both are refused to the `/note` agent and to the reviewer.

| Tool | What it does |
|---|---|
| `web_search` | Ask a search backend about an error message or an API |
| `web_fetch` | Read one page of documentation as text |

**Git** — [Git](docs/git.md)

| Tool | What it does |
|---|---|
| `git_status` | What has changed |
| `git_diff` | The diff, whole or for named paths |
| `git_identity` | Which co-author address is in force, and where it came from |
| `git_commit` | Commit, with you as the author and TMT as co-author |
| `git_push` | Push — only when your own words asked for one |

**The three capabilities** — each does nothing unless your prompt names it. [Capabilities](docs/capabilities.md)

| Tool | What it does |
|---|---|
| `plan` | Write and maintain this task's plan — [The plan](docs/plan.md) |
| `review` | Start the independent reviewer — [Independent review](docs/review.md) |
| `verify` | Run this repository's own checks — [Verification](docs/verification.md) |

**The project's memory** — [Project context](docs/project-context.md)

| Tool | What it does |
|---|---|
| `project_context` | Read and update `TMT_Context/notes.md` and `progress.md` |

**Background agents** — the main agent only; a worker has no register to reach. [Background agents](docs/background-agents.md)

| Tool | What it does |
|---|---|
| `spawn_agent` | Start a background worker, optionally under a contract |
| `agent_status` | What every agent is doing |
| `agent_result` | The report one agent produced |
| `wait_for_agent` | Block until one finishes |
| `wait_for_agents` | Block until all of them finish |
| `kill_agent` | Stop one |

**Talking to you** — [Talking to you](docs/messaging.md)

| Tool | What it does |
|---|---|
| `send_message` | Say something; the task carries on afterwards |
| `end_conversation` | Say the last thing. The only action that ends a task |

**Used only by background agents, never by the main one**

| Tool | What it does |
|---|---|
| `internal_response` | A worker's report back to the main agent, which is how it ends |
| `review_agenda` | The reviewer's own checklist, drawn under the progress bar |

## License

Apache license 2. See [LICENSE](LICENSE).
