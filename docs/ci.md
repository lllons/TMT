# CI mode

```
tmtcode --ci "run the test suite and fix failures"
tmtcode --ci --max-turns 20 --timeout 900 "fix lint errors in src/"
tmtcode --ci --json "verify the build"
echo "fix the failing parser test" | tmtcode --ci
```

Interactively TMT is a program you sit in front of. `--ci` turns it into a
bounded worker: one task, no screens, no question that can block, a hard limit on
both turns and wall clock, and an exit code to branch on.

**It is not a mode that can do more — it is a mode that can do less.** Every guard
the interactive agent has is still in front of every action. What changes is the
answer to a question nobody is there to answer, and that answer is always no.

## Flags

| Flag | Default | What it does |
|---|---|---|
| `--ci` | off | Enable CI mode. Everything below applies only with it. |
| `--max-turns N` | `30` | How many turns the task may take. Must be ≥ 1. |
| `--timeout SECONDS` | `900` | Wall clock for the whole run. Must be > 0. |
| `--json` | off | Print one JSON object to stdout; send everything else to stderr. |
| `--allow-push` | off | Permit `git push`. Off by default. |
| `--dir PATH` | cwd | The workspace, exactly as in interactive mode. |

The task is the words after the flags, joined — so quoting is optional. With no
task on the command line and stdin not a terminal, the task is read from stdin
**whole**, not one line at a time. With no task anywhere, the run stops with a
usage error rather than waiting for one.

A bad value is refused with a sentence saying what to write instead, and a
value big enough to be a typo (`--timeout 6000000`) is refused as one.

## Exit codes

| Code | Status | Means |
|---|---|---|
| `0` | `completed` | The task finished and reported an answer. |
| `1` | `failed` / `error` | It ran, and the work failed or TMT crashed. |
| `2` | — | Usage: bad flags, no task, no API key, unusable workspace. |
| `3` | `timeout` / `max_turns` | It hit `--timeout` or `--max-turns`. |
| `4` | `blocked` | Policy refused something and the task could not go on. |

Only success is zero, and every code is distinct, so `if tmtcode --ci ...` is a
usable test and a pipeline can tell "it did not finish" from "it finished and
the work is wrong" — those want different retries.

## The JSON result

`--json` prints exactly one object to stdout. Everything a person would read
goes to stderr, so the object is the only thing on stdout and `| jq` works.

```json
{
  "ok": true,
  "status": "completed",
  "workspace": "/path/to/project",
  "task": "run the test suite and fix failures",
  "turns": 9,
  "duration_seconds": 42.8,
  "message": "Fixed calc.py: add() now returns a + b instead of a - b.",
  "changed_files": ["calc.py"],
  "verify": { "ran": false, "passed": null, "details": null },
  "blocked_reason": null,
  "error": null
}
```

`status` is one of `completed`, `failed`, `timeout`, `max_turns`, `blocked`,
`error`. `ok` is true only for `completed`.

Every field is measured or null. `changed_files` comes from the actions' own
requests, never from the model's account of what it changed; `turns` is the
loop's own counter; `verify` comes from exit codes. **A field TMT cannot answer
honestly is null rather than guessed** — which is the only reason to parse this
file instead of the log.

## Policy in CI

Nothing is relaxed. `agent_policy` decides ALLOW, ASK or DENY exactly as it does
interactively:

- **ALLOW** runs, unchanged.
- **DENY** is refused, unchanged. `rm -rf`, `git reset --hard`, force pushes,
  `git remote set-url`, inline code, anything outside the workspace: all still
  denied. **CI is not a way round the denylist.**
- **ASK** — the set of commands TMT would have put to a human — is **refused**,
  because there is no human. The question is recorded and reported in
  `blocked_reason`.

Deletions ask a person interactively, so in CI they are refused too. Writes stay
inside the workspace. The environment is still built rather than inherited, the
PATH is still curated, and the process-tree kill is unchanged.

**A refusal is only terminal when the run did not finish.** A task that was
refused one command, took another route and passed is a task that *succeeded* —
failing a green build over a refusal the agent recovered from would make CI mode
unusable. The refusal is still reported either way.

### Pushing

`git push` needs **both** `--allow-push` **and** the user's own words in the
task ("commit and push to main"). Either alone is refused. An unattended push is
the one action nobody can take back, so it wants two statements of intent: one
in the pipeline's configuration, one in the task.

### Asking you to decide

`ask_user` cannot block a CI run. The model is told there was nobody to ask —
not that a person declined — and decides for itself, saying in its final message
which option it took and why it could not ask.

### Verification

`/verify` is a capability the task's own words authorise, in CI as anywhere
else. To have TMT run this project's checks and gate its own answer on them, put
the command in the task:

```
tmtcode --ci --json "/verify run the test suite and fix any failures"
```

Without it, `verify.ran` is `false` in the summary. It is not a pass that was
skipped; it is a check nobody asked for.

## What is skipped

No splash, no startup menu, no API-key form, no git-identity offer, no screen
clear, no prompt box, no type-ahead reader. **Nothing reads stdin after the
task.** If the API key is missing, the run stops with exit 2 and names the
environment variables that would fix it rather than opening the setup screen.

A directory that holds files and is not a git repository is refused: interactive
TMT asks before adopting one, and what it warns about — "nothing it does will be
recoverable" — is not something an unattended run may agree to on your behalf.
Run in a git repository, or name one with `--dir`.

CI mode also never updates TMT itself. A pipeline that rewrote its own agent
mid-job would be one nobody could reproduce.

## GitHub Actions

```yaml
- run: npm install -g tmtcode
- run: tmtcode --ci --json --max-turns 30 --timeout 900 "run the test suite and fix failures"
  env:
    OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}
```

The step fails the job on any non-zero code. To act on the reason instead:

```yaml
- id: tmt
  continue-on-error: true
  run: tmtcode --ci --json "run the test suite and fix failures" > result.json
- run: jq -r '.status, .message' result.json
```

## Limits worth knowing

- **The wall clock is enforced between actions.** It cannot interrupt a command
  that is already running — `bash` has its own timeout for that — so a run can
  overshoot `--timeout` by at most the length of one command.
- **`--max-turns` bounds turns, not cost.** A turn with a large workspace
  snapshot costs more than a small one.
- **Web search is optional.** If no search key is configured the run continues
  without it rather than stopping; the model is told the tool is unavailable.
- **A partial run leaves its work in the workspace.** `max_turns` and `timeout`
  stop the agent; they do not roll anything back. `/undo` in an interactive
  session on the same workspace can put it back.
