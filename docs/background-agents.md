# Background agents

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

## The delegation contract

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

### `read_only`

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
| `git_commit` | committing changes the repository |
| `open_app` | it launches an application outside the workspace |
| `remember` | it writes to TMT's own memory store |
| `bash`, `git_push`, `plan`, `review`, `verify`, `project_context` | already refused to every background agent, contract or no contract |

**A read-only worker cannot execute anything — and neither can any other worker.** TMT
has exactly one execution verb, `bash`, and it is refused to every background agent
before it is dispatched. So a read-only delegation needs no allowlist of "safe"
commands, does no parsing of command strings, and nobody has to guess whether `sed -i`
writes. That is the honest version of the guarantee: it rests on there being one
execution path and that path being closed here, rather than on TMT being able to tell a
mutating command from a harmless one. The command policy that governs the main agent,
which does have a terminal to be asked at, is in
[Running commands](bash.md).

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

### `timeout_seconds`

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

### `report`

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

### On the screen

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

### Nested delegation

Background agents cannot spawn agents of their own — their action context carries no
register at all — so a read-only delegation has no way to reach a writing one. That was
true before contracts existed and is unchanged; nothing here invents a nested-worker
rule for something that cannot happen.

## Watching them work

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

## `/note` — ask about the workspace without disturbing anything

```
Task> /note which module owns the prompt box?
```

A read-only agent answers from the workspace while everything else carries on. It may
search, read and inspect structure; it cannot create, edit, delete or push, and that is
enforced by a whitelist checked before every action rather than by asking it nicely.

The question goes on the same line. That form works everywhere, including a piped run —
the piped reader takes one task per line, so a two-stage prompt cannot be reached from a
pipe at all. In a real terminal a bare `/note` will ask for the question separately.

## What background agents deliberately cannot do

These are limits of the design, not things left unfinished:

- **A worker cannot push.** It may read `git status`, `diff`, `log` and `branch`, and it
  may commit; reaching a remote stays with the main agent, which needs your own words in
  the task before it can.
- **A worker cannot delete a file or a folder.** Both wait for a human to confirm at the
  terminal, and a background thread has no terminal to be asked at. A worker reports the
  path instead and the main agent does it.
- **A worker cannot run anything at all.** `bash` is refused to every background agent,
  so no worker builds, tests or executes a line of what it wrote. One asked to verify
  tests says it could not, and says what it did instead; it will not report a result it
  never saw. Running things stays with the main agent, which has a terminal to answer
  `bash`'s approval questions at.
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

## What the agents cost

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

---

[← Back to the README](../README.md)
