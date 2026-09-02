# Several tools at once: `multi_tool`

`multi_tool` runs a list of ordinary actions in one round trip and hands every result
back together. It is how TMT reads five files at once, searches every module for the
same thing, or compiles each file in a directory, without spending a turn per file.

```json
{"action": "multi_tool", "calls": [
  {"action": "read_file", "path": "src/app.py"},
  {"action": "read_file", "path": "src/net.py"},
  {"action": "read_file", "path": "tests/test_net.py"}]}

{"action": "multi_tool", "calls": [
  {"action": "read_lines", "for_each": "**/*.py", "start": 1, "end": 6}]}

{"action": "multi_tool", "calls": [
  {"action": "grep", "query": "TODO", "for_each": "src/*.py"},
  {"action": "bash", "command": "python -m py_compile {path}", "for_each": "src/*.py"}]}
```

```
Task> read the first six lines of every python file in the workspace
Task> show me what each module under src does with logging
Task> compile every file under src and tell me which ones fail
```

## The shape

- **`calls`** is a list of action objects, run in the order written. Any action goes
  in it except the four the loop gives their meaning to before dispatch —
  `send_message`, `end_conversation`, a worker's `internal_response` and the
  reviewer's `review_agenda` — and another `multi_tool`. Each of those is refused with
  the place it belongs named.
- **`for_each`** on an entry makes it a template: a path pattern, exactly as `glob`
  takes one, and the entry runs once per matching *file*, sorted. The file's path goes
  in `path`, or wherever the template wrote `{path}`, `{name}` (the last segment) or
  `{stem}` (that without its extension). A template that matched nothing says so in
  the result; a multi_tool whose only entries matched nothing is refused.
- **`limit`** widens the ceiling. At most 200 calls run by default, counted after
  expansion, up to 1000 with `limit`. A list past the ceiling is refused with the
  count rather than cut short, because a fan-out over "every file" that quietly
  stopped at the two-hundredth would be a result claiming a completeness it did not
  have.

The result is a header, a note per template, and one block per call:

```
multi_tool ran 3 calls.
for_each "**/*.py" (read_lines) matched 3 files.

[1/3] read_lines src/a.py
    1 | import logging
    2 |

[2/3] read_lines src/b.py
    1 | import os
    2 | B = 2

[3/3] read_lines src/deep/c.py
    1 | C = 3
```

One result carries at most about 60,000 characters over every call together. When
they do not fit, every result that does keeps its whole text and the rest share what
is left evenly; a cut is marked inside the block it was made in, so a model that needs
the rest knows to ask for that one call on its own.

## What it does not do

**It does not bypass anything.** A multi_tool runs nothing itself. Every call is
dispatched back through the same function a bare action goes through, with the same
authority, so a call inside a list meets exactly the guards it would meet alone: the
sandbox and the command policy for `bash`, the push authority for `git_push`, the
capability gate for `plan`, `review` and `verify`, and the delegation contract for a
read-only worker. A background agent's whitelist is asked about every entry *before*
any of them runs, and one refused entry refuses the whole list with nothing run — the
note agent that lists a `write_file` after three reads gets the sentence the bare
write would get, and the three reads do not happen either.

**It does not stop on failure.** Every call runs whatever the calls before it
returned. What the result can honestly say is which calls *raised* — those are marked
`FAILED` and counted in the header — while whether a call that ran did what was wanted
is in its own block, exactly as it would be alone. A call that must not run unless an
earlier one succeeded belongs in a later turn.

**It does not end the task.** A list is work, never an answer; the turn goes on to
the next action as it would after any read.

## What the screen shows

One transcript row for the whole list, not one per file: `Multi Tool: Read Lines x110`,
with the line counts added up where the calls counted them, the number of calls on the
facts row, and a warning whenever any call's own row would have been one. The per-call
detail is in the result the model reads. A multi_tool refused before anything ran
draws the refusal.

A write inside a list makes a passed review and a passed verification stale exactly as
the write would have on its own, and a list of reads leaves both standing —
`multi_tool` is not in the mutating set by name, because unlike `bash` the verbs inside
it are known.
