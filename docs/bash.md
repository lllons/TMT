# Running commands: the `bash` tool

`bash` is TMT's one execution action, and the only way it runs anything at all.

```json
{"action": "bash", "command": "python run_tests.py"}
{"action": "bash", "command": "npm run build && npm test", "cwd": "web", "timeout": 600}
{"action": "bash", "command": "git log --oneline -20 | head -5"}
{"action": "bash", "command": "make build 2>&1 | tail -40"}
```

Pipes, `&&`, `||`, `;` and redirection (`>`, `>>`, `<`, `2>`, `2>&1`) all work, and
`*` and `?` are expanded against the workspace.

**No shell is ever started on what the model wrote.** TMT parses the command line
itself, runs each program as an argument array, wires the pipes with real pipes and
does the globbing itself. That is not a detail — it is what makes everything below
possible. Every guard here works by reading arguments, and an argument that is only
ever handed to `/bin/sh` cannot be read by anything.

**It is also told what it is for.** Building, testing, installing and running a
program are `bash`; reading, searching and editing files are the file actions and
`grep`/`glob`, which are narrower, report exactly what they touched, and cannot
leave the workspace.

## What is refused outright

| Refused | Why | Instead |
|---|---|---|
| `bash -c`, `sh -c`, `cmd /c`, `powershell` | a nested shell is a second command line TMT never parsed | write the line itself — the pipeline already works |
| `python -c`, `node -e`, `perl -e`, `ruby -e` | inline code is the one argument no inspection can read | write the script to a file and run the file |
| `$(...)`, backticks, `${...}`, `$VAR` | a substitution is a second command hiding inside an argument | write the value out literally |
| `&` | a backgrounded process nothing owns outlives the session | `"operation": "start"`, which registers the job |
| an absolute path or a `..` path to a program | a policy on program names is walked round by naming a path | name the program |
| a path, `cwd` or redirect target outside the workspace | it is outside the workspace | name something inside it |
| privilege, remote-shell and administration tools — `sudo`, `ssh`, `systemctl`, `reg`, `netsh`, `diskpart`… | none of them is repository work | — |

`python -m` is fine: it names a module the interpreter resolves, not code TMT would
have to read as an argument.

## What is enforced while a command runs

- **The working directory is the project directory**, or a subdirectory named with
  `cwd`. One that resolves outside it — through `..`, an absolute path or a symlink
  — is refused rather than followed.
- **The environment is built, not inherited.** The child starts from empty. A short
  allow-list is copied (`SYSTEMROOT`, `PATHEXT`, `LANG`, `TERM` and a few more), and
  any variable whose *name* looks like a credential — `KEY`, `TOKEN`, `SECRET`,
  `PASSWORD`, `AWS_`, `GITHUB_`, `OPENAI_`, `ANTHROPIC_` — is never copied, whatever
  it holds. It is the same vocabulary TMT's project memory already uses to refuse
  writing a secret down.
- **`PATH` is curated.** TMT resolves a fixed list of development tools against your
  real `PATH` once, and builds the child's `PATH` from only the directories those
  live in. Your whole `PATH` is never handed to a child process.
- **`HOME`, the temporary directory and the package caches point into a TMT-managed
  directory** beside TMT's other state, per project. A build that writes a cache
  writes it there rather than into your home directory.
- **Network access is off by default.** `curl`, `wget` and the fetching package
  manager subcommands (`npm install`, `pip install`, `cargo fetch`, `go get`…) are
  refused, and the environment tells the tools themselves they are offline
  (`PIP_NO_INDEX`, `CARGO_NET_OFFLINE`, `GOPROXY=off`). **That is tool-level
  cooperation, not a network namespace.** It stops the tools that read those
  variables. It is not a firewall and does not pretend to be.
- **There is a timeout, and expiry kills the process tree** — not only the process
  TMT started. A grandchild does not survive it.
- **Output is capped**, keeping the end rather than the beginning, because the useful
  part of a failing build is its last page. Truncation is reported along with the
  real size, never hidden.
- **The result is the exit code.** TMT does not read success or failure out of the
  output text; that is the rule the verification engine already lives by, and this
  is the same rule in the same place.

Every result names the command as TMT parsed it, the exit code, the output, the
duration and the sandbox level it ran under.

## Approval

Some commands are neither obviously safe nor obviously forbidden. Those are put to
you rather than decided for you.

- **Destructive commands ask.** `rm`, `rmdir`, `del`, `mv`, `dd`, `truncate`,
  `kill`, `taskkill`, `git reset --hard`, `git clean`, `git checkout --`,
  `git push --force`.
- **Commands TMT does not recognise ask.** Unknown is not the same as banned — the
  point of the tool is that it is usable — so an unfamiliar program is a question,
  never a silent yes.
- **`rm -rf` aimed at the project root, `/` or `C:\` is refused outright**, not
  asked. That is not a question anyone should be answering at a prompt at speed.

You are asked at the terminal, exactly as `delete_file` asks. **Where there is no
terminal — a piped run, the test suite, a background agent — the answer is no**, and
the refusal says which rule asked. Any doubt about whether a human is there means
no; that is the same rule that governs raw keyboard input everywhere else in TMT.

An answer can be remembered for this project, in `.tmt_bash_rules.json` kept beside
TMT's other per-project state and never inside your repository. A remembered rule is
a program name or a `program subcommand` pair, never a pattern — a rule nobody can
read back correctly is not a rule. **A remembered "allow" can never turn one of the
refusals above into an approval.** The executable-shape, denied-program and
inline-code refusals are the boundary itself, and a rules file that could switch them
off would be the way round everything else on this page.

## The two sandbox levels, and which one you get

| Level | When | What confines the command |
|---|---|---|
| `os` | the host has an OS sandbox helper — `bwrap` on Linux, `sandbox-exec` on macOS | the kernel. Filesystem confinement is real and enforced outside TMT |
| `policy` | there is no such helper — **the normal case on Windows** | command policy, argument inspection, the constructed environment, the curated `PATH`, resource limits and process-tree kill |

TMT reports which level it actually ran under, in every result, rather than
describing a sandbox in the abstract.

**Under `policy`, a permitted build tool that runs your repository's own code can
still write outside the workspace.** That is not something left unfinished. It is the
limit of what argument inspection can do: `npm test` has to be allowed for the tool
to be worth having, and what `npm test` then executes is your project's own test
script, which TMT never sees as an argument and could not have refused. The same is
true of `make`, `pytest`, `cargo`, `gradle` and every other tool worth allowing.

So the honest statement is that under `policy` TMT confines **what it is asked to
run** — the program, its arguments, its working directory, its environment, its
lifetime and its output — and does not confine **what that program then does**.
Python's standard library cannot confine a child process's filesystem writes on
Windows, and TMT takes no third-party dependencies. If you need confinement that
holds against the code in the repository itself, run TMT on a host with `bwrap` or
`sandbox-exec` and check that the level in the result says `os`.

## Long-running commands

A server or a watcher is *started* rather than run, and collected afterwards:

```json
{"action": "bash", "operation": "start", "command": "npm run dev"}
{"action": "bash", "operation": "status"}
{"action": "bash", "operation": "logs", "id": "1"}
{"action": "bash", "operation": "stop", "id": "1"}
```

Four at a time. Each writes to its own log file under TMT's own directory, and
**every one is killed when the session ends**, process tree and all. A job that
outlived the session would be the one thing this cannot allow: nothing would own it
afterwards, and nothing would ever stop it.

## Background agents cannot use it

`bash` is refused to every background agent — a worker, the `/note` agent and the
reviewer alike — and it is not one of the reading verbs a `read_only` delegation
keeps. Either reason on its own would be enough. A read-only worker running a build
is not read-only. And a worker has no terminal, so it could never answer the
approval half of this, which means it would meet "no terminal means no" on every
command that needed one. Running things stays with the main agent, in the session you
are watching. See [Background agents](background-agents.md).

---

[← Back to the README](../README.md)
